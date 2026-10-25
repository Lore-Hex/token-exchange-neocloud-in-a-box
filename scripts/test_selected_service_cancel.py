# SPDX-License-Identifier: Apache-2.0
"""Cancellation identity/intent tests; no Docker, service or host mutations."""

from contextlib import ExitStack, redirect_stdout
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("selected_cancel_test",
    Path(__file__).absolute().parents[1] / "host/cancel-selected-service.py")
cancel = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cancel)


def expectation(root=Path("/root/selected-source")):
    return {"schema": "nib.selected-owner-cancel-input/v1", "source_root": str(root),
        "source_revision": "a" * 40, "executable": "/usr/bin/python3.14",
        "executable_sha256": "b" * 64, "state_dir": "/root/selected-state",
        "stdout": "/root/selected-output/owner.log", "stderr": "/root/selected-output/owner.log",
        "argv": ["python3.14", "-I", "-S", "-B", str(root / cancel.OWNER), "--run",
            "--state-dir", "/root/selected-state", "--hard-stop-at", "2026-10-26T00:00:00Z"]}


class InputTests(unittest.TestCase):
    def test_exact_closed_contract_and_no_watchdog_inspection_or_pid_guess(self):
        value = expectation()
        self.assertEqual(cancel.expectation(cancel.encoded(value)), value)
        for mutate in (
            lambda v: v.update(pid=123), lambda v: v.update(source_root="/root/../source"),
            lambda v: v["argv"].append("--watchdog"), lambda v: v["argv"].append("--inspect"),
            lambda v: v["argv"].append("--run"), lambda v: v["argv"].extend(["--state-dir", "x"]),
            lambda v: v["argv"].__setitem__(4, "host/record-selected-service.py"),
            lambda v: v["argv"].__setitem__(1, "-c"), lambda v: v.update(state_dir="/root/other"),
            lambda v: v.update(source_revision="x" * 40), lambda v: v["argv"].append("CANARY\n"),
        ):
            with self.subTest(mutate=mutate):
                changed = copy.deepcopy(value)
                mutate(changed)
                with self.assertRaises(ValueError):
                    cancel.expectation(cancel.encoded(changed))
        with self.assertRaises(ValueError):
            cancel.expectation(b'{"schema":1,"schema":2}')


class TemporaryFiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # Only UID observation is adapted on macOS; real inode/mode/no-follow checks remain.
        self.stack.enter_context(mock.patch.object(cancel, "ROOT_UID", os.getuid()))
        self.end = time.monotonic() + 60


