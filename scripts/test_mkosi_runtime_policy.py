# SPDX-License-Identifier: Apache-2.0
"""Exercise the fixed startup policy only against disposable fixture roots."""
import configparser
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import mkosi_runtime_policy as policy


class RuntimePolicyTests(unittest.TestCase):
    def test_fixed_root_only_no_host_mutation(self):
        with mock.patch.object(policy.os, "open") as opened:
            for root in (Path("/"), Path("/etc"), Path("/work/output/rootfs/.."), " /work/output/rootfs"):
                with self.assertRaises(ValueError):
                    policy.apply(root)
        opened.assert_not_called()

    def test_deterministic_idempotent_files_and_masks(self):
        results = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with mock.patch.object(policy, "ROOTFS", root):
                    result = policy.apply(root)
                    self.assertEqual(result, policy.apply(root))
                for name, raw in policy.FILES.items():
                    path = root / name
                    self.assertEqual(path.read_bytes(), raw)
                    self.assertEqual(path.stat().st_mode & 0o777, 0o644)
                    self.assertEqual(result["files"][name], hashlib.sha256(raw).hexdigest())
                for name, target in policy.LINKS.items():
                    self.assertEqual(os.readlink(root / name), target)
                self.assertFalse(result["services_started"])
                self.assertFalse(result["boot_verified"])
                self.assertFalse(result["complete_release_hardening"])
                results.append(result)
        self.assertEqual(results[0], results[1])

    def test_parent_symlink_and_root_symlink_rejected(self):
        for name in ("etc", "etc/systemd", "etc/systemd/system"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root, outside = Path(temporary) / "root", Path(temporary) / "outside"
                root.mkdir()
                outside.mkdir()
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(outside, target_is_directory=True)
                with mock.patch.object(policy, "ROOTFS", root), self.assertRaises(OSError):
                    policy.apply(root)
                self.assertEqual(list(outside.iterdir()), [])
        with tempfile.TemporaryDirectory() as temporary:
            root, outside = Path(temporary) / "root", Path(temporary) / "outside"
            outside.mkdir()
            root.symlink_to(outside, target_is_directory=True)
            with mock.patch.object(policy, "ROOTFS", root), self.assertRaises(OSError):
                policy.apply(root)
            self.assertEqual(list(outside.iterdir()), [])

    def test_packaged_syslog_dropin_is_masked_by_same_name_not_sort_order(self):
        # systemd 255.4-1ubuntu8.18 ships this setting in /usr/lib. Drop-ins
        # merge by filename, /etc wins same-name ties, then lexical order wins.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vendor = root / "usr/lib/systemd/journald.conf.d/syslog.conf"
            vendor.parent.mkdir(parents=True)
            vendor.write_text("[Journal]\nForwardToSyslog=yes\n")
            with mock.patch.object(policy, "ROOTFS", root):
                policy.apply(root)
            effective = {}
            for directory in (vendor.parent, root / "etc/systemd/journald.conf.d"):
                effective.update({path.name: path for path in directory.glob("*.conf")})
            parser = configparser.ConfigParser(interpolation=None)
            for name in sorted(effective):
                path = effective[name]
                if path.is_symlink():
                    self.assertEqual(os.readlink(path), "/dev/null")
                    continue
                parser.read_string(path.read_text())
            self.assertEqual(parser["Journal"]["ForwardToSyslog"], "no")
            self.assertEqual(parser["Journal"]["Storage"], "none")
            self.assertEqual(vendor.read_text(), "[Journal]\nForwardToSyslog=yes\n")

    def test_conflict_rejected_before_any_file_is_written(self):
        name = sorted(policy.LINKS)[-1]
        for kind in ("regular", "directory", "symlink", "dangling"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = root / name
                path.parent.mkdir(parents=True)
                if kind == "regular":
                    path.write_text("unchanged vendor setting")
                elif kind == "directory":
                    path.mkdir()
                else:
                    path.symlink_to("/dev/zero" if kind == "symlink" else "missing")
                with mock.patch.object(policy, "ROOTFS", root), self.assertRaises(ValueError):
                    policy.apply(root)
                self.assertTrue(all(not (root / item).exists() for item in policy.FILES))
                if kind == "regular":
                    self.assertEqual(path.read_text(), "unchanged vendor setting")

    def test_file_symlink_hardlink_wrong_mode_or_bytes_rejected(self):
        name, raw = next(iter(policy.FILES.items()))
        for kind in ("symlink", "hardlink", "mode", "bytes", "fifo"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                target = root / "sentinel"
                target.write_bytes(raw)
                path = root / name
                path.parent.mkdir(parents=True)
                if kind == "symlink":
                    path.symlink_to(target)
                elif kind == "hardlink":
                    os.link(target, path)
                elif kind == "fifo":
                    os.mkfifo(path)
                else:
                    path.write_bytes(raw if kind == "mode" else b"x" * len(raw))
                    path.chmod(0o600 if kind == "mode" else 0o644)
                with mock.patch.object(policy, "ROOTFS", root), self.assertRaises(ValueError):
                    policy.apply(root)
                self.assertEqual(target.read_bytes(), raw)

    def test_no_overwrite_if_leaf_appears_after_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = policy.inspect
            collided = []
            def inspect(fd, name):
                result = original(fd, name)
                if name in policy.FILES and not collided:
                    (root / name).write_bytes(b"unexpected")
                    collided.append(name)
                return result
            with mock.patch.object(policy, "ROOTFS", root), mock.patch.object(policy, "inspect", inspect):
                with self.assertRaises(FileExistsError):
                    policy.apply(root)
            self.assertEqual((root / collided[0]).read_bytes(), b"unexpected")

    def test_policy_covers_system_and_user_activation_without_deleting_evidence(self):
        for scope in ("system", "user"):
            for name in policy.PODMAN:
                self.assertIn("etc/systemd/" + scope + "/" + name, policy.LINKS)
        for name in ("getty@.service", "serial-getty@.service", "debug-shell.service",
                     "rescue.service", "emergency.service", "systemd-pstore.service",
                     "nvidia-cdi-refresh.path", "user@.service"):
            self.assertIn("etc/systemd/system/" + name, policy.LINKS)
        journal = policy.FILES["etc/systemd/journald.conf.d/00-nib-no-content.conf"]
        for setting in ("Storage=none", "ForwardToSyslog=no", "ForwardToKMsg=no",
                        "ForwardToConsole=no", "ForwardToWall=no"):
            self.assertIn(setting.encode() + b"\n", journal)
        self.assertTrue(all(value == "/dev/null" for value in policy.LINKS.values()))
        # No claim that clearing historical files, unit overrides or boot arguments
        # is handled by this deliberately bounded startup-policy increment.
        self.assertNotIn("/var/log", json.dumps(policy.FILES, default=str))


if __name__ == "__main__":
    unittest.main()
