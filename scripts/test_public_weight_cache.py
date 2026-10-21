# SPDX-License-Identifier: Apache-2.0
"""Synthetic copies only; no network, mounts or real model data."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
cache = types.ModuleType("public_cache")
cache.__file__ = str(ROOT / "host/cache-public-weights.py")
exec(compile(Path(cache.__file__).read_bytes(), cache.__file__, "exec"), cache.__dict__)


class CacheTests(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        source = root / "source"
        source.mkdir()
        (source / "nested").mkdir()
        rows = []
        for name, data in (("config.json", b"public"), ("nested/weights", b"public model bytes")):
            (source / name).write_bytes(data)
            rows.append((hashlib.sha256(data).hexdigest(), len(data), name))
        return source, root / "destination", rows

    def test_complete_copy_rehashed_without_mutating_source(self):
        source, destination, rows = self.fixture()
        report = cache.copy_pack(source, destination, rows, time.monotonic() + 10)
        self.assertEqual(report, {"files": 2, "verified_bytes": 24})
        for _, _, name in rows:
            self.assertEqual((source / name).read_bytes(), (destination / name).read_bytes())
        with self.assertRaises(FileExistsError):
            cache.copy_pack(source, destination, rows, time.monotonic() + 10)

    def test_corruption_and_unexpected_files_prevent_destination_creation(self):
        for mode in ("corrupt", "extra", "symlink", "hardlink", "fifo", "deadline"):
            with self.subTest(mode=mode):
                source, destination, rows = self.fixture()
                target = source / "config.json"
                if mode == "corrupt": target.write_bytes(b"secret")
                if mode == "extra": (source / "private").write_bytes(b"private")
                if mode == "symlink":
                    target.unlink()
                    target.symlink_to(source / "nested/weights")
                if mode == "hardlink": os.link(target, source.parent / "linked")
                if mode == "fifo":
                    target.unlink()
                    os.mkfifo(target)
                deadline = time.monotonic() - 1 if mode == "deadline" else time.monotonic() + 10
                with self.assertRaises((ValueError, OSError)):
                    cache.copy_pack(source, destination, rows, deadline)
                self.assertFalse(destination.exists())

    def test_nested_or_symlinked_destination_rejected(self):
        source, destination, rows = self.fixture()
        with self.assertRaises(ValueError):
            cache.copy_pack(source, source / "child", rows, time.monotonic() + 10)
        alias = destination.with_name("alias")
        alias.symlink_to(destination.parent, target_is_directory=True)
        with self.assertRaises(OSError):
            cache.copy_pack(source, alias / "copy", rows, time.monotonic() + 10)

    def test_mount_requires_exact_readback_and_restrictive_options(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve() / "mount"
            export = "nfs.example.test:/public"
            fs = {"source": export, "target": str(target), "fstype": "nfs4",
                  "options": "rw,nosuid,nodev,noexec,nconnect=16"}
            with mock.patch.object(cache, "command", side_effect=[b"", json.dumps({"filesystems": [fs]}).encode()]) as command:
                cache.mount_cache(export, target)
            self.assertEqual(command.call_args_list[0].args[0],
                ["mount", "-t", "nfs", "-o", "nconnect=16,nodev,nosuid,noexec", export, str(target)])
            for key, value in (("source", "other:/private"), ("fstype", "ext4"), ("options", "rw")):
                with self.subTest(key=key), mock.patch.object(cache, "command", side_effect=[
                        b"", json.dumps({"filesystems": [{**fs, key: value}]}).encode()]), self.assertRaises(ValueError):
                    cache.mount_cache(export, target)

    def test_only_complete_pinned_manifest_is_accepted(self):
        raw = (ROOT / "models/deepseek-v4.1-flash-1017/1/files.sha256").read_bytes()
        self.assertEqual(len(cache.manifest(raw)), 83)
        for invalid in (raw[:-1], raw + b"extra", b"x" * 65537):
            with self.assertRaises(ValueError): cache.manifest(invalid)


if __name__ == "__main__":
    unittest.main()
