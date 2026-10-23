# SPDX-License-Identifier: Apache-2.0
"""Original fixed config-read failure, without inventing its rejected subcheck."""
import hashlib
import json
from pathlib import Path
import unittest

from test_evidence_history import git, producer

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p3/2026-10-23-debug-network-config-read"
PRODUCER = "4e6a415dcd813736f6db9434b1ed3ee0ace80f4c"
HASHES = {
    "validation.json": "afa21d099f290f5aeef626cf1993efe47ee34602f24ada4ff7dd3d5d07442426",
    "resources.json": "bd39ffd3876f4076cc0eb18252cbc3316863a0313390e57f846d4b187cd05c9b",
    "worker.json": "2b4d1cb6094c884ed180cb7e0e3a7c45fce4d355e49031c25300fecaa74ce599",
    "run.json": "4a803517353b79f9c177fe327004593204f479050a7ca220129c3204bcc47eab",
}


def receipt(name):
    return json.loads((EVIDENCE / name).read_bytes())


class ConfigReadFailureEvidenceTests(unittest.TestCase):
    def test_original_bytes_and_failure_envelope(self):
        self.assertEqual({p.name for p in EVIDENCE.iterdir()}, set(HASHES) | {"README.md", "output.log"})
        for name, digest in HASHES.items():
            self.assertEqual(hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest(), digest)
        self.assertEqual((EVIDENCE / "output.log").read_bytes(), (EVIDENCE / "validation.json").read_bytes())
        self.assertEqual(producer((EVIDENCE / "run.json").read_bytes()), PRODUCER)
        run = receipt("run.json")
        self.assertEqual((run["result"], run["started_utc"], run["ended_utc"]),
                         ("fail", "2026-10-23T00:42:19Z", "2026-10-23T00:44:03Z"))
        self.assertFalse(run["hardware_validated"])

    def test_config_read_is_failure_not_a_retroactive_subcheck_diagnosis(self):
        worker = receipt("worker.json")
        self.assertEqual(worker["assembly"]["result"], "pass")
        self.assertEqual(worker["stage"], "boot")
        self.assertEqual(worker["qemu"]["proof"], {"initrd": True, "root": False, "complete": False,
            "guest_failure": "NETWORK_SERVICE", "protocol_error": None,
            "network_failure": {"service_result": "exit-code", "exit_code": "exited",
                                "exit_status": 67, "stage": "config-read"}})
        self.assertEqual(worker["qemu"]["terminal"], {"exit_code": 0, "failure_kind": None,
                         "process_group_cleaned": True, "stderr_bytes": 0, "stdout_bytes": 0})
        for value in (worker, worker["qemu"], receipt("validation.json")):
            self.assertEqual(value["result"], "fail")
            self.assertFalse(value["boot_verified"] or value["confidentiality_verified"])

    def test_source_closure_and_independent_cleanup(self):
        worker, state = receipt("worker.json"), receipt("resources.json")
        self.assertEqual(worker["source"]["profile"], "guest-network/v1")
        self.assertEqual(len(worker["source"]["sources"]), 68)
        self.assertEqual(len(worker["debug_source"]["sources"]), 30)
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
