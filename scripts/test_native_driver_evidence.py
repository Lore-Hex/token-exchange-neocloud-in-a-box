# SPDX-License-Identifier: Apache-2.0
"""Pin the successful compilation and its narrower-than-boot claim boundary."""
import hashlib
import json
from pathlib import Path
import unittest


class NativeDriverEvidenceTests(unittest.TestCase):
    def test_original_driver_compile_and_cleanup(self):
        root = Path(__file__).resolve().parents[1] / "evidence/p3/2026-10-22-native-driver-pass"
        pins = {
            "run.json": "445973df570b7b8eb3e8b4a9f1b032730f13292908064987b92e7f9f6cb4eb66",
            "result.json": "98aad840542f987d19cee787cf293306eff7d670270c860f13de912f42573275",
            "evidence-resources.json": "bc3501601d8cc8603e508e67f342ba8a45e2bdbf32aadc6fdf413b4747e1dc6a",
            "installer.stderr": "7051a1570262c885ee8aa0b3acc42b352ee54cf52540123e30e7c7407661c841",
            "dkms-add.stdout": "275d4618982a880d9ce70253e2fac680dc6fb6da9736dcd6a383f0ee09f9c65a",
            "dkms-build.stderr": "7f2a6a7b6607340cd067a9b0cf807c84083b7b2342ee24966eef1ec54a7371ca",
            "dkms-build.stdout": "c308ddb6a014d9037faaa10120df2ddba9aa6ded03a18ed9d4612ee240ad763d",
            "dkms-compiler.stdout": "bb6aade8c4ca40cae9cb1be8abe12377f651fccd82954af74ec61703b6608fc5",
            "dkms-make-1.log": "57bb264db801ac9cf8b60c3d93eadd3f9946bed8555d9ffcb77932087dbc3f63",
            "input-fetch.log": "3776e4b1a92f1f0b5e77e11b1d2804f06798a159eabeb4b8fc3d79c41ac518be",
            "launcher.stdout": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "launcher.stderr": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        }
        self.assertEqual({p.name for p in root.iterdir()}, set(pins))
        for name, digest in pins.items():
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest)
        run = json.loads((root / "run.json").read_bytes())
        result = json.loads((root / "result.json").read_bytes())
        resources = json.loads((root / "evidence-resources.json").read_bytes())
        worker = result["worker"]
        driver = worker["driver_build"]
        self.assertEqual(run["git_sha"], "c70b1b7a60ed223ead65e12642fc368838244997")
        self.assertEqual(result["git_sha"], run["git_sha"])
        self.assertEqual(run["execution"], "software")
        self.assertIs(run["hardware_validated"], False)
        for record in (run, result, worker, driver, resources):
            self.assertEqual(record["result"], "pass")
        self.assertEqual(worker["installed_packages"], 145)
        self.assertEqual(worker["installer_exit_code"], 0)
        for name in ("configured_state_verified", "package_installation_executed", "dkms_built"):
            self.assertIs(worker[name], True)
        for name in ("payload_removed", "process_cleanup_confirmed"):
            self.assertIs(result[name], True)
        for record in (result, worker):
            for name in ("bootable", "deployable"):
                self.assertIs(record[name], False)
        for name in ("bootable", "installed", "loaded", "lkca_verified", "reproducibility_verified"):
            self.assertIs(driver[name], False)
        self.assertIs(worker["reproducibility_verified"], False)
        self.assertEqual((driver["driver"], driver["kernel"], driver["compiler"]),
                         ("595.91.07", "6.8.0-147-generic", "13.3.0"))
        self.assertEqual([row["name"] for row in driver["modules"]],
                         ["nvidia", "nvidia-modeset", "nvidia-drm", "nvidia-uvm", "nvidia-peermem"])
        for row in driver["modules"]:
            self.assertGreater(row["bytes"], 64)
            self.assertRegex(row["sha256"], r"^[0-9a-f]{64}$")
        for name in ("empty_verified", "group_removed", "limits_read_back"):
            self.assertIs(resources[name], True)
        self.assertEqual(resources["oom_kill"], 0)
        self.assertEqual(resources["populated_before_cleanup"], 0)
        self.assertLess(resources["memory_peak_bytes"], int(resources["limits"]["memory.max"]))

    def test_original_fetch_failure_did_not_execute_packages(self):
        path = (Path(__file__).resolve().parents[1]
                / "evidence/p3/2026-10-22-native-driver-fetch-timeout/input-fetch.log")
        raw = path.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         "f9690cbd9ab55f6c1953a6e8fab0d6ab8dbd94c93e80281e60d7a69e27997641")
        record = json.loads(raw)
        self.assertEqual(record["result"], "fail")
        self.assertEqual(record["returncode"], 28)
        self.assertIs(record["package_execution"], False)


if __name__ == "__main__":
    unittest.main()
