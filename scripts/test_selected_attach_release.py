# SPDX-License-Identifier: Apache-2.0
"""Synthetic process identities and real local pipe/selector tests; no Docker."""

from contextlib import ExitStack, redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import selected_service_network as network
from test_selected_service_network import network as selected_network
import test_selected_service_cancel as identity_tests

release = network.source_module("attach_release_test", "host/release-selected-attach.py")
cancel, host = release.cancel, release.host


def value():
    row = identity_tests.expectation()
    row["source_revision"] = release.LEGACY_REVISION
    row["argv"].extend(["--lifetime-profile", release.smoke.service.ORDINARY_PROFILE,
        "--build-sha256", "b" * 64, "--preparation-sha256", "c" * 64])
    return row


def prior(v, identity):
    return {**cancel.report(), "outcome": "signal-requested", "stage": "completed",
        "signal_intent_at": cancel.now(), "finished_at": cancel.now(),
        "expectation_sha256": cancel.digest(cancel.encoded(v)), "source_revision": release.LEGACY_REVISION,
        "source_closure_sha256": "d" * 64, "current_identity": identity, "identity_verified": True,
        "pidfd_bound": True, "signal_sent": True, "signal_may_have_been_sent": True}


def state_fixture(root, v):
    """Real private journals using production writers and readers."""
    root.mkdir(mode=0o700)
    for name in ("network", "container"):
        (root / name).mkdir(mode=0o700)
    selected = selected_network()
    wall, monotonic = time.time() + 3000, time.monotonic() + 3000
    v["state_dir"] = str(root)
    v["argv"][v["argv"].index("--state-dir") + 1] = str(root)
    cutoff = release.smoke.service.retirement_at(wall)
    v["argv"][v["argv"].index("--hard-stop-at") + 1] = cutoff
    journal = network.Journal(root / "network", selected, wall, monotonic,
                              lifetime_profile=release.smoke.service.ORDINARY_PROFILE)
    journal.initialize()
    for event in network.EVENTS[:-1]:
        journal.record(event, "e" * 64 if event == "network-resolved" else None)
    image = "sha256:" + "f" * 64
    runtime = {"command": ["docker", "container", "create", image, "--lifetime-profile",
        release.smoke.service.ORDINARY_PROFILE, "--hard-stop-at",
        release.smoke.service.retirement_at(wall - release.smoke.HOST_RESERVE)],
        "image_id": image, "image_labels": {}, "network_id": "e" * 64,
        "entrypoint": ["python", "--prepared-sha256", "a" * 64],
        "weights": "/private/weights", "inputs": "/private/inputs"}
    state = release.owner.State(root / "container")
    state.initialize(release.owner.owner_intent(journal), runtime)
    for event in release.owner.EVENTS:
        state.record(event, "1" * 64 if event == "container-resolved" else None)
    return state, journal


def terminal(v, rows, contract):
    cleanup = {key: True for key in ("container_absent", "network_absent", "creations_resolved",
                                   "quarantine_confirmed", "policy_removed", "cleanup_confirmed")}
    return {**host.report(), "schema": "nib.selected-service-owner/v2", "reason": "lifecycle",
        "preflight_pass": True, "container_created": True, "tls_publication_requested": True,
        "runtime_started": None, "cleanup": "confirmed", "owner_cleanup": cleanup,
        "watchdog_confirmed": True, "private_inputs_removed": True,
        "build_sha256": "b" * 64, "preparation_sha256": "c" * 64, "prepared_sha256": "a" * 64,
        "image_id": rows["runtime.json"]["image_id"], "lifetime": {
            **release.smoke.service.lifetime_record(release.smoke.service.ORDINARY_PROFILE,
                contract["hard_wall"] - release.smoke.HOST_RESERVE),
            "owner_hard_stop_at": release.argument(v, "--hard-stop-at")}}


