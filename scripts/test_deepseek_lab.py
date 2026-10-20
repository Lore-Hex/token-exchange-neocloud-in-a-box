# SPDX-License-Identifier: Apache-2.0
"""Software-only lab boundary tests. No engine, GPU, Docker or model execution."""

import ast
import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import sysconfig
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def module(path):
    result = types.ModuleType(Path(path).stem.replace("-", "_"))
    result.__file__ = str(ROOT / path)
    exec(compile((ROOT / path).read_bytes(), result.__file__, "exec"), result.__dict__)
    return result


helpers = module("scripts/engine_python_bootstrap.py")
bootstrap = module("scripts/engine_lab_bootstrap.py")
lab = module("scripts/engine_lab.py")
lab.require = helpers.require
lab.os = os
lab.sys = sys
probe = module("scripts/engine_lab_probe.py")
host = module("host/record-deepseek-lab.py")
snapshot = module("scripts/e2e/snapshot.py")
lab.PROCESSES = snapshot

TEMPLATE = """
import sys
if sys.platform != "linux":
    import torch  # noqa: F401
from ctypes import cdll
cdll.LoadLibrary("__lib_path__")
"""
FAKE_VEC = ("class VecISA:\n    _avx_py_load = " + repr(TEMPLATE) + "\n").encode()

# Original synthetic source contracts, not executed upstream setuptools code.
FAKE_SETUPTOOLS = {
    "setuptools/__init__.py": '''
import os, sys
vendor = os.path.join(os.path.dirname(__file__), "_vendor")
sys.path.extend([] if vendor in sys.path else [vendor])
import _distutils_hack.override
import distutils.core
import lab_vendor
VALUE = distutils.core.VALUE + lab_vendor.VALUE
''',
    "_distutils_hack/__init__.py": '''
import sys
class DistutilsMetaFinder:
    def find_spec(self, fullname, path, target=None):
        import importlib, importlib.util, importlib.abc
        mod = importlib.import_module("setuptools._distutils")
        class Loader(importlib.abc.Loader):
            def create_module(self, spec):
                mod.__name__ = "distutils"
                return mod
            def exec_module(self, module): pass
        return importlib.util.spec_from_loader("distutils", Loader(), origin=mod.__file__)
DISTUTILS_FINDER = DistutilsMetaFinder()
def insert_shim(): sys.meta_path.insert(0, DISTUTILS_FINDER)
def _remove_shim(): sys.meta_path.remove(DISTUTILS_FINDER)
def add_shim(): insert_shim()
class shim:
    def __enter__(self): insert_shim()
    def __exit__(self, *args): _remove_shim()
def do_override():
    import importlib
    with shim(): importlib.import_module("distutils")
    importlib.import_module("distutils.core")
''',
    "_distutils_hack/override.py": '__import__("_distutils_hack").do_override()\n',
    "setuptools/_distutils/__init__.py": 'VALUE = "source"\n',
}


