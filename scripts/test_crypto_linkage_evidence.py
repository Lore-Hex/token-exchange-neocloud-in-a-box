# SPDX-License-Identifier: Apache-2.0
"""Pin the native linkage result, without claiming retained symbol-table output."""
import hashlib
import json
from pathlib import Path
import unittest


class CryptoLinkageEvidenceTests(unittest.TestCase):
    def test_native_linkage_report_and_cleanup(self):
        root = Path(__file__).resolve().parents[1] / "evidence/p3/2026-10-22-crypto-linkage-pass"
        pins = {
            "run.json": "c8987721c850e9673bc04dd573ba54de6be3ff5e0c484dcb6ad7a4421564b294",
            "result.json": "4ed3f86d4e4efa7816791f0a9161c27368c4e2d3fe2c8e3a53916faf3d2648f5",
            "evidence-resources.json": "9fffaf8a0cd864fabb9514e0f70832b37abec6a88773415104a848cac50a98b4",
        }
        for name, digest in pins.items():
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest)
        run = json.loads((root / "run.json").read_bytes())
        result = json.loads((root / "result.json").read_bytes())
        resources = json.loads((root / "evidence-resources.json").read_bytes())
        self.assertEqual(run["git_sha"], "6bf7880b2c5fa59299c0324cc79107803a2c6b5f")
        self.assertEqual(result["git_sha"], run["git_sha"])
        self.assertFalse(run["hardware_validated"])
        self.assertTrue(result["payload_removed"])
        self.assertTrue(result["process_cleanup_confirmed"])
        driver = result["worker"]["driver_build"]
        self.assertEqual(driver["crypto_references"],
                         ["crypto_alloc_aead", "crypto_alloc_akcipher", "crypto_alloc_shash"])
        self.assertTrue(driver["crypto_linkage_verified"])
        for record in (run, result, resources, driver):
            self.assertEqual(record["result"], "pass")
        for name in ("installed", "loaded", "lkca_verified", "bootable", "reproducibility_verified"):
            self.assertFalse(driver[name])
        for name in ("empty_verified", "group_removed", "limits_read_back"):
            self.assertTrue(resources[name])
        self.assertEqual(resources["oom_kill"], 0)
        self.assertEqual(resources["populated_before_cleanup"], 0)
        self.assertLess(resources["memory_peak_bytes"], int(resources["limits"]["memory.max"]))
        # This run checked the table, but its parent omitted it from export.
        # The omission is fixed separately; never invent the missing original.
        self.assertFalse((root / "dkms-symbols.stdout").exists())


if __name__ == "__main__":
    unittest.main()
