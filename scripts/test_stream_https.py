# SPDX-License-Identifier: Apache-2.0
"""In-memory HTTP responses only; no endpoint, DNS, credentials or engine."""

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
import signal
import time
import unittest
from unittest import mock

import stream_https as stream

CANARY = "private-synthetic-stream-canary"
KEY = b"SYNTHETIC_PRIVATE_CANARY"


def body():
    return {"model": stream.transport.SELECTED_MODEL,
            "messages": [{"role": "user", "content": CANARY}], "max_tokens": 512,
            "temperature": 0, "stream": True, "stream_options": {"include_usage": True},
            "reasoning_effort": "none", "cache_salt": "a" * 64}


def data(cached=1024):
    def event(choices, **extra):
        return b"data: " + json.dumps({"model": stream.transport.SELECTED_MODEL,
            "choices": choices, **extra}).encode() + b"\n\n"
    return b"".join([
        event([{"index": 0, "delta": {"content": CANARY}, "finish_reason": None}]),
        event([{"index": 0, "delta": {}, "finish_reason": "stop"}]),
        event([], usage={"prompt_tokens": 2048, "completion_tokens": 2, "total_tokens": 2050,
                        "prompt_tokens_details": {"cached_tokens": cached}}), b"data: [DONE]\n\n"])


def response(raw=None, extra=b"", status=b"200 OK"):
    raw = data() if raw is None else raw
    return (b"HTTP/1.1 " + status + b"\r\nContent-Type: text/event-stream\r\n"
            + extra + b"\r\n" + raw)


class Connection:
    def __init__(self, raw):
        self.buffer, self.sent, self.timeouts, self.closed = io.BytesIO(raw), [], [], False

    def settimeout(self, value): self.timeouts.append(value)
    def sendall(self, value): self.sent.append(value)
    def makefile(self, mode): return self.buffer


