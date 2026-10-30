# SPDX-License-Identifier: Apache-2.0
"""Synthetic parsers and injected I/O only; never inspect this test host."""
import ast
from contextlib import contextmanager, ExitStack
import copy
import io
import itertools
import os
from pathlib import Path
import runpy
import stat
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "host/inspect-linux-capabilities.py"
module = types.SimpleNamespace(**runpy.run_path(str(SOURCE)))


class Fixture:
    def __init__(self):
        self.calls = []
        self.process = {"linux": True, "architecture": "x86_64", "uid_zero": True, "euid_zero": True,
            "python_interfaces": {key: True for key in ("pidfd_open", "pidfd_send_signal", "so_peercred", "sock_seqpacket")}}
        self.meta = {"owned_by_root": True, "protected_mode": True}
        self.files = {
            ("proc", ("mountinfo",)): b"27 20 0:22 / /sys/fs/cgroup rw,nosuid,nodev - cgroup2 cgroup rw\n",
            ("proc", ("cgroup",)): b"0::/ci-job\n",
            ("proc", ("status",)): b"Name:\tDO_NOT_PRINT\nNoNewPrivs:\t1\nSeccomp:\t2\nCapEff:\t0000000000000000\n",
            ("proc", ("uid_map",)): b"0 0 4294967295\n",
            ("fdinfo", (100,)): b"pos:\t0\nflags:\t0100000\nmnt_id:\t27\nino:\t987654321\n",
            ("cgroup", ("cgroup.controllers",)): b"cpu memory pids io\n",
            ("cgroup", ("cgroup.subtree_control",)): b"cpu memory pids\n",
            ("cgroup", ("cgroup.type",)): b"domain\n",
            ("cgroup", ("cgroup.procs",)): b"12345678\n",
        }
        values = {"memory.max": b"8589934592\n", "memory.swap.max": b"0\n", "memory.oom.group": b"1\n",
                  "pids.max": b"256\n", "cpu.max": b"200000 100000\n", "memory.peak": b"4096\n",
                  "memory.events": b"low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n"}
        for parent in ((), ("ci-job",)):
            self.files.update({("cgroup", parent + (name,)): raw for name, raw in values.items()})

    def facts(self):
        return copy.deepcopy(self.process)

    @contextmanager
    def open(self, area, parts):
        yield area, parts

    @contextmanager
    def pin_group(self, expected, mount_id):
        module.require(mount_id(100) == expected, "mount-correspondence")
        yield

    def recheck_group(self):
        pass

    def read(self, handle, maximum, check):
        check()
        area, parts = handle
        self.calls.append((area, parts, maximum))
        value = self.files.get((area, parts), (b"", "missing"))
        raw, error = value if isinstance(value, tuple) else (value, None)
        return raw[:maximum], error

    def root_metadata(self):
        return dict(self.meta)

    def namespace(self, actor, name):
        return (123456789, 987654321)

    def kill_interface(self, parts):
        return True