class VecTests(unittest.TestCase):
    def test_exact_template_only_not_generic_python(self):
        source = types.SimpleNamespace(require=helpers.require, read_source=mock.Mock(return_value=FAKE_VEC))
        layout = {"selected_root": "/selected", "paths": ["/selected", "/selected/setuptools/_vendor"]}
        with mock.patch.object(bootstrap, "VECISA_SOURCE_SHA", hashlib.sha256(FAKE_VEC).hexdigest()):
            path = "/cache/inductor/ab/candidate.so"
            self.assertEqual(bootstrap.vecisa_library(["-c", TEMPLATE.replace("__lib_path__", path)],
                                                      layout, source), path)
            source.read_source.assert_called_once_with("/selected/torch/_inductor/cpu_vec_isa.py", 32768)
            variants = [
                TEMPLATE.replace("__lib_path__", "/private/sentinel.so"),
                TEMPLATE.replace("__lib_path__", "/cache/inductor/../sentinel.so"),
                TEMPLATE.replace("__lib_path__", "/cache/inductor//sentinel.so"),
                TEMPLATE.replace("__lib_path__", path) + "\nprint('private')",
                TEMPLATE.replace("__lib_path__", path).replace("cdll.LoadLibrary", "exec"),
                TEMPLATE.replace("__lib_path__", "/cache/inductor/a';print('private').so"),
                "print('private')", "x" * 4097,
            ]
            for text in variants:
                with self.subTest(text=text[:20]), self.assertRaises((ValueError, SyntaxError)):
                    bootstrap.vecisa_library(["-c", text], layout, source)
        with self.assertRaises(ValueError):
            bootstrap.vecisa_library(["-c", TEMPLATE], layout, source)

    def test_selected_root_missing_or_inconsistent_never_reads(self):
        source = types.SimpleNamespace(require=helpers.require, read_source=mock.Mock(return_value=FAKE_VEC))
        base = {"selected_root": "/selected", "paths": ["/selected", "/selected/setuptools/_vendor"]}
        for changes in ({"selected_root": None}, {"selected_root": "/private/sentinel"},
                        {"selected_root": "/selected/setuptools/_vendor"}, {"selected_root": "relative"},
                        {"selected_root": "/selected/.."}, {"paths": ["/selected"]},
                        {"paths": ["/selected", "/unreviewed"]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                bootstrap.vecisa_library(["-c", TEMPLATE], {**base, **changes}, source)
        with self.assertRaises(ValueError):
            bootstrap.vecisa_library(["-c", TEMPLATE], {"paths": base["paths"]}, source)
        source.read_source.assert_not_called()

    def test_library_nofollow_regular_and_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            library = root / "generated.so"
            library.write_bytes(b"synthetic-not-a-real-library")
            with mock.patch.object(bootstrap.ctypes, "CDLL") as dlopen:
                bootstrap.vecisa_load(str(library), helpers)
                self.assertRegex(dlopen.call_args.args[0], r"^/proc/self/fd/[0-9]+$")
            for kind in ("symlink", "hardlink", "fifo", "empty", "large"):
                path = root / (kind + ".so")
                if kind == "symlink":
                    path.symlink_to(library)
                elif kind == "hardlink":
                    os.link(library, path)
                elif kind == "fifo":
                    os.mkfifo(path)
                elif kind == "empty":
                    path.touch()
                else:
                    with path.open("wb") as stream:
                        stream.truncate((64 << 20) + 1)
                with self.subTest(kind=kind), mock.patch.object(bootstrap.ctypes, "CDLL") as dlopen:
                    with self.assertRaises((ValueError, OSError)):
                        bootstrap.vecisa_load(str(path), helpers)
                    dlopen.assert_not_called()
                path.unlink()
            with mock.patch.object(bootstrap.ctypes, "CDLL", side_effect=lambda _: library.write_bytes(b"changed")):
                with self.assertRaises(ValueError):
                    bootstrap.vecisa_load(str(library), helpers)

    def test_wrapper_restores_fixed_loader_environment(self):
        plan = {"fixed_engine_environment": {"VLLM_PLUGINS": ""}}
        env = lab.lab_environment(plan)
        expected = "/usr/local/lib/python3.12/dist-packages/torch/lib:" + lab.IMAGE_LD_PATH
        self.assertEqual(env["LD_LIBRARY_PATH"], expected)
        tokens = shlex.split(lab.wrapper_bytes("/usr/bin/python3.12", env, "a" * 64).decode().splitlines()[1][5:])
        self.assertEqual(tokens[:2], ["/usr/bin/env", "-i"])
        self.assertIn("LD_LIBRARY_PATH=" + expected, tokens)
        self.assertEqual(tokens[tokens.index("/usr/bin/python3.12") + 1:-1],
                         ["-I", "-S", "-B", "/opt/nib/launch.py", "a" * 64])
        self.assertIn("-S", tokens)
        self.assertIn("-B", tokens)
        self.assertNotIn("TORCHINDUCTOR_VEC_ISA_OK", env)


DRIVER = r'''
import json
from pathlib import Path
LAYOUT = json.loads(Path(__file__).with_name("layout.json").read_text())
ARGS = sys.argv[1:]
STATE = install_lab(LAYOUT, sys.modules[__name__])
LAYOUT = STATE["layout"]
def observe():
    STATE["check"]()
    import fake_engine
    from importlib import metadata
    return {"isolated": bool(sys.flags.isolated), "no_site": bool(sys.flags.no_site),
            "no_bytecode": sys.dont_write_bytecode, "paths": sys.path == LAYOUT["paths"],
            "value": fake_engine.VALUE, "version": metadata.version("lab-fake"),
            "site": "sitecustomize" in sys.modules, "before_unpickle": True}
class Before:
    def __reduce__(self): return observe, ()
def child(connection, observation, depth):
    result = {"before": observation, "after": observe()}
    if depth:
        result["grandchild"] = spawn_one(depth - 1)
    connection.send(result)
    connection.close()
def spawn_one(depth):
    import multiprocessing as mp
    left, right = mp.Pipe()
    process = mp.Process(target=child, args=(right, Before(), depth))
    process.start()
    right.close()
    assert left.poll(15)
    result = left.recv()
    process.join(15)
    assert process.exitcode == 0
    return result
if __name__ == "__main__":
    if ARGS == ["positive"]:
        result = {"parent": observe(), "child": spawn_one(1)}
        import subprocess
        fresh = subprocess.run([LAYOUT["wrapper"], "fresh"], capture_output=True, check=True, timeout=20)
        result["fresh"] = json.loads(fresh.stdout)
        from multiprocessing import resource_tracker
        resource_tracker.ensure_running()
        resource_tracker._resource_tracker._stop()
        result["tracker"] = True
        print(json.dumps(result))
    elif ARGS == ["fresh"]:
        print(json.dumps(observe()))
    elif ARGS and ARGS[0] == "reject":
        import multiprocessing as mp
        from importlib import metadata
        try:
            if ARGS[1] == "fork": mp.get_context("fork")
            elif ARGS[1] == "forkserver": mp.get_context("forkserver")
            elif ARGS[1] == "executable": mp.set_executable("/private/sentinel")
            elif ARGS[1] == "metadata": list(metadata.distributions(path=["/private/sentinel"]))
            elif ARGS[1] == "finder": sys.meta_path.append(importlib.machinery.PathFinder); STATE["check"]()
            elif ARGS[1] == "path": sys.path.append("/private/sentinel"); STATE["check"]()
            elif ARGS[1] == "site": import site; site.main()
            elif ARGS[1] == "numa": os.environ["NUMACTL_PYTHON"] = "/private/sentinel"; STATE["check"]()
            elif ARGS[1] == "pth": import sitecustomize
            else: dispatch_lab(["-c", "print('private')"], STATE, sys.modules[__name__])
        except ValueError:
            print('{"rejected":true}')
        else: raise AssertionError("boundary accepted")
    else: dispatch_lab(ARGS, STATE, sys.modules[__name__])
'''


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.package = self.root / "packages"
        self.package.mkdir()
        (self.package / "fake_engine.py").write_text("VALUE='source-not-bytecode'\n")
        (self.package / "sitecustomize.py").write_text("raise AssertionError('must not run')\n")
        (self.package / "bad.pth").write_text("import sitecustomize\n")
        dist = self.package / "lab_fake-1.0.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text("Name: lab-fake\nVersion: 1.0\n")
        for name, text in FAKE_SETUPTOOLS.items():
            path = self.package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        vendor = self.package / "setuptools/_vendor"
        vendor.mkdir()
        (vendor / "lab_vendor.py").write_text('VALUE="-vendor"\n')
        (self.package / "setuptools/_distutils/core.py").write_text('VALUE="distutils"\n')
        dist = self.package / "setuptools-80.10.2.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text("Name: setuptools\nVersion: 80.10.2\n")
        stdlib = [sysconfig.get_path("stdlib"), sysconfig.get_config_var("DESTSHARED")]
        self.layout = {"paths": stdlib + [str(self.package)], "stdlib_roots": stdlib,
                       "launcher": str(self.root / "launch.py"), "wrapper": str(self.root / "python")}
        (self.root / "layout.json").write_text(json.dumps(self.layout))
        self.source = (ROOT / "scripts/engine_python_bootstrap.py").read_bytes() + b"\n" + (ROOT / "scripts/engine_lab_bootstrap.py").read_bytes()
        pins = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in FAKE_SETUPTOOLS.items()}
        self.source += ("\nSETUPTOOLS_SOURCES=" + repr(pins) + "\n").encode()
        (self.root / "launch.py").write_bytes(self.source + DRIVER.encode())
        env = ["VLLM_PLUGINS=", "VLLM_WORKER_MULTIPROC_METHOD=spawn", "PATH=/usr/bin:/bin", "LC_ALL=C.UTF-8"]
        (self.root / "python").write_text("#!/bin/sh\nexec " + shlex.join(
            ["/usr/bin/env", "-i", *env, sys.executable, "-I", "-S", "-B", self.layout["launcher"]]) + ' "$@"\n')
        (self.root / "python").chmod(0o700)

    def run_fixture(self, *args):
        result = subprocess.run([self.layout["wrapper"], *args], cwd="/", capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        return json.loads(result.stdout)

    def test_real_spawn_grandchild_fresh_tracker_before_unpickle(self):
        row = self.run_fixture("positive")
        observations = [row["parent"], row["fresh"], row["child"]["before"], row["child"]["after"],
                        row["child"]["grandchild"]["before"], row["child"]["grandchild"]["after"]]
        for item in observations:
            self.assertEqual(item, {"isolated": True, "no_site": True, "no_bytecode": True, "paths": True,
                                    "value": "source-not-bytecode", "version": "1.0", "site": False, "before_unpickle": True})
        self.assertTrue(row["tracker"])
        self.assertFalse(list(self.root.rglob("*.pyc")))

    def test_fork_forkserver_paths_numa_and_generic_python_rejected(self):
        for variant in ("fork", "forkserver", "executable", "metadata", "finder", "path", "site", "numa", "pth", "python"):
            with self.subTest(variant=variant):
                self.assertEqual(self.run_fixture("reject", variant), {"rejected": True})

    def test_timestamp_valid_stale_bytecode_ignored(self):
        import importlib.util
        import marshal
        import struct
        source = self.package / "fake_engine.py"
        cache = Path(importlib.util.cache_from_source(str(source)))
        cache.parent.mkdir()
        cache.write_bytes(importlib.util.MAGIC_NUMBER
                          + struct.pack("<III", 0, int(source.stat().st_mtime), source.stat().st_size)
                          + marshal.dumps(compile("raise RuntimeError('private bytecode ran')", str(source), "exec")))
        self.assertEqual(self.run_fixture("fresh")["value"], "source-not-bytecode")

    def test_preparation_tamper_rejected_before_object_unpickle(self):
        driver = DRIVER.replace('    if ARGS == ["positive"]:', '''    if ARGS == ["tamper"]:
        import multiprocessing.spawn as spawn
        data = spawn.get_preparation_data("safe")
        data["sys_path"] = ["/private/sentinel"]
        try: spawn.prepare(data)
        except ValueError: print('{"rejected":true}')
        else: raise AssertionError("preparation accepted")
    elif ARGS == ["positive"]:''')
        (self.root / "launch.py").write_bytes(self.source + driver.encode())
        self.assertEqual(self.run_fixture("tamper"), {"rejected": True})

    def test_backport_metadata_preserves_roots_parent_and_children(self):
        fixture = module("scripts/test_engine_python_bootstrap.py")
        backport = self.package / "importlib_metadata"
        backport.mkdir()
        (backport / "__init__.py").write_text(fixture.BACKPORT)
        (backport / "_compat.py").write_text(fixture.BACKPORT_COMPAT)
        dist = self.package / "importlib_metadata-8.7.1.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text("Name: importlib_metadata\nVersion: 8.7.1\n")
        pins = {name: hashlib.sha256((self.package / name).read_bytes()).hexdigest()
                for name in ("importlib_metadata/__init__.py", "importlib_metadata/_compat.py")}
        driver = DRIVER.replace("    import fake_engine", """    import fake_engine
    import importlib_metadata
    assert importlib_metadata.version("lab-fake") == "1.0"
    try: list(importlib_metadata.distributions(path=["/private/sentinel"]))
    except ValueError: pass
    else: raise AssertionError("outside metadata accepted")""")
        (self.root / "launch.py").write_bytes(self.source + ("\nMETADATA_SOURCES=" + repr(pins) + "\n").encode() + driver.encode())
        row = self.run_fixture("positive")
        self.assertEqual(row["child"]["grandchild"]["after"]["version"], "1.0")

    def test_setuptools_source_adapter_parent_spawn_grandchild_and_fresh(self):
        import importlib.util
        import marshal
        import struct
        for relative in FAKE_SETUPTOOLS:
            source = self.package / relative
            cache = Path(importlib.util.cache_from_source(str(source)))
            cache.parent.mkdir(exist_ok=True)
            cache.write_bytes(importlib.util.MAGIC_NUMBER
                              + struct.pack("<III", 0, int(source.stat().st_mtime), source.stat().st_size)
                              + marshal.dumps(compile("raise RuntimeError('private cached setuptools')", str(source), "exec")))
        driver = DRIVER.replace("    import fake_engine", """    import fake_engine
    before = list(sys.meta_path)
    import setuptools, _distutils_hack, distutils
    assert setuptools.VALUE == "distutils-vendor"
    assert sys.meta_path == before
    assert not STATE["distutils_active"]
    assert sys.path.count(LAYOUT["paths"][-1]) == 1
    with _distutils_hack.shim():
        STATE["check"]()
        assert sys.meta_path == before
    STATE["check"]()""")
        (self.root / "launch.py").write_bytes(self.source + driver.encode())
        result = self.run_fixture("positive")
        self.assertTrue(result["child"]["grandchild"]["before"]["paths"])
        self.assertTrue(result["fresh"]["paths"])

    def test_installed_layout_vecisa_handoff_parent_spawn_grandchild_and_fresh(self):
        source = self.package / "torch/_inductor/cpu_vec_isa.py"
        source.parent.mkdir(parents=True)
        source.write_bytes(FAKE_VEC)
        shadow = self.package / "setuptools/_vendor/torch/_inductor/cpu_vec_isa.py"
        shadow.parent.mkdir(parents=True)
        shadow.write_bytes(b"not the selected package source\n")
        path = "/cache/inductor/ab/synthetic.so"
        call = '    assert vecisa_library(["-c", ' + repr(TEMPLATE.replace("__lib_path__", path)) + \
               '], STATE["layout"], sys.modules[__name__]) == ' + repr(path)
        driver = DRIVER.replace("    import fake_engine", "    import fake_engine\n" + call +
                                '\n    assert STATE["layout"]["selected_root"] == LAYOUT["paths"][-2]')
        pin = "\nVECISA_SOURCE_SHA=" + repr(hashlib.sha256(FAKE_VEC).hexdigest()) + "\n"
        (self.root / "launch.py").write_bytes(self.source + pin.encode() + driver.encode())
        result = self.run_fixture("positive")
        self.assertTrue(result["parent"]["paths"])
        self.assertTrue(result["child"]["grandchild"]["before"]["paths"])
        self.assertTrue(result["fresh"]["paths"])

    def test_setuptools_unadapted_path_and_finder_are_rejected(self):
        # Positive controls reproduce both original strict-boundary failures.
        driver = DRIVER.replace("    import fake_engine", """    import fake_engine
    for field in ("path", "finder"):
        before = list(sys.path), list(sys.meta_path)
        if field == "path": sys.path.append(LAYOUT["paths"][-1])
        else:
            import _distutils_hack
            sys.meta_path.insert(0, _distutils_hack.DISTUTILS_FINDER)
        try: STATE["check"]()
        except ValueError: pass
        else: raise AssertionError("unadapted mutation accepted")
        sys.path[:], sys.meta_path[:] = before
    STATE["check"]()""")
        (self.root / "launch.py").write_bytes(self.source + driver.encode())
        self.assertTrue(self.run_fixture("fresh")["paths"])

    def test_setuptools_source_version_root_and_cached_module_rejections(self):
        def rejected():
            result = subprocess.run([self.layout["wrapper"], "fresh"], cwd="/", capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(b"private-payload-executed", result.stdout + result.stderr)
        for name, original in FAKE_SETUPTOOLS.items():
            path = self.package / name
            path.write_text("raise RuntimeError('private-payload-executed')\n")
            with self.subTest(source=name):
                rejected()
            path.write_text(original)
        driver = DRIVER.replace("    import fake_engine", "    import fake_engine\n    import setuptools")
        (self.root / "launch.py").write_bytes(self.source + driver.encode())
        metadata = self.package / "setuptools-80.10.2.dist-info/METADATA"
        original = metadata.read_bytes()
        metadata.write_text("Name: setuptools\nVersion: 80.10.1\n")
        rejected()
        metadata.write_bytes(original)
        vendor = self.package / "setuptools/_vendor"
        moved = self.root / "outside"
        vendor.rename(moved)
        vendor.symlink_to(moved, target_is_directory=True)
        rejected()
        vendor.unlink()
        moved.rename(vendor)
        for cached in ("setuptools", "_distutils_hack", "distutils"):
            prefix = "\nsys.modules[" + repr(cached) + "] = type(sys)(" + repr(cached) + ")\n"
            (self.root / "launch.py").write_bytes(self.source + prefix.encode() + driver.encode())
            with self.subTest(cached=cached):
                rejected()

    def test_setuptools_rechecks_bytes_and_exact_origin_at_import(self):
        source = self.package / "_distutils_hack/override.py"
        original = source.read_bytes()
        driver = DRIVER.replace("    import fake_engine", """    import fake_engine
    Path(LAYOUT["paths"][-2] + "/_distutils_hack/override.py").write_text(
        "raise RuntimeError('private-code-ran')\\n")
    try: import setuptools
    except ValueError: pass
    else: raise AssertionError("changed source accepted")
    STATE["check"]()""")
        (self.root / "launch.py").write_bytes(self.source + driver.encode())
        self.assertTrue(self.run_fixture("fresh")["paths"])
        source.write_bytes(original)
        driver = DRIVER.replace("    import fake_engine", """    import fake_engine
    import _distutils_hack
    _distutils_hack.__path__[:] = [LAYOUT["paths"][-2] + "/shadow"]
    try: import setuptools
    except ValueError: pass
    else: raise AssertionError("alternate origin accepted")
    STATE["check"]()""")
        shadow = self.package / "shadow"
        shadow.mkdir()
        (shadow / "override.py").write_bytes(original)
        (self.root / "launch.py").write_bytes(self.source + driver.encode())
        self.assertTrue(self.run_fixture("fresh")["paths"])


class BoundedProcessTests(unittest.TestCase):
    def test_quiet_command_bounded_output_both_streams(self):
        for stream in (1, 2):
            with self.subTest(stream=stream), self.assertRaises((ValueError, OSError, RuntimeError, subprocess.SubprocessError)):
                lab.quiet_command([sys.executable, "-I", "-S", "-B", "-c",
                                   f"import os;os.write({stream},b'x'*1000000)"], 5, output=True)

    def test_timeout_kills_descendant_and_no_delayed_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = str(Path(tmp) / "orphan")
            code = ("import subprocess,sys,time;subprocess.Popen([sys.executable,'-I','-S','-c',"
                    + repr("import time;time.sleep(1);open(" + repr(marker) + ",'w').write('bad')") + "]);time.sleep(30)")
            with self.assertRaises(subprocess.TimeoutExpired):
                lab.quiet_command([sys.executable, "-I", "-S", "-B", "-c", code], .15)
            time.sleep(1.1)
            self.assertFalse(Path(marker).exists())

    def test_quiet_command_flags_for_hardware(self):
        lab.GPU_PROBE = probe
        with mock.patch.object(lab, "quiet_command", return_value=b"{}") as command:
            with self.assertRaises((KeyError, ValueError)):
                lab.hardware()
            self.assertEqual(command.call_args.args[0], ["/opt/nib/python", "probe"])
            self.assertNotIn("python3", command.call_args.args[0])

    def test_read_fifo_symlink_hardlink_rejected_without_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            target = root / "file"
            target.write_bytes(b"safe")
            for kind in ("fifo", "symlink", "hardlink"):
                path = root / kind
                if kind == "fifo": os.mkfifo(path)
                elif kind == "symlink": path.symlink_to(target)
                else: os.link(target, path)
                before = time.monotonic()
                with self.assertRaises((ValueError, OSError)):
                    host.read(path)
                self.assertLess(time.monotonic() - before, 1)

    def test_cancel_once_cleanup_not_interrupted(self):
        with mock.patch.object(host, "CANCELLED", False), mock.patch.object(host, "CLEANING", False):
            with self.assertRaises(InterruptedError): host.cancel()
            host.cancel()
        with mock.patch.object(host, "CANCELLED", False), mock.patch.object(host, "CLEANING", True):
            host.cancel()

    def test_watchdog_real_parent_death_cleans_owned_fake_resource(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            marker, ready = root / "cleaned", root / "ready"
            # Only the host lifecycle runs. The fake cleanup writes a marker;
            # no Docker client, image or hardware is involved.
            watchdog = root / "watch.py"
            watchdog.write_text(
                "import sys,types,pathlib,os,time\nm=types.ModuleType('fixture');m.__file__=" + repr(host.__file__) + "\n"
                "exec(compile(pathlib.Path(m.__file__).read_bytes(),m.__file__,'exec'),m.__dict__)\n"
                "m.WATCHDOG_SETTLE_SECONDS=0\nm.bundle_inputs=lambda *a:(None,None,None,None)\n"
                "m.cleanup=lambda *a:pathlib.Path(" + repr(str(marker)) + ").write_text('confirmed')\n"
                "pathlib.Path(" + repr(str(ready)) + ").write_text('ready')\n"
                "m.watchdog(None,None,'nib-deepseek-'+'a'*32,'b'*32,time.time()+30,int(sys.argv[1]),'unix:///var/run/docker.sock')\n")
            parent = root / "parent.py"
            parent.write_text("import os,subprocess,sys,time\nr,w=os.pipe()\n"
                              "p=subprocess.Popen([sys.executable,'-I','-S'," + repr(str(watchdog)) +
                              ",str(r)],pass_fds=(r,),start_new_session=True)\n"
                              "os.close(r)\nprint(p.pid,flush=True)\ntime.sleep(30)\n")
            process = subprocess.Popen([sys.executable, "-I", "-S", str(parent)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                self.assertTrue(process.stdout.readline().strip().isdigit())
                end = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < end:
                    time.sleep(.01)
                self.assertTrue(ready.exists())
                process.kill()
                process.wait(timeout=5)
                observed = None
                while time.monotonic() < end:
                    if marker.exists():
                        observed = marker.read_text()
                        if observed:
                            break
                    time.sleep(.01)
                self.assertEqual(observed, "confirmed")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                process.stdout.close()
                process.stderr.close()


class HardwareTests(unittest.TestCase):
    def raw(self):
        return {"driver": "595.45.01", "cuda_driver": 13000, "feature": 1, "environment": 2,
                "devtools": 0, "multigpu": 2, "ready": 1,
                "devices": [{"b200": True, "protected_kib": 180 << 20} for _ in range(4)]}

    def test_positive_named_values_and_fixed_report(self):
        raw = self.raw()
        row = probe.validate(raw)
        self.assertEqual(probe.validate_report(row), row)
        self.assertEqual(row["multigpu"], "nvle")
        self.assertFalse(row["attestation_verified"])
        raw["multigpu"] = 1
        self.assertEqual(probe.validate(raw)["multigpu"], "protected-pcie")
        self.assertNotIn("UUID", json.dumps(row))

    def test_unknown_mixed_plain_and_malformed_rejected(self):
        for key, value in (("feature", 0), ("feature", True), ("environment", 1), ("devtools", 1),
                           ("multigpu", 0), ("multigpu", 99), ("ready", 0), ("cuda_driver", 12080),
                           ("driver", "private-sentinel"), ("private", "sentinel")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                probe.validate({**self.raw(), key: value})
        for field, value in (("b200", False), ("protected_kib", 0), ("protected_kib", True)):
            raw = self.raw()
            raw["devices"][3][field] = value
            with self.assertRaises(ValueError):
                probe.validate(raw)
        row = probe.validate(self.raw())
        with self.assertRaises(ValueError):
            probe.validate_report({**row, "private": "sentinel"})

    def test_never_nvml_setter(self):
        instance = object.__new__(probe.NVML)
        instance.library = mock.Mock()
        with self.assertRaises(ValueError):
            instance.call("nvmlSystemSetConfComputeGpusReadyState", 1)
        instance.library.assert_not_called()


class DiagnosticTests(unittest.TestCase):
    def library(self, *, codes=None, count=4, feature=1, environment=2, devtools=0, multigpu=2,
                ready=1, memory=180 << 20, name=b"NVIDIA B200 private-name-sentinel",
                driver=b"595.91.07"):
        codes = codes or {}
        library = types.SimpleNamespace()
        for symbol in probe.CALL_STAGES:
            def call(*args, symbol=symbol):
                code = codes.get(symbol, 0)
                if isinstance(code, Exception):
                    raise code
                if code != 0:
                    return code
                if symbol == "nvmlDeviceGetCount_v2":
                    args[0]._obj.value = count
                elif symbol == "nvmlSystemGetConfComputeSettings":
                    settings = args[0]._obj
                    settings.ccFeature, settings.environment = feature, environment
                    settings.devToolsMode, settings.multiGpuMode = devtools, multigpu
                elif symbol == "nvmlSystemGetConfComputeGpusReadyState":
                    args[0]._obj.value = ready
                elif symbol == "nvmlSystemGetCudaDriverVersion_v2":
                    args[0]._obj.value = 13000
                elif symbol == "nvmlSystemGetDriverVersion":
                    args[0].value = driver
                elif symbol == "nvmlDeviceGetHandleByIndex_v2":
                    args[1]._obj.value = 123456  # Never emitted.
                elif symbol == "nvmlDeviceGetName":
                    args[1].value = name
                elif symbol == "nvmlDeviceGetConfComputeMemSizeInfo":
                    args[1]._obj.protectedMemSizeKib = memory
                    args[1]._obj.unprotectedMemSizeKib = 0
                return 0
            setattr(library, symbol, call)
        return library

    def diagnose(self, **kwargs):
        with mock.patch.object(probe.c, "CDLL", return_value=self.library(**kwargs)):
            row = probe.diagnose()
        probe.validate_trace(row)
        self.assertNotIn("sentinel", json.dumps(row))
        return row

    def child(self, trace=None):
        return {"stage": "probe", "invocation": {key: True for key in
                ("linux", "isolated", "no_site", "no_bytecode", "argument_shape")},
                "bootstrap": {"stage": "complete", "source_index": None},
                "probe": trace if trace is not None else self.diagnose()}

    def test_numeric_success_is_never_functional_or_qualification(self):
        row = self.diagnose()
        self.assertTrue(row["policy_checks_passed"])
        self.assertIsNone(row["first_failure"])
        self.assertEqual(len(row["calls"]), 19)
        self.assertEqual(row["observed"]["count"], 4)
        self.assertEqual((row["settings_input_version"], row["settings_struct_bytes"]), (0x01000014, 20))
        self.assertEqual(row["calls"][-1], {"stage": "shutdown", "device": None, "return_code": 0})
        with mock.patch.object(lab, "GPU_PROBE", probe), \
                mock.patch.object(lab, "quiet_command", return_value=json.dumps(self.child(row)).encode()) as command:
            outer = lab.diagnostic_request()
        command.assert_called_once_with(["/opt/nib/python", "probe-diagnostic"], 15, output=True)
        probe.validate_diagnostic(outer)
        self.assertFalse(outer["functional_pass"])
        self.assertFalse(outer["confidential"])
        self.assertFalse(outer["deployable"])

    def test_load_each_call_failure_and_shutdown_are_separate(self):
        with mock.patch.object(probe.c, "CDLL", side_effect=OSError("/private/loader-sentinel")):
            row = probe.diagnose()
        self.assertEqual(row["first_failure"], "ctypes-loading")
        self.assertEqual(row["calls"], [])
        for name, stage in probe.CALL_STAGES.items():
            with self.subTest(stage=stage):
                row = self.diagnose(codes={name: 26})
                self.assertEqual(row["first_failure"], stage)
                self.assertFalse(row["policy_checks_passed"])
                self.assertIn(26, [c["return_code"] for c in row["calls"]])
        row = self.diagnose(codes={"nvmlSystemGetConfComputeSettings": 26, "nvmlShutdown": 999})
        self.assertEqual(row["first_failure"], "settings-abi")
        self.assertEqual(row["calls"][-1], {"stage": "shutdown", "device": None, "return_code": 999})
        row = self.diagnose(codes={"nvmlDeviceGetCount_v2": AttributeError("private-symbol-sentinel")})
        self.assertEqual(row["first_failure"], "count")
        self.assertIsNone(row["calls"][1]["return_code"])

    def test_original_count_cc_readiness_memory_policies_still_reject(self):
        for values, stage in (({"count": 8}, "required-count"), ({"feature": 0}, "cc-policy"),
                              ({"environment": 1}, "cc-policy"), ({"devtools": 1}, "cc-policy"),
                              ({"multigpu": 0}, "cc-policy"), ({"ready": 0}, "readiness-policy"),
                              ({"memory": 0}, "memory-policy"), ({"name": b"private-name-sentinel"}, "memory-policy"),
                              ({"driver": b"private-driver-sentinel"}, "driver-policy")):
            with self.subTest(values=values):
                row = self.diagnose(**values)
                self.assertEqual(row["first_failure"], stage)
                self.assertFalse(row["policy_checks_passed"])
        row = self.diagnose(count=8)
        self.assertEqual(row["observed"]["count"], 8)
        self.assertEqual([c["stage"] for c in row["calls"]], ["init", "count", "shutdown"])
        self.assertIsNone(row["observed"]["feature"])

    def test_child_source_binding_bootstrap_and_flags_fail_closed(self):
        fake_sys = types.SimpleNamespace(platform="linux", flags=types.SimpleNamespace(isolated=1, no_site=1),
                                         dont_write_bytecode=True, argv=["fixed", "a" * 64, "probe-diagnostic"],
                                         modules={lab.__name__: lab})
        contract = {"operation": "hardware-diagnostic", "paths": ["/stdlib", "/stdlib/lib-dynload", "/selected"]}
        for failing in ("invocation", "source-binding", "bootstrap", None):
            with self.subTest(failing=failing), mock.patch.object(lab, "sys", fake_sys), \
                    mock.patch.object(lab, "lab_context", return_value=(contract, {}, [], {}, {})) as context, \
                    mock.patch.object(lab, "install_lab", create=True) as install, \
                    mock.patch.object(lab, "GPU_PROBE", probe), \
                    mock.patch.object(probe, "diagnose", return_value=self.diagnose()) as inspect:
                fake_sys.flags.isolated = failing != "invocation"
                if failing == "source-binding":
                    context.side_effect = ValueError("private-source-sentinel")
                if failing == "bootstrap":
                    def fail(*args, checkpoint):
                        checkpoint("setuptools-source", 2)
                        raise ValueError("private-bootstrap-sentinel")
                    install.side_effect = fail
                else:
                    install.side_effect = lambda *args, checkpoint: checkpoint("complete", None)
                row = lab.diagnostic_child("a" * 64)
                self.assertEqual(row["stage"], failing or "probe")
                if failing:
                    inspect.assert_not_called()
                if failing == "invocation":
                    context.assert_not_called()
                if failing == "bootstrap":
                    self.assertEqual(row["bootstrap"], {"stage": "setuptools-source", "source_index": 2})
                probe.validate_diagnostic({"schema": "nib.deepseek-hardware-diagnostic/v1", "status": "BLOCKED",
                                          "deployable": False, "confidential": False, "functional_pass": False,
                                          "stage": "diagnostic", "probe_execution": "completed", "child": row})
                self.assertNotIn("sentinel", json.dumps(row))

    def test_timeout_failed_malformed_children_never_invent_observations(self):
        for effect, expected in ((subprocess.TimeoutExpired("private-sentinel", 15), "timeout"),
                                 (subprocess.CalledProcessError(1, "private-sentinel"), "failed"),
                                 (b'{"private":"sentinel"}', "malformed"), (b"not JSON", "malformed")):
            with self.subTest(expected=expected), mock.patch.object(lab, "GPU_PROBE", probe), \
                    mock.patch.object(lab, "quiet_command") as command:
                if isinstance(effect, Exception):
                    command.side_effect = effect
                else:
                    command.return_value = effect
                row = lab.diagnostic_request()
                probe.validate_diagnostic(row)
                self.assertEqual(row["probe_execution"], expected)
                self.assertIsNone(row["child"])
                self.assertFalse(row["functional_pass"])
                self.assertNotIn("sentinel", json.dumps(row))

    def test_diagnostic_dispatch_never_enters_application_or_hardware_acceptance(self):
        fake_sys = types.SimpleNamespace(flags=types.SimpleNamespace(isolated=1, no_site=1),
                                         dont_write_bytecode=True, argv=["fixed", "a" * 64, "diagnose-hardware"])
        contract = {"operation": "hardware-diagnostic"}
        result = {"fixed": "diagnostic"}
        with mock.patch.object(lab, "sys", fake_sys), \
                mock.patch.object(lab, "lab_context", return_value=(contract, {}, [], {}, {})), \
                mock.patch.object(lab, "diagnostic_request", return_value=result) as diagnostic, \
                mock.patch.object(lab, "application_and_request") as application, \
                mock.patch.object(lab, "hardware") as acceptance, \
                mock.patch.object(lab.signal, "signal"), mock.patch.object(lab.signal, "alarm") as alarm, \
                mock.patch("builtins.print") as output:
            lab.lab_entry()
            diagnostic.assert_called_once_with()
            application.assert_not_called()
            acceptance.assert_not_called()
            alarm.assert_called_once_with(60)
            output.assert_called_once_with(json.dumps(result, sort_keys=True))
            for mode in ("apply", "probe", "engine"):
                fake_sys.argv[-1] = mode
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    lab.lab_entry()
            self.assertEqual(diagnostic.call_count, 1)

    def test_numeric_and_shape_tamper_rejected(self):
        good = self.diagnose()
        for key, value in (("private", "sentinel"), ("required_count", 8),
                           ("settings_struct_bytes", 24), ("first_failure", "private-stage-sentinel")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                probe.validate_trace({**good, key: value})
        for key, value in (("count", True), ("count", 1 << 32), ("ready", "sentinel")):
            bad = copy.deepcopy(good)
            bad["observed"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                probe.validate_trace(bad)
        bad = copy.deepcopy(good)
        bad["calls"][2]["return_code"] = 26
        with self.assertRaises(ValueError):
            probe.validate_trace(bad)
        bad = copy.deepcopy(good)
        bad["devices"][0]["private"] = "sentinel"
        with self.assertRaises(ValueError):
            probe.validate_trace(bad)


class PayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = module("scripts/test_engine_application.py")
        cls.fixtures.ApplicationTests.setUpClass()
        cls.builder = host.load("lab_test_builder", ROOT / host.BUILDER, host.BUILDER_SHA)
        cls.extra = dict(cls.fixtures.ApplicationTests.extra)
        for name in (host.SCRIPT, host.HELPER, host.RUNTIME, host.LAB_BOOTSTRAP, host.PROBE, host.SMOKE, host.CORRECTNESS,
                     host.TEST, host.PACK + "/pack.yaml", host.PACK + "/files.sha256"):
            cls.extra[name] = (ROOT / name).read_bytes()
        cls.bundle = cls.builder.checker.working_bundle(ROOT)
        cls.files, cls.contract = host.make_payload(cls.builder, "1"*40, cls.bundle, cls.extra, b"fake-linux-binary")

    def test_deterministic_payload_complete_hash_binding_and_unchanged_p0(self):
        again, contract = host.make_payload(self.builder, "1"*40, self.bundle, self.extra, b"fake-linux-binary")
        self.assertEqual((again, contract), (self.files, self.contract))
        self.assertEqual(set(self.files) - set(contract["sources"]), {"lab.json", "python"})
        for name, sha in contract["sources"].items():
            self.assertEqual(hashlib.sha256(self.files[name]).hexdigest(), sha)
        self.assertIn(self.extra[host.HELPER], self.files["launch.py"])
        self.assertEqual(contract["pack_bytes"], 510311045295)
        args, env = contract["arguments"], contract["environment"]
        for flag, value in (("--tensor-parallel-size", "4"), ("--data-parallel-size", "1"),
                            ("--pipeline-parallel-size", "1"), ("--max-model-len", "4096"),
                            ("--max-num-seqs", "1"), ("--distributed-executor-backend", "mp")):
            self.assertEqual(args[args.index(flag)+1], value)
        self.assertIn("--no-numa-bind", args)
        self.assertIn("--disable-custom-all-reduce", args)
        self.assertEqual(env["VLLM_CONFIDENTIAL_COMPUTE"], "1")
        for name in ("VLLM_ALLREDUCE_USE_SYMM_MEM", "VLLM_ALLREDUCE_USE_FLASHINFER",
                     "VLLM_ALLREDUCE_USE_FLASHINFER_PCIE_IPC", "VLLM_USE_NCCL_SYMM_MEM", "VLLM_ENABLE_GEMM_RS"):
            self.assertEqual(env[name], "0")
        self.assertEqual(env["VLLM_PLUGINS"], "")
        self.assertNotIn("TORCHINDUCTOR_VEC_ISA_OK", env)
        self.assertFalse(contract["deployable"])

    def test_diagnostic_payload_is_explicit_source_bound_and_denial_only(self):
        files, contract = host.make_payload(self.builder, "1"*40, self.bundle, self.extra,
                                            b"fake-linux-binary", "hardware-diagnostic")
        self.assertEqual(contract["operation"], "hardware-diagnostic")
        self.assertEqual(contract["sources"]["probe.py"], hashlib.sha256(self.extra[host.PROBE]).hexdigest())
        self.assertNotEqual(files["lab.json"], self.files["lab.json"])
        with self.assertRaises(ValueError):
            host.make_payload(self.builder, "1"*40, self.bundle, self.extra, b"fake", "unreviewed")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "payload").mkdir()
            (root / "payload/lab.json").write_bytes(files["lab.json"])
            (root / "payload/probe.py").write_bytes(files["probe.py"])
            args = host.create_command("owned", "owner", root, "/empty-weights", contract, {"config": {"Env": []}})
            self.assertEqual(args[-1], "diagnose-hardware")
            row = {"schema": "nib.deepseek-hardware-diagnostic/v1", "status": "BLOCKED",
                   "deployable": False, "confidential": False, "functional_pass": False,
                   "stage": "diagnostic", "probe_execution": "timeout", "child": None}
            host.validate_result(row, root, contract, None)
            with self.assertRaises(ValueError):
                host.validate_result({**row, "functional_pass": True}, root, contract, None)

    def test_correctness_payload_is_opt_in_and_source_bound(self):
        files, contract = host.make_payload(self.builder, "1"*40, self.bundle, self.extra,
                                            b"fake-linux-binary", "local-correctness")
        self.assertEqual(contract["operation"], "local-correctness")
        self.assertEqual(files["correctness.py"], self.extra[host.CORRECTNESS])
        self.assertEqual(contract["sources"]["correctness.py"], hashlib.sha256(files["correctness.py"]).hexdigest())
        self.assertNotIn("correctness.py", self.files)
        self.assertEqual(contract["arguments"], self.contract["arguments"])
        self.assertEqual(contract["environment"], self.contract["environment"])
        self.assertFalse(contract["confidential"])
        self.assertFalse(contract["deployable"])

    def test_diagnostic_main_publishes_failed_nvml_observations_with_authoritative_schema(self):
        recorder = self.builder.recorder
        revision = recorder.checker.git(ROOT, "rev-parse", "HEAD").decode().strip()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            env = recorder.offline_environment(root / "control")
            build = root / "build"
            build.mkdir()
            binary, _ = recorder.build_validator(ROOT, revision, build, env)
            files, contract = host.make_payload(self.builder, revision, self.bundle, self.extra,
                                                b"fake-container-binary", "hardware-diagnostic")
            bundle, weights = root / "bundle", root / "empty-weights"
            (bundle / "payload").mkdir(parents=True)
            weights.mkdir()
            (bundle / "payload/probe.py").write_bytes(files["probe.py"])
            receipt = {"git_sha": revision, "source_bundle_sha256": contract["source_bundle_sha256"],
                       "payload_sha256": self.builder.overlay.bundle_digest(files)}
            fixture = DiagnosticTests()
            children = [fixture.child(fixture.diagnose(count=8)),
                        fixture.child(fixture.diagnose(codes={"nvmlDeviceGetCount_v2": 26})),
                        fixture.child(fixture.diagnose()), None]
            children.append({"stage": "bootstrap", "invocation": children[0]["invocation"],
                             "bootstrap": {"stage": "setuptools-source", "source_index": 2}, "probe": None})
            original_publish = recorder.publish_evidence

            def publish(record, report, _container_binary, work, output, _container_env):
                # Only the container boundary is synthetic; run the real native
                # committed-source validator and no-overwrite evidence publisher.
                original_publish(record, report, binary, work, output, env)

            for index, child in enumerate(children):
                output = root / ("published-" + str(index))
                experiment = {"schema": "nib.deepseek-hardware-diagnostic/v1", "status": "BLOCKED",
                              "deployable": False, "confidential": False, "functional_pass": False,
                              "stage": "diagnostic", "probe_execution": "timeout" if child is None else "completed",
                              "child": child}
                host.validate_result(experiment, bundle, contract, recorder)
                report = {"result": "fail", "status": "BLOCKED", "deployable": False, "confidential": False,
                          "functional_pass": False, "stage": "completed", "cleanup": "confirmed", "experiment": experiment}
                deadline = (host.dt.datetime.now(host.dt.timezone.utc) + host.dt.timedelta(seconds=240)).strftime("%Y-%m-%dT%H:%M:%SZ")
                argv = [host.__file__, "--run", str(bundle), "--receipt-sha256", "a" * 64, "--weights", str(weights),
                        "--output", str(output), "--deadline", deadline]
                with self.subTest(index=index), mock.patch.object(host.sys, "argv", argv), \
                        mock.patch.object(host.sys, "platform", "linux"), \
                        mock.patch.object(host, "bundle_inputs", return_value=(receipt, contract, {}, recorder)), \
                        mock.patch.object(host, "run_container", return_value=report), \
                        mock.patch.object(host.signal, "signal"), mock.patch("builtins.print"), \
                        mock.patch.object(recorder, "publish_evidence", side_effect=publish):
                    self.assertEqual(host.main(), 1)
                record = json.loads((output / "run.json").read_bytes())
                self.assertEqual(record["execution"], "software")
                self.assertEqual(record["result"], "fail")
                self.assertFalse(record["hardware_validated"])
                self.assertIsNone(record["host"]["driver"])
                self.assertIsNone(record["host"]["cuda"])
                self.assertEqual(json.loads((output / "output.log").read_bytes())["experiment"], experiment)
            for index, change in enumerate(({"execution": "hardware"}, {"hardware_validated": True})):
                work = root / ("rejected-" + str(index))
                work.mkdir()
                with self.assertRaises(subprocess.CalledProcessError):
                    original_publish({**record, **change}, report, binary, work, work / "published", env)
                self.assertFalse((work / "published").exists())

    def test_authoritative_application_receipt_reused_not_relaxed(self):
        fixtures = self.fixtures
        fixture = fixtures.ApplicationTests()
        with fixture.wheel() as (_, tree, console, application, measured):
            before = fixtures.app.inspect(fixtures.overlay, tree, application, measured, console)
            receipt = fixtures.app.apply(fixtures.overlay, tree, application,
                                         self.builder.checker.verify_stages(self.bundle), before, measured, console)
        writer_hash = fixtures.overlay.digest(fixtures.overlay.json_bytes(before))
        self.assertEqual(receipt["before_inventory_sha256"], writer_hash)
        self.assertNotEqual(writer_hash, measured["packaging"]["inventory_sha256"])
        actual = json.loads(self.files["application/contract.json"])
        measured = self.builder.application.measurement(self.builder.overlay, self.files["application/measurement.json"])
        receipt.update(git_sha=actual["git_sha"], source_bundle_sha256=actual["source_bundle_sha256"],
                       removed_bytecode_files=2879)
        receipt["packaging"].update(removal_plan_sha256=measured["packaging"]["removal_plan_sha256"],
                                    removed_unlisted_bytecode_files=2879)
        row = {"schema": "nib.deepseek-lab-result/v1", "status": "BLOCKED", "deployable": False,
               "confidential": False, "functional_pass": True, "stage": "complete",
               "application": receipt, "hardware": probe.validate(HardwareTests().raw()),
               "completion": {"generated": True, "finish": "stop", "prompt_tokens": 10, "completion_tokens": 1,
                              "final_content_present": True, "synthetic_answer_match": True},
               "engine_stopped": True}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for name, data in self.files.items():
                path = root / "payload" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            path = root / "support" / host.SMOKE
            path.parent.mkdir(parents=True)
            path.write_bytes(self.extra[host.SMOKE])
            host.validate_result(row, root, self.contract, self.builder.recorder)
            for section in ("application", "hardware", "completion"):
                changed = copy.deepcopy(row)
                changed[section]["private"] = "sentinel"
                with self.subTest(section=section), self.assertRaises(ValueError):
                    host.validate_result(changed, root, self.contract, self.builder.recorder)
            for field in ("final_content_present", "synthetic_answer_match"):
                changed = copy.deepcopy(row)
                changed["completion"][field] = False
                with self.subTest(field=field), self.assertRaises(ValueError):
                    host.validate_result(changed, root, self.contract, self.builder.recorder)
            changed = copy.deepcopy(row)
            changed["completion"].update(finish="length", completion_tokens=128)
            with self.assertRaises(ValueError):
                host.validate_result(changed, root, self.contract, self.builder.recorder)
            # Retain a valid token-generation observation, never functional pass.
            changed["functional_pass"] = False
            host.validate_result(changed, root, self.contract, self.builder.recorder)
            correctness = module("scripts/engine_local_correctness.py")
            cases = module("scripts/test_engine_local_correctness.py")
            contract = copy.deepcopy(self.contract)
            contract["operation"] = "local-correctness"
            contract["sources"]["correctness.py"] = hashlib.sha256(self.extra[host.CORRECTNESS]).hexdigest()
            (root / "payload/correctness.py").write_bytes(self.extra[host.CORRECTNESS])
            good = {**row, "schema": "nib.deepseek-lab-correctness-result/v1",
                    "completion": None, "correctness": correctness.run(cases.success, lab.MODEL)}
            host.validate_result(good, root, contract, self.builder.recorder)
            for mutation in ({"correctness": None}, {"completion": row["completion"]},
                             {"engine_stopped": False}, {"schema": row["schema"]}):
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    host.validate_result({**good, **mutation}, root, contract, self.builder.recorder)
            failed = correctness.run(lambda *_: "{}", lab.MODEL)
            with self.assertRaises(ValueError):
                host.validate_result({**good, "correctness": failed}, root, contract, self.builder.recorder)
            host.validate_result({**good, "correctness": failed, "functional_pass": False},
                                 root, contract, self.builder.recorder)
            # An engine startup failure must retain the real application receipt,
            # not disappear behind the outer recorder's validation failure.
            for operation, candidate, input_contract in (
                    ("functional", row, self.contract), ("local-correctness", good, contract)):
                startup_failed = {**candidate, "functional_pass": False, "stage": "startup",
                                  "completion": None, "engine_stopped": True}
                if operation == "local-correctness":
                    startup_failed["correctness"] = None
                with self.subTest(operation=operation):
                    host.validate_result(startup_failed, root, input_contract, self.builder.recorder)
                    self.assertEqual(startup_failed["application"]["before_inventory_sha256"], writer_hash)
                    for malformed in ("", "0" * 63, "private"):
                        bad = copy.deepcopy(startup_failed)
                        bad["application"]["before_inventory_sha256"] = malformed
                        with self.assertRaises(ValueError):
                            host.validate_result(bad, root, input_contract, self.builder.recorder)


class LifecycleTests(unittest.TestCase):
    def test_request_and_shutdown_terminal_states(self):
        @contextmanager
        def selected(*_):
            yield types.SimpleNamespace(deadline=0), (None, types.SimpleNamespace(deadline=0))
        application = {"files": []}
        app = types.SimpleNamespace(verify_layout=lambda *_: None, selected_tree=selected,
                                    inspect=lambda *_: {}, apply=lambda *_: {"stages": 5})
        response = {"object": "chat.completion", "model": lab.MODEL,
                    "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "2"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}}
        for terminal in (0, -signal.SIGTERM, 1, "timeout", "request-error", "dead-worker", "reasoning-only", "wrong-answer", "truncated-answer"):
            engine = mock.Mock(pid=99999999)
            engine.poll.return_value = 1 if terminal == "dead-worker" else None
            engine.wait.side_effect = ([subprocess.TimeoutExpired("fixed", 60), -9] if terminal == "timeout"
                                       else [terminal if type(terminal) is int else 0])
            def http(path, **kwargs):
                if path == "/health": return b"ok"
                if path == "/v1/models": return json.dumps({"data": [{"id": lab.MODEL}]}).encode()
                if terminal == "request-error": raise OSError("private upstream failure")
                body = copy.deepcopy(response)
                if terminal == "reasoning-only":
                    body["choices"][0].update(finish_reason="length", message={"role": "assistant", "content": None,
                                                                            "reasoning": "synthetic-private"})
                elif terminal == "wrong-answer":
                    body["choices"][0]["message"]["content"] = "3"
                elif terminal == "truncated-answer":
                    body["choices"][0]["finish_reason"] = "length"
                    body["usage"].update(completion_tokens=128, total_tokens=138)
                return json.dumps(body).encode()
            with self.subTest(terminal=terminal), mock.patch.object(lab, "hardware", return_value={"fixed": True}), \
                    mock.patch.object(lab, "quiet_command"), mock.patch.object(lab, "http_request", side_effect=http), \
                    mock.patch.object(lab.subprocess, "Popen", return_value=engine), mock.patch.object(lab.os, "killpg"):
                row = lab.application_and_request({"operation": "functional"}, {},
                                                  [None, None, None, app], application, {})
            self.assertEqual(row["functional_pass"], terminal in (0, -signal.SIGTERM))
            self.assertNotIn("private upstream failure", json.dumps(row))
            self.assertNotIn("synthetic-private", json.dumps(row))
            if terminal in ("reasoning-only", "wrong-answer"):
                self.assertTrue(row["completion"]["generated"])
                self.assertFalse(row["completion"]["synthetic_answer_match"])
            if terminal == "truncated-answer":
                self.assertTrue(row["completion"]["synthetic_answer_match"])
                self.assertTrue(row["completion"]["generated"])
                self.assertEqual(row["completion"]["finish"], "length")
            engine.wait.assert_called()

    def test_correctness_runs_inside_existing_lifecycle_and_source_failure_stops_startup(self):
        @contextmanager
        def selected(*_):
            yield types.SimpleNamespace(deadline=0), (None, types.SimpleNamespace(deadline=0))
        app = types.SimpleNamespace(verify_layout=lambda *_: None, selected_tree=selected,
                                    inspect=lambda *_: {}, apply=lambda *_: {"stages": 5})
        correctness = module("scripts/engine_local_correctness.py")
        cases = module("scripts/test_engine_local_correctness.py")
        contract = {"operation": "local-correctness", "paths": ["/selected"]}
        for source_ok in (True, False):
            engine = mock.Mock(pid=99999999)
            engine.poll.return_value = None
            engine.wait.return_value = 0
            def http(path, **kwargs):
                if path == "/health":
                    return b"ok"
                if path == "/v1/models":
                    return json.dumps({"data": [{"id": lab.MODEL}]}).encode()
                raise AssertionError("unexpected endpoint")
            with self.subTest(source_ok=source_ok), \
                    mock.patch.object(lab, "CORRECTNESS", correctness), \
                    mock.patch.object(correctness, "verify_sources", side_effect=None if source_ok else ValueError()), \
                    mock.patch.object(lab, "read_source", create=True), \
                    mock.patch.object(lab, "hardware", return_value={"fixed": True}), \
                    mock.patch.object(lab, "quiet_command"), \
                    mock.patch.object(lab, "http_request", side_effect=http), \
                    mock.patch.object(lab, "correctness_request", side_effect=cases.success) as request, \
                    mock.patch.object(lab.subprocess, "Popen", return_value=engine) as start, \
                    mock.patch.object(lab.os, "killpg"):
                row = lab.application_and_request(contract, {}, [None, None, None, app], {"files": []}, {})
            self.assertEqual(row["functional_pass"], source_ok)
            self.assertEqual(row["engine_stopped"], source_ok)
            self.assertIsNone(row["completion"])
            self.assertEqual(start.call_count, int(source_ok))
            self.assertEqual(request.call_count, 10 if source_ok else 0)
            if source_ok:
                self.assertTrue(row["correctness"]["all_cases_pass"])
                self.assertFalse(row["correctness"]["tau_acceptance"])
            else:
                self.assertIsNone(row["correctness"])

    def test_correctness_request_restores_outer_watchdog_on_success_and_failure(self):
        for failure in (None, TimeoutError("request deadline")):
            with self.subTest(failure=failure), \
                    mock.patch.object(lab.time, "monotonic", side_effect=[100.0, 101.0]), \
                    mock.patch.object(lab.signal, "getsignal", return_value="outer"), \
                    mock.patch.object(lab.signal, "getitimer", return_value=(30.0, 0.0)), \
                    mock.patch.object(lab.signal, "signal") as handler, \
                    mock.patch.object(lab.signal, "setitimer") as timer, \
                    mock.patch.object(lab, "http_request", return_value=b"ok", side_effect=failure):
                if failure is None:
                    self.assertEqual(lab.correctness_request({}, 105.0), b"ok")
                else:
                    with self.assertRaises(TimeoutError):
                        lab.correctness_request({}, 105.0)
            self.assertEqual(timer.call_args_list, [
                mock.call(signal.ITIMER_REAL, 5.0), mock.call(signal.ITIMER_REAL, 0),
                mock.call(signal.ITIMER_REAL, 29.0)])
            self.assertEqual(handler.call_args_list[-1], mock.call(signal.SIGALRM, "outer"))

    def test_owned_cleanup_absence_and_foreign_owner(self):
        recorder = types.SimpleNamespace(call=mock.Mock(side_effect=[b"exists", b"removed", b""]))
        with mock.patch.object(host, "inspect", return_value={"Config": {"Labels": {host.LABEL: "owner"}}}):
            host.cleanup(recorder, "name", "owner", {})
        self.assertIn("rm", recorder.call.call_args_list[1].args[0])
        recorder.call = mock.Mock(return_value=b"exists")
        with mock.patch.object(host, "inspect", return_value={"Config": {"Labels": {host.LABEL: "foreign"}}}):
            with self.assertRaises(ValueError):
                host.cleanup(recorder, "name", "owner", {})
        self.assertEqual(recorder.call.call_count, 1)

    def test_cancel_and_uncertain_create_never_cleanup_success(self):
        recorder = types.SimpleNamespace(image_binding=lambda *_: {"fixed": True},
                                         call=mock.Mock(side_effect=[b"", subprocess.TimeoutExpired("docker", 30)]))
        monitor = mock.Mock(returncode=0)
        monitor.poll.return_value = None
        contract = {"base_image": "fixed"}
        with mock.patch.object(host, "CANCELLED", False), mock.patch.object(host, "CLEANING", False), \
                mock.patch.object(host, "inspect", return_value={"Id": "fixed"}), \
                mock.patch.object(host.subprocess, "Popen", return_value=monitor), \
                mock.patch.object(host, "create_command", return_value=["docker", "create"]), \
                mock.patch.object(host, "cleanup") as cleanup:
            row = host.run_container("/unused", "a"*64, "/unused", time.time()+60,
                                     recorder, contract, {}, host.controlled_env("unix:///var/run/docker.sock"))
        self.assertEqual(row["cleanup"], "create-completion-unknown")
        self.assertFalse(row["functional_pass"])
        cleanup.assert_called_once()
        monitor.wait.assert_called_once()
        with mock.patch.object(host, "CANCELLED", True), mock.patch.object(host, "CLEANING", False), \
                mock.patch.object(host, "inspect", return_value={"Id": "fixed"}), \
                mock.patch.object(host.subprocess, "Popen") as start:
            row = host.run_container("/unused", "a"*64, "/unused", time.time()+60,
                                     recorder, contract, {}, host.controlled_env("unix:///var/run/docker.sock"))
            start.assert_not_called()
        self.assertEqual(row["cleanup"], "not-created")


class ReportTests(unittest.TestCase):
    def test_final_answer_is_distinct_from_reasoning_token_generation(self):
        row = {"object": "chat.completion", "model": lab.MODEL,
               "choices": [{"index": 0, "finish_reason": "length", "message": {"role": "assistant"}}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 128, "total_tokens": 138}}
        for content, present, matches in ((None, False, False), ("", False, False), (" \n", False, False),
                                          ("3", True, False), ("The answer is 2.", True, False),
                                          ("2", True, True), ("\n2\n", True, True)):
            row["choices"][0]["message"] = {"role": "assistant", "content": content,
                                          "reasoning": "private reasoning sentinel"}
            with self.subTest(content=content):
                result = lab.completion_result(json.dumps(row))
                self.assertTrue(result["generated"])
                self.assertEqual((result["final_content_present"], result["synthetic_answer_match"]), (present, matches))
                self.assertNotIn("private reasoning sentinel", json.dumps(result))

    def test_completion_no_content_retained_and_strict_bounds(self):
        row = {"object": "chat.completion", "model": lab.MODEL,
               "choices": [{"index": 0, "finish_reason": "length", "message": {"role": "assistant", "content": "synthetic-private"}}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 128, "total_tokens": 138}}
        safe = lab.completion_result(json.dumps(row))
        self.assertNotIn("synthetic-private", json.dumps(safe))
        for change in ({"error": "private"}, {"usage": {"prompt_tokens": 10, "completion_tokens": 129, "total_tokens": 139}},
                       {"choices": []}, {"model": "private"}):
            with self.assertRaises(ValueError):
                lab.completion_result(json.dumps({**row, **change}))

    def test_failure_report_cannot_smuggle_nested_private_data(self):
        row = {"schema": "nib.deepseek-lab-result/v1", "status": "BLOCKED", "deployable": False,
               "confidential": False, "functional_pass": False, "stage": "hardware",
               "application": None, "hardware": None, "completion": None, "engine_stopped": False}
        host.validate_result(row, Path("/unused"), {}, None)
        for update in ({"completion": {"private": "sentinel"}}, {"private": "sentinel"},
                       {"stage": "sentinel"}, {"functional_pass": True}, {"engine_stopped": 1}):
            with self.subTest(update=update), self.assertRaises((ValueError, KeyError, TypeError)):
                host.validate_result({**row, **update}, Path("/unused"), {}, None)

    def test_unexpected_args_do_not_echo_private_input(self):
        result = subprocess.run([sys.executable, "-I", "-S", str(ROOT / host.SCRIPT), "--private-sentinel"],
                                capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(b"private-sentinel", result.stdout + result.stderr)

    def test_finite_isolated_container_plan_no_host_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "payload").mkdir()
            (root / "payload/lab.json").write_text("{}")
            args = host.create_command("nib-deepseek-" + "a"*32, "b"*32, root, "/weights",
                                       {"base_image": "pinned", "environment": {"VLLM_PLUGINS": ""}, "operation": "functional"},
                                       {"config": {"Env": ["SECRET_IMAGE_DEFAULT=unused"]}})
        for flag, value in (("--network", "none"), ("--pull", "never"), ("--ipc", "private"),
                            ("--shm-size", "2g"), ("--memory", "768g"), ("--pids-limit", "1024")):
            self.assertEqual(args[args.index(flag) + 1], value)
        self.assertNotIn("--publish", args)
        self.assertNotIn("--privileged", args)
        self.assertIn("SECRET_IMAGE_DEFAULT=", args)
        self.assertIn("-S", args)
        self.assertIn("-B", args)
        self.assertEqual(args[-1], "apply")

    def test_verified_nvidia_constraints_survive_creation_and_inspection(self):
        config = {"config": {"Env": ["NVIDIA_REQUIRE_CUDA=cuda>=13.0 driver>=580",
                                    "NVIDIA_REQUIRE_BRAND=brand=tesla", "NVIDIA_DISABLE_REQUIRE=true",
                                    "UNRELATED=private-sentinel"]}}
        contract = {"base_image": "pinned", "environment": {"VLLM_PLUGINS": ""}, "operation": "functional"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "payload").mkdir()
            (root / "payload/lab.json").write_text("{}")
            args = host.create_command("owned", "owner", root, "/weights", contract, config)
            env = [args[i + 1] for i, arg in enumerate(args) if arg == "--env"]
            self.assertIn("NVIDIA_REQUIRE_CUDA=cuda>=13.0 driver>=580", env)
            self.assertIn("NVIDIA_REQUIRE_BRAND=brand=tesla", env)
            self.assertIn("NVIDIA_DISABLE_REQUIRE=", env)
            self.assertIn("UNRELATED=", env)
            application_args = args[args.index("pinned") + 1:]
            self.assertEqual(application_args[0], "-i")
            self.assertNotIn("NVIDIA_REQUIRE_CUDA=cuda>=13.0 driver>=580", application_args)
            info = {
                "Image": "image-id",
                "Config": {"Image": "pinned", "Labels": {host.LABEL: "owner"}, "Entrypoint": ["/usr/bin/env"],
                           "Cmd": application_args, "User": "0:0", "WorkingDir": "/", "Env": env,
                           "Healthcheck": {"Test": ["NONE"]}},
                "HostConfig": {"NetworkMode": "none", "Privileged": False, "Runtime": "nvidia",
                               "ReadonlyRootfs": False, "CapDrop": ["ALL"], "IpcMode": "private",
                               "ShmSize": 2 << 30, "CgroupnsMode": "private", "Memory": 768 << 30,
                               "MemorySwap": 768 << 30, "NanoCpus": 64000000000, "PidsLimit": 1024,
                               "RestartPolicy": {"Name": "no"}, "LogConfig": {"Type": "none"},
                               "SecurityOpt": ["no-new-privileges"],
                               "Tmpfs": {"/cache": "rw,nosuid,nodev,size=16g,mode=0700",
                                         "/tmp": "rw,nosuid,nodev,noexec,size=256m,mode=1777"},
                               "DeviceRequests": [{"DeviceIDs": ["0", "1", "2", "3"], "Capabilities": [["gpu"]],
                                                   "Count": 0, "Driver": "nvidia"}]},
                "Mounts": [{"Type": "bind", "Source": str(root / "payload"), "Destination": "/opt/nib", "RW": False},
                           {"Type": "bind", "Source": "/weights", "Destination": "/model", "RW": False}],
            }
            host.check_container(info, "owned", "owner", root, "/weights", contract, config, "image-id")
            for key in ("NVIDIA_REQUIRE_CUDA", "NVIDIA_REQUIRE_BRAND"):
                original = next(value for value in env if value.startswith(key + "="))
                for replacement in (None, key + "=", key + "=changed"):
                    changed = copy.deepcopy(info)
                    changed["Config"]["Env"].remove(original)
                    if replacement is not None:
                        changed["Config"]["Env"].append(replacement)
                    with self.subTest(key=key, replacement=replacement), self.assertRaises(ValueError):
                        host.check_container(changed, "owned", "owner", root, "/weights", contract, config, "image-id")


def public_source_audit(path):
    raw = Path(path).read_bytes()
    if len(raw) != 22427 or hashlib.sha256(raw).hexdigest() != bootstrap.VECISA_SOURCE_SHA:
        raise ValueError("public VecISA source rejected")
    source = types.SimpleNamespace(require=helpers.require, read_source=lambda *_: raw)
    parsed = ast.parse(raw)
    klass = next(n for n in parsed.body if isinstance(n, ast.ClassDef) and n.name == "VecISA")
    template = next(n.value.value for n in klass.body if isinstance(n, ast.Assign)
                    and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "_avx_py_load")
    path = "/cache/inductor/ab/source-bound.so"
    layout = {"selected_root": "/selected", "paths": ["/selected", "/selected/setuptools/_vendor"]}
    assert bootstrap.vecisa_library(["-c", template.replace("__lib_path__", path)], layout, source) == path
    print("Pinned Torch VecISA source/template: PASS (compile/parse only; no upstream execution).")


def public_setuptools_audit(path):
    for name, sha in bootstrap.SETUPTOOLS_SOURCES.items():
        raw = (Path(path) / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha or len(raw) > 32768:
            raise ValueError("public setuptools source rejected")
        compile(raw, name, "exec", flags=0, dont_inherit=True, optimize=0)
        tree = ast.parse(raw)
        if name == "_distutils_hack/__init__.py":
            functions = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
            assert {"DistutilsMetaFinder", "insert_shim", "_remove_shim", "add_shim", "shim"} <= functions
            shim = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "shim")
            calls = {n.func.id for n in ast.walk(shim) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
            assert calls == {"insert_shim", "_remove_shim"}
    print("Pinned setuptools 80.10.2: four sources PASS (compile/parse only; no upstream execution).")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--public-vecisa":
        public_source_audit(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == "--public-setuptools":
        public_setuptools_audit(sys.argv[2])
    else:
        unittest.main()
