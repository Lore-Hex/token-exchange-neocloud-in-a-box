# SPDX-License-Identifier: Apache-2.0
"""Synthetic interrupted-journal checks; no network, inference or credentials."""

import copy
import hashlib
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import cache_journal as journal
import test_cache_probe as fixtures


class JournalTests(unittest.TestCase):
    def records(self):
        fixture = fixtures.ProbeTests()
        client, rows, _ = fixture.setup_run()
        fixture.run_probe(client, rows)
        return rows

    def encoded(self, rows):
        return b"".join(json.dumps(row, separators=(",", ":")).encode() + b"\n" for row in rows)

    def audit(self, rows):
        return journal.audit(self.encoded(rows))

    def test_every_intact_interrupted_prefix_counts_orphans_without_recovery(self):
        rows = self.records()
        for length in range(1, len(rows)):
            with self.subTest(length=length):
                prefix = rows[:length]
                result = self.audit(prefix)
                attempts = length // 2
                completed = (length - 1) // 2
                self.assertEqual(result["started_or_uncertain_requests"], attempts)
                self.assertEqual(result["recorded_attempted_results"], completed)
                self.assertEqual(result["orphaned_intents"], attempts - completed)
                self.assertEqual(result["unknown_usage_requests"], attempts - completed)
                self.assertEqual(result["requests_without_send_evidence"], 9 - attempts)
                self.assertEqual(result["known_prompt_tokens"], 2048 * completed)
                self.assertEqual(result["source_sha256"], hashlib.sha256(self.encoded(prefix)).hexdigest())
                self.assertEqual(prefix, rows[:length])
                self.assertTrue(all(result[name] is False for name in (
                    "terminal_present", "run_completed", "source_authenticated", "billing_reconciled",
                    "accounting_holds_released", "tariff_change_justified", "inference_performed")))

    def test_missing_usage_and_missing_cache_remain_unknown(self):
        for unknown_totals in (True, False):
            rows = self.records()[:3]
            value = rows[-1]["measurement"]
            value["cached_prompt_tokens"] = None
            if unknown_totals:
                value.update(status="transport-error", ttft_seconds=None,
                             mean_output_token_interval_estimate_seconds=None,
                             prompt_tokens=None, completion_tokens=None)
            report = self.audit(rows)
            self.assertEqual(report["unknown_cache_usage_requests"], 1)
            self.assertEqual(report["unknown_usage_requests"], int(unknown_totals))
            self.assertEqual(report["known_prompt_tokens"], 0 if unknown_totals else 2048)

    def test_late_failure_keeps_totals_above_prepared_prompt_count(self):
        rows = self.records()[:3]
        rows[-1]["measurement"].update(status="protocol-error", ttft_seconds=None,
                                        mean_output_token_interval_estimate_seconds=None,
                                        prompt_tokens=4096, cached_prompt_tokens=128)
        report = self.audit(rows)
        self.assertEqual(report["known_prompt_tokens"], 4096)
        self.assertEqual(report["known_cached_prompt_tokens"], 128)
        self.assertEqual(report["prompt_count_mismatches"], 1)
        self.assertEqual(report["completed_result_records"], 0)

    def test_presend_failure_has_no_intent_or_usage(self):
        rows = self.records()[:3]
        rows.pop(1)
        rows[-1]["attempted"] = False
        rows[-1]["measurement"].update(status="transport-error", ttft_seconds=None,
            first_content_seconds=None, mean_output_token_interval_estimate_seconds=None,
            output_events=0, prompt_tokens=None, completion_tokens=None,
            cached_prompt_tokens=None, finish_reason=None)
        report = self.audit(rows)
        self.assertEqual(report["recorded_results"], 1)
        self.assertEqual(report["started_or_uncertain_requests"], 0)
        self.assertEqual(report["requests_without_send_evidence"], 9)
        with self.assertRaises(ValueError): self.audit(rows + self.records()[3:4])
        # An interrupt can arrive after intent fsync but before the transport
        # sets its attempted flag. The receipt must not erase that uncertainty.
        rows.insert(1, self.records()[1])
        report = self.audit(rows)
        self.assertEqual(report["unconfirmed_presend_intents"], 1)
        self.assertEqual(report["unknown_usage_requests"], 1)
        self.assertEqual(report["started_or_uncertain_requests"], 1)
        self.assertEqual(report["orphaned_intents"], 0)

    def test_rejects_mixed_reordered_duplicate_and_completed_journals(self):
        rows = self.records()
        cases = [rows, rows + rows, rows[1:3], rows[:1] + rows[2:3], rows[:2] + rows[1:2],
                 rows[:3] + rows[1:3], rows[:2] + rows[4:5]]
        for key, value in (("run_id", "b" * 64), ("index", True), ("repeat", 1),
                           ("phase", "warm"), ("attempted", False), ("private_payload", "not-allowed")):
            altered = copy.deepcopy(rows[:3])
            altered[-1][key] = value
            cases.append(altered)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError): self.audit(value)

    def test_stopping_results_cannot_be_followed_by_another_request(self):
        for change in ({"status": "timeout", "ttft_seconds": None,
                        "mean_output_token_interval_estimate_seconds": None}, {"cached_prompt_tokens": None},
                       {"cached_prompt_tokens": 128}):
            rows = self.records()[:4]
            rows[2]["measurement"].update(change)
            self.audit(rows[:3])
            with self.assertRaises(ValueError): self.audit(rows)

    def test_numeric_schema_and_framing_fail_closed(self):
        rows = self.records()[:3]
        for name, value in (("concurrency", True), ("reserved_total_tokens", 0),
                            ("prompt_tokens_per_request", 123), ("run_seconds_cap", 9999),
                            ("messages_sha256", "private-data")):
            altered = copy.deepcopy(rows)
            altered[0][name] = value
            with self.subTest(name=name), self.assertRaises(ValueError): self.audit(altered)
        for name, value in (("elapsed_seconds", -1), ("elapsed_seconds", float("nan")),
                            ("completion_tokens", True), ("completion_tokens", 513),
                            ("cached_prompt_tokens", 2049), ("prompt_tokens", 4096),
                            ("output_events", -1), ("status", "private-data"),
                            ("ttft_seconds", 2), ("body", "private-data")):
            altered = copy.deepcopy(rows)
            altered[-1]["measurement"][name] = value
            with self.subTest(name=name), self.assertRaises(ValueError): self.audit(altered)
        raw = self.encoded(rows)
        for value in (b"", raw[:-1], raw + b"partial", raw + b"\n", b"x" * (journal.LIMIT + 1),
                      b"[]\n", b'{"run_id":"a","run_id":"b"}\n', raw.replace(b'"kind":', b'"kind":0,"kind":', 1)):
            with self.subTest(raw=value[:30]), self.assertRaises(ValueError): journal.audit(value)

    def test_cli_is_bounded_and_does_not_echo_bad_input(self):
        for raw, expected in ((self.encoded(self.records()[:2]), 0), (b"SENSITIVE_TEST_CONTENT\n", 1)):
            source = io.TextIOWrapper(io.BytesIO(raw))
            with patch.object(sys, "argv", ["cache_journal.py"]), patch.object(sys, "stdin", source), \
                    redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                self.assertEqual(journal.main(), expected)
            self.assertNotIn("SENSITIVE_TEST_CONTENT", out.getvalue() + err.getvalue())
            if not expected:
                self.assertEqual(json.loads(out.getvalue())["orphaned_intents"], 1)


if __name__ == "__main__":
    unittest.main()
