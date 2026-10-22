# SPDX-License-Identifier: Apache-2.0
"""Synthetic copies only; no network, mounts or real model data."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
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

    def test_readonly_mount_requires_kernel_readback(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve() / "mount"
            export = "nfs.example.test:/public"
            fs = {"source": export, "target": str(target), "fstype": "nfs4",
                  "options": "ro,nosuid,nodev,noexec,nconnect=16"}
            with mock.patch.object(cache, "command", side_effect=[b"", json.dumps({"filesystems": [fs]}).encode()]) as command:
                cache.mount_cache(export, target, readonly=True)
            self.assertEqual(command.call_args_list[0].args[0][4], "ro,nconnect=16,nodev,nosuid,noexec")
            for options in ("rw,nosuid,nodev,noexec", "ro,rw,nosuid,nodev,noexec", "ro,nodev,noexec"):
                with mock.patch.object(cache, "command", return_value=json.dumps(
                        {"filesystems": [{**fs, "options": options}]}).encode()), self.assertRaises(ValueError):
                    cache.verify_mount(export, target, readonly=True)

    def restore_worker(self, target, destination, rows):
        manifest_path = destination.parent / "manifest"
        manifest_path.write_bytes(b"synthetic manifest")
        output = io.StringIO()
        with mock.patch.object(cache.sys, "platform", "linux"), \
                mock.patch.object(cache.sys, "flags", types.SimpleNamespace(isolated=True, no_site=True)), \
                mock.patch.object(cache.sys, "dont_write_bytecode", True), \
                mock.patch.object(cache.os, "getuid", return_value=0), \
                mock.patch.object(cache, "manifest", return_value=rows), mock.patch.object(cache.sys, "stdout", output):
            cache.restore_worker("nfs.example.test:/public", str(target), str(destination), str(manifest_path), "60")
        return json.loads(output.getvalue())

    def test_restore_worker_uses_verified_copy_and_never_overwrites(self):
        source, destination, rows = self.fixture()
        target = source.parent / "mount"
        target.mkdir()
        source.rename(target / "deepseek-v4.1-flash-1017")
        with mock.patch.object(cache, "mount_cache") as mount, \
                mock.patch.object(cache, "verify_mount") as verify, \
                mock.patch.object(cache.os, "statvfs", return_value=types.SimpleNamespace(f_bavail=1 << 50, f_frsize=1)):
            result = self.restore_worker(target, destination, rows)
            mount.assert_called_once_with("nfs.example.test:/public", target, readonly=True)
            verify.assert_called_once_with("nfs.example.test:/public", target, readonly=True)
            with self.assertRaises(ValueError):
                self.restore_worker(target, destination, rows)
            self.assertEqual(mount.call_count, 1)
        self.assertEqual((result["files"], result["verified_bytes"]), (2, 24))
        self.assertEqual((destination / "config.json").read_bytes(), b"public")

    def test_restore_rejects_bad_cache_and_insufficient_space(self):
        for mode in ("space", "corrupt", "readback"):
            with self.subTest(mode=mode):
                source, destination, rows = self.fixture()
                target = source.parent / "mount"
                target.mkdir()
                source.rename(target / "deepseek-v4.1-flash-1017")
                if mode == "corrupt":
                    (target / "deepseek-v4.1-flash-1017/config.json").write_bytes(b"secret")
                with mock.patch.object(cache, "mount_cache") as mount, \
                        mock.patch.object(cache, "verify_mount", side_effect=ValueError("private") if mode == "readback" else None), \
                        mock.patch.object(cache.os, "statvfs", return_value=types.SimpleNamespace(
                            f_bavail=0 if mode == "space" else 1 << 50, f_frsize=1)):
                    with self.assertRaises(ValueError):
                        self.restore_worker(target, destination, rows)
                    if mode == "space":
                        mount.assert_not_called()
                if mode != "readback":
                    self.assertFalse(destination.exists())

    def restore_args(self, root):
        return ("nfs.example.test:/public", root / "mount", root / "destination", root / "manifest",
                root / "receipt.json", "a" * 40, 120)

    def successful_worker(self):
        return json.dumps({"schema": "nib.public-weight-cache-restore-worker/v1", "files": 83,
                           "verified_bytes": cache.TOTAL_BYTES}).encode()

    def test_restore_parent_reserves_failure_receipt_before_independent_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            args = self.restore_args(root)
            source = Path(cache.__file__).read_bytes()
            def capture(argv, **kwargs):
                pending = json.loads(args[4].read_bytes())
                self.assertEqual((pending["result"], pending["cleanup"]), ("fail", "unproven"))
                self.assertEqual(args[4].stat().st_mode & 0o777, 0o600)
                self.assertEqual(argv[:6], [sys.executable, "-I", "-S", "-B", "-c", cache.WORKER_BOOTSTRAP])
                self.assertEqual(argv[6].encode(), source)
                self.assertEqual(argv[7:], [args[0], *map(str, args[1:4]), "60"])
                self.assertEqual(kwargs, {"timeout": 60, "limit": 4096})
                return self.successful_worker()
            with mock.patch.object(cache, "load_capture", return_value=mock.Mock(side_effect=capture)) as load, \
                    mock.patch.object(cache, "mount_cache") as mount, mock.patch.object(cache, "copy_pack") as copy, \
                    mock.patch.object(cache.os, "statvfs") as space, mock.patch.object(cache.signal, "alarm") as alarm:
                self.assertEqual(cache.restore_report(*args), 0)
                with self.assertRaises(FileExistsError):
                    cache.restore_report(*args)
                load.assert_called_once()
                for operation in (mount, copy, space, alarm):
                    operation.assert_not_called()
            result = json.loads(args[4].read_bytes())
            self.assertEqual((result["result"], result["cleanup"]), ("pass", "worker-completed"))
            self.assertTrue(result["worker_exit_confirmed"])
            self.assertFalse(result["process_group_absence_verified"])
            self.assertFalse(result["confidential"])
            self.assertFalse(result["source_removed"])
            self.assertEqual(result["script_sha256"], hashlib.sha256(source).hexdigest())
            self.assertEqual(result["supervisor_sha256"], cache.PROBE_SHA256)

    def test_restore_timeout_cleanup_failure_cancel_and_bad_output_never_pass(self):
        for failure, expected in ((ValueError("probe-timeout"), "timeout"),
                                 (ValueError("probe-cleanup-timeout"), "cleanup-unproven"),
                                 (ValueError("probe-cleanup-failed"), "cleanup-unproven"),
                                 (ValueError("probe-command-failed"), "worker-failed"),
                                 (ValueError("probe-output-limit"), "output-limit"),
                                 (InterruptedError("private-canary"), "cancelled"),
                                 (ValueError("private-canary"), "rejected"), (b"{}", "rejected")):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temporary:
                args = self.restore_args(Path(temporary).resolve())
                capture = mock.Mock(side_effect=failure) if isinstance(failure, Exception) else mock.Mock(return_value=failure)
                previous = {sig: cache.signal.getsignal(sig) for sig in (cache.signal.SIGINT, cache.signal.SIGTERM)}
                with mock.patch.object(cache, "load_capture", return_value=capture):
                    self.assertEqual(cache.restore_report(*args), 1)
                result = json.loads(args[4].read_bytes())
                self.assertEqual((result["result"], result["cleanup"], result["failure"]), ("fail", "unproven", expected))
                self.assertFalse(result["worker_exit_confirmed"])
                self.assertEqual(result["verified_bytes"], 0)
                self.assertIsNotNone(result["ended_utc"])
                self.assertNotIn("private-canary", args[4].read_text())
                self.assertEqual({sig: cache.signal.getsignal(sig) for sig in previous}, previous)

    def test_restore_receipt_and_path_rejections_precede_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            good = self.restore_args(root)
            for index, bad in ((1, Path("relative")), (2, root / "mount/child"), (4, root / "destination/receipt"),
                               (3, root / "mount/manifest"), (6, 60), (6, 7201)):
                args = list(good)
                args[index] = bad
                with mock.patch.object(cache, "load_capture") as load, self.assertRaises(ValueError):
                    cache.restore_report(*args)
                load.assert_not_called()
                self.assertFalse(good[4].exists())
            good[4].symlink_to(root / "missing")
            with mock.patch.object(cache, "load_capture") as load, self.assertRaises(FileExistsError):
                cache.restore_report(*good)
            load.assert_not_called()

    def test_supervisor_hash_is_verified_before_execution(self):
        capture = cache.load_capture()
        self.assertTrue(callable(capture))
        with mock.patch.object(cache, "read_regular", return_value=b"raise AssertionError('unverified')"):
            with self.assertRaises(ValueError):
                cache.load_capture()

    def test_real_parent_timeout_survives_child_ignoring_alarm(self):
        # Synthetic local sleeper only: no NFS, mount, model, root or network.
        source = b"import signal, time\ndef restore_worker(*args):\n signal.signal(signal.SIGALRM, signal.SIG_IGN)\n time.sleep(10)\n"
        actual_read, actual_capture = cache.read_regular, cache.load_capture()
        with tempfile.TemporaryDirectory() as temporary:
            args = self.restore_args(Path(temporary).resolve())
            def read(path):
                return source if path == Path(cache.__file__).absolute() else actual_read(path)
            def bounded(argv, **kwargs):
                return actual_capture(argv, timeout=0.25, limit=kwargs["limit"])
            started = time.monotonic()
            with mock.patch.object(cache, "read_regular", side_effect=read), \
                    mock.patch.object(cache, "load_capture", return_value=bounded):
                self.assertEqual(cache.restore_report(*args), 1)
            self.assertLess(time.monotonic() - started, 3)
            result = json.loads(args[4].read_bytes())
            self.assertEqual((result["result"], result["failure"], result["cleanup"]), ("fail", "timeout", "unproven"))

    def test_unmount_requires_exact_mount_and_confirmed_absence(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            export = "nfs.example.test:/public"
            fs = {"source": export, "target": str(target), "fstype": "nfs4",
                  "options": "rw,nosuid,nodev,noexec"}
            row = json.dumps({"filesystems": [fs]}).encode()
            missing = subprocess.CalledProcessError(1, "findmnt", output=b"")
            with mock.patch.object(cache, "command", side_effect=[row, b"", missing]) as command:
                cache.unmount_cache(export, target)
            self.assertEqual(command.call_args_list[1].args[0], ["umount", "--", str(target)])
            for key, value in (("source", "nfs.example.test:/wrong"), ("target", "/"),
                               ("fstype", "ext4"), ("options", "rw,nodev,nosuid")):
                with self.subTest(key=key), mock.patch.object(cache, "command", return_value=json.dumps(
                        {"filesystems": [{**fs, key: value}]}).encode()) as command:
                    with self.assertRaises(ValueError): cache.unmount_cache(export, target)
                    self.assertEqual(command.call_count, 1)
            for outcome in (row, b"", subprocess.CalledProcessError(2, "findmnt", output=b""),
                            subprocess.CalledProcessError(1, "findmnt", output=b"unexpected")):
                with self.subTest(outcome=outcome), mock.patch.object(cache, "command", side_effect=[row, b"", outcome]):
                    with self.assertRaises(ValueError): cache.unmount_cache(export, target)
            with mock.patch.object(cache, "command", side_effect=[row, subprocess.CalledProcessError(32, "umount")]) as command:
                with self.assertRaises(subprocess.CalledProcessError): cache.unmount_cache(export, target)
                self.assertEqual(command.call_count, 2)

    def test_unmount_rejects_symlinks_and_root_before_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            alias = root / "alias"
            alias.symlink_to(root, target_is_directory=True)
            for target in (Path("/"), alias, root / ".."):
                with self.subTest(target=target), mock.patch.object(cache, "command") as command:
                    with self.assertRaises((ValueError, OSError)):
                        cache.unmount_cache("nfs.example.test:/public", target)
                    command.assert_not_called()

    def test_unmount_receipt_is_reserved_first_and_never_deletes_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            target = root / "mount"
            target.mkdir()
            retained = target / "weights"
            retained.write_bytes(b"public fixture")
            output = root / "receipt.json"
            with mock.patch.object(cache, "unmount_cache") as unmount:
                def verify_reserved(*_):
                    self.assertTrue(output.is_file())
                    self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                unmount.side_effect = verify_reserved
                self.assertEqual(cache.unmount_report("nfs.example.test:/public", target, output, "a" * 40), 0)
                with self.assertRaises(FileExistsError):
                    cache.unmount_report("nfs.example.test:/public", target, output, "a" * 40)
                self.assertEqual(unmount.call_count, 1)
            report = json.loads(output.read_text())
            self.assertEqual((report["result"], report["unmounted"], report["volume_deleted"], report["files_removed"]),
                             ("pass", True, False, False))
            self.assertEqual(retained.read_bytes(), b"public fixture")
            with mock.patch.object(cache, "unmount_cache", side_effect=ValueError("must-not-log")):
                failed = root / "failed.json"
                self.assertEqual(cache.unmount_report("nfs.example.test:/public", target, failed, "a" * 40), 1)
                self.assertNotIn("must-not-log", failed.read_text())
                self.assertFalse(json.loads(failed.read_text())["unmounted"])
            with mock.patch.object(cache, "unmount_cache") as unmount:
                with self.assertRaises(ValueError):
                    cache.unmount_report("nfs.example.test:/public", target, target / "bad.json", "a" * 40)
                unmount.assert_not_called()


if __name__ == "__main__":
    unittest.main()
