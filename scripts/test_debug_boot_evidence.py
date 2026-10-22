# SPDX-License-Identifier: Apache-2.0
"""Original CPU-only boot failure: initrd observed, root proof absent."""
import hashlib
import json
from pathlib import Path
import unittest

from test_evidence_history import git, producer

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p3/2026-10-22-debug-boot-root-failure"
PRODUCER = "c0900f178072a08b5cc8688638993ccfc3061ab7"
HASHES = {
    "resources.json": "4f4601a09d6b8167bfc6515d3c2bf22335f459ca95eb0c97b2814b3a67e5a9c7",
    "run.json": "2f74cdae1acb24489719ff96ea544ef49206e19501489ae410e4cf40e69c6185",
    "validation.json": "afa21d099f290f5aeef626cf1993efe47ee34602f24ada4ff7dd3d5d07442426",
    "worker.json": "201eb046a2cc4a9c9899552ad318cd2935d268251f3659d4936dee3dece0d1f4",
}


def receipt(name):
    return json.loads((EVIDENCE / name).read_bytes())


class DebugBootEvidenceTests(unittest.TestCase):
    def test_original_four_json_files_are_byte_preserved(self):
        self.assertEqual({p.name for p in EVIDENCE.iterdir()}, set(HASHES) | {"README.md", "output.log"})
        for name, digest in HASHES.items():
            self.assertEqual(hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest(), digest)
        self.assertEqual((EVIDENCE / "output.log").read_bytes(), (EVIDENCE / "validation.json").read_bytes())
        self.assertEqual(producer((EVIDENCE / "run.json").read_bytes()), PRODUCER)
        self.assertEqual(receipt("run.json")["result"], "fail")
        self.assertEqual(receipt("run.json")["started_utc"], "2026-10-22T19:55:00Z")
        self.assertEqual(receipt("run.json")["ended_utc"], "2026-10-22T19:56:34Z")

    def test_failed_root_proof_cannot_be_promoted_from_clean_exit_or_assembly(self):
        worker = receipt("worker.json")
        self.assertEqual(worker["assembly"]["result"], "pass")
        self.assertEqual(len(worker["qemu"]["startup_checks"]), 5)
        self.assertTrue(all(item["result"] == "pass" for item in worker["qemu"]["startup_checks"]))
        self.assertEqual(worker["qemu"]["proof"], {"initrd": True, "root": False, "complete": False})
        self.assertEqual(worker["qemu"]["terminal"], {"exit_code": 0, "failure_kind": None,
            "process_group_cleaned": True, "stderr_bytes": 0, "stdout_bytes": 0})
        for value in (worker, worker["qemu"], receipt("validation.json")):
            self.assertEqual(value["result"], "fail")
            self.assertFalse(value["boot_verified"] or value["confidentiality_verified"])
        # The old receiver discarded failure codes: never retrofit a diagnosis.
        self.assertNotIn("guest_failure", worker["qemu"]["proof"])

    def test_independent_cleanup_and_exact_source_binding(self):
        state, worker = receipt("resources.json"), receipt("worker.json")
        for key in ("empty_verified", "group_removed", "limits_read_back"):
            self.assertTrue(state[key])
        self.assertEqual(state["oom_kill"], 0)
        self.assertEqual(state["child_exit_code"], 1)
        self.assertEqual(state["memory_peak_bytes"], 2147483648)
        self.assertTrue(receipt("validation.json")["private_images_removed"])
        for label in ("source", "debug_source"):
            self.assertEqual(state["producer"][label], worker[label])
            self.assertEqual(worker[label]["git_sha"], PRODUCER)
            for name, digest in worker[label]["sources"].items():
                raw = git(ROOT, "show", PRODUCER + ":" + name)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
