# SPDX-License-Identifier: Apache-2.0
"""Synthetic filesystem/reader tests only; never collect from the test host."""
import ast
from contextlib import contextmanager, ExitStack
import copy
import hashlib
import itertools
from pathlib import Path
import runpy
import stat
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "host/inspect-runtime-inventory.py"
module = types.SimpleNamespace(**runpy.run_path(str(SOURCE)))


def record(name="systemd", version="256.5-1", architecture="amd64", state="install ok installed"):
    return ("Package: %s\nStatus: %s\nArchitecture: %s\nVersion: %s\n\n" %
            (name, state, architecture, version)).encode()


class Clock:
    def __init__(self):
        self.now = 1.0

    def __call__(self):
        return self.now


class Filesystem:
    """An in-memory OS descriptor table, including the actual Linux reader path."""
    def __init__(self):
        self.clock = Clock()
        self.nodes, self.handles, self.errors = {}, {}, {}
        self.ids, self.fds = itertools.count(10), itertools.count(100)
        self.opened, self.closed, self.reads, self.links, self.stats = [], [], [], [], []
        self.used = 0
        self.after_read = lambda path, part: None
        self.after_open = lambda path: None
        self.after_close = lambda path: None
        self.read_size = module.CHUNK
        for path in ((), ("usr",), ("usr", "lib"), ("usr", "lib", "systemd"),
                     ("usr", "bin"), ("var",), ("var", "lib"), ("var", "lib", "dpkg"),
                     ("proc",), ("proc", "1")):
            self.add(path, stat.S_IFDIR | 0o755)
        self.add(("lib",), stat.S_IFLNK | 0o777, target="usr/lib")
        self.add(("bin",), stat.S_IFLNK | 0o777, target="usr/bin")
        self.add(module.STATUS_PATH, stat.S_IFREG | 0o644,
                 record() + record("systemd-container") + record("unrelated") +
                 b"Package: ignored\nArchitecture: all\nDescription: DO_NOT_PRINT\n continued secret\n\n")
        for role, paths in module.BINARIES.items():
            self.add(paths[0], stat.S_IFREG | 0o755, (role + "-synthetic-not-an-executable").encode())
        self.add(("proc", "1", "exe"), stat.S_IFLNK | 0o777, target="DO_NOT_PRINT_PROCESS_PATH")
        self.pid1_target = self.nodes[module.BINARIES["manager"][0]]

    def add(self, path, mode, data=b"", target=None):
        size = len(target.encode()) if target is not None else len(data)
        info = types.SimpleNamespace(st_mode=mode, st_uid=0, st_gid=0, st_dev=1,
            st_ino=next(self.ids), st_nlink=1, st_size=size, st_mtime_ns=1, st_ctime_ns=1)
        self.nodes[path] = {"info": info, "data": data, "target": target}
        return self.nodes[path]

    def path(self, name, directory):
        if name == "/":
            return ()
        if directory is None or "/" in name or not name:
            raise AssertionError("unexpected unanchored filesystem access")
        return self.handles[directory]["path"] + (name,)

    def error(self, operation, path):
        error = self.errors.get((operation, path))
        if error is not None:
            raise error

    def node(self, path):
        if path not in self.nodes:
            raise FileNotFoundError("DO_NOT_PRINT_MISSING_PATH")
        return self.nodes[path]

    def open(self, name, flags, dir_fd=None):
        path = self.path(name, dir_fd)
        self.error("open", path)
        node = self.node(path)
        fd = next(self.fds)
        self.handles[fd] = {"path": path, "node": node, "offset": 0}
        self.opened.append((path, flags, fd))
        self.after_open(path)
        return fd

    def close(self, fd):
        handle = self.handles.pop(fd)
        self.closed.append(fd)
        self.after_close(handle["path"])
        self.error("close", handle["path"])

    def fstat(self, fd):
        handle = self.handles[fd]
        self.error("fstat", handle["path"])
        return copy.copy(handle["node"]["info"])

    def stat(self, name, dir_fd=None, follow_symlinks=True):
        path = self.path(name, dir_fd)
        self.stats.append((path, follow_symlinks))
        self.error("stat", path)
        if follow_symlinks:
            if path != ("proc", "1", "exe"):
                raise AssertionError("unapproved symlink follow")
            self.error("pid1", path)
            return copy.copy(self.pid1_target["info"])
        return copy.copy(self.node(path)["info"])

    def read(self, fd, maximum):
        handle = self.handles[fd]
        self.error("read", handle["path"])
        start = handle["offset"]
        part = handle["node"]["data"][start:start + min(maximum, self.read_size)]
        handle["offset"] += len(part)
        self.used += len(part)
        self.reads.append((handle["path"], maximum, len(part)))
        self.after_read(handle["path"], part)
        return part

    def readlink(self, name, dir_fd=None):
        if name != "" or dir_fd not in self.handles:
            raise AssertionError("link was not descriptor-pinned")
        handle = self.handles[dir_fd]
        self.error("readlink", handle["path"])
        value = handle["node"]["target"]
        self.links.append(handle["path"])
        self.used += len(value.encode())
        return value

    @contextmanager
    def boundary(self):
        with ExitStack() as stack:
            for name in ("open", "close", "fstat", "stat", "read", "readlink"):
                stack.enter_context(mock.patch.object(module.os, name, side_effect=getattr(self, name)))
            stack.enter_context(mock.patch.object(module.sys, "platform", "linux"))
            # Synthetic flag on non-Linux test interpreters; never passed to OS.
            stack.enter_context(mock.patch.object(module.os, "O_PATH",
                getattr(module.os, "O_PATH", 1 << 28), create=True))
            yield module.Linux()
        if self.handles:
            raise AssertionError("synthetic descriptors leaked")


