# SPDX-License-Identifier: Apache-2.0
"""Small local synthetic files and fake mount/cgroup calls; no privileged IO."""
from contextlib import ExitStack, contextmanager, redirect_stdout
import hashlib
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
staging = types.ModuleType("original_staging")
staging.__file__ = str(ROOT / "host/stage-original-weight-view.py")
exec(compile(Path(staging.__file__).read_bytes(), staging.__file__, "exec"), staging.__dict__)


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.addCleanup(self.clean, self.temporary, self.root)
        space = mock.patch.object(os, "fstatvfs", return_value=types.SimpleNamespace(f_bavail=64 << 30, f_frsize=1))
        space.start()
        self.addCleanup(space.stop)
        self.inspection, self.cache, self.view = staging.helpers()
        self.source_mount, self.destination_mount = self.root / "source", self.root / "destination"
        for path in (self.source_mount, self.destination_mount): path.mkdir(mode=0o700)
        self.source = self.source_mount / "deepseek-v4.1-flash-1017"
        self.source.mkdir(mode=0o700)
        (self.source / "nested").mkdir(mode=0o700)
        self.rows = []
        for name, data in (("config.json", b"public config"), ("nested/weights.bin", b"public bytes")):
            target = self.source / name
            target.write_bytes(data)
            target.chmod(0o600)
            self.rows.append((hashlib.sha256(data).hexdigest(), len(data), name))
        self.attempt = "b" * 32
        self.wrapper = self.destination_mount / (".nib-original-view-" + self.attempt)
        self.model = self.wrapper / "model"
        self.exports = ("source.example.test:/public", "destination.example.test:/staging")
        self.profile, _ = self.view.selection(self.cache, "original")
        self.output, self.owner = self.root / "receipt.json", self.root / "ownership"
        self.intent = staging.staging_intent(self.profile, self.attempt, "a" * 40,
            hashlib.sha256(Path(staging.__file__).read_bytes()).hexdigest(), self.exports,
            (self.source_mount, self.destination_mount))
        self.intent_sha = hashlib.sha256(staging.record_bytes(self.intent)).hexdigest()
        self.destination_identity = {"wrapper_device": 22, "wrapper_inode": 23, "model_device": 22,
                                     "model_inode": 24, "inventory_sha256": "d" * 64}
        self.destination_identity["created_record_sha256"] = hashlib.sha256(staging.record_bytes(
            staging.created_record(self.intent_sha, {"wrapper_device": 22, "wrapper_inode": 23}))).hexdigest()

    def ack(self):
        return staging.acknowledgment(self.profile, self.attempt, self.intent_sha, self.destination_identity)

    def clean(self, temporary, root):
        for path, directories, _ in os.walk(root):
            os.chmod(path, 0o700)
            for name in directories:
                child = Path(path) / name
                if not child.is_symlink(): child.chmod(0o700)
        temporary.cleanup()

    def mount_id(self, fd):
        path = Path(os.readlink("/proc/self/fd/" + str(fd)))
        return 2 if path == self.destination_mount or path.is_relative_to(self.destination_mount) else 1

    def copy(self, deadline=None):
        with mock.patch.object(self.inspection, "mount_id", side_effect=self.mount_id):
            return staging.copy_view(self.inspection, self.cache, self.view, self.source_mount,
                self.destination_mount, self.rows, self.attempt, deadline or time.monotonic() + 10, ["copy"],
                self.record_created)

    def record_created(self, wrapper):
        self.owner.mkdir(mode=0o700, exist_ok=True)
        publication = self.cache.load_pinned("scripts/pilot/export.py", self.cache.PUBLICATION_SHA256)
        return staging.publish_record(self.cache, publication, self.owner, "destination-created.json",
                                      staging.created_record(self.intent_sha, wrapper))

    def test_pins_closed_original_profile_and_existing_helpers(self):
        self.assertEqual(self.profile["name"], "original")
        self.assertEqual(self.profile["total_bytes"], 510311045295)
        manifest = ROOT / self.profile["pack"] / "files.sha256"
        self.assertEqual(len(self.cache.manifest(manifest.read_bytes())), 83)
        self.assertEqual(self.inspection.resource_helper(self.cache).LIMITS,
            {"memory.max": "8589934592", "memory.swap.max": "0", "memory.oom.group": "1",
             "pids.max": "256", "cpu.max": "200000 100000"})
        with mock.patch.object(Path, "open", return_value=io.BytesIO(b"raise AssertionError('unverified')")):
            with self.assertRaises(ValueError): staging.helpers()
        with mock.patch.object(self.cache, "read_regular", return_value=b"raise AssertionError('unverified')"):
            with self.assertRaises(ValueError): self.cache.load_pinned("host/prepare-selected-weight-view.py", staging.VIEW_SHA256)

    def test_one_copy_preserves_source_old_view_and_private_parent(self):
        old = self.destination_mount / "old-view"
        old.mkdir(mode=0o700)
        (old / "private").write_bytes(b"old unchanged")
        original = self.cache.strict_inventory(self.source, self.rows)
        old_identity = self.cache.file_identity((old / "private").stat())
        with mock.patch.object(self.cache, "upload_copy", wraps=self.cache.upload_copy) as copy:
            result = self.copy()
        copy.assert_called_once()
        self.assertEqual((result["files"], result["verified_bytes"]), (2, 25))
        self.assertEqual(staging.destination_identity(result["destination"])["wrapper_inode"], self.wrapper.stat().st_ino)
        self.assertEqual(original, self.cache.strict_inventory(self.source, self.rows))
        self.assertEqual(old_identity, self.cache.file_identity((old / "private").stat()))
        self.assertEqual(stat.S_IMODE(self.wrapper.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.destination_mount.stat().st_mode), 0o700)
        for name in (".", "nested"):
            self.assertEqual(stat.S_IMODE((self.model / name).stat().st_mode), 0o555)
        for _, _, name in self.rows:
            self.assertEqual((self.source / name).read_bytes(), (self.model / name).read_bytes())
            self.assertNotEqual((self.source / name).stat().st_ino, (self.model / name).stat().st_ino)
            self.assertEqual(stat.S_IMODE((self.model / name).stat().st_mode), 0o444)
        self.assertEqual([p.name for p in self.wrapper.iterdir()], ["model"])

    def test_existing_wrapper_is_never_adopted_or_replaced(self):
        self.wrapper.mkdir(mode=0o700)
        (self.wrapper / "partial").write_bytes(b"preserve")
        with mock.patch.object(self.cache, "upload_copy") as copy, self.assertRaises(ValueError): self.copy()
        copy.assert_not_called()
        self.assertEqual((self.wrapper / "partial").read_bytes(), b"preserve")

    def test_parent_permissions_space_and_capabilities_fail_before_copy(self):
        for mode in ("permissions", "space", "capability", "modes"):
            with self.subTest(mode=mode), ExitStack() as stack:
                self.setUp()
                copy = stack.enter_context(mock.patch.object(self.cache, "upload_copy"))
                if mode == "permissions": self.destination_mount.chmod(0o777)
                if mode == "space":
                    stack.enter_context(mock.patch.object(os, "fstatvfs", return_value=types.SimpleNamespace(f_bavail=0, f_frsize=1)))
                if mode == "capability":
                    stack.enter_context(mock.patch.object(self.cache, "filesystem_probe", side_effect=OSError("private-capability")))
                if mode == "modes":
                    stack.enter_context(mock.patch.object(self.view, "view_modes", side_effect=PermissionError("private-mode")))
                with self.assertRaises((ValueError, OSError)): self.copy()
                copy.assert_not_called()
                self.assertFalse(self.model.exists())

    def test_same_mount_source_submount_and_deadline_fail_closed(self):
        for mode in ("same", "submount"):
            with self.subTest(mode=mode), mock.patch.object(self.inspection, "mount_id",
                    side_effect=[1, 1, 1] if mode == "same" else [3, 2, 1]):
                with self.assertRaises(ValueError):
                    staging.copy_view(self.inspection, self.cache, self.view, self.source_mount,
                        self.destination_mount, self.rows, self.attempt, time.monotonic() + 10, ["copy"], self.record_created)
        with self.assertRaises(ValueError): self.copy(time.monotonic() - 1)
        self.assertFalse(self.wrapper.exists())

    def test_invalid_source_never_creates_model_bytes(self):
        for mode in ("hash", "extra", "link", "hardlink", "fifo"):
            with self.subTest(mode=mode):
                self.setUp()
                target = self.source / "config.json"
                if mode == "hash": target.write_bytes(b"wrong content")
                if mode == "extra": (self.source / "private-file").write_bytes(b"not copied")
                if mode == "link":
                    target.unlink()
                    target.symlink_to(self.source / "nested/weights.bin")
                if mode == "hardlink": os.link(target, self.root / "linked")
                if mode == "fifo":
                    target.unlink()
                    os.mkfifo(target)
                with self.assertRaises((ValueError, OSError)): self.copy()
                self.assertFalse(self.model.exists())

    def test_copy_failure_keeps_private_partial_and_never_retries(self):
        def fail(source, model, rows, deadline):
            record = json.loads((self.owner / "destination-created.json").read_bytes())
            self.assertEqual(record["wrapper_inode"], self.wrapper.stat().st_ino)
            self.assertEqual(record["staging_intent_sha256"], self.intent_sha)
            model.mkdir(mode=0o700)
            (model / "partial").write_bytes(b"preserved")
            raise OSError("private-path")
        with mock.patch.object(self.cache, "upload_copy", side_effect=fail) as copy, self.assertRaises(OSError): self.copy()
        copy.assert_called_once()
        self.assertEqual((self.model / "partial").read_bytes(), b"preserved")
        self.assertEqual(stat.S_IMODE(self.wrapper.stat().st_mode), 0o700)

    def test_created_record_failure_stops_before_copy_and_preserves_unknown_wrapper(self):
        with mock.patch.object(self, "record_created", side_effect=OSError("local-record-failed")), \
                mock.patch.object(self.cache, "upload_copy") as copy, self.assertRaises(OSError):
            self.copy()
        copy.assert_not_called()
        self.assertTrue(self.wrapper.is_dir())
        self.assertFalse(self.model.exists())

    def test_post_mode_corruption_and_source_change_rejected(self):
        for mode in ("destination", "source"):
            with self.subTest(mode=mode):
                self.setUp()
                real = self.view.view_modes
                def change(cache, root, rows, deadline, **kwargs):
                    real(cache, root, rows, deadline, **kwargs)
                    if root == self.model and kwargs.get("apply"):
                        target = (self.model if mode == "destination" else self.source) / "config.json"
                        target.chmod(0o600)
                        target.write_bytes(b"changed bytes")
                        target.chmod(0o444)
                with mock.patch.object(self.view, "view_modes", side_effect=change), self.assertRaises(ValueError): self.copy()
                self.assertTrue(self.model.is_dir())

    def test_change_after_final_hash_is_not_mistaken_for_verified_inventory(self):
        real = self.inspection.hash_file
        def change(cache, root, row, deadline, mount):
            identity = real(cache, root, row, deadline, mount)
            if row == self.rows[-1]:
                target = self.model / "config.json"
                target.chmod(0o600)
                target.write_bytes(b"changed bytes")
                target.chmod(0o444)
            return identity
        with mock.patch.object(self.inspection, "hash_file", side_effect=change), self.assertRaises(ValueError): self.copy()
        self.assertTrue(self.model.is_dir())

    def private_fixture(self, stack):
        # Empty local mount targets; mounts and all commands remain fake.
        source_target = self.root / "source-target"
        source_target.mkdir(mode=0o700)
        mounts = (source_target, self.destination_mount)
        attached, calls = set(), []
        def unmounted(target):
            if target in attached: raise ValueError("mounted")
        def mount(export, target, **kwargs):
            calls.append(("mount", target, kwargs["readonly"]))
            attached.add(target)
        def unmount(export, target):
            calls.append(("unmount", target))
            attached.remove(target)
        stack.enter_context(mock.patch.object(self.cache, "require_unmounted", side_effect=unmounted))
        stack.enter_context(mock.patch.object(self.cache, "mount_cache", side_effect=mount))
        stack.enter_context(mock.patch.object(self.cache, "unmount_cache", side_effect=unmount))
        stack.enter_context(mock.patch.object(self.cache, "command", return_value=b'{"filesystems":[{"propagation":"private"}]}'))
        stack.enter_context(mock.patch.object(os, "readlink", return_value="mnt:[2]"))
        binding = stack.enter_context(mock.patch.object(staging, "mount_binding", side_effect=
            lambda inspection, cache, export, target, readonly: (1, 11) if readonly else (2, 22)))
        copy = stack.enter_context(mock.patch.object(staging, "copy_view"))
        copy.return_value = {"destination": self.destination_identity}
        return mounts, attached, calls, binding, copy

    def private(self, mounts):
        return staging.private_stage(self.inspection, self.cache, self.view, self.exports, mounts,
            self.rows, self.profile, self.attempt, time.monotonic() + 10, "mnt:[1]", self.intent_sha, self.record_created)

    def test_private_two_mounts_exact_cleanup_required_before_success(self):
        with ExitStack() as stack:
            mounts, attached, calls, _, copy = self.private_fixture(stack)
            self.assertEqual(self.private(mounts), self.ack())
            self.assertEqual(calls, [("mount", mounts[0], True), ("mount", mounts[1], False),
                                     ("unmount", mounts[1]), ("unmount", mounts[0])])
            self.assertFalse(attached)
            copy.assert_called_once()

    def test_cleanup_failure_preserves_primary_and_attempts_both_mounts(self):
        with ExitStack() as stack:
            mounts, _, _, _, copy = self.private_fixture(stack)
            copy.side_effect = PermissionError("private-copy-path")
            with mock.patch.object(self.cache, "unmount_cache", side_effect=OSError("private-cleanup")) as cleanup:
                result = self.private(mounts)
            self.assertEqual(cleanup.call_count, 2)
            self.assertEqual(result["error"], "permission-denied")
            self.assertEqual(result["cleanup_error"], "os-error")
            self.assertNotIn("private-copy-path", json.dumps(result))
            self.assertNotIn("private-cleanup", json.dumps(result))

    def test_cleanup_only_failure_and_alias_exports_never_pass(self):
        for mode in ("cleanup", "alias"):
            with self.subTest(mode=mode), ExitStack() as stack:
                self.setUp()
                mounts, _, _, binding, copy = self.private_fixture(stack)
                if mode == "cleanup": stack.enter_context(mock.patch.object(self.cache, "unmount_cache", side_effect=OSError()))
                else: binding.side_effect = None; binding.return_value = (1, 11)
                self.assertEqual(self.private(mounts)["schema"], staging.FAILURE_SCHEMA)
                if mode == "alias": copy.assert_not_called()

    def test_mount_failure_after_attach_still_attempts_cleanup(self):
        with ExitStack() as stack:
            mounts, attached, calls, _, copy = self.private_fixture(stack)
            real = self.cache.mount_cache.side_effect
            def mount(export, target, **kwargs):
                real(export, target, **kwargs)
                if target == mounts[1]: raise OSError("private-mount-error")
            self.cache.mount_cache.side_effect = mount
            result = self.private(mounts)
            self.assertEqual(result["stage"], "destination-mount")
            self.assertEqual(calls[-2:], [("unmount", mounts[1]), ("unmount", mounts[0])])
            self.assertFalse(attached)
            copy.assert_not_called()

    def test_replaced_mount_is_not_detached(self):
        with ExitStack() as stack:
            mounts, attached, calls, binding, _ = self.private_fixture(stack)
            binding.side_effect = [(1, 11), (2, 22), (1, 11), (9, 99), (9, 99), (1, 11)]
            result = self.private(mounts)
            self.assertEqual(result["schema"], staging.FAILURE_SCHEMA)
            self.assertIn(mounts[1], attached)
            self.assertNotIn(("unmount", mounts[1]), calls)
            self.assertIn(("unmount", mounts[0]), calls)

    def test_namespace_unchanged_or_nonprivate_mount_target_rejected(self):
        for mode in ("namespace", "propagation", "target"):
            with self.subTest(mode=mode), ExitStack() as stack:
                self.setUp()
                mounts, _, calls, _, copy = self.private_fixture(stack)
                if mode == "namespace": stack.enter_context(mock.patch.object(os, "readlink", return_value="mnt:[1]"))
                if mode == "propagation": self.cache.command.return_value = b'{"filesystems":[{"propagation":"shared"}]}'
                if mode == "target": mounts[0].chmod(0o755)
                self.assertEqual(self.private(mounts)["schema"], staging.FAILURE_SCHEMA)
                self.assertFalse(calls)
                copy.assert_not_called()

    def test_mount_readback_rejects_wrong_type_source_target_and_options(self):
        good = {"source": self.exports[1], "target": str(self.destination_mount), "fstype": "nfs4",
                "options": "rw,nodev,nosuid,noexec"}
        for change in ({}, {"source": self.exports[0]}, {"target": "/other"}, {"fstype": "tmpfs"},
                       {"options": "ro,nodev,nosuid,noexec"}, {"options": "rw,nodev,nosuid,noexec,exec"}):
            with self.subTest(change=change), mock.patch.object(self.cache, "verify_mount"), \
                    mock.patch.object(self.inspection, "mount_id", return_value=7), \
                    mock.patch.object(self.cache, "command", return_value=json.dumps({"filesystems": [{**good, **change}]}).encode()):
                if change:
                    with self.assertRaises(ValueError): staging.mount_binding(self.inspection, self.cache, self.exports[1], self.destination_mount, False)
                else:
                    self.assertEqual(staging.mount_binding(self.inspection, self.cache, self.exports[1], self.destination_mount, False),
                                     (7, self.destination_mount.stat().st_dev))

    def bounded_fixture(self, stack, mode=None):
        resource = self.inspection.resource_helper(self.cache)
        @contextmanager
        def cgroup(state):
            state["limits_read_back"] = True
            fd = os.open(self.root, os.O_RDONLY)
            try: yield fd, "nib-build-" + "a" * 32
            finally:
                os.close(fd)
                if mode == "cleanup": raise ValueError("private-cleanup")
                state.update(empty_verified=True, group_removed=True, oom_kill=int(mode == "oom"),
                             memory_peak_bytes=4096, populated_before_cleanup=0)
        stack.enter_context(mock.patch.object(resource, "cgroup", side_effect=cgroup))
        stack.enter_context(mock.patch.object(self.inspection, "resource_helper", return_value=resource))
        stack.enter_context(mock.patch.object(staging, "helpers", return_value=(self.inspection, self.cache, self.view)))
        stack.enter_context(mock.patch.object(self.inspection, "supervisor_identity", return_value={"pid": 47, "start_ticks": 123,
                              "pgid": 47, "boot_id": "12345678-1234-1234-1234-123456789abc"}))
        stack.enter_context(mock.patch.object(os, "readlink", return_value="mnt:[1]"))
        stack.enter_context(mock.patch.object(staging, "record_filesystem"))
        return resource

    def report(self, capture, **changes):
        args = dict(source_export=self.exports[0], destination_export=self.exports[1], source_mount=self.source_mount,
                    destination_mount=self.destination_mount, output=self.output, ownership_dir=self.owner,
                    attempt=self.attempt, producer="a" * 40, seconds=120, mode=staging.MODE)
        args.update(changes)
        with mock.patch.object(self.cache, "load_capture", return_value=capture):
            return staging.stage_report(**args)

    def test_owned_records_and_cgroup_join_precede_unshare_and_copy(self):
        with ExitStack() as stack:
            resource = self.bounded_fixture(stack)
            def capture(argv, **kwargs):
                self.assertEqual(argv[5], self.inspection.RESOURCE_BOOTSTRAP)
                self.assertEqual(json.loads(argv[9]), resource.LIMITS)
                self.assertEqual(argv[10:15], ["/usr/bin/unshare", "--mount", "--propagation", "private", "--"])
                self.assertIn(staging.BOOTSTRAP, argv)
                self.assertEqual(kwargs, {"timeout": 60, "limit": 4096})
                intent = json.loads(self.output.read_bytes())
                self.assertEqual((intent["result"], intent["publication"]), ("fail", "unproven"))
                for name in ("supervisor.json", "group.json"):
                    record = json.loads((self.owner / name).read_bytes())
                    self.assertEqual(record["attempt_id"], self.attempt)
                    self.assertEqual(record["producer"], "a" * 40)
                    self.assertEqual(record["script_sha256"], hashlib.sha256(Path(staging.__file__).read_bytes()).hexdigest())
                binding = (self.owner / "staging-intent.json").read_bytes()
                self.assertEqual(hashlib.sha256(binding).hexdigest(), self.intent_sha)
                self.assertEqual(json.loads(binding), self.intent)
                self.assertFalse((self.owner / "completion.json").exists())
                self.record_created({"wrapper_device": 22, "wrapper_inode": 23})
                return json.dumps(self.ack()).encode()
            self.assertEqual(self.report(capture), 0)
        result = json.loads(self.output.read_bytes())
        self.assertEqual(result["publication"], "exclusive-directory-copy")
        self.assertEqual(result["resources"]["limits"], resource.LIMITS)
        self.assertTrue(result["resources"]["group_removed"])
        self.assertFalse(result["host_visible_mount_created"])
        self.assertFalse(result["runtime_ready"])
        self.assertNotIn(str(self.root), self.output.read_text())
        self.assertNotIn(self.exports[0], self.output.read_text())
        completed = (self.owner / "completion.json").read_bytes()
        self.assertEqual(result["completion_sha256"], hashlib.sha256(completed).hexdigest())
        self.assertEqual(json.loads(completed)["staging_intent_sha256"], self.intent_sha)
        self.assertEqual(json.loads(completed)["worker_acknowledgment"]["destination"], self.destination_identity)

    def test_oom_cleanup_timeout_cancel_and_malformed_ack_preserve_unknown(self):
        for mode in ("oom", "cleanup", "timeout", "cancel", "shape", "profile", "count", "boolean", "attempt", "duplicate", "binding", "destination"):
            with self.subTest(mode=mode), ExitStack() as stack:
                self.setUp()
                self.bounded_fixture(stack, mode)
                ack = self.ack()
                if mode == "shape": ack["unexpected"] = True
                if mode == "profile": ack["model_profile"] = self.cache.profiles()[1]
                if mode == "count": ack["files"] = 83.0
                if mode == "boolean": ack["source_preserved"] = 1
                if mode == "attempt": ack["attempt_id"] = "c" * 32
                if mode == "binding": ack["staging_intent_sha256"] = "e" * 64
                if mode == "destination": ack["destination"]["model_inode"] = True
                raw = json.dumps(ack).encode()
                if mode == "duplicate": raw = raw[:-1] + b',"files":83}'
                def captured(*args, **kwargs):
                    self.record_created({"wrapper_device": 22, "wrapper_inode": 23})
                    return raw
                capture = mock.Mock(side_effect=captured)
                if mode == "timeout": capture.side_effect = ValueError("probe-timeout")
                if mode == "cancel": capture.side_effect = self.cache.Cancelled()
                self.assertEqual(self.report(capture), 1)
                result = json.loads(self.output.read_bytes())
                self.assertEqual((result["result"], result["publication"], result["cleanup"], result["verified_bytes"]),
                                 ("fail", "unproven", "unproven", 0))
                self.assertIsNone(result["source_preserved"])
                self.assertFalse(result["partial_removed"])
                self.assertFalse((self.owner / "completion.json").exists())

    def test_creation_record_missing_or_wrong_binding_never_completes(self):
        for mode in ("missing", "digest", "intent", "inode"):
            with self.subTest(mode=mode), ExitStack() as stack:
                self.setUp()
                self.bounded_fixture(stack)
                def captured(*args, **kwargs):
                    ack = self.ack()
                    if mode != "missing":
                        value = staging.created_record(self.intent_sha, {"wrapper_device": 22, "wrapper_inode": 23})
                        if mode == "intent": value["staging_intent_sha256"] = "e" * 64
                        if mode == "inode": value["wrapper_inode"] = 25
                        publication = self.cache.load_pinned("scripts/pilot/export.py", self.cache.PUBLICATION_SHA256)
                        digest = staging.publish_record(self.cache, publication, self.owner, "destination-created.json", value)
                        ack["destination"]["created_record_sha256"] = "f" * 64 if mode == "digest" else digest
                    return json.dumps(ack).encode()
                self.assertEqual(self.report(mock.Mock(side_effect=captured)), 1)
                result = json.loads(self.output.read_bytes())
                self.assertEqual((result["result"], result["publication"], result["cleanup"]),
                                 ("fail", "unproven", "unproven"))
                self.assertFalse((self.owner / "completion.json").exists())

    def test_immutable_local_records_bind_exports_targets_and_manifest(self):
        changed = staging.staging_intent(self.profile, self.attempt, "a" * 40,
            self.intent["script_sha256"], tuple(reversed(self.exports)), (self.source_mount, self.destination_mount))
        self.assertNotEqual(self.intent_sha, hashlib.sha256(staging.record_bytes(changed)).hexdigest())
        changed = staging.staging_intent(self.profile, self.attempt, "a" * 40,
            self.intent["script_sha256"], self.exports, (self.destination_mount, self.source_mount))
        self.assertNotEqual(self.intent_sha, hashlib.sha256(staging.record_bytes(changed)).hexdigest())
        self.assertEqual(self.intent["model_profile"]["manifest_sha256"], self.profile["manifest_sha256"])
        self.owner.mkdir(mode=0o700)
        publication = self.cache.load_pinned("scripts/pilot/export.py", self.cache.PUBLICATION_SHA256)
        self.assertEqual(staging.publish_record(self.cache, publication, self.owner, "staging-intent.json", self.intent), self.intent_sha)
        before = (self.owner / "staging-intent.json").read_bytes()
        with self.assertRaises(FileExistsError):
            staging.publish_record(self.cache, publication, self.owner, "staging-intent.json", changed)
        self.assertEqual((self.owner / "staging-intent.json").read_bytes(), before)

    def test_records_refuse_nfs_memory_or_unknown_filesystem(self):
        for kind in ("ext4", "xfs", "btrfs", "nfs", "nfs4", "tmpfs", "fuse", "overlay"):
            with self.subTest(kind=kind), mock.patch.object(self.cache, "command", return_value=json.dumps(
                    {"filesystems": [{"fstype": kind}]}).encode()):
                if kind in ("ext4", "xfs", "btrfs"): staging.record_filesystem(self.cache, self.root)
                else:
                    with self.assertRaises(ValueError): staging.record_filesystem(self.cache, self.root)

    def test_record_failure_prevents_launch_or_completion_and_never_retries(self):
        for name in ("staging-intent.json", "completion.json"):
            with self.subTest(name=name), ExitStack() as stack:
                self.setUp()
                self.bounded_fixture(stack)
                real = staging.publish_record
                def publish(cache, publication, directory, filename, value):
                    if filename == name: raise OSError("private-fsync-error")
                    return real(cache, publication, directory, filename, value)
                def captured(*args, **kwargs):
                    self.record_created({"wrapper_device": 22, "wrapper_inode": 23})
                    return json.dumps(self.ack()).encode()
                capture = mock.Mock(side_effect=captured)
                stack.enter_context(mock.patch.object(staging, "publish_record", side_effect=publish))
                self.assertEqual(self.report(capture), 1)
                self.assertEqual(capture.call_count, int(name == "completion.json"))
                self.assertFalse((self.owner / "completion.json").exists())
                self.assertEqual(json.loads(self.output.read_bytes())["publication"], "unproven")

    def test_known_worker_diagnostic_survives_later_resource_failure(self):
        with ExitStack() as stack:
            self.bounded_fixture(stack, "cleanup")
            ack = {"schema": staging.FAILURE_SCHEMA, "stage": "copy", "error": "permission-denied", "cleanup_error": "os-error"}
            self.assertEqual(self.report(mock.Mock(return_value=json.dumps(ack).encode())), 1)
        result = json.loads(self.output.read_bytes())
        self.assertEqual(result["worker_failure"], {k: ack[k] for k in ("stage", "error", "cleanup_error")})
        self.assertEqual(result["publication"], "unproven")

    def test_late_cancellation_clears_success_even_with_a_completion_record(self):
        transition = max(index for index, line in enumerate(Path(staging.__file__).read_text().splitlines(), 1)
                         if line.strip() == "for sig in handlers:")
        previous = sys.gettrace()
        delivered = []
        def trace(frame, event, _):
            if frame.f_code is staging.stage_report.__code__ and event == "line" and frame.f_lineno == transition:
                sys.settrace(None)
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                delivered.append(True)
            return trace
        with ExitStack() as stack:
            self.bounded_fixture(stack)
            def captured(*args, **kwargs):
                self.record_created({"wrapper_device": 22, "wrapper_inode": 23})
                return json.dumps(self.ack()).encode()
            try:
                sys.settrace(trace)
                self.assertEqual(self.report(mock.Mock(side_effect=captured)), 1)
            finally: sys.settrace(previous)
        self.assertEqual(delivered, [True])
        self.assertTrue((self.owner / "completion.json").exists())
        result = json.loads(self.output.read_bytes())
        self.assertEqual((result["result"], result["failure"], result["publication"]), ("fail", "cancelled", "unproven"))

    def test_worker_uses_pinned_original_manifest_and_cli_is_quiet(self):
        fake_sys = types.SimpleNamespace(**vars(sys))
        fake_sys.platform = "linux"
        fake_sys.flags = types.SimpleNamespace(isolated=True, no_site=True)
        fake_sys.dont_write_bytecode = True
        fake_os = types.SimpleNamespace(**vars(os))
        fake_os.getuid = lambda: 0
        self.owner.mkdir(mode=0o700)
        (self.owner / "staging-intent.json").write_bytes(staging.record_bytes(self.intent))
        with mock.patch.object(staging, "sys", fake_sys), mock.patch.object(staging, "os", fake_os), \
                mock.patch.object(staging, "helpers", return_value=(self.inspection, self.cache, self.view)), \
                mock.patch.object(staging, "record_filesystem"), \
                mock.patch.object(staging, "private_stage", return_value=self.ack()) as work, redirect_stdout(io.StringIO()) as output:
            staging.worker(*self.exports, str(self.source_mount), str(self.destination_mount), self.attempt,
                           "60", "mnt:[1]", self.intent_sha, str(self.owner))
            self.assertEqual(len(work.call_args.args[5]), 83)
            self.assertEqual(sum(row[1] for row in work.call_args.args[5]), self.profile["total_bytes"])
            self.assertEqual(json.loads(output.getvalue()), self.ack())
            for args in (["--mode", "auto"], ["--model-profile", "private-model"]):
                with mock.patch("sys.stderr", io.StringIO()) as error, self.assertRaises(ValueError):
                    staging.main(args)
                self.assertEqual(error.getvalue(), "")

    def test_worker_rejects_missing_or_mismatched_intent_before_mounts(self):
        for mode in ("missing", "digest", "attempt", "producer", "script", "profile", "exports", "targets"):
            with self.subTest(mode=mode), ExitStack() as stack:
                self.setUp()
                fake_sys = types.SimpleNamespace(**vars(sys))
                fake_sys.platform = "linux"
                fake_sys.flags = types.SimpleNamespace(isolated=True, no_site=True)
                fake_sys.dont_write_bytecode = True
                fake_os = types.SimpleNamespace(**vars(os))
                fake_os.getuid = lambda: 0
                self.owner.mkdir(mode=0o700)
                intent = dict(self.intent)
                if mode == "attempt": intent["attempt_id"] = "c" * 32
                if mode == "producer": intent["producer"] = "main"
                if mode == "script": intent["script_sha256"] = "e" * 64
                if mode == "profile": intent["model_profile"] = self.cache.profiles()[1]
                if mode == "exports": intent["destination_export_sha256"] = "e" * 64
                if mode == "targets": intent["mount_targets_sha256"] = "e" * 64
                raw = staging.record_bytes(intent)
                if mode != "missing": (self.owner / "staging-intent.json").write_bytes(raw)
                digest = "e" * 64 if mode == "digest" else hashlib.sha256(raw).hexdigest()
                stack.enter_context(mock.patch.object(staging, "sys", fake_sys))
                stack.enter_context(mock.patch.object(staging, "os", fake_os))
                stack.enter_context(mock.patch.object(staging, "helpers", return_value=(self.inspection, self.cache, self.view)))
                stack.enter_context(mock.patch.object(staging, "record_filesystem"))
                work = stack.enter_context(mock.patch.object(staging, "private_stage"))
                output = stack.enter_context(redirect_stdout(io.StringIO()))
                staging.worker(*self.exports, str(self.source_mount), str(self.destination_mount), self.attempt,
                               "60", "mnt:[1]", digest, str(self.owner))
                work.assert_not_called()
                result = json.loads(output.getvalue())
                self.assertEqual((result["schema"], result["stage"]), (staging.FAILURE_SCHEMA, "preflight"))
                self.assertIsNone(result["cleanup_error"])

    def test_argument_gates_and_owned_collision_do_not_capture(self):
        for changes in ({"mode": "auto"}, {"source_export": self.exports[1]}, {"attempt": "not-an-attempt"},
                        {"seconds": True}, {"seconds": 60}, {"seconds": 7201}, {"producer": "main"},
                        {"source_mount": Path("relative")}, {"destination_mount": self.source_mount / "inside"},
                        {"output": self.destination_mount / "receipt"}, {"ownership_dir": self.source_mount}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): self.report(mock.Mock(), **changes)
        with ExitStack() as stack:
            self.bounded_fixture(stack)
            self.owner.mkdir(mode=0o700)
            (self.owner / "foreign").write_bytes(b"preserved")
            capture = mock.Mock()
            self.assertEqual(self.report(capture), 1)
            capture.assert_not_called()
            self.assertEqual((self.owner / "foreign").read_bytes(), b"preserved")


if __name__ == "__main__":
    unittest.main()
