# SPDX-License-Identifier: Apache-2.0
"""Original optional-network boot failure; missing proof is not a diagnosis."""
import hashlib
import json
from pathlib import Path
import unittest

from test_evidence_history import git, producer

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p3/2026-10-22-debug-network-boot-failure"
PRODUCER = "4fe0bd7df8f6ce3293e6dca609106b37dd9676a3"
HASHES = {
    "validation.json": "afa21d099f290f5aeef626cf1993efe47ee34602f24ada4ff7dd3d5d07442426",
    "resources.json": "36f2d94be8e9135eef4b365259211f93487e097af8d602276b0000dbc14c2799",
    "worker.json": "3671b5ac0ea8229715ae689a93fc51d0a6fa73e69e36e5b40710d6d14ed51f92",
    "run.json": "945429f2ddc6b8419c6c572206b3cc65da8c4bc312ffa30d195273b9bcc9c942",
}


def receipt(name):
    return json.loads((EVIDENCE / name).read_bytes())


class NetworkBootFailureEvidenceTests(unittest.TestCase):
    def test_original_bytes_and_failure_envelope(self):
        self.assertEqual({p.name for p in EVIDENCE.iterdir()}, set(HASHES) | {"README.md", "output.log"})
        for name, digest in HASHES.items():
            self.assertEqual(hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest(), digest)
        self.assertEqual((EVIDENCE / "output.log").read_bytes(), (EVIDENCE / "validation.json").read_bytes())
        self.assertEqual(producer((EVIDENCE / "run.json").read_bytes()), PRODUCER)
        run = receipt("run.json")
        self.assertEqual((run["result"], run["started_utc"], run["ended_utc"]),
                         ("fail", "2026-10-22T23:45:24Z", "2026-10-22T23:47:02Z"))
        self.assertFalse(run["hardware_validated"])

    def test_missing_root_cannot_be_promoted_or_retroactively_diagnosed(self):
        worker = receipt("worker.json")
        self.assertEqual(worker["assembly"]["result"], "pass")
        self.assertEqual(worker["stage"], "boot")
        self.assertEqual(worker["qemu"]["proof"], {"initrd": True, "root": False, "complete": False,
                         "guest_failure": None, "protocol_error": "incomplete"})
        self.assertEqual(worker["qemu"]["terminal"], {"exit_code": 0, "failure_kind": None,
                         "process_group_cleaned": True, "stderr_bytes": 0, "stdout_bytes": 0})
        for value in (worker, worker["qemu"], receipt("validation.json")):
            self.assertEqual(value["result"], "fail")
            self.assertFalse(value["boot_verified"] or value["confidentiality_verified"])
        self.assertNotIn("network_failure", worker["qemu"]["proof"])

    def test_original_profile_sources_and_independent_cleanup(self):
        worker, state = receipt("worker.json"), receipt("resources.json")
        self.assertEqual(worker["source"]["profile"], "guest-network/v1")
        self.assertEqual(len(worker["source"]["sources"]), 68)
        for label in ("source", "debug_source"):
            self.assertEqual(state["producer"][label], worker[label])
            self.assertEqual(worker[label]["git_sha"], PRODUCER)
            for name, digest in worker[label]["sources"].items():
                self.assertEqual(hashlib.sha256(git(ROOT, "show", PRODUCER + ":" + name)).hexdigest(), digest)
        for key in ("empty_verified", "group_removed", "limits_read_back"):
            self.assertTrue(state[key])
        self.assertEqual((state["child_exit_code"], state["oom_kill"], state["memory_peak_bytes"]), (1, 0, 2147483648))
        self.assertTrue(receipt("validation.json")["private_images_removed"])
        self.assertFalse(worker["private_work_removed"])


if __name__ == "__main__":
    unittest.main()