class FilesTests(TemporaryFiles):
    def test_private_directory_modes_symlinks_files_and_log_replacement(self):
        target = self.root / "private"
        target.mkdir(mode=0o700)
        with cancel.directory(target, private=True):
            pass
        target.chmod(0o755)
        with self.assertRaises(ValueError):
            with cancel.directory(target, private=True):
                pass
        target.chmod(0o700)
        alias = self.root / "alias"
        alias.symlink_to(target, target_is_directory=True)
        with self.assertRaises(OSError):
            with cancel.directory(alias):
                pass
        log = target / "owner.log"
        log.write_bytes(b"CANARY")
        log.chmod(0o600)
        original = cancel.inode(cancel.file_info(log))
        log.rename(target / "previous")
        log.write_bytes(b"CANARY")
        log.chmod(0o600)
        self.assertNotEqual(cancel.inode(cancel.file_info(log)), original)
        alias.unlink()
        alias.symlink_to(log)
        with self.assertRaises(ValueError):
            cancel.file_info(alias)
        log.chmod(0o666)
        with self.assertRaises(ValueError):
            cancel.file_info(log)

    def make_source(self):
        (self.root / "host").mkdir()
        (self.root / "scripts").mkdir()
        (self.root / cancel.OWNER).write_bytes(b"# synthetic source\n")
        (self.root / "scripts/helper.py").write_bytes(b"# helper\n")
        env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        def git(*args):
            return subprocess.check_output(["/usr/bin/git", "-C", str(self.root), *args],
                env=env, stderr=subprocess.DEVNULL)
        git("init", "--quiet")
        git("add", "host", "scripts")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "fixture")
        value = expectation(self.root)
        value["source_revision"] = git("rev-parse", "HEAD").decode().strip()
        return value

    def test_real_git_blob_closure_rejects_modified_or_untracked_imports(self):
        value = self.make_source()
        closure, stamps = cancel.source(value, self.end)
        self.assertEqual(len(closure), 64)
        self.assertEqual(set(stamps), {cancel.OWNER, "scripts/helper.py"})
        for name in ("scripts/shadow.py", "scripts/shadow.pyc", "scripts/shadow.so", "scripts/site.pth"):
            extra = self.root / name
            extra.write_bytes(b"CANARY")
            with self.assertRaises(ValueError):
                cancel.source(value, self.end)
            extra.unlink()
        changed = {**value, "source_revision": "c" * 40}
        with self.assertRaises(ValueError):
            cancel.source(changed, self.end)
        (self.root / "scripts/helper.py").write_bytes(b"# changed\n")
        with self.assertRaises(ValueError):
            cancel.source(value, self.end)

    def make_proc(self, pid=231):
        value = expectation()
        proc = self.root / "proc"
        proc.mkdir()
        process = proc / str(pid)
        (process / "ns").mkdir(parents=True)
        (proc / "self/ns").mkdir(parents=True)
        (proc / "sys/kernel/random").mkdir(parents=True)
        (proc / "sys/kernel/random/boot_id").write_bytes(b"synthetic-boot\n")
        (process / "fd").mkdir()
        (process / "cmdline").write_bytes(cancel.command(value))
        (process / "stat").write_bytes((str(pid) + " (name ) with spaces) S 1 2 3 " +
            "0 " * 15 + "456\n").encode())
        (process / "status").write_bytes(b"Name:\tpython\nUid:\t0\t0\t0\t0\n" + b"\n".join(
            name.encode() + b":\t" + (b"0000000000004002" if name == "SigCgt" else b"0000000000000000")
            for name in ("SigCgt", "SigIgn", "SigBlk", "SigPnd", "ShdPnd")) + b"\n")
        for name in ("pid", "user", "mnt", "net"):
            common = proc / "self/ns" / name
            common.touch()
            (process / "ns" / name).symlink_to(common)
        exe = self.root / "python"
        exe.write_bytes(b"synthetic executable")
        exe.chmod(0o755)
        value.update(executable=str(exe), executable_sha256=cancel.digest(exe.read_bytes()))
        (process / "exe").symlink_to(exe)
        log = self.root / "owner.log"
        log.write_bytes(b"PRIVATE_CONTENT_CANARY")
        log.chmod(0o600)
        value.update(stdout=str(log), stderr=str(log))
        for number in (1, 2):
            (process / "fd" / str(number)).symlink_to(log)
        state = self.root / "state"
        state.mkdir(mode=0o700)
        value["state_dir"] = str(state)
        value["argv"][7] = str(state)
        (process / "cmdline").write_bytes(cancel.command(value))
        self.stack.enter_context(mock.patch.object(cancel, "PROC", proc))
        return value, process

    def test_real_proc_fixture_exact_identity_no_log_content(self):
        value, process = self.make_proc()
        self.assertEqual(cancel.discover(value, self.end), 231)
        identity = cancel.observe(231, value, self.end)
        self.assertEqual(identity["start_ticks"], 456)
        self.assertNotIn("PRIVATE_CONTENT_CANARY", json.dumps(identity))
        for name, raw in (("cmdline", b"different\0"), ("status", b"Uid:\t0\t1\t0\t0\n"),
                          ("stat", b"231 (gone) Z " + b"0 " * 20)):
            original = (process / name).read_bytes()
            (process / name).write_bytes(raw)
            with self.assertRaises(ValueError):
                cancel.observe(231, value, self.end)
            (process / name).write_bytes(original)
        value["executable_sha256"] = "d" * 64
        with self.assertRaises(ValueError):
            cancel.observe(231, value, self.end)

    def test_discovery_absent_duplicate_denied_oversize_and_deadline(self):
        value, process = self.make_proc()
        duplicate = process.parent / "232"
        duplicate.mkdir()
        (duplicate / "cmdline").write_bytes(cancel.command(value))
        with self.assertRaises(ValueError):
            cancel.discover(value, self.end)
        (duplicate / "cmdline").write_bytes(b"")
        self.assertEqual(cancel.discover(value, self.end), 231)
        (process / "cmdline").write_bytes(b"")
        with self.assertRaises(ValueError):
            cancel.discover(value, self.end)
        for error in (PermissionError("CANARY"),):
            with mock.patch.object(cancel, "kernel", side_effect=error), self.assertRaises(PermissionError):
                cancel.discover(value, self.end)
        (process / "cmdline").write_bytes(b"x" * (cancel.LIMIT + 1))
        with self.assertRaises(ValueError):
            cancel.discover(value, self.end)
        with self.assertRaises(ValueError):
            cancel.discover(value, time.monotonic() - 1)

    def test_missing_blocked_ignored_or_already_pending_term_refuses_signal(self):
        value, process = self.make_proc()
        original = (process / "status").read_bytes()
        for name in ("SigCgt", "SigIgn", "SigBlk", "SigPnd", "ShdPnd"):
            before = b"0000000000004002" if name == "SigCgt" else b"0000000000000000"
            after = b"0000000000000002" if name == "SigCgt" else b"0000000000004000"
            (process / "status").write_bytes(original.replace(name.encode() + b":\t" + before,
                                                               name.encode() + b":\t" + after))
            with self.subTest(name=name), self.assertRaises(ValueError):
                cancel.observe(231, value, self.end)

    def test_wrong_namespace_executable_descriptor_or_log_inode_rejected(self):
        value, process = self.make_proc()
        for relative in ("ns/net", "exe", "fd/1", "fd/2"):
            link = process / relative
            original = os.readlink(link)
            other = self.root / "other"
            other.write_bytes(b"CANARY")
            link.unlink()
            link.symlink_to(other)
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                cancel.observe(231, value, self.end)
            link.unlink()
            link.symlink_to(original)


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.value = expectation()
        self.result = cancel.report()
        self.end = time.monotonic() + 60
        self.identity = {"pid": 123, "start_ticks": 456}
        self.stack.enter_context(mock.patch.object(cancel.sys, "platform", "linux"))
        self.stack.enter_context(mock.patch.object(cancel.os, "getuid", return_value=0))
        self.source = self.stack.enter_context(mock.patch.object(cancel, "source", return_value=("c" * 64, {})))
        self.discover = self.stack.enter_context(mock.patch.object(cancel, "discover", return_value=123))
        self.observe = self.stack.enter_context(mock.patch.object(cancel, "observe", return_value=self.identity))
        self.open = self.stack.enter_context(mock.patch.object(cancel.os, "pidfd_open", return_value=91, create=True))
        self.close = self.stack.enter_context(mock.patch.object(cancel.os, "close"))
        self.live = self.stack.enter_context(mock.patch.object(cancel, "live"))
        self.persist = self.stack.enter_context(mock.patch.object(cancel, "persist"))
        self.send = self.stack.enter_context(mock.patch.object(cancel.signal, "pidfd_send_signal", create=True))
        self.kill = self.stack.enter_context(mock.patch.object(cancel.os, "kill"))

    def run_request(self, send=True):
        cancel.request(self.value, self.result, self.end, 81, send)

    def test_one_pidfd_term_after_durable_intent_never_pass_or_cleanup(self):
        def sent(*args):
            self.persist.assert_called_once()
            self.assertTrue(self.result["signal_may_have_been_sent"])
            self.assertFalse(self.result["signal_sent"])
        self.send.side_effect = sent
        self.run_request()
        self.open.assert_called_once_with(123, 0)
        self.send.assert_called_once_with(91, signal.SIGTERM, None, 0)
        self.close.assert_called_once_with(91)
        self.kill.assert_not_called()
        self.assertEqual(self.result["outcome"], "signal-requested")
        for flag in ("original_launch_identity_proven", "owner_exit_observed", "cleanup_confirmed",
                     "original_run_pass", "soak_validated"):
            self.assertIs(self.result[flag], False)

    def test_inspect_never_signals(self):
        self.run_request(send=False)
        self.send.assert_not_called()
        self.persist.assert_not_called()
        self.assertEqual(self.result["outcome"], "identified")

    def test_pid_reuse_after_bind_or_during_last_recheck_rejected(self):
        for call in (1, 2, 3):
            with self.subTest(call=call):
                self.observe.side_effect = [self.identity] * call + [{"pid": 123, "start_ticks": 999}]
                with self.assertRaises(ValueError):
                    self.run_request()
                self.send.assert_not_called()

    def test_source_drift_ambiguity_and_closed_pidfd_reject(self):
        for target, effect in ((self.source, [("c" * 64, {}), ("d" * 64, {})]),
                               (self.discover, [123, 124]), (self.live, ProcessLookupError())):
            with self.subTest(target=target):
                target.side_effect = effect
                with self.assertRaises((ValueError, ProcessLookupError)):
                    self.run_request()
                self.send.assert_not_called()
                target.side_effect = None

    def test_new_duplicate_after_durable_intent_still_blocks_send(self):
        self.discover.side_effect = [123, 123, ValueError("duplicate")]
        with self.assertRaises(ValueError):
            self.run_request()
        self.persist.assert_called_once()
        self.send.assert_not_called()

    def test_bind_denial_or_unsupported_kernel_never_falls_back(self):
        for error in (PermissionError(), ProcessLookupError(), OSError(38, "unsupported")):
            self.open.side_effect = error
            with self.assertRaises(OSError):
                self.run_request()
            self.send.assert_not_called()
            self.kill.assert_not_called()

    def test_signal_denial_interruption_and_intent_failure_never_retried(self):
        for error in (PermissionError(), ProcessLookupError(), KeyboardInterrupt("CANARY")):
            self.send.reset_mock()
            self.send.side_effect = error
            with self.assertRaises(type(error)):
                self.run_request()
            self.send.assert_called_once()
            self.assertEqual(self.result["outcome"], "unconfirmed")
            self.assertIs(self.result["signal_sent"], False)
        self.send.reset_mock()
        self.persist.side_effect = OSError("disk full CANARY")
        with self.assertRaises(OSError):
            self.run_request()
        self.send.assert_not_called()


