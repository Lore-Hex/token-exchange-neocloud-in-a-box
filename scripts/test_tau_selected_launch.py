# SPDX-License-Identifier: Apache-2.0
"""Original-byte joins and real child pipes; no inference or hardware claims."""

import copy
from contextlib import ExitStack, contextmanager
import datetime
from itertools import product
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import types
from unittest import mock

import test_tau_trial as trial
import test_tau_cpu_session as cpu
import test_tau_cpu_owner as cpu_owner
import test_tau_gpu_owner as gpu

launch = trial.module("record-tau-selected.py")
tau = launch.tau


def inputs():
    c, g = cpu.packet(), gpu.packet()
    c["expires_unix"] = g["expires_unix"] = int(time.time()) + 120
    ready = {"support": cpu.session.probes.encoded(c), "candidate": gpu.gpu.observer.encoded(g)}
    toolkit = {"git_sha": cpu.PRODUCER["git_sha"], "files": {
        name: cpu.PRODUCER["source_sha256"] for name in (*launch.cpu_rec.SOURCES, *launch.cpu_rec.OWNER_SOURCES)}}
    toolkit["files"][launch.gpu_rec.PROBE] = tau.digest((launch.ROOT / launch.gpu_rec.PROBE).read_bytes())
    with mock.patch.object(cpu.session.identity, "source_receipt"), \
            mock.patch.object(launch.runner, "selected_policy", return_value=gpu.gpu.policy):
        frozen, expiry, held, image = launch.admit(ready, toolkit, cpu.session, gpu.gpu, gpu.contract(),
                                                   gpu.SHA, time.monotonic() + 300)
    return ready, toolkit, frozen, expiry, held, image


