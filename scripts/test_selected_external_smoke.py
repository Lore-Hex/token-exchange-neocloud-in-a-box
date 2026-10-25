# SPDX-License-Identifier: Apache-2.0
"""Synthetic sockets and exact HTTP bytes; no external request or inference."""

from contextlib import ExitStack, contextmanager, redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
from pathlib import Path
import ssl
import types
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("external_smoke", Path(__file__).with_name("probe-selected-service.py"))
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
CANARY = "PRIVATE_RESPONSE_CANARY"
KEY = b"SYNTHETIC_TEST_KEY_" * 4


def args(**changes):
    values = dict(run=True, address="8.8.8.8", port=8443, hostname="selected.test",
        certificate="/private/cert.pem", certificate_sha256="a" * 64, api_key_file="/private/key",
        deadline=(datetime.now(timezone.utc) + timedelta(seconds=149)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return types.SimpleNamespace(**{**values, **changes})


def stream(*, usage=True, done=True):
    rows = [dict(model=probe.service.MODEL, choices=[dict(index=0,
        delta={"role": "assistant", "content": CANARY}, finish_reason=None)]),
        dict(model=probe.service.MODEL, choices=[dict(index=0, delta={}, finish_reason="stop")])]
    if usage:
        rows.append(dict(model=probe.service.MODEL, choices=[], usage={
            "prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}))
    value = b"".join(b"data: " + probe.encoded(row) + b"\n\n" for row in rows)
    return value + (b"data: [DONE]\n\n" if done else b"")


def response(status=200, payload=None, extra=b""):
    payload = stream() if payload is None else payload
    media = b"text/event-stream" if status == 200 else b"application/json"
    return (b"HTTP/1.1 " + str(status).encode() + b" Test\r\nContent-Type: " + media
        + b"\r\nContent-Length: " + str(len(payload)).encode() + b"\r\nConnection: close\r\n\r\n"
        + payload + extra)


class Socket:
    def __init__(self, raw):
        self.raw, self.sent, self.timeouts = raw, [], []

    def makefile(self, *_):
        return io.BytesIO(self.raw)

    def settimeout(self, value):
        self.timeouts.append(value)

    def sendall(self, value):
        self.sent.append(value)


class ExternalTests(unittest.TestCase):
    def test_health_is_one_empty_200_with_complete_framing_and_no_credential(self):
        good = b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        for raw in (good, good + b"unexpected", good.replace(b"200 OK", b"503 Down"),
                    good.replace(b"Content-Length: 0", b"Content-Length: 1"),
                    good.replace(b"Content-Length: 0", b"Content-Length: 0\r\nContent-Length: 0"),
                    good.replace(b"Content-Length: 0", b"Transfer-Encoding: chunked") + b"0\r\n\r\n"):
            sock, report = Socket(raw), probe.empty_result()
            if raw == good:
                probe.request(sock, args(), "health", KEY, probe.time.monotonic() + 5, report)
            else:
                with self.assertRaises((ValueError, probe.inference.Rejected)):
                    probe.request(sock, args(), "health", KEY, probe.time.monotonic() + 5, report)
            self.assertTrue(report["attempts"]["health"])
            self.assertFalse(report["attempts"]["stream"])
            self.assertEqual(len(sock.sent), 1)
            self.assertNotIn(KEY, sock.sent[0])
            self.assertNotIn(b"Authorization", sock.sent[0])

    def test_target_is_explicit_numeric_public_ipv4_and_header_safe(self):
        probe.validate_target(args())
        for change in ({"address": "localhost"}, {"address": "127.0.0.1"}, {"address": "10.0.0.1"},
            {"address": "169.254.169.254"}, {"address": "::1"}, {"address": "224.1.1.1"},
            {"port": True}, {"port": 0}, {"hostname": "good\r\nAuthorization: bad"},
            {"certificate_sha256": "bad"}):
            with self.subTest(change=change), self.assertRaises((ValueError, probe.inference.Rejected)):
                probe.validate_target(args(**change))

    def test_exact_peer_pin_checked_before_yield(self):
        raw = mock.MagicMock()
        raw.__enter__.return_value = raw
        wrapped = mock.MagicMock()
        wrapped.__enter__.return_value = wrapped
        wrapped.getpeercert.return_value = b"certificate"
        trust = mock.Mock()
        trust.check_hostname, trust.verify_mode = True, ssl.CERT_REQUIRED
        trust.minimum_version, trust.keylog_filename = ssl.TLSVersion.TLSv1_2, None
        trust.wrap_socket.return_value = wrapped
        with mock.patch.object(probe.socket, "socket", return_value=raw):
            with probe.pinned_tls(args(), trust, hashlib.sha256(b"certificate").digest(), probe.time.monotonic() + 5):
                pass
            with self.assertRaises(probe.inference.Rejected):
                with probe.pinned_tls(args(), trust, b"x" * 32, probe.time.monotonic() + 5):
                    self.fail("wrong certificate accepted")
        raw.connect.assert_called_with(("8.8.8.8", 8443))
        trust.wrap_socket.assert_called_with(raw, server_hostname="selected.test")
        wrapped.sendall.assert_not_called()

    def test_trust_file_hash_and_single_certificate_required_before_connect(self):
        pem = ssl.DER_cert_to_PEM_cert(b"synthetic-not-a-certificate").encode()
        for value, digest in ((pem, "a" * 64), (pem + pem, hashlib.sha256(pem + pem).hexdigest()),
                              (pem, hashlib.sha256(pem).hexdigest())):
            with mock.patch.object(probe.service.identity, "private_read", return_value=value), \
                    mock.patch.object(probe.socket, "socket") as connect:
                with self.assertRaises((ValueError, ssl.SSLError)):
                    probe.trust_input(args(certificate_sha256=digest), probe.time.monotonic() + 5)
                connect.assert_not_called()

    def test_stream_keeps_usage_and_never_content(self):
        sock, report = Socket(response()), probe.empty_result()
        probe.request(sock, args(), "stream", KEY, probe.time.monotonic() + 5, report)
        self.assertEqual(report["usage"], {"prompt_tokens": 12, "completion_tokens": 3})
        self.assertFalse(report["usage_unknown"])
        self.assertEqual(report["measurement"]["status"], "completed")
        self.assertNotIn(CANARY, probe.encoded(report).decode())
        self.assertNotIn(KEY.decode(), probe.encoded(report).decode())
        self.assertEqual(len(sock.sent), 1)
        self.assertIn(b"Authorization: Bearer " + KEY, sock.sent[0])

    def test_401_checks_do_not_send_real_key(self):
        denied = probe.encoded({"error": {"message": "invalid_api_key", "type": "invalid_api_key"}})
        for case in ("missing-key", "wrong-key"):
            sock, report = Socket(response(401, denied)), probe.empty_result()
            probe.request(sock, args(), case, KEY, probe.time.monotonic() + 5, report)
            self.assertNotIn(KEY, sock.sent[0])
            self.assertIsNone(report["usage"])
            self.assertFalse(report["usage_unknown"])
        with self.assertRaises(probe.inference.Rejected):
            probe.request(Socket(response(302, b"")), args(), "missing-key", KEY,
                          probe.time.monotonic() + 5, probe.empty_result())

    def test_broken_stream_does_not_erase_known_usage_or_retry(self):
        for payload, extra, known in ((stream(done=False), b"", True), (stream(usage=False), b"", False),
                                      (stream(), b"unexpected", True)):
            sock, report = Socket(response(payload=payload, extra=extra)), probe.empty_result()
            with self.subTest(known=known), self.assertRaises((ValueError, probe.inference.Rejected)):
                probe.request(sock, args(), "stream", KEY, probe.time.monotonic() + 5, report)
            self.assertEqual(report["usage"] is not None, known)
            self.assertEqual(report["usage_unknown"], not known)
            self.assertEqual(len(sock.sent), 1)
            self.assertIsNone(report["measurement"])

    def test_malformed_401_framing_stops_before_real_key_read(self):
        denied = probe.encoded({"error": {"message": "invalid_api_key", "type": "invalid_api_key"}})
        good = response(401, denied)
        length = b"Content-Length: " + str(len(denied)).encode()
        malformed = (
            good.replace(length, b"Content-Length: " + str(len(denied) + 10).encode()),
            good.replace(length, b"Content-Length: " + str(len(denied) - 1).encode()),
            good.replace(length, length + b"\r\n" + length),
            good.replace(length, length + b"\r\nContent-Length: 2"),
            good.replace(length + b"\r\n", b""),
            good.replace(length, b"Content-Length: 4097"),
            good.replace(length, b"Content-Length: +" + str(len(denied)).encode()),
            good.replace(length, b"Content-Length: 0" + str(len(denied)).encode()),
            good.replace(length, length + b"\r\nTransfer-Encoding: chunked"),
            good.replace(length, length + b"\r\nContent-Encoding: gzip"),
            good.replace(b"application/json", b"text/html"),
            good.replace(length, length + b"\r\nContent-Type: application/json"),
            good + b"PRIVATE_TRAILING_CANARY", good[:-1])
        for raw in malformed:
            for position in (1, 2):
                with self.subTest(position=position, response=raw), self.fixture() as (sockets, read):
                    sockets[position].raw = raw
                    report = probe.run(args())
                    self.assertEqual(report["result"], "fail", report)
                    self.assertFalse(report["checks"][probe.CASES[position]])
                    self.assertFalse(report["attempts"]["stream"])
                    self.assertIsNone(report["usage"])
                    read.assert_not_called()
                    self.assertEqual(sum(len(sock.sent) for sock in sockets), position + 1)
                    self.assertNotIn("PRIVATE_TRAILING_CANARY", probe.encoded(report).decode())

    @contextmanager
    def fixture(self, *, fail_at=None):
        events = []
        denied = probe.encoded({"error": {"message": "invalid_api_key", "type": "invalid_api_key"}})
        sockets = [Socket(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"),
                   Socket(response(401, denied)), Socket(response(401, denied)), Socket(response())]
        @contextmanager
        def tls(*_):
            index = len(events)
            events.append("verified")
            if fail_at == index:
                raise ssl.SSLError("PRIVATE_CERT_CANARY")
            yield sockets[index]
        def private_read(*_):
            self.assertEqual(events, ["verified"] * 4)
            return KEY
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(probe, "source_identity", return_value=("b" * 40, {})))
            stack.enter_context(mock.patch.object(probe, "trust_input", return_value=(object(), b"c" * 32)))
            stack.enter_context(mock.patch.object(probe, "pinned_tls", tls))
            read = stack.enter_context(mock.patch.object(probe.service.identity, "private_read", side_effect=private_read))
            yield sockets, read

    def test_complete_run_has_one_inference_and_closed_claims(self):
        with self.fixture() as (sockets, read):
            report = probe.run(args())
        self.assertEqual(report["result"], "pass", report)
        self.assertTrue(all(report["checks"].values()))
        self.assertTrue(report["tls_peer_verified"])
        self.assertEqual(read.call_count, 1)
        self.assertEqual(sum(KEY in sock.sent[0] for sock in sockets), 1)
        for name in ("provider_conformance", "quality_eligible", "deployment_eligible", "confidential", "e2ee"):
            self.assertIs(report[name], False)

    def test_tls_failure_before_secret_access_and_no_retry(self):
        for failed in range(4):
            with self.fixture(fail_at=failed) as (sockets, read):
                report = probe.run(args())
            self.assertEqual(report["result"], "fail")
            self.assertFalse(report["tls_peer_verified"])
            self.assertFalse(report["attempts"]["stream"])
            read.assert_not_called()
            self.assertEqual(sum(len(sock.sent) for sock in sockets), failed)
            self.assertNotIn("PRIVATE_CERT_CANARY", probe.encoded(report).decode())

    def test_failed_health_stops_before_secret_read_or_inference(self):
        with self.fixture() as (sockets, read):
            sockets[0].raw = b"HTTP/1.1 503 Down\r\nContent-Length: 0\r\n\r\n"
            report = probe.run(args())
        self.assertEqual(report["result"], "fail")
        self.assertEqual(report["failure"], "not-ready")
        read.assert_not_called()
        self.assertEqual([len(sock.sent) for sock in sockets], [1, 0, 0, 0])

    def test_cutoff_and_cli_reject_without_network_or_private_output(self):
        with mock.patch.object(probe, "source_identity") as source:
            for value in ("2000-01-01T00:00:00Z", "2999-01-01T00:00:00Z", "invalid"):
                self.assertEqual(probe.run(args(deadline=value))["result"], "fail")
            source.assert_not_called()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(probe.main(["--PRIVATE_CANARY=/private/key"]), 1)
        self.assertNotIn("PRIVATE_CANARY", output.getvalue())
        self.assertNotIn("/private/key", output.getvalue())

    def test_failed_send_keeps_unknown_usage_without_retry(self):
        sock, report = Socket(response()), probe.empty_result()
        sock.sendall = mock.Mock(side_effect=OSError("PRIVATE_TRANSPORT_CANARY"))
        with self.assertRaises(OSError):
            probe.request(sock, args(), "stream", KEY, probe.time.monotonic() + 5, report)
        self.assertTrue(report["attempts"]["stream"])
        self.assertTrue(report["usage_unknown"])
        self.assertIsNone(report["usage"])
        self.assertEqual(sock.sendall.call_count, 1)
        self.assertNotIn("PRIVATE_TRANSPORT_CANARY", probe.encoded(report).decode())


if __name__ == "__main__":
    unittest.main()
