# SPDX-License-Identifier: Apache-2.0
"""Real local HTTP sockets with synthetic SSE; no engine or identity claim."""

import contextlib
import errno
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import os
import signal
import socket
import threading
import time
import unittest
from unittest import mock

import stream_transport as stream

CANARY = "private-synthetic-stream-canary"
MESSAGES = [{"role": "user", "content": "Reply with 2."}]


def event(choices, **extra):
    return b"data: " + json.dumps({"model": stream.MODEL, "choices": choices, **extra}).encode() + b"\n\n"


def pieces():
    return [event([{"index": 0, "delta": {"content": CANARY}, "finish_reason": None}]),
            event([{"index": 0, "delta": {}, "finish_reason": "stop"}]),
            event([], usage={"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}),
            b"data: [DONE]\n\n"]


def chunk(raw):
    return format(len(raw), "x").encode() + b"\r\n" + raw + b"\r\n"


@contextlib.contextmanager
def server(reply, *, headers=None, status=200):
    seen = []
    stop = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_POST(self):
            seen.append((self.path, dict(self.headers), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.close_connection = True
            try:
                if callable(reply):
                    reply(self, stop)
                    return
                self.send_response(status)
                for key, value in headers or [("Content-Type", "text/event-stream"),
                                               ("Content-Length", str(len(reply)))]:
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(reply)
            except (BrokenPipeError, ConnectionResetError):
                pass

    try:
        httpd = HTTPServer((stream.HOST, stream.PORT), Handler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise unittest.SkipTest("fixed local engine test port occupied") from None
        raise
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.005})
    thread.start()
    try:
        yield seen
    finally:
        stop.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(2)
        if thread.is_alive():
            raise AssertionError("synthetic server failed to stop")


@contextlib.contextmanager
def client(guard=None):
    # No public callback/alternate endpoint is added to production for tests.
    endpoint = stream.identity.Endpoint(stream.PORT, {"process": {}})
    with mock.patch.object(stream.identity.Endpoint, "check", autospec=True, side_effect=guard) as check:
        yield stream.OwnedStreamHTTP(endpoint), check


def collect(http, seconds=2):
    return http.collect(MESSAGES, prompt_tokens=7, max_output_tokens=2, deadline=time.monotonic() + seconds)


class TransportTests(unittest.TestCase):
    def test_million_response_bounds_are_closed_and_legacy_is_unchanged(self):
        class Socket:
            def __init__(self, raw): self.raw = raw
            def makefile(self, *args): return io.BytesIO(self.raw)
        def response(size, profile=None):
            raw = (b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: "
                   + str(size).encode() + b"\r\n\r\n")
            return stream.StreamResponse(Socket(raw), profile=profile)
        for profile, cap in ((None, 64 << 20), (stream.measurement.MILLION_PROFILE, 512 << 20)):
            for size in (cap, cap + 1):
                with response(size, profile) as parsed:
                    parsed.begin()
                    if size == cap:
                        self.assertEqual(stream.response_length(parsed, profile=profile), cap)
                    else:
                        with self.assertRaises(ValueError): stream.response_length(parsed, profile=profile)
                raw = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + format(size, "x").encode() + b"\r\n"
                with stream.StreamResponse(Socket(raw), profile=profile) as parsed:
                    parsed.begin()
                    if size == cap:
                        self.assertEqual(parsed._read_next_chunk_size(), cap)
                    else:
                        with self.assertRaises(ValueError): parsed._read_next_chunk_size()
        for profile in ("auto", True, [], "long128k"):
            with self.assertRaises(ValueError): response(1, profile)

    def test_million_profile_keeps_strict_http_termination(self):
        class Socket:
            def __init__(self, raw): self.raw = raw
            def makefile(self, *args): return io.BytesIO(self.raw)
        data = b"".join(pieces())
        for ending in (b"0\r\n\r\n", b"0\r\n", b"0\r\nX-Trailer: private\r\n\r\n"):
            raw = b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n" + chunk(data) + ending
            with stream.StreamResponse(Socket(raw), profile=stream.measurement.MILLION_PROFILE) as parsed:
                parsed.begin()
                self.assertIsNone(stream.response_length(parsed, profile=stream.measurement.MILLION_PROFILE))
                self.assertEqual(parsed.read1(len(data)), data)
                if ending == b"0\r\n\r\n":
                    self.assertEqual(parsed.read1(1), b"")
                else:
                    with self.assertRaises(ValueError): parsed.read1(1)

    def test_pinned_vllm_source_shaped_text_fixture_and_null_tool_rejection(self):
        # vLLM 5df63a7 serving.py: explicit fields survive exclude_unset;
        # final_usage_chunk uses exclude_unset + exclude_none. ParserEngine
        # sets only content/reasoning kwargs for text (source audit in docs).
        common = {"id": "chatcmpl-synthetic", "object": "chat.completion.chunk", "created": 1}
        rows = [event([{"index": 0, "delta": {"role": "assistant", "content": ""},
                        "logprobs": None, "finish_reason": None}], **common,
                       prompt_token_ids=None, prompt_text=None),
                event([{"index": 0, "delta": {"content": "2"}, "logprobs": None,
                        "finish_reason": None, "token_ids": None}], **common),
                event([{"index": 0, "delta": {}, "logprobs": None,
                        "finish_reason": "stop", "stop_reason": None, "token_ids": None}], **common),
                event([], **common, usage={"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9,
                      "prompt_tokens_details": {"cached_tokens": 3},
                      "completion_tokens_details": {"reasoning_tokens": 0}}), b"data: [DONE]\n\n"]
        with server(b"".join(chunk(row) for row in rows) + b"0\r\n\r\n",
                    headers=[("Content-Type", "text/event-stream; charset=utf-8"),
                             ("Transfer-Encoding", "chunked"), ("Connection", "close")]), client() as (http, _):
            result = collect(http)
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.output_events, 1)
            self.assertEqual(result.cached_prompt_tokens, 3)
        for field in ("tool_calls", "refusal"):
            rejected = event([{"index": 0, "delta": {"content": "2", field: None}, "finish_reason": None}])
            with self.subTest(field=field), server(rejected + b"".join(rows[2:])), client() as (http, _):
                self.assertEqual(collect(http).status, "protocol-error")

    def test_real_socket_fixed_request_no_dns_proxy_or_retained_content(self):
        with server(b"".join(pieces())) as seen, client() as (http, guard), \
                mock.patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS forbidden")), \
                mock.patch.dict(os.environ, {"HTTP_PROXY": CANARY, "HTTPS_PROXY": CANARY, "ALL_PROXY": CANARY}), \
                contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
            result = collect(http)
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.completion_tokens, 2)
            self.assertEqual(len(seen), 1)
            path, headers, body = seen[0]
            self.assertEqual(path, stream.PATH)
            self.assertEqual(body["model"], stream.MODEL)
            self.assertEqual(body["stream_options"], {"include_usage": True})
            self.assertEqual(body["max_completion_tokens"], 2)
            self.assertIs(body["stream"], True)
            self.assertEqual(body["n"], 1)
            self.assertEqual(body["chat_template_kwargs"], {"reasoning_effort": "none", "enable_thinking": False})
            self.assertNotIn("Authorization", headers)
            self.assertEqual(headers["Accept-Encoding"], "identity")
            self.assertEqual(guard.call_count, 3)
            self.assertEqual(len(guard.call_args_list[1].args), 3)
            self.assertEqual(stdout.getvalue() + stderr.getvalue(), "")
            self.assertNotIn(CANARY, repr(result) + repr(vars(http)))
            self.assertFalse(http.busy.locked())

    def test_chunked_and_close_delimited(self):
        data = b"".join(pieces())
        for headers, body in (([("Transfer-Encoding", "chunked")], b"".join(chunk(part) for part in pieces()) + b"0\r\n\r\n"),
                              ([], data)):
            with self.subTest(headers=headers), server(body, headers=[("Content-Type", "text/event-stream; charset=utf-8"), *headers]), client() as (http, _):
                self.assertEqual(collect(http).status, "completed")

    def test_fragmented_single_chunk_is_measured_before_remainder(self):
        consumed = threading.Event()
        original = stream.measurement.StreamMeasurement.feed

        def feed(meter, raw, elapsed):
            original(meter, raw, elapsed)
            if meter.events:
                consumed.set()

        def reply(handler, stop):
            parts = pieces()
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Transfer-Encoding", "chunked")
            handler.end_headers()
            handler.wfile.write(format(sum(map(len, parts)), "x").encode() + b"\r\n")
            for byte in parts[0]:
                handler.wfile.write(bytes([byte]))
            # A read(n)/whole-chunk implementation times out instead of seeing
            # this first event. No scheduling-dependent latency threshold.
            if consumed.wait(1):
                handler.wfile.write(b"".join(parts[1:]) + b"\r\n0\r\n\r\n")

        with server(reply), client() as (http, _), mock.patch.object(stream.measurement.StreamMeasurement, "feed", feed):
            result = collect(http)
            self.assertEqual(result.status, "completed")
            self.assertTrue(consumed.is_set())
            self.assertLess(result.first_output_seconds, result.elapsed_seconds)

    def test_bad_http_headers_and_statuses_never_parse_body(self):
        good = [("Content-Type", "text/event-stream")]
        invalid = [good * 2, good + [("Content-Length", "10"), ("content-length", "10")],
                   good + [("Content-Length", "10"), ("Transfer-Encoding", "chunked")],
                   good + [("Transfer-Encoding", "gzip, chunked")],
                   good + [("Transfer-Encoding", "chunked"), ("transfer-encoding", "chunked")],
                   good + [("Content-Encoding", "gzip")], [("Content-Type", "application/json")],
                   [("Content-Type", "text/event-stream; charset=latin1")],
                   good + [("Content-Length", "-1")], good + [("Content-Length", "67108865")],
                   good + [("X-Extra", "a" * 65537)], good + [("X-Extra", "a")] * 33,
                   good + [("X-Folded", "a\r\n b")]]
        for headers in invalid:
            with self.subTest(headers=str(headers)[:80]), server(CANARY.encode(), headers=headers), client() as (http, _):
                self.assertEqual(collect(http).status, "protocol-error")
        for code in (301, 401, 429, 500):
            with self.subTest(code=code), server(CANARY.encode(), status=code), client() as (http, _):
                result = collect(http)
                self.assertEqual(result.status, "transport-error" if code in (429, 500) else "protocol-error")
                self.assertEqual(result.output_events, 0)

    def test_content_length_truncated_and_excess_after_done_preserve_usage(self):
        data = b"".join(pieces())
        for delta, extra in ((1, b""), (0, CANARY.encode())):
            with self.subTest(delta=delta), server(data + extra, headers=[("Content-Type", "text/event-stream"),
                    ("Content-Length", str(len(data) + delta))]), client() as (http, _):
                result = collect(http)
                self.assertEqual(result.status, "protocol-error")
                self.assertEqual(result.completion_tokens, 2)
                self.assertIsNone(result.metadata()["ttft_seconds"])

    def test_chunk_framing_errors_after_done_preserve_usage(self):
        data = b"".join(pieces())
        good = chunk(data)
        bodies = [good, good + b"0\r\n", good + b"0\r\nX-Trailer: private\r\n\r\n",
                  good[:-2] + b"XX0\r\n\r\n", good + b"0\r\n\r\nextra",
                  good + b"-1\r\n", good + b"1;ext=a\r\nx\r\n0\r\n\r\n",
                  good + b"4000001\r\n", good + b"00000000000000000\r\n"]
        for index, body in enumerate(bodies):
            with self.subTest(index=index), server(body, headers=[("Content-Type", "text/event-stream"),
                    ("Transfer-Encoding", "chunked")]), client() as (http, _):
                result = collect(http)
                self.assertEqual(result.status, "protocol-error")
                self.assertEqual(result.completion_tokens, 2)

    def test_wrong_model_usage_and_missing_terminal_events_fail(self):
        data = b"".join(pieces())
        bodies = [data.replace(stream.MODEL.encode(), b"other-model"),
                  data.replace(b'"prompt_tokens": 7', b'"prompt_tokens": 6').replace(b'"total_tokens": 9', b'"total_tokens": 8'),
                  b"".join(pieces()[:-1]), pieces()[0] + pieces()[-1], data + pieces()[-1],
                  data + b"data: {\"error\":\"private\"}\n\n"]
        for index, body in enumerate(bodies):
            with self.subTest(index=index), server(body), client() as (http, _):
                self.assertEqual(collect(http).status, "protocol-error")

    def test_slow_headers_body_and_after_usage_have_whole_deadline(self):
        for stage in ("header", "body", "chunk", "done"):
            def reply(handler, stop):
                if stage == "header":
                    raw = b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nX-Slow: "
                else:
                    raw = b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                    raw += b"Transfer-Encoding: chunked\r\n\r\n" if stage == "chunk" else b"\r\n"
                    raw += b"".join(pieces()) if stage == "done" else b""
                handler.wfile.write(raw)
                for _ in range(100):
                    if stop.wait(0.01):
                        break
                    handler.wfile.write(b" " if stage == "header" else b"1" if stage == "chunk" else b":")

            with self.subTest(stage=stage), server(reply), client() as (http, _):
                result = collect(http, 0.15)
                self.assertEqual(result.status, "timeout")
                self.assertLess(result.elapsed_seconds, 1)
                self.assertEqual(result.completion_tokens, 2 if stage == "done" else None)
                self.assertFalse(http.busy.locked())

    def test_done_waits_for_real_eof_even_with_content_length(self):
        def reply(handler, stop):
            data = b"".join(pieces())
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
            stop.wait(1)

        with server(reply), client() as (http, _):
            result = collect(http, 0.15)
            self.assertEqual(result.status, "timeout")
            self.assertEqual(result.completion_tokens, 2)

    def test_identity_checks_precede_body_and_drift_prevents_success(self):
        for fail_at in (1, 2, 3):
            calls = []

            def guard(endpoint, deadline, sock=None):
                calls.append(sock)
                if len(calls) == fail_at:
                    raise ValueError(CANARY)

            with self.subTest(fail_at=fail_at), server(b"".join(pieces())) as seen, client(guard) as (http, _):
                result = collect(http)
                self.assertEqual(result.status, "protocol-error")
                self.assertEqual(len(seen), 1 if fail_at == 3 else 0)
                self.assertEqual(result.completion_tokens, 2 if fail_at == 3 else None)
                if fail_at >= 2:
                    self.assertEqual(calls[1].fileno(), -1)
                self.assertFalse(http.busy.locked())

    def test_cancel_cleanup_and_existing_alarm_restored(self):
        handler = signal.getsignal(signal.SIGALRM)
        try:
            signal.setitimer(signal.ITIMER_REAL, 2)
            with client(lambda *_: (_ for _ in ()).throw(KeyboardInterrupt(CANARY))) as (http, _):
                result = collect(http)
                self.assertEqual(result.status, "cancelled")
                self.assertFalse(http.busy.locked())
                self.assertNotIn(CANARY, repr(result))
            self.assertIs(signal.getsignal(signal.SIGALRM), handler)
            remaining, interval = signal.getitimer(signal.ITIMER_REAL)
            self.assertGreater(remaining, 0)
            self.assertLessEqual(remaining, 2)
            self.assertEqual(interval, 0)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)

    def test_expired_deadline_no_connection_and_bounds_rejected_before_io(self):
        with client() as (http, guard), mock.patch.object(socket, "socket") as sock:
            self.assertEqual(collect(http, -1).status, "timeout")
            sock.assert_not_called()
            guard.assert_not_called()
            for prompt, output in ((True, 2), (0, 2), (4095, 2), (7, 513), (7, 0)):
                with self.subTest(prompt=prompt, output=output), self.assertRaises(ValueError):
                    http.collect(MESSAGES, prompt_tokens=prompt, max_output_tokens=output, deadline=time.monotonic() + 1)
            for messages in ([{"role": "tool", "content": CANARY}], [{"role": "user", "content": "", "tools": []}]):
                with self.assertRaises(ValueError):
                    http.collect(messages, prompt_tokens=7, max_output_tokens=2, deadline=time.monotonic() + 1)
            sock.assert_not_called()
        with self.assertRaises(ValueError):
            stream.OwnedStreamHTTP(stream.identity.Endpoint(8001, {"process": {}}))
        with self.assertRaises(ValueError):
            stream.OwnedStreamHTTP(object())

    def test_size_limit_and_transport_failure_release_resources(self):
        with server(b"data: " + b"x" * (stream.measurement.MAX_EVENT + 1)), client() as (http, _):
            result = collect(http)
            self.assertEqual(result.status, "size-limit")
            self.assertFalse(http.busy.locked())
        with client() as (http, _), mock.patch.object(stream.local.LocalChatHTTP, "connect",
                side_effect=ConnectionResetError(CANARY)) as connect:
            result = collect(http)
            self.assertEqual(result.status, "transport-error")
            self.assertEqual(connect.call_args.args[0].fileno(), -1)
            self.assertFalse(http.busy.locked())
            self.assertNotIn(CANARY, repr(result))

    def test_non_main_thread_and_busy_instance_cannot_dispatch(self):
        failures = []
        with client() as (http, _), mock.patch.object(socket, "socket") as sock:
            def other_thread():
                try:
                    collect(http)
                except ValueError:
                    failures.append("rejected")
            thread = threading.Thread(target=other_thread)
            thread.start()
            thread.join(1)
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, ["rejected"])
            http.busy.acquire()
            try:
                with self.assertRaises(ValueError):
                    collect(http)
            finally:
                http.busy.release()
            sock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