class PackageTests(unittest.TestCase):
    def parse(self, data):
        return module.package_records(data, lambda: None)

    def test_only_fixed_packages_and_allowed_fields_survive(self):
        raw = record() + record("systemd-container", "1:256.5-1~test")
        raw += b"Package: unrelated\nArchitecture: all\nDescription: PRIVATE_SENTINEL\n more secret text\n\n"
        result = self.parse(raw)
        self.assertEqual(set(result), set(module.PACKAGES))
        self.assertEqual(result["systemd-container"]["version"], "1:256.5-1~test")
        self.assertEqual(set(result["systemd"]), {"status", "version", "package_state", "status_ok"})
        self.assertNotIn(b"PRIVATE_SENTINEL", module.render(result))

    def test_empty_and_absent_records_do_not_invent_versions(self):
        for raw in (b"", record("unrelated")):
            with self.subTest(raw=raw):
                self.assertEqual(self.parse(raw), {name: {"status": "missing"} for name in module.PACKAGES})

    def test_duplicate_fields_records_and_multiarch_target_are_rejected(self):
        for raw in (record().replace(b"Version:", b"vErSiOn: 1\nVersion:"), record() * 2,
                    record() + record(architecture="arm64"),
                    record("unrelated").replace(b"Version:", b"X-Field: a\nx-field: b\nVersion:")):
            with self.subTest(raw=raw), self.assertRaises(module.Rejected):
                self.parse(raw)

    def test_malformed_and_multiline_metadata_is_rejected(self):
        for raw in (b"broken\n", b" leading field\n", record().replace(b"Package:", b"Bad Field:"),
                    record().replace(b"\n", b"\r\n"), record() + b"\x00", record() + b"\xff",
                    record(version="1\n continued"), record(version="secret/token"),
                    record(version="1" * 129), record(version="1-"), record(version="1x:2"),
                    record(state="install ok PRIVATE_SENTINEL"),
                    record().rstrip(b"\n")):
            with self.subTest(raw=raw), self.assertRaises(module.Rejected):
                self.parse(raw)

    def test_excessive_records_fields_paragraph_and_total_size(self):
        cases = [b"".join(record("pkg%d" % n) for n in range(module.RECORD_LIMIT + 1)),
                 record().rstrip(b"\n") + b"\n" + b"".join(
                     ("X-%d: value\n" % n).encode() for n in range(module.FIELD_LIMIT)),
                 record().rstrip(b"\n") + b"\nDescription: " + b"x" * module.PARAGRAPH_LIMIT + b"\n",
                 b"x" * (module.STATUS_LIMIT + 1)]
        for raw in cases:
            with self.subTest(size=len(raw)), self.assertRaises(module.Rejected):
                self.parse(raw)

    def test_parse_deadline_checked_between_records(self):
        calls = []
        def check():
            calls.append(True)
            if len(calls) == 2:
                raise module.Rejected("timeout")
        with self.assertRaises(module.Rejected):
            module.package_records(record() + record("systemd-container"), check)
        self.assertEqual(len(calls), 2)


