"""Original DP2 failures remain failures, independent of later successful runs."""
import hashlib
import json
from pathlib import Path
import unittest


class DP2FailureEvidenceTests(unittest.TestCase):
    def test_original_failures_and_claim_boundaries(self):
        root = Path(__file__).resolve().parents[1] / "evidence/p2"
        pins = {
            "2026-10-22-dp2-pack-path-fail": (
                "6ef8018e4d7e42f6561eebf2f409b813307bfe5ab5676b861d1fdf28ee6756f9",
                "046d7a5a72e85aa5ec34db034857dd9fe7f03c12c576db72719fa1e5bdd722b1"),
            "2026-10-22-dp2-tokenizer-fail": (
                "a20b14e4b06a66a0ada7427bfe37a3b4470edaa5d54c2a967b9353fcddece06d",
                "bf55589a3b91523adc13812084a91189f94c9d4dcb69ed5fc4fdc1936d062643"),
        }
        for directory, hashes in pins.items():
            with self.subTest(directory=directory):
                documents = []
                for name, expected in zip(("run.json", "output.log"), hashes, strict=True):
                    raw = (root / directory / name).read_bytes()
                    self.assertLessEqual(len(raw), 65536)
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), expected)
                    documents.append(json.loads(raw))
                run, report = documents
                self.assertEqual(run["git_sha"], "34f99da7d8965fa993512a7f8b51d3c450aee47f")
                self.assertEqual(run["execution"], "hardware")
                self.assertEqual(run["result"], "fail")
                self.assertFalse(run["hardware_validated"])
                self.assertEqual(report["result"], "fail")
                self.assertEqual(report["cleanup"], "confirmed")
                self.assertFalse(report["container_observation"]["oom_killed"])
                experiment = report["experiment"]
                for row in (report, experiment):
                    self.assertEqual(row["status"], "BLOCKED")
                    for key in ("confidential", "deployable", "functional_pass"):
                        self.assertIs(row[key], False)
                self.assertEqual(experiment["hardware"]["selected_gpus"], 8)
                self.assertFalse(experiment["hardware"]["attestation_verified"])
                if directory.endswith("pack-path-fail"):
                    self.assertEqual(experiment["stage"], "weights")
                    self.assertIsNone(experiment["startup"])
                    self.assertIsNone(experiment["application"])
                else:
                    self.assertEqual(experiment["stage"], "startup")
                    self.assertTrue(experiment["engine_stopped"])
                    startup = experiment["startup"]
                    self.assertEqual(startup["outcome"], "child-exited")
                    self.assertEqual(startup["exit_code"], 1)
                    self.assertEqual(startup["wait_ms"], 315109)
                    failure = startup["worker_exception_diagnostic"]
                    self.assertEqual(failure["type"], "other")
                    self.assertFalse(failure["source_unavailable"])
                    self.assertFalse(failure["observer_failed"])
                    self.assertEqual(failure["frames"][-1], {
                        "source_sha256": "32e4ff002092023720bdbe9101e01005a4a149a0729c82e05d7dbf497cbaa388",
                        "line": 1063})


if __name__ == "__main__":
    unittest.main()
