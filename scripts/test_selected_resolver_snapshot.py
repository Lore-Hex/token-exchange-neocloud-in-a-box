# SPDX-License-Identifier: Apache-2.0
"""Actual temporary descriptors with realistic service-owner stat fixtures; no host changes."""

from contextlib import contextmanager, ExitStack
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import types
import unittest
from unittest import mock

import selected_host_baseline as baseline
import test_selected_host_baseline as common

PASSWD = b"root:x:0:0:root:/root:/bin/sh\nsystemd-resolve:x:989:989:Resolver:/:/usr/sbin/nologin\n"
GROUP = b"root:x:0:\nsystemd-resolve:x:989:\n"
STUB = b"# Synthetic service-owned fixture, not a host resolver export.\n".ljust(920, b"#")


@contextmanager
def filesystem(*, change=None):
    with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
        root = Path(directory).resolve()
        for name in ("etc", "run", "run/systemd", "run/systemd/resolve"):
            (root / name).mkdir(mode=0o755)
        target = root / "run/systemd/resolve/stub-resolv.conf"
        target.write_bytes(STUB)
        target.chmod(0o644)
        (root / "etc/resolv.conf").symlink_to("../run/systemd/resolve/stub-resolv.conf")
        (root / "etc/passwd").write_bytes(PASSWD)
        (root / "etc/group").write_bytes(GROUP)
        for name in ("etc/passwd", "etc/group"):
            (root / name).chmod(0o644)
        if change == "symlink":
            target.rename(target.with_name("other"))
            target.symlink_to("other")
        if change == "parent-symlink":
            target.parent.rename(target.parent.with_name("other"))
            target.parent.symlink_to("other")
        if change == "fifo":
            target.unlink()
            os.mkfifo(target)
        if change == "hardlink":
            os.link(target, target.with_name("other"))
        if change == "oversize":
            target.write_bytes(b"x" * 65537)
        real_open, real_stat, real_fstat = os.open, os.stat, os.fstat
        descriptors, calls = {}, {}
        def mapped(path):
            text = os.fspath(path)
            if text == "/" or text.startswith(("/etc/", "/run/")):
                return root / text.lstrip("/")
            return Path(text)
        def stamped(row, path):
            relative = path.relative_to(root).as_posix()
            calls[relative] = calls.get(relative, 0) + 1
            service = relative.startswith("run/systemd/resolve")
            values = {"st_" + key: value for key, value in baseline.stamp(row).items()}
            values.update(st_nlink=row.st_nlink, st_uid=989 if service else 0, st_gid=989 if service else 0)
            if relative == "run/systemd/resolve/stub-resolv.conf":
                if change == "uid": values["st_uid"] = 0
                if change == "gid": values["st_gid"] = 0
                if change == "writable": values["st_mode"] |= 0o020
                if change == "race" and calls[relative] > 2: values["st_ino"] += 1
                if change == "timestamp" and calls[relative] > 2: values["st_mtime_ns"] += 1
            if relative == "run/systemd":
                if change == "parent-owner": values["st_uid"] = 989
                if change == "parent-write": values["st_mode"] |= 0o002
                if change == "parent-race" and calls[relative] > 1: values["st_ino"] += 1
            if relative == "etc/passwd" and change == "account-owner": values["st_uid"] = 989
            return types.SimpleNamespace(**values)
        def open_file(path, flags, *args, dir_fd=None, **kwargs):
            resolved = descriptors[dir_fd] / path if dir_fd is not None else mapped(path)
            fd = real_open(resolved, flags, *args, **kwargs)
            descriptors[fd] = resolved
            return fd
        def file_stat(path, *, dir_fd=None, follow_symlinks=True):
            resolved = descriptors[dir_fd] / path if dir_fd is not None else mapped(path)
            return stamped(real_stat(resolved, follow_symlinks=follow_symlinks), resolved)
        stack.enter_context(mock.patch.object(os, "open", side_effect=open_file))
        stack.enter_context(mock.patch.object(os, "stat", side_effect=file_stat))
        stack.enter_context(mock.patch.object(os, "lstat", side_effect=lambda path, **kwargs:
            file_stat(path, follow_symlinks=False, **kwargs)))
        stack.enter_context(mock.patch.object(os, "fstat", side_effect=lambda fd: stamped(real_fstat(fd), descriptors[fd])))
        stack.enter_context(mock.patch.object(os, "getuid", return_value=0))
        try:
            yield root
        finally:
            for fd in descriptors:
                try:
                    real_fstat(fd)
                except OSError:
                    continue
                raise AssertionError("snapshot descriptor not closed")


