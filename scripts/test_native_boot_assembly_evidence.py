# SPDX-License-Identifier: Apache-2.0
"""Preserve original native assembly proof without promoting it to a boot."""
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1] / "evidence/p3/2026-10-22-native-boot-assembly-pass"
PINS = {
    "run.json": "cfed0ded97592ca95695c6f762689de1fa626b06b8fa34f8fcc9fb4c67206408",
    "result.json": "6f4580912735f7771cfa6187e1752f928e5fdf45111629ac53bf0448d466b0e7",
    "evidence-resources.json": "3301db630bbf18f8fa39b1fc83955b284a414e9f29522138b9fa0896abcc66c0",
    "boot-inspect.stdout": "3319a71430496387b14e3a48c9476860405486a3c86ea295661529550aa4a8c2",
    "verity-reject-root.stderr": "c9ae63461387c26b7188a74b8ab384a9fcf3e560eeefce4cc8d315f823507f9f",
}


class NativeBootAssemblyEvidenceTests(unittest.TestCase):
    def test_original_bytes_and_cleanup(self):
        for name, digest in PINS.items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest)
        self.assertEqual((ROOT / "output.log").read_bytes(), (ROOT / "result.json").read_bytes())
        for name in ("launcher.stdout", "launcher.stderr"):
            self.assertEqual((ROOT / name).read_bytes(), b"")
        run = json.loads((ROOT / "run.json").read_bytes())
        result = json.loads((ROOT / "result.json").read_bytes())
        resources = json.loads((ROOT / "evidence-resources.json").read_bytes())
        self.assertEqual(run["git_sha"], "eee2388421e09568ae28951d0d747c84267f1fea")
        self.assertEqual(result["git_sha"], run["git_sha"])
        self.assertEqual(run["result"], "pass")
        self.assertFalse(run["hardware_validated"])
        self.assertEqual(result["stage"], "complete")
        self.assertEqual(result["result"], "pass")
        self.assertIsNone(result["failure_kind"])
        self.assertTrue(result["process_cleanup_confirmed"])
        self.assertTrue(result["payload_removed"])
        for key in ("empty_verified", "group_removed", "limits_read_back"):
            self.assertTrue(resources[key])
        self.assertEqual(resources["result"], "pass")
        self.assertEqual(resources["child_exit_code"], 0)
        self.assertEqual(resources["oom_kill"], 0)
        self.assertEqual(resources["memory_peak_bytes"], 7106478080)
        self.assertLess(resources["memory_peak_bytes"], int(resources["limits"]["memory.max"]))

    def test_native_sections_match_without_boot_or_confidential_claim(self):
        result = json.loads((ROOT / "result.json").read_bytes())
        worker = result["worker"]
        boot, verity = worker["boot_uki"], worker["verity_root"]
        self.assertEqual(worker["installed_packages"], 212)
        self.assertEqual(worker["boot_input_diagnostics"], {})
        for row in (result, worker):
            self.assertFalse(row["bootable"])
            self.assertFalse(row["deployable"])
        for row in (boot, verity):
            self.assertEqual(row["result"], "pass")
            self.assertTrue(row["private_images_removed"])
            for key in ("boot_verified", "images_exported", "reproducibility_verified", "confidentiality_verified"):
                self.assertFalse(row[key])
        self.assertTrue(verity["userspace_verity_verified"])
        self.assertTrue(verity["wrong_root_rejected"])
        self.assertFalse(verity["kernel_verity_verified"])
        self.assertEqual(boot["root_hash"], verity["root_hash"])
        self.assertTrue(boot["archive_checked"])
        self.assertTrue(boot["sections_checked"])
        self.assertFalse(boot["signed"])
        self.assertEqual(boot["initrd_members"], 2359)
        self.assertEqual(boot["initrd"]["bytes"], 82744320)
        self.assertEqual(boot["inputs"]["sha256"],
                         "b10eb64d60d8cfeba7de15b8533e2f7f8b38f56594bfa4abe90b4ff55ba6971b")
        uki = json.loads((ROOT / "boot-inspect.stdout").read_bytes())
        self.assertEqual(boot["uki"], uki)
        self.assertEqual(uki["image"]["bytes"], 97898496)
        self.assertEqual(uki["sections"][".initrd"], boot["initrd"])
        self.assertEqual(uki["sections"][".osrel"], {
            "bytes": 445, "sha256": "27294876c0c569b1f50960119e09e149bc2436aaff672528931a81d4ffc79b8b"})


if __name__ == "__main__":
    unittest.main()
