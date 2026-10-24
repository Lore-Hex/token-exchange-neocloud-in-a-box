# SPDX-License-Identifier: Apache-2.0
"""Mock owners and isolated import-only children; no listeners or hardware."""

import ast
from contextlib import ExitStack, contextmanager
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

import deepseek_tuning
import selected_service_process as process
from test_selected_service import Clock, endpoint

service = process.service
ROOT = Path(__file__).resolve().parents[1]


def lab_contract():
    # Read the actual committed literal, without importing the overlay builder's
    # repository tooling or starting its build/preparation path.
    tree = ast.parse((ROOT / "scripts/prepare-engine-overlay.py").read_bytes())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "launch_plan")
    plan = ast.literal_eval(next(node.value for node in function.body if isinstance(node, ast.Return)))
    model = service.models.select("uncensored")
    return {"operation": "local-correctness", "gpu_family": "B300",
            "gpu_security": "nonconfidential", "execution_profile": "tau8k-eager",
            "data_parallel_size": 2, "model_profile": model,
            "pack_revision": model["hf_revision"], "pack_bytes": model["total_bytes"],
            "manifest_sha256": model["manifest_sha256"],
            "arguments": deepseek_tuning.arguments(service.engine_lab.lab_args(plan, 2,
                model=model["served_model_name"]), "tau8k-eager"),
            "environment": service.engine_lab.lab_environment(plan),
            "interpreter": {"resolved": "/usr/bin/python3.12", "bytes": 1,
                            "sha256": process.digest(b"synthetic interpreter")}}


class EngineEntryTests(unittest.TestCase):
    def test_only_canonical_argument_changes_original_unchanged(self):
        lab = lab_contract()
        before = json.dumps(lab, sort_keys=True)
        arguments = process.engine_entry.canonical_arguments(lab)
        changed = [index for index, pair in enumerate(zip(lab["arguments"], arguments)) if pair[0] != pair[1]]
        self.assertEqual(changed, [arguments.index("--served-model-name") + 1])
        self.assertEqual(arguments[changed[0]], service.MODEL)
        self.assertEqual(json.dumps(lab, sort_keys=True), before)

    def test_wrong_profile_role_capacity_or_name_rejected(self):
        mutations = [{"operation": "provider"}, {"gpu_family": "B200"},
                     {"gpu_security": "confidential"}, {"execution_profile": "stream-c8"},
                     {"data_parallel_size": True}, {"tau_owner": {}},
                     {"provider_conformance": {}}, {"stream_cohort": {}}]
        for change in mutations:
            with self.subTest(change=change), self.assertRaises(ValueError):
                process.engine_entry.canonical_arguments({**lab_contract(), **change})
        for flag, value in (("--served-model-name", service.MODEL), ("--host", "0.0.0.0"),
                            ("--max-num-seqs", "8"), ("--max-model-len", "4096")):
            lab = lab_contract()
            lab["arguments"][lab["arguments"].index(flag) + 1] = value
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                process.engine_entry.canonical_arguments(lab)

    def test_composed_launcher_preserves_definitions_and_original_worker_dispatch(self):
        base = process.launcher_source(ROOT, selected=False)
        selected = process.launcher_source(ROOT, selected=True)
        old, new = ast.parse(base), ast.parse(selected)
        self.assertEqual([ast.dump(node) for node in old.body[:-1]],
                         [ast.dump(node) for node in new.body[:len(old.body) - 1]])
        self.assertIn("lab_entry()", ast.unparse(new.body[-1]))
        # Only execute adapter definitions. Engine/vLLM imports are never run.
        lab = lab_contract()
        run, recheck = mock.Mock(), mock.Mock()
        namespace = {"require": lambda value: self.assertTrue(value),
            "sys": SimpleNamespace(argv=["/opt/nib/launch.py", "digest", "selected-service-engine"]),
            "lab_context": mock.Mock(return_value=(dict(lab, paths={}), None, ["module"], "application", None)),
            "recheck_sources": recheck, "run_engine": run}
        exec(compile((ROOT / "scripts/selected_service_engine.py").read_bytes(), "adapter", "exec"), namespace)
        namespace["selected_engine_entry"]()
        recheck.assert_called_once_with("module", "application", {})
        self.assertEqual(run.call_args.args[0]["arguments"], process.engine_entry.canonical_arguments(lab))
        self.assertEqual(lab, lab_contract())

    def test_payload_derivation_does_not_mutate_original_or_fake_its_receipt(self):
        lab = lab_contract()
        payload = {"launch.py": process.launcher_source(ROOT, selected=False),
                   "pack/files.sha256": (ROOT / lab["model_profile"]["pack"] / "files.sha256").read_bytes()}
        lab["sources"] = {name: process.digest(raw) for name, raw in payload.items()}
        payload["lab.json"] = json.dumps(lab).encode()
        payload["python"] = service.engine_lab.wrapper_bytes(lab["interpreter"]["resolved"],
            lab["environment"], process.digest(payload["lab.json"]))
        original = dict(payload)
        result = process.prepare_payload(payload)
        self.assertEqual(payload, original)
        self.assertEqual(set(result), set(payload))
        self.assertEqual({name for name in result if result[name] != payload[name]},
                         {"launch.py", "lab.json", "python"})
        derived = json.loads(result["lab.json"])
        self.assertEqual(derived["sources"]["launch.py"], process.digest(result["launch.py"]))
        self.assertIn(process.digest(result["lab.json"]).encode(), result["python"])
        self.assertEqual(derived["arguments"], lab["arguments"])
        with self.assertRaises(service.Rejected):
            process.prepare_payload({**payload, "launch.py": payload["launch.py"] + b"\n"})
        with self.assertRaises(service.Rejected):
            process.prepare_payload({**payload, "python": payload["python"] + b"\n"})