class InventoryTests(unittest.TestCase):
    def collect(self, fs):
        with fs.boundary() as boundary:
            report, status = module.collect(boundary, clock=fs.clock)
        self.assertEqual(report["bytes_read"], fs.used)
        self.assertLessEqual(report["bytes_read"], module.TOTAL_LIMIT)
        self.assertLessEqual(len(module.render(report)), module.OUTPUT_LIMIT)
        for name in module.AUTHORITY:
            self.assertIs(report[name], False)
        return report, status

    def test_complete_fake_observations_never_qualify_runtime(self):
        fs = Filesystem()
        report, status = self.collect(fs)
        self.assertEqual(status, 0)
        self.assertTrue(report["collection_complete"])
        for role, paths in module.BINARIES.items():
            expected = fs.nodes[paths[0]]["data"]
            self.assertEqual(report["binaries"][role], {"status": "observed", "bytes": len(expected),
                "sha256": hashlib.sha256(expected).hexdigest()})
            self.assertEqual(sum(n for path, _, n in fs.reads if path == paths[0]), len(expected))
        self.assertEqual(report["pid1"], {"status": "observed", "correspondence": "matches-manager"})
        self.assertEqual(len(fs.opened), len(fs.closed))

    def test_unsupported_platform_does_not_try_fixed_paths(self):
        fs = Filesystem()
        with fs.boundary() as boundary, mock.patch.object(module.sys, "platform", "unsupported"):
            report, status = module.collect(boundary, clock=fs.clock)
        self.assertEqual(status, 0)
        self.assertFalse(report["collection_complete"])
        self.assertEqual(fs.opened, [])
        self.assertEqual(report["bytes_read"], 0)
        self.assertTrue(all(row["reason"] == "unsupported" for row in report["binaries"].values()))

    def test_exact_absolute_and_relative_system_aliases(self):
        for absolute in (True, False):
            with self.subTest(absolute=absolute):
                fs = Filesystem()
                for name in ("lib", "bin"):
                    fs.add((name,), stat.S_IFLNK | 0o777, target=("/" if absolute else "") + "usr/" + name)
                report, _ = self.collect(fs)
                self.assertTrue(report["collection_complete"])
                self.assertTrue(set(fs.links) <= {("lib",), ("bin",)})

    def test_fixed_unmerged_directories_are_supported_without_search(self):
        fs = Filesystem()
        fs.add(("lib",), stat.S_IFDIR | 0o755)
        fs.add(("lib", "systemd"), stat.S_IFDIR | 0o755)
        fs.add(("bin",), stat.S_IFDIR | 0o755)
        for role, paths in module.BINARIES.items():
            node = fs.nodes.pop(paths[0])
            fs.nodes[paths[1]] = node
        report, _ = self.collect(fs)
        self.assertTrue(report["collection_complete"])

    def test_different_fixed_candidates_are_ambiguous_even_with_same_bytes(self):
        fs = Filesystem()
        fs.add(("bin",), stat.S_IFDIR | 0o755)
        paths = module.BINARIES["run"]
        fs.add(paths[1], stat.S_IFREG | 0o755, fs.nodes[paths[0]]["data"])
        report, status = self.collect(fs)
        self.assertEqual(status, 0)
        self.assertEqual(report["binaries"]["run"], {"status": "unknown", "reason": "ambiguous"})
        self.assertFalse(any(path in paths for path, _, _ in fs.reads))

    def test_denied_alias_is_not_bypassed_using_other_candidate(self):
        fs = Filesystem()
        fs.errors[("stat", ("bin",))] = PermissionError("PRIVATE_SENTINEL")
        report, _ = self.collect(fs)
        self.assertEqual(report["binaries"]["run"]["reason"], "denied")
        self.assertNotIn(b"PRIVATE_SENTINEL", module.render(report))

    def test_disappearance_between_stat_and_open_is_not_missing_fallback(self):
        fs = Filesystem()
        path = module.BINARIES["run"][0]
        fs.errors[("open", path)] = FileNotFoundError("secret-race")
        report, _ = self.collect(fs)
        self.assertEqual(report["binaries"]["run"], {"status": "unknown", "reason": "changed"})
        self.assertFalse(any(p == path for p, _, _ in fs.reads))

    def test_missing_denied_and_partial_failure_keep_distinct_diagnostics(self):
        for exception, expected in ((FileNotFoundError("secret"), "missing"),
                                    (PermissionError("secret"), "denied"), (OSError("secret"), "unavailable")):
            with self.subTest(expected=expected):
                fs = Filesystem()
                fs.errors[("stat", module.STATUS_PATH)] = exception
                fs.nodes.pop(module.BINARIES["nspawn"][0])
                report, status = self.collect(fs)
                self.assertEqual(status, 0)
                self.assertFalse(report["collection_complete"])
                self.assertEqual(report["packages"]["systemd"]["reason"], expected)
                self.assertEqual(report["binaries"]["nspawn"]["status"], "missing")

    def test_hostile_links_parents_and_file_types_are_not_read(self):
        cases = ("traversal", "foreign-link", "link-owner", "parent-link", "parent-owner",
                 "parent-writable", "leaf-link", "fifo", "directory", "hardlink", "owner", "writable", "no-exec")
        for case in cases:
            with self.subTest(case=case):
                fs = Filesystem()
                path = module.BINARIES["run"][0]
                node = fs.nodes[path]["info"]
                if case == "traversal": fs.add(("bin",), stat.S_IFLNK | 0o777, target="usr/../private")
                if case == "foreign-link": fs.add(("bin",), stat.S_IFLNK | 0o777, target="private")
                if case == "link-owner": fs.nodes[("bin",)]["info"].st_uid = 1234
                if case == "parent-link": fs.add(("usr",), stat.S_IFLNK | 0o777, target="private")
                if case == "parent-owner": fs.nodes[("usr",)]["info"].st_uid = 1234
                if case == "parent-writable": fs.nodes[("usr", "bin")]["info"].st_mode |= 0o020
                if case == "leaf-link": fs.add(path, stat.S_IFLNK | 0o777, target="private")
                if case == "fifo": node.st_mode = stat.S_IFIFO | 0o755
                if case == "directory": node.st_mode = stat.S_IFDIR | 0o755
                if case == "hardlink": node.st_nlink = 2
                if case == "owner": node.st_uid = 1234
                if case == "writable": node.st_mode |= 0o002
                if case == "no-exec": node.st_mode &= ~0o111
                report, _ = self.collect(fs)
                self.assertEqual(report["binaries"]["run"]["status"], "unknown")
                self.assertFalse(any(p == path for p, _, _ in fs.reads))
                self.assertFalse(any("private" in p for p, _, _ in fs.opened))

    def test_missing_or_changed_binary_after_read_never_returns_hash(self):
        for case in ("removed", "replaced", "content", "parent", "link"):
            with self.subTest(case=case):
                fs = Filesystem()
                path = module.BINARIES["run"][0]
                def change(current, part):
                    if current != path or not part:
                        return
                    fs.after_read = lambda *_: None
                    if case == "removed": fs.nodes.pop(path)
                    if case == "replaced": fs.add(path, stat.S_IFREG | 0o755, part)
                    if case == "content": fs.nodes[path]["info"].st_mtime_ns += 1
                    if case == "parent": fs.add(("usr", "bin"), stat.S_IFDIR | 0o755)
                    if case == "link": fs.add(("bin",), stat.S_IFLNK | 0o777, target="usr/bin")
                fs.after_read = change
                report, _ = self.collect(fs)
                self.assertNotIn("sha256", report["binaries"]["run"])
                self.assertFalse(report["collection_complete"])

    def test_short_reads_are_joined_and_charged(self):
        fs = Filesystem()
        fs.read_size = 3
        report, _ = self.collect(fs)
        self.assertTrue(report["collection_complete"])
        self.assertGreater(len(fs.reads), 10)

    def test_partial_read_error_preserves_consumed_byte_count(self):
        fs = Filesystem()
        fs.read_size = 3
        path = module.STATUS_PATH
        def fail(current, part):
            if current == path and part:
                fs.errors[("read", path)] = OSError("PRIVATE_PARTIAL_ERROR")
        fs.after_read = fail
        report, _ = self.collect(fs)
        self.assertEqual(sum(n for p, _, n in fs.reads if p == path), 3)
        self.assertEqual(report["packages"]["systemd"]["reason"], "unavailable")
        self.assertNotIn(b"PRIVATE_PARTIAL_ERROR", module.render(report))

    def test_status_replacement_discards_parsed_package_result(self):
        fs = Filesystem()
        def change(path, part):
            if path == module.STATUS_PATH and part:
                fs.after_read = lambda *_: None
                fs.add(path, stat.S_IFREG | 0o644, part)
        fs.after_read = change
        report, _ = self.collect(fs)
        self.assertEqual(report["packages"]["systemd"], {"status": "unknown", "reason": "changed"})
        self.assertNotIn("version", report["packages"]["systemd"])

    def test_truncated_or_growing_binary_is_rejected(self):
        for difference in (-1, 1):
            with self.subTest(difference=difference):
                fs = Filesystem()
                fs.nodes[module.BINARIES["run"][0]]["info"].st_size += difference
                report, _ = self.collect(fs)
                self.assertEqual(report["binaries"]["run"]["reason"], "changed")

    def test_metadata_oversize_and_rejected_status_reads_are_counted(self):
        for case in ("binary", "status", "malformed"):
            with self.subTest(case=case):
                fs = Filesystem()
                if case == "binary": fs.nodes[module.BINARIES["run"][0]]["info"].st_size = module.BINARY_LIMIT + 1
                if case == "status": fs.nodes[module.STATUS_PATH]["info"].st_size = module.STATUS_LIMIT + 1
                if case == "malformed": fs.add(module.STATUS_PATH, stat.S_IFREG | 0o644, b"secret-invalid\n")
                report, status = self.collect(fs)
                self.assertEqual(status, 1)
                if case != "binary": self.assertNotEqual(report["packages"]["systemd"]["status"], "observed")
                if case == "malformed":
                    self.assertEqual(sum(n for p, _, n in fs.reads if p == module.STATUS_PATH), len(b"secret-invalid\n"))

    def test_pid1_difference_denied_and_missing_are_not_versions(self):
        for case in ("different", "denied", "missing", "deleted"):
            with self.subTest(case=case):
                fs = Filesystem()
                if case == "different": fs.pid1_target = fs.nodes[module.BINARIES["run"][0]]
                if case == "denied": fs.errors[("pid1", ("proc", "1", "exe"))] = PermissionError("secret")
                if case == "missing": fs.nodes.pop(("proc", "1", "exe"))
                if case == "deleted":
                    fs.pid1_target = copy.deepcopy(fs.pid1_target)
                    fs.pid1_target["info"].st_nlink = 0
                report, _ = self.collect(fs)
                self.assertEqual(report["binaries"]["manager"]["status"], "observed")
                self.assertNotEqual(report["pid1"].get("correspondence"), "matches-manager")
                self.assertFalse(any(path[:1] == ("proc",) for path, _, _ in fs.reads))
                self.assertNotIn(("proc", "1", "exe"), fs.links)

    def test_deadline_during_open_read_and_cleanup_closes_all_handles(self):
        for phase in ("open", "read", "close"):
            with self.subTest(phase=phase):
                fs = Filesystem()
                path = module.BINARIES["run"][0]
                def expire(current, *unused):
                    if current == path:
                        fs.clock.now = 11.0
                setattr(fs, "after_" + phase, expire)
                report, status = self.collect(fs)
                self.assertEqual(status, 1)
                self.assertEqual(report["binaries"]["run"]["reason"], "timeout")
                self.assertNotIn("sha256", report["binaries"]["run"])
                self.assertEqual(len(fs.opened), len(fs.closed))

    def test_cleanup_error_cannot_preserve_success_and_other_handles_close(self):
        fs = Filesystem()
        fs.errors[("close", module.BINARIES["run"][0])] = OSError("secret-close")
        report, _ = self.collect(fs)
        self.assertEqual(report["binaries"]["run"]["status"], "unknown")
        self.assertEqual(len(fs.opened), len(fs.closed))

    def test_allowlisted_read_surface_and_no_raw_metadata_output(self):
        fs = Filesystem()
        report, _ = self.collect(fs)
        allowed = {module.STATUS_PATH} | {paths[0] for paths in module.BINARIES.values()}
        self.assertTrue({p for p, _, _ in fs.reads} <= allowed)
        for _, flags, _ in fs.opened:
            self.assertTrue(flags & module.os.O_NOFOLLOW)
            self.assertFalse(flags & (module.os.O_CREAT | module.os.O_WRONLY | module.os.O_RDWR |
                                     module.os.O_TRUNC | module.os.O_APPEND))
        self.assertTrue({p for p, followed in fs.stats if followed} <= {("proc", "1", "exe")})
        raw = module.render(report)
        for forbidden in (b"DO_NOT_PRINT", b"unrelated", b"ignored", b"secret", b"/usr", b"st_ino", b"st_uid"):
            self.assertNotIn(forbidden, raw)


