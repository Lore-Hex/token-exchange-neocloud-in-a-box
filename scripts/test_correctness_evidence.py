"""Preserve original failed hardware and local diagnostic recorder output."""
import hashlib
import json
from pathlib import Path
import unittest


class CorrectnessEvidenceTests(unittest.TestCase):
    def test_cpu_entry_failure_records_only_the_observed_import_boundary(self):
        for name, source, run_digest, output_digest in (
            ("cpu-entry-125036c", "125036cb61a9d15d624f5d1c8a0c65d69076574c",
             "c5a60637849450294deea13af77ddffea9fe8bdfe850a1b4d4f2466885d87b15",
             "f4985fd5f781e0324cce4d4277970ad1a5e67c24aaa8d1395fe945c7f197d6c3"),
            ("cpu-entry-732fe9f", "732fe9fd77e5e2b877fdfa04ac18547d4e437f25",
             "a45e523ef0bef472c6ade2878019aa8a6ce237f84192d51c0b7547cfa5edf6fa",
             "d0bc25522652f61e63e258bef3c47688400d88cbc8cf68948c372043b7874035"),
            ("cpu-finder-9833667", "983366709ffb1bd0682fb7692bb71b1e4777a450",
             "36112d70c3a91c0f45b20da58dcffc6e916859771e72bafceeeb1b1e134f001b",
             "c9ec7cb8d8eb841d3dcea5d0c7fd1fd88b7c10df749966b0ce46a836824c50e0"),
        ):
            with self.subTest(source=source):
                self.check_cpu_entry_failure(name, source, run_digest, output_digest)

    def check_cpu_entry_failure(self, name, source, run_digest, output_digest):
        root = Path(__file__).resolve().parents[1] / ("evidence/p2/2026-10-20-" + name)
        values = {}
        for name, digest in (("run.json", run_digest), ("output.log", output_digest)):
            raw = (root / name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            values[name] = json.loads(raw)
        run, output = values["run.json"], values["output.log"]
        self.assertEqual(run["git_sha"], source)
        self.assertEqual(run["git_sha"], output["provenance"]["git_sha"])
        self.assertEqual(run["execution"], "software")
        self.assertEqual(run["host"]["gpus"], "none")
        self.assertFalse(run["hardware_validated"])
        self.assertEqual(run["result"], "fail")
        self.assertEqual(output["cleanup"], "confirmed")
        self.assertEqual(output["stage"], "completed")
        experiment = output["experiment"]
        self.assertEqual(experiment["application"]["stages"], 5)
        self.assertEqual(experiment["entry"]["checkpoints"], {"status": "eof", "last_id": 11})
        self.assertEqual(experiment["entry"]["exit_code"], 1)
        self.assertEqual(experiment["entry"]["state_observation"], "observed")
        self.assertEqual({key for key, value in experiment["entry"]["state_mismatches"].items() if value},
                         {"finders"})
        self.assertEqual(len(experiment["entry"]["state_mismatches"]), 10)
        for row in (output, experiment):
            for key in ("entry_pass", "confidential", "deployable", "inference_started", "hardware_validated"):
                self.assertFalse(row[key])

    def test_unknown_finder_remains_unidentified_and_blocked(self):
        root = Path(__file__).resolve().parents[1] / "evidence/p2/2026-10-20-cpu-finder-9833667"
        output = json.loads((root / "output.log").read_bytes())
        self.assertEqual(output["experiment"]["entry"]["finder_diagnostic"], {
            "status": "observed",
            "entries": ["builtin", "frozen", "sealed", "unknown"],
            "comparison_failed": False,
            "expected_duplicated": False,
            "expected_order_changed": False,
            "missing_expected_in_scan": [],
            "pattern_adapter_stage": "pending",
            "same_identity_list": False,
            "scan_truncated": False,
            "sources": [
                {"id": name, "status": "verified"}
                for name in ("vllm-env", "setuptools-hack", "metadata-backport", "six")
            ],
        })
        self.assertEqual(output["candidate_status"], "BLOCKED")
        self.assertEqual(output["result"], "fail")

    def test_original_failure_is_preserved_without_inferred_runtime_success(self):
        root = Path(__file__).resolve().parents[1] / "evidence/p2/2026-10-20-correctness-recorder-failed"
        values = {}
        for name, digest in (
            ("run.json", "6d1a3383e54e9303927e72880e276282a96d93f130a0978a2af2f4eaa575ea88"),
            ("output.log", "b8275e6549395a83ad529728fe5920b9334c480d55eeb41f4654a37ea10428dc"),
        ):
            raw = (root / name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            values[name] = json.loads(raw)
        run, output = values["run.json"], values["output.log"]
        self.assertEqual(run["git_sha"], output["provenance"]["git_sha"])
        self.assertEqual(run["git_sha"], "26a7240b53ee0590d98655b12c6b57ca4779c2aa")
        self.assertEqual(run["execution"], "software")
        self.assertFalse(run["hardware_validated"])
        self.assertEqual(run["result"], "fail")
        self.assertEqual(output["stage"], "experiment")
        self.assertEqual(output["failure"], "bounded-lab-stage-failed")
        self.assertEqual(output["cleanup"], "confirmed")
        self.assertNotIn("experiment", output)
        for key in ("functional_pass", "confidential", "deployable"):
            self.assertFalse(output[key])

    def test_validated_startup_failure_is_not_inference_or_oom(self):
        root = Path(__file__).resolve().parents[1] / "evidence/p2/2026-10-20-correctness-startup-failed"
        values = {}
        for name, digest in (
            ("run.json", "b2d7749f0a5e3b400cd6525f202b95186f1b3b20bfd989df13b2a7ed717da8ae"),
            ("output.log", "c229a6a25a0463b53d2bfe3eecae55235fe8b7af49852f682f01224fff9bfe26"),
        ):
            raw = (root / name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            values[name] = json.loads(raw)
        run, output = values["run.json"], values["output.log"]
        self.assertEqual(run["git_sha"], "bac13b0fac39096a0990417f3cfcf4e623e911ac")
        self.assertEqual(run["git_sha"], output["provenance"]["git_sha"])
        self.assertEqual(run["execution"], "hardware")
        self.assertFalse(run["hardware_validated"])
        self.assertEqual(output["cleanup"], "confirmed")
        self.assertEqual(output["experiment_step"], "completed")
        self.assertEqual(output["container_observation"],
                         {"status": "exited", "exit_code": 0, "oom_killed": False})
        experiment = output["experiment"]
        self.assertEqual(experiment["stage"], "startup")
        self.assertEqual(experiment["application"]["stages"], 5)
        self.assertTrue(experiment["hardware"]["system_ready"])
        self.assertEqual(experiment["startup"],
                         {"outcome": "child-exited", "exit_code": 1,
                          "wait_ms": 8003, "health_probes": 8})
        self.assertIsNone(experiment["completion"])
        self.assertIsNone(experiment["correctness"])
        for row in (output, experiment):
            for key in ("functional_pass", "confidential", "deployable"):
                self.assertFalse(row[key])


if __name__ == "__main__":
    unittest.main()