class ReceiptTests(TemporaryFiles):
    def test_output_cannot_write_into_owner_source_or_state(self):
        value = expectation(self.root)
        expected = self.root / "expectation.json"
        expected.write_bytes(cancel.encoded(value))
        expected.chmod(0o600)
        output = self.root / "receipt"
        with redirect_stdout(io.StringIO()), mock.patch.object(cancel, "request") as request:
            self.assertEqual(cancel.main(["--cancel", "--expectation", str(expected),
                                           "--output-dir", str(output)]), 1)
        request.assert_not_called()
        self.assertFalse(output.exists())

    def test_cli_receipts_private_exclusive_and_content_free_on_failure(self):
        value = self.root / "expectation.json"
        value.write_bytes(cancel.encoded(expectation()))
        value.chmod(0o600)
        output = self.root / "receipt"
        stdout = io.StringIO()
        with mock.patch.object(cancel, "request", side_effect=PermissionError("PRIVATE_CANARY")), redirect_stdout(stdout):
            code = cancel.main(["--cancel", "--expectation", str(value), "--output-dir", str(output)])
        self.assertEqual(code, 1)
        self.assertNotIn("PRIVATE_CANARY", stdout.getvalue())
        self.assertEqual(json.loads((output / "receipt.json").read_bytes()), json.loads(stdout.getvalue()))
        self.assertEqual((output / "receipt.json").stat().st_mode & 0o777, 0o600)
        with redirect_stdout(io.StringIO()), mock.patch.object(cancel, "request") as request:
            self.assertEqual(cancel.main(["--cancel", "--expectation", str(value), "--output-dir", str(output)]), 1)
        request.assert_not_called()

    def test_durable_uncertain_intent_and_original_failure_not_promoted(self):
        value = self.root / "expectation.json"
        value.write_bytes(cancel.encoded(expectation()))
        value.chmod(0o600)
        output = self.root / "receipt"
        def interrupted(value, result, end, fd, send):
            result.update(outcome="unconfirmed", stage="signal", signal_may_have_been_sent=True)
            cancel.persist(fd, "signal-intent.json", result)
            self.assertIs(json.loads((output / "signal-intent.json").read_bytes())["signal_sent"], False)
            raise KeyboardInterrupt("PRIVATE_CANARY")
        with mock.patch.object(cancel, "request", side_effect=interrupted), redirect_stdout(io.StringIO()):
            code = cancel.main(["--cancel", "--expectation", str(value), "--output-dir", str(output)])
        self.assertEqual(code, 1)
        result = json.loads((output / "receipt.json").read_bytes())
        self.assertEqual(result["outcome"], "unconfirmed")
        self.assertIs(result["original_run_pass"], False)
        self.assertIs(result["cleanup_confirmed"], False)
        self.assertNotIn("PRIVATE_CANARY", (output / "receipt.json").read_text())

    def test_directory_or_intent_fsync_failure_prevents_request(self):
        value = self.root / "expectation.json"
        value.write_bytes(cancel.encoded(expectation()))
        value.chmod(0o600)
        with mock.patch.object(cancel.os, "fsync", side_effect=OSError("PRIVATE_CANARY")), \
                mock.patch.object(cancel, "request") as request, redirect_stdout(io.StringIO()):
            code = cancel.main(["--cancel", "--expectation", str(value),
                                "--output-dir", str(self.root / "receipt")])
        self.assertEqual(code, 1)
        request.assert_not_called()

    def test_repeated_cancellation_during_restore_does_not_interrupt_restoration(self):
        original_signal = signal.signal
        installed, restored = {}, []
        def install(sig, handler):
            if callable(handler) and getattr(handler, "__name__", "") == "cancel":
                installed[sig] = handler
            elif sig in installed:
                restored.append(sig)
                if sig == signal.SIGINT:
                    installed[signal.SIGTERM]()
                    installed[signal.SIGTERM]()
            return original_signal(sig, handler)
        value = self.root / "expectation.json"
        value.write_bytes(cancel.encoded(expectation()))
        value.chmod(0o600)
        with mock.patch.object(signal, "signal", side_effect=install), redirect_stdout(io.StringIO()), \
                mock.patch.object(cancel, "request", side_effect=KeyboardInterrupt()):
            self.assertEqual(cancel.main(["--cancel", "--expectation", str(value),
                "--output-dir", str(self.root / "receipt")]), 1)
        self.assertEqual(restored, [signal.SIGINT, signal.SIGTERM])


@unittest.skipUnless(sys.platform == "linux", "Linux procfs/pidfd kernel regression")
class LinuxPidfdTests(unittest.TestCase):
    def test_real_child_exit_pidfd_never_targets_replacement(self):
        # No service or sockets; the only target is this test's freshly owned child.
        child = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", "import time; time.sleep(30)"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            fd = os.pidfd_open(child.pid, 0)
            try:
                cancel.live(fd)
                signal.pidfd_send_signal(fd, signal.SIGTERM, None, 0)
                self.assertEqual(child.wait(timeout=5), -signal.SIGTERM)
                with self.assertRaises(ValueError):
                    cancel.live(fd)
                with self.assertRaises(ProcessLookupError):
                    signal.pidfd_send_signal(fd, signal.SIGTERM, None, 0)
            finally:
                os.close(fd)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
