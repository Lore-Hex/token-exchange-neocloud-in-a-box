# SPDX-License-Identifier: Apache-2.0
"""Synthetic cache inspection only; no mount, network or model downloads."""
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
inspection = types.ModuleType("cache_inspection")
inspection.__file__ = str(ROOT / "host/inspect-public-weight-cache.py")
exec(compile(Path(inspection.__file__).read_bytes(), inspection.__file__, "exec"), inspection.__dict__)


class InspectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.root.chmod(0o700)
        self.cache = inspection.cache_helper()
        self.mount = self.root / "mount"
        self.pack = self.mount / "deepseek-v4.1-flash-1017"
        self.pack.mkdir(parents=True)
        self.rows = []
        for name, data in (("config.json", b"public"), ("weights", b"public model bytes")):
            (self.pack / name).write_bytes(data)
            self.rows.append((hashlib.sha256(data).hexdigest(), len(data), name))
        self.export = "nfs.example.test:/public"
        self.output = self.root / "receipt.json"
        self.profile = self.cache.profiles()[0]

    def inspect(self, deadline=None):
        return inspection.inspect_pack(self.cache, self.export, self.mount, self.rows,
                                       time.monotonic() + 10 if deadline is None else deadline)

    def test_complete_pack_uses_stable_hashes_without_copy_or_mount(self):
        before = {p.name: p.read_bytes() for p in self.pack.iterdir()}
        with mock.patch.object(self.cache, "verify_mount") as verify, \
                mock.patch.object(self.cache, "mount_cache") as mount, \
                mock.patch.object(self.cache, "unmount_cache") as unmount, \
                mock.patch.object(self.cache, "copy_pack") as copy:
            self.assertEqual(self.inspect(), {"files": 2, "verified_bytes": 24})
            self.assertEqual(verify.call_args_list, [mock.call(self.export, self.mount, readonly=True)] * 2)
            for operation in (mount, unmount, copy):
                operation.assert_not_called()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.pack.iterdir()})

    def test_corrupt_extra_link_fifo_and_deadline_rejected(self):
        for mode in ("corrupt", "extra", "symlink", "hardlink", "fifo", "deadline"):
            with self.subTest(mode=mode):
                self.setUp()
                path = self.pack / "config.json"
                if mode == "corrupt": path.write_bytes(b"secret")
                if mode == "extra": (self.pack / "private").write_bytes(b"private")
                if mode == "symlink":
                    path.unlink()
                    path.symlink_to(self.pack / "weights")
                if mode == "hardlink": os.link(path, self.root / "linked")
                if mode == "fifo":
                    path.unlink()
                    os.mkfifo(path)
                with mock.patch.object(self.cache, "verify_mount"), self.assertRaises((ValueError, OSError)):
                    self.inspect(time.monotonic() - 1 if mode == "deadline" else None)

    def test_changes_after_hash_and_changed_mount_fail(self):
        original = self.cache.check_file
        def changed(root, row, deadline, **kwargs):
            result = original(root, row, deadline, **kwargs)
            if row == self.rows[-1]:
                (self.pack / "config.json").write_bytes(b"changed")
            return result
        with mock.patch.object(self.cache, "verify_mount"), \
                mock.patch.object(self.cache, "check_file", side_effect=changed), self.assertRaises(ValueError):
            self.inspect()
        self.setUp()
        with mock.patch.object(self.cache, "verify_mount", side_effect=[None, ValueError("changed")]), \
                self.assertRaises(ValueError):
            self.inspect()

    def test_requires_readonly_mount_and_exact_export_before_hash(self):
        for options, export in (("rw,nosuid,nodev,noexec", self.export),
                                ("ro,rw,nosuid,nodev,noexec", self.export),
                                ("ro,nosuid,noexec", self.export),
                                ("ro,nosuid,nodev,noexec", "nfs.example.test:/other")):
            with self.subTest(options=options, export=export):
                fs = {"source": export, "target": str(self.mount), "fstype": "nfs4", "options": options}
                with mock.patch.object(self.cache, "command", return_value=json.dumps({"filesystems": [fs]}).encode()), \
                        mock.patch.object(self.cache, "check_file") as check, self.assertRaises(ValueError):
                    self.inspect()
                check.assert_not_called()

    def success(self, **changes):
        return json.dumps({"schema": "nib.public-weight-cache-inspection-worker/v1", "model_profile": self.profile,
                           "files": 83, "verified_bytes": self.profile["total_bytes"], **changes}).encode()

    def report(self, capture, **changes):
        args = {"export": self.export, "mount": self.mount, "output": self.output,
                "producer": "a" * 40, "seconds": 120, **changes}
        with mock.patch.object(inspection, "cache_helper", return_value=self.cache), \
                mock.patch.object(self.cache, "load_capture", return_value=capture):
            return inspection.inspect_report(**args)

    def test_durable_intent_precedes_bounded_worker_and_success_is_scoped(self):
        def capture(argv, **kwargs):
            intent = json.loads(self.output.read_bytes())
            self.assertEqual((intent["result"], intent["cleanup"]), ("fail", "unproven"))
            self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(argv[:6], [sys.executable, "-I", "-S", "-B", "-c", inspection.BOOTSTRAP])
            self.assertEqual(argv[6], inspection.__file__)
            self.assertEqual(argv[7].encode(), Path(inspection.__file__).read_bytes())
            self.assertEqual(argv[8:], [self.export, str(self.mount), "60"])
            self.assertEqual(kwargs, {"timeout": 60, "limit": 4096})
            return self.success()
        self.assertEqual(self.report(capture), 0)
        result = json.loads(self.output.read_bytes())
        self.assertEqual(result["model_profile"], self.profile)
        self.assertTrue(result["pack_verified"])
        self.assertTrue(result["worker_exit_confirmed"])
        self.assertEqual(result["verified_bytes"], self.profile["total_bytes"])
        for key in ("mount_changed", "source_written", "source_removed", "immutable", "runtime_ready",
                    "confidential", "process_group_absence_verified"):
            self.assertFalse(result[key])
        self.assertNotIn(self.export, self.output.read_text())
        self.assertNotIn(str(self.mount), self.output.read_text())

    def test_bad_ack_timeout_cancellation_and_cleanup_preserve_failed_receipts(self):
        for failure, kind in ((ValueError("probe-timeout"), "timeout"),
                              (ValueError("probe-cleanup-timeout"), "cleanup-unproven"),
                              (ValueError("probe-command-failed"), "worker-failed"),
                              (InterruptedError("private-canary"), "cancelled"),
                              (ValueError("private-canary"), "rejected"),
                              (b"{}", "rejected"), (self.success(files=83.0), "rejected"),
                              (self.success(model_profile=self.cache.profiles()[1]), "rejected"),
                              (self.success()[:-1] + b', "files": 83}', "rejected")):
            with self.subTest(kind=kind):
                self.setUp()
                capture = mock.Mock(side_effect=failure) if isinstance(failure, Exception) else mock.Mock(return_value=failure)
                previous = {sig: inspection.signal.getsignal(sig) for sig in (inspection.signal.SIGINT, inspection.signal.SIGTERM)}
                self.assertEqual(self.report(capture), 1)
                result = json.loads(self.output.read_bytes())
                self.assertEqual((result["result"], result["failure"]), ("fail", kind))
                self.assertFalse(result["pack_verified"])
                self.assertFalse(result["worker_exit_confirmed"])
                self.assertEqual(result["verified_bytes"], 0)
                self.assertIsNotNone(result["ended_utc"])
                self.assertNotIn("private-canary", self.output.read_text())
                self.assertEqual({sig: inspection.signal.getsignal(sig) for sig in previous}, previous)

    def test_path_budget_and_receipt_collision_rejected_before_worker(self):
        capture = mock.Mock(return_value=self.success())
        for changes in ({"mount": Path("relative")}, {"output": self.pack / "receipt"},
                        {"mount": Path(inspection.__file__).parent}, {"seconds": 60},
                        {"seconds": 7201}, {"seconds": True}, {"producer": "main"},
                        {"export": "nfs.example.test:/public/../private"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.report(capture, **changes)
        self.output.write_text("original")
        with self.assertRaises(FileExistsError): self.report(capture)
        self.assertEqual(self.output.read_text(), "original")
        self.output.unlink()
        self.output.symlink_to(self.root / "missing")
        with self.assertRaises(FileExistsError): self.report(capture)
        capture.assert_not_called()

    def test_worker_uses_only_pinned_manifest_and_original_identity(self):
        fake_sys = types.SimpleNamespace(**vars(sys))
        fake_sys.platform = "linux"
        fake_sys.flags = types.SimpleNamespace(isolated=True, no_site=True)
        fake_sys.dont_write_bytecode = True
        stdout = io.StringIO()
        with mock.patch.object(inspection, "sys", fake_sys), mock.patch.object(inspection.os, "getuid", return_value=0), \
                mock.patch.object(inspection, "inspect_pack", return_value={"files": 83, "verified_bytes": self.profile["total_bytes"]}) as inspect, \
                mock.patch("sys.stdout", stdout):
            inspection.worker(self.export, str(self.mount), "60")
        self.assertEqual(json.loads(stdout.getvalue()), json.loads(self.success()))
        self.assertEqual(len(inspect.call_args.args[3]), 83)
        self.assertEqual(sum(row[1] for row in inspect.call_args.args[3]), self.profile["total_bytes"])
        with mock.patch.object(inspection.Path, "open", return_value=io.BytesIO(b"raise AssertionError('unverified')")):
            with self.assertRaises(ValueError): inspection.cache_helper()


if __name__ == "__main__":
    unittest.main()
