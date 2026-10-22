# SPDX-License-Identifier: Apache-2.0
"""Synthetic tree fingerprints only; never builds or mounts a real image."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import mkosi_verity as verity


class RootTreeDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        for name in ("etc", "usr", "var/log", "var/lib/dkms", "var/lib/dpkg"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        (self.root / "etc/machine-id").write_bytes(b"private-marker\n")
        (self.root / "usr/example").write_bytes(b"fixed library\n")
        (self.root / "var/log/dpkg.log").write_bytes(b"private build timestamp\n")

    def test_deterministic_content_free_fingerprints(self):
        first = verity.tree_diagnostics(self.root)
        self.assertEqual(first, verity.tree_diagnostics(self.root))
        self.assertNotIn("private-marker", json.dumps(first))
        self.assertNotIn("private build timestamp", json.dumps(first))
        self.assertNotIn("example", json.dumps(first))
        self.assertIsNone(first["selected_files"]["etc/shadow"])
        self.assertFalse(first["image_integrity_verified"])
        self.assertEqual(first["entries"], sum(x["entries"] for x in first["buckets"].values()))

    def test_changed_log_localizes_without_altering_files_or_other_buckets(self):
        first = verity.tree_diagnostics(self.root)
        (self.root / "var/log/dpkg.log").write_bytes(b"new private timestamp\n")
        second = verity.tree_diagnostics(self.root)
        self.assertEqual([key for key in verity.TREE_BUCKETS if first["buckets"][key] != second["buckets"][key]],
                         ["var/log"])
        self.assertNotEqual(first["selected_files"]["var/log/dpkg.log"], second["selected_files"]["var/log/dpkg.log"])
        self.assertEqual((self.root / "var/log/dpkg.log").read_bytes(), b"new private timestamp\n")

    def test_symlinks_are_hashed_not_followed(self):
        (self.root / "usr/outside").symlink_to("/no-such-file/PRIVATE_TARGET")
        first = verity.tree_diagnostics(self.root)
        self.assertNotIn("PRIVATE_TARGET", json.dumps(first))
        (self.root / "usr/outside").unlink()
        (self.root / "usr/outside").symlink_to("/")
        self.assertNotEqual(first["buckets"]["usr"], verity.tree_diagnostics(self.root)["buckets"]["usr"])

    def test_permissions_are_included_but_mtimes_are_not(self):
        path = self.root / "usr/example"
        first = verity.tree_diagnostics(self.root)
        os.utime(path, (1, 1))
        self.assertEqual(first, verity.tree_diagnostics(self.root))
        path.chmod(0o700)
        self.assertNotEqual(first["buckets"]["usr"], verity.tree_diagnostics(self.root)["buckets"]["usr"])

    def test_bounds_special_files_and_selected_symlinks_reject(self):
        for key, value in (("TREE_MAX_ENTRIES", 1), ("TREE_MAX_BYTES", 1)):
            with self.subTest(key=key), mock.patch.object(verity, key, value), self.assertRaises(ValueError):
                verity.tree_diagnostics(self.root)
        with self.assertRaises(ValueError):
            verity.tree_diagnostics(self.root, clock=iter((0, 121)).__next__)
        (self.root / "etc/machine-id").unlink()
        (self.root / "etc/machine-id").symlink_to("/etc/machine-id")
        with self.assertRaises(ValueError):
            verity.tree_diagnostics(self.root)
        (self.root / "etc/machine-id").unlink()
        os.mkfifo(self.root / "unexpected")
        with self.assertRaises(ValueError):
            verity.tree_diagnostics(self.root)


if __name__ == "__main__":
    unittest.main()
