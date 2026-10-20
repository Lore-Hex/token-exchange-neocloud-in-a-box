# SPDX-License-Identifier: Apache-2.0
"""Validate the original native finder observation against its producer."""

import copy
import hashlib
import json
from pathlib import Path
import unittest

from test_native_entry_evidence import producer_payload

ROOT = Path(__file__).resolve().parents[1]
REVISION = "d764993463b6072681227185f4b9a82967178755"
RECORDER_SHA = "8f73310e082ddd0f7e779f5b9df6ae63b50c143b94f748ad5c6c8877211a8bb5"
SLUG = "cpu-native-39467970211-1"
DIRECTORY = ROOT / ("evidence/p2/2026-10-20-" + SLUG)


class NativeBotocoreEvidenceTests(unittest.TestCase):
    def test_original_failure_is_validated_without_promoting_entry_or_inference(self):
        documents = []
        for name, digest in (
            ("run.json", "6fd052106f05a170242d0907edf75b38b987a9be2b6984b9c4c6088d1e20684c"),
            ("output.log", "ff86eaa6fa6fa655ef16213abafd14e1d8ef777f612803d9fd6cd246afb5185a"),
        ):
            raw = (DIRECTORY / name).read_bytes()
            self.assertLessEqual(len(raw), 65536)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            documents.append(json.loads(raw))
        run, report = documents
        self.assertEqual((run["started_utc"], run["ended_utc"]),
                         ("2026-10-20T17:44:21Z", "2026-10-20T17:56:25Z"))
        self.assertEqual((report["stage"], report["cleanup"]), ("completed", "confirmed"))
        self.assertEqual((run["result"], report["result"]), ("fail", "fail"))
        experiment = report["experiment"]
        self.assertEqual(experiment["phase_elapsed_ms"],
                         {"application-preflight": 17269, "application": 671086, "entry": 17557})
        self.assertEqual(experiment["application"]["stages"], 5)
        entry = experiment["entry"]
        self.assertEqual((entry["outcome"], entry["exit_code"]), ("exited", 1))
        self.assertEqual(entry["checkpoints"], {"status": "eof", "last_id": 11})
        self.assertEqual([name for name, value in entry["state_mismatches"].items() if value], ["finders"])
        finder = entry["finder_diagnostic"]
        self.assertEqual(finder["entries"], ["builtin", "frozen", "sealed", "botocore-vendored-six"])
        self.assertEqual(finder["pattern_adapter_stage"], "pending")
        self.assertEqual(finder["status"], "observed")
        self.assertFalse(finder["same_identity_list"])
        self.assertFalse(finder["comparison_failed"])
        self.assertFalse(finder["scan_truncated"])
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
                             "030ca2bfda9231eac67c6666e35a6bedeaa95c53b8313888e0f6dd4bf9bed2c8")
            self.assertFalse(historical.check_evidence(
                run, report, REVISION, SLUG, files, contract, "github-actions", validator_sha))
            for mutation in ("entry", "confidential", "payload", "cleanup"):
                changed = copy.deepcopy(report)
                if mutation == "entry":
                    changed["entry_pass"] = True
                elif mutation == "confidential":
                    changed["confidential"] = True
                elif mutation == "payload":
                    changed["provenance"]["payload_sha256"] = "0" * 64
                else:
                    changed["cleanup"] = "completion-unknown"
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    historical.check_evidence(
                        run, changed, REVISION, SLUG, files, contract, "github-actions", validator_sha)


if __name__ == "__main__":
    unittest.main()
