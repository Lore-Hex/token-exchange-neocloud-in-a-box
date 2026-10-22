# SPDX-License-Identifier: Apache-2.0
"""Preserve actual mock plans without upgrading them to cloud validation."""

import hashlib
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evidence/p2/2026-10-22-gcp-a4-mock-plan"
REVISION = "538f97c54f6e649f9d8af29e270713f838f1794f"
PINS = {
    "run.json": "1e98a02e579a98d622caa89770366cc36edd98a45f5e7d488f45d999a262db3d",
    "sha256.json": "606c97060997c48529bdb3581c68183b64ad616f4c2fe973de6ec12874ceb4a9",
    "output.log": "1ef967ea8596fca7aaf64245bbf225e48707a80f3b480030ef8b48e874199412",
    "tests.jsonl": "2105d8710f58f156de76c48d3724796751e809e3f1c8301f7ae791aa5cbe0983",
}


class GcpTerraformEvidenceTests(unittest.TestCase):
    def test_original_bytes_bind_to_producer_commit(self):
        for name, digest in PINS.items():
            self.assertEqual(hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest(), digest)
        manifest = json.loads((EVIDENCE / "sha256.json").read_bytes())
        expected_sources = {"platforms/gcp/terraform/" + name for name in (
            "versions.tf", "variables.tf", "main.tf", "outputs.tf",
            ".terraform.lock.hcl", "tests/lab.tftest.hcl")} | {
            "scripts/check_gcp_terraform.py", "scripts/e2e/snapshot.py"}
        self.assertEqual(set(manifest), expected_sources | {"output.log", "tests.jsonl"})
        for name, digest in manifest.items():
            data = ((EVIDENCE / name).read_bytes() if name in PINS else
                    subprocess.check_output(["git", "show", f"{REVISION}:{name}"], cwd=ROOT, timeout=10))
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest)

    def test_mock_only_claim_and_completed_plan_runs(self):
        run = json.loads((EVIDENCE / "run.json").read_bytes())
        self.assertEqual(run["git_sha"], REVISION)
        self.assertEqual(run["execution"], "fake")
        self.assertEqual(run["result"], "pass")
        self.assertFalse(run["hardware_validated"])
        self.assertIn("NOT a GCP/cloud plan", run["notes"])
        events = [json.loads(line) for line in (EVIDENCE / "tests.jsonl").read_text().splitlines()]
        self.assertEqual([event["test_summary"] for event in events if event["type"] == "test_summary"],
                         [{"status": "pass", "passed": 11, "failed": 0, "errored": 0, "skipped": 0}])
        log = (EVIDENCE / "output.log").read_text()
        self.assertIn("Plan: 6 to add, 0 to change, 0 to destroy.", log)
        self.assertEqual(log.count("No changes. Your infrastructure matches the configuration."), 2)
        self.assertNotIn("$ terraform apply", log)


if __name__ == "__main__":
    unittest.main()
