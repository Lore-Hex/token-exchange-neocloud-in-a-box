# SPDX-License-Identifier: Apache-2.0
"""Synthetic public files only; no weights, network, inference or privileged IO."""
import hashlib
from contextlib import ExitStack, redirect_stdout
import io
import json
import os
from pathlib import Path
import signal
import stat
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
view = types.ModuleType("selected_weight_view")
view.__file__ = str(ROOT / "host/prepare-selected-weight-view.py")
exec(compile(Path(view.__file__).read_bytes(), view.__file__, "exec"), view.__dict__)
cache = view.cache_helper()


class WeightViewTests(unittest.TestCase):
    def test_original_parent_failure_receipt_stays_failed(self):
        raw = (ROOT / "evidence/p2/2026-10-24-selected-weight-view-parent-failure/receipt.json").read_bytes()
        report = json.loads(raw)
        self.assertEqual(report["producer"], "9c1fb3c62318ad83087b2ab08f8ff7c4e7ae79d9")
        self.assertEqual(report["result"], "fail")
        self.assertEqual(report["publication"], "unproven")
        self.assertIsNone(report["source_preserved"])
        self.assertFalse(report["worker_exit_confirmed"])
        self.assertFalse(report["confidential"])
        self.assertFalse(report["runtime_ready"])

    def test_shared_parent_requires_exact_root_sticky_and_private_child(self):
        for change in (None, "device-write", "device-owner", "shared-mode", "shared-owner", "child-mode", "child-owner", "child-link"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                root = Path(temporary).resolve()
                device, shared, private = root / "dev", root / "dev/shm", root / "dev/shm/owned"
                device.mkdir(mode=0o755)
                shared.mkdir(mode=0o1777)
                shared.chmod(0o1777)
                private.mkdir(mode=0o700)
                stack.enter_context(mock.patch.object(view, "SHARED_MEMORY", shared))
                real_stat = os.fstat
                device_inode, shared_inode, private_inode = device.stat().st_ino, shared.stat().st_ino, private.stat().st_ino
                def observed(fd):
                    info = real_stat(fd)
                    uid, mode = info.st_uid, info.st_mode
                    if info.st_ino in (device_inode, shared_inode): uid = 0
                    if info.st_ino == device_inode:
                        if change == "device-write": mode |= 0o020
                        if change == "device-owner": uid = 98765
                    if info.st_ino == shared_inode:
                        if change == "shared-mode": mode &= ~stat.S_ISVTX
                        if change == "shared-owner": uid = 98765
                    if info.st_ino == private_inode:
                        if change == "child-mode": mode |= 0o040
                        if change == "child-owner": uid = 98765
                    return types.SimpleNamespace(st_uid=uid, st_mode=mode)
                stack.enter_context(mock.patch.object(view.os, "fstat", side_effect=observed))
                if change == "child-link":
                    private.rmdir()
                    private.symlink_to(root, target_is_directory=True)
                if change is None:
                    with view.private_view_parent(cache, private) as fd:
                        self.assertEqual(real_stat(fd).st_ino, private_inode)
                    with self.assertRaises(OSError): os.fstat(fd)
                else:
                    with self.assertRaises((ValueError, OSError)):
                        with view.private_view_parent(cache, private):
                            self.fail("unsafe shared parent accepted")

    @unittest.skipUnless(sys.platform == "linux" and Path("/dev/shm").is_dir(), "Linux shared-memory filesystem")
    def test_real_linux_private_shared_memory_child(self):
        with tempfile.TemporaryDirectory(prefix="nib-view-policy-", dir="/dev/shm") as directory:
            path = Path(directory)
            # The original generic cache policy rejects this ancestor. Only the
            # selected view has the explicit root-sticky RAM-disk contract.
            with self.assertRaises(ValueError):
                with cache.directory(path, private=True):
                    self.fail("generic policy unexpectedly changed")
            with view.private_view_parent(cache, path) as fd:
                self.assertEqual(os.fstat(fd).st_ino, path.stat().st_ino)
            path.chmod(0o755)
            with self.assertRaises(ValueError):
                with view.private_view_parent(cache, path):
                    self.fail("nonprivate RAM directory accepted")

    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        def clean():
            for parent, dirs, _ in os.walk(temporary.name):
                os.chmod(parent, 0o700)
                for name in dirs:
                    path = Path(parent) / name
                    if not path.is_symlink():
                        os.chmod(path, 0o700)
            temporary.cleanup()
        self.addCleanup(clean)
        root = Path(temporary.name).resolve()
        source = root / "source"
        source.mkdir(mode=0o700)
        (source / "nested").mkdir(mode=0o700)
        rows = []
        for name, data in (("config.json", b"public config"), ("nested/model.bin", b"public model bytes")):
            (source / name).write_bytes(data)
            (source / name).chmod(0o600)
            rows.append((hashlib.sha256(data).hexdigest(), len(data), name))
        return source, root / "view", rows

    def prepare(self, source, destination, rows, deadline=None):
        return view.prepare_view(cache, source, destination, rows, deadline or time.monotonic() + 10)

    def test_verified_new_view_does_not_modify_source_or_parent(self):
        source, destination, rows = self.fixture()
        original = cache.strict_inventory(source, rows)
        result = self.prepare(source, destination, rows)
        self.assertEqual(result, {"files": 2, "verified_bytes": 31, "file_mode": "0444", "directory_mode": "0555",
                                  "relative_path": "model"})
        self.assertEqual(original, cache.strict_inventory(source, rows))
        self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(destination.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
        destination = destination / "model"
        for name in (".", "nested"):
            self.assertEqual(stat.S_IMODE((destination / name).stat().st_mode), 0o555)
        for _, _, name in rows:
            self.assertEqual((source / name).read_bytes(), (destination / name).read_bytes())
            self.assertEqual(stat.S_IMODE((destination / name).stat().st_mode), 0o444)
            self.assertNotEqual((source / name).stat().st_ino, (destination / name).stat().st_ino)

    def test_source_corruption_extra_secret_links_fifo_reject(self):
        for kind in ("content", "extra", "symlink", "hardlink", "fifo", "nested-destination"):
            with self.subTest(kind=kind):
                source, destination, rows = self.fixture()
                target = source / "config.json"
                if kind == "content": target.write_bytes(b"private wrong")
                if kind == "extra": (source / "api-key").write_bytes(b"never copied")
                if kind == "symlink":
                    target.unlink()
                    target.symlink_to(source / "nested/model.bin")
                if kind == "hardlink": os.link(target, source.parent / "linked")
                if kind == "fifo":
                    target.unlink()
                    os.mkfifo(target)
                if kind == "nested-destination": destination = source / "view"
                with self.assertRaises((ValueError, OSError)):
                    self.prepare(source, destination, rows)
                self.assertFalse(destination.exists())

    def test_destination_parent_must_be_private_and_not_symlink(self):
        for kind in ("mode", "link"):
            source, destination, rows = self.fixture()
            if kind == "mode": destination.parent.chmod(0o755)
            else:
                alias = destination.parent / "alias"
                alias.symlink_to(destination.parent, target_is_directory=True)
                destination = alias / "view"
            with self.subTest(kind=kind), self.assertRaises((ValueError, OSError)):
                self.prepare(source, destination, rows)
            self.assertFalse(destination.exists())

    def test_occupied_destination_is_never_replaced(self):
        source, destination, rows = self.fixture()
        destination.mkdir()
        marker = destination / "owned"
        marker.write_bytes(b"preserve")
        with self.assertRaises(ValueError): self.prepare(source, destination, rows)
        self.assertEqual(marker.read_bytes(), b"preserve")

    def test_destination_created_during_copy_is_not_overwritten(self):
        source, destination, rows = self.fixture()
        original = cache.copy_pack
        def race(*args, **kwargs):
            result = original(*args, **kwargs)
            destination.mkdir()
            (destination / "other-owner").write_bytes(b"preserve")
            return result
        with mock.patch.object(cache, "copy_pack", side_effect=race), self.assertRaises(FileExistsError):
            self.prepare(source, destination, rows)
        self.assertEqual((destination / "other-owner").read_bytes(), b"preserve")

    def test_post_mode_corruption_is_rehashed_before_publication(self):
        source, destination, rows = self.fixture()
        original = view.view_modes
        def corrupt(*args, **kwargs):
            result = original(*args, **kwargs)
            if kwargs.get("apply"):
                target = args[1] / "config.json"
                target.chmod(0o600)
                target.write_bytes(b"wrong bytes!!")
                target.chmod(0o444)
            return result
        with mock.patch.object(view, "view_modes", side_effect=corrupt), self.assertRaises(ValueError):
            self.prepare(source, destination, rows)
        self.assertFalse(destination.exists())

    def test_deadline_space_and_publication_failure_stay_closed(self):
        for kind in ("deadline", "space", "rename"):
            source, destination, rows = self.fixture()
            with self.subTest(kind=kind):
                if kind == "deadline":
                    with self.assertRaises(ValueError): self.prepare(source, destination, rows, time.monotonic() - 1)
                elif kind == "space":
                    with mock.patch.object(cache.os, "fstatvfs", return_value=types.SimpleNamespace(f_bavail=0, f_frsize=4096)), \
                            self.assertRaises(ValueError): self.prepare(source, destination, rows)
                else:
                    publication = cache.load_pinned("scripts/pilot/export.py", cache.PUBLICATION_SHA256)
                    with mock.patch.object(cache, "load_pinned", return_value=publication), \
                            mock.patch.object(publication, "rename_exclusive", side_effect=OSError("unsupported")), \
                            self.assertRaises(ValueError): self.prepare(source, destination, rows)
                self.assertFalse(destination.exists())

    def test_post_copy_source_change_or_destination_corruption_cannot_publish(self):
        for kind in ("source", "destination"):
            source, destination, rows = self.fixture()
            original = cache.copy_pack
            def changed(*args, **kwargs):
                result = original(*args, **kwargs)
                target = source if kind == "source" else args[1]
                (target / "config.json").write_bytes(b"wrong bytes!!")
                return result
            with self.subTest(kind=kind), mock.patch.object(cache, "copy_pack", side_effect=changed), self.assertRaises(ValueError):
                self.prepare(source, destination, rows)
            self.assertFalse(destination.exists())

    def test_chmod_never_touches_original_even_on_failure(self):
        source, destination, rows = self.fixture()
        initial = cache.strict_inventory(source, rows)
        with mock.patch.object(view.os, "fchmod", side_effect=PermissionError), self.assertRaises(PermissionError):
            self.prepare(source, destination, rows)
        self.assertEqual(cache.strict_inventory(source, rows), initial)
        self.assertFalse(destination.exists())

    def test_pinned_profile_has_exact_79_files_and_bytes(self):
        _, profile = cache.profiles()
        rows = cache.manifest_rows(cache.read_regular(ROOT / profile["pack"] / "files.sha256"),
                                   profile["manifest_sha256"], profile["total_bytes"], 79)
        self.assertEqual(len(rows), 79)
        self.assertEqual(sum(row[1] for row in rows), 510311056155)

    def test_original_selection_is_explicit_pinned_and_not_a_selected_receipt(self):
        original, count = view.selection(cache, "original")
        self.assertEqual(original, cache.profiles()[0])
        rows = cache.manifest_rows(cache.read_regular(ROOT / original["pack"] / "files.sha256"),
                                  original["manifest_sha256"], original["total_bytes"], count)
        self.assertEqual((len(rows), sum(row[1] for row in rows)), (83, 510311045295))
        selected, count = view.selection(cache)
        self.assertEqual((selected, count), (cache.profiles()[1], 79))
        self.assertEqual(view.identity_fields(selected), {"schema": "nib.selected-weight-view/v1"})
        self.assertEqual(view.identity_fields(original),
                         {"schema": "nib.selected-weight-view/v2", "model_profile": original})
        for value in (None, True, "deepseek", "original/other"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                view.selection(cache, value)

    def test_worker_metadata_and_parent_failure_preserves_signals(self):
        for model_profile, outcome in ((name, result) for name in ("uncensored", "original")
                                       for result in ("pass", "interrupt", "other-checkpoint")):
            success = outcome == "pass"
            source, destination, _ = self.fixture()
            output = source.parent / "report.json"
            profile, count = view.selection(cache, model_profile)
            returned, returned_count = view.selection(cache,
                ("uncensored" if model_profile == "original" else "original")
                if outcome == "other-checkpoint" else model_profile)
            packet = json.dumps({**view.identity_fields(returned, worker=True), "files": returned_count,
                "verified_bytes": returned["total_bytes"], "file_mode": "0444", "directory_mode": "0555",
                "relative_path": "model"}).encode()
            capture = mock.Mock(side_effect=InterruptedError) if outcome == "interrupt" else mock.Mock(return_value=packet)
            old = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
            flags = types.SimpleNamespace(**({name: getattr(view.sys.flags, name) for name in dir(view.sys.flags)
                if type(getattr(view.sys.flags, name)) is int} | {"isolated": True, "no_site": True}))
            with mock.patch.object(view, "cache_helper", return_value=cache), \
                    mock.patch.object(cache, "load_capture", return_value=capture), \
                    mock.patch.object(view.sys, "platform", "linux"), \
                    mock.patch.object(view.sys, "flags", flags), \
                    mock.patch.object(view.sys, "dont_write_bytecode", True), \
                    mock.patch.object(cache, "directory", wraps=cache.directory) as directory:
                # Keep the real OS ownership check for the non-root test user.
                uid = source.stat().st_uid
                with mock.patch.object(view.os, "getuid", side_effect=[0] + [uid] * 50):
                    code = view.main(["--source", str(source), "--destination", str(destination),
                        "--output", str(output), "--producer", "a" * 40, "--timeout", "120"]
                        + (["--model-profile", "original"] if model_profile == "original" else []))
                self.assertTrue(directory.called)
            result = json.loads(output.read_bytes())
            self.assertEqual(code, 0 if success else 1)
            self.assertEqual(result["result"], "pass" if success else "fail")
            self.assertIs(result["source_preserved"], True if success else None)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertNotIn(str(source), output.read_text())
            self.assertEqual(old, {number: signal.getsignal(number) for number in old})
            self.assertEqual(capture.call_args.kwargs, {"timeout": 60, "limit": 4096})
            self.assertEqual(capture.call_args.args[0][-1], "original" if model_profile == "original" else "60")
            self.assertEqual({key: result[key] for key in view.identity_fields(profile)},
                             view.identity_fields(profile))

    def test_official_worker_passes_all_manifest_rows_to_unchanged_copy(self):
        profile, _ = view.selection(cache, "original")
        result = {"files": 83, "verified_bytes": profile["total_bytes"],
                  "file_mode": "0444", "directory_mode": "0555", "relative_path": "model"}
        flags = types.SimpleNamespace(isolated=True, no_site=True)
        output = io.StringIO()
        with mock.patch.object(view, "cache_helper", return_value=cache), \
                mock.patch.object(view.sys, "platform", "linux"), \
                mock.patch.object(view.sys, "flags", flags), \
                mock.patch.object(view.sys, "dont_write_bytecode", True), \
                mock.patch.object(view.os, "getuid", return_value=0), \
                mock.patch.object(view, "prepare_view", return_value=result) as prepare, redirect_stdout(output):
            view.worker("/synthetic/source", "/synthetic/destination", "60", "original")
        self.assertEqual(prepare.call_args.args[1:3], (Path("/synthetic/source"), Path("/synthetic/destination")))
        self.assertEqual(len(prepare.call_args.args[3]), 83)
        self.assertEqual(sum(row[1] for row in prepare.call_args.args[3]), profile["total_bytes"])
        self.assertEqual(json.loads(output.getvalue()), {**view.identity_fields(profile, worker=True), **result})


if __name__ == "__main__":
    unittest.main()
