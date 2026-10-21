# SPDX-License-Identifier: Apache-2.0
"""Preserve the original bounded installation result, not a boot claim."""
import hashlib
import json
from pathlib import Path
import unittest


class NativeInstallationEvidenceTests(unittest.TestCase):
    def test_original_package_install_and_independent_cleanup(self):
        root = Path(__file__).resolve().parents[1] / "evidence/p3/2026-10-21-native-install-pass"
        pins = {
            "run.json": "aca9d39cb5eb275274f9598fab4964055221664cf31510ec3fc3189b506e2b14",
            "result.json": "2296e2b19b3ceaa55584c9d12ed13f368749aa0abc504d18102d5d54054d1b12",
            "evidence-resources.json": "3916380bd01e3f4a90509597d4f7aae052230cbcf1fd0e6e2ff4e2d4e92775bd",
            "installer.stderr": "7051a1570262c885ee8aa0b3acc42b352ee54cf52540123e30e7c7407661c841",
            "launcher.stdout": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "launcher.stderr": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        }
        for name, digest in pins.items():
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest)
        run = json.loads((root / "run.json").read_bytes())
        result = json.loads((root / "result.json").read_bytes())
        resources = json.loads((root / "evidence-resources.json").read_bytes())
        self.assertEqual(run["git_sha"], "66ef40db80da15cc54305872815d5e40d67f134f")
        self.assertEqual(result["git_sha"], run["git_sha"])
        self.assertEqual(run["execution"], "software")
        self.assertFalse(run["hardware_validated"])
        for record in (run, result, result["worker"], resources):
            self.assertEqual(record["result"], "pass")
        for name in ("payload_removed", "process_cleanup_confirmed"):
            self.assertIs(result[name], True)
        for name in ("bootable", "deployable"):
            self.assertIs(result[name], False)
            self.assertIs(result["worker"][name], False)
        worker = result["worker"]
        self.assertEqual(worker["installed_packages"], 145)
        self.assertEqual(worker["installer_exit_code"], 0)
        self.assertIs(worker["configured_state_verified"], True)
        self.assertIs(worker["package_installation_executed"], True)
        self.assertIs(worker["dkms_built"], False)
        self.assertIs(worker["reproducibility_verified"], False)
        for name in ("empty_verified", "group_removed", "limits_read_back"):
            self.assertIs(resources[name], True)
        self.assertEqual(resources["oom_kill"], 0)
        self.assertEqual(resources["populated_before_cleanup"], 0)
        self.assertLess(resources["memory_peak_bytes"], int(resources["limits"]["memory.max"]))


if __name__ == "__main__":
    unittest.main()
