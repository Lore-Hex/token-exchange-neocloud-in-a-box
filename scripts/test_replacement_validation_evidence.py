# SPDX-License-Identifier: Apache-2.0
"""Original software/GPU appraisal receipts cannot imply confidential acceptance."""
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "evidence/p2"


class ReplacementEvidenceTests(unittest.TestCase):
    def read(self, directory, hashes):
        raw = [(ROOT / directory / name).read_bytes() for name in ("run.json", "output.log")]
        self.assertEqual(tuple(hashlib.sha256(value).hexdigest() for value in raw), hashes)
        envelope, result = map(json.loads, raw)
        self.assertEqual(envelope["result"], "pass")
        self.assertEqual(result["result"], "pass")
        self.assertFalse(envelope["hardware_validated"])
        return envelope, result

    def test_final_reviewed_parent_pipe_trial_is_scripted_not_model_quality(self):
        envelope, result = self.read("2026-10-22-tau-pipe-final-head", (
            "37b4363f5bcf0027aa9a94da82afc147e9f992a6f5e3892ddcf9826ad47115a0",
            "a23792e76f32241ab2562de887b229827508f7a515b02069e5eff2cb851f9e41"))
        self.assertEqual(envelope["git_sha"], "55e5ba6dce65ad3260e7747d10f4494341052b8e")
        self.assertIn("--parent-pipe", envelope["command"])
        self.assertEqual(result["cleanup"], "confirmed")
        self.assertTrue(result["cleanup_confirmed"])
        for name in ("live_transport", "quality_accepted", "confidential"):
            self.assertFalse(result[name])
        detail = result["diagnostic"]
        self.assertEqual(detail["stage"], "completed")
        self.assertTrue(detail["upstream_executed"])
        self.assertTrue(detail["accounting"]["usage_known"])
        self.assertEqual(detail["accounting"]["role_requests"],
                         {"agent": 1, "evaluator": 1, "simulator": 2})
        self.assertEqual(detail["report"]["kind"], "synthetic-fixture")
        self.assertEqual(detail["token_usage"], "synthetic-not-tokenized")

    def test_eight_gpu_appraisal_does_not_qualify_cpu_image_or_service(self):
        envelope, result = self.read("2026-10-22-replacement-gpu-appraisal", (
            "f6b9a1d772a383bd1c3e7422ee9a36da076ceb207bfa7ff95303ff04130e65c0",
            "cdee73e058ea1af3ddda121f11232b4bc727ad07794128cb6cfbbb1ee1da67a8"))
        self.assertEqual(envelope["git_sha"], "319201ea33a63266755118b1305cd696314c8a83")
        appraisal = result["appraisal"]
        self.assertTrue(appraisal["gpu_appraisal_verified"])
        claims = appraisal["claims"]
        self.assertEqual(claims["authenticated_gpu_count"], 8)
        self.assertTrue(all(value is True for key, value in claims.items()
                            if key != "authenticated_gpu_count"))
        self.assertEqual(appraisal["ready_after"], 1)
        for scope in (result, appraisal):
            for key in ("confidential_qualified", "deployable", "runtime_ready"):
                self.assertFalse(scope[key])


if __name__ == "__main__":
    unittest.main()