class CancellationTests(unittest.TestCase):
    def test_real_blocked_selector_cancellation_escapes_and_runs_finally(self):
        code = r'''
import io, os, selectors, signal, sys
from contextlib import redirect_stdout
from unittest import mock
sys.path.insert(0, sys.argv[1])
import selected_service_network as network
host = network.source_module("selector_owner", "host/record-selected-service.py")
legacy = sys.argv[3] == "legacy"
if legacy:
    host.Cancelled = InterruptedError
returned, cleaned, blocked = [], [], []
def preflight(args):
    reader, writer = os.pipe()
    previous = signal.getsignal(signal.SIGALRM)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(reader, selectors.EVENT_READ)
            def deliver(signum, frame):
                blocked.append(frame.f_code.co_filename == selectors.__file__ and frame.f_code.co_name == "select")
                os.kill(os.getpid(), getattr(signal, sys.argv[2]))
            signal.signal(signal.SIGALRM, deliver)
            signal.setitimer(signal.ITIMER_REAL, .05)
            selector.select(1)
            returned.append(True)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        os.close(reader)
        os.close(writer)
        cleaned.append(True)
    return {"result": "pass"}
with mock.patch.object(host.smoke.QuietParser, "parse_args", return_value=None), \
     mock.patch.object(host, "preflight", preflight), redirect_stdout(io.StringIO()):
    status = host.main([])
assert blocked == [True] and cleaned == [True]
if legacy:
    assert status == 0 and returned == [True]
else:
    assert status == 1 and not returned
    assert not issubclass(host.Cancelled, Exception)
'''
        for signum in ("SIGTERM", "SIGINT"):
            for mode in ("legacy", "fixed"):
                with self.subTest(signum=signum, mode=mode):
                    result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code,
                        str(release.ROOT / "scripts"), signum, mode], capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr.decode())

    @unittest.skipUnless(sys.platform == "linux", "real Linux pidfd pipe-release proof")
    def test_pidfd_killed_pipe_child_releases_parent_without_parent_signal(self):
        # A harmless stand-in holds the same stdout/stderr pipe topology as the
        # Docker CLI. Only that child's pidfd is signalled; parent runs finally.
        code = r'''
import os, selectors, subprocess, sys
child = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c",
    "import time; time.sleep(30)"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
print(child.pid, flush=True)
try:
    with selectors.DefaultSelector() as selector:
        for stream in (child.stdout, child.stderr):
            selector.register(stream, selectors.EVENT_READ)
        while selector.get_map():
            for key, mask in selector.select(5):
                if not os.read(key.fd, 4096):
                    selector.unregister(key.fd)
    assert child.wait(timeout=2) == -9
finally:
    if child.poll() is None:
        child.kill()
        child.wait(timeout=2)
    child.stdout.close()
    child.stderr.close()
    print("parent-finally", flush=True)
'''
        process = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", code],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            readable, _, _ = select.select([process.stdout], [], [], 5)
            self.assertTrue(readable)
            pid = int(process.stdout.readline())
            fd = os.pidfd_open(pid)
            try:
                signal.pidfd_send_signal(fd, signal.SIGKILL, None, 0)
                stdout, stderr = process.communicate(timeout=5)
                self.assertTrue(release.exited(fd))
            finally:
                os.close(fd)
            self.assertEqual((process.returncode, stdout, stderr), (0, "parent-finally\n", ""))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()


class TemporaryFiles(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(cancel, "ROOT_UID", os.getuid()))
        self.end = time.monotonic() + 60
        self.value = value()


