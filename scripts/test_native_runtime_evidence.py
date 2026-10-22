# SPDX-License-Identifier: Apache-2.0
"""Keep native runtime installation proof separate from boot qualification."""
import hashlib
import json
from pathlib import Path
import unittest


class NativeRuntimeEvidenceTests(unittest.TestCase):
    def test_original_runtime_installation_and_cleanup(self):
        root = Path(__file__).resolve().parents[1] / "evidence/p3/2026-10-22-native-runtime-pass"
        pins = {
            "dkms-add.stdout": "275d4618982a880d9ce70253e2fac680dc6fb6da9736dcd6a383f0ee09f9c65a",
            "dkms-build.stdout": "5b43287c58f20711bc74d2ff29884e0a47336ef5ab8bdd179a4e739a3365c8eb",
            "dkms-compiler.stdout": "bb6aade8c4ca40cae9cb1be8abe12377f651fccd82954af74ec61703b6608fc5",
            "dkms-install.stdout": "4f2470a30b9b0653955fcbd15d1bbdd5affa549e8285307051c300e12130755b",
            "dkms-make-1.log": "164b8d074d14a6f374d04483dda9d1fd8028111eab6a0ae8491341284a9df8f6",
            "dkms-symbols.stdout": "38771d47badea124cdfc6d3a74787fa033aa50bd68e20f90b51488315b81ae77",
            "evidence-resources.json": "4793259235234898653221f3534f57b77c1832aeca0519de12c0d9410a221b18",
            "input-fetch.log": "2058ec39648ded5c44ca3db3a9c0d42866ff5c5dfb08b9e92613a77c25d5a1f8",
            "installer.stderr": "fd197e68a281e76f620a995d45cf76aea6c14a00bb7c4a63fb3e36d425c30194",
            "launcher.stderr": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "launcher.stdout": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "result.json": "64d23154836ec222b99c724b4d3e9ea05e00002b47dd8aa5c5614d2b82c78c3c",
            "run.json": "2391668c4b2a55d00a8e0f8a654e6b921df3973daed4c82133856725bd2b4d0d",
            "runtime-hook-review.json": "0c25d71c0b9472dbbd8f1a7bc21f57ae35514a98fd495e377f7af11fc9505d0c",
        }
        self.assertEqual({p.name for p in root.iterdir()}, set(pins))
        for name, digest in pins.items():
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest)
        run = json.loads((root / "run.json").read_bytes())
        result = json.loads((root / "result.json").read_bytes())
        resources = json.loads((root / "evidence-resources.json").read_bytes())
        worker = result["worker"]
        driver = worker["driver_build"]
        self.assertEqual(run["git_sha"], "97c8a1e40e39f2286641586b3d7bd3b0a4b97d4f")
        self.assertEqual(result["git_sha"], run["git_sha"])
        self.assertEqual(run["execution"], "software")
        self.assertIs(run["hardware_validated"], False)
        for record in (run, result, worker, driver, resources):
            self.assertEqual(record["result"], "pass")
        self.assertEqual(worker["installed_packages"], 212)
        self.assertEqual(worker["installer_exit_code"], 0)
        for name in ("configured_state_verified", "package_installation_executed", "dkms_built"):
            self.assertIs(worker[name], True)
        for name in ("payload_removed", "process_cleanup_confirmed"):
            self.assertIs(result[name], True)
        for record in (result, worker):
            for name in ("bootable", "deployable"):
                self.assertIs(record[name], False)
        self.assertNotIn("runtime_policy", worker)
        self.assertIs(driver["installed"], True)
        self.assertIs(driver["crypto_linkage_verified"], True)
        for name in ("bootable", "loaded", "lkca_verified", "reproducibility_verified"):
            self.assertIs(driver[name], False)
        self.assertIs(worker["reproducibility_verified"], False)
        self.assertEqual((driver["driver"], driver["kernel"], driver["compiler"]),
                         ("595.91.07", "6.8.0-147-generic", "13.3.0"))
        names = ["nvidia", "nvidia-modeset", "nvidia-drm", "nvidia-uvm", "nvidia-peermem"]
        for field in ("modules", "compressed_modules"):
            self.assertEqual([row["name"] for row in driver[field]], names)
            for row in driver[field]:
                self.assertGreater(row["bytes"], 64)
                self.assertRegex(row["sha256"], r"^[0-9a-f]{64}$")
        for name in ("empty_verified", "group_removed", "limits_read_back"):
            self.assertIs(resources[name], True)
        self.assertEqual(resources["oom_kill"], 0)
        self.assertEqual(resources["populated_before_cleanup"], 0)
        self.assertLess(resources["memory_peak_bytes"], int(resources["limits"]["memory.max"]))


if __name__ == "__main__":
    unittest.main()
