# SPDX-License-Identifier: Apache-2.0
"""Bind actual native export metadata while preserving its proof boundaries."""

import hashlib
import json
from pathlib import Path
import unittest

import mkosi_export

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p3/2026-10-22-native-private-export-pass"
PRODUCER = "db23faee25adddccd5d090ca55ec3120d5f7a957"
HASHES = {
    "manifest.json": "ffddd3377243eeb94b30afdbd0eb699ec2fc36d3cf19cd702fbc0fb5403cdb31",
    "resources.json": "70f0cadb2bbc1d6eaa4e6d32d7e5a10794a32d2109e5f4de301706f5a8f1af6e",
    "result.json": "a6c5d8d25b1c4c290bc9b548947b51e617385ecd7156c9b6c744551c9fef393c",
    "run.json": "9251b06a7a818f379def08e7b23e1cfc89bb858dd3f4925557fa72287c27b105",
    "validation.json": "085e0e8eeda0403bc4c0d25a0a2c94071cb2c966d2c29c316fb5ea30d7404eee",
}


def receipt(name):
    return json.loads((EVIDENCE / name).read_bytes())


class NativeExportEvidenceTests(unittest.TestCase):
    def test_original_files_are_preserved_without_binary_artifacts(self):
        self.assertEqual({path.name for path in EVIDENCE.iterdir()},
                         set(HASHES) | {"README.md", "output.log"})
        for name, digest in HASHES.items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest(), digest)
        self.assertEqual((EVIDENCE / "output.log").read_bytes(),
                         (EVIDENCE / "validation.json").read_bytes())
        envelope = receipt("run.json")
        self.assertEqual(envelope["git_sha"], PRODUCER)
        self.assertEqual(envelope["started_utc"], "2026-10-22T17:50:25Z")
        self.assertEqual(envelope["ended_utc"], "2026-10-22T17:54:47Z")
        self.assertEqual(envelope["result"], "pass")
        self.assertEqual(envelope["execution"], "software")
        self.assertFalse(envelope["hardware_validated"])

    def test_actual_export_receipts_bind_build_files_and_resource_cleanup(self):
        manifest, validation = receipt("manifest.json"), receipt("validation.json")
        report, resources = receipt("result.json"), receipt("resources.json")
        self.assertEqual(manifest["source"], validation["source"])
        self.assertEqual(manifest["source"]["git_sha"], PRODUCER)
        self.assertEqual(len(manifest["source"]["sources"]), 42)
        staged = mkosi_export.validate_proof(report, manifest["source"])
        mkosi_export.validate_resources(resources)
        self.assertEqual(manifest["files"], staged["files"])
        self.assertEqual(validation["files"], staged["files"])
        self.assertEqual(manifest["root_hash"], staged["root_hash"])
        self.assertEqual(validation["root_hash"], staged["root_hash"])
        for name, expected in manifest["receipts"].items():
            raw = (EVIDENCE / name).read_bytes()
            self.assertEqual(expected, {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        self.assertEqual(manifest["files"]["root.raw"]["bytes"], 928989184)
        self.assertEqual(manifest["files"]["nib.efi"]["bytes"], 97898496)
        self.assertEqual(validation["result"], "pass")
        self.assertEqual(validation["build_result"], "success")
        for key in ("artifacts_verified", "private_images_removed", "metadata_only"):
            self.assertTrue(validation[key])
        self.assertEqual(resources["memory_peak_bytes"], 7616405504)
        self.assertEqual(resources["oom_kill"], 0)
        self.assertEqual(resources["child_exit_code"], 0)
        for key in ("empty_verified", "group_removed", "limits_read_back"):
            self.assertTrue(resources[key])

    def test_export_is_not_boot_signing_or_reproducibility(self):
        report = receipt("result.json")
        for row in (receipt("manifest.json"), receipt("validation.json")):
            for key in ("boot_verified", "confidentiality_verified", "signed", "reproducibility_verified"):
                self.assertFalse(row[key])
        for row in (report, report["worker"]):
            self.assertFalse(row["bootable"])
            self.assertFalse(row["deployable"])
        self.assertFalse(report["worker"]["verity_root"]["kernel_verity_verified"])
        previous = json.loads((ROOT / "evidence/p3/2026-10-22-native-boot-assembly-pass/result.json").read_bytes())
        self.assertNotEqual(previous["worker"]["verity_root"]["root_hash"],
                            report["worker"]["verity_root"]["root_hash"])
        self.assertEqual(previous["worker"]["boot_uki"]["initrd"],
                         report["worker"]["boot_uki"]["initrd"])


if __name__ == "__main__":
    unittest.main()