class ResolverTests(unittest.TestCase):
    def test_exact_service_owned_stub_uses_real_descriptors_and_preserves_all_bytes(self):
        with filesystem():
            raw, row = baseline.read_resolved_stub()
            self.assertEqual(raw, STUB)
            self.assertEqual((row["uid"], row["gid"], row["mode"] & 0o777, row["size"]), (989, 989, 0o644, len(STUB)))
            with self.assertRaises(ValueError):
                baseline.read_file(Path("/run/systemd/resolve/stub-resolv.conf"), private=False)

    def test_wrong_ownership_writes_links_races_and_bounds_rejected(self):
        for change in ("uid", "gid", "writable", "parent-owner", "parent-write", "account-owner",
                       "parent-symlink", "parent-race", "symlink", "fifo", "hardlink", "oversize", "race", "timestamp"):
            with self.subTest(change=change), filesystem(change=change):
                with self.assertRaises((ValueError, OSError)):
                    baseline.read_resolved_stub()

    def test_named_local_identity_is_exact_unambiguous_and_not_nss(self):
        for passwd, group in ((PASSWD, GROUP), (PASSWD.replace(b"989", b"988"), GROUP),
                (PASSWD + b"alias:x:989:989:alias:/:/bin/false\n", GROUP),
                (PASSWD, GROUP + b"alias:x:989:\n"), (PASSWD.replace(b":989:", b":0989:"), GROUP),
                (PASSWD.replace(b"systemd-resolve", b"other"), GROUP),
                (PASSWD, GROUP.replace(b"systemd-resolve", b"other")),
                (PASSWD.replace(b"989:989", b"989:0"), GROUP), (PASSWD, b"")):
            with self.subTest(passwd=passwd, group=group), \
                    mock.patch.object(baseline, "read_file", side_effect=[(passwd, {}), (group, {})]) as read:
                if (passwd, group) == (PASSWD, GROUP):
                    baseline.resolver_service_identity()
                    self.assertEqual(read.call_args_list, [mock.call(Path("/etc/passwd"), private=False),
                                                          mock.call(Path("/etc/group"), private=False)])
                else:
                    with self.assertRaises(ValueError):
                        baseline.resolver_service_identity()

    def test_identity_drift_after_read_fails_and_closes_all_descriptors(self):
        with filesystem(), mock.patch.object(baseline, "resolver_service_identity",
                side_effect=[None, ValueError("selected network contract rejected")]) as identity:
            with self.assertRaises(ValueError):
                baseline.read_resolved_stub()
            self.assertEqual(identity.call_count, 2)

    def test_snapshot_dispatch_is_only_for_fixed_ordinary_target(self):
        link = mock.Mock()
        before = types.SimpleNamespace(**{"st_" + key: value for key, value in
            {**common.STAMP, "mode": stat.S_IFLNK | 0o777, "size": 39}.items()})
        target_row = {**common.STAMP, "uid": 989, "gid": 989, "size": len(STUB)}
        link.lstat.return_value = before
        link.is_symlink.return_value = True
        link.readlink.return_value = Path("../run/systemd/resolve/stub-resolv.conf")
        with mock.patch.object(baseline.proof, "RESOLVER", link), \
                mock.patch.object(baseline.proof, "resolver_target", return_value=baseline.proof.RESOLVER_TARGET), \
                mock.patch.object(baseline, "read_resolved_stub", return_value=(STUB, target_row)) as read, \
                mock.patch.object(Path, "lstat", return_value=types.SimpleNamespace(
                    **{"st_" + key: value for key, value in target_row.items()})):
            raw, _ = baseline.resolver_snapshot(baseline.proof.ORDINARY_VENUE)
            self.assertEqual(raw, STUB)
            read.assert_called_once_with()
        with mock.patch.object(baseline.proof, "resolver_target", return_value=Path("/tmp/not-the-stub")), \
                mock.patch.object(baseline.proof, "RESOLVER", link), \
                mock.patch.object(baseline, "read_resolved_stub") as read:
            with self.assertRaises(ValueError):
                baseline.resolver_snapshot(baseline.proof.ORDINARY_VENUE)
            read.assert_not_called()

    def test_manifest_validator_has_same_closed_owner_boundary(self):
        for venue in baseline.proof.VENUES:
            raw = common.resolver(venue)
            baseline.check_resolver(raw, venue)
            for key in ("link_stat", "target_stat"):
                for owner_key in ("uid", "gid"):
                    changed = baseline.decode(raw)
                    changed[key][owner_key] = 988
                    if venue == baseline.proof.LEGACY_VENUE and owner_key == "gid":
                        changed["link_stat"]["gid"] = changed["target_stat"]["gid"] = 988
                        baseline.check_resolver(baseline.encoded(changed), venue)
                    else:
                        with self.assertRaises(ValueError):
                            baseline.check_resolver(baseline.encoded(changed), venue)


class OriginalFailureTests(unittest.TestCase):
    def test_original_summary_remains_failed_without_invented_proof_or_cleanup(self):
        directory = baseline.ROOT / "evidence/p2/2026-10-25-selected-quarantine-presnapshot-failure"
        self.assertEqual({path.name for path in directory.iterdir()}, {"README.md", "summary.json"})
        raw = (directory / "summary.json").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), "568c93565e8b3c147e968b99f41275fe6d8cc270880e1a6e73c6e517895b04d7")
        report = baseline.decode(raw)
        baseline.check_summary(report)
        self.assertEqual(report, {**baseline.empty_result(), "stage": "pre-snapshot"})
        old = subprocess.check_output(["git", "show", "e890860922bd94e75d332ad10461b60b6a1bfeac:scripts/selected_host_baseline.py"],
                                      cwd=baseline.ROOT)
        self.assertIn(b"before.st_uid == os.getuid()", old)


if __name__ == "__main__":
    unittest.main()
