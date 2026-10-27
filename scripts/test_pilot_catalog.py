# SPDX-License-Identifier: Apache-2.0
"""Replay real Go handler catalogs through the pinned checker, without a server."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest

import selected_service_conformance as conformance

ROOT = Path(__file__).resolve().parents[1]
MILLION = "selected-deepseek-million"
LIMITS = {
    "qwen": (4096, 256),
    "selected-deepseek": (8192, 256),
    "selected-deepseek-graphs": (4096, 256),
    "selected-deepseek-quality": (32768, 2048),
    "selected-deepseek-long": (131072, 16384),
}


@unittest.skipUnless(importlib.util.find_spec("tr_provider_check"), "run with hash-locked checker test environment")
class GeneratedCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tr_provider_check.checks import catalog
        cls.checker = SimpleNamespace(catalog=catalog)
        result = subprocess.run(["go", "run", "./scripts/fixtures/pilot_catalog"], cwd=ROOT,
            env={**os.environ, "GOTOOLCHAIN": "local", "GOENV": "off", "GOWORK": "off",
                 "GOPROXY": "off", "GOSUMDB": "off", "GONOPROXY": "", "GOPRIVATE": "",
                 "GOFLAGS": "-mod=readonly -p=1"},
            capture_output=True, check=True, timeout=120)
        cls.catalogs = json.loads(result.stdout)

    def test_legacy_profiles_remain_compatible_with_pinned_checker(self):
        self.assertEqual(set(self.catalogs), set(LIMITS) | {MILLION})
        for profile, document in self.catalogs.items():
            with self.subTest(profile=profile):
                if profile == MILLION:
                    continue
                parsed = conformance.catalog_payload(self.checker, json.dumps(document).encode())
                self.assertEqual(parsed, document)
                self.assertEqual(len(parsed["data"]), 1)
                model = parsed["data"][0]
                self.assertEqual((model["context_length"], model["max_output_tokens"]), LIMITS[profile])
                self.assertLessEqual(model["reliability"]["completion_timeout_seconds"], 900)

    def test_v21_cannot_pass_historical_v20_conformance(self):
        self.assertEqual(self.catalogs[MILLION]["contract_version"], "2.1")
        self.assertEqual(len(self.catalogs[MILLION]["data"]), 1)
        raw = json.dumps(self.catalogs[MILLION]).encode()
        with self.assertRaises(conformance.inference.Rejected) as caught:
            conformance.validate_catalog(self.checker, raw, None, 0)
        self.assertEqual(caught.exception.category, "protocol")

    def test_long_deadline_remains_rejected_by_unmodified_pinned_checker(self):
        document = copy.deepcopy(self.catalogs["selected-deepseek-long"])
        model = document["data"][0]
        model.update(context_length=1048576, max_output_tokens=393216)
        model["reliability"]["completion_timeout_seconds"] = 3540
        with self.assertRaises(conformance.inference.Rejected) as caught:
            conformance.catalog_payload(self.checker, json.dumps(document).encode())
        self.assertEqual(caught.exception.category, "protocol")


if __name__ == "__main__":
    unittest.main()
