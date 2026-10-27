# SPDX-License-Identifier: Apache-2.0
"""Fake-clock scheduling and synthetic sockets only; never contact a serving peer."""

from contextlib import ExitStack, contextmanager, redirect_stdout
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import signal
import tempfile
import types
import unittest
from unittest import mock

import test_selected_external_smoke as external

SPEC = importlib.util.spec_from_file_location("ordinary_soak", Path(__file__).with_name("soak-selected-service.py"))
soak = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(soak)
PRIVATE = "PRIVATE_SOAK_CANARY"
REVISION = "b" * 40
HASHES = {name: "a" * 64 for name in (*soak.probe.SOURCES, soak.SCRIPT)}


class Clock:
    def __init__(self):
        self.mono, self.wall = 1000.0, 1800000000.0
        self.sleeps, self.alarms = [], []
        self.on_sleep = None

    def advance(self, seconds):
        self.mono += seconds
        self.wall += seconds

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.advance(seconds)
        if self.on_sleep:
            self.on_sleep(self)


def arguments(directory, clock, **changes):
    values = dict(run=True, address=external.args().address, port=8443, hostname="selected.test",
        tls_mode="system-roots", certificate=None, certificate_sha256=None,
        api_key_file="/private/" + PRIVATE, output_directory=str(directory / "observations"),
        deadline=datetime.fromtimestamp(clock.wall + 86460, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        duration_seconds=900, interval_seconds=300, max_samples=3)
    return types.SimpleNamespace(**{**values, **changes})


def observation(*, failure=None, unknown=False):
    row = soak.probe.empty_result()
    row.update(schema="nib.selected-external-smoke/v2", tls_mode="system-roots",
               result="pass" if failure is None else "fail", failure=failure or "none",
               git_sha=REVISION, source_sha256={name: HASHES[name] for name in soak.probe.SOURCES},
               started_at="2027-01-15T08:00:00+00:00", ended_at="2027-01-15T08:00:01+00:00")
    if failure is None or unknown or failure == "completion":
        for name in ("attempts", "tls_checks", "checks"):
            row[name] = dict.fromkeys(soak.probe.CASES, True)
        row["tls_peer_verified"] = True
        row["usage"] = None if unknown else {"prompt_tokens": 12, "completion_tokens": 3}
        row["usage_unknown"] = unknown
        if failure:
            row["checks"]["stream"] = False
    elif failure == "not-ready":
        row["attempts"]["health"] = row["tls_checks"]["health"] = True
    return row


class SoakTests(unittest.TestCase):
    @contextmanager
    def fixture(self, samples=None):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            # Resolve the system temporary symlink; production rejects symlink ancestors.
            directory = Path(directory).resolve()
            clock = Clock()
            calls = []
            rows = iter(samples) if samples is not None else None

            def source(_deadline, *, extra_files=()):
                return REVISION, {name: HASHES[name] for name in (*soak.probe.SOURCES, *extra_files)}

            def sample(args):
                calls.append((clock.mono, args.deadline))
                clock.advance(2)
                item = next(rows) if rows else observation()
                if isinstance(item, BaseException):
                    raise item
                return item

            @contextmanager
            def alarm(deadline):
                clock.alarms.append(deadline)
                if clock.mono >= deadline:
                    raise soak.worker.Abort("timeout")
                yield
                if clock.mono >= deadline:
                    raise soak.worker.Abort("timeout")

            stack.enter_context(mock.patch.object(soak.time, "monotonic", side_effect=lambda: clock.mono))
            stack.enter_context(mock.patch.object(soak.time, "time", side_effect=lambda: clock.wall))
            stack.enter_context(mock.patch.object(soak.time, "sleep", side_effect=clock.sleep))
            stack.enter_context(mock.patch.object(soak.worker, "alarm", side_effect=alarm))
            stack.enter_context(mock.patch.object(soak.probe, "source_identity", side_effect=source))
            run = stack.enter_context(mock.patch.object(soak.probe, "run", side_effect=sample))
            stack.enter_context(mock.patch.object(soak.probe.socket, "socket", side_effect=AssertionError("network forbidden")))
            stack.enter_context(mock.patch.object(soak.probe.service.identity, "private_read",
                                                 side_effect=AssertionError("credential read forbidden")))
            yield directory, clock, calls, run

    def receipts(self, args):
        return {path.name: json.loads(path.read_bytes()) for path in Path(args.output_directory).iterdir()}

    def assert_chain(self, args):
        rows = self.receipts(args)
        previous = None
        for ordinal, (name, row) in enumerate(sorted(rows.items(), key=lambda item: item[1]["ordinal"])):
            self.assertEqual(row["ordinal"], ordinal)
            self.assertEqual(row["previous_sha256"], previous)
            raw = (Path(args.output_directory) / name).read_bytes()
            previous = hashlib.sha256(raw).hexdigest()
            for private in (PRIVATE, args.address, args.hostname, "Bearer ", "short greeting"):
                self.assertNotIn(private.encode(), raw)
            self.assertLess(len(raw), soak.RECEIPT_LIMIT)
            self.assertEqual((Path(args.output_directory) / name).stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(args.output_directory).stat().st_mode & 0o777, 0o700)
        return rows, previous

    def test_full_day_is_288_serial_samples_and_1152_maximum_requests(self):
        with self.fixture() as (directory, clock, calls, _):
            args = arguments(directory, clock, duration_seconds=86400, max_samples=288)
            result = soak.run(args)
            self.assertEqual((result["result"], result["failure"]), ("pass", "none"), result)
            self.assertEqual(len(calls), 288)
            self.assertTrue(all(b[0] - a[0] >= 300 for a, b in zip(calls, calls[1:])))
            self.assertEqual(result["elapsed_seconds"], 86400)
            self.assertTrue(result["window_completed"])
            self.assertEqual(result["http_requests_attempted"], 1152)
            self.assertEqual(result["model_posts_attempted"], 864)
            self.assertEqual(result["authenticated_requests_attempted"], 288)
            self.assertEqual(result["known_completion_tokens"], 288 * 3)
            self.assertTrue(all(result[name] is False for name in soak.CLAIMS))
            rows, last = self.assert_chain(args)
            self.assertEqual(len(rows), 578)
            self.assertEqual(result["terminal_sha256"], last)
            self.assertEqual(rows["run.json"]["payload"]["max_http_requests"], 1152)
            self.assertEqual(rows["run.json"]["payload"]["max_requested_output_tokens_all_posts"], 221184)

    def test_failed_sample_is_retained_and_never_retried_or_turned_into_pass(self):
        for failed in (observation(failure="not-ready"), observation(failure="completion")):
            with self.fixture([failed, observation(), observation()]) as (directory, clock, calls, _):
                args = arguments(directory, clock)
                result = soak.run(args)
                self.assertEqual((result["result"], result["failure"]), ("fail", "failed-samples"))
                self.assertEqual(result["samples_failed"], 1)
                self.assertEqual([call[0] for call in calls], [1000, 1300, 1600])
                rows, _ = self.assert_chain(args)
                self.assertEqual(rows["0000-sample.json"]["payload"]["probe"], failed)
                self.assertEqual(result["retries"], 0)

    def test_unknown_spend_stops_without_retry_and_preserves_failed_receipt(self):
        with self.fixture([observation(failure="transport", unknown=True)]) as (directory, clock, calls, _):
            args = arguments(directory, clock)
            result = soak.run(args)
            self.assertEqual(result["failure"], "unknown-usage")
            self.assertEqual(result["unknown_usage_samples"], 1)
            self.assertEqual(result["authenticated_requests_attempted"], 1)
            self.assertEqual(len(calls), 1)
            rows, _ = self.assert_chain(args)
            self.assertTrue(rows["0000-sample.json"]["payload"]["probe"]["usage_unknown"])

    def test_zero_token_failed_completion_is_preserved_not_discarded(self):
        failed = observation(failure="completion")
        failed["usage"] = {"prompt_tokens": 0, "completion_tokens": 0}
        with self.fixture([failed, observation(), observation()]) as (directory, clock, calls, _):
            args = arguments(directory, clock)
            result = soak.run(args)
            self.assertEqual(result["failure"], "failed-samples")
            self.assertEqual(len(calls), 3)
            self.assertEqual(result["known_prompt_tokens"], 24)
            self.assertEqual(result["unknown_usage_samples"], 0)
            rows, _ = self.assert_chain(args)
            self.assertEqual(rows["0000-sample.json"]["payload"]["probe"], failed)

    def test_identity_authentication_protocol_and_cancelled_are_terminal(self):
        for failure in ("identity", "authentication", "protocol", "cancelled", "deadline"):
            with self.fixture([observation(failure=failure)]) as (directory, clock, calls, _):
                result = soak.run(arguments(directory, clock))
                self.assertEqual(result["failure"], failure)
                self.assertEqual(len(calls), 1)
                self.assertFalse(result["window_completed"])

    def test_original_monotonic_limit_survives_wall_rollback(self):
        with self.fixture() as (directory, clock, calls, _):
            def rollback(current):
                current.wall -= 10000
                current.on_sleep = None
            clock.on_sleep = rollback
            result = soak.run(arguments(directory, clock))
            self.assertEqual(result["result"], "pass", result)
            self.assertEqual(result["elapsed_seconds"], 900)
            self.assertEqual(len(calls), 3)
            self.assertLessEqual(max(clock.alarms), 1905)

    def test_forward_jump_shortens_irreversibly_and_cannot_claim_full_window(self):
        clock = Clock()
        with mock.patch.object(soak.time, "monotonic", side_effect=lambda: clock.mono), \
                mock.patch.object(soak.time, "time", side_effect=lambda: clock.wall):
            window = soak.Window(900, clock.wall + 900)
            clock.wall += 500
            self.assertEqual(window.cutoff(), 1400)
            clock.wall -= 1000
            self.assertEqual(window.cutoff(), 1400)
        with self.fixture() as (directory, clock, calls, _):
            def forward(current):
                current.wall += 1000
                current.on_sleep = None
            clock.on_sleep = forward
            result = soak.run(arguments(directory, clock))
            self.assertEqual(result["failure"], "deadline")
            self.assertEqual(len(calls), 1)
            self.assertFalse(result["window_completed"])

    def test_invalid_budgets_deadline_and_tls_fail_before_samples(self):
        changes = ({"run": False}, {"duration_seconds": 86401}, {"duration_seconds": 299},
            {"interval_seconds": 0}, {"interval_seconds": 299}, {"interval_seconds": 3601},
            {"max_samples": 289}, {"max_samples": 2}, {"max_samples": True},
            {"deadline": "2000-01-01T00:00:00Z"}, {"tls_mode": "insecure"},
            {"certificate": "/private/" + PRIVATE})
        for change in changes:
            with self.subTest(change=change), self.fixture() as (directory, clock, calls, _):
                args = arguments(directory, clock, **change)
                result = soak.run(args)
                self.assertEqual(result["result"], "fail")
                self.assertFalse(calls)
                self.assertFalse(Path(args.output_directory).exists())
                self.assertNotIn(PRIVATE, soak.encoded(result).decode())

    def test_source_change_before_next_sample_stops_without_network(self):
        with self.fixture() as (directory, clock, calls, _):
            original = soak.probe.source_identity.side_effect
            count = 0
            def changed(*args, **kwargs):
                nonlocal count
                count += 1
                revision, hashes = original(*args, **kwargs)
                return ("c" * 40 if count > 2 else revision), hashes
            soak.probe.source_identity.side_effect = changed
            result = soak.run(arguments(directory, clock))
            self.assertEqual(result["failure"], "identity")
            self.assertEqual(len(calls), 1)

    def test_interrupted_probe_preserves_intent_unknown_budget_and_terminal(self):
        for exception in (KeyboardInterrupt(), soak.worker.Abort("timeout"), RuntimeError(PRIVATE)):
            with self.fixture([exception]) as (directory, clock, calls, _):
                args = arguments(directory, clock)
                result = soak.run(args)
                self.assertEqual(result["result"], "fail")
                self.assertEqual(result["potential_unrecorded_authenticated_requests"], 1)
                self.assertEqual(result["potential_unrecorded_http_requests"], 4)
                self.assertFalse(result["receipts_complete"])
                self.assertFalse(result["client_operations_returned"])
                self.assertEqual(len(calls), 1)
                rows, _ = self.assert_chain(args)
                self.assertEqual(set(rows), {"run.json", "0000-intent.json", "terminal.json"})

    def test_sigterm_while_idle_restores_handlers_and_retains_failure(self):
        previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
        with self.fixture() as (directory, clock, calls, _):
            clock.on_sleep = lambda _: signal.raise_signal(signal.SIGTERM)
            args = arguments(directory, clock)
            result = soak.run(args)
            self.assertEqual(result["failure"], "cancelled")
            self.assertEqual(len(calls), 1)
            self.assertTrue(result["client_operations_returned"])
            self.assert_chain(args)
        self.assertEqual(previous, {number: signal.getsignal(number) for number in previous})

    def test_exclusive_receipts_and_no_resume_or_symlink_traversal(self):
        with self.fixture() as (directory, clock, calls, _):
            args = arguments(directory, clock)
            self.assertEqual(soak.run(args)["result"], "pass")
            originals = {p.name: p.read_bytes() for p in Path(args.output_directory).iterdir()}
            self.assertEqual(soak.run(args)["result"], "fail")
            self.assertEqual(len(calls), 3)
            self.assertEqual(originals, {p.name: p.read_bytes() for p in Path(args.output_directory).iterdir()})
            (directory / "alias").symlink_to(directory, target_is_directory=True)
            with self.assertRaises(OSError):
                soak.Receipts(directory / "alias" / "escape")
            with self.assertRaises(soak.inference.Rejected):
                soak.Receipts(directory / ".." / "escape")

    def test_disk_failure_before_intent_prevents_probe(self):
        with self.fixture() as (directory, clock, calls, _):
            original = soak.Receipts.write
            def fail_intent(journal, name, payload):
                if name.endswith("intent.json"):
                    raise OSError(PRIVATE)
                return original(journal, name, payload)
            with mock.patch.object(soak.Receipts, "write", new=fail_intent):
                result = soak.run(arguments(directory, clock))
            self.assertEqual(result["result"], "fail")
            self.assertFalse(calls)

    def test_failed_sample_write_preserves_pending_intent_and_stops(self):
        with self.fixture() as (directory, clock, calls, _):
            original = soak.Receipts.write
            def fail_sample(journal, name, payload):
                if name.endswith("sample.json"):
                    raise OSError(PRIVATE)
                return original(journal, name, payload)
            with mock.patch.object(soak.Receipts, "write", new=fail_sample):
                result = soak.run(arguments(directory, clock))
            self.assertEqual(result["result"], "fail")
            self.assertEqual(len(calls), 1)
            self.assertEqual(result["potential_unrecorded_authenticated_requests"], 1)

    def test_cli_does_not_echo_private_arguments_and_requires_run(self):
        for argv in (["--unknown", PRIVATE], ["--max-samples", "bad-" + PRIVATE]):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(soak.main(argv), 1)
            self.assertNotIn(PRIVATE, output.getvalue())

    def test_actual_probe_helpers_tls_key_usage_and_cleanup_are_reused(self):
        # Use the real probe path with synthetic byte streams, never a network socket.
        with self.fixture() as (directory, clock, calls, _):
            denied = soak.encoded({"error": {"message": "invalid_api_key", "type": "invalid_api_key"}})
            responses = [b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n",
                         external.response(401, denied), external.response(401, denied), external.response()]
            opened, closed = [], []
            @contextmanager
            def tls(*_):
                sock = external.Socket(responses[len(opened)])
                opened.append(sock)
                try:
                    yield sock
                finally:
                    closed.append(sock)
            def read(*_):
                self.assertEqual(len(opened), 4)
                return external.KEY
            spec = importlib.util.spec_from_file_location("soak_real_probe", soak.ROOT / "scripts/probe-selected-service.py")
            real = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(real)
            with mock.patch.object(real, "source_identity", return_value=(REVISION, {n: HASHES[n] for n in real.SOURCES})), \
                    mock.patch.object(real, "trust_input", return_value=(object(), None)), \
                    mock.patch.object(real, "pinned_tls", side_effect=tls), \
                    mock.patch.object(real.service.identity, "private_read", side_effect=read), \
                    mock.patch.object(soak.probe, "run", side_effect=real.run):
                result = soak.run(arguments(directory, clock, duration_seconds=300, max_samples=1))
            self.assertEqual(result["result"], "pass", result)
            self.assertEqual(opened, closed)
            self.assertEqual(sum(len(sock.sent) for sock in opened), 4)
            self.assertEqual(sum(external.KEY in sock.sent[0] for sock in opened), 1)
            self.assertEqual(result["known_completion_tokens"], 3)
            self.assertFalse(calls)

    def test_deadline_overrun_preserves_uncertain_intent_and_never_starts_next_sample(self):
        with self.fixture() as (directory, clock, calls, run):
            def overrun(_args):
                calls.append(clock.mono)
                clock.advance(151)
                raise soak.worker.Abort("timeout")
            run.side_effect = overrun
            args = arguments(directory, clock)
            result = soak.run(args)
            self.assertEqual(result["failure"], "deadline")
            self.assertEqual(len(calls), 1)
            self.assertEqual(result["potential_unrecorded_authenticated_requests"], 1)
            self.assert_chain(args)

    def test_terminal_write_failure_cannot_report_pass(self):
        with self.fixture() as (directory, clock, calls, _):
            original = soak.Receipts.write
            def fail_terminal(journal, name, payload):
                if name == "terminal.json":
                    raise OSError(PRIVATE)
                return original(journal, name, payload)
            args = arguments(directory, clock)
            with mock.patch.object(soak.Receipts, "write", new=fail_terminal):
                result = soak.run(args)
            self.assertEqual(result["failure"], "receipt-write")
            self.assertEqual(result["result"], "fail")
            self.assertFalse(result["receipts_complete"])
            self.assertEqual(len(calls), 3)
            self.assertNotIn("terminal.json", self.receipts(args))

    def test_inherited_timer_is_not_replaced(self):
        with self.fixture() as (directory, clock, calls, _):
            with mock.patch.object(soak.signal, "getitimer", return_value=(10, 0)):
                result = soak.run(arguments(directory, clock))
            self.assertEqual(result["failure"], "contract")
            self.assertFalse(calls)

    def test_extra_untrusted_receipt_fields_are_not_retained(self):
        row = observation()
        row["response_body"] = PRIVATE
        with self.fixture([row]) as (directory, clock, calls, _):
            args = arguments(directory, clock)
            result = soak.run(args)
            self.assertEqual(result["failure"], "protocol")
            self.assertEqual(len(calls), 1)
            self.assertEqual(result["potential_unrecorded_authenticated_requests"], 1)
            self.assert_chain(args)


if __name__ == "__main__":
    unittest.main()