class MainTests(unittest.TestCase):
    @contextmanager
    def entrypoint(self, *, arguments=("collector",), isolated=True, no_site=True,
                   no_bytecode=True, collection_status=0):
        report = {"schema": "nib.runtime-file-inventory/v1", "collection_complete": True,
                  "errors": [], **{name: False for name in module.AUTHORITY}}
        boundary = object()
        factory = mock.Mock(return_value=boundary)
        collect = mock.Mock(return_value=(report, collection_status))
        monotonic = mock.Mock(return_value=1.0)
        output = mock.Mock()
        output.write.side_effect = lambda raw: len(raw)
        output.flush.return_value = None
        fake_sys = types.SimpleNamespace(argv=list(arguments), dont_write_bytecode=no_bytecode,
            flags=types.SimpleNamespace(isolated=isolated, no_site=no_site),
            stdout=types.SimpleNamespace(buffer=output))
        # runpy functions retain their own globals, not the module namespace
        # wrapper. Patch those globals so no entrypoint can reach real collection.
        with mock.patch.dict(module.main.__globals__, {
            "Linux": factory, "collect": collect, "time": types.SimpleNamespace(monotonic=monotonic),
            "sys": fake_sys,
        }):
            yield types.SimpleNamespace(factory=factory, collect=collect, boundary=boundary,
                                        output=output, monotonic=monotonic)

    def emitted_report(self, fixture):
        fixture.output.write.assert_called_once()
        raw = fixture.output.write.call_args.args[0]
        self.assertTrue(raw.endswith(b"\n"))
        self.assertLessEqual(len(raw), module.OUTPUT_LIMIT)
        self.assertNotIn(b"PRIVATE_SENTINEL", raw)
        report = module.json.loads(raw)
        self.assertEqual(report["schema"], "nib.runtime-file-inventory/v1")
        for name in module.AUTHORITY:
            self.assertIs(report[name], False)
        return report

    def assert_collected(self, fixture):
        fixture.factory.assert_called_once_with()
        fixture.collect.assert_called_once_with(fixture.boundary)

    def test_main_preserves_synthetic_collection_exit_and_false_authority(self):
        for status in (0, 1):
            with self.subTest(status=status), self.entrypoint(collection_status=status) as fixture:
                self.assertEqual(module.main(), status)
                self.assert_collected(fixture)
                self.emitted_report(fixture)
                fixture.output.flush.assert_called_once_with()

    def test_invocation_rejection_never_constructs_or_collects_linux(self):
        for options in ({"arguments": ()}, {"arguments": ("collector", "PRIVATE_SENTINEL")},
                        {"isolated": False}, {"no_site": False}, {"no_bytecode": False}):
            with self.subTest(options=options), self.entrypoint(**options) as fixture:
                self.assertEqual(module.main(), 1)
                fixture.factory.assert_not_called()
                fixture.collect.assert_not_called()
                report = self.emitted_report(fixture)
                self.assertIs(report["collection_complete"], False)
                self.assertEqual(report["errors"], [{"field": "collector", "reason": "unavailable"}])
                fixture.output.flush.assert_called_once_with()

    def test_write_exception_returns_nonzero_without_flush_or_retry(self):
        for exception in (OSError, BrokenPipeError, KeyboardInterrupt):
            with self.subTest(exception=exception), self.entrypoint() as fixture:
                fixture.output.write.side_effect = exception("PRIVATE_SENTINEL")
                self.assertEqual(module.main(), 1)
                self.assert_collected(fixture)
                self.emitted_report(fixture)  # Attempted payload, not delivery proof.
                fixture.output.flush.assert_not_called()

    def test_short_or_missing_write_count_returns_nonzero_without_flush(self):
        for outcome in ("zero", "short", "none"):
            with self.subTest(outcome=outcome), self.entrypoint() as fixture:
                fixture.output.write.side_effect = lambda raw: {
                    "zero": 0, "short": len(raw) - 1, "none": None}[outcome]
                self.assertEqual(module.main(), 1)
                self.assert_collected(fixture)
                self.emitted_report(fixture)
                fixture.output.flush.assert_not_called()

    def test_flush_exception_returns_nonzero_without_rewriting_output(self):
        for exception in (OSError, BrokenPipeError, KeyboardInterrupt):
            with self.subTest(exception=exception), self.entrypoint() as fixture:
                fixture.output.flush.side_effect = exception("PRIVATE_SENTINEL")
                self.assertEqual(module.main(), 1)
                self.assert_collected(fixture)
                self.emitted_report(fixture)
                fixture.output.flush.assert_called_once_with()

    def test_flush_crossing_unchanged_deadline_returns_nonzero(self):
        with self.entrypoint() as fixture:
            fixture.monotonic.side_effect = (1.0, 1.0, 11.0)
            self.assertEqual(module.main(), 1)
            self.assert_collected(fixture)
            self.emitted_report(fixture)
            fixture.output.flush.assert_called_once_with()
            self.assertEqual(fixture.monotonic.call_count, 3)


