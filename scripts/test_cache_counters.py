# SPDX-License-Identifier: Apache-2.0
"""Synthetic metrics only; no endpoint, inference or credential access."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

import cache_counters as counters

MODEL = "synthetic/model"


def fixture(queries=("100", "300"), hits=("80", "120")):
    return "".join(
        f'{name}{{engine="{rank}",model_name="{MODEL}"}} {values[rank]}\n'
        for name, values in zip(counters.METRICS, (queries, hits))
        for rank in (0, 1)).encode()


class CacheCountersTests(unittest.TestCase):
    def test_exact_totals_and_weighted_fraction(self):
        raw = fixture()
        result = counters.parse(raw, MODEL)
        self.assertEqual(result["ranks"], [
            {"rank": 0, "queried_tokens": 100, "cached_tokens": 80},
            {"rank": 1, "queried_tokens": 300, "cached_tokens": 120}])
        self.assertEqual((result["queried_tokens"], result["cached_tokens"]), (400, 200))
        self.assertEqual(result["cached_fraction"], .5)
        self.assertEqual(result["source_metrics_sha256"], hashlib.sha256(raw).hexdigest())
        for key in ("snapshot_atomic", "source_identity_verified", "customer_cache_rate_verified",
                    "billing_reconciled", "performance_qualified", "tariff_change_justified"):
            self.assertIs(result[key], False)

    def test_zero_query_is_unknown_not_zero_hit_rate(self):
        self.assertIsNone(counters.parse(fixture(("0", "0"), ("0", "0")), MODEL)["cached_fraction"])
        self.assertEqual(counters.parse(fixture(hits=("0", "0")), MODEL)["cached_fraction"], 0)

    def test_exact_decimal_and_exponent_counter_values(self):
        self.assertEqual(counters.parse(fixture(("1e2", "+300.00")), MODEL)["queried_tokens"], 400)
        bound = str(2**53 - 1)
        self.assertEqual(counters.parse(fixture((bound, "0"), (bound, "0")), MODEL)["cached_tokens"], 2**53 - 1)

    def test_rejects_missing_duplicate_foreign_and_malformed_series(self):
        raw = fixture()
        first = raw.splitlines(keepends=True)[0]
        invalid = [raw[len(first):], raw + first,
                   raw.replace(b'engine="0"', b'engine="2"'),
                   raw.replace(b'engine="0"', b'engine="00"'),
                   raw.replace(MODEL.encode(), b"other"),
                   raw.replace(b'engine="0",', b'engine="0",engine="0",'),
                   raw.replace(b'engine="0",', b'engine="0",extra="x",'),
                   raw.replace(b'engine="0",', b""),
                   raw.replace(b" 100\n", b" 100 1234\n"),
                   raw + b"vllm:prefix_cache_hits_total 1\n",
                   raw + b"vllm:prefix_cache_queries_total\n"]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                counters.parse(value, MODEL)

    def test_rejects_noninteger_unsafe_and_inconsistent_counts(self):
        for value in ("NaN", "Inf", "-1", ".5", "0.5", "99.99999999999999999",
                      "1_000", "true", str(2**53), "1e9999", "1e999999999", "1" * 129):
            with self.subTest(value=value), self.assertRaises(ValueError):
                counters.parse(fixture((value, "300")), MODEL)
        for raw in (fixture(hits=("101", "120")),
                    fixture((str(2**53 - 1), "1"), ("0", "0"))):
            with self.assertRaises(ValueError):
                counters.parse(raw, MODEL)

    def test_no_raw_or_model_labels_in_output(self):
        raw = fixture() + b'unrelated{private_label="PRIVATE_CANARY"} 123\n'
        result = counters.parse(raw, MODEL)
        encoded = json.dumps(result)
        self.assertNotIn("PRIVATE_CANARY", encoded)
        self.assertNotIn(MODEL, encoded)
        self.assertEqual(result["source_metrics_sha256"], hashlib.sha256(raw).hexdigest())

    def test_input_and_model_bounds(self):
        for raw in (b"", b"x" * (counters.capacity.LIMIT + 1), b"\xff", "text"):
            with self.assertRaises((ValueError, UnicodeError)):
                counters.parse(raw, MODEL)
        for model in (None, "", "x" * 257, "x\ny", "x\ry"):
            with self.assertRaises(ValueError):
                counters.parse(fixture(), model)

    def test_cli_no_sensitive_error_echo(self):
        command = [sys.executable, "-I", "-S", "-B", str(Path(counters.__file__)), "--model", MODEL]
        result = subprocess.run(command, input=fixture(), capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), counters.parse(fixture(), MODEL))
        result = subprocess.run(command, input=fixture().replace(b" 100\n", b" PRIVATE_CANARY\n"),
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"cache counter input rejected\n")


if __name__ == "__main__":
    unittest.main()
