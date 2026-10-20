# SPDX-License-Identifier: Apache-2.0
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "host" / "install-lab-runtime.py"
spec = importlib.util.spec_from_file_location("lab_runtime", SOURCE)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
pull_spec = importlib.util.spec_from_file_location("lab_pull", SOURCE.with_name("pull-lab-image.py"))
pull = importlib.util.module_from_spec(pull_spec)
pull_spec.loader.exec_module(pull)


class LabRuntimeTests(unittest.TestCase):
    def plan(self):
        return "\n".join(f"Inst {name} ({version} Vendor [amd64])"
                         for name, version in runtime.PACKAGES.items())

    def test_exact_new_packages_pass(self):
        self.assertEqual(runtime.installation_plan(self.plan()), runtime.PACKAGES)

    def test_extra_driver_rejected(self):
        with self.assertRaises(ValueError):
            runtime.installation_plan(self.plan() + "\nInst nvidia-driver (595 Vendor [amd64])")

    def test_removal_rejected(self):
        with self.assertRaises(ValueError):
            runtime.installation_plan(self.plan() + "\nRemv existing-package")

    def test_upgrade_rejected(self):
        with self.assertRaises(ValueError):
            runtime.installation_plan(self.plan().replace("Inst runc (", "Inst runc [1.0] ("))

    def test_missing_rejected(self):
        with self.assertRaises(ValueError):
            runtime.installation_plan("\n".join(self.plan().splitlines()[1:]))

    def test_changed_version_rejected(self):
        with self.assertRaises(ValueError):
            runtime.installation_plan(self.plan().replace("1.20.1-1", "1.20.2-1"))

    def test_duplicate_rejected(self):
        with self.assertRaises(ValueError):
            runtime.installation_plan(self.plan() + "\n" + self.plan().splitlines()[0])

    def test_non_root_rejected(self):
        with patch.object(runtime.os, "geteuid", return_value=1000):
            with self.assertRaises(ValueError):
                runtime.platform_check()

    def test_expected_image_identity(self):
        with patch.object(pull.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = (pull.CONFIG + " linux/amd64\n").encode()
            self.assertTrue(pull.image_identity())

    def test_unexpected_image_identity(self):
        with patch.object(pull.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b"sha256:wrong linux/arm64\n"
            with self.assertRaises(ValueError):
                pull.image_identity()

    def test_missing_image(self):
        with patch.object(pull.subprocess, "run") as run:
            run.return_value.returncode = 1
            self.assertFalse(pull.image_identity())
