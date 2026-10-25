# SPDX-License-Identifier: Apache-2.0
"""Deterministic clocks and socket bytes only; no endpoint, key or model calls."""

from contextlib import ExitStack, contextmanager, nullcontext, redirect_stdout
from datetime import datetime, timezone
import errno
import hashlib
import importlib.util
import io
from pathlib import Path
import signal
import ssl
import subprocess
import sys
import textwrap
import unittest
from unittest import mock

import test_selected_external_smoke as external

SPEC = importlib.util.spec_from_file_location("selected_health_wait", Path(__file__).with_name("wait-selected-service.py"))
waiter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(waiter)
CANARY = "PRIVATE_HEALTH_CANARY"
GOOD = b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
DOWN = GOOD.replace(b"200 OK", b"503 Down")


class Clock:
    def __init__(self):
        self.wall, self.mono, self.sleeps = 1767225600.0, 1000.0, []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.wall += seconds
        self.mono += seconds

    def args(self, seconds=1199, **changes):
        deadline = datetime.fromtimestamp(self.wall + seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        value = external.args(deadline=deadline, **changes)
        del value.api_key_file
        return value


class Socket(external.Socket):
    def __init__(self, raw):
        super().__init__(raw)
        self.readers = []

    def makefile(self, *_):
        reader = super().makefile()
        self.readers.append(reader)
        return reader


class WaitTests(unittest.TestCase):
    @contextmanager
    def fixture(self, outcomes=(GOOD,), *, close_error=None):
        clock, sockets, connects, closed = Clock(), [], [], []
        @contextmanager
        def tls(args, trust, digest, deadline):
            index = len(connects)
            connects.append((args, deadline))
            outcome = outcomes[min(index, len(outcomes) - 1)]
            if callable(outcome):
                outcome = outcome(clock)
            if isinstance(outcome, BaseException):
                raise outcome
            sock = Socket(outcome)
            sockets.append(sock)
            try:
                yield sock
            finally:
                closed.append(sock)
                if close_error:
                    raise close_error
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(waiter.time, "time", side_effect=lambda: clock.wall))
            stack.enter_context(mock.patch.object(waiter.time, "monotonic", side_effect=lambda: clock.mono))
            stack.enter_context(mock.patch.object(waiter.time, "sleep", side_effect=clock.sleep))
            stack.enter_context(mock.patch.object(waiter.service.identity.worker, "alarm", side_effect=lambda *_: nullcontext()))
            stack.enter_context(mock.patch.object(waiter, "source_identity", return_value=("b" * 40, {"test": "c" * 64})))
            stack.enter_context(mock.patch.object(waiter.probe, "trust_input", return_value=(object(), b"d" * 32)))
            stack.enter_context(mock.patch.object(waiter.probe, "pinned_tls", tls))
            private = stack.enter_context(mock.patch.object(waiter.service.identity, "private_read",
                side_effect=AssertionError("unexpected credential read")))
            model = stack.enter_context(mock.patch.object(waiter.probe, "request",
                side_effect=AssertionError("unexpected inference")))
            yield clock, sockets, connects, closed
            private.assert_not_called()
            model.assert_not_called()
        self.assertEqual(sockets, closed)
        self.assertTrue(all(reader.closed for sock in sockets for reader in sock.readers))

    def test_first_health_success_has_no_credentials_model_calls_or_ownership(self):
        with self.fixture() as (clock, sockets, connects, _):
            args = clock.args()
            report = waiter.run(args)
            self.assertEqual(report["result"], "pass", report)
            self.assertEqual(report["attempts"], 1)
            self.assertEqual(report["tls_checks"], 1)
            self.assertEqual(report["health_requests"], 1)
            self.assertEqual(report["health_retries"], 0)
            self.assertEqual(report["model_requests"], 0)
            self.assertEqual(clock.sleeps, [])
            self.assertEqual(connects, [(args, 1005)])
            self.assertTrue(report["health_ready"])
            self.assertEqual(sockets[0].sent, [b"GET /healthz HTTP/1.1\r\nHost: selected.test:8443\r\n"
                b"Connection: close\r\nAccept-Encoding: identity\r\n\r\n"])
            for field in ("credential_accessed", "ownership_verified", "continued_readiness",
                          "provider_conformance", "quality_eligible", "deployment_eligible", "confidential", "e2ee"):
                self.assertIs(report[field], False)
            self.assertEqual(report["target_sha256"], hashlib.sha256(waiter.encoded({
                "address": args.address, "port": args.port, "hostname": args.hostname,
                "certificate_sha256": args.certificate_sha256})).hexdigest())
            text = waiter.encoded(report).decode()
            for private in (args.address, args.hostname, args.certificate, CANARY, "Authorization"):
                self.assertNotIn(private, text)

    def test_refusal_then_unready_then_strict_success_has_bounded_backoff(self):
        with self.fixture((ConnectionRefusedError(errno.ECONNREFUSED, CANARY), DOWN, GOOD)) as fixture:
            clock, sockets, connects, _ = fixture
            report = waiter.run(clock.args())
            self.assertEqual(report["result"], "pass", report)
            self.assertEqual(report["attempts"], 3)
            self.assertEqual(report["transport_failures"], 1)
            self.assertEqual(report["health_rejections"], 1)
            self.assertEqual(report["health_retries"], 2)
            self.assertEqual(report["tls_checks"], 2)
            self.assertEqual(report["health_requests"], 2)
            self.assertEqual(sum(clock.sleeps), 3)
            self.assertEqual([deadline for _, deadline in connects], [1005, 1006, 1008])
            self.assertEqual(sum(len(sock.sent) for sock in sockets), 2)

    def test_permanent_tls_pin_or_certificate_errors_never_retry(self):
        for error, category in ((ssl.SSLCertVerificationError(CANARY), "identity"),
                      (ssl.CertificateError(CANARY), "identity"), (ssl.SSLError(CANARY), "transport"),
                      (waiter.inference.Rejected("identity"), "identity")):
            with self.subTest(error=type(error).__name__), self.fixture((error, GOOD)) as fixture:
                clock, _, connects, _ = fixture
                report = waiter.run(clock.args())
                self.assertEqual(report["failure"], category, report)
                self.assertEqual(len(connects), 1)
                self.assertEqual(report["health_requests"], 0)
                self.assertFalse(report["health_ready"])
                self.assertEqual(clock.sleeps, [])
                self.assertNotIn(CANARY, waiter.encoded(report).decode())

    def test_malformed_health_is_never_accepted_or_redirected(self):
        malformed = (GOOD + b"unexpected", GOOD.replace(b"200 OK", b"302 Found"),
            GOOD.replace(b"Content-Length: 0", b"Content-Length: 0\r\nContent-Length: 0"),
            GOOD.replace(b"Content-Length: 0", b"Content-Length: 0\r\nContent-Encoding: gzip"),
            GOOD.replace(b"Content-Length: 0", b"Transfer-Encoding: chunked") + b"0\r\n\r\n")
        for raw in malformed:
            with self.subTest(raw=raw), self.fixture((raw, GOOD)) as fixture:
                clock, sockets, connects, _ = fixture
                report = waiter.run(clock.args())
                self.assertEqual(report["result"], "pass", report)
                self.assertEqual(report["health_rejections"], 1)
                self.assertEqual(report["attempts"], 2)
                self.assertIs(connects[0][0], connects[1][0])
                self.assertTrue(all(sock.sent[0].startswith(b"GET /healthz HTTP/1.1\r\n") for sock in sockets))

    def test_global_deadline_and_attempt_count_are_independent_caps(self):
        with self.fixture((DOWN,)) as (clock, _, connects, _):
            report = waiter.run(clock.args(seconds=4))
            self.assertEqual(report["failure"], "deadline", report)
            self.assertEqual(report["attempts"], 3)
            self.assertEqual(sum(clock.sleeps), 4)
            self.assertTrue(all(deadline <= 1004 for _, deadline in connects))
        with self.fixture((DOWN,)) as (clock, _, connects, _), \
                mock.patch.object(waiter.time, "sleep"):
            # Simulate immediate backoffs, not a weakened production deadline.
            with mock.patch.object(waiter, "BACKOFF", (0,)):
                report = waiter.run(clock.args())
            self.assertEqual(report["failure"], "attempt-limit", report)
            self.assertEqual(len(connects), 128)
            self.assertEqual(report["health_retries"], 127)

    def test_backoff_is_capped_and_no_attempt_or_sleep_renews_cutoff(self):
        with self.fixture((DOWN,) * 6 + (GOOD,)) as (clock, _, connects, _):
            report = waiter.run(clock.args())
            self.assertEqual(report["result"], "pass", report)
            self.assertEqual([deadline - 5 for _, deadline in connects], [1000, 1001, 1003, 1007, 1015, 1025, 1035])
            self.assertEqual(sum(clock.sleeps), 35)
            self.assertTrue(all(0 < seconds <= 1 for seconds in clock.sleeps))

    def test_timeouts_release_socket_before_retry_and_original_deadline_wins(self):
        for error in (TimeoutError(CANARY), waiter.service.identity.worker.Abort("timeout")):
            with self.subTest(error=type(error).__name__), self.fixture() as fixture:
                clock, sockets, _, closed = fixture
                real_health = waiter.probe.health
                def health(sock, args, deadline, report):
                    if len(sockets) == 1:
                        report["attempts"]["health"] = True
                        clock.sleep(5)
                        raise error
                    self.assertEqual(closed, sockets[:1])
                    return real_health(sock, args, deadline, report)
                with mock.patch.object(waiter.probe, "health", side_effect=health):
                    report = waiter.run(clock.args())
                self.assertEqual(report["result"], "pass", report)
                self.assertEqual(report["attempt_timeouts"], 1)
                self.assertEqual(report["attempts"], 2)
        def expire(clock):
            clock.sleep(5)
            return TimeoutError(CANARY)
        with self.fixture((expire, GOOD)) as (clock, _, connects, _):
            report = waiter.run(clock.args(seconds=4))
            self.assertEqual(report["failure"], "deadline", report)
            self.assertEqual(len(connects), 1)

    def test_wall_jumps_never_extend_and_forward_jump_fails_before_pass(self):
        def forward(clock):
            clock.wall += 2000
            return GOOD
        with self.fixture((forward,)) as (clock, _, connects, _):
            report = waiter.run(clock.args())
            self.assertEqual(report["failure"], "deadline", report)
            self.assertFalse(report["health_ready"])
            self.assertEqual(len(connects), 1)
        def backward(clock):
            clock.wall -= 2000
            return DOWN
        with self.fixture((backward,)) as (clock, _, connects, _):
            report = waiter.run(clock.args(seconds=4))
            self.assertEqual(report["failure"], "deadline", report)
            self.assertTrue(all(deadline <= 1004 for _, deadline in connects))
        def shorten(clock):
            clock.wall += 10
            return DOWN
        with self.fixture((shorten, backward)) as (clock, _, connects, _):
            report = waiter.run(clock.args(seconds=14))
            self.assertEqual(report["failure"], "deadline", report)
            self.assertTrue(all(deadline <= 1004 for _, deadline in connects[1:]))

    def test_unexpected_errors_and_finalization_failures_are_sticky(self):
        for error in (PermissionError(errno.EACCES, CANARY), RuntimeError(CANARY), SystemExit(CANARY)):
            with self.subTest(error=type(error).__name__), self.fixture((error, GOOD)) as fixture:
                clock, _, connects, _ = fixture
                report = waiter.run(clock.args())
                self.assertEqual(report["result"], "fail")
                self.assertEqual(len(connects), 1)
                self.assertEqual(clock.sleeps, [])
                self.assertNotIn(CANARY, waiter.encoded(report).decode())
        with self.fixture(close_error=OSError(CANARY)) as (clock, _, connects, _):
            report = waiter.run(clock.args())
            self.assertEqual(report["failure"], "internal")
            self.assertFalse(report["health_ready"])
            self.assertEqual(len(connects), 1)
        with self.fixture() as (clock, _, connects, _), mock.patch.object(waiter.probe, "health",
                side_effect=ConnectionResetError(errno.ECONNRESET, CANARY)):
            report = waiter.run(clock.args())
            self.assertEqual(report["failure"], "transport")
            self.assertEqual(len(connects), 1)

    def test_external_cancellation_restores_handlers_without_more_requests(self):
        before = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
        def cancel(_):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        with self.fixture((cancel, GOOD)) as (clock, _, connects, _):
            report = waiter.run(clock.args())
            self.assertEqual(report["failure"], "cancelled", report)
            self.assertEqual(len(connects), 1)
            self.assertEqual(clock.sleeps, [])
        self.assertEqual(before, {number: signal.getsignal(number) for number in before})

    def test_source_or_trust_failures_stop_before_network(self):
        for target in ((waiter, "source_identity"), (waiter.probe, "trust_input")):
            with self.fixture() as (clock, _, connects, _), mock.patch.object(*target,
                    side_effect=waiter.inference.Rejected("identity")):
                report = waiter.run(clock.args())
                self.assertEqual(report["failure"], "identity")
                self.assertEqual(connects, [])

    def test_contract_requires_explicit_run_target_main_thread_and_bounded_cutoff(self):
        with self.fixture() as (clock, _, connects, _):
            for seconds in (-1, 0, 1201):
                self.assertEqual(waiter.run(clock.args(seconds=seconds))["failure"], "deadline")
            for change in ({"run": False}, {"address": "127.0.0.1"}, {"hostname": "bad\r\n"}):
                self.assertEqual(waiter.run(clock.args(**change))["failure"], "contract")
            with mock.patch.object(waiter.threading, "current_thread", return_value=object()):
                self.assertEqual(waiter.run(clock.args())["failure"], "contract")
            with mock.patch.object(waiter.signal, "getitimer", return_value=(1.0, 1.0)):
                self.assertEqual(waiter.run(clock.args())["failure"], "contract")
            self.assertEqual(connects, [])
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(waiter.main(["--api-key-file", "/private/" + CANARY]), 1)
        self.assertNotIn(CANARY, output.getvalue())
        self.assertNotIn("/private/", output.getvalue())

    def test_source_binding_adds_own_committed_blob_to_existing_provenance(self):
        raw = b"synthetic source"
        with mock.patch.object(waiter.probe, "source_identity", return_value=("b" * 40, {"old": "c" * 64})), \
                mock.patch.object(waiter.inference.process.bootstrap, "read_source", return_value=raw), \
                mock.patch.object(waiter.service.identity, "source_receipt") as receipt:
            revision, hashes = waiter.source_identity(1005)
        self.assertEqual(revision, "b" * 40)
        self.assertEqual(hashes, {"old": "c" * 64, waiter.SCRIPT: hashlib.sha256(raw).hexdigest()})
        receipt.assert_called_once_with({"git_sha": revision, "script": waiter.SCRIPT,
            "source_sha256": hashes[waiter.SCRIPT]}, 1005)

    def test_real_alarm_timeout_and_prior_deadline_in_disposable_process(self):
        setup = f"""
import sys
sys.path.insert(0, {str(waiter.ROOT / 'scripts')!r})
"""
        body = """
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import signal, time
import test_selected_health_wait as tests
w = tests.waiter
worker = w.service.identity.worker
original = {number: signal.getsignal(number) for number in (signal.SIGALRM, signal.SIGINT, signal.SIGTERM)}
w.source_identity = lambda _: ('b' * 40, {})
w.probe.trust_input = lambda *_: (object(), b'c' * 32)
opened, closed = [], []
@contextmanager
def tls(*_):
    sock = tests.Socket(tests.GOOD)
    opened.append(sock)
    try:
        yield sock
    finally:
        closed.append(sock)
w.probe.pinned_tls = tls
real_health = w.probe.health
def health(sock, args, deadline, report):
    if len(opened) == 1:
        time.sleep(3)
        raise AssertionError('alarm did not interrupt read')
    return real_health(sock, args, deadline, report)
w.probe.health = health
w.ATTEMPT_SECONDS, w.BACKOFF = .05, (0,)
args = tests.external.args(deadline=(datetime.now(timezone.utc) + timedelta(seconds=8)).strftime('%Y-%m-%dT%H:%M:%SZ'))
report = w.run(args)
assert report['result'] == 'pass' and report['attempts'] == 2 and report['attempt_timeouts'] == 1, report
assert opened == closed
assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
assert all(signal.getsignal(number) == handler for number, handler in original.items())
opened.clear()
closed.clear()
w.ATTEMPT_SECONDS = 5
try:
    with worker.alarm(time.monotonic() + .05):
        report = w.run(args)
        assert report['result'] == 'fail' and report['failure'] == 'deadline' and report['attempts'] == 1, report
except worker.Abort:
    pass
else:
    raise AssertionError('outer deadline was swallowed')
assert opened == closed and len(opened) == 1
assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
assert all(signal.getsignal(number) == handler for number, handler in original.items())
assert signal.SIGALRM not in signal.sigpending()
"""
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", textwrap.dedent(setup) + body],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
