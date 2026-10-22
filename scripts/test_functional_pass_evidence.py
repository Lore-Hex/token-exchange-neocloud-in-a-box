# SPDX-License-Identifier: Apache-2.0
"""Preserve functional success without promoting the blocked candidate."""
import hashlib
import json
from pathlib import Path
import unittest


class FunctionalPassEvidenceTests(unittest.TestCase):
    def test_original_functional_success_and_qualification_boundary(self):
        self.check_original("2026-10-22-b300-functional-pass", "c73590ef9bba125f655fc09f53421ffadd35742e", (
            ("run.json", "aa811e84f03a017d54f43dd2ec956cca1f19dba3ffd0187c9ec74577eebae92f"),
            ("output.log", "c020d9032477280d38be01ec2e904cdcb04d8eb5f6d22bb5ee6380c1ce5ec522"),
        ))

    def test_lower_memory_success_preserves_all_qualification_limits(self):
        self.check_original("2026-10-22-b300-bounded-memory", "9e1eb42441427e07375f3347c822a58d150882e2", (
            ("run.json", "6451a8316ac970d63021c67cd3d0d45bff1b0fbd4bec8aa7fbba1d7370b88113"),
            ("output.log", "aeb69230607ac6b97ae68724a2699cc8bac242f66509c5b089d35cc16ec0adad"),
        ))

    def test_two_replica_eight_gpu_success_does_not_claim_qualification(self):
        self.check_original("2026-10-22-b300-dp2-functional-pass", "080a7fe1f594b27113a015a605d29c5d0d7f674e", (
            ("run.json", "d6fb237f015532042dd0de8cda6191dbdef1b4d35b18832be6157bb91b21af50"),
            ("output.log", "c658d5e38c564e83a767190b48ffad33fa7662a2a8923d897102c76881dcfaf1"),
        ), selected_gpus=8)

    def check_original(self, directory, producer, files, selected_gpus=4):
        root = Path(__file__).resolve().parents[1] / "evidence/p2" / directory
        for name, digest in files:
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest)
        run = json.loads((root / "run.json").read_bytes())
        output = json.loads((root / "output.log").read_bytes())
        self.assertEqual(run["git_sha"], producer)
        self.assertEqual(output["provenance"]["git_sha"], producer)
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
        self.assertEqual(experiment["hardware"]["selected_gpus"], selected_gpus)
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
