# SPDX-License-Identifier: Apache-2.0
"""Synthetic session controls only; no model, Docker or live-admission proof."""
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

import tau_cpu_session as session
import test_tau_cpu_parity as fixtures
import test_tau_cpu_admission as admission

cpu, tau, rec = session.cpu, session.tau, fixtures.rec
PRODUCER = {"git_sha": "a" * 40, "script": "scripts/record-tau-cpu-parity.py", "source_sha256": "b" * 64}
IMAGE = "c" * 64


def packet():
    _, documents = admission.documents()
    for name in session.FILES:
        row = documents[name]
        row["producer"] = PRODUCER
        if name.endswith("-runtime.json"):
            row["serving"]["image_sha256"] = IMAGE
            row["measurements"]["image_sha256"] = {"expected_sha256": IMAGE, "observed_sha256": IMAGE}
        else:
            row["runtime_receipt_sha256"] = tau.digest(tau.encoded(documents[name.replace("-tokenizer", "-runtime")]))
    return {"schema": "nib.tau-cpu-pair-ready/v1", "producer": PRODUCER, "image_sha256": IMAGE,
            "expires_unix": int(time.time()) + 60,
            "receipts": {name: tau.encoded(documents[name]).decode() for name in session.FILES}}


def result(held=None):
    return {"schema": "nib.tau-cpu-session/v1", "result": "pass" if held else "fail",
            "stage": "released" if held else "inputs", "receipts": held["receipts"] if held else {},
            "ready_sha256": held["ready_sha256"] if held else None, "acknowledged": bool(held),
            "cleanup": "confirmed", "quality_eligible": False, "confidential": False, "live_admission": False}


@contextlib.contextmanager
def pipe():
    read, write = os.pipe()
    try:
        yield read, write
    finally:
        os.close(read)
        os.close(write)


