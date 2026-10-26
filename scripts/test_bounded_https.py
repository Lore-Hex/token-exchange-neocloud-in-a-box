# SPDX-License-Identifier: Apache-2.0
"""Synthetic streams only; no HTTPS endpoint or credentials."""

from contextlib import contextmanager
import io
import hashlib
import signal
import socket
import time
import unittest
from unittest import mock

import bounded_https as https


class Connection:
    def __init__(self, response):
        self.response, self.sent = response, []

    def sendall(self, value):
        self.sent.append(value)

    def makefile(self, mode):
        return io.BytesIO(self.response)


class HTTPTests(unittest.TestCase):
    def test_pinned_candidate_binds_port_cert_and_rejects_wrong_peer(self):
        pem = b"-----BEGIN CERTIFICATE-----\nSYNTHETIC\n-----END CERTIFICATE-----\n"
        with mock.patch.object(https.ssl, "PEM_cert_to_DER_cert", return_value=b"synthetic-der"), \
                mock.patch.object(https.ssl, "SSLContext") as context, \
                mock.patch.object(https.socket, "socket") as sockets, \
                mock.patch.object(https, "system_trust") as roots:
            client = https.HTTPS("candidate.example.test", ("/v1/chat/completions",),
                address="8.8.8.8", port=18443, certificate_pem=pem,
                certificate_sha256=hashlib.sha256(pem).hexdigest())
            peer = context.return_value.wrap_socket.return_value.__enter__.return_value
            peer.getpeercert.return_value = b"different-der"
            with self.assertRaises(ValueError):
                client.post("/v1/chat/completions", {}, b"SYNTHETIC_PRIVATE_CANARY", time.monotonic() + 2)
            sockets.return_value.__enter__.return_value.connect.assert_called_once_with(("8.8.8.8", 18443))
            peer.sendall.assert_not_called()
            self.assertEqual(client.attempts["/v1/chat/completions"], 0)
            roots.assert_not_called()
            peer.getpeercert.return_value = b"synthetic-der"
            with client.connection(time.monotonic() + 2) as actual:
                self.assertIs(actual, peer)

    def test_pin_cannot_override_support_api_or_accept_bad_digest(self):
        for args in ({"address": None}, {"address": "8.8.8.8"}):
            with self.assertRaises(ValueError):
                https.HTTPS("api.openai.com", ("/v1/responses",), certificate_pem=b"bad",
                    certificate_sha256="a" * 64, **args)
        with self.assertRaises(ValueError):
            https.HTTPS("candidate.example.test", ("/v1/chat/completions",), address="8.8.8.8",
                port=18443, certificate_pem=b"bad", certificate_sha256="a" * 64)
        with self.assertRaises(ValueError):
            https.HTTPS("candidate.example.test", ("/v1/chat/completions",), address="8.8.8.8", port=18443)

    def client(self, response):
        with mock.patch.object(https, "system_trust"):
            client = https.HTTPS("api.openai.com", ("/v1/responses",))
        connection = Connection(response)
        @contextmanager
        def connect(deadline):
            yield connection
        client.connection = connect
        return client, connection

    def test_single_post_closed_path_and_credentials_not_redirected(self):
        client, connection = self.client(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}')
        self.assertEqual(client.post("/v1/responses", {}, b"SYNTHETIC_PRIVATE_CANARY", time.monotonic() + 2), {})
        self.assertEqual(client.attempts, {"/v1/responses": 1})
        self.assertEqual(len(connection.sent), 1)
        self.assertTrue(connection.sent[0].startswith(b"POST /v1/responses HTTP/1.1\r\nHost: api.openai.com\r\n"))
        for path in ("https://other.invalid/v1/responses", "/v1/responses?x=1", "/v1/chat/completions"):
            with self.assertRaises(ValueError):
                client.post(path, {}, b"SYNTHETIC_PRIVATE_CANARY", time.monotonic() + 2)
        self.assertEqual(len(connection.sent), 1)

    def test_redirects_errors_duplicate_length_oversize_and_encoding_fail(self):
        variants = [b"302 Found\r\nLocation: https://other.invalid/x", b"401 Unauthorized", b"429 Too Many Requests",
                    b"200 OK\r\nContent-Length: 2\r\nContent-Length: 2",
                    b"200 OK\r\nContent-Length: 9999999", b"200 OK\r\nContent-Encoding: gzip",
                    b"200 OK\r\nTransfer-Encoding: chunked\r\nContent-Length: 2"]
        for start in variants:
            with self.subTest(start=start):
                client, connection = self.client(b"HTTP/1.1 " + start + b"\r\nContent-Type: application/json\r\n\r\n{}")
                with self.assertRaises(ValueError):
                    client.post("/v1/responses", {}, b"SYNTHETIC_PRIVATE_CANARY", time.monotonic() + 2)
                self.assertEqual(len(connection.sent), 1)

    def test_tls_failure_sends_no_authorization_and_no_attempt(self):
        client, connection = self.client(b"")
        client.connection = mock.Mock(side_effect=OSError("synthetic TLS rejection"))
        with self.assertRaises(OSError):
            client.post("/v1/responses", {}, b"SYNTHETIC_PRIVATE_CANARY", time.monotonic() + 2)
        self.assertEqual(connection.sent, [])
        self.assertEqual(client.attempts["/v1/responses"], 0)
        self.assertFalse(client.used)

    def test_absolute_deadline_bounds_slow_trickle_read(self):
        client, _ = self.client(b"")
        class Slow(Connection):
            def makefile(self, mode):
                class Stream(io.BytesIO):
                    def read(self, size=-1):
                        while True:
                            time.sleep(0.01)
                return Stream(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n")
        @contextmanager
        def connect(deadline):
            yield Slow(b"")
        client.connection = connect
        previous = signal.getsignal(signal.SIGALRM)
        started = time.monotonic()
        with self.assertRaises(https.worker.Abort):
            client.post("/v1/responses", {}, b"SYNTHETIC_PRIVATE_CANARY", started + 0.08)
        self.assertLess(time.monotonic() - started, 1)
        self.assertIs(signal.getsignal(signal.SIGALRM), previous)
        self.assertFalse(client.used)

    def test_private_and_nonclosed_hosts_rejected(self):
        with mock.patch.object(https, "system_trust") as trust:
            for hostname, address in (("other.invalid", None), ("api.openai.com\r\nX", None),
                                      ("synthetic.invalid", "127.0.0.1"), ("synthetic.invalid", "10.0.0.1"),
                                      ("synthetic.invalid", "::1")):
                with self.assertRaises(ValueError):
                    https.HTTPS(hostname, ("/v1/responses",), address=address)
            trust.assert_not_called()

    def test_dns_has_one_connect_no_address_retry(self):
        with mock.patch.object(https, "system_trust"), mock.patch.object(https.socket, "getaddrinfo",
                return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
                              (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))]), \
                mock.patch.object(https.socket, "socket") as sockets:
            sockets.return_value.__enter__.return_value.connect.side_effect = OSError("synthetic")
            client = https.HTTPS("api.openai.com", ("/v1/responses",))
            with self.assertRaises(OSError):
                client.post("/v1/responses", {}, b"SYNTHETIC_PRIVATE_CANARY", time.monotonic() + 2)
            sockets.assert_called_once()
            sockets.return_value.__enter__.return_value.connect.assert_called_once_with(("8.8.8.8", 443))


if __name__ == "__main__":
    unittest.main()
