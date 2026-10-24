# SPDX-License-Identifier: Apache-2.0
"""Actual complete checkpoint and preceding failed transfer; no downloads."""
import hashlib
import json
from pathlib import Path
import unittest

from deepseek_model_profile import select
from test_evidence_history import git
from test_weight_stage_recorder import stage

ROOT = Path(__file__).resolve().parents[1]


class CompleteStageEvidenceTests(unittest.TestCase):
    def record(self, slug, revision, run_hash, output_hash, result, *, model_profile="original"):
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
        profile = select(model_profile)
        manifest = (ROOT / profile["pack"] / "files.sha256").read_bytes()
        self.assertEqual(report["plan"]["files"], len(manifest.splitlines()))
        for field in ("hf_repo", "hf_revision", "total_bytes", "manifest_sha256"):
            self.assertEqual(report["plan"][field], profile[field])
        self.assertEqual(report["plan"]["manifest_sha256"], hashlib.sha256(manifest).hexdigest())
        return report

    def test_ordinary_node_uses_complete_abliterated_checkpoint_not_original(self):
        revision = "323ab0eedacee474e5b12fcc80a169ef43400075"
        report = self.record(
            "2026-10-24-ordinary-uncensored-stage", revision,
            "be1bda549c34db368bac8647850d63b0d2c4433426a222ff8d5e47f634047eee",
            "0bca7e41b985d4df4906920c4dff4a40b7a79fcbd13882fef823de5bdc9a3bbe",
            "pass", model_profile="uncensored")
        source = git(ROOT, "show", revision + ":host/record-stage.py")
        self.assertEqual(hashlib.sha256(source).hexdigest(),
                         "19e6f58fef0a877d3fc13718bd03fa2baa1aba74cab396e1bcfb1600bb43177f")
        self.assertEqual(source, (ROOT / "host/record-stage.py").read_bytes())
        stage.validate_plan(report["plan"])
        stage.validate_proof(report["proof"], report["plan"], report["body_byte_budget"])
        self.assertEqual(report["stage"], "complete")
        self.assertEqual((report["proof"]["files"], report["proof"]["verified_bytes"],
                          report["proof"]["downloaded_bytes"], report["proof"]["reused_files"]),
                         (79, 510311056155, 510311056155, 0))
        self.assertEqual((report["deadline_seconds"], report["download_workers"], report["body_byte_budget"]),
                         (7200, 8, 510311056234))
        self.assertFalse(report["last_observation"]["verified"])
        self.assertEqual(report["last_observation"]["counts"]["partial_files"], 0)
        cached = json.loads((ROOT / "evidence/p2/2026-10-23-dealign-cache-upload/receipt.json").read_bytes())
        self.assertEqual(cached["result"], "pass")
        self.assertTrue(cached["pack_complete"])
        self.assertEqual(cached["model_profile"], select("uncensored"))
        self.assertEqual(cached["verified_bytes"], report["proof"]["verified_bytes"])
        self.assertEqual(cached["target_manifest_sha256"], report["proof"]["manifest_sha256"])

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
