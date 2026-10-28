# SPDX-License-Identifier: Apache-2.0
"""Synthetic complete-journal reconciliation; no network, credentials or inference."""

import copy
import hashlib
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import cache_complete as complete
import test_cache_journal as fixtures


class CompleteTests(unittest.TestCase):
    def records(self):
        rows = fixtures.JournalTests().records()
        # Fixture samples each declare one second without waiting for a second.
        rows[-1]["elapsed_seconds"] = 10
        return rows

    def encoded(self, rows):
        return fixtures.JournalTests().encoded(rows)

    def audit(self, rows):
        return complete.audit(self.encoded(rows))

    def test_complete_chain_recomputes_totals_without_qualifying_runtime(self):
        rows = self.records()
        original = copy.deepcopy(rows)
        report = self.audit(rows)
        self.assertEqual(rows, original)
        self.assertEqual(report["source_sha256"], hashlib.sha256(self.encoded(rows)).hexdigest())
        self.assertEqual(report["journal_records"], 20)
        self.assertEqual(report["known_prompt_tokens"], 9 * 2048)
        self.assertEqual(report["known_completion_tokens"], 9 * 64)
        self.assertEqual(report["known_cached_prompt_tokens"], 3 * 1920)
        self.assertEqual(report["warm_requests_with_reuse"], 3)
        self.assertTrue(report["terminal_present"] and report["recorded_sequence_completed"])
        for name in ("source_authenticated", "billing_reconciled", "accounting_holds_released",
                     "cache_isolation_qualified", "tariff_change_justified", "quality_qualified",
                     "confidential", "inference_performed"):
            self.assertIs(report[name], False)
        self.assertNotIn("run_completed", report)

    def test_warm_misses_are_valid_not_dropped_or_retried(self):
        rows = self.records()
        for row in rows[2:-1:2]:
            row["measurement"]["cached_prompt_tokens"] = 0
        rows[-1].update(known_cached_prompt_tokens=0, warm_requests_with_reuse=0)
        report = self.audit(rows)
        self.assertEqual(report["known_cached_prompt_tokens"], 0)
        self.assertEqual(report["completed_result_records"], 9)

    def test_every_terminal_counter_and_boolean_is_exact(self):
        rows = self.records()
        for name, old in rows[-1].items():
            if type(old) not in (int, bool) or name == "elapsed_seconds":
                continue
            for replacement in ((not old, int(old)) if type(old) is bool else (old + 1, True)):
                altered = copy.deepcopy(rows)
                altered[-1][name] = replacement
                with self.subTest(name=name, value=replacement), self.assertRaises(ValueError):
                    self.audit(altered)

    def test_final_negative_reuse_is_not_success_even_with_matching_totals(self):
        rows = self.records()
        rows[-2]["measurement"]["cached_prompt_tokens"] = 1
        rows[-1]["known_cached_prompt_tokens"] += 1
        with self.assertRaises(ValueError): self.audit(rows)

    def test_missing_usage_and_late_failure_cannot_be_hidden_by_summary(self):
        for update in ({"cached_prompt_tokens": None}, {"prompt_tokens": 4096},
                       {"status": "protocol-error", "ttft_seconds": None,
                        "first_content_seconds": None,
                        "mean_output_token_interval_estimate_seconds": None}):
            rows = self.records()
            rows[-2]["measurement"].update(update)
            with self.subTest(update=update), self.assertRaises(ValueError): self.audit(rows)

    def test_interrupted_mixed_reordered_and_duplicated_chains_reject(self):
        rows = self.records()
        cases = [rows[:length] for length in range(len(rows))]
        cases += [rows + rows, rows + [rows[-1]], rows[:2] + rows[3:],
                  rows[:3] + [rows[1]] + rows[4:]]
        for altered in cases:
            with self.subTest(length=len(altered)), self.assertRaises(ValueError): self.audit(altered)
        for index in (0, 1, 2, -1):
            altered = copy.deepcopy(rows)
            altered[index]["run_id"] = "b" * 64
            with self.subTest(index=index), self.assertRaises(ValueError): self.audit(altered)
        with self.assertRaises(ValueError): complete.journal.audit(self.encoded(rows))

    def test_summary_schema_and_serial_elapsed_bounds_fail_closed(self):
        for name, value in (("kind", "intent"), ("schema", "other"), ("stop_reason", "deadline"),
                            ("private_payload", "SENSITIVE_TEST_CONTENT"), ("elapsed_seconds", 8.9),
                            ("elapsed_seconds", 1200), ("elapsed_seconds", True),
                            ("elapsed_seconds", -1), ("elapsed_seconds", float("nan"))):
            rows = self.records()
            rows[-1][name] = value
            with self.subTest(name=name, value=value), self.assertRaises(ValueError): self.audit(rows)
        rows = self.records()
        del rows[-1]["known_prompt_tokens"]
        with self.assertRaises(ValueError): self.audit(rows)

    def test_raw_framing_duplicate_keys_and_size_bounds(self):
        raw = self.encoded(self.records())
        for value in (b"", raw[:-1], raw + b"partial", raw + b"\n",
                      b"x" * (complete.journal.LIMIT + 1), b"[]\n",
                      raw.replace(b'"completed":true', b'"completed":false,"completed":true')):
            with self.subTest(value=value[:20]), self.assertRaises(ValueError): complete.audit(value)

    def test_successful_request_must_finish_before_collector_deadline(self):
        for elapsed in (118.999, 119, 120):
            rows = self.records()
            rows[2]["measurement"]["elapsed_seconds"] = elapsed
            rows[-1]["elapsed_seconds"] = elapsed + 9
            with self.subTest(elapsed=elapsed):
                if elapsed < 119:
                    self.assertTrue(self.audit(rows)["recorded_sequence_completed"])
                else:
                    with self.assertRaises(ValueError): self.audit(rows)

    def test_cli_fixed_errors_and_no_extra_arguments(self):
        for raw, args, expected in ((self.encoded(self.records()), [], 0),
                                   (b"SENSITIVE_TEST_CONTENT\n", [], 1),
                                   (self.encoded(self.records()), ["SENSITIVE_TEST_CONTENT"], 1)):
            source = io.TextIOWrapper(io.BytesIO(raw))
            with patch.object(sys, "argv", ["cache_complete.py", *args]), \
                    patch.object(sys, "stdin", source), redirect_stdout(io.StringIO()) as out, \
                    redirect_stderr(io.StringIO()) as err:
                self.assertEqual(complete.main(), expected)
            self.assertNotIn("SENSITIVE_TEST_CONTENT", out.getvalue() + err.getvalue())
            if expected == 0:
                self.assertTrue(json.loads(out.getvalue())["recorded_sequence_completed"])


if __name__ == "__main__":
    unittest.main()
