# SPDX-License-Identifier: Apache-2.0
"""Fake mount readbacks and tiny local primitive probes; no real mounts or model I/O."""
from contextlib import ExitStack, redirect_stdout
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

from test_public_weight_cache import cache, ROOT
from test_evidence_history import git


class VirtiofsCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.mount = self.root / "mount"
        self.parent = self.mount / "private"
        self.tag = "synthetic-volume"
        self.manifest = ROOT / cache.profiles()[1]["pack"] / "files.sha256"
        self.output = self.root / "receipt.json"

    def readback(self, **changes):
        fs = dict(source=self.tag, target=str(self.mount), fstype="virtiofs", options="rw,nodev,nosuid,noexec")
        return json.dumps({"filesystems": [{**fs, **changes}]}).encode()

    def absent(self):
        return subprocess.CalledProcessError(1, "findmnt", output=b"")

    def report(self, **changes):
        args = dict(operation="admit", export=self.tag, mount=self.mount, local=self.parent,
                    manifest_path=self.manifest, seed_manifest=None, output=self.output,
                    producer="a" * 40, seconds=120, backend="virtiofs")
        return cache.profile_report(**{**args, **changes})

    def worker_result(self, **changes):
        return json.dumps({"schema": "nib.public-weight-profile-worker/v1", "operation": "admit",
                           "publication_mode": "none", "files": 0, "verified_bytes": 0,
                           "filesystem_checks": cache.ADMISSION_CHECKS, **changes}).encode()

    def test_mount_has_exact_source_type_flags_and_no_nfs_options(self):
        for readonly in (False, True):
            options = ("ro" if readonly else "rw") + ",nodev,nosuid,noexec"
            with mock.patch.object(cache, "command", side_effect=[self.absent(), b"", self.readback(options=options)]) as call:
                cache.mount_cache(self.tag, self.mount, backend="virtiofs", readonly=readonly)
            self.assertEqual(call.call_args_list[1].args[0],
                             ["mount", "-t", "virtiofs", "-o", ("ro," if readonly else "") +
                              "nodev,nosuid,noexec", self.tag, str(self.mount)])
            self.assertEqual(self.mount.stat().st_mode & 0o777, 0o700)

    def test_bad_tag_existing_mount_or_nonempty_target_never_mounts(self):
        for tag in ("", "-o", "../tag", "tag/path", "nfs.example.test:/public", "tag\n", "x" * 129):
            with self.subTest(tag=tag), mock.patch.object(cache, "command") as call, self.assertRaises(ValueError):
                cache.mount_cache(tag, self.mount, backend="virtiofs")
            call.assert_not_called()
        with mock.patch.object(cache, "command", return_value=self.readback()) as call, self.assertRaises(ValueError):
            cache.mount_cache(self.tag, self.mount, backend="virtiofs")
        self.assertEqual(call.call_count, 1)
        (self.mount / "retained").write_bytes(b"unchanged")
        with mock.patch.object(cache, "command") as call, self.assertRaises(ValueError):
            cache.mount_cache(self.tag, self.mount, backend="virtiofs")
        call.assert_not_called()

    def test_readback_rejects_wrong_source_type_target_and_flags(self):
        changes = [dict(source="wrong-tag"), dict(target=str(self.parent)), dict(fstype="nfs4")]
        changes += [dict(options=value) for value in ("rw", "rw,nodev,nosuid", "ro,nodev,nosuid,noexec",
                                                      "ro,rw,nodev,nosuid,noexec", "nodev,nosuid,noexec",
                                                      "rw,nodev,nosuid,noexec,exec", "rw,nodev,nosuid,noexec,dev",
                                                      "rw,nodev,nosuid,noexec,suid")]
        for change in changes:
            with self.subTest(change=change), mock.patch.object(cache, "command", return_value=self.readback(**change)), \
                    self.assertRaises(ValueError):
                cache.verify_mount(self.tag, self.mount, backend="virtiofs")
        for data in (b"{}", b'{"filesystems":[]}', b'{"filesystems":[{},{}]}'):
            with mock.patch.object(cache, "command", return_value=data), self.assertRaises((ValueError, KeyError)):
                cache.verify_mount(self.tag, self.mount, backend="virtiofs")

    def test_private_traversal_rejects_foreign_ancestor_even_if_final_owned(self):
        self.parent.mkdir(parents=True, mode=0o700)
        ancestor = self.mount.stat().st_ino
        actual = cache.os.fstat
        def foreign(fd):
            info = actual(fd)
            if info.st_ino == ancestor:
                return types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=os.getuid() + 12345)
            return info
        with mock.patch.object(cache.os, "fstat", side_effect=foreign), self.assertRaises(ValueError):
            with cache.directory(self.parent, private=True):
                self.fail("foreign ancestor accepted")
        # Existing NFS traversal intentionally retains its previous contract.
        with mock.patch.object(cache.os, "fstat", side_effect=foreign), cache.directory(self.parent):
            pass

    def test_only_named_root_owned_temporary_ancestors_get_sticky_exception(self):
        self.parent.mkdir(parents=True, mode=0o700)
        self.mount.chmod(0o1777)
        with self.assertRaises(ValueError), cache.directory(self.parent, private=True):
            pass
        self.mount.chmod(0o755)
        self.parent.chmod(0o755)
        with self.assertRaises(ValueError), cache.directory(self.parent, private=True):
            pass
        alias = self.root / "alias"
        alias.symlink_to(self.mount, target_is_directory=True)
        with self.assertRaises(OSError), cache.directory(alias / "private", private=True):
            pass

    def test_real_primitives_leave_existing_files_and_remove_only_probe(self):
        self.parent.mkdir(parents=True, mode=0o700)
        marker = self.parent / "retained"
        marker.write_bytes(b"unchanged")
        with mock.patch.object(cache, "command", return_value=self.readback()) as call:
            cache.admit_filesystem(self.tag, self.mount, self.parent, time.monotonic() + 10)
        self.assertEqual(call.call_count, 3)
        self.assertIn("--target", call.call_args.args[0])
        self.assertEqual(list(self.parent.iterdir()), [marker])
        self.assertEqual(marker.read_bytes(), b"unchanged")

    def test_nested_mount_rejected_before_probe(self):
        self.mount.mkdir(mode=0o700)
        with mock.patch.object(cache, "command", side_effect=[self.readback(), self.readback(target=str(self.parent))]), \
                mock.patch.object(cache, "filesystem_probe") as probe, self.assertRaises(ValueError):
            cache.admit_filesystem(self.tag, self.mount, self.parent, time.monotonic() + 10)
        probe.assert_not_called()

    def test_probe_rejects_unsupported_or_nonexclusive_lock_and_link(self):
        for kind in ("lock-noop", "lock-error", "link-noop", "link-error"):
            with self.subTest(kind=kind), ExitStack() as stack:
                directory = self.root / kind
                directory.mkdir(mode=0o700)
                if kind.startswith("lock"):
                    stack.enter_context(mock.patch.object(cache.fcntl, "flock", side_effect=
                        None if kind == "lock-noop" else OSError(errno.EACCES, "private-detail")))
                else:
                    stack.enter_context(mock.patch.object(cache.os, "link", side_effect=
                        None if kind == "link-noop" else OSError(errno.ENOTSUP, "private-detail")))
                with cache.directory(directory, private=True) as fd, self.assertRaises((ValueError, OSError)):
                    cache.filesystem_probe(fd, time.monotonic() + 10)
                self.assertTrue(all(p.name.startswith(".nib-fs-check-") for p in directory.iterdir()))

    def test_file_and_directory_sync_errors_fail_admission(self):
        for is_directory in (False, True):
            directory = self.root / str(is_directory)
            directory.mkdir(mode=0o700)
            actual = cache.os.fsync
            def sync(fd):
                if stat.S_ISDIR(os.fstat(fd).st_mode) == is_directory:
                    raise OSError("private-sync-error")
                return actual(fd)
            with mock.patch.object(cache.os, "fsync", side_effect=sync), cache.directory(directory, private=True) as fd, \
                    self.assertRaises(OSError):
                cache.filesystem_probe(fd, time.monotonic() + 10)

    def test_worker_admission_binds_manifest_and_never_copies_or_checks_capacity(self):
        with mock.patch.object(cache.sys, "platform", "linux"), \
                mock.patch.object(cache.sys, "flags", types.SimpleNamespace(isolated=True, no_site=True)), \
                mock.patch.object(cache.sys, "dont_write_bytecode", True), \
                mock.patch.object(cache.os, "getuid", return_value=0), \
                mock.patch.object(cache, "mount_cache") as mount, mock.patch.object(cache, "admit_filesystem") as admit, \
                mock.patch.object(cache, "copy_pack", side_effect=AssertionError("no copy")), \
                mock.patch.object(cache.os, "fstatvfs", side_effect=AssertionError("no capacity claim")), \
                redirect_stdout(io.StringIO()) as output:
            cache.profile_worker("admit", self.tag, str(self.mount), str(self.parent), str(self.manifest), "-", "60", "virtiofs")
            self.assertEqual(json.loads(output.getvalue()), json.loads(self.worker_result()))
            mount.assert_called_once_with(self.tag, self.mount, backend="virtiofs")
            self.assertEqual(admit.call_args.args[:3], (self.tag, self.mount, self.parent))
            mount.reset_mock()
            wrong = ROOT / cache.profiles()[0]["pack"] / "files.sha256"
            with self.assertRaises(ValueError):
                cache.profile_worker("admit", self.tag, str(self.mount), str(self.parent), str(wrong), "-", "60", "virtiofs")
            mount.assert_not_called()

    def test_virtiofs_copy_modes_reuse_existing_copy_helpers(self):
        self.mount.mkdir(mode=0o700)
        local = self.root / "local"
        target = cache.profiles()[1]
        result = {"files": 79, "verified_bytes": target["total_bytes"]}
        with mock.patch.object(cache.sys, "platform", "linux"), \
                mock.patch.object(cache.sys, "flags", types.SimpleNamespace(isolated=True, no_site=True)), \
                mock.patch.object(cache.sys, "dont_write_bytecode", True), \
                mock.patch.object(cache.os, "getuid", return_value=0), \
                mock.patch.object(cache, "mount_cache") as mount, mock.patch.object(cache, "verify_mount") as verify, \
                mock.patch.object(cache, "upload_copy", return_value=result) as upload, \
                mock.patch.object(cache, "atomic_copy", return_value=result) as restore, \
                mock.patch.object(cache.os, "fstatvfs", return_value=types.SimpleNamespace(f_bavail=1 << 50, f_frsize=1)):
            for operation in ("upload", "restore"):
                with redirect_stdout(io.StringIO()) as output:
                    cache.profile_worker(operation, self.tag, str(self.mount), str(local), str(self.manifest), "-", "60", "virtiofs")
                self.assertEqual(json.loads(output.getvalue())["files"], 79)
                mount.assert_called_with(self.tag, self.mount, readonly=operation != "upload", backend="virtiofs")
                verify.assert_called_with(self.tag, self.mount, readonly=operation != "upload", backend="virtiofs")
            self.assertEqual(upload.call_args.args[:2], (local, self.mount / target["served_model_name"]))
            self.assertEqual(restore.call_args.args[:2], (self.mount / target["served_model_name"], local))
            self.assertEqual((upload.call_count, restore.call_count), (1, 1))

    def test_default_nfs_profile_receipt_bytes_match_base_except_script_hash(self):
        original = git(ROOT, "show", "11923d966a82f3e840ad0590ed0dc95392128995:host/cache-public-weights.py")
        path = self.root / "original.py"
        path.write_bytes(original)
        old = types.ModuleType("original_nfs_cache")
        old.__file__ = str(path)
        exec(compile(original, str(path), "exec"), old.__dict__)
        for operation in ("seed", "upload", "restore"):
            records = []
            row = {"schema": "nib.public-weight-profile-worker/v1", "operation": operation,
                   "publication_mode": cache.publication_mode(operation),
                   "files": 48 if operation == "seed" else 79,
                   "verified_bytes": cache.SEED_BYTES if operation == "seed" else cache.profiles()[1]["total_bytes"]}
            for index, module in enumerate((old, cache)):
                output = self.root / (operation + str(index) + ".json")
                with mock.patch.object(module, "profiles", return_value=cache.profiles()), \
                        mock.patch.object(module, "load_capture", return_value=mock.Mock(return_value=json.dumps(row).encode())), \
                        mock.patch.object(module.datetime, "datetime") as clock:
                    clock.now.return_value.isoformat.return_value = "2000-01-01T00:00:00+00:00"
                    self.assertEqual(module.profile_report(operation, "nfs.example.test:/public", self.mount,
                        self.root / "local", self.manifest, self.root / "old-manifest" if operation == "seed" else None,
                        output, "a" * 40, 120), 0)
                records.append(output.read_bytes())
            self.assertEqual(records[0].replace(hashlib.sha256(original).hexdigest().encode(),
                             hashlib.sha256(Path(cache.__file__).read_bytes()).hexdigest().encode()), records[1])

    def test_receipt_reserved_before_bounded_worker_no_model_or_runtime_claims(self):
        def capture(argv, **kwargs):
            pending = json.loads(self.output.read_bytes())
            self.assertEqual((pending["result"], pending["filesystem_admitted"]), ("fail", False))
            self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(argv[-2:], ["60", "virtiofs"])
            self.assertEqual(argv[7].encode(), Path(cache.__file__).read_bytes())
            self.assertEqual(kwargs, {"timeout": 60, "limit": 4096})
            return self.worker_result()
        with mock.patch.object(cache, "load_capture", return_value=capture):
            self.assertEqual(self.report(), 0)
            with self.assertRaises(FileExistsError):
                self.report()
        raw = self.output.read_text()
        result = json.loads(raw)
        self.assertEqual(result["model_profile"], cache.profiles()[1])
        self.assertTrue(result["filesystem_admitted"])
        self.assertEqual((result["files"], result["verified_bytes"]), (0, 0))
        self.assertEqual(result["publication"], "not-applicable")
        for key in ("pack_complete", "runtime_ready", "confidential", "hardware_validated", "immutable", "capacity_checked"):
            self.assertIs(result[key], False)
        for private in (self.tag, str(self.mount), str(self.root)):
            self.assertNotIn(private, raw)

    def test_bad_worker_messages_and_failures_never_admit(self):
        invalid = [self.worker_result(files=True), self.worker_result(verified_bytes=1),
                   self.worker_result(filesystem_checks=[]), self.worker_result(runtime_ready=True),
                   self.worker_result(operation="upload"), self.worker_result(publication_mode="confirmed"),
                   b'{"files":0,"files":0}', b'null']
        invalid += [ValueError(reason) for reason in ("probe-timeout", "probe-cleanup-timeout", "private-detail")]
        invalid += [InterruptedError("private-detail")]
        for index, value in enumerate(invalid):
            self.output = self.root / (str(index) + ".json")
            capture = mock.Mock(side_effect=value) if isinstance(value, Exception) else mock.Mock(return_value=value)
            with mock.patch.object(cache, "load_capture", return_value=capture):
                self.assertEqual(self.report(), 1)
            raw = self.output.read_text()
            result = json.loads(raw)
            self.assertFalse(result["filesystem_admitted"])
            self.assertFalse(result["pack_complete"])
            self.assertEqual(result["mount_state"], "unproven")
            self.assertNotIn("private-detail", raw)

    def test_invalid_bounds_and_paths_fail_before_worker_or_receipt(self):
        for change in (dict(seconds=60), dict(seconds=301), dict(backend="nfs"), dict(local=self.mount),
                       dict(local=self.parent / "nested"), dict(output=self.mount / "receipt.json"),
                       dict(manifest_path=self.mount / "manifest")):
            with self.subTest(change=change), mock.patch.object(cache, "load_capture") as load, self.assertRaises(ValueError):
                self.report(**change)
            load.assert_not_called()
            self.assertFalse(self.output.exists())

    def test_real_supervisor_bounds_stalled_child(self):
        capture = cache.load_capture()
        def stalled(*_, **__):
            return capture([sys.executable, "-I", "-S", "-c", "import time; time.sleep(10)"], timeout=0.1)
        started = time.monotonic()
        with mock.patch.object(cache, "load_capture", return_value=stalled):
            self.assertEqual(self.report(), 1)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(json.loads(self.output.read_bytes())["failure"], "timeout")

    def test_virtiofs_unmount_supports_readonly_without_force_or_lazy(self):
        self.mount.mkdir(mode=0o700)
        for access in ("ro", "rw"):
            with mock.patch.object(cache, "command", side_effect=[
                    self.readback(options=access + ",nodev,nosuid,noexec"), b"", self.absent()]) as call:
                cache.unmount_cache(self.tag, self.mount, backend="virtiofs")
            self.assertEqual(call.call_args_list[1].args[0], ["umount", "--", str(self.mount)])

    def test_cli_requires_explicit_backend_profile_and_admission_paths(self):
        argv = [cache.__file__, "--backend", "virtiofs", "--export", self.tag, "--mount", str(self.mount),
                "--mount-only", "--staging-parent", str(self.parent), "--model-profile", "uncensored",
                "--manifest", str(self.manifest), "--output", str(self.output), "--producer", "a" * 40, "--timeout", "120"]
        flags = types.SimpleNamespace(**{key: getattr(sys.flags, key) for key in dir(sys.flags)
                                        if not key.startswith('_') and isinstance(getattr(sys.flags, key), int)})
        flags.isolated = flags.no_site = True
        with mock.patch.object(cache.sys, "flags", flags), mock.patch.object(cache.sys, "platform", "linux"), \
                mock.patch.object(cache.os, "getuid", return_value=0), \
                mock.patch.object(cache, "profile_report", return_value=0) as report:
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(cache.main(), 0)
            report.assert_called_once_with("admit", self.tag, self.mount, self.parent, self.manifest, None,
                                           self.output, "a" * 40, 120, backend="virtiofs")
            for extra in (["--unmount-only"], ["--source", str(self.root / "source")], ["--backend", "nfs"],
                          ["--model-profile", "original"]):
                with mock.patch.object(sys, "argv", argv + extra), self.assertRaises(ValueError):
                    cache.main()
            self.assertEqual(report.call_count, 1)


if __name__ == "__main__":
    unittest.main()
