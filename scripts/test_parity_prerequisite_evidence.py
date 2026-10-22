# SPDX-License-Identifier: Apache-2.0
"""Keep original preparation receipts distinct from serving qualification."""
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "evidence/p2"


class ParityPrerequisiteEvidenceTests(unittest.TestCase):
    def test_original_parity_failure_does_not_claim_token_mismatch_or_quality(self):
        directory = ROOT / "2026-10-22-tokenizer-parity-tool-protocol-fail"
        raw = [(directory / name).read_bytes() for name in ("run.json", "output.log")]
        self.assertEqual(tuple(hashlib.sha256(value).hexdigest() for value in raw), (
            "817c11cf9e0e2bc85090bc68b8738f726c07f55aac72cc7885fa864c3ffe81b4",
            "d0b931dbfcdbbc47322624223ffe79cb8fff5241cb2a281e5d2ee14819efddac"))
        envelope, row = map(json.loads, raw)
        self.assertEqual(envelope["git_sha"], "6fd01351158e3a2f0ff66d44abd0a6b2c3379ccc")
        self.assertEqual(envelope["result"], "fail")
        self.assertFalse(envelope["hardware_validated"])
        self.assertEqual(row["cleanup"], "confirmed")
        experiment = row["experiment"]
        self.assertTrue(experiment["engine_stopped"])
        self.assertEqual(experiment["startup"]["outcome"], "ready")
        for scope in (row, experiment):
            for key in ("functional_pass", "confidential", "deployable"):
                self.assertFalse(scope[key])
        parity = experiment["tokenizer_parity"]
        self.assertEqual(parity["requests_started"], 7)
        self.assertEqual([item["id"] for item in parity["cases"]],
                         ["text", "system", "tools", "tool-continuation"])
        for item, count in zip(parity["cases"][:3], (12, 16, 275)):
            self.assertEqual(item["result"], "pass")
            self.assertEqual(item["local"], item["server"])
            self.assertEqual(item["local"]["count"], count)
            self.assertEqual(item["usage"], {"prompt_tokens": count, "completion_tokens": 1})
        failed = parity["cases"][3]
        self.assertEqual(failed["result"], "fail")
        self.assertEqual(failed["failure"], "protocol")
        self.assertEqual(failed["requests"], 1)
        self.assertEqual(failed["local"]["count"], 332)
        self.assertIsNone(failed["server"])
        self.assertIsNone(failed["usage"])
        for key in ("all_cases_pass", "quality_accepted", "confidential", "tau_acceptance"):
            self.assertFalse(parity[key])

    def test_restore_verified_every_file_and_kept_the_source(self):
        directory = ROOT / "2026-10-22-replacement-cache-restore"
        raw = (directory / "receipt.json").read_bytes()
        self.assertEqual(raw, (directory / "output.log").read_bytes())
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         "acead24f95a6bb5427e37078f6b3ea03901fe5fc4adf23e42a0fe084fa7160c1")
        row = json.loads(raw)
        self.assertEqual(row["schema"], "nib.public-weight-cache-restore/v1")
        self.assertEqual(row["producer"], "a6c9d95a8c1286c0751d974003bddd33a0d1650b")
        self.assertEqual(row["result"], "pass")
        self.assertEqual(row["stage"], "complete")
        self.assertEqual(row["files"], 83)
        self.assertEqual(row["verified_bytes"], 510311045295)
        self.assertEqual(row["manifest_sha256"],
                         "b74ae635c35372cf6c350e4b33ef60c90ff01fe6426ad0fa7bc99b1982fe5bed")
        self.assertIsNone(row["failure"])
        self.assertEqual(row["cleanup"], "worker-completed")
        self.assertTrue(row["worker_exit_confirmed"])
        self.assertFalse(row["source_removed"])
        self.assertFalse(row["process_group_absence_verified"])
        self.assertFalse(row["confidential"])

    def test_fresh_eight_gpu_appraisal_is_not_full_node_attestation(self):
        directory = ROOT / "2026-10-22-pre-parity-gpu-appraisal"
        raw = [(directory / name).read_bytes() for name in ("run.json", "output.log")]
        self.assertEqual(tuple(hashlib.sha256(value).hexdigest() for value in raw), (
            "17c42794c18930bb991463ede265c76762b9b39ab42d0892664f75bda2828424",
            "81c9332f98c754726c7e9fd8689a1518be086cfaa8a23f971bf80b5928513e8b"))
        envelope, row = map(json.loads, raw)
        self.assertEqual(envelope["started_utc"], "2026-10-22T12:33:30.337078Z")
        self.assertEqual(envelope["result"], "pass")
        self.assertEqual(row["result"], "pass")
        self.assertFalse(envelope["hardware_validated"])
        appraisal = row["appraisal"]
        self.assertTrue(appraisal["gpu_appraisal_verified"])
        self.assertEqual(appraisal["claims"]["authenticated_gpu_count"], 8)
        self.assertTrue(all(value is True for key, value in appraisal["claims"].items()
                            if key != "authenticated_gpu_count"))
        self.assertEqual(appraisal["readiness_transition"], "already-ready-verified")
        self.assertEqual(appraisal["ready_before"], 1)
        self.assertEqual(appraisal["ready_after"], 1)
        for scope in (row, appraisal):
            for key in ("confidential_qualified", "deployable", "runtime_ready"):
                self.assertFalse(scope[key])


if __name__ == "__main__":
    unittest.main()
