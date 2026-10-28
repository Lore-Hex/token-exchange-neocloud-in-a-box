# SPDX-License-Identifier: Apache-2.0
"""Offline scheduling/accounting tests; no endpoints, keys or GPU work."""

from dataclasses import replace
import io
from contextlib import redirect_stderr, redirect_stdout
import time
import unittest
from unittest.mock import Mock, patch

import cache_probe as probe


class ProbeTests(unittest.TestCase):
    def setup_run(self, outcomes=None):
        with patch.object(probe.stream.bounded_https, "system_trust"):
            client = probe.stream.SelectedHTTPS("candidate.example.test", address="8.8.8.8")
        records, bodies = [], []
        outcomes = iter(outcomes) if outcomes is not None else None
        def collect(body, credential, *, prompt_tokens, deadline, before_send):
            before_send()
            self.assertEqual(records[-1]["kind"], "intent")
            bodies.append(body)
            client.last_attempt_started = True
            client.attempts[probe.stream.transport.PATH] += 1
            sample = probe.meter.Sample("completed", 1, .1, .1, .9, 2,
                                        prompt_tokens, 64, 1920 if len(bodies) % 3 == 2 else 0, "stop")
            return next(outcomes) if outcomes is not None else sample
        client.collect = Mock(side_effect=collect)
        return client, records, bodies

    def run_probe(self, client, records, **kwargs):
        values = {"padding": 2000, "prompt_tokens": 2048, "run_id": "a" * 64,
                  "deadline": time.monotonic() + 30, "emit": records.append,
                  "check_runtime": lambda: None}
        values.update(kwargs)
        return probe.run(client, b"SYNTHETIC_NOT_A_REAL_KEY", **values)

    def sample(self, **kwargs):
        return replace(probe.meter.Sample("completed", 1, .1, .1, .9, 2, 2048, 64, 0, "stop"), **kwargs)

    def test_three_triplets_bound_scopes_receipts_and_numeric_summary(self):
        client, records, bodies = self.setup_run()
        check = Mock()
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            summary = self.run_probe(client, records, check_runtime=check)
        self.assertTrue(summary["completed"])
        self.assertEqual(summary["attempted_requests"], 9)
        self.assertEqual(summary["known_prompt_tokens"], 9 * 2048)
        self.assertEqual(summary["known_completion_tokens"], 9 * 64)
        self.assertEqual(summary["known_cached_prompt_tokens"], 3 * 1920)
        self.assertEqual(summary["warm_requests_with_reuse"], 3)
        self.assertEqual(summary["not_started_requests"], 0)
        self.assertEqual(summary["requests_with_unknown_usage"], 0)
        self.assertEqual(check.call_count, 9)
        self.assertEqual([row["kind"] for row in records], ["manifest"] + ["intent", "result"] * 9 + ["summary"])
        salts = [row["cache_salt"] for row in bodies]
        self.assertEqual(len(set(salts)), 6)
        for index in (0, 3, 6):
            self.assertEqual(salts[index], salts[index + 1])
            self.assertNotEqual(salts[index], salts[index + 2])
        self.assertTrue(all(body["messages"] == bodies[0]["messages"] for body in bodies))
        self.assertNotIn("SYNTHETIC_NOT_A_REAL_KEY", repr(records) + out.getvalue() + err.getvalue())
        self.assertNotIn("List consecutive integers", repr(records))
        self.assertTrue(all(not summary[name] for name in (
            "billing_reconciled", "cache_isolation_qualified", "quality_qualified", "confidential", "tariff_change_justified")))
        with self.assertRaises(ValueError): self.run_probe(client, records)
        self.assertEqual(len(bodies), 9)

    def test_missing_usage_and_known_late_failure_stop_without_retry(self):
        for sample, unknown, known in (
            (self.sample(status="transport-error", prompt_tokens=None, completion_tokens=None, cached_prompt_tokens=None), 1, 0),
            (self.sample(status="protocol-error", cached_prompt_tokens=128), 0, 2048)):
            client, records, bodies = self.setup_run([sample])
            summary = self.run_probe(client, records)
            self.assertEqual(len(bodies), 1)
            self.assertEqual(summary["stop_reason"], "request-failed")
            self.assertEqual(summary["requests_with_unknown_usage"], unknown)
            self.assertEqual(summary["known_prompt_tokens"], known)
            self.assertEqual(summary["not_started_requests"], 8)

    def test_missing_cache_usage_is_not_assumed_zero(self):
        client, records, _ = self.setup_run([self.sample(cached_prompt_tokens=None)])
        summary = self.run_probe(client, records)
        self.assertEqual(summary["stop_reason"], "cache-usage-missing")
        self.assertEqual(summary["requests_with_unknown_usage"], 0)
        self.assertEqual(summary["requests_with_unknown_cache_usage"], 1)
        self.assertIsNone(records[-2]["measurement"]["cached_prompt_tokens"])

    def test_cold_and_negative_reuse_stop_but_warm_miss_is_recorded(self):
        for outcomes, expected in (([self.sample(cached_prompt_tokens=128)], 1),
                                   ([self.sample(), self.sample(), self.sample(cached_prompt_tokens=128)], 3)):
            client, records, bodies = self.setup_run(outcomes)
            summary = self.run_probe(client, records)
            self.assertEqual(summary["stop_reason"], "unexpected-cache-reuse")
            self.assertEqual(len(bodies), expected)
        client, records, _ = self.setup_run([self.sample()] * 9)
        summary = self.run_probe(client, records)
        self.assertTrue(summary["completed"])
        self.assertEqual(summary["warm_requests_with_reuse"], 0)

    def test_receipt_failures_never_start_another_request(self):
        for failed_kind, expected_sends in (("manifest", 0), ("intent", 0), ("result", 1), ("summary", 9)):
            client, records, bodies = self.setup_run()
            def emit(row):
                if row["kind"] == failed_kind:
                    raise OSError("private receipt failure")
                records.append(row)
            with self.subTest(kind=failed_kind), self.assertRaises(OSError):
                self.run_probe(client, records, emit=emit)
            self.assertEqual(len(bodies), expected_sends)
            if failed_kind == "result":
                self.assertEqual(records[-1]["kind"], "intent")

    def test_tls_failure_is_not_started_and_preflight_failure_sends_nothing(self):
        client, records, bodies = self.setup_run()
        client.collect = Mock(return_value=self.sample(status="transport-error", prompt_tokens=None,
                                                       completion_tokens=None, cached_prompt_tokens=None))
        summary = self.run_probe(client, records)
        self.assertEqual(summary["attempted_requests"], 0)
        self.assertEqual(summary["not_started_requests"], 9)
        self.assertEqual(summary["requests_with_unknown_usage"], 0)
        self.assertNotIn("intent", [row["kind"] for row in records])
        client, records, bodies = self.setup_run()
        summary = self.run_probe(client, records, check_runtime=Mock(side_effect=ValueError("private identity")))
        self.assertEqual(summary["stop_reason"], "preflight-failed")
        client.collect.assert_not_called()

    def test_bounds_and_namespace_collision_reject_before_transport(self):
        for update in ({"prompt_tokens": True}, {"prompt_tokens": 2049}, {"padding": -1},
                       {"run_id": "not-hex"}, {"emit": None}, {"check_runtime": None}, {"deadline": 0}):
            client, records, _ = self.setup_run()
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.run_probe(client, records, **update)
            client.collect.assert_not_called()
            self.assertEqual(records, [])
        client, records, _ = self.setup_run()
        with patch.object(probe.secrets, "token_hex", return_value="b" * 64), self.assertRaises(ValueError):
            self.run_probe(client, records)
        client.collect.assert_not_called()

    def test_deadline_stops_before_second_request_and_preserves_first(self):
        client, records, bodies = self.setup_run()
        original = client.collect.side_effect
        def slow(*args, **kwargs):
            sample = original(*args, **kwargs)
            time.sleep(.05)
            return sample
        client.collect.side_effect = slow
        summary = self.run_probe(client, records, deadline=time.monotonic() + .03)
        self.assertEqual(summary["stop_reason"], "deadline")
        self.assertEqual(len(bodies), 1)
        self.assertEqual(summary["known_prompt_tokens"], 2048)


if __name__ == "__main__":
    unittest.main()
