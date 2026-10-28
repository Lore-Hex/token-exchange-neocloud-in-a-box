# SPDX-License-Identifier: Apache-2.0
"""Real sequence/HTTP/SSE/accounting code over in-memory connections only."""

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
import signal
import time
import unittest
from unittest.mock import patch

import cache_journal as journal
import cache_probe as probe
import test_stream_https as wire


class CacheStreamTests(unittest.TestCase):
    def fixture(self, responses, *, partial_send=False, slow_headers=False):
        with patch.object(probe.stream.bounded_https, "system_trust"):
            client = probe.stream.SelectedHTTPS("candidate.example.test", address="8.8.8.8")
        rows, connections = [], []
        responses = iter(responses)

        @contextmanager
        def connect(deadline):
            connection = wire.Connection(next(responses))
            connections.append(connection)
            send = connection.sendall

            def sendall(value):
                self.assertEqual(rows[-1]["kind"], "intent")
                self.assertEqual(rows[-1]["index"], len(connections) - 1)
                send(value)
                if partial_send:
                    raise OSError("synthetic partial send")

            connection.sendall = sendall
            if slow_headers:
                class Slow(io.BytesIO):
                    def readline(self, *args):
                        while True: time.sleep(.01)
                connection.buffer.close()
                connection.buffer = Slow()
            try:
                yield connection
            finally:
                connection.buffer.close()
                connection.closed = True

        client.connection = connect
        return client, rows, connections

    def run_probe(self, client, rows, *, seconds=2, emit=None):
        return probe.run(client, wire.KEY, padding=2000, prompt_tokens=2048,
            run_id="a" * 64, deadline=time.monotonic() + seconds,
            emit=rows.append if emit is None else emit, check_runtime=lambda: None)

    def audit_prefix(self, rows):
        return journal.audit(b"".join(json.dumps(row).encode() + b"\n" for row in rows))

    def assert_no_content(self, rendered, prompt):
        self.assertIn("CEDAR42", prompt)
        for canary in (wire.CANARY, wire.KEY.decode(), "CEDAR42", prompt):
            self.assertNotIn(canary, rendered)

    def test_complete_sequence_parses_real_streams_and_preserves_scope_order(self):
        client, rows, connections = self.fixture([
            wire.response(wire.data(1024 if index % 3 == 1 else 0)) for index in range(9)])
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            summary = self.run_probe(client, rows)
        self.assertTrue(summary["completed"])
        self.assertEqual(summary["known_prompt_tokens"], 9 * 2048)
        self.assertEqual(summary["known_completion_tokens"], 9 * 2)
        self.assertEqual(summary["known_cached_prompt_tokens"], 3 * 1024)
        self.assertEqual(summary["warm_requests_with_reuse"], 3)
        self.assertEqual(summary["requests_with_unknown_usage"], 0)
        self.assertEqual(client.attempts[probe.stream.transport.PATH], 9)
        self.assertEqual(len(connections), 9)
        bodies = [json.loads(connection.sent[0].split(b"\r\n\r\n", 1)[1]) for connection in connections]
        for index in (0, 3, 6):
            self.assertEqual(bodies[index]["cache_salt"], bodies[index + 1]["cache_salt"])
            self.assertNotEqual(bodies[index]["cache_salt"], bodies[index + 2]["cache_salt"])
        self.assertEqual(len({body["cache_salt"] for body in bodies}), 6)
        self.assertTrue(all(connection.closed and connection.buffer.closed for connection in connections))
        rendered = repr(rows) + out.getvalue() + err.getvalue()
        self.assert_no_content(rendered, bodies[0]["messages"][0]["content"])
        for length in range(1, len(rows)):
            report = self.audit_prefix(rows[:length])
            self.assertEqual(report["known_prompt_tokens"], ((length - 1) // 2) * 2048)
            self.assertEqual(report["orphaned_intents"], int(length % 2 == 0))
        with self.assertRaises(ValueError): self.run_probe(client, rows)
        self.assertEqual(len(connections), 9)

    def test_content_assertion_detects_escaped_prompt_logging(self):
        body = probe.request(2000, 2048, "a" * 64)
        prompt = body["messages"][0]["content"]
        for rendered in (prompt, repr(body), json.dumps(body), wire.CANARY, wire.KEY.decode()):
            with self.subTest(rendered=rendered[:30]), self.assertRaises(AssertionError):
                self.assert_no_content(rendered, prompt)

    def test_late_protocol_failure_keeps_actual_totals_and_never_retries(self):
        for raw, cached in ((wire.data(0).replace(b"data: [DONE]\n\n", b""), 0),
                            (wire.data(-1), None)):
            client, rows, connections = self.fixture([wire.response(raw)])
            summary = self.run_probe(client, rows)
            self.assertFalse(summary["completed"])
            self.assertEqual(summary["stop_reason"], "request-failed")
            self.assertEqual((summary["attempted_requests"], summary["known_prompt_tokens"],
                              summary["known_completion_tokens"]), (1, 2048, 2))
            self.assertEqual(summary["requests_with_unknown_usage"], 0)
            self.assertEqual(rows[-2]["measurement"]["status"], "protocol-error")
            self.assertEqual(rows[-2]["measurement"]["cached_prompt_tokens"], cached)
            report = self.audit_prefix(rows[:-1])
            self.assertEqual(report["known_prompt_tokens"], 2048)
            self.assertEqual(report["unknown_cache_usage_requests"], int(cached is None))
            self.assertEqual(len(connections), 1)

    def test_missing_cache_and_unexpected_reuse_stop_the_combined_path(self):
        for cached, reason in ((None, "cache-usage-missing"), (128, "unexpected-cache-reuse")):
            client, rows, connections = self.fixture([wire.response(wire.data(cached))])
            summary = self.run_probe(client, rows)
            self.assertEqual(summary["stop_reason"], reason)
            self.assertFalse(summary["completed"])
            report = self.audit_prefix(rows[:-1])
            self.assertEqual(report["unknown_cache_usage_requests"], int(cached is None))
            self.assertEqual(report["known_prompt_tokens"], 2048)
            self.assertEqual(len(connections), 1)

    def test_partial_send_and_overload_remain_unknown_without_retry(self):
        for partial, status in ((True, b"200 OK"), (False, b"503 Unavailable")):
            client, rows, connections = self.fixture([wire.response(status=status)], partial_send=partial)
            summary = self.run_probe(client, rows)
            self.assertEqual(summary["attempted_requests"], 1)
            self.assertEqual(summary["requests_with_unknown_usage"], 1)
            self.assertEqual(summary["known_prompt_tokens"], 0)
            report = self.audit_prefix(rows[:-1])
            self.assertEqual(report["unknown_usage_requests"], 1)
            self.assertEqual(len(connections), 1)
            self.assertTrue(connections[0].closed)

    def test_failed_receipt_stops_before_send_or_leaves_an_orphan(self):
        for kind, sends, known in (("intent", 0, 0), ("result", 1, 0), ("summary", 9, 9 * 2048)):
            client, rows, connections = self.fixture([wire.response(wire.data(0))] * 9)
            def emit(row):
                if row["kind"] == kind:
                    raise OSError("synthetic journal failure")
                rows.append(row)
            with self.subTest(kind=kind), self.assertRaises(OSError):
                self.run_probe(client, rows, emit=emit)
            self.assertEqual(sum(len(connection.sent) for connection in connections), sends)
            report = self.audit_prefix(rows)
            self.assertEqual(report["known_prompt_tokens"], known)
            self.assertEqual(report["orphaned_intents"], int(kind == "result"))
            self.assertFalse(report["run_completed"] or report["accounting_holds_released"])
            self.assertTrue(all(connection.closed for connection in connections))

    def test_nested_deadline_stops_slow_headers_and_restores_alarm(self):
        client, rows, connections = self.fixture([wire.response()], slow_headers=True)
        previous = signal.getsignal(signal.SIGALRM)
        started = time.monotonic()
        summary = self.run_probe(client, rows, seconds=.05)
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(summary["stop_reason"], "deadline")
        self.assertEqual(summary["attempted_requests"], 1)
        self.assertEqual(rows[-2]["measurement"]["status"], "timeout")
        self.assertEqual(self.audit_prefix(rows[:-1])["unknown_usage_requests"], 1)
        self.assertEqual(len(connections), 1)
        self.assertTrue(connections[0].closed and connections[0].buffer.closed)
        self.assertIs(signal.getsignal(signal.SIGALRM), previous)
        self.assertFalse(client.used)


if __name__ == "__main__":
    unittest.main()
