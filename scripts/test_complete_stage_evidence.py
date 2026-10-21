# SPDX-License-Identifier: Apache-2.0
"""Actual complete checkpoint and preceding failed transfer; no downloads."""
import hashlib
import json
from pathlib import Path
import unittest

from test_weight_stage_recorder import stage

ROOT = Path(__file__).resolve().parents[1]


class CompleteStageEvidenceTests(unittest.TestCase):
    def record(self, slug, revision, run_hash, output_hash, result):
        directory = ROOT / "evidence/p0" / slug
        documents = []
        for name, expected in (("run.json", run_hash), ("output.log", output_hash)):
            raw = (directory / name).read_bytes()
            self.assertLessEqual(len(raw), 65536)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), expected)
            documents.append(json.loads(raw))
        run, report = documents
        self.assertEqual(run["git_sha"], revision)
        self.assertEqual(report["source_git_sha"], revision)
        self.assertEqual((run["result"], report["result"]), (result, result))
        self.assertEqual(run["execution"], "software")
        self.assertFalse(run["hardware_validated"])
        for flag in ("hardware_validated", "runtime_ready", "confidential_qualified"):
            self.assertFalse(report[flag])
        self.assertEqual(report["cleanup"], "confirmed")
        self.assertEqual(report["plan"]["files"], 83)
        self.assertEqual(report["plan"]["total_bytes"], 510311045295)
        self.assertEqual(report["plan"]["hf_revision"], "1bae0e1088993b9b82de08f59de5be5a34e42170")
        self.assertEqual(report["plan"]["manifest_sha256"],
                         hashlib.sha256((ROOT / "models/deepseek-v4.1-flash-1017/1/files.sha256").read_bytes()).hexdigest())
        return report

    def test_complete_stage_has_exact_proof_not_just_progress_counts(self):
        report = self.record(
            "2026-10-21-deepseek-stage-complete", "212559364d78fafe74c98113038646069e6621f4",
            "254ad6f63dde728020e103055e0b8ae4d9cf19f82f3d2cf5f32f8f23cedc80ad",
            "818031130c706ad0d95cc20a011fefd73a364a44498fcb119158c837790c3050", "pass")
        stage.validate_proof(report["proof"], report["plan"], report["body_byte_budget"])
        self.assertEqual(report["stage"], "complete")
        self.assertEqual(report["proof"]["reused_files"], 81)
        self.assertEqual(report["proof"]["downloaded_bytes"], 203073077576)
        self.assertEqual(report["last_observation"]["counts"]["partial_files"], 0)
        self.assertFalse(report["last_observation"]["verified"])

    def test_client_timeout_failure_keeps_null_proof(self):
        report = self.record(
            "2026-10-21-deepseek-stage-client-timeout", "133fd607fc4eed75b043c5da42260056800b4c0f",
            "f4e13cd43f01f716ef59bf292031d0f8ab31e582d563f45a58261f24963a1415",
            "c0e1878f47c04bd6a43f3b377768b821f8a9a75edd482913a79972bca81ef492", "fail")
        self.assertIsNone(report["proof"])
        self.assertEqual(report["stage"], "staging")
        self.assertEqual(report["last_observation"]["counts"]["partial_files"], 2)
        self.assertEqual(report["deadline_seconds"], 10800)

    def test_full_model_trial_preserves_failure_before_any_completion(self):
        directory = ROOT / "evidence/p2/2026-10-21-b300-model-config-rejected"
        hashes = {"run.json": "e5581612fcfa025e708dabe6c3762f599464daa7746d4270d6f2d68ffa8cf020",
                  "output.log": "04d03010e139aa26dd9adbb52b1658df2cf15e56b2c0e87b7cbeadfce172eb6f"}
        records = {}
        for name, digest in hashes.items():
            raw = (directory / name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            records[name] = json.loads(raw)
        run, report = records["run.json"], records["output.log"]
        self.assertEqual(run["git_sha"], "0beb6fa56d63ee4ed80e3103c7851dabed69bca0")
        self.assertEqual(run["result"], "fail")
        self.assertFalse(run["hardware_validated"])
        self.assertEqual(report["cleanup"], "confirmed")
        experiment = report["experiment"]
        self.assertEqual(experiment["stage"], "startup")
        for flag in ("functional_pass", "confidential", "deployable"):
            self.assertFalse(experiment[flag])
        self.assertIsNone(experiment["completion"])
        self.assertIsNone(experiment["correctness"])
        self.assertTrue(experiment["engine_stopped"])
        self.assertEqual(experiment["startup"]["exit_code"], 1)
        self.assertEqual(experiment["startup"]["checkpoints"], {"last_id": 17, "status": "eof"})
        self.assertEqual(experiment["application"]["stages"], 5)


if __name__ == "__main__":
    unittest.main()
