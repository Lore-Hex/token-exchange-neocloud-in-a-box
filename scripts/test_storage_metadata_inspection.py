# SPDX-License-Identifier: Apache-2.0
"""Fake storage observations only; no real mounts, process probes or cgroups."""
import contextlib
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
metadata = types.ModuleType("storage_metadata")
metadata.__file__ = str(ROOT / "host/inspect-storage-metadata.py")
exec(compile(Path(metadata.__file__).read_bytes(), metadata.__file__, "exec"), metadata.__dict__)


class MetadataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.root.chmod(0o700)
        self.mount = self.root / "mount"
        self.mount.mkdir(mode=0o700)
        self.output, self.ownership = self.root / "receipt.json", self.root / "ownership"
        self.export = "nfs.example.test:/retained"
        self.inspection = metadata.inspection_helper()
        self.cache = self.inspection.cache_helper()
        self.binding = {"mount_id": 17, **metadata.directory_identity(self.mount.stat())}
        self.space = types.SimpleNamespace(f_bavail=300, f_frsize=4096, f_favail=1000)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(metadata, "inspection_helper", return_value=self.inspection))
        self.stack.enter_context(mock.patch.object(self.inspection, "cache_helper", return_value=self.cache))
        self.stack.enter_context(mock.patch.object(self.inspection, "mount_id", return_value=17))
        self.stack.enter_context(mock.patch.object(self.inspection, "supervisor_identity", return_value={
            "pid": 11, "start_ticks": 22, "pgid": 11, "boot_id": "a" * 8 + "-" + "a" * 4 + "-"
            + "a" * 4 + "-" + "a" * 4 + "-" + "a" * 12}))
        self.stack.enter_context(mock.patch.object(metadata.os, "readlink", return_value="mnt:[2]"))
        self.stack.enter_context(mock.patch.object(metadata.os, "fstatvfs", return_value=self.space))
        self.command = self.stack.enter_context(mock.patch.object(self.cache, "command", side_effect=self.readback))
        self.mount_call = self.stack.enter_context(mock.patch.object(self.cache, "mount_cache"))
        self.unmount_call = self.stack.enter_context(mock.patch.object(self.cache, "unmount_cache"))
        self.unmounted = self.stack.enter_context(mock.patch.object(self.cache, "require_unmounted"))
        self.capture = self.stack.enter_context(mock.patch.object(self.cache, "load_capture", return_value=mock.Mock()))
        for obj, names in ((self.inspection, ("hash_file", "inspect_pack", "private_inspection")),
                           (self.cache, ("copy_pack", "upload_copy", "filesystem_probe", "strict_inventory"))):
            for name in names:
                self.stack.enter_context(mock.patch.object(obj, name, side_effect=AssertionError("forbidden operation")))

    def row(self, **changes):
        return {"source": self.export, "target": str(self.mount), "fstype": "nfs4",
                "options": "ro,nodev,nosuid,noexec", **changes}

    def readback(self, argv):
        if argv[-1] == "PROPAGATION":
            return b'{"filesystems":[{"propagation":"private"}]}'
        self.assertEqual(argv, ["findmnt", "--json", "--id", "17", "--output", "SOURCE,TARGET,FSTYPE,OPTIONS"])
        return json.dumps({"filesystems": [self.row()]}).encode()

    def observe(self):
        return metadata.observe(self.inspection, self.cache, self.export, self.mount,
                                self.binding, time.monotonic() + 10)

    def private(self, state=None):
        state = {} if state is None else state
        return metadata.private_observation(self.inspection, self.cache, self.export, self.mount,
                                            time.monotonic() + 10, "mnt:[1]", state)

    def ack(self):
        return {"schema": metadata.WORKER_SCHEMA, "result": "pass", "error": None,
                "observation": self.observe(), "private_namespace_verified": True,
                "private_mount_attempted": True, "private_mount_cleanup_confirmed": True}

    def report(self, capture=None, **changes):
        args = {"export": self.export, "mount": self.mount, "output": self.output,
                "ownership_dir": self.ownership, "attempt_id": "b" * 32, "producer": "c" * 40, **changes}
        raw = json.dumps(self.ack()).encode()
        def bounded(cache, call, argv, budget, state, ownership):
            state.update(limits_read_back=True, empty_verified=True, group_removed=True, oom_kill=0)
            if capture is not None:
                return capture(cache, call, argv, budget, state, ownership)
            return raw
        with mock.patch.object(self.inspection, "bounded_capture", side_effect=bounded):
            return metadata.inspect_report(**args)

    def test_helper_is_exact_pinned_unchanged_inspector(self):
        self.assertEqual(hashlib.sha256((ROOT / "host/inspect-public-weight-cache.py").read_bytes()).hexdigest(),
                         metadata.INSPECTION_SHA256)
        source = Path(metadata.__file__).read_bytes()
        loader = types.ModuleType("fresh_metadata")
        loader.__file__ = metadata.__file__
        exec(compile(source, metadata.__file__, "exec"), loader.__dict__)
        with mock.patch.object(Path, "open", return_value=io.BytesIO(b"foreign")), self.assertRaises(ValueError):
            loader.inspection_helper()

    def test_only_root_metadata_is_read_even_when_pack_contents_exist(self):
        (self.mount / "weights").write_bytes(b"never read or hashed")
        result = self.observe()
        self.assertEqual(result["capacity"], {"available_bytes": 1228800, "available_inodes": 1000})
        self.assertEqual(result["directory"], {key: self.binding[key] for key in
                                             ("device", "inode", "uid", "gid", "mode")})
        self.mount_call.assert_not_called()
        self.unmount_call.assert_not_called()

    def test_exact_private_mount_and_normal_cleanup(self):
        state = {}
        self.assertEqual(self.private(state), self.observe())
        self.assertEqual(state, {"private_namespace_verified": True, "private_mount_attempted": True,
                                 "private_mount_cleanup_confirmed": True})
        self.mount_call.assert_called_once_with(self.export, self.mount, readonly=True)
        self.unmount_call.assert_called_once_with(self.export, self.mount)
        self.assertEqual(self.unmounted.call_count, 2)

    def test_same_namespace_nonprivate_propagation_and_nonempty_target_rejected(self):
        with mock.patch.object(metadata.os, "readlink", return_value="mnt:[1]"), self.assertRaises(ValueError):
            self.private()
        with mock.patch.object(self.cache, "command", return_value=b'{"filesystems":[{"propagation":"shared"}]}'), \
                self.assertRaises(ValueError):
            self.private()
        (self.mount / "foreign").write_bytes(b"preserve")
        with self.assertRaises(ValueError):
            self.private()
        self.mount_call.assert_not_called()
        self.unmount_call.assert_not_called()

    def test_existing_mount_rejected_before_mount(self):
        with mock.patch.object(self.cache, "require_unmounted", side_effect=ValueError("foreign")), \
                self.assertRaises(ValueError):
            self.private()
        self.mount_call.assert_not_called()
        self.unmount_call.assert_not_called()

    def test_foreign_readwrite_and_oversized_mount_readback_rejected(self):
        for changes in ({"source": "nfs.example.test:/foreign"}, {"target": "/other"}, {"fstype": "ext4"},
                        {"options": "rw,nodev,nosuid,noexec"}, {"options": "ro,nodev,nosuid,noexec,exec"},
                        {"options": "ro,nodev,nosuid,noexec,ro"}, {"options": "x" * 1025}):
            with self.subTest(changes=changes), mock.patch.object(self.cache, "command", return_value=json.dumps(
                    {"filesystems": [self.row(**changes)]}).encode()), self.assertRaises(ValueError):
                self.observe()
        for raw in (b"x" * 8193, b'{"filesystems":[],"filesystems":[]}'):
            with mock.patch.object(self.cache, "command", return_value=raw), self.assertRaises(ValueError):
                self.observe()

    def test_changed_descriptor_or_path_identity_rejected(self):
        with mock.patch.object(self.inspection, "mount_id", return_value=18), self.assertRaises(ValueError):
            self.observe()
        original = metadata.directory_identity(self.mount.stat())
        with mock.patch.object(metadata, "directory_identity", side_effect=[original, {**original, "inode": 999}]), \
                self.assertRaises(ValueError):
            self.observe()
        with mock.patch.object(self.inspection, "mount_id", side_effect=[17, 17, 18]), self.assertRaises(ValueError):
            self.observe()

    def test_invalid_or_overflowed_capacity_is_not_inferred(self):
        for field, value in (("f_bavail", -1), ("f_bavail", True), ("f_bavail", 1 << 62),
                             ("f_frsize", 0), ("f_favail", -1), ("f_favail", 1.0)):
            with self.subTest(field=field, value=value), mock.patch.object(self.space, field, value), \
                    self.assertRaises(ValueError):
                self.observe()

    def test_observation_failure_still_cleans_only_bound_mount(self):
        state = {}
        with mock.patch.object(metadata, "observe", side_effect=OSError("private-canary")), self.assertRaises(OSError):
            self.private(state)
        self.assertTrue(state["private_mount_cleanup_confirmed"])
        self.unmount_call.assert_called_once_with(self.export, self.mount)

    def test_failed_mount_or_replaced_mount_is_not_adopted_for_cleanup(self):
        with mock.patch.object(self.cache, "mount_cache", side_effect=OSError("uncertain")), self.assertRaises(OSError):
            self.private()
        self.unmount_call.assert_not_called()
        with mock.patch.object(metadata, "observe", return_value={}), \
                mock.patch.object(self.inspection, "mount_id", side_effect=[17, 18]), self.assertRaises(ValueError):
            self.private()
        self.unmount_call.assert_not_called()

    def test_cleanup_failure_never_confirms_absence(self):
        state = {}
        with mock.patch.object(self.cache, "unmount_cache", side_effect=OSError("busy")), self.assertRaises(OSError):
            self.private(state)
        self.assertNotIn("private_mount_cleanup_confirmed", state)

    def test_durable_receipt_and_ownership_precede_bounded_worker(self):
        def capture(cache, call, argv, budget, state, ownership):
            receipt = json.loads(self.output.read_bytes())
            record = json.loads((self.ownership / "supervisor.json").read_bytes())
            self.assertEqual(receipt["result"], "fail")
            self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
            self.assertEqual(record["script_sha256"], receipt["script_sha256"])
            self.assertEqual(record["attempt_id"], "b" * 32)
            self.assertEqual(argv[:6], ["/usr/bin/unshare", "--mount", "--propagation", "private", "--", sys.executable])
            self.assertEqual(argv[6:11], ["-I", "-S", "-B", "-c", metadata.BOOTSTRAP])
            self.assertEqual(argv[-3:], [self.export, str(self.mount), "mnt:[2]"])
            self.assertEqual(budget, 120)
            return json.dumps(self.ack()).encode()
        self.assertEqual(self.report(capture), 0)
        result = json.loads(self.output.read_bytes())
        self.assertEqual(result["cleanup"], "confirmed")
        for key in ("quota", "backing", "all_client_aliases"):
            self.assertEqual(result[key], "unknown")
        for key in ("admission_authorized", "pack_verified", "runtime_ready", "confidential",
                    "host_mount_mutation_requested", "process_group_absence_verified"):
            self.assertIs(result[key], False)

    def test_output_and_ownership_collisions_do_not_launch_or_overwrite(self):
        self.output.write_bytes(b"retained")
        capture = mock.Mock()
        with self.assertRaises(FileExistsError):
            self.report(capture)
        self.assertEqual(self.output.read_bytes(), b"retained")
        self.output.unlink()
        self.ownership.mkdir(mode=0o700)
        self.assertEqual(self.report(capture), 1)
        capture.assert_not_called()

    def test_failed_receipt_fsync_prevents_ownership_and_capture(self):
        capture = mock.Mock()
        with mock.patch.object(metadata.os, "fsync", side_effect=[None, OSError("full")]), self.assertRaises(OSError):
            self.report(capture)
        capture.assert_not_called()
        self.assertFalse(self.ownership.exists())

    def test_expiry_before_mount_or_after_observation_never_passes(self):
        with self.assertRaises(ValueError):
            metadata.private_observation(self.inspection, self.cache, self.export, self.mount,
                                         time.monotonic() - 1, "mnt:[1]", {})
        self.mount_call.assert_not_called()
        raw = json.dumps(self.ack()).encode()
        with mock.patch.object(metadata.time, "monotonic", return_value=0) as clock:
            def late(*_):
                clock.return_value = 121
                return raw
            self.assertEqual(self.report(late), 1)
        result = json.loads(self.output.read_bytes())
        self.assertEqual(result["result"], "fail")
        self.assertFalse(result["private_mount_cleanup_confirmed"])

    def test_signal_during_capture_fails_and_restores_handlers(self):
        previous = signal.getsignal(signal.SIGTERM)
        def capture(*_):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        self.assertEqual(self.report(capture), 1)
        result = json.loads(self.output.read_bytes())
        self.assertEqual((result["result"], result["failure"]), ("fail", "cancelled"))
        self.assertFalse(result["worker_exit_confirmed"])
        self.assertIs(signal.getsignal(signal.SIGTERM), previous)

    def test_timeout_and_cancellation_remain_failed_and_private(self):
        for error in (ValueError("probe-timeout"), InterruptedError("private-canary"), OSError("private-canary")):
            with self.subTest(error=type(error).__name__):
                with tempfile.TemporaryDirectory(dir=self.root) as directory:
                    root = Path(directory)
                    capture = mock.Mock(side_effect=error)
                    self.assertEqual(self.report(capture, output=root / "receipt", ownership_dir=root / "owner"), 1)
                    raw = (root / "receipt").read_text()
                    result = json.loads(raw)
                    self.assertEqual(result["result"], "fail")
                    self.assertFalse(result["worker_exit_confirmed"])
                    self.assertFalse(result["private_mount_cleanup_confirmed"])
                    self.assertFalse(result["process_group_absence_verified"])
                    self.assertNotIn("private-canary", raw)

    def test_worker_failure_ack_is_not_success(self):
        value = {**self.ack(), "result": "fail", "error": "permission-denied", "observation": None,
                 "private_mount_cleanup_confirmed": False}
        self.assertEqual(self.report(lambda *_: json.dumps(value).encode()), 1)
        result = json.loads(self.output.read_bytes())
        self.assertTrue(result["worker_exit_confirmed"])
        self.assertEqual(result["cleanup"], "unproven")

    def test_inconsistent_failure_cleanup_flags_rejected(self):
        value = {**self.ack(), "result": "fail", "error": "rejected", "observation": None,
                 "private_namespace_verified": False}
        with self.assertRaises(ValueError):
            metadata.acknowledgement(json.dumps(value).encode(), self.cache, self.export, self.mount)

    def test_worker_errors_do_not_emit_paths_or_exception_contents(self):
        fake_sys = types.SimpleNamespace(platform="linux", flags=types.SimpleNamespace(isolated=1, no_site=1),
                                         dont_write_bytecode=True)
        output = io.StringIO()
        with mock.patch.object(metadata, "sys", fake_sys), mock.patch.object(metadata.os, "getuid", return_value=0), \
                mock.patch.object(metadata, "private_observation", side_effect=OSError("private-canary")), \
                contextlib.redirect_stdout(output):
            metadata.worker(self.export, str(self.mount), "mnt:[1]")
        raw = output.getvalue()
        self.assertEqual(json.loads(raw)["error"], "os-error")
        for forbidden in ("private-canary", str(self.mount), self.export):
            self.assertNotIn(forbidden, raw)

    def test_ack_rejects_unknown_fields_false_cleanup_and_numeric_substitution(self):
        for mutate in (lambda v: v.update(extra=True), lambda v: v.update(private_mount_cleanup_confirmed=False),
                       lambda v: v["observation"]["capacity"].update(available_bytes=True),
                       lambda v: v["observation"]["directory"].update(inode=1.0),
                       lambda v: v["observation"]["mount"].update(source="nfs.example.test:/foreign")):
            value = self.ack()
            mutate(value)
            with self.assertRaises(ValueError):
                metadata.acknowledgement(json.dumps(value).encode(), self.cache, self.export, self.mount)

    def test_invalid_paths_attempts_and_producer_reject_before_capture(self):
        capture = mock.Mock()
        for changes in ({"mount": Path("relative")}, {"mount": Path("/")},
                        {"output": self.mount / "receipt"}, {"attempt_id": "old"},
                        {"producer": "main"}, {"export": "nfs.example.test:/../other"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.report(capture, **changes)
        capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
