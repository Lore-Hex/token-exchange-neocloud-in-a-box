# SPDX-License-Identifier: Apache-2.0
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "host" / "prepare-lab.py"
spec = importlib.util.spec_from_file_location("lab_preparation", SOURCE)
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)


class LabPreparationTests(unittest.TestCase):
    def test_private_init_is_repeatable(self):
        with tempfile.TemporaryDirectory() as parent:
            target = Path(parent).resolve() / "lab"
            self.assertEqual(lab.private_directory(target, create=True), target)
            self.assertEqual(lab.private_directory(target, create=True), target)
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)

    def test_relative_rejected(self):
        with self.assertRaises(ValueError):
            lab.private_directory("relative", create=True)

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as parent:
            link = Path(parent) / "link"
            link.symlink_to(parent, target_is_directory=True)
            with self.assertRaises(ValueError):
                lab.private_directory(link, create=True)

    def test_nonprivate_parent_rejected(self):
        with tempfile.TemporaryDirectory() as parent:
            Path(parent).chmod(0o755)
            with self.assertRaises(ValueError):
                lab.private_directory(Path(parent).resolve() / "lab", create=True)

    def test_invalid_bundle_never_executes(self):
        with tempfile.TemporaryDirectory() as parent:
            (Path(parent) / "source-45e7a09.bundle").write_bytes(b"not a bundle")
            with patch.object(lab, "command") as command:
                with self.assertRaises(ValueError):
                    lab.checkout(Path(parent))
                command.assert_not_called()

    def test_symlink_bundle_never_executes(self):
        with tempfile.TemporaryDirectory() as parent:
            (Path(parent) / "source-45e7a09.bundle").symlink_to(SOURCE)
            with patch.object(lab, "command") as command:
                with self.assertRaises(ValueError):
                    lab.checkout(Path(parent))
                command.assert_not_called()

    def test_prerequisites_are_booleans(self):
        with patch.object(lab.shutil, "which", return_value=None):
            self.assertTrue(all(value is False for value in lab.prerequisites().values()))
