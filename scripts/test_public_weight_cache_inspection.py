# SPDX-License-Identifier: Apache-2.0
"""Synthetic cache inspection only; no mount, network or model downloads."""
import hashlib
import contextlib
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
        command = mock.patch.object(self.cache, "command", return_value=json.dumps({"filesystems": [{
            "source": self.export, "target": str(self.mount), "fstype": "nfs4",
            "options": "ro,nosuid,nodev,noexec"}]}).encode())
        command.start()
        self.addCleanup(command.stop)

    def inspect(self, deadline=None):
        with mock.patch.object(inspection, "mount_id", return_value=17):
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
        original = inspection.hash_file
        def changed(cache, root, row, deadline, expected_mount):
            result = original(cache, root, row, deadline, expected_mount)
            if row == self.rows[-1]:
                (self.pack / "config.json").write_bytes(b"changed")
            return result
        with mock.patch.object(self.cache, "verify_mount"), \
                mock.patch.object(inspection, "hash_file", side_effect=changed), self.assertRaises(ValueError):
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
                        mock.patch.object(inspection, "hash_file") as check, self.assertRaises(ValueError):
                    self.inspect()
                check.assert_not_called()

    def test_nested_mounts_on_pack_parent_or_file_never_pass(self):
        for ids in ([17, 18], [17, 17, 18], [17, 17, 17, 18]):
            with self.subTest(ids=ids), mock.patch.object(self.cache, "verify_mount"), \
                    mock.patch.object(inspection, "mount_id", side_effect=ids), \
                    mock.patch.object(inspection.hashlib, "file_digest") as digest, self.assertRaises(ValueError):
                inspection.inspect_pack(self.cache, self.export, self.mount, self.rows, time.monotonic() + 10)
            digest.assert_not_called()

    def test_open_mount_id_must_resolve_to_the_approved_export(self):
        with mock.patch.object(self.cache, "verify_mount"), mock.patch.object(self.cache, "command", return_value=json.dumps({
                "filesystems": [{"source": "nfs.example.test:/other", "target": str(self.mount),
                                 "fstype": "nfs4", "options": "ro,nosuid,nodev,noexec"}]}).encode()) as command, \
                self.assertRaises(ValueError):
            self.inspect()
        self.assertEqual(command.call_args.args[0], ["findmnt", "--json", "--id", "17", "--output",
                                                   "SOURCE,TARGET,FSTYPE,OPTIONS"])

    def test_mount_id_is_bounded_and_rejects_missing_or_duplicate_ids(self):
        for raw in (b"mnt_id: 17\n", b"mnt_id:\t17\nino: 20\n"):
            with mock.patch("builtins.open", return_value=io.BytesIO(raw)):
                self.assertEqual(inspection.mount_id(4), 17)
        for raw in (b"mnt_id: 0\n", b"", b"mnt_id: 17\nmnt_id: 17\n", b"x" * 4097):
            with mock.patch("builtins.open", return_value=io.BytesIO(raw)), self.assertRaises(ValueError):
                inspection.mount_id(4)

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

    def test_late_cancellation_clears_success_and_cli_errors_are_private(self):
        transition = max(index for index, line in enumerate(Path(inspection.__file__).read_text().splitlines(), 1)
                         if line.strip() == "for sig in handlers:")
        delivered = []
        def trace(frame, event, _):
            if frame.f_code is inspection.inspect_report.__code__ and event == "line" and frame.f_lineno == transition:
                sys.settrace(None)
                inspection.signal.getsignal(inspection.signal.SIGINT)(inspection.signal.SIGINT, None)
                delivered.append(True)
            return trace
        previous = sys.gettrace()
        try:
            sys.settrace(trace)
            self.assertEqual(self.report(mock.Mock(return_value=self.success())), 1)
        finally:
            sys.settrace(previous)
        self.assertEqual(delivered, [True])
        result = json.loads(self.output.read_bytes())
        self.assertEqual((result["result"], result["failure"], result["verified_bytes"]), ("fail", "cancelled", 0))
        self.assertFalse(result["pack_verified"])
        fake_sys = types.SimpleNamespace(**vars(sys))
        fake_sys.platform = "linux"
        fake_sys.flags = types.SimpleNamespace(isolated=True, no_site=True)
        fake_sys.dont_write_bytecode = True
        for argv in (["--timeout", "private-canary"], ["--unexpected-private-canary"]):
            stderr = io.StringIO()
            with mock.patch.object(inspection, "sys", fake_sys), mock.patch.object(inspection.os, "getuid", return_value=0), \
                    mock.patch("sys.stderr", stderr), self.assertRaisesRegex(ValueError, "arguments-rejected"):
                inspection.main(argv)
            self.assertEqual(stderr.getvalue(), "")

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

    def bounded_fixture(self, stack, *, cleanup_error=False, oom=0):
        resource = inspection.resource_helper(self.cache)
        @contextlib.contextmanager
        def group(state):
            state["limits_read_back"] = True
            fd = os.open(self.root, os.O_RDONLY)
            try:
                yield fd, "nib-build-" + "a" * 32
            finally:
                os.close(fd)
                if cleanup_error:
                    raise ValueError("private-cleanup-error")
                state.update(empty_verified=True, group_removed=True, oom_kill=oom,
                             memory_peak_bytes=1234, populated_before_cleanup=0)
        stack.enter_context(mock.patch.object(resource, "cgroup", side_effect=group))
        stack.enter_context(mock.patch.object(inspection, "resource_helper", return_value=resource))
        return resource

    def test_bounded_capture_pins_helper_and_wraps_before_mount_with_cleanup(self):
        with contextlib.ExitStack() as stack:
            resource = self.bounded_fixture(stack)
            def capture(argv, **kwargs):
                self.assertEqual(argv[:6], [sys.executable, "-I", "-S", "-B", "-c", inspection.RESOURCE_BOOTSTRAP])
                self.assertEqual(argv[6], "nib-build-" + "a" * 32)
                self.assertEqual(tuple(map(int, argv[7:9])), (self.root.stat().st_dev, self.root.stat().st_ino))
                self.assertEqual(json.loads(argv[9]), resource.LIMITS)
                self.assertEqual(argv[10:15], ["/usr/bin/unshare", "--mount", "--propagation", "private", "--"])
                self.assertEqual(kwargs, {"timeout": 60, "limit": 4096})
                intent = json.loads(self.output.read_bytes())
                self.assertEqual(intent["schema"], "nib.public-weight-cache-inspection/v3")
                self.assertFalse(intent["resources"]["empty_verified"])
                return self.success(private_namespace_verified=True, private_mount_cleanup_confirmed=True)
            stack.enter_context(mock.patch.object(inspection.os, "readlink", return_value="mnt:[17]"))
            self.assertEqual(self.report(capture, private_mount=True, bounded_resources=True), 0)
        result = json.loads(self.output.read_bytes())
        self.assertTrue(result["pack_verified"])
        self.assertTrue(result["resources"]["empty_verified"])
        self.assertTrue(result["resources"]["group_removed"])
        self.assertEqual(result["resources"]["limits"], resource.LIMITS)
        self.assertEqual(result["resources"]["oom_kill"], 0)
        self.assertNotIn(str(self.root), self.output.read_text())
        self.assertNotIn("nib-build-", self.output.read_text())

    def test_bounded_cleanup_oom_timeout_and_cancel_never_pass(self):
        for mode in ("cleanup", "oom", "timeout", "cancel", "ack"):
            with self.subTest(mode=mode), contextlib.ExitStack() as stack:
                self.setUp()
                self.bounded_fixture(stack, cleanup_error=mode == "cleanup", oom=int(mode == "oom"))
                capture = mock.Mock(return_value=b"{}" if mode == "ack" else self.success())
                if mode == "timeout": capture.side_effect = ValueError("probe-timeout")
                if mode == "cancel": capture.side_effect = InterruptedError()
                self.assertEqual(self.report(capture, bounded_resources=True), 1)
                result = json.loads(self.output.read_bytes())
                self.assertFalse(result["pack_verified"])
                self.assertEqual(result["verified_bytes"], 0)
                self.assertEqual(result["resources"]["empty_verified"], mode != "cleanup")
                self.assertNotIn("private-cleanup-error", self.output.read_text())

    def test_resource_helper_hash_and_boolean_gate(self):
        helper = inspection.resource_helper(self.cache)
        self.assertEqual(helper.LIMITS, {"memory.max": str(8 << 30), "memory.swap.max": "0",
                        "memory.oom.group": "1", "pids.max": "256", "cpu.max": "200000 100000"})
        with mock.patch.object(self.cache, "read_regular", return_value=b"raise AssertionError('unverified')"):
            with self.assertRaises(ValueError): inspection.resource_helper(self.cache)
        with self.assertRaises(ValueError): self.report(mock.Mock(), bounded_resources=1)

    def test_resource_bootstrap_checks_membership_and_limits_before_exec(self):
        # Execute the real bootstrap with fake kernel files; no cgroup mutation.
        program = compile(inspection.RESOURCE_BOOTSTRAP, "<resource-test>", "exec")
        limits = {"memory.max": str(8 << 30), "cpu.max": "200000 100000"}
        args = ["-c", "nib-build-" + "a" * 32, "1", "2", json.dumps(limits), "/usr/bin/unshare", "--mount"]
        for mode in ("ok", "inode", "membership", "limit"):
            with self.subTest(mode=mode), contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(sys, "argv", args))
                stack.enter_context(mock.patch.object(os, "open", return_value=19))
                stack.enter_context(mock.patch.object(os, "close"))
                stack.enter_context(mock.patch.object(os, "fstat", return_value=types.SimpleNamespace(st_dev=1, st_ino=3 if mode == "inode" else 2)))
                stack.enter_context(mock.patch.object(os, "write", side_effect=lambda fd, raw: len(raw)))
                stack.enter_context(mock.patch.object(Path, "read_text", return_value="0::/other\n" if mode == "membership" else "0::/" + args[1] + "\n"))
                stack.enter_context(mock.patch.object(os, "read", side_effect=[b"unlimited" if mode == "limit" else str(8 << 30).encode(), b"200000 100000"]))
                execute = stack.enter_context(mock.patch.object(os, "execv"))
                if mode == "ok":
                    exec(program, {})
                    execute.assert_called_once_with("/usr/bin/unshare", ["/usr/bin/unshare", "--mount"])
                else:
                    with self.assertRaises(AssertionError): exec(program, {})
                    execute.assert_not_called()

    def private_fixture(self, stack):
        mount = self.root / "empty-mount"
        mount.mkdir(mode=0o700)
        stack.enter_context(mock.patch.object(inspection.os, "readlink", return_value="mnt:[18]"))
        stack.enter_context(mock.patch.object(self.cache, "command", return_value=b'{"filesystems":[{"propagation":"private"}]}'))
        unmounted = stack.enter_context(mock.patch.object(self.cache, "require_unmounted", side_effect=[None, ValueError("mounted")]))
        mounted = stack.enter_context(mock.patch.object(self.cache, "mount_cache"))
        verify = stack.enter_context(mock.patch.object(self.cache, "verify_mount"))
        unmount = stack.enter_context(mock.patch.object(self.cache, "unmount_cache"))
        check = stack.enter_context(mock.patch.object(inspection, "inspect_pack", return_value={"files": 2, "verified_bytes": 24}))
        return mount, unmounted, mounted, verify, unmount, check

    def test_private_namespace_full_check_and_normal_cleanup_share_deadline(self):
        with contextlib.ExitStack() as stack:
            mount, unmounted, mounted, verify, unmount, check = self.private_fixture(stack)
            deadline = time.monotonic() + 10
            result = inspection.private_inspection(self.cache, self.export, mount, self.rows, deadline, "mnt:[17]")
            self.assertEqual(result, {"files": 2, "verified_bytes": 24,
                                      "private_namespace_verified": True, "private_mount_cleanup_confirmed": True})
            self.assertEqual(unmounted.call_args_list, [mock.call(mount)] * 2)
            mounted.assert_called_once_with(self.export, mount, readonly=True)
            check.assert_called_once_with(self.cache, self.export, mount, self.rows, deadline)
            verify.assert_called_once_with(self.export, mount, readonly=True)
            unmount.assert_called_once_with(self.export, mount)

    def test_namespace_propagation_target_and_deadline_reject_before_mount(self):
        for mode in ("same", "invalid", "shared", "nonempty", "mounted", "deadline"):
            with self.subTest(mode=mode), contextlib.ExitStack() as stack:
                self.setUp()
                mount, unmounted, mounted, _, _, check = self.private_fixture(stack)
                parent = "mnt:[17]"
                deadline = time.monotonic() + 10
                if mode == "same": parent = "mnt:[18]"
                if mode == "invalid": parent = "private-input"
                if mode == "shared": self.cache.command.return_value = b'{"filesystems":[{"propagation":"shared"}]}'
                if mode == "nonempty": (mount / "existing").write_bytes(b"untouched")
                if mode == "mounted": unmounted.side_effect = ValueError("mounted")
                if mode == "deadline": deadline = time.monotonic() - 1
                with self.assertRaises(ValueError):
                    inspection.private_inspection(self.cache, self.export, mount, self.rows, deadline, parent)
                mounted.assert_not_called()
                check.assert_not_called()

    def test_private_failures_never_pass_and_cleanup_refuses_foreign_mount(self):
        for mode in ("mount", "hash", "unmount", "wrong-export", "late"):
            with self.subTest(mode=mode), contextlib.ExitStack() as stack:
                self.setUp()
                mount, _, mounted, verify, unmount, check = self.private_fixture(stack)
                deadline = time.monotonic() + 10
                if mode == "mount": mounted.side_effect = ValueError("mount-failed-after-attachment")
                if mode == "hash": check.side_effect = ValueError("bad-hash")
                if mode == "unmount": unmount.side_effect = ValueError("busy")
                if mode == "wrong-export": verify.side_effect = ValueError("foreign")
                if mode == "late":
                    stack.enter_context(mock.patch.object(inspection.time, "monotonic", side_effect=[deadline - 1, deadline - 1, deadline + 1]))
                with self.assertRaises(ValueError):
                    inspection.private_inspection(self.cache, self.export, mount, self.rows, deadline, "mnt:[17]")
                if mode == "wrong-export": unmount.assert_not_called()
                else: unmount.assert_called_once_with(self.export, mount)

    def test_private_receipt_uses_isolated_worker_and_requires_exact_ack(self):
        def capture(argv, **kwargs):
            intent = json.loads(self.output.read_bytes())
            self.assertEqual(intent["schema"], "nib.public-weight-cache-inspection/v2")
            self.assertTrue(intent["private_mount_requested"])
            self.assertFalse(intent["private_namespace_verified"])
            self.assertFalse(intent["private_mount_cleanup_confirmed"])
            self.assertEqual(argv[:6], ["/usr/bin/unshare", "--mount", "--propagation", "private", "--", sys.executable])
            self.assertEqual(argv[-4:], [self.export, str(self.mount), "60", "mnt:[17]"])
            self.assertEqual(kwargs, {"timeout": 60, "limit": 4096})
            return self.success(private_namespace_verified=True, private_mount_cleanup_confirmed=True)
        with mock.patch.object(inspection.os, "readlink", return_value="mnt:[17]"):
            self.assertEqual(self.report(capture, private_mount=True), 0)
        result = json.loads(self.output.read_bytes())
        self.assertTrue(result["private_namespace_verified"])
        self.assertTrue(result["private_mount_cleanup_confirmed"])
        for key in ("mount_changed", "runtime_ready", "immutable", "confidential"):
            self.assertFalse(result[key])
        for ack in (self.success(), self.success(private_namespace_verified=1, private_mount_cleanup_confirmed=True),
                    self.success(private_namespace_verified=True, private_mount_cleanup_confirmed=False)):
            self.output.unlink()
            with mock.patch.object(inspection.os, "readlink", return_value="mnt:[17]"):
                self.assertEqual(self.report(mock.Mock(return_value=ack), private_mount=True), 1)
            result = json.loads(self.output.read_bytes())
            self.assertFalse(result["private_namespace_verified"])
            self.assertFalse(result["private_mount_cleanup_confirmed"])
            self.assertFalse(result["pack_verified"])

    def test_private_timeout_keeps_cleanup_unproven_without_retry(self):
        capture = mock.Mock(side_effect=ValueError("probe-timeout"))
        with mock.patch.object(inspection.os, "readlink", return_value="mnt:[17]"):
            self.assertEqual(self.report(capture, private_mount=True), 1)
        capture.assert_called_once()
        result = json.loads(self.output.read_bytes())
        self.assertEqual(result["failure"], "timeout")
        self.assertEqual(result["cleanup"], "unproven")
        self.assertFalse(result["private_mount_cleanup_confirmed"])

    def test_private_worker_routes_only_to_isolated_mount_check(self):
        fake_sys = types.SimpleNamespace(**vars(sys))
        fake_sys.platform = "linux"
        fake_sys.flags = types.SimpleNamespace(isolated=True, no_site=True)
        fake_sys.dont_write_bytecode = True
        ack = {"files": 83, "verified_bytes": self.profile["total_bytes"],
               "private_namespace_verified": True, "private_mount_cleanup_confirmed": True}
        with mock.patch.object(inspection, "sys", fake_sys), mock.patch.object(inspection.os, "getuid", return_value=0), \
                mock.patch.object(inspection, "private_inspection", return_value=ack) as check, \
                mock.patch.object(inspection, "inspect_pack") as direct, mock.patch("sys.stdout", io.StringIO()) as output:
            inspection.worker(self.export, str(self.mount), "60", "mnt:[17]")
        direct.assert_not_called()
        self.assertEqual(check.call_args.args[-1], "mnt:[17]")
        self.assertEqual(json.loads(output.getvalue()), json.loads(self.success(**ack)))


if __name__ == "__main__":
    unittest.main()