class HTTPTests(unittest.TestCase):
    def client(self, raw):
        with mock.patch.object(stream.bounded_https, "system_trust"):
            client = stream.SelectedHTTPS("candidate.example.test", address="8.8.8.8")
        connection = Connection(raw)
        @contextmanager
        def connect(deadline):
            try:
                yield connection
            finally:
                connection.closed = True
        client.connection = connect
        return client, connection

    def collect(self, client, *, before_send=lambda: None, seconds=2, prompt=2048):
        return client.collect(body(), KEY, prompt_tokens=prompt,
                              deadline=time.monotonic() + seconds, before_send=before_send)

    def test_numeric_cache_receipt_no_content_and_intent_before_send(self):
        client, connection = self.client(response())
        intents = []
        def intent():
            self.assertEqual(connection.sent, [])
            intents.append(1)
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            result = self.collect(client, before_send=intent)
        self.assertEqual(result.status, "completed")
        self.assertEqual((result.prompt_tokens, result.completion_tokens, result.cached_prompt_tokens), (2048, 2, 1024))
        self.assertEqual(intents, [1])
        self.assertEqual(client.attempts, {stream.transport.PATH: 1})
        self.assertTrue(client.last_attempt_started and connection.closed and connection.buffer.closed)
        self.assertEqual(client.last_status, 200)
        self.assertIn(b"Accept: text/event-stream\r\n", connection.sent[0])
        self.assertIn(b"Host: candidate.example.test\r\n", connection.sent[0])
        self.assertEqual(json.loads(connection.sent[0].split(b"\r\n\r\n", 1)[1]), body())
        self.assertNotIn(CANARY, repr(result) + repr(vars(client)) + out.getvalue() + err.getvalue())
        self.assertFalse(client.used)

    def test_length_and_chunked_framing(self):
        raw = data()
        chunk = format(len(raw), "x").encode() + b"\r\n" + raw + b"\r\n"
        variants = [(raw, b"Content-Length: " + str(len(raw)).encode() + b"\r\n", "completed"),
                    (chunk + b"0\r\n\r\n", b"Transfer-Encoding: chunked\r\n", "completed"),
                    (chunk + b"0\r\n", b"Transfer-Encoding: chunked\r\n", "protocol-error"),
                    (raw + b"unexpected", b"Content-Length: " + str(len(raw)).encode() + b"\r\n", "protocol-error")]
        for wire, headers, expected in variants:
            with self.subTest(expected=expected, wire=wire[-20:]):
                client, connection = self.client(response(wire, headers))
                sample = self.collect(client)
                self.assertEqual(sample.status, expected)
                self.assertEqual(sample.prompt_tokens, 2048)
                self.assertTrue(connection.closed and connection.buffer.closed)

    def test_known_usage_survives_missing_done_wrong_count_and_bad_cache(self):
        for raw, prompt, cached in ((data().replace(b"data: [DONE]\n\n", b""), 2048, 1024),
                                    (data(), 2049, 1024), (data(-1), 2048, None)):
            client, _ = self.client(response(raw))
            result = self.collect(client, prompt=prompt)
            self.assertEqual(result.status, "protocol-error")
            self.assertEqual((result.prompt_tokens, result.completion_tokens, result.cached_prompt_tokens), (2048, 2, cached))

    def test_redirect_and_overload_have_one_attempt_no_body_logging(self):
        for status, expected in ((b"302 Found", "protocol-error"), (b"429 Too Many Requests", "transport-error"),
                                  (b"503 Unavailable", "transport-error")):
            client, connection = self.client(response(status=status, extra=b"Location: https://other.invalid/\r\n"))
            result = self.collect(client)
            self.assertEqual(result.status, expected)
            self.assertIsNone(result.prompt_tokens)
            self.assertEqual(len(connection.sent), 1)
            self.assertEqual(client.attempts[stream.transport.PATH], 1)
            self.assertTrue(connection.closed)

    def test_tls_failure_does_not_send_intent_or_auth(self):
        client, connection = self.client(b"")
        client.connection = mock.Mock(side_effect=OSError("private failure"))
        intent = mock.Mock()
        result = self.collect(client, before_send=intent)
        self.assertEqual(result.status, "transport-error")
        intent.assert_not_called()
        self.assertFalse(client.last_attempt_started or client.used)
        self.assertEqual(client.attempts[stream.transport.PATH], 0)
        self.assertEqual(connection.sent, [])

    def test_failed_durable_intent_propagates_and_prevents_send(self):
        client, connection = self.client(response())
        with self.assertRaises(OSError):
            self.collect(client, before_send=mock.Mock(side_effect=OSError("receipt unavailable")))
        self.assertEqual(connection.sent, [])
        self.assertFalse(client.used or client.last_attempt_started)
        self.assertEqual(client.attempts[stream.transport.PATH], 0)
        self.assertTrue(connection.closed)

    def test_partial_send_is_unknown_not_free_or_retried(self):
        client, connection = self.client(response())
        connection.sendall = mock.Mock(side_effect=OSError("partial send"))
        sample = self.collect(client)
        self.assertEqual(sample.status, "transport-error")
        self.assertIsNone(sample.prompt_tokens)
        self.assertTrue(client.last_attempt_started and connection.closed)
        self.assertEqual(client.attempts[stream.transport.PATH], 1)
        connection.sendall.assert_called_once()

    def test_absolute_deadline_bounds_slow_headers_and_restores_alarm(self):
        client, connection = self.client(response())
        class Slow(io.BytesIO):
            def readline(self, *args):
                while True: time.sleep(0.01)
        connection.buffer = Slow()
        previous = signal.getsignal(signal.SIGALRM)
        started = time.monotonic()
        result = self.collect(client, seconds=0.05)
        self.assertEqual(result.status, "timeout")
        self.assertLess(time.monotonic() - started, 1)
        self.assertTrue(connection.closed and connection.buffer.closed)
        self.assertIs(signal.getsignal(signal.SIGALRM), previous)
        self.assertFalse(client.used)

    def test_closed_request_bounds_before_any_connection(self):
        variants = [{"model": "other"}, {"cache_salt": ""}, {"temperature": True},
                    {"stream": False}, {"stream_options": {"include_usage": 1}},
                    {"max_tokens": 8193}, {"messages": [{"role": "assistant", "content": CANARY}]},
                    {"messages": [{"role": "user", "content": "x" * (stream.MAX_REQUEST + 1)}]},
                    {"tools": []}]
        client, connection = self.client(b"")
        for change in variants:
            with self.subTest(fields=list(change)), self.assertRaises(ValueError):
                client.collect(body() | change, KEY, prompt_tokens=2048,
                               deadline=time.monotonic() + 2, before_send=lambda: None)
        for prompt in (True, 0, 131073):
            with self.assertRaises(ValueError): self.collect(client, prompt=prompt)
        with self.assertRaises(ValueError):
            stream.SelectedHTTPS("candidate.example.test", address=None)
        self.assertEqual(connection.sent, [])
        self.assertFalse(client.used)


if __name__ == "__main__":
    unittest.main()
