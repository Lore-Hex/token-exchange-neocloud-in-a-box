# SPDX-License-Identifier: Apache-2.0
"""Preserve the standard-six observation with its historical producer closure."""

import copy
import hashlib
import json
from pathlib import Path
import unittest

from test_native_entry_evidence import producer_payload

ROOT = Path(__file__).resolve().parents[1]
REVISION = "4001efbd2a28ddb00a98dff05eef1d47bdc3acab"
RECORDER_SHA = "52fbc9bbced18795dc72a522a7575aed49196aaa77ec615fc3d15d36d59e3fbb"
SLUG = "cpu-native-39485114646-1"
DIRECTORY = ROOT / ("evidence/p2/2026-10-20-" + SLUG)


class NativeSixEvidenceTests(unittest.TestCase):
    def test_original_standard_six_failure_remains_failed_and_source_bound(self):
        documents = []
        for name, digest in (
            ("run.json", "2a23a081a36ac9b9dcf1b2d444efef40e1e49d84112fe88ddcb829a7aa2a3530"),
            ("output.log", "81942e9d9282128d9fcc7b2a514eb8d8a1ea6dc4edb6a97a2dea7797e848eee3"),
        ):
            raw = (DIRECTORY / name).read_bytes()
            self.assertLessEqual(len(raw), 65536)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            documents.append(json.loads(raw))
        run, report = documents
        self.assertEqual((run["started_utc"], run["ended_utc"]),
                         ("2026-10-20T20:36:10Z", "2026-10-20T20:45:28Z"))
        self.assertEqual((report["stage"], report["cleanup"]), ("completed", "confirmed"))
        self.assertEqual((run["result"], report["result"]), ("fail", "fail"))
        experiment = report["experiment"]
        self.assertEqual(experiment["phase_elapsed_ms"],
                         {"application-preflight": 9265, "application": 524154, "entry": 12033})
        self.assertEqual(experiment["application"]["stages"], 5)
        self.assertTrue(experiment["application"]["native_inventory_unchanged"])
        entry = experiment["entry"]
        self.assertEqual((entry["outcome"], entry["exit_code"]), ("exited", 1))
        self.assertEqual(entry["checkpoints"], {"status": "eof", "last_id": 11})
        self.assertEqual([name for name, value in entry["state_mismatches"].items() if value], ["finders"])
        finder = entry["finder_diagnostic"]
        self.assertEqual(finder["entries"], ["builtin", "frozen", "sealed", "six"])
        self.assertEqual(finder["pattern_adapter_stage"], "pending")
        self.assertEqual(finder["status"], "observed")
        for key in ("same_identity_list", "comparison_failed", "scan_truncated",
                    "expected_order_changed", "expected_duplicated"):
            self.assertFalse(finder[key])
        self.assertEqual(finder["missing_expected_in_scan"], [])
        self.assertEqual(finder["sources"], [
            {"id": name, "status": "verified"}
            for name in ("vllm-env", "setuptools-hack", "metadata-backport", "six",
                         "botocore-six", "setuptools-metadata")])
        for row in (report, experiment):
            for name in ("entry_pass", "confidential", "deployable", "inference_started",
                         "hardware_validated", "abi_validated"):
                self.assertIs(row[name], False)
        with producer_payload(REVISION, RECORDER_SHA) as (historical, files, contract, validator_sha):
            self.assertEqual(historical.overlay.bundle_digest(files),
                             "6e0f0bcb57c58c1334d369ed9f208be5cacc997cf9824605f5dde601292c3358")
            self.assertFalse(historical.check_evidence(
                run, report, REVISION, SLUG, files, contract, "github-actions", validator_sha))
            for mutation in ("entry", "confidential", "payload", "cleanup", "finder"):
                changed = copy.deepcopy(report)
                if mutation == "entry":
                    changed["entry_pass"] = True
                elif mutation == "confidential":
                    changed["confidential"] = True
                elif mutation == "payload":
                    changed["provenance"]["payload_sha256"] = "0" * 64
                elif mutation == "finder":
                    changed["experiment"]["entry"]["finder_diagnostic"]["same_identity_list"] = True
                else:
                    changed["cleanup"] = "completion-unknown"
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    historical.check_evidence(
                        run, changed, REVISION, SLUG, files, contract, "github-actions", validator_sha)


if __name__ == "__main__":
    unittest.main()
