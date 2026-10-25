# SPDX-License-Identifier: Apache-2.0
"""Preserve the real CPU boot without promoting it to hardware qualification."""
import hashlib
from pathlib import Path
import unittest

from mkosi_export import parsed
from test_evidence_history import git, producer

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p3/2026-10-25-debug-network-pass"
PRODUCER = "a1427d7e7e6d7fdd337badcc6c77987ff61e9ed5"
HASHES = {
    "run.json": "117b0763e8604cbe7cfe5d479c64a6c44e2899b152880f61a707130607079325",
    "worker.json": "e32e952f113e441b032905b52684fe2a2e18c1a3b702916f62c2e47411a94598",
    "validation.json": "523524d207a543ae290d44858325065a7f5022f921752fcd86cfae5165738051",
    "resources.json": "75f37a91b8ff1b1f28b9f7d9164c2d3cb6111ea76338bbb0d08f0ebdb1794285",
}


def receipt(name):
    return parsed((EVIDENCE / name).read_bytes())


class NetworkBootPassEvidenceTests(unittest.TestCase):
    def test_original_receipts_and_source_identity(self):
        self.assertEqual({p.name for p in EVIDENCE.iterdir()}, set(HASHES) | {"README.md", "output.log"})
        for name, digest in HASHES.items():
            self.assertEqual(hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest(), digest)
        self.assertEqual((EVIDENCE / "output.log").read_bytes(), (EVIDENCE / "validation.json").read_bytes())
        self.assertEqual(producer((EVIDENCE / "run.json").read_bytes()), PRODUCER)
        run = receipt("run.json")
        self.assertEqual((run["result"], run["started_utc"], run["ended_utc"]),
                         ("pass", "2026-10-25T10:31:48Z", "2026-10-25T10:34:13Z"))
        self.assertEqual(run["execution"], "software")
        self.assertFalse(run["hardware_validated"])
        worker, resources = receipt("worker.json"), receipt("resources.json")
        for label in ("source", "debug_source"):
            self.assertEqual(worker[label], resources["producer"][label])
            self.assertEqual(worker[label]["git_sha"], PRODUCER)
            for path, digest in worker[label]["sources"].items():
                self.assertEqual(hashlib.sha256(git(ROOT, "show", PRODUCER + ":" + path)).hexdigest(), digest)

    def test_boot_and_outer_cleanup_without_confidential_claim(self):
        worker, resources, validation = (receipt(name) for name in
                                        ("worker.json", "resources.json", "validation.json"))
        self.assertEqual(worker["assembly"]["result"], "pass")
        self.assertEqual(worker["qemu"]["proof"], {"initrd": True, "root": True,
            "complete": True, "guest_failure": None, "protocol_error": None})
        self.assertTrue(worker["qemu"]["tcg_only"])
        self.assertEqual(worker["qemu"]["terminal"], {"exit_code": 0, "failure_kind": None,
            "process_group_cleaned": True, "stderr_bytes": 0, "stdout_bytes": 0})
        for value in (worker, worker["qemu"], validation):
            self.assertEqual(value["result"], "pass")
            self.assertTrue(value["boot_verified"])
            self.assertFalse(value["confidentiality_verified"])
        self.assertFalse(worker["private_work_removed"])
        self.assertTrue(validation["private_images_removed"])
        for key in ("production_service_verified", "reproducibility_verified", "signed"):
            self.assertFalse(validation[key])
        for key in ("empty_verified", "group_removed", "limits_read_back"):
            self.assertTrue(resources[key])
        self.assertEqual((resources["child_exit_code"], resources["oom_kill"],
                          resources["populated_before_cleanup"]), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