class CPUHeldSessionTests(unittest.TestCase):
    def test_fresh_originals_round_trip_through_admission_without_reencoding(self):
        value = packet()
        raw = session.probes.encoded(value)
        with mock.patch.object(session.identity, "source_receipt") as source:
            originals, held = session.accept(raw, PRODUCER, IMAGE, time.monotonic() + 5)
        self.assertEqual(source.call_count, 4)
        self.assertEqual(held["ready_sha256"], tau.digest(raw))
        with tempfile.TemporaryDirectory() as temp:
            fd = os.open(temp, os.O_DIRECTORY)
            try:
                for name in session.FILES:
                    session.write_exact(fd, name, originals[name])
                    path = Path(temp) / name
                    self.assertEqual(path.read_bytes(), value["receipts"][name].encode())
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(held["receipts"][name], tau.digest(path.read_bytes()))
                    with self.assertRaises(FileExistsError):
                        session.write_exact(fd, name, b"changed")
                with self.assertRaises(ValueError):
                    session.write_exact(fd, "../other", b"bad")
            finally:
                os.close(fd)
        for private in ("pid", "port", "model", "tokens_sha256", "process"):
            self.assertNotIn(private, session.probes.encoded(held).decode())

    def test_receipt_producer_builds_original_bindings_from_fresh_measurements(self):
        value = packet()
        original = json.loads(value["receipts"]["simulator-runtime.json"])
        endpoint = mock.Mock(port=original["endpoint"]["port"], expected=original["process"])
        rows = [{"id": name, "result": "pass", "local": session.probes.tokens([index + 1]),
                 "server": session.probes.tokens([index + 1])} for index, name in enumerate(session.probes.IDS)]
        docs = session.receipts("simulator", cpu.MODEL_IDS[0], endpoint, rows, PRODUCER, IMAGE, "d" * 64,
                                time.monotonic() + 5)
        runtime, tokenizer = (json.loads(docs["simulator-" + kind + ".json"]) for kind in ("runtime", "tokenizer"))
        self.assertEqual(tokenizer["runtime_receipt_sha256"], tau.digest(docs["simulator-runtime.json"]))
        self.assertEqual(runtime["process"], endpoint.expected)
        self.assertEqual(runtime["serving"]["helper_sha256"], "d" * 64)
        endpoint.check.assert_called_once()
        self.assertEqual(set(tokenizer["cases"]), set(session.probes.IDS))
        rows[-1]["result"] = "fail"
        with self.assertRaises(ValueError):
            session.receipts("simulator", cpu.MODEL_IDS[0], endpoint, rows, PRODUCER, IMAGE, "d" * 64,
                             time.monotonic() + 5)

    def test_receipt_mutations_rejected_with_no_original_publication(self):
        changes = (
            lambda p: p.update(producer={**PRODUCER, "git_sha": "f" * 40}),
            lambda p: p.update(image_sha256="f" * 64),
            lambda p: p.update(expires_unix=int(time.time()) - 1),
            lambda p: p.update(expires_unix=int(time.time()) + 3601),
            lambda p: p["receipts"].pop("evaluator-tokenizer.json"),
            lambda p: p.update(content=fixtures.fixtures.CANARY),
        )
        for change in changes:
            value = packet()
            change(value)
            with self.subTest(change=change), mock.patch.object(session.identity, "source_receipt"), \
                    self.assertRaises((ValueError, session.llama.worker.Abort)):
                session.accept(session.probes.encoded(value), PRODUCER, IMAGE, time.monotonic() + 5)
        for field in ("pid", "listener_inode"):
            value = packet()
            left, right = (json.loads(value["receipts"][role + "-runtime.json"]) for role in session.ROLES)
            right["process"][field] = left["process"][field]
            value["receipts"]["evaluator-runtime.json"] = tau.encoded(right).decode()
            token = json.loads(value["receipts"]["evaluator-tokenizer.json"])
            token["runtime_receipt_sha256"] = tau.digest(tau.encoded(right))
            value["receipts"]["evaluator-tokenizer.json"] = tau.encoded(token).decode()
            with self.subTest(field=field), mock.patch.object(session.identity, "source_receipt"), \
                    self.assertRaises(ValueError):
                session.accept(session.probes.encoded(value), PRODUCER, IMAGE, time.monotonic() + 5)

    def test_hold_requires_live_children_owned_endpoints_and_exact_release(self):
        raw = b'{"synthetic":true}'
        good = session.DONE + session.probes.encoded({"ready_sha256": tau.digest(raw), "released": True}) + b"\n"
        for case in ("good", "wrong-hash", "false", "extra", "oversize", "exit", "identity", "timeout", "eof"):
            with self.subTest(case=case), pipe() as (rd, wr), pipe() as (out_rd, out_wr):
                process = mock.Mock()
                process.poll.return_value = 1 if case == "exit" else None
                endpoint = mock.Mock()
                if case == "identity":
                    endpoint.check.side_effect = ValueError("private content")
                ack = good
                if case == "wrong-hash":
                    ack = good.replace(tau.digest(raw).encode(), b"f" * 64)
                elif case == "false":
                    ack = good.replace(b"true", b"false")
                elif case == "extra":
                    ack += b"other\n"
                elif case == "oversize":
                    ack = b"a" * 513
                if case not in ("timeout", "eof"):
                    os.write(wr, ack)
                deadline = time.monotonic() + (0.02 if case == "timeout" else 2)
                with mock.patch.object(session.os, "read", return_value=b"") if case == "eof" else contextlib.nullcontext():
                    if case == "eof":
                        os.write(wr, b"wake")
                    if case == "good":
                        session.hold(raw, [endpoint], [process], deadline, rd, out_wr)
                        self.assertGreaterEqual(endpoint.check.call_count, 2)
                    else:
                        with self.assertRaises(ValueError):
                            session.hold(raw, [endpoint], [process], deadline, rd, out_wr)
                self.assertEqual(os.read(out_rd, 4096), session.READY + raw + b"\n")

    def test_private_report_cannot_promote_partial_or_unconfirmed_session(self):
        held = {"ready_sha256": "a" * 64, "receipts": dict.fromkeys(session.FILES, "b" * 64)}
        self.assertEqual(session.validate(result(held))["result"], "pass")
        for key, value in (("acknowledged", False), ("cleanup", "unknown"), ("ready_sha256", None),
                           ("stage", "hold"), ("live_admission", True), ("confidential", True),
                           ("quality_eligible", True), ("receipts", {})):
            row = result(held)
            row[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                session.validate(row)
        with self.assertRaises(session.llama.worker.Abort):
            session.validate({**result(), "exception": "private content"})

    def test_two_owned_children_are_stopped_even_when_second_stage_or_cleanup_fails(self):
        for failure in (None, "second-parity", "hold", "first-stop"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
                work = Path(temp)
                pins = cpu.settings()
                pilot = mock.Mock()
                pilot.controlled_env.return_value = {}
                pilot.file_digest.side_effect = lambda p: (pins["engine"]["archive_sha256"] if p == "archive" else
                    pins["models"][cpu.MODEL_IDS[p.index]]["sha256"] if isinstance(p, Weight) else "d" * 64)
                pilot.free_port.side_effect = [24001, 24002]
                pilot.engine_command.side_effect = lambda *args: ["server", "--api-key-file", "unused"]
                pilot.stop.side_effect = [False, True] if failure == "first-stop" else None
                pilot.stop.return_value = True
                archive = mock.Mock()
                archive.stat.return_value.st_size = 12
                previous = pilot.file_digest.side_effect
                pilot.file_digest.side_effect = lambda p: pins["engine"]["archive_sha256"] if p is archive else previous(p)
                processes = [mock.Mock(pid=301), mock.Mock(pid=302)]
                endpoints = [mock.Mock(port=24001, expected={"listener_inode": 401}),
                             mock.Mock(port=24002, expected={"listener_inode": 402})]
                for process in processes:
                    process.poll.return_value = None
                for name, replacement in (("confinement", mock.Mock()), ("tools", lambda: (pilot, mock.Mock())),
                        ("build", lambda *args: ("server", "helper")), ("native_tokens", mock.Mock(return_value=[])),
                        ("endpoint", mock.Mock(side_effect=endpoints)), ("compare", mock.Mock())):
                    stack.enter_context(mock.patch.object(cpu, name, replacement))
                popen = stack.enter_context(mock.patch.object(session.subprocess, "Popen", side_effect=processes))
                stack.enter_context(mock.patch.object(session, "receipts", side_effect=lambda role, *args: {
                    role + "-runtime.json": b"{}\n", role + "-tokenizer.json": b"{}\n"}))
                hold = stack.enter_context(mock.patch.object(session, "hold"))
                if failure == "second-parity":
                    cpu.compare.side_effect = [None, ValueError("private failure")]
                if failure == "hold":
                    hold.side_effect = ValueError("private failure")
                row = session.run(archive, [Weight(0, pins), Weight(1, pins)], work, PRODUCER, IMAGE)
                self.assertEqual(popen.call_count, 2)
                self.assertEqual(pilot.stop.call_args_list, [mock.call(processes[1]), mock.call(processes[0])])
                self.assertEqual(row["result"], "pass" if failure is None else "fail")
                self.assertEqual(row["cleanup"], "unknown" if failure == "first-stop" else "confirmed")
                self.assertNotIn("private failure", json.dumps(row))
                self.assertTrue(all(call.kwargs["stdout"] == subprocess.DEVNULL
                                    and call.kwargs["stderr"] == subprocess.DEVNULL for call in popen.call_args_list))

    def test_real_pipe_relay_retains_original_bytes_and_ack_binding(self):
        value = packet()
        raw = session.probes.encoded(value)
        with mock.patch.object(session.identity, "source_receipt"):
            _, held = session.accept(raw, PRODUCER, IMAGE, time.monotonic() + 5)
        script = ("import sys,json;sys.stdout.buffer.write(" + repr(session.READY + raw + b"\n") + ");sys.stdout.flush();"
                  "ack=sys.stdin.buffer.readline();assert ack.startswith(" + repr(session.DONE) + ");"
                  "sys.stdout.buffer.write(" + repr(session.probes.encoded(result(held))) + ")")
        snapshot = mock.Mock()
        def cleanup(process):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
        snapshot.cleanup_process_group.side_effect = cleanup
        with tempfile.TemporaryDirectory() as temp, pipe() as (rd, wr), pipe() as (out_rd, out_wr):
            fd = os.open(temp, os.O_DIRECTORY)
            observed, failures = [], []
            def controller():
                try:
                    with os.fdopen(os.dup(out_rd), "rb", buffering=0) as stream:
                        line = stream.readline(4096)
                    observed.append(line)
                    packet = json.loads(line[len(session.READY):])
                    os.write(wr, session.DONE + session.probes.encoded({
                        "ready_sha256": packet["ready_sha256"], "released": True}) + b"\n")
                except BaseException as error:
                    failures.append(error)
            thread = threading.Thread(target=controller, daemon=True)
            thread.start()
            try:
                with mock.patch.object(session.identity, "source_receipt"):
                    actual = session.relay([sys.executable, "-I", "-S", "-B", "-c", script], {}, snapshot,
                                            PRODUCER, IMAGE, fd, read_fd=rd, write_fd=out_wr)
                self.assertEqual(actual, result(held))
                for name in session.FILES:
                    self.assertEqual((Path(temp) / name).read_bytes(), value["receipts"][name].encode())
                self.assertEqual(json.loads(observed[0][len(session.READY):]), held)
            finally:
                os.close(fd)
                thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])
        snapshot.cleanup_process_group.assert_called_once()

    def test_default_container_unchanged_and_session_requires_fifo_controller(self):
        command = rec.runtime_command("sha256:" + IMAGE, "nib-test")
        for flag, value in (("--network", "none"), ("--user", "65532:65532"), ("--memory", "8g"),
                            ("--memory-swap", "8g"), ("--cpus", "4"), ("--pids-limit", "128")):
            self.assertEqual(command[command.index(flag) + 1], value)
        self.assertNotIn("-i", command)
        with tempfile.TemporaryFile() as stream, mock.patch.object(subprocess, "Popen") as popen:
            with self.assertRaises(ValueError):
                session.relay([], {}, mock.Mock(), PRODUCER, IMAGE, -1,
                              read_fd=stream.fileno(), write_fd=stream.fileno())
            popen.assert_not_called()

    def test_controller_eof_child_output_and_timeout_reap_the_real_child(self):
        for case in ("eof", "stderr", "overflow", "timeout", "early-failure"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp, \
                    pipe() as (rd, wr), pipe() as (out_rd, out_wr):
                script = {"eof": "import time;time.sleep(10)",
                          "stderr": "import sys;sys.stderr.write('private-content');sys.stderr.flush()",
                          "overflow": "import sys;sys.stdout.write('x'*65537);sys.stdout.flush()",
                          "timeout": "import time;time.sleep(10)",
                          "early-failure": "import sys;sys.stdout.buffer.write(" +
                              repr(session.probes.encoded(result())) + ");sys.exit(1)"}[case]
                fd = os.open(temp, os.O_DIRECTORY)
                children = []
                original_popen = subprocess.Popen
                def spawn(*args, **kwargs):
                    child = original_popen(*args, **kwargs)
                    children.append(child)
                    return child
                snapshot = mock.Mock()
                def cleanup(child):
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=2)
                snapshot.cleanup_process_group.side_effect = cleanup
                original_read = os.read
                with mock.patch.object(session.subprocess, "Popen", side_effect=spawn), \
                        mock.patch.object(cpu, "TOTAL_SECONDS", 0.05 if case == "timeout" else 2), \
                        mock.patch.object(session.os, "read", side_effect=lambda n, count: b"" if n == rd else
                                          original_read(n, count)) if case == "eof" else contextlib.nullcontext():
                    # Wake the controller descriptor, then simulate its actual EOF.
                    if case == "eof":
                        os.write(wr, b"wake")
                    try:
                        if case == "early-failure":
                            self.assertEqual(session.relay([sys.executable, "-I", "-S", "-B", "-c", script], {},
                                snapshot, PRODUCER, IMAGE, fd, read_fd=rd, write_fd=out_wr), result())
                        else:
                            with self.assertRaises((ValueError, session.llama.worker.Abort)):
                                session.relay([sys.executable, "-I", "-S", "-B", "-c", script], {}, snapshot,
                                              PRODUCER, IMAGE, fd, read_fd=rd, write_fd=out_wr)
                    finally:
                        os.close(fd)
                self.assertEqual(len(children), 1)
                self.assertTrue(all(child.poll() is not None for child in children))
                self.assertEqual(list(Path(temp).iterdir()), [])

    def test_optin_supervisor_keeps_exact_image_bounds_and_always_cleans_daemon_resources(self):
        namespace = rec.execute.__globals__
        for failed in (False, True):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as temp:
                work = Path(temp)
                snapshot = mock.Mock()
                snapshot.local_docker_endpoint.return_value = "unix:///fixture"
                def execute(argv, **kwargs):
                    if argv[1:3] == ["image", "inspect"]:
                        return subprocess.CompletedProcess(argv, 0, ("sha256:" + IMAGE + " linux/amd64\n").encode(), b"")
                    self.assertEqual(argv[1], "build")
                    self.assertEqual(kwargs["timeout"], 240)
                    (work / "image-id").write_text("sha256:" + IMAGE)
                    return subprocess.CompletedProcess(argv, 0)
                snapshot.execute_process_group.side_effect = execute
                cleanup = mock.Mock(return_value={"result": "pass", "container": {"absence": "confirmed"},
                                                  "image": {"absence": "confirmed"}})
                relay = mock.Mock(side_effect=ValueError("private") if failed else None, return_value=result())
                report = {"git_sha": PRODUCER["git_sha"], "result": "fail", "runtime": None}
                with mock.patch.dict(namespace, toolkit=lambda _: cpu, context=lambda *args: (
                        "d" * 64, {rec.SCRIPT: PRODUCER["source_sha256"]}),
                        load=lambda *args: types.SimpleNamespace(cleanup_resources=cleanup)), \
                        mock.patch.dict(sys.modules, tau_cpu_session=types.SimpleNamespace(relay=relay)):
                    rec.execute(fixtures.ROOT, work, {}, snapshot, [], report, Path("/unused"), session_fd=42)
                args = relay.call_args.args
                command = args[0]
                self.assertEqual(command[-8:], ["sha256:" + IMAGE, "-I", "-S", "-B", rec.SCRIPT,
                                                "--session-worker", "sha256:" + IMAGE, PRODUCER["git_sha"]])
                self.assertEqual(command[2:5], ["-i", "--entrypoint", "python3"])
                self.assertEqual(args[3:], (PRODUCER, IMAGE, 42))
                cleanup.assert_called_once()
                self.assertEqual(report["cleanup"], dict.fromkeys(("container", "image", "build_completion"), "confirmed"))
                self.assertNotIn("private", json.dumps(report))


class Weight:
    def __init__(self, index, pins):
        self.index, self.pins = index, pins

    def stat(self):
        return types.SimpleNamespace(st_size=self.pins["models"][cpu.MODEL_IDS[self.index]]["bytes"])


if __name__ == "__main__":
    unittest.main()
