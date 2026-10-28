# SPDX-License-Identifier: Apache-2.0
import copy
from fractions import Fraction
import io
import json
import unittest
from unittest.mock import patch

import cache_economics as economics


def request(status="completed", prompt=1000, output=100, cached=800):
    return {"status": status, "prompt_tokens": prompt,
            "completion_tokens": output, "cached_prompt_tokens": cached}


def fixture(*rows):
    return {"schema": "nib.cache-economics-input/v1", "measurement_source_sha256": "a" * 64,
        "window_seconds": "60", "fleet_usd_per_hour": "60",
        "input_usd_per_million": "2", "output_usd_per_million": "10",
        "cache_rates_usd_per_million": ["2", "0.5", "0"],
        "max_prompt_tokens": 1000, "max_output_tokens": 100,
        "planned_requests": len(rows), "not_started_requests": 0, "requests": list(rows)}


class CacheEconomicsTests(unittest.TestCase):
    def test_cache_subtracts_from_full_input_instead_of_being_added(self):
        report = economics.analyze(fixture(request()))
        unchanged, discount, free = report["scenarios"]
        for row, expected in ((unchanged, "0.003000000000"),
                              (discount, "0.001800000000"), (free, "0.001400000000")):
            self.assertEqual(row["completed_request_gross_projection_usd"], {"lower": expected, "upper": expected})
        self.assertEqual(report["known_prompt_tokens"], 1000)
        self.assertEqual(report["known_cached_prompt_tokens"], 800)
        self.assertEqual(report["full_window_fleet_allocation_usd"], {"lower": "1.000000000000", "upper": "1.000000000000"})
        self.assertEqual(discount["completed_gross_minus_full_window_allocation_usd"],
                         {"lower": "-0.998200000000", "upper": "-0.998200000000"})

    def test_missing_cache_is_a_range_not_assumed_zero_or_full(self):
        report = economics.analyze(fixture(request(cached=None)))
        self.assertEqual(report["scenarios"][1]["completed_request_gross_projection_usd"],
                         {"lower": "0.001500000000", "upper": "0.003000000000"})
        self.assertEqual(report["unknown_cache_usage_requests"], 1)
        self.assertEqual(report["unknown_total_usage_requests"], 0)

    def test_failed_and_orphan_work_is_not_revenue_or_free(self):
        report = economics.analyze(fixture(request("failed"), request("unresolved", None, None, None)))
        row = report["scenarios"][1]
        self.assertEqual(row["completed_request_gross_projection_usd"], {"lower": "0.000000000000", "upper": "0.000000000000"})
        self.assertEqual(row["failed_or_unresolved_tariff_equivalent_usd"],
                         {"lower": "0.001800000000", "upper": "0.004800000000"})
        self.assertEqual(report["unknown_total_usage_requests"], 1)
        self.assertEqual(report["failed_or_unresolved_requests"], 2)
        self.assertEqual(report["known_completion_tokens"], 100)

    def test_unknown_usage_is_not_invented_on_completed_status(self):
        report = economics.analyze(fixture(request(prompt=None, output=None, cached=None)))
        self.assertEqual(report["scenarios"][0]["completed_request_gross_projection_usd"],
                         {"lower": "0.000000000000", "upper": "0.003000000000"})

    def test_idle_window_is_included_without_dividing_by_replicas(self):
        data = fixture(request())
        data.update(planned_requests=2, not_started_requests=1, window_seconds="3600")
        report = economics.analyze(data)
        self.assertEqual(report["full_window_fleet_allocation_usd"]["lower"], "60.000000000000")
        self.assertEqual(report["started_or_uncertain_requests"], 1)
        self.assertEqual(report["not_started_requests"], 1)
        data["requests"] = []
        data["not_started_requests"] = 2
        self.assertEqual(economics.analyze(data)["full_window_fleet_allocation_usd"]["upper"], "60.000000000000")

    def test_decimal_math_bounds_round_outwards_even_when_negative(self):
        self.assertEqual(economics.bounds(Fraction(-1, 3), Fraction(1, 3)),
                         {"lower": "-0.333333333334", "upper": "0.333333333334"})
        data = fixture(request(prompt=1, output=1, cached=1))
        data.update(input_usd_per_million="0.000000001", output_usd_per_million="0",
                    cache_rates_usd_per_million=["0.000000001"], window_seconds="0.000000001")
        report = economics.analyze(data)
        self.assertEqual(report["scenarios"][0]["completed_request_gross_projection_usd"],
                         {"lower": "0.000000000000", "upper": "0.000000000001"})

    def test_cache_price_increase_or_ambiguous_numeric_input_rejected(self):
        for bad in ("NaN", "Infinity", "-1", "1e2", "01", " 1", "1.0000000001", 0.5, True, "1000001"):
            with self.subTest(bad=bad):
                data = fixture(request())
                data["input_usd_per_million"] = bad
                with self.assertRaises(ValueError):
                    economics.analyze(data)
        for rates in ([], ["2", "2.0"], ["2.1"], ["1"] * 9):
            with self.subTest(rates=rates):
                data = fixture(request())
                data["cache_rates_usd_per_million"] = rates
                with self.assertRaises(ValueError):
                    economics.analyze(data)

    def test_request_accounting_and_bounds_are_strict(self):
        updates = [{"planned_requests": True}, {"planned_requests": 0}, {"planned_requests": 10001},
                   {"not_started_requests": 1}, {"window_seconds": "0"}, {"window_seconds": "604801"},
                   {"max_prompt_tokens": 1048577}, {"max_output_tokens": 393217},
                   {"measurement_source_sha256": "unverified"}, {"extra": "never printed"}]
        for update in updates:
            with self.subTest(update=update):
                data = fixture(request())
                data.update(update)
                with self.assertRaises(ValueError):
                    economics.analyze(data)
        for update in ({"cached_prompt_tokens": 1001}, {"cached_prompt_tokens": True},
                       {"prompt_tokens": None}, {"completion_tokens": -1}, {"prompt_tokens": 1001},
                       {"status": "ignored"}, {"content": "private content"}):
            with self.subTest(update=update):
                data = fixture(request())
                data["requests"][0].update(update)
                with self.assertRaises(ValueError):
                    economics.analyze(data)

    def test_no_qualification_inference_or_mutation_claims(self):
        data = fixture(request())
        original = copy.deepcopy(data)
        report = economics.analyze(data)
        self.assertEqual(data, original)
        for claim in ("measurement_source_verified", "settlement_verified", "marginal_cost_measured",
                      "sustainable_tariff_qualified", "tariff_changed"):
            self.assertIs(report[claim], False)
        changed = copy.deepcopy(data)
        changed["requests"][0]["cached_prompt_tokens"] = 0
        self.assertNotEqual(report["input_sha256"], economics.analyze(changed)["input_sha256"])

    def test_cli_rejects_duplicate_keys_and_redacts_invalid_inputs(self):
        for raw in (b'{"schema":"private secret", "schema":"duplicate"}', b'private secret', b'x' * (economics.LIMIT + 1)):
            stderr = io.StringIO()
            with patch.object(economics.sys, "stdin", type("Input", (), {"buffer": io.BytesIO(raw)})()), \
                    patch.object(economics.sys, "stderr", stderr):
                self.assertEqual(economics.main(), 1)
            self.assertNotIn("private secret", stderr.getvalue())

    def test_cli_is_content_free_and_offline(self):
        stdin = type("Input", (), {"buffer": io.BytesIO(json.dumps(fixture(request())).encode())})()
        stdout = io.StringIO()
        with patch.object(economics.sys, "stdin", stdin), patch.object(economics.sys, "stdout", stdout), \
                patch("socket.socket", side_effect=AssertionError("network forbidden")):
            self.assertEqual(economics.main(), 0)
        self.assertEqual(json.loads(stdout.getvalue())["schema"], "nib.cache-economics/v1")


if __name__ == "__main__":
    unittest.main()
