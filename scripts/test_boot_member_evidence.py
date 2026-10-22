# SPDX-License-Identifier: Apache-2.0
"""Original failed native receipt, not a hardware or successful boot claim."""
import hashlib
import json
from pathlib import Path
import unittest


class EvidenceTests(unittest.TestCase):
    def test_original_failure_and_partial_success_are_preserved(self):
        root = Path(__file__).resolve().parent.parent / "evidence/p3/2026-10-22-boot-member-rejected"
        pins = {
            "run.json": "a4d0e0cb6dc3f5ba09ed8c8ab35fccfad00bb6a182f7be8b4c8a5dcbe78189b6",
            "result.json": "7ba63d04be2662ff3e3fcb3cda09c3251f3d367b542596640c9d310f245d360f",
            "evidence-resources.json": "55a68dfee7e0fadd57413c2e2a5576d86086cca09446aa6a603dd7a6a366aeb7",
        }
        data = {}
        for name, digest in pins.items():
            raw = (root / name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            data[name] = json.loads(raw)
        run, result, resources = (data[n] for n in pins)
        self.assertEqual(run["git_sha"], "527adf09912614515c8313df82499643be2c19a8")
        self.assertEqual(run["result"], "fail")
        self.assertFalse(run["hardware_validated"])
        worker = result["worker"]
        self.assertEqual(worker["stage"], "boot-uki")
        self.assertEqual(worker["result"], "fail")
        self.assertNotIn("boot_uki", worker)
        self.assertEqual(worker["verity_root"]["result"], "pass")
        self.assertTrue(worker["verity_root"]["wrong_root_rejected"])
        self.assertFalse(worker["verity_root"]["boot_verified"])
        self.assertTrue(result["process_cleanup_confirmed"])
        self.assertTrue(result["payload_removed"])
        self.assertTrue(resources["empty_verified"])
        self.assertEqual(resources["oom_kill"], 0)
