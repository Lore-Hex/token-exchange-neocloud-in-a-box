# SPDX-License-Identifier: Apache-2.0
"""Preserve the native installation proof without promoting it to a boot result."""
import hashlib
import json
from pathlib import Path
import unittest

import mkosi_dkms


ROOT = Path(__file__).resolve().parents[1] / "evidence/p3/2026-10-22-driver-install-pass"


class DriverInstallEvidenceTests(unittest.TestCase):
    def test_original_receipts_and_diagnostics(self):
        pins = {
            "run.json": "575319260fc781f9194fb3ec246aea2ae6bfbb32656b10dbb9d8b695063fdebc",
            "result.json": "c8f98319e596f3bc77b201372fd3f1a607bb0d0b158a7791055fdbed12e2bc50",
            "evidence-resources.json": "242dc921b046ffffac793623364da7c950b429ef42093ae037ac14fd3470b65a",
            "output.log": "4f2470a30b9b0653955fcbd15d1bbdd5affa549e8285307051c300e12130755b",
            "dkms-symbols.stdout": "38771d47badea124cdfc6d3a74787fa033aa50bd68e20f90b51488315b81ae77",
            "dkms-build.stderr": "7f2a6a7b6607340cd067a9b0cf807c84083b7b2342ee24966eef1ec54a7371ca",
        }
        self.assertEqual({path.name for path in ROOT.iterdir()}, set(pins))
        for name, digest in pins.items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest)
        installed = (ROOT / "output.log").read_text().splitlines()
        self.assertEqual(installed, [
            "Installing /lib/modules/6.8.0-147-generic/updates/dkms/" + name + ".ko"
            for name in mkosi_dkms.MODULES
        ] + ["Running depmod... done."])
        # Keep the actual compression warnings; verified objects are uncompressed ELF.
        self.assertEqual((ROOT / "dkms-build.stderr").read_bytes(),
                         b"/usr/sbin/dkms: line 1675: zstd: command not found\n" * 5)

    def test_installation_claim_is_narrow_and_cleanup_complete(self):
        run = json.loads((ROOT / "run.json").read_bytes())
        result = json.loads((ROOT / "result.json").read_bytes())
        resources = json.loads((ROOT / "evidence-resources.json").read_bytes())
        worker = result["worker"]
        driver = worker["driver_build"]
        self.assertEqual(run["git_sha"], "3987926d4e13e2312fbe468ae992fc060b8272ac")
        self.assertEqual(result["git_sha"], run["git_sha"])
        self.assertIn("--compile-driver --install-driver", run["command"])
        self.assertEqual(run["execution"], "software")
        self.assertEqual(run["host"]["gpus"], "none")
        self.assertFalse(run["hardware_validated"])
        for record in (run, result, worker, driver, resources):
            self.assertEqual(record["result"], "pass")
        self.assertEqual(worker["installer_exit_code"], 0)
        self.assertEqual(worker["installed_packages"], 145)
        self.assertTrue(worker["configured_state_verified"])
        self.assertTrue(worker["dkms_built"])
        self.assertTrue(driver["installed"])
        self.assertTrue(driver["crypto_linkage_verified"])
        self.assertEqual(driver["kernel"], "6.8.0-147-generic")
        self.assertEqual(driver["driver"], "595.91.07")
        self.assertEqual(mkosi_dkms.crypto_references((ROOT / "dkms-symbols.stdout").read_bytes()),
                         driver["crypto_references"])
        previous = json.loads((ROOT.parent / "2026-10-22-crypto-symbols-pass/result.json").read_bytes())
        self.assertEqual(driver["modules"], previous["worker"]["driver_build"]["modules"])
        self.assertEqual(set(driver["dependency_indexes"]), {
            "modules.dep", "modules.dep.bin", "modules.alias.bin", "modules.symbols.bin"})
        for index in driver["dependency_indexes"].values():
            self.assertGreater(index["bytes"], 0)
            self.assertLessEqual(index["bytes"], 8 << 20)
            self.assertRegex(index["sha256"], r"^[0-9a-f]{64}$")
        for name in ("loaded", "lkca_verified", "bootable", "reproducibility_verified"):
            self.assertFalse(driver[name])
        for record in (worker, result):
            self.assertFalse(record["bootable"])
            self.assertFalse(record["deployable"])
        self.assertTrue(result["payload_removed"])
        self.assertTrue(result["process_cleanup_confirmed"])
        for name in ("empty_verified", "group_removed", "limits_read_back"):
            self.assertTrue(resources[name])
        self.assertEqual(resources["child_exit_code"], 0)
        self.assertEqual(resources["oom_kill"], 0)
        self.assertEqual(resources["populated_before_cleanup"], 0)
        self.assertEqual(resources["limits"]["memory.swap.max"], "0")
        self.assertLess(resources["memory_peak_bytes"], int(resources["limits"]["memory.max"]))


if __name__ == "__main__":
    unittest.main()
