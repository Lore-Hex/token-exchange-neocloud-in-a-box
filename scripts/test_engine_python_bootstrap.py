# SPDX-License-Identifier: Apache-2.0
"""Local stdlib/fake-package processes only, never Docker or upstream imports."""

import copy
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import sysconfig
import tempfile
import threading
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "scripts/engine_python_bootstrap.py").read_bytes()
bootstrap = types.ModuleType("bootstrap_test")
exec(compile(SOURCE, "bootstrap_test", "exec"), bootstrap.__dict__)

# This fixture is deliberately real subprocess code, not an AST assertion.
DRIVER = r'''
ARGS = sys.argv[1:]
LAYOUT = json.loads(read_source(os.path.dirname(__file__) + "/layout.json"))
STATE = install(LAYOUT)

def before_unpickle():
    return observation(sys._nib_smoke_guard)

class Prepared:
    def __reduce__(self):
        return before_unpickle, ()

def target(writer, prepared, depth):
    import fake_engine
    result = {"before": prepared, "after": observation(STATE), "imported": fake_engine.VALUE}
    if depth:
        result["grandchild"] = child(0)
    writer.send_bytes(json.dumps(result).encode())
    writer.close()

def child(depth):
    import multiprocessing as mp
    context = mp.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=target, args=(writer, Prepared(), depth))
    try:
        process.start()
        writer.close()
        require(reader.poll(10))
        data = reader.recv_bytes(16384)
        process.join(10)
        require(process.exitcode == 0)
        return json.loads(data)
    finally:
        reader.close()
        writer.close()
        if process.pid is not None:
            if process.is_alive():
                process.kill()
            process.join(5)
            process.close()

def main():
    import multiprocessing as mp
    import subprocess
    import site
    from multiprocessing import context, resource_tracker, spawn
    if ARGS == ["fresh"]:
        print(json.dumps(observation(STATE)))
        return
    if ARGS == ["positive"]:
        try:
            # A real semaphore causes the tracker to use the same sealed wrapper.
            lock = mp.Lock()
            result = child(1)
            fresh = subprocess.run([LAYOUT["wrapper"], "fresh"], check=True, capture_output=True, timeout=10)
            result["fresh"] = json.loads(fresh.stdout)
            result["parent"] = observation(STATE)
        finally:
            del lock
            result["tracker"] = stop_tracker(STATE)
        print(json.dumps(result))
        return
    if len(ARGS) == 2 and ARGS[0] == "reject":
        name = ARGS[1]
        def forbidden():
            if name == "fork": mp.get_context("fork")
            elif name == "forkserver": mp.get_context("forkserver")
            elif name == "direct-fork": context.ForkProcess(target=lambda: None).start()
            elif name == "direct-forkserver": context.ForkServerProcess(target=lambda: None).start()
            elif name == "method": mp.set_start_method("fork", force=True)
            elif name == "numa": mp.set_executable("/usr/bin/numactl")
            elif name == "ordinary-python": subprocess.run(["/usr/bin/python3", "-c", "raise Exception('private')"])
            elif name == "arbitrary-code": subprocess.run([LAYOUT["wrapper"], "-c", "raise Exception('private')"])
            elif name == "shell": subprocess.run("printf private", shell=True)
            elif name == "site": site.main()
            elif name == "addsitedir": site.addsitedir("/tmp")
            elif name == "sitecustomize": __import__("sitecustomize")
            elif name == "usercustomize": __import__("usercustomize")
            elif name == "path": sys.path.append("/private"); __import__("fake_engine")
            elif name == "env": os.environ["NUMACTL_PYTHON"] = "private"; __import__("fake_engine")
            elif name == "preparation":
                data = spawn.get_preparation_data("test")
                data["sys_path"] = ["/private"]
                spawn.prepare(data)
            elif name == "main-module":
                data = spawn.get_preparation_data("test")
                data["init_main_from_name"] = "private"
                spawn.prepare(data)
            else: raise AssertionError("unknown test")
        try:
            forbidden()
        except ValueError as exc:
            print(json.dumps({"rejected": True, "message": str(exc), "failures": STATE["failures"]}))
            return
        raise AssertionError("accepted forbidden operation")
    dispatch(ARGS, STATE)

if __name__ == "__main__":
    main()
'''


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nib-smoke-unit-", dir="/private/tmp" if sys.platform == "darwin" else None)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.launcher = self.root / "launch.py"
        self.wrapper = self.root / "python"
        self.launcher.write_bytes(SOURCE + DRIVER.encode())
        package = self.root / "packages"
        package.mkdir()
        (package / "fake_engine.py").write_text("VALUE = 'synthetic-only'\n")
        for name in ("sitecustomize", "usercustomize"):
            (package / (name + ".py")).write_text("raise AssertionError('private hook must never run')\n")
        (package / "unknown.pth").write_text("import sitecustomize\n")
        stdlib = [sysconfig.get_path("stdlib"), sysconfig.get_config_var("DESTSHARED")]
        self.env = {"PATH": "/usr/bin:/bin", "HOME": str(self.root), "LC_ALL": "C.UTF-8",
                    "VLLM_WORKER_MULTIPROC_METHOD": "spawn", "VLLM_PLUGINS": ""}
        # macOS may add its fixed per-account text encoding at interpreter start.
        # Observe this local test interpreter only; production has a fixed Linux env.
        env_probe = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", "import os,json;print(json.dumps(dict(os.environ)))"],
                                   cwd="/", env=self.env, capture_output=True, check=True)
        self.env = json.loads(env_probe.stdout)
        command = ["/usr/bin/env", "-i", *[key + "=" + value for key, value in self.env.items()],
                   sys.executable, "-I", "-S", "-B", str(self.launcher)]
        self.wrapper.write_text("#!/bin/sh\nexec " + shlex.join(command) + ' "$@"\n')
        self.wrapper.chmod(0o700)
        self.layout = {"paths": stdlib + [str(package)], "stdlib_roots": stdlib,
                       "launcher": str(self.launcher), "wrapper": str(self.wrapper), "environment": self.env}
        (self.root / "layout.json").write_text(json.dumps(self.layout))

    def run_fixture(self, *args):
        result = subprocess.run([str(self.wrapper), *args], cwd="/", env=self.env,
                                capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"", result.stderr.decode())
        return json.loads(result.stdout)

    def assert_state(self, row):
        self.assertTrue(all(row[key] for key in ("isolated", "no_site", "dont_write_bytecode", "before_unpickle")))
        self.assertFalse(any(row["rejections"].values()))
        self.assertEqual(row["path_sha256"], bootstrap.hashlib.sha256((json.dumps(self.layout["paths"], sort_keys=True, indent=2) + "\n").encode()).hexdigest())

    def test_real_spawn_grandchild_fresh_and_resource_tracker(self):
        row = self.run_fixture("positive")
        for stage in (row, row["grandchild"]):
            self.assertEqual(stage["imported"], "synthetic-only")
            self.assert_state(stage["before"])
            self.assert_state(stage["after"])
        self.assert_state(row["fresh"])
        self.assert_state(row["parent"])
        self.assertEqual(row["tracker"], {"launched": True, "exit_zero": True})
        self.assertFalse(list(self.root.rglob("*.pyc")))

    def test_real_smoke_child_functions_import_only_fake_package_after_bootstrap(self):
        runtime = (ROOT / "scripts/engine_import_smoke.py").read_bytes()
        (self.root / "packages/smoke_runtime.py").write_bytes(SOURCE + b"\n" + runtime)
        driver = DRIVER.replace("STATE = install(LAYOUT)",
            "STATE = install(LAYOUT)\nimport smoke_runtime\nsmoke_runtime.MODULES = ('fake_engine',)")
        driver = driver.replace('    if ARGS == ["fresh"]:',
            '    if ARGS == ["runtime"]:\n'
            '        try: result = smoke_runtime.child(1)\n'
            '        finally: stop_tracker(STATE)\n'
            '        print(json.dumps(result)); return\n'
            '    if ARGS == ["fresh"]:')
        self.launcher.write_bytes(SOURCE + driver.encode())
        row = self.run_fixture("runtime")
        for stage in (row, row["grandchild"]):
            self.assert_state(stage["before_unpickle"])
            self.assert_state(stage["state"])
            self.assertEqual(stage["imports"], [{"module": "fake_engine", "status": "pass"}])
            self.assertFalse(any(stage["guard_rejections"].values()))

    def test_launch_state_and_import_rejections_are_fixed(self):
        for name in ("fork", "forkserver", "direct-fork", "direct-forkserver", "method", "numa", "ordinary-python",
                     "arbitrary-code", "shell", "site", "addsitedir", "sitecustomize", "usercustomize", "path", "env",
                     "preparation", "main-module"):
            with self.subTest(name=name):
                row = self.run_fixture("reject", name)
                self.assertTrue(row["rejected"])
                self.assertEqual(row["message"], "sealed Python operation rejected")
                self.assertGreater(sum(row["failures"].values()), 0)
                self.assertNotIn("private", json.dumps(row))

    def test_only_exact_spawn_and_tracker_forms_parse(self):
        good = ["-B", "-S", "-I", "-c", "from multiprocessing.spawn import spawn_main; spawn_main(tracker_fd=3, pipe_handle=4)", "--multiprocessing-fork"]
        self.assertEqual(bootstrap.route(good, check_fd=False), ("spawn", {"tracker_fd": 3, "pipe_handle": 4}))
        tracker = good[:4] + ["from multiprocessing.resource_tracker import main;main(3)"]
        self.assertEqual(bootstrap.route(tracker, check_fd=False), ("tracker", [3]))
        for args in (good + ["extra"], good[:4] + ["import os; os.system('private')"],
                     good[:4] + ["from multiprocessing.forkserver import main; main(3,4,[])"],
                     ["-I", "-S", "-B"] + good[3:],
                     good[:4] + [good[4].replace("tracker_fd=3", "tracker_fd=True")] + good[5:],
                     good[:4] + [good[4] + "; print('private')"] + good[5:]):
            with self.subTest(args=args), self.assertRaises(ValueError):
                bootstrap.route(args, check_fd=False)

    def test_preparation_rejects_every_mutable_bootstrap_field(self):
        valid = {"log_to_stderr": False, "authkey": b"test", "name": "unit", "sys_path": self.layout["paths"],
                 "sys_argv": [self.layout["launcher"]], "orig_dir": "/", "dir": "/", "start_method": "spawn",
                 "init_main_from_path": self.layout["launcher"]}
        bootstrap.check_preparation(valid, self.layout)
        for key, value in (("sys_path", ["/private"]), ("sys_argv", ["-c"]), ("orig_dir", "/tmp"),
                           ("dir", "/tmp"), ("start_method", "fork"), ("init_main_from_path", "/private.py"),
                           ("init_main_from_name", "private"), ("log_to_stderr", True), ("extra", 1)):
            data = copy.deepcopy(valid)
            data[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                bootstrap.check_preparation(data, self.layout)

    def test_safe_source_reader_rejects_links_fifo_and_size(self):
        source = self.root / "regular"
        source.write_bytes(b"ok")
        self.assertEqual(bootstrap.read_source(str(source)), b"ok")
        with self.assertRaises(ValueError):
            bootstrap.read_source(str(source), 1)
        link = self.root / "link"
        link.symlink_to(source)
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        hard = self.root / "hard"
        os.link(source, hard)
        for path in (source, link, fifo, hard, self.root):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                bootstrap.read_source(str(path))

    def test_tracker_nonzero_restart_timeout_and_unknown_ownership_reject(self):
        from multiprocessing import resource_tracker
        for outcome in ("nonzero", "timeout", "restart", "unknown"):
            with self.subTest(outcome=outcome):
                read, write = os.pipe()
                tracker = types.SimpleNamespace(_lock=threading.RLock(), _pid=300000, _fd=read)
                state = {"check": lambda: None, "tracker_pids": [300000]}
                if outcome == "restart": state["tracker_pids"] = [299999, 300000]
                if outcome == "unknown": state["tracker_pids"] = []
                try:
                    with mock.patch.object(resource_tracker, "_resource_tracker", tracker), \
                         mock.patch.object(bootstrap.os, "waitpid", return_value=(0, 0) if outcome == "timeout" else (300000, 256)), \
                         mock.patch.object(bootstrap.os, "kill") as kill, \
                         mock.patch("time.monotonic", side_effect=[0, 11]):
                        with self.assertRaises(ValueError) as caught:
                            bootstrap.stop_tracker(state)
                        self.assertNotIn("300000", str(caught.exception))
                        self.assertEqual(kill.call_count, int(outcome == "timeout"))
                finally:
                    if tracker._fd is not None: os.close(tracker._fd)
                    os.close(write)


if __name__ == "__main__":
    unittest.main()
