# SPDX-License-Identifier: Apache-2.0
"""Keep the observed count rejection separate from the source-level diagnosis."""
import hashlib
from pathlib import Path
import unittest

from mkosi_export import parsed
from test_evidence_history import git, producer

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p3/2026-10-25-debug-network-count"
PRODUCER = "987b0124be192dd0a27d8a8c05321aa9bc95fc52"
HASHES = {
    "worker.json": "060c44e2cfbf4a7d88244aa614805277603bf796b7d5ef4be83bf8bdb72d5690",
    "resources.json": "0e2d1a41ecf736e532b7a7facf224a1c4ded05a08036437be80ac1a593d5a70b",
    "validation.json": "afa21d099f290f5aeef626cf1993efe47ee34602f24ada4ff7dd3d5d07442426",
    "run.json": "d104d771fb6fd672e328e073efe01517948ea856c1c73feeb6eba408a340c13f",
}


def receipt(name):
    return parsed((EVIDENCE / name).read_bytes())


class NetworkCountEvidenceTests(unittest.TestCase):
    def test_originals_and_producer(self):
        self.assertEqual({p.name for p in EVIDENCE.iterdir()}, set(HASHES) | {"README.md", "output.log"})
        for name, digest in HASHES.items():
            self.assertEqual(hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest(), digest)
        self.assertEqual((EVIDENCE / "output.log").read_bytes(), (EVIDENCE / "validation.json").read_bytes())
        self.assertEqual(producer((EVIDENCE / "run.json").read_bytes()), PRODUCER)
        run = receipt("run.json")
        self.assertEqual((run["result"], run["started_utc"], run["ended_utc"]),
                         ("fail", "2026-10-25T09:27:27Z", "2026-10-25T09:29:07Z"))
        self.assertFalse(run["hardware_validated"])
        worker, resources = receipt("worker.json"), receipt("resources.json")
        for label in ("source", "debug_source"):
            self.assertEqual(worker[label], resources["producer"][label])
            self.assertEqual(worker[label]["git_sha"], PRODUCER)
            for path, digest in worker[label]["sources"].items():
                self.assertEqual(hashlib.sha256(git(ROOT, "show", PRODUCER + ":" + path)).hexdigest(), digest)

    def test_fail_closed_and_independent_cleanup(self):
        worker, resources = receipt("worker.json"), receipt("resources.json")
        self.assertEqual(worker["assembly"]["result"], "pass")
        self.assertEqual(worker["qemu"]["proof"], {
            "initrd": True, "root": False, "complete": False, "guest_failure": "NETWORK_SERVICE",
            "protocol_error": None, "network_failure": {"exit_code": "exited", "exit_status": 96,
                "service_result": "exit-code", "stage": "readback-count"}})
        self.assertEqual(worker["qemu"]["terminal"], {"exit_code": 0, "failure_kind": None,
            "process_group_cleaned": True, "stderr_bytes": 0, "stdout_bytes": 0})
        for value in (worker, worker["qemu"], receipt("validation.json")):
            self.assertEqual(value["result"], "fail")
            self.assertFalse(value["boot_verified"] or value["confidentiality_verified"])
        self.assertTrue(receipt("validation.json")["private_images_removed"])
        for key in ("empty_verified", "group_removed", "limits_read_back"):
            self.assertTrue(resources[key])
        self.assertEqual((resources["child_exit_code"], resources["oom_kill"],
                          resources["populated_before_cleanup"]), (1, 0, 0))


if __name__ == "__main__":
    unittest.main()
