# SPDX-License-Identifier: Apache-2.0
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "host" / "install-lab-runtime.py"
spec = importlib.util.spec_from_file_location("lab_runtime", SOURCE)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


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