class BudgetAndStaticTests(unittest.TestCase):
    def test_aggregate_remaining_budget_limits_reads_including_rejected_bytes(self):
        fs = Filesystem()
        with fs.boundary() as boundary:
            reader = module.Reader(fs.clock)
            reader.used = module.TOTAL_LIMIT - 7
            result = reader.observe("packages", lambda: boundary.packages(reader))
            self.assertEqual(result["reason"], "read-budget")
            self.assertEqual(reader.used, module.TOTAL_LIMIT)
            self.assertEqual(fs.used, 7)
            self.assertEqual(fs.reads, [(module.STATUS_PATH, 7, 7)])

    def test_oversize_sentinel_counts_and_is_not_yielded(self):
        fs = Filesystem()
        path = module.BINARIES["run"][0]
        with fs.boundary() as boundary:
            reader = module.Reader(fs.clock)
            with boundary.file(path, reader, module.BINARY_LIMIT, True) as (fd, _):
                with self.assertRaises(module.Rejected):
                    list(reader.chunks(fd, 3))
            self.assertEqual(reader.used, 4)
            self.assertEqual(fs.used, 4)

    def test_rejected_link_bytes_and_insufficient_link_budget(self):
        for remaining in (2, module.TOTAL_LIMIT):
            with self.subTest(remaining=remaining):
                fs = Filesystem()
                fs.add(("bin",), stat.S_IFLNK | 0o777, target="private")
                with fs.boundary() as boundary:
                    reader = module.Reader(fs.clock)
                    start = reader.used = module.TOTAL_LIMIT - remaining
                    def attempt():
                        with boundary.directory(("bin",), reader):
                            pass
                    result = reader.observe("run", attempt)
                    self.assertEqual(result["reason"], "read-budget" if remaining == 2 else "unsafe-path")
                    self.assertEqual(reader.used - start, fs.used)
                    self.assertEqual(fs.used, 0 if remaining == 2 else len("private"))

    def test_unexpected_exception_content_and_reason_are_never_rendered(self):
        reader = module.Reader(lambda: 1.0)
        def failure():
            raise module.Rejected("PRIVATE_SENTINEL")
        self.assertEqual(reader.observe("manager", failure), {"status": "unknown", "reason": "internal"})
        self.assertNotIn(b"PRIVATE_SENTINEL", module.render(reader.errors))

    def test_output_ceiling(self):
        with self.assertRaises(module.Rejected):
            module.render({"oversize": "x" * module.OUTPUT_LIMIT})

    def test_static_surface_has_no_process_network_environment_or_config_reads(self):
        tree = ast.parse(SOURCE.read_text())
        imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        imports.update(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
        self.assertEqual(imports, {"contextlib", "email.parser", "hashlib", "json", "os", "re", "stat", "sys", "time"})
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        self.assertFalse(attributes & {"environ", "getenv", "system", "popen", "fork", "execve", "spawn",
            "listdir", "scandir", "walk", "mkdir", "unlink", "chmod", "connect", "socket"})
        writes = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute) and node.func.attr == "write"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(ast.unparse(writes[0].func), "sys.stdout.buffer.write")
        self.assertEqual(module.STATUS_LIMIT, 8 << 20)
        self.assertEqual(module.BINARY_LIMIT, 16 << 20)
        self.assertEqual(module.TOTAL_LIMIT, 64 << 20)
        self.assertEqual(module.OUTPUT_LIMIT, 16 << 10)
        self.assertEqual(module.SECONDS, 10)

    def test_existing_resource_helper_pin_is_unchanged(self):
        raw = (ROOT / "host/probe-build-resources.py").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         "2b740ef5b1ecbc87cb3a078fcbb081017f7db5c9600c9956f97cc52330ad9fdc")


if __name__ == "__main__":
    unittest.main()
