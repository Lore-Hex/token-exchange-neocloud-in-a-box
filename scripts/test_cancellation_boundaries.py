# SPDX-License-Identifier: Apache-2.0
"""Real blocked selectors, synthetic work, and unchanged closed failure envelopes."""

from contextlib import ExitStack, contextmanager, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import selectors
import signal
import tempfile
import time
import types
import unittest
from unittest import mock

import test_selected_service_cancel as cancel_tests
import test_selected_service_smoke as smoke_tests
import test_selected_service_recovery as recovery_tests
import test_deepseek_lab as lab_tests
import test_engine_image as image_tests
import test_engine_import_smoke as import_tests
import test_engine_cpu_entry as entry_tests
import test_public_weight_cache as cache_tests
import test_cache_profile_seed as profile_tests
import test_selected_weight_view as view_tests

SIGS = (signal.SIGINT, signal.SIGTERM)


def interrupt_selector(signum):
    """Deliver only while in the real selector; a swallowed stop returns normally."""
    reader, writer = os.pipe()
    previous = signal.getsignal(signal.SIGALRM)
    observed = []
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    def deliver(_sig, frame):
        observed.append(frame.f_code.co_filename == selectors.__file__ and frame.f_code.co_name == "select")
        os.kill(os.getpid(), signum)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(reader, selectors.EVENT_READ)
            signal.signal(signal.SIGALRM, deliver)
            signal.setitimer(signal.ITIMER_REAL, .05)
            selector.select(1)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        os.close(reader)
        os.close(writer)
        assert observed == [True], "signal did not interrupt the real selector"


@contextmanager
def handlers(handler):
    original = {sig: signal.getsignal(sig) for sig in SIGS}
    try:
        for sig in SIGS:
            signal.signal(sig, handler)
        yield
    finally:
        for sig, saved in original.items():
            signal.signal(sig, saved)


