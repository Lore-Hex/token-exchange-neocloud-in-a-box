# SPDX-License-Identifier: Apache-2.0
"""Source-only skeleton composition; no packages, hooks, VM or host changes."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest import mock

import mkosi_export as export
import mkosi_guest_network as network
from test_mkosi_build import host, inputs, load, worker

driver = load("scripts/mkosi_dkms.py")


class SkeletonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.prepared, self.config = self.root / "prepared", self.root / "config"
        self.config.mkdir(mode=0o700)
        self.files = {**host.mkosi.dkms_build_controls(), "mkosi.conf.candidate": b"[Build]\n"}
        host.base.publish(self.prepared, self.files)
        self.source = self.prepared / "mkosi-skeleton"
        self.target = self.config / "mkosi-skeleton"
        modules = {"package-inputs": inputs, "dkms-worker": driver}
        patch = mock.patch.object(worker, "module", side_effect=modules.__getitem__)
        patch.start()
        self.addCleanup(patch.stop)

    def check_guest(self, skeleton, mode):
        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            guest = Path(temporary)
            # Model pinned copy_tree's mode-preserving copy of the new /etc,
            # keeping the private outer root separate from logical image dirs.
            shutil.copytree(skeleton / "etc", guest / "etc")
            self.assertEqual(stat.S_IMODE((guest / "etc").stat().st_mode), mode)
            fd = os.open(guest, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                with network.parent(fd, "etc/nib/network.json", export, create=True):
                    pass
            finally:
                os.close(fd)
            self.assertEqual(stat.S_IMODE(guest.stat().st_mode), 0o700)

    def test_private_transport_reproduces_strict_guest_rejection(self):
        with self.assertRaisesRegex(ValueError, "guest-network-composition-rejected"):
            self.check_guest(self.source, 0o700)
        host.base.verify(self.prepared, self.files)

    def test_fresh_image_modes_preserve_bytes_private_source_and_guest_gate(self):
        for mask in (0o022, 0o027, 0o077):
            with self.subTest(umask=mask):
                old = os.umask(mask)
                try:
                    worker.prepare_skeleton(self.config, self.prepared)
                    self.check_guest(self.target, 0o755)
                finally:
                    os.umask(old)
                self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o700)
                actual = {}
                for path in self.target.rglob("*"):
                    mode = stat.S_IMODE(path.lstat().st_mode)
                    self.assertFalse(path.is_symlink())
                    if path.is_dir():
                        self.assertEqual(mode, 0o755)
                    else:
                        self.assertEqual(mode, 0o600)
                        self.assertEqual(path.stat().st_nlink, 1)
                        name = path.relative_to(self.target).as_posix()
                        actual[name] = path.read_bytes()
                        self.assertNotEqual(path.stat().st_ino, (self.source / name).stat().st_ino)
                self.assertEqual(actual, {n: self.files["mkosi-skeleton/" + n] for n in driver.CONTROL_NAMES})
                host.base.verify(self.prepared, self.files)
                shutil.rmtree(self.target)

    def test_only_fixed_controls_are_copied(self):
        (self.source / "etc/unrelated").write_bytes(b"not an image input")
        worker.prepare_skeleton(self.config, self.prepared)
        self.assertFalse((self.target / "etc/unrelated").exists())

    def test_source_parent_links_and_wrong_modes_reject_before_publication(self):
        for name in ("", "mkosi-skeleton", "mkosi-skeleton/etc", "mkosi-skeleton/etc/dkms",
                     "mkosi-skeleton/etc/dkms/framework.conf.d"):
            path = self.prepared / name
            for attack in ("mode", "symlink"):
                with self.subTest(parent=name, attack=attack):
                    saved = self.root / "saved"
                    if attack == "mode":
                        path.chmod(0o755)
                    else:
                        path.rename(saved)
                        path.symlink_to(saved, target_is_directory=True)
                    try:
                        with self.assertRaises((OSError, ValueError)):
                            worker.prepare_skeleton(self.config, self.prepared)
                        self.assertFalse(self.target.exists())
                    finally:
                        if attack == "mode":
                            path.chmod(0o700)
                        else:
                            path.unlink()
                            saved.rename(path)

    def test_bad_leaf_types_links_modes_and_bounds_reject_before_publication(self):
        leaf = self.source / driver.CONTROL_NAMES[0]
        original = leaf.read_bytes()
        for attack in ("empty", "oversize", "symlink", "hardlink", "fifo", "mode"):
            with self.subTest(attack=attack):
                leaf.unlink()
                other = self.root / "other"
                if attack in ("symlink", "hardlink"):
                    other.write_bytes(original)
                    other.chmod(0o600)
                    if attack == "symlink":
                        leaf.symlink_to(other)
                    else:
                        os.link(other, leaf)
                elif attack == "fifo":
                    os.mkfifo(leaf, 0o600)
                else:
                    leaf.write_bytes(b"" if attack == "empty" else b"x" * 16385 if attack == "oversize" else original)
                    leaf.chmod(0o644 if attack == "mode" else 0o600)
                try:
                    with self.assertRaises((OSError, ValueError)):
                        worker.prepare_skeleton(self.config, self.prepared)
                    self.assertFalse(self.target.exists())
                finally:
                    leaf.unlink()
                    if other.exists():
                        other.unlink()
                    leaf.write_bytes(original)
                    leaf.chmod(0o600)

    def test_existing_destination_or_link_is_never_modified(self):
        for kind in ("directory", "file", "symlink"):
            with self.subTest(kind=kind):
                if kind == "directory":
                    self.target.mkdir(mode=0o711)
                elif kind == "file":
                    self.target.write_bytes(b"existing")
                else:
                    self.target.symlink_to(self.source, target_is_directory=True)
                before = self.target.lstat()
                with self.assertRaises(FileExistsError):
                    worker.prepare_configuration(self.config, self.prepared)
                after = self.target.lstat()
                self.assertEqual((before.st_ino, before.st_mode, before.st_mtime_ns),
                                 (after.st_ino, after.st_mode, after.st_mtime_ns))
                self.assertFalse((self.config / "mkosi.conf").exists())
                host.base.verify(self.prepared, self.files)
                if kind == "directory":
                    self.target.rmdir()
                else:
                    self.target.unlink()

    def test_private_config_mode_and_symlink_are_not_accepted(self):
        self.config.chmod(0o755)
        with self.assertRaises(ValueError):
            worker.prepare_skeleton(self.config, self.prepared)
        self.assertFalse(self.target.exists())
        self.config.chmod(0o700)
        saved = self.root / "saved"
        self.config.rename(saved)
        self.config.symlink_to(saved, target_is_directory=True)
        with self.assertRaises(OSError):
            worker.prepare_skeleton(self.config, self.prepared)
        self.assertEqual(list(saved.iterdir()), [])

    def test_wrong_source_owner_rejects_before_destination_creation(self):
        uid = os.getuid()
        with mock.patch.object(worker.os, "getuid", return_value=uid + 1):
            with self.assertRaises(ValueError):
                worker.prepare_skeleton(self.config, self.prepared)
        self.assertFalse(self.target.exists())

    def test_failed_fresh_directory_mode_never_publishes_mkosi_configuration(self):
        with mock.patch.object(worker.os, "fchmod", side_effect=OSError("synthetic refusal")):
            with self.assertRaises(OSError):
                worker.prepare_configuration(self.config, self.prepared)
        self.assertEqual({p.name for p in self.config.iterdir()}, {"mkosi-skeleton"})
        self.assertEqual(stat.S_IMODE((self.target / "etc").stat().st_mode), 0o700)
        host.base.verify(self.prepared, self.files)

    def test_readback_mismatch_never_publishes_mkosi_configuration(self):
        regular = inputs.regular
        calls = 0
        def read(*args, **kwargs):
            nonlocal calls
            calls += 1
            raw = regular(*args, **kwargs)
            return raw if calls <= 3 else raw + b"changed"
        with mock.patch.object(inputs, "regular", side_effect=read):
            with self.assertRaises(ValueError):
                worker.prepare_configuration(self.config, self.prepared)
        self.assertEqual({p.name for p in self.config.iterdir()}, {"mkosi-skeleton"})
        host.base.verify(self.prepared, self.files)

    def test_worker_records_configuration_failure_without_install_or_export(self):
        def path(*parts):
            logical = Path(*parts)
            if logical.is_relative_to("/nib/prepared"):
                return self.prepared / logical.relative_to("/nib/prepared")
            return self.root / logical.relative_to("/")
        for name in ("/nib", "/work", "/out"):
            path(name).mkdir()
        path("/nib/ownership-input.json").write_bytes(b"{}")
        path("/nib/prepared/guest-inputs.json").write_bytes(b"{}")
        (self.source / driver.CONTROL_NAMES[0]).chmod(0o644)
        modules = {name: mock.Mock() for name in ("namespace-check", "pid-check", "ownership", "snapshot")}
        modules.update({"package-inputs": inputs, "dkms-worker": driver})
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(worker, "Path", side_effect=path))
            stack.enter_context(mock.patch.object(worker, "module", side_effect=modules.__getitem__))
            stack.enter_context(mock.patch.object(worker, "sys", mock.Mock(
                platform="linux", version_info=(3, 12), argv=["worker", "1", "2", "3", "4", "5"])))
            for name in ("geteuid", "getegid"):
                stack.enter_context(mock.patch.object(worker.os, name, return_value=0))
            stack.enter_context(mock.patch.object(worker, "expected_packages", return_value={}))
            install = stack.enter_context(mock.patch.object(worker, "install"))
            self.assertEqual(worker.main(), 1)
            install.assert_not_called()
        report = json.loads(path("/out/result.json").read_bytes())
        self.assertEqual((report["result"], report["stage"], report["failure_kind"]),
                         ("fail", "configuration", "rejected"))
        self.assertFalse(report["package_installation_executed"])
        self.assertNotIn("artifact_stage", report)
        self.assertEqual(list(path("/work/config").iterdir()), [])
        self.assertEqual({p.name for p in path("/out").iterdir()}, {"result.json"})

    def test_anchored_regular_reader_keeps_leaf_bounds_and_no_follow(self):
        fd = os.open(self.source / "etc/dkms", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            raw = inputs.regular("no-autoinstall", 16384, dir_fd=fd)
            self.assertEqual(raw, (self.source / driver.CONTROL_NAMES[0]).read_bytes())
            with self.assertRaises(ValueError):
                inputs.regular("no-autoinstall", len(raw) - 1, dir_fd=fd)
            (self.source / "etc/dkms/link").symlink_to("no-autoinstall")
            with self.assertRaises(OSError):
                inputs.regular("link", 16384, dir_fd=fd)
        finally:
            os.close(fd)


if __name__ == "__main__":
    unittest.main()
