# SPDX-License-Identifier: Apache-2.0
"""Pinned metadata and tiny regular-copy fixtures; no mounts, model or network."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
cache = types.ModuleType("profile_cache")
cache.__file__ = str(ROOT / "host/cache-public-weights.py")
exec(compile(Path(cache.__file__).read_bytes(), cache.__file__, "exec"), cache.__dict__)


class ProfileCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir(mode=0o700)
        (self.source / "nested").mkdir(mode=0o700)
        self.rows = []
        for name, data in (("config.json", b"public configuration"), ("nested/weights", b"public weights"),
                           ("changed", b"old public bytes")):
            (self.source / name).write_bytes(data)
            self.rows.append((hashlib.sha256(data).hexdigest(), len(data), name))
        self.selected = self.rows[:2]
        self.destination = self.root / "destination"

    def copy(self, rows=None):
        return cache.atomic_copy(self.source, self.destination, self.selected if rows is None else rows,
                                 self.rows, time.monotonic() + 10)

    def arguments(self, operation="seed"):
        return (operation, "nfs.example.test:/public", self.root / "mount", self.destination,
                self.root / "target-manifest", self.root / "old-manifest" if operation == "seed" else None,
                self.root / "receipt.json", "b" * 40, 120)

    def worker_result(self, operation="seed"):
        return json.dumps({"schema": "nib.public-weight-profile-worker/v1", "operation": operation,
                           "publication_mode": cache.publication_mode(operation),
                           "files": 48 if operation == "seed" else 79,
                           "verified_bytes": 310737990397 if operation == "seed" else 510311056155}).encode()

    def test_pinned_intersection_includes_only_equal_path_size_and_hash(self):
        original, target = cache.profiles()
        old_raw = (ROOT / original["pack"] / "files.sha256").read_bytes()
        target_raw = (ROOT / target["pack"] / "files.sha256").read_bytes()
        old = cache.manifest(old_raw)
        new = cache.manifest_rows(target_raw, target["manifest_sha256"], target["total_bytes"], 79)
        rows = cache.seed_rows(old, new)
        self.assertEqual((len(rows), sum(row[1] for row in rows)), (48, 310737990397))
        shards = [row for row in rows if row[2].endswith(".safetensors")]
        self.assertEqual((len(shards), sum(row[1] for row in shards)), (21, 310723700832))
        self.assertEqual((len(new) - len(rows), target["total_bytes"] - sum(row[1] for row in rows)),
                         (31, 199573065758))
        for mutated in (old_raw, target_raw[:-1], target_raw + b"extra"):
            with self.assertRaises(ValueError):
                cache.manifest_rows(mutated, target["manifest_sha256"], target["total_bytes"], 79)
        digest, size, name = self.rows[0]
        self.assertEqual(cache.seed_rows(self.rows, [(digest, size + 1, name), ("0" * 64, size, name),
                                                    (digest, size, "renamed")]), [])

    def test_profile_and_publication_source_tamper_rejected(self):
        for relative, expected in (("scripts/deepseek_model_profile.py", cache.PROFILE_SHA256),
                                   ("scripts/pilot/export.py", cache.PUBLICATION_SHA256)):
            with self.subTest(relative=relative), mock.patch.object(cache, "read_regular", return_value=b"raise AssertionError()"):
                with self.assertRaises(ValueError):
                    cache.load_pinned(relative, expected)

    def test_atomic_seed_copies_only_selected_regular_files_and_preserves_source(self):
        before = {name: (self.source / name).read_bytes() for _, _, name in self.rows}
        result = self.copy()
        self.assertEqual(result, {"files": 2, "verified_bytes": sum(row[1] for row in self.selected)})
        self.assertFalse((self.destination / "changed").exists())
        for _, _, name in self.selected:
            source, target = self.source / name, self.destination / name
            self.assertEqual(source.read_bytes(), target.read_bytes())
            self.assertNotEqual(source.stat().st_ino, target.stat().st_ino)
            self.assertEqual((source.stat().st_nlink, target.stat().st_nlink), (1, 1))
        self.assertEqual(before, {name: (self.source / name).read_bytes() for _, _, name in self.rows})
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o700)
        self.assertFalse(list(self.root.glob(".nib-cache-*")))
        with self.assertRaises(ValueError):
            self.copy()

    def test_full_profile_uses_same_atomic_copy(self):
        self.assertEqual(self.copy(self.rows), {"files": 3, "verified_bytes": sum(row[1] for row in self.rows)})

    def test_upload_uses_exclusive_creation_without_any_rename(self):
        with mock.patch.object(cache, "load_pinned", side_effect=AssertionError("rename helper must not load")), \
                mock.patch.object(cache, "publication_preflight", side_effect=AssertionError("no rename probe")):
            result = cache.upload_copy(self.source, self.destination, self.rows, time.monotonic() + 10)
        self.assertEqual(result, {"files": 3, "verified_bytes": sum(row[1] for row in self.rows)})
        for _, _, name in self.rows:
            self.assertEqual((self.source / name).read_bytes(), (self.destination / name).read_bytes())
            self.assertEqual((self.destination / name).stat().st_nlink, 1)
        with self.assertRaises(ValueError):
            cache.upload_copy(self.source, self.destination, self.rows, time.monotonic() + 10)

    def test_failed_partial_upload_cannot_verify_restore_or_be_overwritten(self):
        original_write = cache.os.write
        calls = 0
        def fail(fd, data):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise OSError("synthetic interruption")
            return original_write(fd, data)
        with mock.patch.object(cache.os, "write", side_effect=fail), self.assertRaises(OSError):
            cache.upload_copy(self.source, self.destination, self.rows, time.monotonic() + 10)
        self.assertTrue(self.destination.is_dir())
        with self.assertRaises(ValueError):
            cache.strict_inventory(self.destination, self.rows)
        restore = self.root / "restore"
        with self.assertRaises(ValueError):
            cache.atomic_copy(self.destination, restore, self.rows, self.rows, time.monotonic() + 10)
        self.assertFalse(restore.exists())
        retained = (self.destination / "config.json").read_bytes()
        with self.assertRaises(ValueError):
            cache.upload_copy(self.source, self.destination, self.rows, time.monotonic() + 10)
        self.assertEqual((self.destination / "config.json").read_bytes(), retained)

    def test_bad_source_never_publishes_or_copies(self):
        for kind in ("extra", "partial", "corrupt", "truncated", "symlink", "hardlink", "fifo", "unselected-hardlink"):
            with self.subTest(kind=kind):
                target = self.source / "config.json"
                extra = self.source / (".nib-part-fixed" if kind == "partial" else "extra")
                original = target.read_bytes()
                if kind in {"extra", "partial"}: extra.write_bytes(b"unknown")
                if kind == "corrupt": target.write_bytes(b"x" * len(original))
                if kind == "truncated": target.write_bytes(b"x")
                if kind in {"symlink", "fifo"}:
                    target.unlink()
                    target.symlink_to(self.source / "changed") if kind == "symlink" else os.mkfifo(target)
                if kind in {"hardlink", "unselected-hardlink"}:
                    os.link(target if kind == "hardlink" else self.source / "changed", self.root / "alias")
                with self.assertRaises((ValueError, OSError)):
                    self.copy()
                self.assertFalse(self.destination.exists())
                self.assertFalse(list(self.root.glob(".nib-cache-*")))
                if extra.exists(): extra.unlink()
                if (self.root / "alias").exists(): (self.root / "alias").unlink()
                target.unlink()
                target.write_bytes(original)

    def test_copy_rehashes_target_rows_not_just_old_manifest(self):
        bad = [("0" * 64, self.selected[0][1], self.selected[0][2]), self.selected[1]]
        with self.assertRaises(ValueError):
            self.copy(bad)
        self.assertFalse(self.destination.exists())

    def test_source_mutation_prevents_publication(self):
        original = cache.copy_pack
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.source / "changed").write_bytes(b"different bytes!")
            return result
        with mock.patch.object(cache, "copy_pack", side_effect=mutate), self.assertRaises(ValueError):
            self.copy()
        self.assertFalse(self.destination.exists())

    def test_post_hash_destination_mutation_prevents_publication(self):
        original = cache.copy_pack
        def mutate(source, target, *args, **kwargs):
            result = original(source, target, *args, **kwargs)
            path = target / "config.json"
            path.write_bytes(b"x" * path.stat().st_size)
            return result
        with mock.patch.object(cache, "copy_pack", side_effect=mutate), self.assertRaises(ValueError):
            self.copy()
        self.assertFalse(self.destination.exists())

    def test_read_during_source_mutation_is_detected(self):
        original = cache.os.read
        mutated = False
        def read(fd, size):
            nonlocal mutated
            data = original(fd, size)
            if data and not mutated:
                mutated = True
                path = self.source / "config.json"
                path.write_bytes(b"x" * path.stat().st_size)
            return data
        with mock.patch.object(cache.os, "read", side_effect=read), self.assertRaises(ValueError):
            self.copy()
        self.assertTrue(mutated)
        self.assertFalse(self.destination.exists())

    def test_publication_is_exclusive_and_failure_has_no_fallback(self):
        actual = cache.load_pinned
        for kind in ("conflict", "unsupported"):
            with self.subTest(kind=kind):
                def load(relative, expected):
                    helper = actual(relative, expected)
                    rename = helper.rename_exclusive
                    def publish(fd, source, target):
                        if kind == "unsupported":
                            raise OSError("unsupported")
                        if not source.startswith(".nib-cache-"):
                            return rename(fd, source, target)
                        self.destination.mkdir()
                        (self.destination / "retained").write_bytes(b"unchanged")
                        return rename(fd, source, target)
                    helper.rename_exclusive = publish
                    return helper
                with mock.patch.object(cache, "load_pinned", side_effect=load), self.assertRaises((OSError, ValueError)):
                    self.copy()
                if kind == "conflict":
                    self.assertEqual((self.destination / "retained").read_bytes(), b"unchanged")
                    (self.destination / "retained").unlink()
                    self.destination.rmdir()
                else:
                    self.assertFalse(self.destination.exists())

    def test_nested_and_symlink_parents_and_expired_deadline_rejected(self):
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        for target in (self.source / "child", alias / "destination"):
            with self.subTest(target=target), self.assertRaises((ValueError, OSError)):
                cache.atomic_copy(self.source, target, self.selected, self.rows, time.monotonic() + 10)
        with self.assertRaises(ValueError):
            cache.atomic_copy(self.source, self.destination, self.selected, self.rows, time.monotonic() - 1)
        self.assertFalse(self.destination.exists())

    def test_seed_receipt_is_reserved_before_worker_and_cannot_claim_complete_pack(self):
        args = self.arguments()
        def capture(argv, **kwargs):
            pending = json.loads(args[6].read_bytes())
            self.assertEqual((pending["result"], pending["publication"], pending["cleanup"]),
                             ("fail", "unproven", "unproven"))
            self.assertEqual(args[6].stat().st_mode & 0o777, 0o600)
            self.assertEqual(argv[:6], [sys.executable, "-I", "-S", "-B", "-c", cache.PROFILE_WORKER_BOOTSTRAP])
            self.assertEqual(argv[7].encode(), Path(cache.__file__).read_bytes())
            self.assertEqual(argv[8:], ["seed", args[1], *map(str, args[2:6]), "60"])
            self.assertEqual(kwargs, {"timeout": 60, "limit": 4096})
            return self.worker_result()
        with mock.patch.object(cache, "load_capture", return_value=capture), \
                mock.patch.object(cache, "mount_cache") as mount, mock.patch.object(cache, "atomic_copy") as copy:
            self.assertEqual(cache.profile_report(*args), 0)
            mount.assert_not_called()
            copy.assert_not_called()
            with self.assertRaises(FileExistsError):
                cache.profile_report(*args)
        row = json.loads(args[6].read_bytes())
        self.assertEqual((row["result"], row["files"], row["verified_bytes"]), ("pass", 48, 310737990397))
        self.assertEqual((row["remaining_files"], row["remaining_bytes"]), (31, 199573065758))
        self.assertEqual((row["source_manifest_sha256"], row["target_manifest_sha256"]),
                         (cache.MANIFEST_SHA256, cache.profiles()[1]["manifest_sha256"]))
        for field in ("pack_complete", "runtime_ready", "confidential", "source_removed", "process_group_absence_verified"):
            self.assertFalse(row[field])

    def test_full_profile_receipts_bind_mode_and_complete_bytes_not_runtime(self):
        for operation in ("upload", "restore"):
            with self.subTest(operation=operation):
                args = list(self.arguments(operation))
                args[6] = self.root / (operation + ".json")
                with mock.patch.object(cache, "load_capture", return_value=lambda *_a, **_k: self.worker_result(operation)):
                    self.assertEqual(cache.profile_report(*args), 0)
                row = json.loads(args[6].read_bytes())
                self.assertTrue(row["pack_complete"])
                self.assertEqual((row["files"], row["verified_bytes"]), (79, 510311056155))
                self.assertEqual(row["publication_mode"], cache.publication_mode(operation))
                if operation == "upload":
                    self.assertIsNone(row["publication_helper_sha256"])
                self.assertFalse(row["runtime_ready"])
                self.assertFalse(row["confidential"])

    def test_failures_never_become_seed_or_complete_proof(self):
        wrong = json.loads(self.worker_result())
        outcomes = [ValueError("probe-timeout"), ValueError("probe-cleanup-timeout"), InterruptedError("private"),
                    ValueError("probe-output-limit"), ValueError("probe-command-failed"), b"{}"]
        outcomes.append(self.worker_result().replace(b'"files": 48', b'"files": 79, "files": 48'))
        for field, value in (("files", True), ("verified_bytes", 510311056155), ("operation", "restore"),
                             ("publication_mode", "exclusive-directory-copy"), ("extra", 1)):
            outcomes.append(json.dumps({**wrong, field: value}).encode())
        for index, outcome in enumerate(outcomes):
            args = list(self.arguments())
            args[6] = self.root / ("failed-" + str(index) + ".json")
            callback = mock.Mock(side_effect=outcome) if isinstance(outcome, Exception) else mock.Mock(return_value=outcome)
            with mock.patch.object(cache, "load_capture", return_value=callback):
                self.assertEqual(cache.profile_report(*args), 1)
            row = json.loads(args[6].read_bytes())
            self.assertEqual((row["result"], row["cleanup"], row["publication"]), ("fail", "unproven", "unproven"))
            self.assertFalse(row["pack_complete"])
            self.assertFalse(row["worker_exit_confirmed"])
            self.assertEqual(row["verified_bytes"], 0)
            self.assertNotIn("private", args[6].read_text())

    def test_path_mode_and_deadline_rejections_precede_worker(self):
        for index, value in ((0, "arbitrary"), (2, Path("relative")), (3, self.root / "mount/child"),
                             (4, Path("models/new/files.sha256")), (5, Path("models/old/files.sha256")),
                             (5, None), (6, self.destination / "receipt"), (8, 60), (8, 7201)):
            args = list(self.arguments())
            args[index] = value
            with self.subTest(index=index, value=value), mock.patch.object(cache, "load_capture") as load:
                with self.assertRaises(ValueError):
                    cache.profile_report(*args)
                load.assert_not_called()
        self.assertFalse((self.root / "receipt.json").exists())

    def test_real_supervisor_timeout_retains_failure_receipt(self):
        source = b"import time\ndef profile_worker(*args):\n time.sleep(10)\n"
        actual_read, actual_capture = cache.read_regular, cache.load_capture()
        def read(path, *args):
            return source if path == Path(cache.__file__).absolute() else actual_read(path, *args)
        def capture(argv, **kwargs):
            return actual_capture(argv, timeout=0.25, limit=kwargs["limit"])
        args = self.arguments()
        started = time.monotonic()
        with mock.patch.object(cache, "read_regular", side_effect=read), mock.patch.object(cache, "load_capture", return_value=capture):
            self.assertEqual(cache.profile_report(*args), 1)
        self.assertLess(time.monotonic() - started, 3)
        row = json.loads(args[6].read_bytes())
        self.assertEqual((row["failure"], row["cleanup"], row["publication"]), ("timeout", "unproven", "unproven"))
        self.assertFalse(row["pack_complete"])

    def test_all_new_operations_supervise_blocking_io_outside_parent(self):
        actual_read, actual_capture = cache.read_regular, cache.load_capture()
        source = b"import time\ndef profile_worker(*args):\n time.sleep(10)\n"
        for operation in ("seed", "upload", "restore"):
            with self.subTest(operation=operation):
                args = list(self.arguments(operation))
                args[6] = self.root / (operation + ".json")
                def read(path, *rest):
                    return source if path == Path(cache.__file__).absolute() else actual_read(path, *rest)
                def capture(argv, **kwargs):
                    return actual_capture(argv, timeout=0.25, limit=kwargs["limit"])
                with mock.patch.object(cache, "read_regular", side_effect=read), \
                        mock.patch.object(cache, "load_capture", return_value=capture), \
                        mock.patch.object(cache, "mount_cache") as mount, \
                        mock.patch.object(cache, "copy_pack") as copy, \
                        mock.patch.object(cache.os, "fstatvfs") as space:
                    self.assertEqual(cache.profile_report(*args), 1)
                    for action in (mount, copy, space): action.assert_not_called()
                row = json.loads(args[6].read_bytes())
                self.assertEqual((row["result"], row["failure"], row["cleanup"]), ("fail", "timeout", "unproven"))
                self.assertFalse(row["pack_complete"])

    def test_six_file_public_package_has_all_fixed_source_dependencies(self):
        package = self.root / "package"
        names = ("host/cache-public-weights.py", "host/probe.py", "scripts/deepseek_model_profile.py",
                 "scripts/pilot/export.py", "models/deepseek-v4.1-flash-1017/1/files.sha256",
                 "models/deepseek-v4.1-flash-1017-uncensored-fp8/1/files.sha256")
        for name in names:
            target = package / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / name).read_bytes())
        helper = types.ModuleType("packaged_cache")
        helper.__file__ = str(package / names[0])
        exec(compile((package / names[0]).read_bytes(), helper.__file__, "exec"), helper.__dict__)
        self.assertEqual(helper.profiles(), cache.profiles())
        self.assertTrue(callable(helper.load_capture()))
        self.assertTrue(callable(helper.load_pinned("scripts/pilot/export.py", cache.PUBLICATION_SHA256).rename_exclusive))

    def test_all_three_workers_route_io_mount_mode_profile_and_capacity(self):
        original, target = cache.profiles()
        old_path, new_path = self.root / "old-manifest", self.root / "target-manifest"
        old_path.write_bytes((ROOT / original["pack"] / "files.sha256").read_bytes())
        new_path.write_bytes((ROOT / target["pack"] / "files.sha256").read_bytes())
        old = cache.manifest(old_path.read_bytes())
        new = cache.manifest_rows(new_path.read_bytes(), target["manifest_sha256"], target["total_bytes"], 79)
        mount = self.root / "mount"
        mount.mkdir(mode=0o700)
        for operation in ("seed", "upload", "restore"):
            with self.subTest(operation=operation):
                selected = cache.seed_rows(old, new) if operation == "seed" else new
                printed = io.StringIO()
                with mock.patch.object(cache.sys, "platform", "linux"), \
                        mock.patch.object(cache.sys, "flags", types.SimpleNamespace(isolated=True, no_site=True)), \
                        mock.patch.object(cache.sys, "dont_write_bytecode", True), \
                        mock.patch.object(cache.os, "getuid", return_value=0), \
                        mock.patch.object(cache, "mount_cache") as attach, \
                        mock.patch.object(cache, "verify_mount") as readback, \
                        mock.patch.object(cache.os, "fstatvfs", return_value=types.SimpleNamespace(f_bavail=929387577344, f_frsize=1)), \
                        mock.patch.object(cache, "atomic_copy", return_value={"files": len(selected), "verified_bytes": sum(r[1] for r in selected)}) as copy, \
                        mock.patch.object(cache, "upload_copy", return_value={"files": len(selected), "verified_bytes": sum(r[1] for r in selected)}) as upload, \
                        mock.patch.object(cache.sys, "stdout", printed):
                    cache.profile_worker(operation, "nfs.example.test:/public", str(mount), str(self.destination),
                                         str(new_path), str(old_path) if operation == "seed" else "-", "60")
                attach.assert_called_once_with("nfs.example.test:/public", mount, readonly=operation != "upload")
                readback.assert_called_once_with("nfs.example.test:/public", mount, readonly=operation != "upload")
                if operation == "upload":
                    copy.assert_not_called()
                else:
                    upload.assert_not_called()
                args = (upload if operation == "upload" else copy).call_args.args
                self.assertEqual(args[0], self.destination if operation == "upload" else mount / (
                    original["served_model_name"] if operation == "seed" else target["served_model_name"]))
                self.assertEqual(args[1], mount / target["served_model_name"] if operation == "upload" else self.destination)
                self.assertEqual(args[2], selected)
                if operation != "upload": self.assertEqual(args[3], old if operation == "seed" else new)
                self.assertEqual(json.loads(printed.getvalue())["operation"], operation)

    def test_post_copy_mount_readback_failure_never_claims_complete_upload(self):
        target = cache.profiles()[1]
        manifest = self.root / "target-manifest"
        manifest.write_bytes((ROOT / target["pack"] / "files.sha256").read_bytes())
        mount = self.root / "mount"
        mount.mkdir(mode=0o700)
        copied = False
        def copy(*_):
            nonlocal copied
            copied = True
            return {"files": 79, "verified_bytes": 510311056155}
        def capture(*_args, **_kwargs):
            printed = io.StringIO()
            with mock.patch.object(cache.sys, "platform", "linux"), \
                    mock.patch.object(cache.sys, "flags", types.SimpleNamespace(isolated=True, no_site=True)), \
                    mock.patch.object(cache.sys, "dont_write_bytecode", True), mock.patch.object(cache.os, "getuid", return_value=0), \
                    mock.patch.object(cache, "mount_cache"), mock.patch.object(cache, "verify_mount", side_effect=ValueError("readback failed")), \
                    mock.patch.object(cache.os, "fstatvfs", return_value=types.SimpleNamespace(f_bavail=929387577344, f_frsize=1)), \
                    mock.patch.object(cache, "upload_copy", side_effect=copy), mock.patch.object(cache.sys, "stdout", printed):
                with self.assertRaises(ValueError):
                    cache.profile_worker("upload", "nfs.example.test:/public", str(mount), str(self.source), str(manifest), "-", "60")
            self.assertEqual(printed.getvalue(), "")
            raise ValueError("probe-command-failed")
        with mock.patch.object(cache, "load_capture", return_value=capture):
            self.assertEqual(cache.profile_report(*self.arguments("upload")), 1)
        self.assertTrue(copied)
        row = json.loads((self.root / "receipt.json").read_bytes())
        self.assertEqual((row["result"], row["publication"], row["cleanup"]), ("fail", "unproven", "unproven"))
        self.assertFalse(row["pack_complete"])
        self.assertEqual(row["verified_bytes"], 0)

    def test_worker_rejects_old_target_manifest_and_capacity_shortfall(self):
        original, target = cache.profiles()
        path = self.root / "manifest"
        mount = self.root / "mount"
        mount.mkdir(mode=0o700)
        for kind in ("old-manifest", "capacity"):
            path.write_bytes((ROOT / (original if kind == "old-manifest" else target)["pack"] / "files.sha256").read_bytes())
            with self.subTest(kind=kind), mock.patch.object(cache.sys, "platform", "linux"), \
                    mock.patch.object(cache.sys, "flags", types.SimpleNamespace(isolated=True, no_site=True)), \
                    mock.patch.object(cache.sys, "dont_write_bytecode", True), mock.patch.object(cache.os, "getuid", return_value=0), \
                    mock.patch.object(cache, "mount_cache") as attach, \
                    mock.patch.object(cache.os, "fstatvfs", return_value=types.SimpleNamespace(f_bavail=561342161770, f_frsize=1)), \
                    mock.patch.object(cache, "atomic_copy") as copy:
                with self.assertRaises(ValueError):
                    cache.profile_worker("restore", "nfs.example.test:/public", str(mount), str(self.destination), str(path), "-", "60")
                copy.assert_not_called()
                if kind == "old-manifest": attach.assert_not_called()

    def test_cli_explicit_profiles_keep_original_restore_routing(self):
        flags = types.SimpleNamespace(**({name: getattr(cache.sys.flags, name) for name in dir(cache.sys.flags)
            if type(getattr(cache.sys.flags, name)) is int} | {"isolated": True, "no_site": True}))
        base = ["cache", "--export", "nfs.example.test:/public", "--mount", str(self.root / "mount"),
                "--output", str(self.root / "receipt"), "--producer", "b" * 40, "--timeout", "120",
                "--manifest", str(self.root / "manifest")]
        for profile in (None, "original", "uncensored"):
            for mode in ("--source", "--restore-to", "--seed-to"):
                if profile != "uncensored" and mode == "--source":
                    continue
                argv = base + [mode, str(self.destination)]
                if profile is not None: argv += ["--model-profile", profile]
                if mode == "--seed-to": argv += ["--seed-manifest", str(self.root / "old-manifest")]
                with self.subTest(profile=profile, mode=mode), mock.patch.object(cache.sys, "argv", argv), \
                        mock.patch.object(cache.sys, "platform", "linux"), \
                        mock.patch.object(cache.sys, "flags", flags), \
                        mock.patch.object(cache.os, "getuid", return_value=0), \
                        mock.patch.object(cache, "restore_report", return_value=0) as legacy, \
                        mock.patch.object(cache, "profile_report", return_value=0) as current:
                    if profile != "uncensored" and mode == "--seed-to":
                        with self.assertRaises(ValueError): cache.main()
                        legacy.assert_not_called()
                        current.assert_not_called()
                    else:
                        self.assertEqual(cache.main(), 0)
                        if profile == "uncensored":
                            legacy.assert_not_called()
                            self.assertEqual(current.call_args.args[0], {"--source": "upload", "--restore-to": "restore", "--seed-to": "seed"}[mode])
                        else:
                            current.assert_not_called()
                            legacy.assert_called_once()

    def test_documented_seed_uses_absolute_package_manifest_paths(self):
        text = (ROOT / "docs/tasks/p2-t4-public-cache-seed.md").read_text()
        self.assertIn('--manifest "$PACKAGE/models/deepseek-v4.1-flash-1017-uncensored-fp8/1/files.sha256"', text)
        self.assertIn('--seed-manifest "$PACKAGE/models/deepseek-v4.1-flash-1017/1/files.sha256"', text)
        self.assertIn("canonical absolute paths", text)


if __name__ == "__main__":
    unittest.main()
