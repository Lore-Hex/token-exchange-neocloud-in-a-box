# SPDX-License-Identifier: Apache-2.0
"""Tariff plumbing only; fake clocks and no processes, hardware or requests."""
from contextlib import redirect_stdout
from dataclasses import replace
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import selected_service as service
import selected_service_process as process
import test_selected_service as fixtures


class CachePricingTests(unittest.TestCase):
    def setUp(self):
        self.clock = fixtures.Clock()
        self.inputs = SimpleNamespace(root=Path("/private-fixture"))
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(service, "time", self.clock).start()
        mock.patch.object(service.pilot, "time", self.clock).start()

    def contract(self, **prices):
        return service.Contract.begin(self.clock.timestamp(86400), input_price="0.65", output_price="1.45",
            lifetime_profile=service.ORDINARY_PROFILE, execution_profile=service.engine_entry.MILLION_PROFILE, **prices)

    def test_omission_keeps_original_arguments_and_discount_changes_only_tariff(self):
        baseline = self.contract()
        original = baseline.guard_arguments(self.inputs)
        self.assertNotIn("--cached-input-price", original)
        for value in ("0", "0.000001", "0.065", "0.650000"):
            candidate = self.contract(cached_input_price=value)
            self.assertEqual(candidate.guard_arguments(self.inputs), original + ("--cached-input-price", value))
            self.assertEqual(replace(candidate, cached_input_price=None), baseline)
            self.assertEqual(candidate.lifetime, baseline.lifetime)
            self.assertEqual(candidate.execution, baseline.execution)

    def test_cache_price_cannot_bypass_profile_or_exact_decimal_validation(self):
        for value in ("", " ", "-1", "+0.1", "1e-2", "NaN", "Infinity", "00", "01.0", ".1", "0.",
                      "0.0000001", "1000000", "0.650001", "1", "0.1\n", True, 0, 0.1, [], {}):
            with self.subTest(value=value), self.assertRaises(service.Rejected):
                self.contract(cached_input_price=value)
        for profile in (service.engine_entry.EAGER_PROFILE, service.engine_entry.GRAPHS_PROFILE,
                        service.engine_entry.QUALITY_PROFILE, service.engine_entry.LONG_PROFILE,
                        service.engine_entry.LONG_GRAPHS_PROFILE):
            with self.subTest(profile=profile), self.assertRaises(service.Rejected):
                replace(self.contract(cached_input_price="0.065"), execution_profile=profile)
        with self.assertRaises(service.Rejected):
            replace(self.contract(), input_price="bad", cached_input_price="0")

    def test_process_cli_rejects_bad_or_duplicate_cache_prices_before_preparation(self):
        base = ["--prepared-sha256", "a" * 64, "--provider-config", "/unused", "--api-key-file", "/unused",
            "--cert", "/unused", "--key", "/unused", "--hostname", "private.invalid", "--input-price", "0.65",
            "--output-price", "1.45", "--hard-stop-at", self.clock.timestamp(86400),
            "--lifetime-profile", service.ORDINARY_PROFILE, "--execution-profile", service.engine_entry.MILLION_PROFILE]
        for flags, admitted in (([], True), (["--cached-input-price", "0.065"], True),
                (["--cached-input-price", "0"], True), (["--cached-input-price", "PRIVATE_CANARY"], False),
                (["--cached-input-price", ""], False), (["--cached-input-price", "0.7"], False),
                (["--cached-input-price", "0", "--cached-input-price", "0"], False)):
            with self.subTest(flags=flags), redirect_stdout(io.StringIO()) as output, \
                    mock.patch.object(process, "Prepared", side_effect=ValueError("stop before inputs")) as prepared, \
                    mock.patch.object(process.subprocess, "Popen") as spawn, \
                    mock.patch.object(service.Contract, "begin", wraps=service.Contract.begin) as begin:
                self.assertEqual(process.main(base + flags), 1)
                self.assertEqual(prepared.called, admitted)
                spawn.assert_not_called()
                if admitted:
                    self.assertEqual(begin.call_args.kwargs.get("cached_input_price"), flags[-1] if flags else None)
                self.assertNotIn("PRIVATE_CANARY", output.getvalue())


if __name__ == "__main__":
    unittest.main()
