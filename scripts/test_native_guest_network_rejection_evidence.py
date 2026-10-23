# SPDX-License-Identifier: Apache-2.0
"""Original bounded failure projection, not a guessed precise remote cause."""
import hashlib
import json
from pathlib import Path
import unittest

from test_evidence_history import git

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p3/2026-10-23-native-guest-network-rejection"
PRODUCER = "942dba9d38b8a19bf6c3ab6b9a3a7de44fcb3b0f"
HASHES = {
    "validation.json": "9bb47760989c04bb3a0c855d52dd254b45fa480d518a7af4fe329872072fb6f5",
    "artifact-metadata.json": "2ce804cebfbd306f79ee5b1ad2dc8859496bbcbcd2a27123e8507f810a88c8bc",
    "workflow-run.json": "acda954f3b609ebd6e8a6a652202b92793256725cfaa9cc88fca92763f717e75",
}


def read(name):
    return json.loads((EVIDENCE / name).read_bytes())


class NativeGuestNetworkRejectionTests(unittest.TestCase):
    def test_original_bytes_and_exact_single_json_artifact(self):
        self.assertEqual({p.name for p in EVIDENCE.iterdir()}, set(HASHES) | {"README.md", "output.log"})
        for name, digest in HASHES.items():
            self.assertEqual(hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest(), digest)
        raw = (EVIDENCE / "validation.json").read_bytes()
        self.assertEqual(len(raw), 8938)
        self.assertEqual((EVIDENCE / "output.log").read_bytes(), raw)
        response = read("artifact-metadata.json")
        self.assertEqual((response["total_count"], len(response["artifacts"])), (1, 1))
        artifact, = response["artifacts"]
        self.assertEqual((artifact["id"], artifact["name"], artifact["size_in_bytes"]),
                         (12475450719, "unused-debug-export-39891331417-1", 3999))
        self.assertEqual(artifact["digest"], "sha256:53d6b1127b5e5529f6489a4e207f31e87047bd06feb264ac96449cadda1d84c3")
        self.assertEqual((artifact["workflow_run"]["id"], artifact["workflow_run"]["head_sha"]),
                         (39891331417, PRODUCER))

    def test_failure_is_before_qemu_and_cleanup_is_not_qualification(self):
        run = read("workflow-run.json")
        self.assertEqual((run["headSha"], run["status"], run["conclusion"]), (PRODUCER, "completed", "failure"))
        job, = run["jobs"]
        self.assertEqual((job["startedAt"], job["completedAt"]),
                         ("2026-10-23T02:45:45Z", "2026-10-23T02:51:45Z"))
        stages = {step["name"]: step["conclusion"] for step in job["steps"]}
        self.assertEqual(stages["Fetch only authenticated public inputs"], "success")
        self.assertEqual(stages["Generate one fresh private native export"], "failure")
        self.assertEqual(stages["Assemble debug UKI, boot once, and delete private images"], "skipped")
        self.assertEqual(stages["Discard native export if debug worker never started"], "success")
        self.assertEqual(stages["Preserve unused-export cleanup metadata only"], "success")
        value = read("validation.json")
        self.assertEqual(value["schema"], "nib.native-private-export-validation/v1")
        self.assertEqual((value["result"], value["build_result"]), ("fail", "failure"))
        self.assertTrue(value["private_images_removed"] and value["metadata_only"])
        for key in ("artifacts_verified", "boot_verified", "signed", "confidentiality_verified", "reproducibility_verified"):
            self.assertIs(value[key], False)

    def test_projected_stage_status_and_bounds_without_inventing_failed_check(self):
        failure = read("validation.json")["failure"]
        self.assertEqual((failure["schema"], failure["git_sha"]), ("nib.native-build-failure/v1", PRODUCER))
        install = failure["installation"]
        self.assertEqual((install["result"], install["stage"], install["failure_kind"]),
                         ("fail", "installation", "process-exit"))
        self.assertTrue(install["payload_removed"] and install["process_cleanup_confirmed"])
        self.assertEqual(install["receipt"], {"bytes": 9138,
            "sha256": "6d61e30ed180a77a5cd55f0e2bc963cee38a5180f2d1f23422b926efd8ea18f2"})
        self.assertEqual(install["worker"], {"result": "fail", "stage": "guest-network",
            "failure_kind": "rejected", "installed_packages": 212, "installer_exit_code": 0})
        resources = failure["resources"]
        self.assertEqual((resources["result"], resources["child_exit_code"], resources["oom_kill"],
                          resources["memory_peak_bytes"], resources["populated_before_cleanup"]),
                         ("fail", 1, 0, 5024845824, 0))
        self.assertTrue(resources["limits_read_back"])

    def test_all_original_source_hashes_match_reachable_exact_producer(self):
        value = read("validation.json")
        source = value["source"]
        self.assertEqual((source["git_sha"], source["profile"]), (PRODUCER, "guest-network/v1"))
        self.assertEqual((len(source["sources"]), len(value["validator_source"])), (68, 2))
        self.assertIn(PRODUCER, git(ROOT, "rev-list", "HEAD").decode().splitlines())
        for name, digest in {**source["sources"], **value["validator_source"]}.items():
            self.assertEqual(hashlib.sha256(git(ROOT, "show", PRODUCER + ":" + name)).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