class CancellationBoundaryTests(unittest.TestCase):
    def test_cancel_helper_signal_cannot_continue_to_target_mutation(self):
        cancel = cancel_tests.cancel
        for sig in SIGS:
            with self.subTest(sig=sig), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                source = root / "expectation.json"
                source.write_bytes(cancel.encoded(cancel_tests.expectation()))
                source.chmod(0o600)
                mutation = mock.Mock()
                def request(*args):
                    interrupt_selector(sig)
                    mutation()
                output = io.StringIO()
                before = {sig: signal.getsignal(sig) for sig in SIGS}
                with mock.patch.object(cancel, "ROOT_UID", os.getuid()), \
                        mock.patch.object(cancel, "request", side_effect=request), redirect_stdout(output):
                    self.assertEqual(cancel.main(["--cancel", "--expectation", str(source),
                        "--output-dir", str(root / "receipt")]), 1)
                row = json.loads(output.getvalue())
                self.assertEqual(row["outcome"], "rejected")
                self.assertFalse(row["signal_may_have_been_sent"])
                self.assertEqual(row, json.loads((root / "receipt/receipt.json").read_bytes()))
                mutation.assert_not_called()
                self.assertEqual(before, {sig: signal.getsignal(sig) for sig in SIGS})

    def test_smoke_main_retains_failed_envelope_and_restores_handlers(self):
        smoke = smoke_tests.smoke
        for sig in SIGS:
            continued = mock.Mock()
            def preflight(_args):
                interrupt_selector(sig)
                continued()
                return {**smoke.report(), "result": "pass"}
            output = io.StringIO()
            before = {sig: signal.getsignal(sig) for sig in SIGS}
            with self.subTest(sig=sig), mock.patch.object(smoke.QuietParser, "parse_args", return_value=None), \
                    mock.patch.object(smoke, "preflight", side_effect=preflight), redirect_stdout(output):
                self.assertEqual(smoke.main([]), 1)
            self.assertEqual(json.loads(output.getvalue())["result"], "fail")
            continued.assert_not_called()
            self.assertEqual(before, {sig: signal.getsignal(sig) for sig in SIGS})

    def test_recovery_cannot_issue_another_command_after_cancellation(self):
        recovery = recovery_tests.recovery
        for sig in SIGS:
            mutation = mock.Mock()
            def execute(*_):
                interrupt_selector(sig)
                mutation()
            output = io.StringIO()
            before = {sig: signal.getsignal(sig) for sig in SIGS}
            with self.subTest(sig=sig), mock.patch.object(recovery, "execute", side_effect=execute), redirect_stdout(output):
                self.assertEqual(recovery.main(["--cleanup-only", "--state-dir", "/synthetic-state",
                    "--hard-stop-at", "2026-10-25T06:15:00Z"]), 1)
            self.assertFalse(json.loads(output.getvalue())["cleanup_confirmed"])
            mutation.assert_not_called()
            self.assertEqual(before, {sig: signal.getsignal(sig) for sig in SIGS})

    def test_lab_real_selector_stop_enters_cleanup_and_never_functional_pass(self):
        lab = lab_tests.host
        for sig in SIGS:
            identifier, token = "a" * 64, "b" * 32
            initial = {"Id": identifier, "State": {"Status": "created"}}
            final = {"Id": identifier, "Config": {"Labels": {lab.LABEL: token}},
                "State": {"Status": "exited", "ExitCode": 0, "OOMKilled": False}}
            continued = mock.Mock()
            def call(command, *_args, **_kwargs):
                if "start" in command:
                    interrupt_selector(sig)
                    continued()
                    return b'{"functional_pass":true}'
                return identifier.encode() if "create" in command else b""
            recorder = types.SimpleNamespace(image_binding=lambda *_: {}, call=call)
            monitor = mock.Mock(returncode=0)
            monitor.poll.return_value = None
            with self.subTest(sig=sig), ExitStack() as stack:
                stack.enter_context(mock.patch.object(lab, "CANCELLED", False))
                stack.enter_context(mock.patch.object(lab, "CLEANING", False))
                stack.enter_context(mock.patch.object(lab.uuid, "uuid4", return_value=types.SimpleNamespace(hex=token)))
                stack.enter_context(mock.patch.object(lab, "inspect", side_effect=[{"Id": "image"}, initial, final]))
                stack.enter_context(mock.patch.object(lab, "check_container"))
                stack.enter_context(mock.patch.object(lab, "create_command", return_value=["docker", "create"]))
                stack.enter_context(mock.patch.object(lab.subprocess, "Popen", return_value=monitor))
                cleanup = stack.enter_context(mock.patch.object(lab, "cleanup", side_effect=lambda *_: lab.cancel()))
                stack.enter_context(handlers(lab.cancel))
                row = lab.run_container("/unused", "a" * 64, "/unused", time.time() + 60,
                    recorder, {"base_image": "fixed"}, {}, lab.controlled_env("unix:///var/run/docker.sock"))
                self.assertTrue(lab.CANCELLED)
            cleanup.assert_called_once()
            continued.assert_not_called()
            monitor.wait.assert_called_once()
            self.assertFalse(row["functional_pass"])
            self.assertEqual((row["failure"], row["cleanup"]), ("bounded-lab-stage-failed", "confirmed"))

    def test_cache_restore_and_profile_record_cancellation_not_success(self):
        for sig in SIGS:
            for profile in (False, True):
                with self.subTest(sig=sig, profile=profile), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp).resolve()
                    module = profile_tests.cache if profile else cache_tests.cache
                    fixture = profile_tests.ProfileCacheTests() if profile else cache_tests.CacheTests()
                    if profile:
                        fixture.setUp()
                        self.addCleanup(fixture.doCleanups)
                        args, call, receipt = fixture.arguments(), module.profile_report, fixture.root / "receipt.json"
                        packet = fixture.worker_result()
                    else:
                        args, call, receipt = fixture.restore_args(root), module.restore_report, root / "receipt.json"
                        packet = fixture.successful_worker()
                    def capture(*_args, **_kwargs):
                        interrupt_selector(sig)
                        return packet
                    with mock.patch.object(module, "load_capture", return_value=capture):
                        self.assertEqual(call(*args), 1)
                    row = json.loads(receipt.read_bytes())
                    self.assertEqual((row["result"], row["failure"]), ("fail", "cancelled"))
                    self.assertFalse(row["worker_exit_confirmed"])
                    self.assertEqual(row["verified_bytes"], 0)

    def test_weight_view_no_success_publication_after_blocked_selector_stop(self):
        view, cache = view_tests.view, view_tests.cache
        for sig in SIGS:
            fixture = view_tests.WeightViewTests()
            self.addCleanup(fixture.doCleanups)
            source, destination, _ = fixture.fixture()
            output = source.parent / "report.json"
            _, profile = cache.profiles()
            packet = json.dumps({"schema": "nib.selected-weight-view-worker/v1", "files": 79,
                "verified_bytes": profile["total_bytes"], "file_mode": "0444", "directory_mode": "0555",
                "relative_path": "model"}).encode()
            def capture(*_args, **_kwargs):
                interrupt_selector(sig)
                return packet
            uid = os.getuid()
            flags = types.SimpleNamespace(**({name: getattr(view.sys.flags, name) for name in dir(view.sys.flags)
                if type(getattr(view.sys.flags, name)) is int} | {"isolated": True, "no_site": True}))
            with self.subTest(sig=sig), mock.patch.object(view, "cache_helper", return_value=cache), \
                    mock.patch.object(cache, "load_capture", return_value=capture), \
                    mock.patch.object(view.sys, "platform", "linux"), \
                    mock.patch.object(view.sys, "flags", flags), \
                    mock.patch.object(view.sys, "dont_write_bytecode", True), \
                    mock.patch.object(view.os, "getuid", side_effect=[0] + [uid] * 50):
                self.assertEqual(view.main(["--source", str(source), "--destination", str(destination),
                    "--output", str(output), "--producer", "a" * 40, "--timeout", "120"]), 1)
            row = json.loads(output.read_bytes())
            self.assertEqual((row["result"], row["publication"]), ("fail", "unproven"))
            self.assertFalse(row["worker_exit_confirmed"])

    def test_image_handler_escapes_selector_and_is_idempotent(self):
        recorder = image_tests.recorder
        for sig in SIGS:
            with self.subTest(sig=sig), mock.patch.object(recorder, "CANCELLED", False), handlers(recorder.cancel):
                with self.assertRaises(recorder.Cancelled):
                    interrupt_selector(sig)
                recorder.cancel(None, None)
                self.assertTrue(recorder.CANCELLED)

    def test_image_cancellation_preserves_failed_envelope_and_owned_cleanup(self):
        recorder = image_tests.recorder
        image_tests.ImageTests.setUpClass()
        for sig in SIGS:
            fixture = image_tests.ImageTests()
            fixture.setUp()
            self.addCleanup(fixture.doCleanups)
            fake = image_tests.FakeDocker(fixture.image, fixture.collect())
            def execute(command, **kwargs):
                if command[1:3] == ["container", "start"]:
                    interrupt_selector(sig)
                return fake(command, **kwargs)
            with self.subTest(sig=sig), mock.patch.object(recorder, "CANCELLED", False), handlers(recorder.cancel):
                row = recorder.run_container(fixture.payload, fixture.contract, fixture.config, {}, execute)
            self.assertEqual(row["result"], "fail")
            self.assertTrue(fake.removed)
            self.assertEqual(row["cleanup"], "completion-unknown")
            self.assertEqual(row["failure"], "diagnostic-stage-failed")

    def test_exact_source_closure_and_ordinary_eintr_retry_remain(self):
        builder = lab_tests.host.load("cancel_pinned_builder", lab_tests.ROOT / lab_tests.host.BUILDER,
                                      lab_tests.host.BUILDER_SHA)
        self.assertEqual(builder.RECORDER_SHA, hashlib.sha256(Path(image_tests.recorder.__file__).read_bytes()).hexdigest())
        self.assertEqual(view_tests.view.CACHE_SHA256, hashlib.sha256(Path(cache_tests.cache.__file__).read_bytes()).hexdigest())
        self.assertEqual(smoke_tests.smoke.CACHE_SHA, view_tests.view.CACHE_SHA256)
        self.assertEqual(import_tests.tool.BUILDER_SHA, lab_tests.host.BUILDER_SHA)
        self.assertEqual(entry_tests.tool.BASE_SHA,
                         hashlib.sha256(Path(import_tests.tool.__file__).read_bytes()).hexdigest())
        with mock.patch.object(lab_tests.snapshot.os, "waitpid", side_effect=[InterruptedError(), (0, 0)]) as wait:
            lab_tests.snapshot.reap_group_children(123)
        self.assertEqual(wait.call_count, 2)

    def test_shared_recorder_consumers_preserve_cancellation_envelopes(self):
        for tests, cls, pass_field in ((import_tests, import_tests.SmokeTests, "smoke_pass"),
                                      (entry_tests, entry_tests.ContractTests, "entry_pass")):
            cls.setUpClass()
            recorder = tests.tool.recorder
            for sig in SIGS:
                fixture = cls()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                original = recorder.call
                def call(command, *args, **kwargs):
                    if command[1:3] == ["container", "start"]:
                        interrupt_selector(sig)
                    return original(command, *args, **kwargs)
                with self.subTest(consumer=pass_field, sig=sig), \
                        mock.patch.object(recorder, "CANCELLED", False), \
                        mock.patch.object(recorder, "call", side_effect=call), handlers(recorder.cancel):
                    row, _commands, state = fixture.daemon(fixture.valid_report())
                self.assertEqual(row["result"], "fail")
                self.assertFalse(row[pass_field])
                self.assertEqual(row["cleanup"], "completion-unknown")
                self.assertFalse(state["present"])


if __name__ == "__main__":
    unittest.main()