class KernelFixture:
    """In-memory descriptor table for the actual Linux boundary, not host I/O."""
    def __init__(self):
        self.fixture = Fixture()
        self.handles = {}
        self.sequence = itertools.count(10)
        self.overmounts = {}
        self.fdinfo_errors = {}
        self.opened, self.closed, self.read_paths = [], [], []
        self.bytes_read = 0

    def open(self, name, flags, dir_fd=None):
        path = () if name == "/" else self.handles[dir_fd]["path"] + (name,)
        mount = 27 if path[:3] == ("sys", "fs", "cgroup") else 2
        for prefix, identifier in sorted(self.overmounts.items(), key=lambda row: len(row[0])):
            if path[:len(prefix)] == prefix:
                mount = identifier
        fd = next(self.sequence)
        self.handles[fd] = {"path": path, "mount": mount, "offset": 0,
                            "mode": stat.S_IFDIR if flags & os.O_DIRECTORY else stat.S_IFREG}
        self.opened.append((path, flags, fd))
        return fd

    def close(self, fd):
        self.closed.append(fd)
        del self.handles[fd]

    def fstat(self, fd):
        # All simulated mounts deliberately share a device: st_dev is not proof.
        return types.SimpleNamespace(st_mode=self.handles[fd]["mode"] | 0o755, st_uid=0, st_dev=22)

    def read(self, fd, maximum):
        handle = self.handles[fd]
        path = handle["path"]
        self.read_paths.append(path)
        if path[:3] == ("proc", "777", "fdinfo"):
            subject = self.handles[int(path[-1])]
            error = self.fdinfo_errors.get(subject["path"])
            if error:
                raise error
            raw = ("pos:\t0\nflags:\t0100000\nmnt_id:\t%d\nino:\t987654321\n" % subject["mount"]).encode()
        else:
            key = ("cgroup", path[3:]) if path[:3] == ("sys", "fs", "cgroup") else ("proc", (path[-1],))
            raw = self.fixture.files[key]
        part = raw[handle["offset"]:handle["offset"] + maximum]
        handle["offset"] += len(part)
        self.bytes_read += len(part)
        return part

    @contextmanager
    def boundary(self):
        boundary = module.Linux()
        with ExitStack() as stack:
            for name in ("open", "close", "fstat", "read"):
                stack.enter_context(mock.patch.object(module.os, name, side_effect=getattr(self, name)))
            stack.enter_context(mock.patch.object(module.os, "getpid", return_value=777))
            stack.enter_context(mock.patch.object(boundary, "facts", side_effect=self.fixture.facts))
            stack.enter_context(mock.patch.object(boundary, "namespace", side_effect=self.fixture.namespace))
            yield boundary
        if self.handles or boundary.descriptors:
            raise AssertionError("synthetic descriptors not closed")


class DescriptorCorrespondenceTests(unittest.TestCase):
    def collect(self, kernel):
        with kernel.boundary() as boundary:
            report, status = module.collect(boundary, clock=lambda: 1.0)
        self.assertEqual(report["bytes_read"], kernel.bytes_read)
        return report, status

    def test_root_is_retained_and_all_accepted_cgroup_handles_are_bound(self):
        kernel = KernelFixture()
        report, status = self.collect(kernel)
        self.assertEqual(status, 0)
        self.assertTrue(report["collection_complete"])
        cgroup_handles = [(path, fd) for path, _, fd in kernel.opened if path[:3] == ("sys", "fs", "cgroup")]
        for _, fd in cgroup_handles:
            self.assertIn(("proc", "777", "fdinfo", str(fd)), kernel.read_paths)
        pinned = cgroup_handles[0][1]
        self.assertEqual(kernel.closed[-4:], [pinned, pinned - 1, pinned - 2, pinned - 3])
        self.assertFalse(any(path[-1:] == ("cgroup.kill",) for path in kernel.read_paths))
        for _, flags, _ in kernel.opened:
            self.assertTrue(flags & os.O_NOFOLLOW)
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
        for name in ("delegation_verified", "kernel_integration_verified", "operationally_approved"):
            self.assertIs(report[name], False)

    def test_new_directory_leaf_and_same_device_binds_fail_before_payload(self):
        for suffix in ((), ("ci-job",), ("ci-job", "memory.max"), ("cgroup.controllers",), ("ci-job", "cgroup.kill")):
            with self.subTest(suffix=suffix):
                kernel = KernelFixture()
                path = ("sys", "fs", "cgroup") + suffix
                kernel.overmounts[path] = 28
                # mountinfo remains the earlier root-only snapshot; fdinfo must
                # detect this later overmount despite identical device metadata.
                report, status = self.collect(kernel)
                self.assertEqual(status, 0)
                self.assertFalse(report["collection_complete"])
                self.assertEqual(report["observations"]["ancestry"]["mapping"], "unknown")
                self.assertEqual(report["current_helper_preconditions"], "unknown")
                self.assertTrue(any(row["reason"] == "mount-correspondence" for row in report["errors"]))
                self.assertFalse(any(value[:len(path)] == path for value in kernel.read_paths))

    def test_crossed_directory_rejected_even_if_leaf_mount_would_match(self):
        kernel = KernelFixture()
        path = ("sys", "fs", "cgroup", "ci-job")
        kernel.overmounts[path] = 28
        kernel.overmounts[path + ("memory.max",)] = 27
        report, _ = self.collect(kernel)
        self.assertEqual(report["observations"]["ancestry"]["mapping"], "unknown")
        self.assertNotIn(path + ("memory.max",), [row[0] for row in kernel.opened])

    def test_unreadable_descriptor_identity_never_falls_back_to_device(self):
        kernel = KernelFixture()
        path = ("sys", "fs", "cgroup", "ci-job", "memory.max")
        kernel.fdinfo_errors[path] = PermissionError("NEVER_PRINT_FDINFO")
        report, status = self.collect(kernel)
        self.assertEqual(status, 0)
        self.assertEqual(report["current_helper_preconditions"], "unknown")
        self.assertNotIn(path, kernel.read_paths)
        self.assertNotIn(b"NEVER_PRINT_FDINFO", module.render(report))

    def test_fdinfo_spends_budget_before_payload_capacity_is_computed(self):
        kernel = KernelFixture()
        with kernel.boundary() as boundary:
            collector = module.Collector(boundary, lambda: 1.0)
            identify = lambda fd: collector.read("fdinfo", (fd,), module.descriptor_mount)
            with boundary.pin_group(27, identify):
                identity_size = collector.used
                collector.used = module.TOTAL_LIMIT - identity_size - 2
                result = collector.observe("limit", lambda: collector.read("cgroup", ("memory.max",),
                    lambda raw: module.limit_value("memory.max", raw)))
                self.assertIsNone(result)
                self.assertEqual(collector.used, module.TOTAL_LIMIT)
                self.assertEqual(collector.errors, [{"field": "limit", "reason": "read-budget"}])
                opened = len(kernel.opened)
                self.assertIsNone(collector.observe("again", lambda: identify(boundary.group_root)))
                self.assertEqual(len(kernel.opened), opened)