class FileTests(TemporaryFiles):
    def test_prior_cancel_exact_hash_join_legacy_and_no_original_success(self):
        row = prior(self.value, {"pid": 231})
        sha = cancel.digest(cancel.encoded(self.value))
        self.assertEqual(release.prior_receipt(cancel.encoded(row), self.value, sha), row)
        for key, changed in (("signal_sent", False), ("signal_sent", 1), ("outcome", "identified"),
            ("cleanup_confirmed", True), ("source_revision", "f" * 40), ("expectation_sha256", "f" * 64),
            ("original_run_pass", True), ("extra", "PRIVATE_CANARY")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                release.prior_receipt(cancel.encoded({**row, key: changed}), self.value, sha)
        for change in (lambda v: v.update(stderr="/root/other.log"),
                       lambda v: v["argv"].append("--tool-smoke"),
                       lambda v: v.update(source_revision="e" * 40)):
            v = copy.deepcopy(self.value)
            change(v)
            with self.assertRaises(ValueError):
                release.prior_receipt(cancel.encoded(row), v, sha)

    def test_real_journals_exact_state_join_and_immutable_stamps(self):
        state, journal = state_fixture(self.root / "state", self.value)
        before = release.journals(self.value, self.end)
        self.assertEqual(before[0], state.read())
        self.assertEqual(before[1], journal.contract)
        self.assertEqual(release.journals(self.value, self.end), before)
        path = state.root / "runtime.json"
        original = path.read_bytes()
        changed = json.loads(original)
        changed["network_id"] = "2" * 64
        path.write_bytes(cancel.encoded(changed))
        with self.assertRaises(ValueError):
            release.journals(self.value, self.end)
        path.write_bytes(original)
        self.assertNotEqual(release.journals(self.value, self.end), before)
        journal.record("network-removed")
        with self.assertRaises(ValueError):
            release.journals(self.value, self.end)

    def test_missing_start_extra_state_symlink_or_expired_contract_rejected(self):
        state_fixture(self.root / "state", self.value)
        state = Path(self.value["state_dir"])
        (state / "extra").touch()
        with self.assertRaises(ValueError):
            release.journals(self.value, self.end)
        (state / "extra").unlink()
        path = state / "container/start-intent.json"
        original = path.read_bytes()
        path.unlink()
        with self.assertRaises(ValueError):
            release.journals(self.value, self.end)
        path.symlink_to(state / "container/container-intent.json")
        with self.assertRaises((OSError, ValueError)):
            release.journals(self.value, self.end)
        path.unlink()
        path.write_bytes(original)
        path.chmod(0o600)
        with mock.patch.object(release.time, "time", return_value=1e11), self.assertRaises(ValueError):
            release.journals(self.value, self.end)

    def test_terminal_closed_failure_joins_and_no_success_or_content_leak(self):
        state_fixture(self.root / "state", self.value)
        rows, contract, _ = release.journals(self.value, self.end)
        row = terminal(self.value, rows, contract)
        self.assertEqual(release.terminal(cancel.encoded(row), self.value, rows, contract), row)
        for key, changed in (("result", "pass"), ("status", "PASS"), ("reason", "completed"),
            ("tools", True), ("runtime_started", True), ("prepared_sha256", "e" * 64),
            ("image_id", "sha256:" + "e" * 64), ("build_sha256", "e" * 64),
            ("owner_cleanup", None), ("error", "PRIVATE_CANARY")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                release.terminal(cancel.encoded({**row, key: changed}), self.value, rows, contract)
        changed = copy.deepcopy(row)
        changed["owner_cleanup"]["container_absent"] = False
        with self.assertRaises(ValueError):
            release.terminal(cancel.encoded(changed), self.value, rows, contract)

    def test_terminal_capture_is_byte_exact_only_after_both_exits(self):
        state_fixture(self.root / "state", self.value)
        rows, contract, _ = release.journals(self.value, self.end)
        raw = json.dumps(terminal(self.value, rows, contract), indent=None).encode() + b"\n"
        logfile = self.root / "owner.log"
        logfile.write_bytes(b"PRIVATE_EARLIER_LOG_CANARY\n" + raw)
        logfile.chmod(0o600)
        self.value["stdout"] = str(logfile)
        parent = {"logs": {"stdout": cancel.inode(cancel.file_info(logfile))}}
        output = self.root / "output"
        output.mkdir(mode=0o700)
        result = release.report()
        with cancel.directory(output, private=True) as fd, mock.patch.object(release, "exited", return_value=True):
            release.observe_completion(81, 82, self.value, parent, rows, contract, result, fd, self.end)
        self.assertEqual((output / "owner-terminal.json").read_bytes(), raw)
        self.assertTrue(result["owner_reported_cleanup"])
        self.assertFalse(result["cleanup_confirmed"])
        self.assertFalse(result["original_run_pass"])
        self.assertNotIn("PRIVATE_EARLIER_LOG_CANARY", cancel.encoded(result).decode())
        with mock.patch.object(release, "exited", return_value=False), \
                mock.patch.object(release, "preserve") as preserve, self.assertRaises(ValueError):
            release.observe_completion(81, 82, self.value, parent, rows, contract,
                                       release.report(), -1, time.monotonic())
        preserve.assert_not_called()


class CLITests(TemporaryFiles):
    def invoke(self, action):
        raw = cancel.encoded(self.value)
        expectation = self.root / "expectation.json"
        expectation.write_bytes(raw)
        expectation.chmod(0o600)
        receipt = self.root / "cancel.json"
        receipt.write_bytes(cancel.encoded(prior(self.value, {"pid": 231})))
        receipt.chmod(0o600)
        destination = self.root / "result"
        output = io.StringIO()
        with mock.patch.object(release, "request", side_effect=action), redirect_stdout(output):
            status = release.main(["--release", "--expectation", str(expectation),
                "--cancel-receipt", str(receipt), "--cancel-receipt-sha256", cancel.digest(receipt.read_bytes()),
                "--output-dir", str(destination)])
        row = json.loads(output.getvalue())
        self.assertEqual(row, json.loads((destination / "receipt.json").read_bytes()))
        self.assertNotIn("PRIVATE_CANARY", output.getvalue())
        self.assertFalse(row["cleanup_confirmed"])
        self.assertFalse(row["original_run_pass"])
        return status, row

    def test_exception_after_identification_cannot_retain_success(self):
        def action(value, prior, result, *args):
            result.update(outcome="identified", stage="identified", identity_verified=True)
            raise release.Cancelled("PRIVATE_CANARY")
        status, row = self.invoke(action)
        self.assertEqual((status, row["outcome"]), (1, "rejected"))
        self.assertFalse(row["signal_may_have_been_sent"])

    def test_exception_after_signal_retains_uncertainty_not_success(self):
        def action(value, prior, result, *args):
            result.update(outcome="owner-failed", stage="completed", signal_may_have_been_sent=True,
                          signal_sent=True, owner_reported_cleanup=True)
            raise ValueError("PRIVATE_CANARY")
        status, row = self.invoke(action)
        self.assertEqual((status, row["outcome"]), (1, "unconfirmed"))
        self.assertTrue(row["signal_sent"])

    def test_invalid_arguments_are_content_free(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(release.main(["--PRIVATE_CANARY"]), 1)
        self.assertEqual(json.loads(output.getvalue())["outcome"], "rejected")
        self.assertNotIn("PRIVATE_CANARY", output.getvalue())


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.value = value()
        self.parent = {"pid": 231, "start_ticks": 456}
        self.child = {"pid": 234, "parent_pid": 231}
        self.prior = prior(self.value, self.parent)
        self.result = release.report()
        self.end = time.monotonic() + 60
        self.state = ({"container-resolved.json": {"id": "1" * 64}},
            {"hard_wall": time.time() + 3000, "hard_monotonic": time.monotonic() + 3000}, {})
        def patch(obj, key, **kwargs):
            return self.stack.enter_context(mock.patch.object(obj, key, **kwargs))
        patch(release.sys, "platform", new="linux")
        patch(release.os, "getuid", return_value=0)
        self.source = patch(cancel, "source", return_value=("d" * 64, {}))
        self.discover = patch(cancel, "discover", side_effect=lambda value, end: 234 if value["argv"][0] == "docker" else 231)
        self.parent_observe = patch(cancel, "observe", return_value=self.parent)
        self.journals = patch(release, "journals", return_value=self.state)
        self.child_observe = patch(release, "child_identity", return_value=self.child)
        self.open = patch(release.os, "pidfd_open", side_effect=[81, 82], create=True)
        self.close = patch(release.os, "close")
        self.live = patch(cancel, "live")
        self.persist = patch(cancel, "persist")
        self.send = patch(release.signal, "pidfd_send_signal", create=True)
        self.kill = patch(release.os, "kill")
        self.completion = patch(release, "observe_completion")

    def request(self, send=True):
        release.request(self.value, self.prior, self.result, 90, send, self.end)

    def test_only_child_pidfd_sigkill_after_durable_intent_and_rechecks(self):
        def sent(*args):
            self.persist.assert_called_once()
            self.assertEqual(self.parent_observe.call_count, 3)
            self.assertEqual(self.child_observe.call_count, 3)
            self.assertTrue(self.result["signal_may_have_been_sent"])
            self.assertFalse(self.result["signal_sent"])
        self.send.side_effect = sent
        self.request()
        self.assertEqual(self.open.call_args_list, [mock.call(231, 0), mock.call(234, 0)])
        self.send.assert_called_once_with(82, signal.SIGKILL, None, 0)
        self.assertEqual(self.close.call_args_list, [mock.call(82), mock.call(81)])
        self.kill.assert_not_called()
        self.completion.assert_called_once()
        self.assertFalse(self.result["cleanup_confirmed"])
        self.assertFalse(self.result["original_run_pass"])

    def test_inspect_does_not_signal_or_observe_terminal(self):
        self.request(False)
        self.send.assert_not_called()
        self.persist.assert_not_called()
        self.completion.assert_not_called()
        self.assertEqual(self.result["outcome"], "identified")

    def test_prior_parent_identity_must_match_before_child_binding(self):
        self.prior["current_identity"] = {"pid": 231, "start_ticks": 999}
        with self.assertRaises(ValueError):
            self.request()
        self.open.assert_not_called()
        self.send.assert_not_called()

    def test_source_or_state_or_pid_drift_blocks_signal_after_intent(self):
        for target, sequence in ((self.parent_observe, [self.parent, self.parent, {"pid": 999}]),
            (self.child_observe, [self.child, self.child, {"pid": 999}]),
            (self.journals, [self.state, self.state, ({}, {}, {})])):
            with self.subTest(target=target):
                self.open.side_effect = [81, 82]
                target.side_effect = sequence
                with self.assertRaises(ValueError):
                    self.request()
                self.send.assert_not_called()
                target.side_effect = None
        self.open.side_effect = [81, 82]
        self.source.side_effect = [("d" * 64, {}), ("e" * 64, {})]
        with self.assertRaises(ValueError):
            self.request()
        self.send.assert_not_called()

    def test_ambiguous_child_denied_bind_dead_pidfd_or_fsync_never_signals(self):
        for target in (self.discover, self.open, self.live, self.persist):
            with self.subTest(target=target):
                self.open.side_effect = [81, 82]
                original = target.side_effect
                target.side_effect = PermissionError("PRIVATE_CANARY")
                with self.assertRaises(PermissionError):
                    self.request()
                self.send.assert_not_called()
                target.side_effect = original

    def test_signal_denial_or_interruption_leaves_uncertain_intent_no_retry(self):
        for error in (PermissionError(), ProcessLookupError(), release.Cancelled()):
            self.open.side_effect = [81, 82]
            self.send.reset_mock()
            self.send.side_effect = error
            with self.assertRaises(type(error)):
                self.request()
            self.send.assert_called_once_with(82, signal.SIGKILL, None, 0)
            self.assertEqual(self.result["outcome"], "unconfirmed")
            self.assertFalse(self.result["signal_sent"])
            self.completion.assert_not_called()


class ChildTests(TemporaryFiles):
    # Keep this fixture separate from admission mocks: real stat/no-follow/hash
    # reads check every child boundary. Only proc symlink spelling is synthetic.
    def make_child(self):
        v, parent_path = identity_tests.FilesTests.make_proc(self)
        self.stack.enter_context(mock.patch.object(cancel, "PROC", identity_tests.cancel.PROC))
        parent = cancel.observe(231, v, self.end)
        child = cancel.PROC / "234"
        for name in ("ns", "fd", "fdinfo"):
            (child / name).mkdir(parents=True)
        (parent_path / "fdinfo").mkdir()
        (parent_path / "task/231").mkdir(parents=True)
        (parent_path / "task/231/children").write_bytes(b"234 999\n")
        (child / "stat").write_bytes(b"234 (docker) S 231 234 234 " + b"0 " * 15 + b"567\n")
        (child / "status").write_bytes(b"Uid:\t0\t0\t0\t0\n")
        (child / "cmdline").write_bytes(cancel.command({"argv": ["docker", "container", "start", "--attach", "1" * 64]}))
        (child / "environ").write_bytes(b"\0".join((key + "=" + val).encode()
            for key, val in host.environment().items()) + b"\0")
        for name in ("pid", "user", "mnt", "net"):
            (child / "ns" / name).symlink_to(cancel.PROC / "self/ns" / name)
        executable = self.root / "docker"
        executable.write_bytes(b"synthetic executable")
        executable.chmod(0o755)
        (child / "exe").symlink_to(executable)
        self.stack.enter_context(mock.patch.object(release, "DOCKER", executable))
        venue = self.root / release.VENUE
        venue.parent.mkdir(parents=True)
        venue.write_bytes(cancel.encoded({"files": {str(executable): cancel.digest(executable.read_bytes())}}))
        self.stack.enter_context(mock.patch.object(release, "ROOT", self.root))
        targets = {}
        for writer, reader in ((1, 7), (2, 8)):
            pipe = self.root / ("pipe" + str(writer))
            os.mkfifo(pipe, 0o600)
            for proc, fd, flags in ((child, writer, "01"), (parent_path, reader, "00")):
                descriptor = proc / "fd" / str(fd)
                descriptor.symlink_to(pipe)
                (proc / "fdinfo" / str(fd)).write_bytes(("flags:\t" + flags + "\n").encode())
                targets[descriptor] = "pipe:[" + str(pipe.stat().st_ino) + "]"
        readlink = os.readlink
        self.stack.enter_context(mock.patch.object(release.os, "readlink",
            side_effect=lambda path: targets.get(Path(path), None) or readlink(path)))
        return parent, parent_path, child, executable

    def test_exact_child_executable_namespace_parent_group_pipes_and_environment(self):
        parent, parent_path, child, executable = self.make_child()
        observed = release.child_identity(234, parent, "1" * 64, self.end)
        self.assertEqual((observed["pid"], observed["parent_pid"], observed["start_ticks"]), (234, 231, 567))
        self.assertEqual([row[0] for row in observed["readers"]], [7, 8])
        for relative, raw in (("stat", b"234 (docker) S 999 234 234 " + b"0 " * 15 + b"567\n"),
            ("stat", b"234 (docker) S 231 999 234 " + b"0 " * 15 + b"567\n"),
            ("stat", b"234 (docker) S 231 234 999 " + b"0 " * 15 + b"567\n"),
            ("stat", b"234 (docker) S 231 234 234 " + b"0 " * 15 + b"1\n"),
            ("status", b"Uid:\t0\t1\t0\t0\n"), ("environ", b"DOCKER_HOST=other\0"),
            ("cmdline", b"docker\0container\0stop\0"), ("fdinfo/1", b"flags:\t00\n")):
            path = child / relative
            original = path.read_bytes()
            path.write_bytes(raw)
            with self.subTest(relative=relative, raw=raw), self.assertRaises(ValueError):
                release.child_identity(234, parent, "1" * 64, self.end)
            path.write_bytes(original)
        executable.write_bytes(b"changed executable")
        with self.assertRaises(ValueError):
            release.child_identity(234, parent, "1" * 64, self.end)

    def test_missing_wrong_direction_or_extra_parent_pipe_reader_rejected(self):
        parent, parent_path, child, _ = self.make_child()
        path = parent_path / "fdinfo/7"
        path.write_bytes(b"flags:\t01\n")
        with self.assertRaises(ValueError):
            release.child_identity(234, parent, "1" * 64, self.end)
        path.write_bytes(b"flags:\t00\n")
        (parent_path / "fd/7").unlink()
        with self.assertRaises(ValueError):
            release.child_identity(234, parent, "1" * 64, self.end)

    def test_wrong_namespace_or_not_main_thread_child_rejected(self):
        parent, parent_path, child, _ = self.make_child()
        membership = parent_path / "task/231/children"
        membership.write_bytes(b"999\n")
        with self.assertRaises(ValueError):
            release.child_identity(234, parent, "1" * 64, self.end)
        membership.write_bytes(b"234\n")
        link = child / "ns/net"
        link.unlink()
        link.symlink_to(parent_path / "status")
        with self.assertRaises(ValueError):
            release.child_identity(234, parent, "1" * 64, self.end)


if __name__ == "__main__":
    unittest.main()
