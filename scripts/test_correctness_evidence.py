"""Preserve the original failed hardware-attempt recorder output."""
import hashlib
import json
from pathlib import Path
import unittest


class CorrectnessEvidenceTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
