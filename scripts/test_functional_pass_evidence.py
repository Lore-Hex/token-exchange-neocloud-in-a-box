# SPDX-License-Identifier: Apache-2.0
"""Preserve functional success without promoting the blocked candidate."""
import hashlib
import json
from pathlib import Path
import unittest


class FunctionalPassEvidenceTests(unittest.TestCase):
    def test_original_functional_success_and_qualification_boundary(self):
        root = Path(__file__).resolve().parents[1] / "evidence/p2/2026-10-22-b300-functional-pass"
        for name, digest in (
            ("run.json", "aa811e84f03a017d54f43dd2ec956cca1f19dba3ffd0187c9ec74577eebae92f"),
            ("output.log", "c020d9032477280d38be01ec2e904cdcb04d8eb5f6d22bb5ee6380c1ce5ec522"),
        ):
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest)
        run = json.loads((root / "run.json").read_bytes())
        output = json.loads((root / "output.log").read_bytes())
        self.assertEqual(run["git_sha"], "c73590ef9bba125f655fc09f53421ffadd35742e")
        self.assertEqual(run["result"], "fail")
        self.assertFalse(run["hardware_validated"])
        self.assertEqual(output["result"], "fail")
        self.assertEqual(output["status"], "BLOCKED")
        self.assertEqual(output["cleanup"], "confirmed")
        self.assertEqual(output["container_observation"],
                         {"exit_code": 0, "oom_killed": False, "status": "exited"})
        experiment = output["experiment"]
        for record in (output, experiment):
            self.assertTrue(record["functional_pass"])
            self.assertFalse(record["deployable"])
            self.assertFalse(record["confidential"])
        self.assertTrue(experiment["engine_stopped"])
        self.assertEqual(experiment["startup"]["outcome"], "ready")
        self.assertIsNone(experiment["startup"]["exception_diagnostic"])
        self.assertIsNone(experiment["startup"]["worker_exception_diagnostic"])
        self.assertEqual(experiment["hardware"]["family"], "B300")
        self.assertEqual(experiment["hardware"]["selected_gpus"], 4)
        smoke = experiment["correctness"]
        self.assertEqual(smoke["schema"], "nib.local-correctness-smoke/v3")
        self.assertEqual(smoke["case_sha256"], "f59ae7cee2852d4259b340c1bd5e5d6aae0451bc776d303ea559162b46d649ac")
        self.assertTrue(smoke["all_cases_pass"])
        self.assertEqual(len(smoke["cases"]), 8)
        self.assertTrue(all(row["result"] == "pass" for row in smoke["cases"]))
        self.assertEqual(smoke["requests_started"], 10)
        self.assertEqual(sum(len(row["requests"]) for row in smoke["cases"]), 10)
        self.assertFalse(smoke["tau_acceptance"])


if __name__ == "__main__":
    unittest.main()
