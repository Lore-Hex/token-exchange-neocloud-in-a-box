# SPDX-License-Identifier: Apache-2.0
"""Pin the native linkage result, without claiming retained symbol-table output."""
import hashlib
import json
from pathlib import Path
import unittest

import mkosi_dkms


class CryptoLinkageEvidenceTests(unittest.TestCase):
    def test_retained_raw_symbol_table_matches_native_receipts(self):
        evidence = Path(__file__).resolve().parents[1] / "evidence/p3"
        root = evidence / "2026-10-22-crypto-symbols-pass"
        pins = {
            "run.json": "02d33403c4d9fdf61fb49bdc320a75ddf8b2001c79a0ba3903c5466fcdbd63b9",
            "result.json": "07ff0ec916c82c99cd307828073f477165d85e588a83b1c8c3ffca2bb831c199",
            "evidence-resources.json": "bb44e4286357708e235b07f0338bb66b9db03644bd7a5ff97b8bca67ecc17399",
            "output.log": "38771d47badea124cdfc6d3a74787fa033aa50bd68e20f90b51488315b81ae77",
        }
        for name, digest in pins.items():
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest)
        run = json.loads((root / "run.json").read_bytes())
        result = json.loads((root / "result.json").read_bytes())
        resources = json.loads((root / "evidence-resources.json").read_bytes())
        driver = result["worker"]["driver_build"]
        self.assertEqual(run["git_sha"], "e01eb89fee56f36686b27c74dd92713970db93f4")
        self.assertEqual(result["git_sha"], run["git_sha"])
        self.assertEqual(mkosi_dkms.crypto_references((root / "output.log").read_bytes()),
                         driver["crypto_references"])
        self.assertTrue(driver["crypto_linkage_verified"])
        previous = json.loads((evidence / "2026-10-22-crypto-linkage-pass/result.json").read_bytes())
        self.assertEqual(driver["modules"], previous["worker"]["driver_build"]["modules"])
        self.assertEqual(result["worker"]["installed_packages"], 145)
        self.assertTrue(result["worker"]["configured_state_verified"])
        self.assertTrue(result["payload_removed"])
        self.assertTrue(result["process_cleanup_confirmed"])
        for row in (run, result, resources, driver):
            self.assertEqual(row["result"], "pass")
        self.assertFalse(run["hardware_validated"])
        for name in ("installed", "loaded", "lkca_verified", "bootable", "reproducibility_verified"):
            self.assertFalse(driver[name])
        for name in ("empty_verified", "group_removed", "limits_read_back"):
            self.assertTrue(resources[name])
        self.assertEqual(resources["oom_kill"], 0)
        self.assertEqual(resources["populated_before_cleanup"], 0)
        self.assertLess(resources["memory_peak_bytes"], int(resources["limits"]["memory.max"]))

    def test_original_pre_execution_timeout_is_preserved(self):
        root = Path(__file__).resolve().parents[1] / "evidence/p3/2026-10-22-input-fetch-timeout"
        raw = (root / "output.log").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         "f52a99a94893e34c886961c45ea0f65a6fcabfa7a7ce0650a2b22739416a36c9")
        report = json.loads(raw)
        self.assertEqual(report["result"], "fail")
        self.assertEqual(report["returncode"], 28)
        self.assertEqual(report["expected_bytes"], 77218)
        self.assertFalse(report["package_execution"])
        self.assertFalse((root / "run.json").exists())

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
