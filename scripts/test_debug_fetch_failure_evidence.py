# SPDX-License-Identifier: Apache-2.0
"""Retain the pre-build failure and its missing diagnostic, not a boot claim."""
import hashlib
import json
from pathlib import Path
import unittest

from test_evidence_history import git

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p3/2026-10-27-debug-topology-fetch-failure"
PRODUCER = "4e0c065cf37b24108e5c371a05d226958391df8e"
ORIGINALS = {
    "validation.json": (8261, "c10e675221d4e4e2868a0226cbf89a655f9fce736d397f516d5351fb98802f9f"),
    "github-run.json": (3081, "270cc96010aa41de9fde757a8e9b8004792a9686b54a5e8617b5f29d8ef250d9"),
    "github-artifacts.json": (773, "e96ea19fb439f8779fb48e130fd46af91bda1e393de4b4636c4a9052ca9e63b4"),
    "failed-workflow.log": (3479, "ce2b2955e0e18aca6999a099ceac28ec04f4cb379498ad2b2115fc1a562ac58e"),
}


def receipt(name):
    return json.loads((EVIDENCE / name).read_bytes())


class FetchFailureEvidenceTests(unittest.TestCase):
    def test_originals_unchanged_no_synthetic_run_receipt(self):
        self.assertEqual({p.name for p in EVIDENCE.iterdir()}, set(ORIGINALS) | {"README.md"})
        for name, (size, digest) in ORIGINALS.items():
            raw = (EVIDENCE / name).read_bytes()
            self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), (size, digest))

    def test_cleanup_receipt_does_not_claim_build_boot_or_hardware(self):
        record = receipt("validation.json")
        self.assertEqual(record["schema"], "nib.native-private-export-validation/v1")
        self.assertEqual((record["result"], record["build_result"]), ("fail", "skipped"))
        self.assertEqual(record["failure"], {"schema": "nib.native-build-failure/v1",
                         "git_sha": PRODUCER, "installation": None, "resources": None})
        for name in ("artifacts_verified", "boot_verified", "confidentiality_verified", "reproducibility_verified", "signed"):
            self.assertIs(record[name], False)
        self.assertIs(record["private_images_removed"], True)
        self.assertIs(record["metadata_only"], True)
        self.assertEqual((record["source"]["git_sha"], record["source"]["profile"]), (PRODUCER, "guest-network/v1"))
        self.assertEqual(len(record["source"]["sources"]), 68)
        self.assertEqual(len(record["validator_source"]), 2)
        for sources in (record["source"]["sources"], record["validator_source"]):
            for name, digest in sources.items():
                self.assertEqual(hashlib.sha256(git(ROOT, "show", PRODUCER + ":" + name)).hexdigest(), digest)

    def test_github_failure_and_only_cleanup_artifact(self):
        run, artifacts = receipt("github-run.json"), receipt("github-artifacts.json")
        self.assertEqual((run["databaseId"], run["attempt"], run["headSha"], run["event"], run["status"], run["conclusion"]),
                         (40509832521, 1, PRODUCER, "workflow_dispatch", "completed", "failure"))
        self.assertEqual((run["startedAt"], run["jobs"][0]["completedAt"]),
                         ("2026-10-27T13:11:11Z", "2026-10-27T13:16:08Z"))
        steps = {row["number"]: row for row in run["jobs"][0]["steps"]}
        self.assertEqual([steps[n]["conclusion"] for n in (2, 5, 6, 7, 8, 9, 10)],
                         ["success", "failure", "skipped", "skipped", "success", "skipped", "success"])
        self.assertEqual((artifacts["total_count"], len(artifacts["artifacts"])), (1, 1))
        artifact = artifacts["artifacts"][0]
        self.assertEqual((artifact["id"], artifact["name"], artifact["size_in_bytes"]),
                         (12737325696, "unused-debug-export-40509832521-1", 3761))
        self.assertEqual(artifact["digest"], "sha256:e08cde1503f04f01037de5adcdbc48f21f5f9754ca5b6f3ac31ea51398ab3029")
        self.assertFalse(artifact["expired"])
        self.assertEqual((artifact["workflow_run"]["id"], artifact["workflow_run"]["head_sha"]), (40509832521, PRODUCER))

    def test_original_log_cannot_identify_the_failed_archive(self):
        raw = (EVIDENCE / "failed-workflow.log").read_text()
        self.assertIn("build/native-install/metadata.tar.gz: OK", raw)
        self.assertIn("Pinned input fetch failed; see content-free diagnostic.", raw)
        self.assertNotIn('"schema": "nib.input-fetch-failure/v1"', raw)
        self.assertNotIn('"expected_sha256"', raw)
        workflow = git(ROOT, "show", PRODUCER + ":.github/workflows/debug-boot.yml").decode()
        self.assertIn('> build/native-install/input-fetch.log', workflow)
        self.assertNotIn("input-fetch.log", workflow[workflow.index("      - name: Preserve fixed debug metadata"):])


if __name__ == "__main__":
    unittest.main()
