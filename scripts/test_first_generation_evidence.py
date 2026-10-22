# SPDX-License-Identifier: Apache-2.0
"""Preserve the original failed smoke; successful requests are not full qualification."""
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "evidence/p2/2026-10-21-b300-first-generations"


class FirstGenerationEvidenceTests(unittest.TestCase):
    def test_original_result_and_nonqualifying_claims(self):
        for name, expected in (
            ("run.json", "435e745d74d0566fb6c7da357758f0af11361ae45cc5482ed1ba0f26b933c1f6"),
            ("output.log", "2db44ae3f12255a960e963193f358b93e5776ab2e484cddcf882f2510c7fbda4"),
        ):
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), expected)
        run = json.loads((ROOT / "run.json").read_bytes())
        output = json.loads((ROOT / "output.log").read_bytes())
        self.assertEqual(run["git_sha"], "8885dc941ed27bdcbfb85da4a0667b92ad92af6b")
        self.assertEqual(run["result"], "fail")
        self.assertFalse(run["hardware_validated"])
        self.assertEqual(output["cleanup"], "confirmed")
        self.assertFalse(output["container_observation"]["oom_killed"])
        for flag in ("confidential", "deployable", "functional_pass"):
            self.assertFalse(output[flag])
        experiment = output["experiment"]
        self.assertEqual(experiment["stage"], "complete")
        self.assertEqual(experiment["startup"]["outcome"], "ready")
        self.assertEqual(experiment["startup"]["wait_ms"], 767272)
        self.assertIsNone(experiment["startup"]["exception_diagnostic"])
        self.assertIsNone(experiment["startup"]["worker_exception_diagnostic"])
        self.assertTrue(experiment["engine_stopped"])
        smoke = experiment["correctness"]
        self.assertEqual(smoke["schema"], "nib.local-correctness-smoke/v1")
        self.assertEqual(smoke["requests_started"], 10)
        self.assertEqual(sum(c["result"] == "pass" for c in smoke["cases"]), 7)
        self.assertEqual([(c["id"], c["failure"]) for c in smoke["cases"] if c["result"] != "pass"],
                         [("logic", "answer")])
        self.assertFalse(smoke["all_cases_pass"])
        self.assertFalse(smoke["tau_acceptance"])


if __name__ == "__main__":
    unittest.main()
