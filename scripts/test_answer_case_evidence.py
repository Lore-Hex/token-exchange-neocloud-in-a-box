# SPDX-License-Identifier: Apache-2.0
"""Keep the content-free casing diagnosis and its original failed result."""
import hashlib
import json
from pathlib import Path
import unittest


class AnswerCaseEvidenceTests(unittest.TestCase):
    def test_original_strict_failure_and_cleanup(self):
        root = Path(__file__).resolve().parents[1] / "evidence/p2/2026-10-22-b300-answer-case"
        for name, digest in (
            ("run.json", "f75bb0d1bcee836c6b7fa751c440f117f65e516421efeffe96ccac20365ae362"),
            ("output.log", "c4df311cf23a8997d5c7f913c69f44b42007ee7cb58b855b54fe6a19e2172271"),
        ):
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest)
        run = json.loads((root / "run.json").read_bytes())
        output = json.loads((root / "output.log").read_bytes())
        self.assertEqual(run["git_sha"], "8cb7a5b91cdcb531ba8d23008ba6ce27fd1104c7")
        self.assertEqual(run["result"], "fail")
        self.assertFalse(run["hardware_validated"])
        self.assertEqual(output["cleanup"], "confirmed")
        self.assertFalse(output["container_observation"]["oom_killed"])
        for flag in ("confidential", "deployable", "functional_pass"):
            self.assertFalse(output[flag])
        experiment = output["experiment"]
        self.assertEqual(experiment["startup"]["outcome"], "ready")
        self.assertIsNone(experiment["startup"]["exception_diagnostic"])
        self.assertIsNone(experiment["startup"]["worker_exception_diagnostic"])
        self.assertTrue(experiment["engine_stopped"])
        smoke = experiment["correctness"]
        self.assertEqual(smoke["schema"], "nib.local-correctness-smoke/v2")
        self.assertEqual(smoke["case_sha256"], "b622ef5975f51feea1dd01c8c3d350d3c1aaf59723cba5e75c32890715d060f3")
        self.assertEqual(smoke["requests_started"], 10)
        self.assertEqual(sum(c["result"] == "pass" for c in smoke["cases"]), 7)
        self.assertEqual([(c["id"], c["failure"]) for c in smoke["cases"] if c["result"] != "pass"],
                         [("logic", "answer-case")])
        self.assertFalse(smoke["all_cases_pass"])
        self.assertFalse(smoke["tau_acceptance"])


if __name__ == "__main__":
    unittest.main()
