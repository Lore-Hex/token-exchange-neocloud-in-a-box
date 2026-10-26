# SPDX-License-Identifier: Apache-2.0
"""Owned local metrics framing/deadline tests, synthetic content only."""

from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
import unittest
from unittest import mock

import stream_capacity as capacity
from test_stream_scaling import capacities, metrics
from test_stream_c8 import MODEL, endpoint


class CapacityHTTPTests(unittest.TestCase):
    @contextmanager
    def server(self, mode):
        release, requests = threading.Event(), []
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *_):
                pass
            def do_GET(self):
                requests.append((self.path, dict(self.headers)))
                self.close_connection = True
                raw = metrics()
                self.send_response(302 if mode == "redirect" else 200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                if mode == "chunked":
                    self.send_header("Transfer-Encoding", "chunked")
                else:
                    self.send_header("Content-Length", str(capacity.LIMIT + 1 if mode == "large" else len(raw)))
                    if mode == "duplicate":
                        self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                try:
                    if mode == "trickle":
                        for b in raw:
                            self.wfile.write(bytes([b]))
                            self.wfile.flush()
                            if release.wait(.02):
                                break
                    elif mode == "chunked":
                        self.wfile.write(f"{len(raw):x}\r\n".encode() + raw + b"\r\n0\r\n\r\n")
                    else:
                        self.wfile.write(raw)
                except OSError:
                    pass
        server = ThreadingHTTPServer((capacity.stream.HOST, capacity.stream.PORT), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield requests
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(3)

    def probe(self, seconds=3):
        ep, checks = endpoint(), []
        def check(deadline, sock=None):
            self.assertIs(threading.current_thread(), threading.main_thread())
            checks.append(sock is not None)
            if sock is not None:
                self.assertEqual(sock.getpeername(), (capacity.stream.HOST, capacity.stream.PORT))
        with mock.patch.object(ep, "check", side_effect=check):
            row = capacity.probe(ep, MODEL, time.monotonic() + seconds)
        self.assertEqual(checks, [False, True, False])
        return row

    def test_exact_length_and_chunked_without_redirect_or_compression(self):
        for mode in ("length", "chunked"):
            with self.subTest(mode=mode), self.server(mode) as seen:
                self.assertEqual(self.probe(), capacities())
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0][0], "/metrics")
            self.assertEqual(seen[0][1]["Accept-Encoding"], "identity")

    def test_redirect_duplicate_length_and_oversize_fail_without_retry(self):
        for mode in ("redirect", "duplicate", "large"):
            with self.subTest(mode=mode), self.server(mode) as seen, self.assertRaises(ValueError):
                self.probe()
            self.assertEqual(len(seen), 1)

    def test_slow_trickle_uses_absolute_alarm(self):
        with self.server("trickle"), self.assertRaises(capacity.stream.worker.Abort):
            self.probe(.15)

    def test_exact_source_closure_and_tamper_control(self):
        paths = []
        data = b"source fixture"
        pins = {k: hashlib.sha256(data).hexdigest() for k in capacity.SOURCES}
        def read(path, limit):
            paths.append(path)
            self.assertEqual(limit, 256 << 10)
            return data
        with mock.patch.object(capacity, "SOURCES", pins):
            capacity.verify_sources(read, "/fixed")
            self.assertEqual(set(paths), {"/fixed/" + k for k in pins})
            with self.assertRaises(ValueError):
                capacity.verify_sources(lambda *_: data + b"tamper", "/fixed")


if __name__ == "__main__":
    unittest.main()
