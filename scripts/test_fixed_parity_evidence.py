# SPDX-License-Identifier: Apache-2.0
"""Preserve the real fixed-parity receipts without promoting qualification."""

import hashlib
import json
from pathlib import Path
import unittest

import engine_local_correctness as correctness
import engine_tokenizer_parity as parity

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p2/2026-10-22-tokenizer-parity-fixed-pass"
PRODUCER = "8e2236c251e8fde607de4506d0550fd72b1e7775"


class FixedParityEvidenceTests(unittest.TestCase):
    def test_original_receipts_and_exact_producer(self):
        expected = {
            "run.json": "746c5c4da9798edb3f8f12bffa8b68e68ccedf0c23f0d062108c24711f8ce716",
            "output.log": "f54295245bb58125117032df21c4093ab2d93534b3df9312f62e61ba1dc5be04",
        }
        for name, digest in expected.items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest(), digest)
        envelope = json.loads((EVIDENCE / "run.json").read_bytes())
        report = json.loads((EVIDENCE / "output.log").read_bytes())
        self.assertEqual(envelope["git_sha"], PRODUCER)
        self.assertEqual(report["provenance"]["git_sha"], PRODUCER)
        self.assertEqual(envelope["started_utc"], "2026-10-22T17:12:13Z")
        self.assertEqual(envelope["ended_utc"], "2026-10-22T17:38:24Z")
        self.assertEqual(report["provenance"]["receipt_sha256"],
                         "cffda20a4c88d2430814ab2ef06480f78d9abb4a4a3d9911112e527472b8de0a")
        self.assertEqual(report["provenance"]["pack_revision"],
                         "1bae0e1088993b9b82de08f59de5be5a34e42170")

    def test_all_four_unchanged_cases_match_tokens_and_generated_usage(self):
        report = json.loads((EVIDENCE / "output.log").read_bytes())
        experiment = report["experiment"]
        result = experiment["tokenizer_parity"]
        self.assertIs(parity.validate(result, correctness), result)
        self.assertTrue(result["all_cases_pass"])
        self.assertEqual(result["requests_started"], 8)
        self.assertEqual(result["elapsed_ms"], 12386)
        self.assertEqual([row["id"] for row in result["cases"]], list(parity.IDS))
        for row, count in zip(result["cases"], (12, 16, 275, 332)):
            with self.subTest(case=row["id"]):
                self.assertEqual(row["result"], "pass")
                self.assertEqual(row["failure"], "none")
                self.assertEqual(row["requests"], 2)
                self.assertEqual(row["local"], row["server"])
                self.assertEqual(row["local"]["count"], count)
                self.assertEqual(row["usage"], {"prompt_tokens": count, "completion_tokens": 1})
        previous = json.loads((ROOT / "evidence/p2/2026-10-22-tokenizer-parity-tool-protocol-fail/output.log").read_bytes())
        old = previous["experiment"]["tokenizer_parity"]
        self.assertEqual(old["binding_sha256"], result["binding_sha256"])
        self.assertEqual([row["local"] for row in old["cases"]],
                         [row["local"] for row in result["cases"]])
        self.assertFalse(old["all_cases_pass"])
        self.assertEqual(old["cases"][-1]["failure"], "protocol")

    def test_cleanup_and_partial_success_are_not_quality_or_attestation(self):
        envelope = json.loads((EVIDENCE / "run.json").read_bytes())
        report = json.loads((EVIDENCE / "output.log").read_bytes())
        experiment = report["experiment"]
        self.assertEqual(report["cleanup"], "confirmed")
        self.assertEqual(report["container_observation"],
                         {"exit_code": 0, "oom_killed": False, "status": "exited"})
        self.assertTrue(experiment["engine_stopped"])
        self.assertEqual(experiment["startup"]["outcome"], "ready")
        self.assertEqual(experiment["startup"]["wait_ms"], 1201371)
        self.assertIsNone(experiment["startup"]["exception_diagnostic"])
        self.assertIsNone(experiment["startup"]["worker_exception_diagnostic"])
        self.assertEqual(experiment["hardware"]["selected_gpus"], 8)
        self.assertEqual(experiment["hardware"]["family"], "B300")
        self.assertFalse(experiment["hardware"]["attestation_verified"])
        for scope in (report, experiment):
            self.assertTrue(scope["functional_pass"])
            self.assertEqual(scope["status"], "BLOCKED")
            self.assertFalse(scope["confidential"])
            self.assertFalse(scope["deployable"])
        self.assertEqual(envelope["result"], "fail")
        self.assertEqual(report["result"], "fail")
        self.assertFalse(envelope["hardware_validated"])
        for key in ("quality_accepted", "confidential", "tau_acceptance"):
            self.assertFalse(experiment["tokenizer_parity"][key])
        self.assertFalse(experiment["tokenizer_materialization"]["disk_candidate_changed"])


if __name__ == "__main__":
    unittest.main()