class PackagingTests(unittest.TestCase):
    @contextmanager
    def prepared_fixture(self):
        # Synthetic binaries and metadata test the binding machinery only.
        # This is not an image-provenance or executable-compatibility fixture.
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory).resolve() / "service"
            payload = root.parent / "payload"
            (root / "scripts/e2e").mkdir(parents=True)
            (root / "bin").mkdir()
            (payload / "pack").mkdir(parents=True)
            for name in ("engine_python_bootstrap.py", "engine_exception.py", "engine_lab_bootstrap.py",
                         "engine_lab.py", "selected_service_engine.py"):
                shutil.copyfile(ROOT / "scripts" / name, root / "scripts" / name)
            for name in ("trcs", "nib-pilot-api", "python"):
                (root / "bin" / name).write_bytes(b"synthetic " + name.encode())
            (root / "scripts/e2e/pins.json").write_text(json.dumps({"trcs": {
                "binary_sha256": process.digest((root / "bin/trcs").read_bytes())}}))
            lab = lab_contract()
            interpreter = root / "bin/python"
            lab["interpreter"] = {"resolved": str(interpreter), "bytes": interpreter.stat().st_size,
                                  "sha256": process.digest(interpreter.read_bytes())}
            files = {"launch.py": process.launcher_source(ROOT, selected=False),
                "pack/files.sha256": (ROOT / lab["model_profile"]["pack"] / "files.sha256").read_bytes()}
            lab["sources"] = {name: process.digest(raw) for name, raw in files.items()}
            files["lab.json"] = json.dumps(lab).encode()
            files["python"] = service.engine_lab.wrapper_bytes(str(interpreter), lab["environment"],
                                                               process.digest(files["lab.json"]))
            files = process.prepare_payload(files)
            for name, raw in files.items():
                (payload / name).write_bytes(raw)
            receipt = process.preparation_receipt(root, process.digest(files["lab.json"]))
            raw = json.dumps(receipt).encode()
            (root / "prepared.json").write_bytes(raw)
            stack.enter_context(mock.patch.object(process, "ROOT", root))
            stack.enter_context(mock.patch.object(process, "PAYLOAD", payload))
            stack.enter_context(mock.patch.object(process.sys, "version_info", (3, 12, 3)))
            stack.enter_context(mock.patch.object(process.sys, "executable", str(interpreter)))
            stack.enter_context(mock.patch.object(process.Prepared, "mount_state", return_value=(1,)))
            yield root, payload, process.digest(raw)

    def test_prepared_sources_payload_wrapper_and_interpreter_are_bound(self):
        for tamper in (None, "source", "extra-source", "payload", "wrapper", "interpreter", "receipt"):
            with self.subTest(tamper=tamper), self.prepared_fixture() as (root, payload, sha):
                paths = {"source": root / "scripts/engine_lab.py", "extra-source": root / "scripts/extra.py",
                    "payload": payload / "launch.py", "wrapper": payload / "python",
                    "interpreter": root / "bin/python", "receipt": root / "prepared.json"}
                if tamper is not None:
                    paths[tamper].write_bytes(b"tampered public fixture")
                    with self.assertRaises((ValueError, service.Rejected)):
                        process.Prepared(sha, process.time.monotonic() + 10)
                else:
                    prepared = process.Prepared(sha, process.time.monotonic() + 10)
                    self.assertEqual(prepared.lab_sha, process.digest((payload / "lab.json").read_bytes()))
                    self.assertEqual(prepared.interpreter, str(root / "bin/python"))

    def test_real_alarm_bounds_slow_body_and_restores_signal_state(self):
        # Separate child protects the suite's signal state. HTTPResponse itself
        # reads a trickling byte stream; no socket or listening port is created.
        code = r'''
import io, signal, sys, time
from unittest import mock
sys.path.insert(0, sys.argv[1])
import selected_service_process as process
class Trickle(io.RawIOBase):
    def __init__(self):
        self.header = b'HTTP/1.1 200 OK\r\nContent-Length: 1048576\r\n\r\n'
        self.chunks = 0
    def readable(self):
        return True
    def readinto(self, target):
        if self.header:
            data, self.header = self.header[:len(target)], self.header[len(target):]
            target[:len(data)] = data
            return len(data)
        time.sleep(.005)
        self.chunks += 1
        target[0] = 120
        return 1
body = Trickle()
sock = mock.MagicMock()
sock.__enter__.return_value = sock
sock.makefile.return_value = io.BufferedReader(body)
owner = process.ProcessOwner.__new__(process.ProcessOwner)
owner.hostname = 'service.invalid'
handler, timer = signal.getsignal(signal.SIGALRM), signal.getitimer(signal.ITIMER_REAL)
assert timer == (0.0, 0.0)
with mock.patch.object(owner, 'bound', return_value=object()), \
     mock.patch.object(owner, 'peer'), \
     mock.patch.object(process.socket, 'create_connection', return_value=sock):
    assert owner.probe('engine', object(), time.monotonic() + .1) is None
assert body.closed and body.chunks < 1048576
assert signal.getsignal(signal.SIGALRM) == handler
assert signal.getitimer(signal.ITIMER_REAL) == timer
sock.__exit__.assert_called_once()
print('bounded-response-ok')
'''
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(ROOT / "scripts")],
            capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"bounded-response-ok\n")

    def test_prepared_image_isolated_imports_without_repository_data(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory).resolve()
            root = image / "opt/nib-service"
            for source in (ROOT / "scripts").rglob("*.py"):
                target = root / source.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            shutil.copyfile(ROOT / "scripts/e2e/pins.json", root / "scripts/e2e/pins.json")
            (root / "bin").mkdir()
            for name in ("trcs", "nib-pilot-api"):
                (root / "bin" / name).write_bytes(b"not executable import fixtures")
            (image / "opt/nib").mkdir()
            code = """
import sys
from unittest import mock
sys.path.insert(0, sys.argv[1])
with mock.patch('subprocess.Popen', side_effect=AssertionError('unexpected process')), \\
     mock.patch('socket.socket', side_effect=AssertionError('unexpected socket')):
    import selected_service_process as owner
    assert owner.service.MODEL == owner.engine_entry.SERVICE_MODEL
print('isolated-import-ok')
"""
            result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(root / "scripts")],
                cwd=image, env={"PATH": os.defpath}, capture_output=True, timeout=20, check=False)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(result.stdout, b"isolated-import-ok\n")
            self.assertEqual(set(path.name for path in root.iterdir()), {"scripts", "bin"})
            help_result = subprocess.run([sys.executable, "-I", "-S", "-B",
                str(root / "scripts/selected_service_process.py"), "--help"],
                cwd=image, env={"PATH": os.defpath}, capture_output=True, timeout=20, check=False)
            self.assertEqual(help_result.returncode, 0, help_result.stderr.decode())
            self.assertIn(b"--prepared-sha256", help_result.stdout)
            self.assertNotIn(b"--upstream", help_result.stdout)
            self.assertFalse(list(image.rglob("*.pyc")))

    def test_source_walk_is_bounded_and_rejects_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "scripts").mkdir()
            (root / "scripts/a.py").write_bytes(b"pass\n")
            with mock.patch.object(process.time, "monotonic", return_value=1):
                self.assertEqual(process.source_files(root, 2),
                    {"scripts/a.py", "scripts/e2e/pins.json", "bin/trcs", "bin/nib-pilot-api"})
                with self.assertRaises(service.Rejected):
                    process.source_files(root, 1)
                for name in ("unbound.pyc", "unbound.so", "extra.json"):
                    extra = root / "scripts" / name
                    extra.write_bytes(b"unbound import material")
                    with self.subTest(name=name), self.assertRaises(service.Rejected):
                        process.source_files(root, 2)
                    extra.unlink()
                (root / "scripts/link.py").symlink_to(root / "scripts/a.py")
                with self.assertRaises(service.Rejected):
                    process.source_files(root, 2)

    def test_wrong_supervisor_python_rejected_before_inputs(self):
        with mock.patch.object(process.sys, "version_info", (3, 12, 13)), \
                mock.patch.object(process.bootstrap, "read_source") as read:
            with self.assertRaises(service.Rejected):
                process.Prepared(process.digest(b"fixture"), 100)
            read.assert_not_called()

    def test_mounts_require_nonroot_pid_one_and_readonly(self):
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(process.sys, "platform", "linux"))
            stack.enter_context(mock.patch.object(process.os, "getpid", return_value=1))
            uid = stack.enter_context(mock.patch.object(process.os, "getuid", return_value=65532))
            stack.enter_context(mock.patch.object(Path, "stat", return_value=SimpleNamespace(st_dev=1, st_ino=2)))
            scratch = stack.enter_context(mock.patch.object(service.engine_lab, "check_scratch_mounts"))
            vfs = stack.enter_context(mock.patch.object(process.os, "statvfs",
                return_value=SimpleNamespace(f_flag=process.os.ST_RDONLY)))
            self.assertEqual(len(process.Prepared.mount_state()), 4)
            scratch.assert_called_once()
            uid.return_value = 0
            with self.assertRaises(service.Rejected):
                process.Prepared.mount_state()
            uid.return_value = 65532
            vfs.return_value.f_flag = 0
            with self.assertRaises(service.Rejected):
                process.Prepared.mount_state()
            vfs.return_value.f_flag = process.os.ST_RDONLY
            scratch.side_effect = ValueError("scratch mount rejected")
            with self.assertRaises(ValueError):
                process.Prepared.mount_state()

    def test_changed_prepared_file_or_cgroup_fails(self):
        prepared = process.Prepared.__new__(process.Prepared)
        prepared.mounts, prepared.stamps = (1,), {Path("/fixture"): (2,)}
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(prepared, "mount_state", return_value=(1,)))
            stamp = stack.enter_context(mock.patch.object(process, "stamp", return_value=(2,)))
            kernel = stack.enter_context(mock.patch.object(service.observer, "read_kernel",
                side_effect=[str(service.MEMORY_BYTES).encode() + b"\n", b"0\n"]))
            stack.enter_context(mock.patch.object(process.time, "monotonic", return_value=1))
            self.assertTrue(prepared.check(10))
            kernel.side_effect = [b"max\n"]
            with self.assertRaises(service.Rejected):
                prepared.check(10)
            stamp.return_value = (3,)
            with self.assertRaises(service.Rejected):
                prepared.check(10)


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = Clock()
        self.stack.enter_context(mock.patch.object(process.time, "monotonic", self.clock.monotonic))
        self.stack.enter_context(mock.patch.object(process.time, "time", self.clock.time))
        self.stack.enter_context(mock.patch.object(process.time, "sleep", self.clock.advance))
        self.stack.enter_context(mock.patch.object(service.pilot, "tls_client_context", return_value=mock.Mock()))
        self.stack.enter_context(mock.patch.object(service.pilot.pilot, "controlled_env", return_value={"controlled": "yes"}))
        self.inputs = service.Inputs(Path("/private/fixture"), {})
        self.prepared = SimpleNamespace(interpreter="/usr/bin/python3.12", lab_sha=process.digest(b"lab"),
            lab=lab_contract(), files={}, check=mock.Mock(return_value=True))
        self.shutdown = threading.Event()
        self.owner = process.ProcessOwner(self.prepared, self.inputs, "service.invalid", self.shutdown)
        self.contract = service.Contract.begin(self.clock.timestamp(7200), input_price="1", output_price="2")

    def child(self, pid=9000, code=None):
        child = mock.Mock(pid=pid, returncode=code)
        child.poll.return_value = code
        child.wait.return_value = 0
        return child

    def test_fixed_launches_and_early_registration(self):
        validator, engine, guard, tls = [self.child(9000 + value) for value in range(4)]
        registered = []
        with mock.patch.object(process.subprocess, "Popen", side_effect=[validator, engine, guard, tls]) as popen, \
                mock.patch.object(self.owner, "reap", return_value=True) as reap:
            for role in service.ROLES:
                self.owner.start(role, self.contract, self.inputs, registered.append, 1900)
        self.assertEqual(registered, [engine, guard, tls])
        self.assertEqual(self.owner.processes["weights"], validator)
        self.assertEqual(popen.call_args_list[0].args[0],
            ["/opt/nib/nib", "model", "check-local", "--workers", "8", "/opt/nib/pack", "/model"])
        self.assertEqual(popen.call_args_list[1].args[0], ["/usr/bin/python3.12", "-I", "-S", "-B",
            "/opt/nib/launch.py", self.prepared.lab_sha, "selected-service-engine"])
        self.assertEqual(popen.call_args_list[1].kwargs["env"], self.prepared.lab["environment"])
        args = popen.call_args_list[2].args[0]
        self.assertEqual(args[args.index("--profile") + 1], "selected-deepseek")
        args = popen.call_args_list[3].args[0]
        self.assertEqual(args[args.index("--max-inflight") + 1], "1")
        for call in popen.call_args_list:
            self.assertTrue(call.kwargs["close_fds"])
            self.assertTrue(call.kwargs["start_new_session"])
            for name in ("stdin", "stdout", "stderr"):
                self.assertEqual(call.kwargs[name], subprocess.DEVNULL)
        reap.assert_called_once_with("weights", validator, 125)

    def test_weight_failure_cancel_and_expiry_never_start_engine(self):
        for mode in ("failure", "cancel", "expiry", "unreaped"):
            with self.subTest(mode=mode):
                self.shutdown.clear()
                validator = self.child()
                validator.wait.return_value = 1 if mode == "failure" else 0
                if mode == "cancel":
                    self.shutdown.set()
                with mock.patch.object(process.subprocess, "Popen", return_value=validator) as popen, \
                        mock.patch.object(self.owner, "reap", return_value=mode != "unreaped") as reap:
                    with self.assertRaises(service.Rejected):
                        self.owner.start("engine", self.contract, self.inputs, mock.Mock(),
                                         120 if mode == "expiry" else 1900)
                    self.assertEqual(popen.call_count, 0 if mode == "cancel" else 1)
                    if mode == "cancel":
                        reap.assert_not_called()
                    else:
                        reap.assert_called_once()

    def test_registration_interrupt_retains_owned_process(self):
        child = self.child()
        with mock.patch.object(process.subprocess, "Popen", return_value=child):
            with self.assertRaises(KeyboardInterrupt):
                self.owner.start("guard", self.contract, self.inputs,
                    mock.Mock(side_effect=KeyboardInterrupt), 1900)
        self.assertIs(self.owner.processes["guard"], child)

    @contextmanager
    def http_probe(self, *, role="engine", body=b'{"data":[]}', bound_after=True):
        bound = endpoint(9000)
        socket = mock.MagicMock()
        socket.__enter__.return_value = socket
        response = mock.MagicMock(status=200)
        response.__enter__.return_value = response
        response.read.return_value = body
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(self.owner, "bound", side_effect=[bound, bound if bound_after else None]))
            peer = stack.enter_context(mock.patch.object(self.owner, "peer"))
            stack.enter_context(mock.patch.object(process.socket, "create_connection", return_value=socket))
            stack.enter_context(mock.patch.object(process.http.client, "HTTPResponse", return_value=response))
            def send(_):
                peer.assert_called_once()
            socket.sendall.side_effect = send
            yield socket, response

    def test_probe_peer_precedes_http_and_post_listener_loss_is_not_ready(self):
        for present in (True, False):
            with self.subTest(present=present), self.http_probe(bound_after=present):
                result = self.owner._probe("engine", self.child(), 110)
                self.assertEqual(isinstance(result, service.EngineReady), present)
                if not present:
                    self.assertIsNone(result)

    def test_tls_peer_requires_the_owned_accepted_socket(self):
        sock = mock.Mock()
        sock.getpeername.return_value = ("127.0.0.1", process.TLS_PORT)
        sock.getsockname.return_value = ("127.0.0.1", 40000)
        row = ("0100007F:" + format(process.TLS_PORT, "04X"), "0100007F:9C40", "01", 7)
        with mock.patch.object(service.identity, "tcp_rows", return_value=[row]), \
                mock.patch.object(service.identity, "sockets", side_effect=[set(), {7}]):
            self.owner.peer("tls", {"pid": 9000}, sock, 101)
            self.assertEqual(self.clock.mono, 100.005)
        with mock.patch.object(service.identity, "tcp_rows", return_value=[row]), \
                mock.patch.object(service.identity, "sockets", return_value=set()):
            with self.assertRaises(service.Rejected):
                self.owner.peer("tls", {"pid": 9000}, sock, 100.02)
        sock.getpeername.return_value = ("127.0.0.2", process.TLS_PORT)
        with self.assertRaises(service.Rejected):
            self.owner.peer("tls", {"pid": 9000}, sock, 101)

    def test_engine_binding_cannot_refresh_a_changed_process_identity(self):
        child = self.child()
        self.owner.commands["engine"] = ["/usr/bin/python3.12", "-I", "-S", "-B"]
        row = ("0100007F:1F40", "00000000:0000", "0A", 3)
        original = endpoint(child.pid)
        changed = endpoint(child.pid)
        changed.expected["start_ticks"] += 1
        with mock.patch.object(service.identity, "tcp_rows", return_value=[row]), \
                mock.patch.object(service, "owned_endpoint", side_effect=[original, changed]) as bound:
            self.assertIs(self.owner.bound("engine", child, 110), original)
            self.assertEqual(bound.call_args.kwargs["arguments_sha256"],
                process.digest(b"/usr/bin/python3.12\0-I\0-S\0-B\0"))
            with self.assertRaises(service.Rejected):
                self.owner.bound("engine", child, 110)

    def test_slow_trickle_whole_probe_deadline(self):
        deadlines = []
        @contextmanager
        def alarm(deadline):
            deadlines.append(deadline)
            yield
        def trickle(_):
            # Each chunk fits the two-second socket timeout, but the whole
            # response cannot outlive the enclosing absolute timer.
            while self.clock.mono < deadlines[-1]:
                self.clock.advance(.25)
            raise service.identity.worker.Abort("timeout")
        with self.http_probe() as (socket, response), \
                mock.patch.object(service.identity.worker, "alarm", alarm):
            response.read.side_effect = trickle
            self.assertIsNone(self.owner.probe("engine", self.child(), 101))
            self.assertEqual(deadlines, [101])
            self.assertEqual(self.clock.mono, 101)
            socket.__exit__.assert_called_once()
            response.__exit__.assert_called_once()

    def test_probe_alarm_clips_long_deadline_and_preserves_other_errors(self):
        @contextmanager
        def alarm(deadline):
            self.assertEqual(deadline, 115)
            yield
        with mock.patch.object(service.identity.worker, "alarm", alarm), \
                mock.patch.object(self.owner, "_probe", side_effect=service.identity.worker.Abort("protocol_error")):
            with self.assertRaises(service.identity.worker.Abort):
                self.owner.probe("guard", self.child(), 1000)

    def test_oversized_and_unready_response(self):
        with self.http_probe(body=b"x" * ((1 << 20) + 1)):
            with self.assertRaises(service.Rejected):
                self.owner._probe("engine", self.child(), 110)
        with self.http_probe() as (_, response):
            response.status = 503
            self.assertIsNone(self.owner._probe("engine", self.child(), 110))

    def test_drain_clips_wait_and_nonzero_never_graceful(self):
        child = self.child()
        self.assertTrue(self.owner.drain("tls", child, 105))
        child.wait.assert_called_once_with(timeout=5)
        child.wait.return_value = 1
        self.assertFalse(self.owner.drain("tls", child, 105))
        child.poll.return_value = 0
        self.assertFalse(self.owner.drain("tls", child, 105))

    def test_signal_denied_race_requires_reap_and_absence(self):
        child = self.child()
        with mock.patch.object(process.os, "killpg",
                side_effect=[PermissionError, ProcessLookupError, ProcessLookupError]) as kill, \
                mock.patch.object(process.snapshot, "reap_group_children"):
            self.assertTrue(self.owner.reap("engine", child, 105))
            self.assertEqual(child.wait.call_count, 2)
            self.assertEqual(kill.call_args_list[-1].args, (child.pid, 0))
        child.wait.reset_mock()
        with mock.patch.object(process.os, "killpg", side_effect=PermissionError), \
                mock.patch.object(process.snapshot, "reap_group_children"):
            self.assertFalse(self.owner.reap("engine", child, 105))
            self.assertEqual(child.wait.call_count, 2)

    def test_reap_wait_timeout_remains_failure_even_signal_succeeded(self):
        child = self.child()
        child.wait.side_effect = subprocess.TimeoutExpired("fixture", 1)
        with mock.patch.object(process.os, "killpg"):
            self.assertFalse(self.owner.reap("engine", child, 101))

    def test_partial_spawn_namespace_cleanup_is_closed_and_denial_fails(self):
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(process.sys, "platform", "linux"))
            pid = stack.enter_context(mock.patch.object(process.os, "getpid", return_value=1))
            stack.enter_context(mock.patch.object(process.os, "waitpid", side_effect=ChildProcessError))
            paths = stack.enter_context(mock.patch.object(Path, "iterdir",
                side_effect=[[Path("/proc/1"), Path("/proc/23")], [Path("/proc/1")]]))
            kill = stack.enter_context(mock.patch.object(process.os, "kill"))
            self.assertTrue(self.owner.reap_unregistered(105))
            kill.assert_called_once_with(23, signal.SIGKILL)
            paths.side_effect = [[Path("/proc/23")]]
            kill.side_effect = PermissionError
            self.assertFalse(self.owner.reap_unregistered(105))
            pid.return_value = 12
            with self.assertRaises(service.Rejected):
                self.owner.reap_unregistered(105)

    def test_closed_cli_rejects_upstream_and_preparation_has_no_child(self):
        args = ["--prepared-sha256", process.digest(b"fixture"), "--provider-config", "/provider",
                "--api-key-file", "/api-key", "--cert", "/cert", "--key", "/key",
                "--hostname", "service.invalid", "--input-price", "1", "--output-price", "2",
                "--hard-stop-at", self.clock.timestamp(7200)]
        with mock.patch.object(process.subprocess, "Popen") as popen, \
                mock.patch.object(process.sys, "stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                process.main([*args, "--upstream", "http://service.invalid"])
            popen.assert_not_called()
        output = io.StringIO()
        with mock.patch.object(process, "Prepared", side_effect=ValueError("PRIVATE_CANARY")), \
                mock.patch.object(process.subprocess, "Popen") as popen, \
                mock.patch.object(process.signal, "signal"), mock.patch.object(process.sys, "stdout", output):
            self.assertEqual(process.main(args), 1)
            popen.assert_not_called()
        self.assertNotIn("PRIVATE_CANARY", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["status"], "failed")


if __name__ == "__main__":
    unittest.main()