class SelectedLaunchTests(unittest.TestCase):
    def test_launch_joins_cleanup_without_promoting_failed_trials(self):
        for failure in (None, "second-spawn", "readiness", "scorer", "join", "post-scorer-interrupt", "scorer-fail"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                root = Path(temporary).resolve()
                ready, toolkit, frozen, expiry, held, image = inputs()
                args = types.SimpleNamespace(gpu_bundle=root / "gpu-bundle", gpu_receipt_sha256="c" * 64,
                    weights=root / "weights", source_archive=root / "upstream", wheelhouse=root / "wheels",
                    cpu_inputs=root / "cpu-inputs", output=root / "output",
                    deadline=datetime.datetime.fromtimestamp(time.time() + 2400, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
                processes, observed = [], {}
                def spawn(*args, **kwargs):
                    self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
                    self.assertEqual(kwargs["env"], launch.environment())
                    self.assertTrue(kwargs["close_fds"] and kwargs["start_new_session"])
                    if failure == "second-spawn" and processes: raise OSError("PRIVATE_CANARY")
                    process = mock.Mock()
                    process.stdout.fileno.return_value = 7 + 2 * len(processes)
                    process.stdin.fileno.return_value = 8 + 2 * len(processes)
                    processes.append(process)
                    return process
                def cleanup(owned, deadline, *, abort):
                    self.assertEqual(abort, failure not in (None, "join", "scorer-fail"))
                    observed.update(owned)
                    return {name: 1 if name == "candidate" else 0 for name in owned}
                def prepare(archive_path, wheelhouse, manifest_path, manifest_sha, selection, deadline,
                            *, parent_pipe, selected_operational):
                    self.assertEqual((archive_path, wheelhouse), (args.source_archive, args.wheelhouse))
                    self.assertEqual((manifest_path, manifest_sha, selection), (None, None, None))
                    self.assertTrue(parent_pipe and selected_operational)
                    return {}, {"producer": {"git_sha": toolkit["git_sha"]}}
                @contextmanager
                def alarm(deadline):
                    yield
                    if failure == "post-scorer-interrupt": raise KeyboardInterrupt
                mocked = ((launch.sys, "platform", "linux"),
                    (launch.os, "uname", mock.Mock(return_value=types.SimpleNamespace(machine="x86_64"))),
                    (launch, "identity", mock.Mock(return_value=toolkit)),
                    (launch.gpu_rec, "bundle_inputs", mock.Mock(return_value=({"git_sha": toolkit["git_sha"]}, {}, None, None))),
                    (launch.gpu_rec, "validate_runtime_payload", mock.Mock()),
                    (launch.gpu_rec, "owner_module", mock.Mock(return_value=gpu.gpu)),
                    (launch.gpu_rec, "read", mock.Mock(return_value=b"fixed")),
                    (launch.cpu_rec, "toolkit", mock.Mock()),
                    (launch, "preflight_images", mock.Mock()),
                    (launch.runner, "prepare", prepare),
                    (cpu.session.llama.worker, "alarm", alarm),
                    (launch.runner, "publish", mock.Mock()),
                    (launch.subprocess, "Popen", mock.Mock(side_effect=spawn)),
                    (launch, "read_ready", mock.Mock(side_effect=ValueError("PRIVATE_CANARY") if failure == "readiness" else None,
                                                     return_value=ready)),
                    (launch, "admit", mock.Mock(return_value=(frozen, expiry, held, image))),
                    (launch.runner, "run", mock.Mock(side_effect=ValueError("PRIVATE_CANARY") if failure == "scorer" else None,
                                                     return_value={"result": "fail" if failure == "scorer-fail" else "pass"})),
                    (launch, "close_and_reap", mock.Mock(side_effect=cleanup)),
                    (launch, "final_join", mock.Mock(side_effect=ValueError("PRIVATE_CANARY") if failure == "join" else None,
                                                      return_value={"owner_reports": {"cpu": {}, "gpu": {}},
                                                          "result": "fail" if failure == "scorer-fail" else "pass",
                                                          "host": {"driver": "580.178.04", "cuda": "13.0"}})))
                for obj, name, value in mocked:
                    stack.enter_context(mock.patch.object(obj, name, value))
                self.assertEqual(launch.run(args), 0 if failure is None else 1)
                self.assertEqual(len(observed), 1 if failure == "second-spawn" else 2)
                report = tau.json_value((args.output / "evidence/output.log").read_bytes())
                self.assertEqual(report["result"], "pass" if failure is None else "fail")
                self.assertEqual(report["cleanup"], "confirmed" if failure in (None, "post-scorer-interrupt", "scorer-fail") else "unconfirmed")
                envelope = tau.json_value((args.output / "evidence/run.json").read_bytes())
                self.assertEqual(envelope["result"], report["result"])
                self.assertEqual(envelope["host"]["driver"], "580.178.04" if report["cleanup"] == "confirmed" else None)
                if failure in ("second-spawn", "readiness"):
                    self.assertEqual(report["ready_sha256"], {})
                    self.assertIsNone(report["scorer"])
                    self.assertIsNone(report["owner_reports"])
                self.assertNotIn("CANARY", repr(report))
                self.assertFalse(report["confidential"] or report["quality_eligible"] or report["live_admission"])

    def test_image_preflight_reuses_validators_and_each_consumers_daemon(self):
        calls = []
        bases = tau.json_value((launch.ROOT / "scripts/e2e/pins.json").read_bytes())
        def inspect(argv, **kwargs):
            calls.append((argv, {**kwargs, "env": dict(kwargs["env"])}))
            raw = tau.encoded([trial.image_inspection()]) if len(calls) == 1 else b"sha256:" + b"a" * 64 + b" linux/amd64\n"
            return subprocess.CompletedProcess(argv, 0, raw, b"")
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(launch.process_tools, "execute_process_group", side_effect=inspect))
            context = stack.enter_context(mock.patch.object(launch.process_tools, "local_docker_endpoint",
                                                            return_value="unix:///private/cpu-fixture.sock"))
            buildkit = stack.enter_context(mock.patch.object(launch.process_tools, "require_buildkit"))
            scorer_check = stack.enter_context(mock.patch.object(launch.runner, "check_image", wraps=launch.runner.check_image))
            cpu_check = stack.enter_context(mock.patch.object(launch.cpu_rec, "check_base_image", wraps=launch.cpu_rec.check_base_image))
            stack.enter_context(mock.patch.dict(os.environ, {"DOCKER_HOST": "tcp://untrusted.invalid:2375",
                                                            "DOCKER_CONFIG": "/private/untrusted"}))
            launch.preflight_images(time.monotonic() + 5)
        scorer_check.assert_called_once()
        self.assertEqual(cpu_check.call_count, 2)
        context.assert_called_once()
        self.assertNotIn("DOCKER_HOST", context.call_args.args[0])
        self.assertEqual(context.call_args.args[1], Path(launch.pwd.getpwuid(os.getuid()).pw_dir))
        buildkit.assert_called_once_with(calls[1][1]["env"])
        self.assertEqual(calls[0][0], ["docker", "image", "inspect", "--platform", "linux/amd64", launch.runner.runtime.TARGET["image"]])
        self.assertEqual(calls[0][1]["env"]["DOCKER_HOST"], "unix:///var/run/docker.sock")
        for index, key in enumerate(("go_image", "python_image"), 1):
            self.assertEqual(calls[index][0], ["docker", "image", "inspect", "--format",
                                              "{{.Id}} {{.Os}}/{{.Architecture}}", bases[key]])
            self.assertEqual(calls[index][1]["env"]["DOCKER_HOST"], "unix:///private/cpu-fixture.sock")
        for index, (_, kwargs) in enumerate(calls):
            self.assertGreater(kwargs["timeout"], 0)
            self.assertLessEqual(kwargs["timeout"], 5)
            self.assertEqual(kwargs["max_output_bytes"], 65536 if index == 0 else 1024)
            self.assertEqual(kwargs["max_stderr_bytes"], 1024)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertNotEqual(kwargs["env"]["DOCKER_CONFIG"], "/private/untrusted")
            self.assertFalse(Path(kwargs["env"]["DOCKER_CONFIG"]).exists())

    def test_incompatible_or_missing_images_never_start_either_owner(self):
        for index in range(3):
            for failure in ("missing", "incompatible", "malformed", "stderr", "timeout"):
                with self.subTest(image=index, failure=failure), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                    root = Path(temporary).resolve()
                    args = types.SimpleNamespace(gpu_bundle=root / "bundle", gpu_receipt_sha256="c" * 64,
                        output=root / "output", source_archive=root / "source", wheelhouse=root / "wheels",
                        deadline=datetime.datetime.fromtimestamp(time.time() + 2400, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
                    calls = []
                    def inspect(argv, **kwargs):
                        position = len(calls)
                        calls.append(argv)
                        raw = tau.encoded([trial.image_inspection()]) if position == 0 else b"sha256:" + b"a" * 64 + b" linux/amd64\n"
                        code, stderr = 0, b""
                        if position == index:
                            if failure == "missing": code = 1
                            if failure == "stderr": stderr = b"PRIVATE_CANARY"
                            if failure == "timeout": raise subprocess.TimeoutExpired(argv, 15)
                            if failure == "malformed": raw = b"not-an-image"
                            if failure == "incompatible":
                                raw = (tau.encoded([{**trial.image_inspection(), "Descriptor": None,
                                                    "Id": launch.runner.PROBE_CONFIG}]) if index == 0 else raw.replace(b"amd64", b"arm64"))
                        return subprocess.CompletedProcess(argv, code, raw, stderr)
                    mocked = ((launch.sys, "platform", "linux"),
                        (launch.os, "uname", mock.Mock(return_value=types.SimpleNamespace(machine="x86_64"))),
                        (launch, "identity", mock.Mock(return_value={"git_sha": "a" * 40})),
                        (launch.gpu_rec, "bundle_inputs", mock.Mock(return_value=({"git_sha": "a" * 40}, {}, None, None))),
                        (launch.gpu_rec, "validate_runtime_payload", mock.Mock()),
                        (launch.gpu_rec, "owner_module", mock.Mock(return_value=gpu.gpu)),
                        (launch.gpu_rec, "read", mock.Mock(return_value=b"fixed")),
                        (launch.cpu_rec, "toolkit", mock.Mock()),
                        (launch.process_tools, "execute_process_group", mock.Mock(side_effect=inspect)),
                        (launch.process_tools, "require_buildkit", mock.Mock()),
                        (launch.process_tools, "local_docker_endpoint", mock.Mock(return_value="unix:///private/cpu-fixture.sock")))
                    for obj, name, value in mocked:
                        stack.enter_context(mock.patch.object(obj, name, value))
                    owners = stack.enter_context(mock.patch.object(launch.subprocess, "Popen"))
                    prepare = stack.enter_context(mock.patch.object(launch.runner, "prepare"))
                    with self.assertRaises((ValueError, subprocess.TimeoutExpired)):
                        launch.run(args)
                    self.assertEqual(len(calls), index + 1)
                    owners.assert_not_called()
                    prepare.assert_not_called()
                    self.assertFalse(args.output.exists())

    def test_buildx_prerequisite_uses_real_helper_and_blocks_owner_startup(self):
        for failure in (None, "missing-docker", "missing-buildx", "timeout"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                root = Path(temporary).resolve()
                args = types.SimpleNamespace(gpu_bundle=root / "bundle", gpu_receipt_sha256="c" * 64,
                    output=root / "output", source_archive=root / "source", wheelhouse=root / "wheels",
                    deadline=datetime.datetime.fromtimestamp(time.time() + 2400, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
                calls = []
                def inspect(argv, **kwargs):
                    calls.append(argv)
                    raw = tau.encoded([trial.image_inspection()]) if len(calls) == 1 else b"sha256:" + b"a" * 64 + b" linux/amd64\n"
                    return subprocess.CompletedProcess(argv, 0, raw, b"")
                command = ["docker", "buildx", "version"]
                error = (subprocess.CalledProcessError(1, command, stderr=b"unknown command") if failure == "missing-buildx"
                         else subprocess.TimeoutExpired(command, 10) if failure == "timeout" else None)
                version = mock.Mock(side_effect=error, return_value=subprocess.CompletedProcess(command, 0, b"fixture", b""))
                stop = RuntimeError("stop before preparation")
                prepare = mock.Mock(side_effect=stop)
                mocked = ((launch.sys, "platform", "linux"),
                    (launch.os, "uname", mock.Mock(return_value=types.SimpleNamespace(machine="x86_64"))),
                    (launch, "identity", mock.Mock(return_value={"git_sha": "a" * 40})),
                    (launch.gpu_rec, "bundle_inputs", mock.Mock(return_value=({"git_sha": "a" * 40}, {}, None, None))),
                    (launch.gpu_rec, "validate_runtime_payload", mock.Mock()),
                    (launch.gpu_rec, "owner_module", mock.Mock(return_value=gpu.gpu)),
                    (launch.gpu_rec, "read", mock.Mock(return_value=b"fixed")),
                    (launch.cpu_rec, "toolkit", mock.Mock()),
                    (launch.process_tools, "execute_process_group", mock.Mock(side_effect=inspect)),
                    (launch.process_tools, "local_docker_endpoint", mock.Mock(return_value="unix:///private/cpu-fixture.sock")),
                    (launch.process_tools.shutil, "which", mock.Mock(return_value=None if failure == "missing-docker" else str(root / "docker"))),
                    (launch.process_tools.subprocess, "run", version),
                    (launch.runner, "prepare", prepare))
                for obj, name, value in mocked:
                    stack.enter_context(mock.patch.object(obj, name, value))
                helper = stack.enter_context(mock.patch.object(launch.process_tools, "require_buildkit",
                                                               wraps=launch.process_tools.require_buildkit))
                owners = stack.enter_context(mock.patch.object(launch.subprocess, "Popen"))
                expected = ValueError if failure == "missing-docker" else type(error) if error is not None else RuntimeError
                with self.assertRaises(expected) as caught:
                    launch.run(args)
                helper.assert_called_once()
                self.assertEqual(helper.call_args.args[0]["DOCKER_HOST"], "unix:///private/cpu-fixture.sock")
                self.assertFalse(Path(helper.call_args.args[0]["DOCKER_CONFIG"]).exists())
                if failure == "missing-docker":
                    version.assert_not_called()
                else:
                    version.assert_called_once_with(command, env=helper.call_args.args[0],
                                                    check=True, capture_output=True, timeout=10)
                if failure is not None:
                    prepare.assert_not_called()
                    self.assertEqual(len(calls), 1)
                    if error is not None: self.assertIs(caught.exception, error)
                else:
                    prepare.assert_called_once()
                    self.assertIs(caught.exception, stop)
                    self.assertEqual(len(calls), 3)
                owners.assert_not_called()
                self.assertFalse(args.output.exists())

    def test_late_buildx_success_cannot_continue_preflight(self):
        result = subprocess.CompletedProcess([], 0, tau.encoded([trial.image_inspection()]), b"")
        with ExitStack() as stack:
            clock = stack.enter_context(mock.patch.object(launch.time, "monotonic", return_value=10))
            inspect = stack.enter_context(mock.patch.object(launch.process_tools, "execute_process_group", return_value=result))
            stack.enter_context(mock.patch.object(launch.process_tools, "local_docker_endpoint",
                                                  return_value="unix:///private/cpu-fixture.sock"))
            buildkit = stack.enter_context(mock.patch.object(launch.process_tools, "require_buildkit",
                side_effect=lambda env: setattr(clock, "return_value", 16)))
            with self.assertRaises(ValueError):
                launch.preflight_images(15)
        buildkit.assert_called_once()
        inspect.assert_called_once()

    def test_payload_ownership_preflight_precedes_images_preparation_and_owners(self):
        for bad_owner in ("", "launch.py", None):
            with self.subTest(bad_owner=bad_owner), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                root = Path(temporary).resolve()
                bundle = root / "bundle"
                payload = bundle / "payload"
                payload.mkdir(parents=True, mode=0o700)
                for name in ("launch.py", "python", "nib"):
                    (payload / name).write_bytes(b"fixture")
                    (payload / name).chmod(0o600 if name == "launch.py" else 0o500)
                args = types.SimpleNamespace(gpu_bundle=bundle, gpu_receipt_sha256="c" * 64, output=root / "output",
                    deadline=datetime.datetime.fromtimestamp(time.time() + 2400, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
                real_stat = Path.lstat
                def observed(path, *args, **kwargs):
                    row = real_stat(path, *args, **kwargs)
                    uid = 501 if bad_owner is not None and path == payload / bad_owner else 0
                    return types.SimpleNamespace(st_uid=uid, st_mode=row.st_mode, st_nlink=row.st_nlink)
                stop = RuntimeError("stop before Docker")
                images = mock.Mock(side_effect=stop)
                mocked = ((launch.sys, "platform", "linux"),
                    (launch.os, "uname", mock.Mock(return_value=types.SimpleNamespace(machine="x86_64"))),
                    (launch, "identity", mock.Mock(return_value={"git_sha": "a" * 40})),
                    (launch.gpu_rec, "bundle_inputs", mock.Mock(return_value=({"git_sha": "a" * 40}, {}, None, None))),
                    (launch.gpu_rec, "owner_module", mock.Mock(return_value=gpu.gpu)),
                    (launch.gpu_rec, "read", mock.Mock(return_value=b"fixed")),
                    (launch.cpu_rec, "toolkit", mock.Mock()),
                    (launch, "preflight_images", images), (Path, "lstat", observed))
                for obj, name, value in mocked:
                    stack.enter_context(mock.patch.object(obj, name, value))
                validator = stack.enter_context(mock.patch.object(launch.gpu_rec, "validate_runtime_payload",
                                                                  wraps=launch.gpu_rec.validate_runtime_payload))
                owners = stack.enter_context(mock.patch.object(launch.subprocess, "Popen"))
                prepare = stack.enter_context(mock.patch.object(launch.runner, "prepare"))
                with self.assertRaises(ValueError if bad_owner is not None else RuntimeError) as caught:
                    launch.run(args)
                validator.assert_called_once_with(bundle)
                if bad_owner is not None:
                    images.assert_not_called()
                else:
                    self.assertIs(caught.exception, stop)
                    images.assert_called_once()
                owners.assert_not_called()
                prepare.assert_not_called()
                self.assertFalse(args.output.exists())

    def test_image_preflight_expired_budget_never_inspects_or_discovers_context(self):
        with mock.patch.object(launch.process_tools, "execute_process_group") as inspect, \
                mock.patch.object(launch.process_tools, "local_docker_endpoint") as context, self.assertRaises(ValueError):
            launch.preflight_images(time.monotonic() - 1)
        inspect.assert_not_called()
        context.assert_not_called()

    def test_image_preflight_rejects_late_success_before_context_discovery(self):
        result = subprocess.CompletedProcess([], 0, tau.encoded([trial.image_inspection()]), b"")
        with mock.patch.object(launch.process_tools, "execute_process_group", return_value=result) as inspect, \
                mock.patch.object(launch.process_tools, "local_docker_endpoint") as context, \
                mock.patch.object(launch.time, "monotonic", side_effect=(10, 16)), self.assertRaises(ValueError):
            launch.preflight_images(15)
        self.assertEqual(inspect.call_args.kwargs["timeout"], 5)
        context.assert_not_called()

    def test_original_ready_packets_bind_manifest_and_never_renew_expiry(self):
        ready, toolkit, frozen, expiry, held, image = inputs()
        self.assertEqual(image, cpu.IMAGE)
        self.assertEqual(set(frozen["framing"]), {"candidate", "simulator", "evaluator"})
        for name, raw in ready.items():
            self.assertEqual(frozen["owners"][name]["ready_sha256"], tau.digest(raw))
            self.assertLessEqual(expiry[name] - time.monotonic(), 120)
        self.assertEqual(held["ready_sha256"], tau.digest(ready["support"]))
        mutations = (lambda r: r.update(model="unreviewed"), lambda r: r.update(expires_unix=int(time.time()) - 1),
                     lambda r: r.update(contract_sha256="a" * 64))
        for change in mutations:
            bad = gpu.gpu.worker.decode(ready["candidate"])
            change(bad)
            with self.assertRaises((ValueError, gpu.gpu.worker.Abort)):
                launch.admit({**ready, "candidate": gpu.gpu.observer.encoded(bad)}, toolkit,
                    cpu.session, gpu.gpu, gpu.contract(), gpu.SHA, time.monotonic() + 300)

    def test_private_ready_read_and_drain_reap_real_owned_children(self):
        processes = {}
        try:
            for name, prefix in (("candidate", b"GPU "), ("support", b"CPU ")):
                script = "import sys;sys.stdout.buffer.write(%r);sys.stdout.flush();sys.stdin.buffer.read();print('fixed final')" % (prefix + b'{}\n')
                processes[name] = subprocess.Popen([sys.executable, "-I", "-S", "-c", script],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, start_new_session=True)
            observed = launch.read_ready(processes, {"candidate": b"GPU ", "support": b"CPU "}, time.monotonic() + 3)
            self.assertEqual(observed, dict.fromkeys(processes, b"{}"))
            self.assertEqual(launch.close_and_reap(processes, time.monotonic() + 3), dict.fromkeys(processes, 0))
            self.assertTrue(all(p.poll() == 0 and p.stdin.closed and p.stdout.closed for p in processes.values()))
        finally:
            for process in processes.values():
                if process.poll() is None: process.kill()
                process.wait(timeout=3)

    def test_malformed_readiness_and_hanging_shutdown_are_bounded(self):
        for value in (b"wrong\n", b"CPU {}\nextra", b"CPU " + b"x" * 33000):
            process = subprocess.Popen([sys.executable, "-I", "-S", "-c",
                "import sys,time;sys.stdout.buffer.write(%r);sys.stdout.flush();time.sleep(30)" % value],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, start_new_session=True)
            with self.assertRaises(ValueError):
                launch.read_ready({"support": process}, {"support": b"CPU "}, time.monotonic() + .2)
            with self.assertRaises(ValueError):
                launch.close_and_reap({"support": process}, time.monotonic() + .1)
            self.assertIsNotNone(process.poll())

    def test_abort_interrupts_startup_without_waiting_for_overall_cutoff(self):
        process = subprocess.Popen([sys.executable, "-I", "-S", "-c",
            "import signal,sys,time\ndef stop(*args): sys.exit(7)\nsignal.signal(signal.SIGINT,stop)\nprint('ready',flush=True)\ntime.sleep(120)"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, start_new_session=True)
        try:
            self.assertEqual(process.stdout.readline(), b"ready\n")
            started = time.monotonic()
            self.assertEqual(launch.close_and_reap({"support": process}, started + 3600, abort=True), {"support": 7})
            self.assertLess(time.monotonic() - started, 5)
            self.assertTrue(process.stdout.closed and process.stdin.closed)
        finally:
            if process.poll() is None: process.kill()
            process.wait(timeout=3)

    def test_abort_before_ready_uses_gpu_outer_cancel_and_preserves_cleanup_report(self):
        # Actual outer cancellation/report logic with a blocked fake attach and
        # an owned-container Docker fixture, never a real daemon or GPU.
        script = textwrap.dedent("""
            import json, signal, sys, time, types
            from pathlib import Path
            from unittest import mock
            path, output, ignore_int, cleanup_fails = sys.argv[1:]
            host = types.ModuleType('gpu_outer_fixture')
            host.__file__ = path
            exec(compile(Path(path).read_bytes(), path, 'exec'), host.__dict__)
            identifier, owner = 'a' * 64, 'b' * 32
            info = {'Id': identifier, 'Config': {'Labels': {host.LABEL: owner}},
                    'State': {'Status': 'created', 'ExitCode': 0, 'OOMKilled': False}}
            signals, calls = [], []
            def cancel(number, frame):
                signals.append(number)
                host.cancel(number, frame)
            signal.signal(signal.SIGTERM, cancel)
            signal.signal(signal.SIGINT, signal.SIG_IGN if ignore_int == 'True' else cancel)
            def capture(*args):
                print('fixture-startup', flush=True)
                time.sleep(120)  # No READY and no stdin reader during startup.
                raise AssertionError('startup was not cancelled')
            owner_module = types.SimpleNamespace(worker=types.SimpleNamespace(Abort=type('Abort', (BaseException,), {})),
                                                capture=capture)
            replies = iter((b'', identifier.encode(), identifier.encode(), b'',
                            identifier.encode() if cleanup_fails == 'True' else b''))
            def call(argv, *args, **kwargs):
                calls.append(argv)
                return next(replies)
            recorder = types.SimpleNamespace(image_binding=lambda *_: {}, call=call, snapshot=None)
            monitor = mock.Mock(returncode=0)
            monitor.poll.return_value = None
            from contextlib import ExitStack
            with ExitStack() as stack:
                for name, value in (
                    ('owner_module', lambda *_: owner_module),
                    ('inspect', lambda *_: info), ('check_container', lambda *_: None),
                    ('create_command', lambda *_: ['docker', 'create']), ('read', lambda *_: b'contract')):
                    stack.enter_context(mock.patch.object(host, name, value))
                stack.enter_context(mock.patch.object(host.uuid, 'uuid4', return_value=types.SimpleNamespace(hex=owner)))
                stack.enter_context(mock.patch.object(host.subprocess, 'Popen', return_value=monitor))
                report = host.run_container('/unused', 'a' * 64, '/unused', time.time() + 120,
                                            recorder, {'base_image': 'fixed'}, {}, {'DOCKER_HOST': 'unix:///unused'})
            assert monitor.wait.call_count == 1
            Path(output).write_text(json.dumps({'report': report, 'signals': signals, 'calls': calls}))
            sys.stdout.write('x' * 60000)  # Final output must be drained, not retained.
            sys.stdout.flush()
            sys.exit(1)  # Original GPU qualification remains FAIL/BLOCKED.
        """)
        for ignore_int, cleanup_fails in ((False, False), (True, False), (False, True)):
            with self.subTest(ignore_int=ignore_int, cleanup_fails=cleanup_fails), tempfile.TemporaryDirectory() as temporary:
                result = Path(temporary) / "gpu-report.json"
                processes = {}
                unrelated = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", "import time;time.sleep(120)"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                try:
                    processes["candidate"] = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", script,
                        str(launch.ROOT / "host/record-deepseek-lab.py"), str(result), str(ignore_int), str(cleanup_fails)],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, start_new_session=True)
                    process = processes["candidate"]
                    with launch.selectors.DefaultSelector() as selector:
                        selector.register(process.stdout, launch.selectors.EVENT_READ)
                        self.assertTrue(selector.select(5), "GPU fixture did not reach startup")
                    self.assertEqual(process.stdout.readline(), b"fixture-startup\n")
                    processes["support"] = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", "raise SystemExit(1)"],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, start_new_session=True)
                    processes["support"].wait(timeout=3)
                    with self.assertRaises(ValueError):
                        launch.read_ready(processes, {"candidate": gpu.gpu.READY, "support": cpu.session.READY}, time.monotonic() + 5)
                    with mock.patch.object(process, "send_signal", wraps=process.send_signal) as cancel, \
                            mock.patch.object(processes["support"], "send_signal", wraps=processes["support"].send_signal) as exited:
                        self.assertEqual(launch.close_and_reap(processes, time.monotonic() + 5, abort=True),
                                         {"candidate": 1, "support": 1})
                    cancel.assert_called_once_with(signal.SIGTERM)
                    exited.assert_not_called()
                    row = tau.json_value(result.read_bytes())
                    self.assertEqual(row["signals"], [signal.SIGTERM])
                    self.assertEqual(row["report"]["cleanup"], "unconfirmed" if cleanup_fails else "confirmed")
                    self.assertEqual(row["report"]["experiment_step"], "attach")
                    self.assertEqual(row["report"]["failure"], "bounded-lab-stage-failed")
                    self.assertEqual(row["report"]["status"], "BLOCKED")
                    self.assertEqual(row["report"]["result"], "fail")
                    self.assertFalse(row["report"]["functional_pass"] or row["report"]["confidential"] or row["report"]["deployable"])
                    name = "nib-deepseek-" + "b" * 32
                    self.assertEqual(row["calls"][-2:], [
                        ["docker", "container", "rm", "--force", name],
                        ["docker", "container", "ls", "--all", "--quiet", "--filter", "name=^/" + name + "$"]])
                    self.assertTrue(all(p.stdin.closed and p.stdout.closed for p in processes.values()))
                    self.assertIsNone(unrelated.poll())
                finally:
                    for process in (*processes.values(), unrelated):
                        if process.poll() is None: process.kill()
                        process.wait(timeout=3)
                        for pipe in (process.stdin, process.stdout):
                            if pipe is not None: pipe.close()

    def test_abort_grace_keeps_original_deadline_and_reaps_both_on_timeout(self):
        for cutoff, expiry in ((110, 110), (1000, 280)):
            with self.subTest(cutoff=cutoff), ExitStack() as stack:
                processes = {name: mock.Mock(returncode=None) for name in ("candidate", "support")}
                for process in processes.values():
                    process.poll.return_value = None
                    process.stdin.closed = False
                clock = stack.enter_context(mock.patch.object(launch.time, "monotonic", side_effect=(100, expiry)))
                selector = stack.enter_context(mock.patch.object(launch.selectors, "DefaultSelector"))
                selector.return_value.__enter__.return_value.get_map.return_value = {1: "still-open"}
                reap = stack.enter_context(mock.patch.object(launch.process_tools, "cleanup_process_group"))
                with self.assertRaises(ValueError):
                    launch.close_and_reap(processes, cutoff, abort=True)
                self.assertEqual(clock.call_count, 2)
                processes["candidate"].send_signal.assert_called_once_with(signal.SIGTERM)
                processes["support"].send_signal.assert_called_once_with(signal.SIGINT)
                self.assertEqual(reap.call_args_list, [mock.call(p) for p in processes.values()])
                for process in processes.values():
                    process.stdin.close.assert_called()

    def test_final_join_requires_both_original_reports_and_preserves_gpu_blocked(self):
        ready, toolkit, frozen, expiry, held, image = inputs()
        finish = {"candidate": {"status": "evaluated", "completed_requests": 1},
                  "support": {"status": "evaluated", "completed_requests": 2}}
        runtime = {**cpu.result(held), "schema": "nib.tau-cpu-session/v4", "owner": finish["support"]}
        c = {"schema": "nib.tau-cpu-owner-run/v1", "git_sha": toolkit["git_sha"],
            "image_id": "sha256:" + image, "sources": {name: toolkit["files"][name]
                for name in (*launch.cpu_rec.SOURCES, *launch.cpu_rec.OWNER_SOURCES)}, "result": "pass",
            "cleanup": dict.fromkeys(("container", "image", "build_completion"), "confirmed"), "runtime": runtime}
        packet = gpu.gpu.worker.decode(ready["candidate"])
        experiment = {"tau_owner": {"ready_sha256": tau.digest(ready["candidate"]), **finish["candidate"]},
            "tokenizer_parity": packet["parity"], "runtime_observation": {"samples": [packet["sample"]]},
            "engine_stopped": True, "functional_pass": True,
            "hardware": {"status": "observed", "selected_gpus": 8, "family": "B300", "cc_feature": "disabled",
                "environment": "unavailable", "devtools": "off", "multigpu": "none", "system_ready": None,
                "driver": "580.178.04", "cuda_driver": "13.0", "total_kib": [281640960] * 8,
                "attestation_verified": False}}
        g = {"provenance": {"git_sha": toolkit["git_sha"], "receipt_sha256": "c" * 64},
            "cleanup": "confirmed", "functional_pass": True, "experiment": experiment,
            "status": "BLOCKED", "deployable": False, "confidential": False}
        scorer = {"result": "pass", "cleanup": "confirmed", "diagnostic": {"owner_finish": finish}}
        changes = (None, lambda c, g, s: c["cleanup"].update(image="unknown"),
                   lambda c, g, s: c["runtime"].update(ready_sha256="f" * 64),
                   lambda c, g, s: c["runtime"]["owner"].update(completed_requests=3),
                   lambda c, g, s: g["provenance"].update(receipt_sha256="f" * 64),
                   lambda c, g, s: g["experiment"].update(engine_stopped=False),
                   lambda c, g, s: g.update(cleanup="unconfirmed"),
                   lambda c, g, s: s.update(result="fail" if s["result"] == "pass" else "pass"),
                   lambda c, g, s: g["experiment"]["hardware"].update(driver="PRIVATE_CANARY"),
                   lambda c, g, s: g["experiment"]["hardware"].update(driver="595.91.07"),
                   lambda c, g, s: g["experiment"]["hardware"].update(cuda_driver=None),
                   lambda c, g, s: g["experiment"]["hardware"].update(attestation_verified=True),
                   lambda c, g, s: g["experiment"]["hardware"].update(selected_gpus=4))
        for failed, change in product((False, True), changes):
            with self.subTest(failed=failed, change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                dc, dg, ds = copy.deepcopy(c), copy.deepcopy(g), copy.deepcopy(scorer)
                if failed:
                    dc["result"] = dc["runtime"]["result"] = ds["result"] = "fail"
                    dc["runtime"]["owner"]["status"] = "protocol_error"
                    dg["functional_pass"] = dg["experiment"]["functional_pass"] = False
                    dg["experiment"]["tau_owner"]["status"] = "protocol_error"
                    for owner in ds["diagnostic"]["owner_finish"].values():
                        owner["status"] = "protocol_error"
                if change: change(dc, dg, ds)
                for name, report in (("cpu", dc), ("gpu", dg)):
                    directory = root / name
                    directory.mkdir()
                    documents = {"run.json": {"git_sha": toolkit["git_sha"],
                        "result": dc["result"] if name == "cpu" else "fail", "execution": "hardware",
                        "hardware_validated": False, "host": {"gpus": "eight-selected-B300", "driver": "580.178.04", "cuda": "13.0"}},
                                 "output.log": report}
                    if name == "cpu": documents.update({key: cpu.session.llama.worker.decode(value.encode())
                        for key, value in cpu.session.llama.worker.decode(ready["support"])["receipts"].items()})
                    for file, row in documents.items():
                        (directory / file).write_bytes(tau.encoded(row))
                args = (root, ready, held, image, toolkit, gpu.gpu, gpu.contract(), "c" * 64, ds,
                        {"candidate": 1, "support": 1 if failed else 0}, time.monotonic() + 3)
                if change:
                    with self.assertRaises((ValueError, gpu.gpu.worker.Abort)): launch.final_join(*args)
                else:
                    joined = launch.final_join(*args)
                    self.assertEqual(set(joined["owner_reports"]), {"cpu", "gpu"})
                    self.assertEqual(joined["result"], "fail" if failed else "pass")
                    self.assertEqual(joined["host"], {"gpus": "eight-selected-B300", "driver": "580.178.04", "cuda": "13.0"})


if __name__ == "__main__":
    unittest.main()
