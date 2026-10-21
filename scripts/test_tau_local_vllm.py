# SPDX-License-Identifier: Apache-2.0
"""Synthetic local HTTP only. No models, external APIs or paid requests."""

import contextlib
import copy
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import os
import signal
import socket
import threading
import time
import traceback
import unittest
from unittest import mock

import tau_local_vllm as live
import test_tau_transport as fixtures

transport, tau, worker = live.transport, live.tau, live.worker
CANARY = fixtures.CANARY


def counted(count=7, context=4096):
    return {"count": count, "max_model_len": context, "tokens": [5] * count, "token_strs": None}


def completion(model="synthetic-candidate", count=7):
    row = fixtures.response()
    row.update(id="chatcmpl-fixture", object="chat.completion", model=model, created=1)
    row["choices"][0].update(index=0, logprobs=None)
    row["choices"][0]["message"].update(reasoning=None, tool_calls=None)
    row["usage"] = {"prompt_tokens": count, "completion_tokens": 1, "total_tokens": count + 1,
                    "completion_tokens_details": {"reasoning_tokens": 0}}
    return row


@contextlib.contextmanager
def endpoint(replies):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append((self.path, json.loads(request)))
            index = len(seen) - 1
            if index >= len(replies):
                self.send_error(500)
                return
            reply = replies[index]
            if callable(reply):
                reply(self)
                return
            status, headers, raw = reply if isinstance(reply, tuple) else (200, {}, worker.encoded(reply))
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if "Content-Length" not in headers and "Transfer-Encoding" not in headers:
                self.send_header("Content-Length", str(len(raw)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield server.server_port, seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        if thread.is_alive():
            raise AssertionError("local test server failed to stop")


class EndpointTests(unittest.TestCase):
    def body(self):
        return transport.request_body(fixtures.manifest(), "candidate", "agent", fixtures.arguments())[1]

    def test_real_socket_count_and_completion_no_dns_or_proxy(self):
        with endpoint([counted(), completion()]) as (port, seen):
            client = live.LocalVllm(port)
            with mock.patch.dict(os.environ, {"HTTP_PROXY": "http://invalid.invalid:9", "ALL_PROXY": CANARY}), \
                    mock.patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS forbidden")):
                count = client.count(self.body(), 4096, time.monotonic() + 2)
                reply = client.complete(self.body(), count, time.monotonic() + 2)
            self.assertEqual(reply["usage"]["total_tokens"], 8)
            self.assertEqual([row[0] for row in seen], ["/tokenize", "/v1/chat/completions"])
            self.assertEqual(seen[0][1]["messages"], seen[1][1]["messages"])
            self.assertEqual(seen[0][1]["chat_template_kwargs"],
                             {"reasoning_effort": "none", "enable_thinking": False})
            self.assertFalse(seen[0][1]["return_token_strs"])

    def test_valid_chunked_body(self):
        raw = worker.encoded(counted())
        chunks = hex(len(raw))[2:].encode() + b"\r\n" + raw + b"\r\n0\r\n\r\n"
        with endpoint([(200, {"Transfer-Encoding": "chunked"}, chunks)]) as (port, _):
            self.assertEqual(live.LocalVllm(port).count(self.body(), 4096, time.monotonic() + 2), 7)

    def test_redirect_and_error_body_are_not_followed_read_or_exposed(self):
        for code in (301, 302, 307, 400, 401, 429, 500):
            with self.subTest(code=code), endpoint([(code, {"Location": "http://example.invalid"},
                                                    CANARY.encode())]) as (port, seen):
                with self.assertRaises(worker.Abort) as caught:
                    live.LocalVllm(port).count(self.body(), 4096, time.monotonic() + 2)
                self.assertEqual(caught.exception.status, "infrastructure_error" if code in (429, 500)
                                 else "protocol_error")
                self.assertEqual(len(seen), 1)
                self.assertNotIn(CANARY, "".join(traceback.format_exception(caught.exception)))

    def test_response_size_framing_and_json_errors(self):
        replies = [
            (200, {"Content-Length": str(live.MAX_HTTP_RESPONSE + 1)}, b""),
            (200, {"Content-Length": "100"}, b"{}"),
            (200, {"Content-Length": "-1"}, b"{}"),
            (200, {"Content-Length": "2", "Transfer-Encoding": "chunked"}, b"{}"),
            (200, {"Content-Encoding": "gzip"}, b"{}"),
            (200, {"Transfer-Encoding": "gzip"}, b"{}"),
            (200, {}, b'{"count":1,"count":2}'),
            (200, {}, b'{"count":NaN}'),
        ]
        for reply in replies:
            with self.subTest(headers=reply[1]), endpoint([reply]) as (port, _):
                with self.assertRaises(worker.Abort):
                    live.LocalVllm(port).count(self.body(), 4096, time.monotonic() + 2)

    def test_duplicate_header_wrong_media_type_and_unknown_length_limit(self):
        def raw_reply(payload):
            def send(handler):
                try:
                    handler.connection.sendall(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            return send
        for raw in (b"HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\n\r\n{}",
                    b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nContent-Type: application/json\r\n\r\n{}",
                    b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}",
                    b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n" + b" " * (live.MAX_HTTP_RESPONSE + 1)):
            with endpoint([raw_reply(raw)]) as (port, _), self.assertRaises(worker.Abort):
                live.LocalVllm(port).count(self.body(), 4096, time.monotonic() + 2)

    def test_slow_headers_and_body_have_one_whole_deadline(self):
        def drip_headers(handler):
            try:
                for byte in b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n":
                    handler.connection.send(bytes([byte]))
                    time.sleep(0.01)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def drip_body(handler):
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", "200")
            handler.end_headers()
            try:
                for _ in range(200):
                    handler.wfile.write(b" ")
                    handler.wfile.flush()
                    time.sleep(0.01)
            except (BrokenPipeError, ConnectionResetError):
                pass
        previous = signal.getsignal(signal.SIGALRM)
        for reply in (drip_headers, drip_body):
            with endpoint([reply]) as (port, seen):
                start = time.monotonic()
                with self.assertRaises(worker.Abort) as caught:
                    live.LocalVllm(port).count(self.body(), 4096, start + 0.08)
                self.assertEqual(caught.exception.status, "timeout")
                self.assertLess(time.monotonic() - start, 0.7)
                self.assertEqual(len(seen), 1)
        self.assertEqual(signal.getsignal(signal.SIGALRM), previous)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_endpoint_and_deadline_cannot_expand_scope(self):
        for port in (True, 0, 80, 65536, "8000", "https://example.invalid"):
            with self.assertRaises(worker.Abort):
                live.LocalVllm(port)
        client = live.LocalVllm(8000)
        with mock.patch.object(socket, "socket") as sock:
            with self.assertRaises(worker.Abort):
                client.post("/unreviewed", {}, time.monotonic() + 1)
            sock.assert_not_called()
        for deadline in (float("nan"), float("inf"), True, time.monotonic() - 1,
                         time.monotonic() + transport.MAX_WAIT_SECONDS + 10):
            with self.assertRaises(worker.Abort):
                client.remaining(deadline)
        with client.busy, self.assertRaises(worker.Abort):
            client.post("/tokenize", {}, time.monotonic() + 1)

    def test_token_count_must_match_tokens_and_frozen_context(self):
        mutations = [{"count": True}, {"count": 0}, {"tokens": [5]}, {"max_model_len": 8192},
                     {"max_model_len": True}, {"tokens": [False] * 7}, {"tokens": [-1] * 7},
                     {"tokens": [2**63] * 7}, {"token_strs": [CANARY]}, {"extra": CANARY}]
        client = live.LocalVllm(8000)
        for mutation in mutations:
            with self.subTest(fields=list(mutation)), mock.patch.object(client, "post", return_value={**counted(), **mutation}):
                with self.assertRaises(worker.Abort):
                    client.count(self.body(), 4096, time.monotonic() + 1)

    def test_model_count_reasoning_and_refusal_are_not_silently_repaired(self):
        mutations = [lambda row: row.update(model="wrong"), lambda row: row.update(error=CANARY),
                     lambda row: row["usage"].update(prompt_tokens=8),
                     lambda row: row["usage"].update(prompt_tokens=True),
                     lambda row: row["choices"][0].update(index=True),
                     lambda row: row["choices"][0]["message"].update(reasoning=CANARY),
                     lambda row: row["choices"][0]["message"].update(reasoning_content=CANARY),
                     lambda row: row["choices"][0]["message"].update(refusal=CANARY),
                     lambda row: row["choices"][0]["message"].update(audio={"data": CANARY}),
                     lambda row: row["choices"][0]["message"].update(function_call={"name": CANARY}),
                     lambda row: row["usage"]["completion_tokens_details"].update(reasoning_tokens=1),
                     lambda row: row["usage"]["completion_tokens_details"].update(reasoning_tokens=False)]
        client = live.LocalVllm(8000)
        for mutation in mutations:
            row = completion()
            mutation(row)
            with mock.patch.object(client, "post", return_value=row), self.assertRaises(worker.Abort):
                client.complete(self.body(), 7, time.monotonic() + 1)

    def test_tool_and_history_framing_and_unreviewed_choices(self):
        args = fixtures.arguments(tool_choice="auto")
        args["tools"] = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]
        body = transport.request_body(fixtures.manifest(), "candidate", "agent", args)[1]
        self.assertEqual(live.tokenize_body(body)["tools"], body["tools"])
        for choice in ("none", "required", {"type": "function", "function": {"name": "lookup"}}):
            with self.assertRaises(worker.Abort):
                live.tokenize_body({**body, "tool_choice": choice})


class SessionTests(unittest.TestCase):
    def setUp(self):
        first, second = os.pipe()
        third, fourth = os.pipe()
        self.parent_channel = transport.Channel(first, fourth)
        self.child_channel = transport.Channel(third, second)
        self.addCleanup(self.parent_channel.close)
        self.addCleanup(self.child_channel.close)

    def session(self, port, frozen=None):
        frozen = frozen or fixtures.manifest()
        # Other endpoints are deliberately never contacted by these tests.
        others = [p for p in range(31001, 31006) if p != port]
        ports = dict(zip(tau.ROLES, others))
        ports.update(candidate=port)
        parent = live.LocalVllmSession(self.parent_channel, frozen, tau.digest(tau.encoded(frozen)), "candidate", ports)
        self.addCleanup(parent.close)
        return parent

    def exchange(self, parent, args=None, sequence=1, category="agent"):
        self.child_channel.send(fixtures.packet(sequence=sequence, args=args, category=category), time.monotonic() + 2)
        parent.serve_once()
        return self.child_channel.receive(time.monotonic() + 2)["response"]

    def test_complete_through_pipe_and_shared_ledger_with_no_content_logs(self):
        with endpoint([counted(), completion()]) as (port, seen):
            with mock.patch.object(transport.tau, "Budget", wraps=tau.Budget) as budget:
                parent = self.session(port)
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    reply = self.exchange(parent)
                self.assertEqual(budget.call_count, 1)
            self.assertEqual(reply["usage"]["total_tokens"], 8)
            self.assertEqual(parent.bridge.budget.requests, 1)
            self.assertEqual(parent.bridge.budget.tokens, 8)
            self.assertEqual(parent.bridge.usage()["total_tokens"], 8)
            self.assertEqual(parent.sequence, 1)
            self.assertEqual(out.getvalue() + err.getvalue(), "")
            self.assertIsNone(parent.current)
            self.assertNotIn(CANARY, json.dumps(parent.bridge.usage()))
            self.assertEqual(len(seen), 2)

    def test_all_roles_share_one_ledger_but_not_the_tested_endpoint(self):
        evaluation = completion(model="synthetic-evaluator")
        evaluation["choices"][0]["message"]["content"] = '{"results":[]}'
        with contextlib.ExitStack() as stack:
            candidate, _ = stack.enter_context(endpoint([counted(), completion()]))
            simulator, _ = stack.enter_context(endpoint([counted(), completion(model="synthetic-simulator")]))
            evaluator, _ = stack.enter_context(endpoint([counted(), evaluation]))
            reference, _ = stack.enter_context(endpoint([]))
            frozen = fixtures.manifest()
            with mock.patch.object(transport.tau, "Budget", wraps=tau.Budget) as budget:
                parent = live.LocalVllmSession(self.parent_channel, frozen, tau.digest(tau.encoded(frozen)), "candidate",
                    dict(candidate=candidate, reference=reference, simulator=simulator, evaluator=evaluator))
                self.addCleanup(parent.close)
                for sequence, category in enumerate(("agent", "simulator", "evaluator"), 1):
                    self.exchange(parent, sequence=sequence, category=category)
                self.assertEqual(budget.call_count, 1)
            self.assertEqual(parent.bridge.counts, dict(agent=1, simulator=1, evaluator=1))
            self.assertEqual(parent.bridge.budget.requests, 3)
            self.assertEqual(parent.bridge.usage()["total_tokens"], 24)

    def test_count_failure_and_context_overflow_never_generate_or_reserve(self):
        for reply in ((500, {}, CANARY.encode()), counted(101), counted(60, 100)):
            with self.subTest(reply_type=type(reply).__name__), endpoint([reply]) as (port, seen):
                frozen = fixtures.manifest()
                if isinstance(reply, dict) and reply["max_model_len"] == 100:
                    frozen["framing"]["candidate"]["context_tokens"] = 100
                parent = self.session(port, frozen)
                with self.assertRaises(worker.Abort):
                    self.exchange(parent)
                self.assertEqual([row[0] for row in seen], ["/tokenize"])
                self.assertEqual(parent.bridge.budget.requests, 0)
                self.assertEqual(parent.bridge.usage()["total_tokens"], 0)
                # Sessions close failed pipes; each subcase needs a fresh pair.
                parent.close()
                self.child_channel.close()
                self.setUp()

    def test_generation_failure_keeps_reservation_and_sticky_unknown_usage(self):
        with endpoint([counted(), (500, {}, CANARY.encode())]) as (port, seen):
            parent = self.session(port)
            with self.assertRaises(worker.Abort):
                self.exchange(parent)
            self.assertEqual(parent.bridge.budget.requests, 1)
            self.assertEqual(parent.bridge.budget.tokens, 150)
            self.assertIsNone(parent.bridge.usage())
            with self.assertRaises(worker.Abort):
                parent.serve_once()
            self.assertEqual(len(seen), 2)

    def test_count_generation_disagreement_and_malformed_tools_retain_reservation(self):
        wrong_tools = completion()
        wrong_tools["choices"][0].update(finish_reason="tool_calls", message={"role": "assistant", "content": None,
            "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "lookup", "arguments": "{"}}]})
        for row in (completion(count=8), wrong_tools):
            with endpoint([counted(), row]) as (port, _):
                parent = self.session(port)
                with self.assertRaises(worker.Abort):
                    self.exchange(parent)
                self.assertIsNone(parent.bridge.usage())
                self.assertEqual(parent.bridge.budget.tokens, 150)
                parent.close()
                self.child_channel.close()
                self.setUp()

    def test_budget_abort_after_count_never_generates(self):
        with endpoint([counted()]) as (port, seen):
            parent = self.session(port)
            parent.bridge.budget.requests = parent.bridge.budget.limits["requests"]
            with self.assertRaises(worker.Abort) as caught:
                self.exchange(parent)
            self.assertEqual(caught.exception.status, "budget_abort")
            self.assertEqual([row[0] for row in seen], ["/tokenize"])

    def test_generation_is_reserved_before_dispatch_and_count_uses_same_deadline(self):
        with endpoint([counted(), completion()]) as (port, _):
            parent = self.session(port)
            endpoint_client = parent.endpoints["candidate"]
            original = endpoint_client.post
            deadlines = []
            def observe(path, body, deadline):
                deadlines.append(deadline)
                self.assertEqual(parent.bridge.budget.pending is not None, path == "/v1/chat/completions")
                return original(path, body, deadline)
            with mock.patch.object(endpoint_client, "post", side_effect=observe):
                self.exchange(parent)
            self.assertEqual(deadlines[0], deadlines[1])

    def test_truncation_settles_known_usage_without_success(self):
        row = completion()
        row["choices"][0]["finish_reason"] = "length"
        with endpoint([counted(), row]) as (port, _):
            parent = self.session(port)
            with self.assertRaises(worker.Abort) as caught:
                self.exchange(parent)
            self.assertEqual(caught.exception.status, "truncated")
            self.assertEqual(parent.bridge.usage()["total_tokens"], 8)

    def test_tool_roundtrip_and_framing(self):
        call = {"id": "call-1", "type": "function", "function": {"name": "lookup", "arguments": '{"q":"x"}'}}
        row = completion()
        row["choices"][0].update(finish_reason="tool_calls", message={"role": "assistant", "content": None, "tool_calls": [call]})
        args = fixtures.arguments(tool_choice="auto")
        args["tools"] = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]
        with endpoint([counted(), row, counted(9), completion(count=9)]) as (port, seen):
            parent = self.session(port)
            first = self.exchange(parent, args)
            followup = copy.deepcopy(args)
            followup["messages"] += [first["choices"][0]["message"],
                                     {"role": "tool", "tool_call_id": "call-1", "content": CANARY}]
            self.exchange(parent, followup, sequence=2)
            self.assertEqual(seen[2][1]["messages"], seen[3][1]["messages"])
            self.assertEqual(seen[2][1]["tools"], seen[3][1]["tools"])
            self.assertEqual(parent.bridge.usage()["total_tokens"], 18)

    def test_separate_roles_and_no_arbitrary_endpoint_objects(self):
        frozen = fixtures.manifest()
        valid = dict(candidate=31001, reference=31002, simulator=31003, evaluator=31003)
        for bad in ({**valid, "reference": 31001}, {**valid, "simulator": 31001},
                    {**valid, "evaluator": 31002}, {**valid, "candidate": "https://example.invalid"},
                    {**valid, "extra": 31004}):
            with self.assertRaises(worker.Abort):
                live.LocalVllmSession(self.parent_channel, frozen, tau.digest(tau.encoded(frozen)), "candidate", bad)


if __name__ == "__main__":
    unittest.main()
