# SPDX-License-Identifier: Apache-2.0
"""Original pre-boot failure remains unknown, never retroactively diagnosed."""
import hashlib
import json
from pathlib import Path
import unittest

from test_evidence_history import git

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p3/2026-10-23-native-export-before-network-boot"
PRODUCER = "ff708bcfc5bac542cff0bc95a7c958926cb368d9"
HASHES = {
    "validation.json": "37f5d028914f0539a24b5e9bcef8c979f0aab277432d392043711bff0276766b",
    "artifact-metadata.json": "2a19bce7e98445cffb9442b678984b9f077fb195da4f5f6e07b08eb25cea6a5a",
    "workflow-run.json": "870b62861403930521df686073737a3a8117e88d7ae7ef4756bdb4c2da63cc2e",
}


def read(name):
    return json.loads((EVIDENCE / name).read_bytes())


class NativeBuildFailureEvidenceTests(unittest.TestCase):
    def test_original_bytes_and_single_json_artifact_binding(self):
        self.assertEqual({p.name for p in EVIDENCE.iterdir()}, set(HASHES) | {"README.md", "output.log"})
        for name, digest in HASHES.items():
            self.assertEqual(hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest(), digest)
        self.assertEqual((EVIDENCE / "output.log").read_bytes(), (EVIDENCE / "validation.json").read_bytes())
        self.assertEqual(len((EVIDENCE / "validation.json").read_bytes()), 8089)
        response = read("artifact-metadata.json")
        self.assertEqual((response["total_count"], len(response["artifacts"])), (1, 1))
        artifact = response["artifacts"][0]
        self.assertEqual((artifact["id"], artifact["name"], artifact["size_in_bytes"]),
                         (12472417484, "unused-debug-export-39885063707-1", 3710))
        self.assertEqual(artifact["digest"], "sha256:90299250a0b967b37a6bb943ce237197251469252aa4a9a84cc27feab6b7c05d")
        self.assertEqual((artifact["workflow_run"]["id"], artifact["workflow_run"]["head_sha"]),
                         (39885063707, PRODUCER))

    def test_build_failure_precedes_boot_and_safe_cleanup_is_not_success(self):
        run = read("workflow-run.json")
        self.assertEqual((run["headSha"], run["status"], run["conclusion"]), (PRODUCER, "completed", "failure"))
        job, = run["jobs"]
        self.assertEqual((job["startedAt"], job["completedAt"]),
                         ("2026-10-23T01:37:42Z", "2026-10-23T01:43:37Z"))
        stages = {step["name"]: step["conclusion"] for step in job["steps"]}
        self.assertEqual(stages["Fetch only authenticated public inputs"], "success")
        self.assertEqual(stages["Generate one fresh private native export"], "failure")
        self.assertEqual(stages["Assemble debug UKI, boot once, and delete private images"], "skipped")
        self.assertEqual(stages["Discard native export if debug worker never started"], "success")
        self.assertEqual(stages["Preserve unused-export cleanup metadata only"], "success")
        value = read("validation.json")
        self.assertEqual(value["schema"], "nib.native-private-export-validation/v1")
        self.assertEqual((value["result"], value["build_result"]), ("fail", "failure"))
        self.assertNotIn("failure", value)
        self.assertTrue(value["private_images_removed"] and value["metadata_only"])
        for key in ("artifacts_verified", "boot_verified", "signed", "confidentiality_verified", "reproducibility_verified"):
            self.assertIs(value[key], False)

    def test_all_original_source_hashes_match_reachable_committed_producer(self):
        value = read("validation.json")
        source = value["source"]
        self.assertEqual((source["git_sha"], source["profile"]), (PRODUCER, "guest-network/v1"))
        self.assertEqual((len(source["sources"]), len(value["validator_source"])), (68, 2))
        self.assertIn(PRODUCER, git(ROOT, "rev-list", "HEAD").decode().splitlines())
        for name, digest in {**source["sources"], **value["validator_source"]}.items():
            self.assertEqual(hashlib.sha256(git(ROOT, "show", PRODUCER + ":" + name)).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