class CapabilitiesTests(unittest.TestCase):
    def setUp(self):
        self.boundary = Fixture()

    def collect(self):
        return module.collect(self.boundary, clock=lambda: 1.0)

    def test_complete_synthetic_observations_are_not_delegation_or_admission(self):
        report, status = self.collect()
        self.assertEqual(status, 0)
        self.assertTrue(report["collection_complete"])
        self.assertEqual(report["current_helper_preconditions"], "not_disproved")
        for name in ("delegation_verified", "kernel_integration_verified", "operationally_approved"):
            self.assertIs(report[name], False)
        self.assertFalse(report["observations"]["ancestry"]["effective_limits_complete"])
        self.assertIn("hidden-ancestors-unknown", report["limitations"])
        self.assertEqual(len(report["observations"]["ancestry"]["visible"]), 2)

    def test_nonroot_is_expected_blocked_diagnostic_not_collection_failure(self):
        self.boundary.process.update(uid_zero=False, euid_zero=False)
        report, status = self.collect()
        self.assertEqual(status, 0)
        self.assertTrue(report["collection_complete"])
        self.assertEqual(report["current_helper_preconditions"], "blocked")
        self.assertIn("not-root", report["blocked_reasons"])

    def test_known_negative_mount_controller_and_mode_observations(self):
        for case in ("read-only", "not-v2", "duplicate", "disabled", "mode"):
            with self.subTest(case=case):
                self.boundary = Fixture()
                key = ("proc", ("mountinfo",))
                if case == "read-only": self.boundary.files[key] = self.boundary.files[key].replace(b" rw,", b" ro,")
                if case == "not-v2": self.boundary.files[key] = self.boundary.files[key].replace(b"- cgroup2", b"- tmpfs")
                if case == "duplicate": self.boundary.files[key] *= 2
                if case == "disabled": self.boundary.files[("cgroup", ("cgroup.subtree_control",))] = b"cpu memory\n"
                if case == "mode": self.boundary.meta["protected_mode"] = False
                report, status = self.collect()
                self.assertEqual(status, 0)
                self.assertEqual(report["current_helper_preconditions"], "blocked")

    def test_escaped_mount_fields_are_decoded_without_echo(self):
        rows = module.mounts(b"27 20 0:22 /private\\040name /sys/fs/cgroup rw - cgroup2 ignored rw\n")
        self.assertEqual(rows[0]["root"], "/private name")
        self.boundary.files[("proc", ("mountinfo",))] = (
            b"27 20 0:22 /private\\040name /sys/fs/cgroup rw - cgroup2 ignored rw\n")
        report, status = self.collect()
        self.assertEqual(status, 0)
        self.assertEqual(report["current_helper_preconditions"], "unknown")
        self.assertEqual(report["observations"]["mount"]["root_class"], "subtree")
        self.assertNotIn(b"private", module.render(report))
        self.assertEqual(module.mount_field(r"/a\134b\011c\012d"), "/a\\b\tc\nd")
        with self.assertRaises(module.Rejected): module.mount_field(r"/a\777b")

    def test_directory_leaf_and_same_filesystem_overmounts_are_unknown(self):
        for row in (
            b"28 27 0:23 / /sys/fs/cgroup/ci-job rw - tmpfs ignored rw\n",
            b"28 27 0:23 / /sys/fs/cgroup/ci-job/memory.max rw - tmpfs ignored rw\n",
            b"28 27 0:22 /other /sys/fs/cgroup/ci-job rw - cgroup2 ignored rw\n",
            b"28 27 0:22 /other/memory.max /sys/fs/cgroup/ci-job/memory.max rw - cgroup2 ignored rw\n",
            b"28 27 0:22 / /sys/fs/cgroup/escaped\\040name rw - cgroup2 ignored rw\n",
        ):
            with self.subTest(row=row):
                self.boundary = Fixture()
                self.boundary.files[("proc", ("mountinfo",))] += row
                report, status = self.collect()
                self.assertEqual(status, 0)
                self.assertEqual(report["current_helper_preconditions"], "unknown")
                self.assertEqual(report["observations"]["ancestry"]["mapping"], "unknown")
                self.assertEqual(report["observations"]["ancestry"]["visible"], [])
                self.assertIn({"field": "mount.correspondence", "reason": "mount-correspondence"}, report["errors"])
                self.assertFalse(any(area == "cgroup" for area, _, _ in self.boundary.calls))
                self.assertNotIn(b"escaped", module.render(report))

    def test_unrelated_mount_is_not_an_intersecting_overmount(self):
        self.boundary.files[("proc", ("mountinfo",))] += (
            b"28 20 0:23 / /sys/fs/cgroup-other rw - tmpfs ignored rw\n")
        report, status = self.collect()
        self.assertEqual(status, 0)
        self.assertTrue(report["collection_complete"])

    def test_changed_final_mount_table_or_root_binding_is_unknown(self):
        for case in ("table", "root"):
            with self.subTest(case=case):
                self.boundary = Fixture()
                def changed():
                    if case == "root":
                        raise module.Rejected("mount-correspondence")
                    self.boundary.files[("proc", ("mountinfo",))] += (
                        b"28 27 0:22 /other /sys/fs/cgroup/ci-job rw - cgroup2 ignored rw\n")
                self.boundary.recheck_group = changed
                report, status = self.collect()
                self.assertEqual(status, 0)
                self.assertFalse(report["collection_complete"])
                self.assertEqual(report["observations"]["ancestry"]["mapping"], "unknown")
                self.assertEqual(report["current_helper_preconditions"], "unknown")

    def test_missing_bad_or_truncated_fdinfo_never_establishes_correspondence(self):
        for raw, error, expected_status in (
            (b"", "missing", 0), (b"", "denied", 0), (b"mnt_id:", "denied", 0),
            (b"mnt_id: 28\n", None, 0),
            (b"ino: 27\n", None, 1), (b"mnt_id: 27\nmnt_id: 27\n", None, 1),
            (b"mnt_id: 0\n", None, 1), (b"x" * (module.SMALL_LIMIT + 1), None, 1),
        ):
            with self.subTest(raw=raw[:30], error=error):
                self.boundary = Fixture()
                self.boundary.files[("fdinfo", (100,))] = (raw, error)
                report, status = self.collect()
                self.assertEqual(status, expected_status)
                self.assertEqual(report["observations"]["ancestry"]["mapping"], "unknown")
                self.assertEqual(report["current_helper_preconditions"], "unknown")
                self.assertFalse(any(area == "cgroup" for area, _, _ in self.boundary.calls))
                expected = sum(len((value[0] if isinstance(value, tuple) else value)[:maximum])
                    for area, parts, maximum in self.boundary.calls
                    for value in [self.boundary.files.get((area, parts), (b"", "missing"))])
                self.assertEqual(report["bytes_read"], expected)

    def test_subtree_root_is_unknown_not_prefix_stripped(self):
        self.boundary.files[("proc", ("mountinfo",))] = (
            b"27 20 0:22 /delegated /sys/fs/cgroup rw - cgroup2 cgroup rw\n")
        self.boundary.files[("proc", ("cgroup",))] = b"0::/delegated/ci-job\n"
        report, status = self.collect()
        self.assertEqual(status, 0)
        self.assertFalse(report["collection_complete"])
        self.assertEqual(report["observations"]["ancestry"]["visible"], [])
        self.assertTrue(any(row["reason"] == "unsupported-mapping" for row in report["errors"]))
        self.assertFalse(any(area == "cgroup" and len(parts) > 1 for area, parts, _ in self.boundary.calls))

    def test_path_and_depth_rejection_never_reads_guessed_ancestors(self):
        paths = (b"/../escape", b"/a//b", b"/a/./b", b"/a\\b", b"/" + b"/".join([b"a"] * 16))
        for path in paths:
            with self.subTest(path=path):
                self.boundary = Fixture()
                self.boundary.files[("proc", ("cgroup",))] = b"0::" + path + b"\n"
                report, status = self.collect()
                self.assertEqual(status, 0)
                self.assertEqual(report["current_helper_preconditions"], "unknown")
                self.assertEqual(report["observations"]["ancestry"]["visible"], [])
                self.assertFalse(any(area == "cgroup" and len(parts) > 1 for area, parts, _ in self.boundary.calls))
        self.assertEqual(len(module.mapped_parts("/", "/" + "/".join(["a"] * 15))), 15)

    def test_missing_and_denied_never_become_unlimited_or_not_disproved(self):
        for error in ("missing", "denied"):
            with self.subTest(error=error):
                self.boundary.files[("cgroup", ("ci-job", "memory.max"))] = (b"", error)
                report, status = self.collect()
                self.assertEqual(status, 0)
                self.assertEqual(report["current_helper_preconditions"], "unknown")
                self.assertIsNone(report["observations"]["ancestry"]["visible"][0]["memory.max"])
                self.assertFalse(report["collection_complete"])

    def test_malformed_and_oversize_data_are_failures_and_counted(self):
        for raw in (b"malformed\n", b"S" * (module.SMALL_LIMIT + 20)):
            with self.subTest(size=len(raw)):
                self.boundary = Fixture()
                self.assertEqual(self.boundary.calls, [])
                self.boundary.files[("proc", ("status",))] = raw
                report, status = self.collect()
                self.assertEqual(status, 1)
                self.assertFalse(report["collection_complete"])
                self.assertNotEqual(report["current_helper_preconditions"], "not_disproved")
                self.assertEqual(sum(area == "proc" and parts == ("status",)
                    for area, parts, _ in self.boundary.calls), 1)
                expected = sum(len((value[0] if isinstance(value, tuple) else value)[:maximum])
                    for area, parts, maximum in self.boundary.calls
                    for value in [self.boundary.files.get((area, parts), (b"", "missing"))])
                self.assertEqual(report["bytes_read"], expected)

    def test_rejected_partial_and_sentinel_reads_exhaust_same_aggregate_budget(self):
        collector = module.Collector(self.boundary, lambda: 1.0)
        collector.used = module.TOTAL_LIMIT - 8
        self.boundary.files[("proc", ("status",))] = (b"abc", "denied")
        self.assertIsNone(collector.observe("status", lambda: collector.read("proc", ("status",), module.policy)))
        self.assertEqual(collector.used, module.TOTAL_LIMIT - 5)
        self.boundary.files[("proc", ("status",))] = b"123456"
        self.assertIsNone(collector.observe("status", lambda: collector.read("proc", ("status",), module.policy)))
        self.assertEqual(collector.used, module.TOTAL_LIMIT)
        calls = len(self.boundary.calls)
        self.assertIsNone(collector.observe("status", lambda: collector.read("proc", ("status",), module.policy)))
        self.assertEqual(len(self.boundary.calls), calls)
        self.assertEqual(collector.errors[-1]["reason"], "read-budget")

    def test_timeout_after_read_still_charges_bytes_and_prevents_positive_report(self):
        clock = [1.0]
        original = self.boundary.read
        def delayed(*args):
            result = original(*args)
            clock[0] = 11.0
            return result
        self.boundary.read = delayed
        report, status = module.collect(self.boundary, clock=lambda: clock[0])
        self.assertEqual(status, 1)
        self.assertGreater(report["bytes_read"], 0)
        self.assertFalse(report["collection_complete"])
        self.assertNotEqual(report["current_helper_preconditions"], "not_disproved")
        self.assertEqual(len(self.boundary.calls), 1)

    def test_secret_strings_paths_pids_and_namespace_numbers_never_appear(self):
        secret = "NEVER_PRINT_SYNTHETIC_PRIVATE_VALUE"
        self.boundary.files[("proc", ("status",))] += ("Ignored: " + secret + "\n").encode()
        self.boundary.files[("proc", ("cgroup",))] = ("0::/" + secret + "\n").encode()
        self.boundary.namespace = mock.Mock(side_effect=PermissionError(secret))
        report, _ = self.collect()
        raw = module.render(report)
        for value in (secret, "12345678", "123456789", "987654321", "DO_NOT_PRINT", "/sys/", "/proc/"):
            self.assertNotIn(value.encode(), raw)
        self.assertNotIn(b"Traceback", raw)

    def test_namespace_comparison_is_only_a_relation_not_isolation(self):
        self.boundary.namespace = lambda actor, name: (1, 2 if actor == "self" else 3)
        report, status = self.collect()
        self.assertEqual(status, 0)
        self.assertEqual(set(report["observations"]["namespaces"].values()), {"different-from-pid1"})
        self.assertFalse(report["delegation_verified"])

    def test_parsers_reject_ambiguous_and_bad_numeric_records(self):
        for raw in (b"0::/a\n0::/b\n", b"0:cpu:/a\n", b"0::relative\n"):
            with self.assertRaises(module.Rejected): module.membership(raw)
        for raw in (b"0 0 1\n0 1 1\n", b"0 0 0\n", b"0 0 4294967297\n"):
            with self.assertRaises(module.Rejected): module.uid_mapping(raw)
        self.assertEqual(module.uid_mapping(b"0 1000 1\n"), "root-mapped")
        self.assertEqual(module.uid_mapping(b"1 1000 1\n"), "no-root-mapping")
        with self.assertRaises(module.Rejected): module.controllers(b"cpu cpu\n")
        with self.assertRaises(module.Rejected): module.limit_value("memory.events", b"oom 0\noom 0\noom_kill 0\n")
        for raw in (b"-1\n", b"18446744073709551616\n", b"unknown\n"):
            with self.assertRaises(module.Rejected): module.limit_value("memory.peak", raw)
        self.assertEqual(module.limit_value("memory.max", b"max\n"), "max")
        self.assertEqual(module.limit_value("cpu.max", b"max 100000\n"), {"quota": "max", "period": 100000})

    def test_output_bound_is_hard(self):
        with self.assertRaises(module.Rejected): module.render({"synthetic": "x" * module.OUTPUT_LIMIT})
        self.assertLess(len(module.render(module.failure("collector-failed"))), module.OUTPUT_LIMIT)

    def test_filesystem_boundary_never_follows_arbitrary_links_or_opens_for_write(self):
        opened = []
        def fake_open(path, flags, **kwargs):
            opened.append((path, flags, kwargs))
            return len(opened) + 10
        with mock.patch.object(module.os, "open", side_effect=fake_open), \
             mock.patch.object(module.os, "close") as close, \
             mock.patch.object(module.os, "getpid", return_value=777), \
             mock.patch.object(module.os, "fstat", return_value=types.SimpleNamespace(st_mode=stat.S_IFREG)), \
             mock.patch.object(module.os, "read", side_effect=[b"abc", b""]):
            boundary = module.Linux()
            with boundary.open("proc", ("status",)) as fd:
                raw, error = boundary.read(fd, 4097, lambda: None)
        self.assertEqual((raw, error), (b"abc", None))
        self.assertEqual([row[0] for row in opened], ["/", "proc", "777", "status"])
        self.assertEqual(close.call_count, len(opened))
        for _, flags, _ in opened:
            self.assertTrue(flags & os.O_NOFOLLOW)
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
        with self.assertRaises(module.Rejected): module.Linux().location("proc", ("environ",))
        with self.assertRaises(module.Rejected): module.Linux().location("cgroup", ("..", "memory.max"))
        with self.assertRaises(module.Rejected): module.Linux().namespace("arbitrary-pid", "time")
        with self.assertRaises(module.Rejected): module.Linux().location("fdinfo", (100,))
        self.assertEqual(boundary.descriptors, set())

    def test_partial_kernel_read_error_and_timeout_preserve_consumed_count(self):
        for problem in (PermissionError("NEVER_PRINT"), module.Rejected("timeout")):
            with self.subTest(kind=type(problem).__name__):
                with mock.patch.object(module.os, "open", side_effect=itertools.count(10)), \
                     mock.patch.object(module.os, "close"), mock.patch.object(module.os, "getpid", return_value=777), \
                     mock.patch.object(module.os, "fstat", return_value=types.SimpleNamespace(st_mode=stat.S_IFREG)), \
                     mock.patch.object(module.os, "read", side_effect=[b"abc", problem]):
                    boundary = module.Linux()
                    with boundary.open("proc", ("status",)) as fd:
                        raw, code = boundary.read(fd, 4097, lambda: None)
                self.assertEqual(raw, b"abc")
                self.assertIn(code, ("denied", "timeout"))

    def test_only_fixed_namespace_handles_are_followed_and_symlink_leaf_rejected(self):
        with mock.patch.object(module.os, "open", side_effect=itertools.count(10)) as opened, \
             mock.patch.object(module.os, "close") as closed, \
             mock.patch.object(module.os, "getpid", return_value=777), \
             mock.patch.object(module.os, "stat", return_value=types.SimpleNamespace(st_dev=1, st_ino=2)) as stated:
            self.assertEqual(module.Linux().namespace("self", "time"), (1, 2))
            self.assertEqual([call.args[0] for call in opened.call_args_list], ["/", "proc", "777", "ns"])
            stated.assert_called_once_with("time", dir_fd=13, follow_symlinks=True)
            self.assertEqual(closed.call_count, 4)
        with mock.patch.object(module.os, "open", side_effect=AssertionError("no-open")):
            with self.assertRaises(module.Rejected): module.Linux().namespace("self", "../environ")
        with mock.patch.object(module.os, "open", side_effect=itertools.count(10)), \
             mock.patch.object(module.os, "close"), \
             mock.patch.object(module.os, "fstat", return_value=types.SimpleNamespace(st_mode=stat.S_IFLNK)):
            boundary = module.Linux()
            with boundary.pin_group(27, lambda fd: 27):
                with self.assertRaises(module.Rejected): boundary.kill_interface(())
            self.assertEqual(boundary.descriptors, set())

    def test_mountinfo_sentinel_is_charged_and_never_parsed_as_complete(self):
        self.boundary.files[("proc", ("mountinfo",))] = b"x" * (module.MOUNT_LIMIT + 10)
        report, status = self.collect()
        self.assertEqual(status, 1)
        self.assertFalse(report["collection_complete"])
        self.assertGreaterEqual(report["bytes_read"], module.MOUNT_LIMIT + 1)
        self.assertIn({"field": "mountinfo", "reason": "read-limit"}, report["errors"])
        self.assertIsNone(report["observations"]["mount"])

    def test_main_has_fixed_error_output_and_no_traceback(self):
        output = io.BytesIO()
        fake_sys = types.SimpleNamespace(argv=["collector", "NEVER_PRINT"], stdout=types.SimpleNamespace(buffer=output))
        with mock.patch.dict(module.main.__globals__, sys=fake_sys, Linux=mock.Mock(side_effect=AssertionError("no-probe"))):
            self.assertEqual(module.main(), 1)
        self.assertNotIn(b"NEVER_PRINT", output.getvalue())
        self.assertIn(b'"current_helper_preconditions":"unknown"', output.getvalue())

    def test_no_subprocess_network_environment_or_mutation_calls(self):
        tree = ast.parse(SOURCE.read_text())
        imports = {alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names}
        self.assertNotIn("subprocess", imports)
        calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertFalse(calls & {"socket", "connect", "urlopen", "getenv", "mkdir", "unlink", "chmod", "mount", "pidfd_open", "pidfd_send_signal"})
        self.assertNotIn("os.environ", SOURCE.read_text())


if __name__ == "__main__":
    unittest.main()
