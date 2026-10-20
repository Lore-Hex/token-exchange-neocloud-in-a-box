# SPDX-License-Identifier: Apache-2.0
"""Synthetic files and stdlib children only; never run Docker or import vLLM."""

import copy
import base64
import csv
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).absolute().parent.parent


def source(name, relative):
    module = types.ModuleType(name)
    module.__file__ = str(ROOT / relative)
    exec(compile(Path(module.__file__).read_bytes(), module.__file__, "exec"), module.__dict__)
    return module


descriptor = source("descriptor_test", "scripts/engine_python_descriptor.py")
overlay = source("overlay_descriptor_test", "scripts/engine_overlay.py")
diagnostic = source("diagnostic_descriptor_test", "scripts/engine_image_diagnostic.py")
snapshot = source("snapshot_descriptor_test", "scripts/e2e/snapshot.py")
fixtures = source("descriptor_wheel_test", "scripts/test_engine_overlay.py")


class DescriptorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.OverlayTests.setUpClass()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nib-pyd-", dir="/private/tmp" if sys.platform == "darwin" else None)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.stdlib = "/usr/lib/python3.12"
        self.selected = "/usr/local/lib/python3.12/dist-packages"
        self.dsl = self.selected + "/nvidia_cutlass_dsl/dsl_packages"
        for path in (self.stdlib, self.selected, self.dsl, "/usr/bin", self.stdlib + "/lib-dynload"):
            (self.root / path[1:]).mkdir(parents=True, exist_ok=True)
        self.write("/usr/bin/python3.12", b"synthetic executable never run")
        self.alternatives_chain()
        for name in descriptor.SOURCES:
            self.write(self.stdlib + "/" + name, b"raise RuntimeError('never imported')\n")
        for name in diagnostic.HOOKS:
            self.write(self.selected + "/" + name, b"raise RuntimeError('never executed')\n")
        values = ["/usr/bin/python3", "/usr/bin/python3", "/usr", "/usr", "/usr", "/usr",
                  self.stdlib, self.stdlib, self.selected, self.selected, "/usr/bin", self.stdlib + "/lib-dynload", None]
        self.facts = {"status": "observed", "locations": [descriptor.location(name, value, name == "pycache_prefix")
            for name, value in zip(descriptor.FIXED_LOCATIONS, values, strict=True)],
            "python": "3.12.3", "cache_tag": "cpython-312", "build": {"SOABI": "cpython-312-x86_64-linux-gnu",
                "EXT_SUFFIX": ".cpython-312-x86_64-linux-gnu.so", "MULTIARCH": "x86_64-linux-gnu", "Py_DEBUG": 0, "Py_ENABLE_SHARED": 1}, "flags": dict.fromkeys(
                ("isolated", "no_site", "ignore_environment", "no_user_site", "safe_path", "dont_write_bytecode"), True),
            "extension_suffixes": [".cpython-312-x86_64-linux-gnu.so", ".abi3.so", ".so"], "multiprocessing_flags": ["-B", "-S", "-I"]}
        self.contract = {"diagnostic_mode": "python-descriptor", "base_image": overlay.BASE_IMAGE,
                         "official_config": {"sha256": overlay.PACKAGING_POLICY["config_sha256"]},
                         "descriptor_preparation": {"stages": 5, "candidate_bundle_sha256": "a" * 64,
                                                    "context_sha256": "b" * 64, "image_writes": False},
                         "descriptor_source_pins": {"revision": overlay.BASE_REVISION, "files": {"vllm/fixture.py": overlay.digest(b"source")}}}
        self.write(self.selected + "/vllm/fixture.py", b"source")

    def write(self, name, data):
        path = self.root / name[1:]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def alternatives_chain(self):
        (self.root / "etc/alternatives").mkdir(parents=True, exist_ok=True)
        for name, target in descriptor.INTERPRETER_CHAIN:
            path = self.root / name[1:]
            path.unlink(missing_ok=True)
            path.symlink_to(target)

    def test_exact_interpreter_chain_is_scoped_and_validated_without_execution(self):
        self.alternatives_chain()
        with overlay.directory(self.root) as fd:
            fs = descriptor.Filesystem(overlay, fd, self.contract)
            with self.assertRaises(descriptor.ObservationRejected):
                fs.resolve("/usr/bin/python3")
            self.assertFalse(descriptor.public_path("/etc/alternatives/python3"))
            for name in ("executable", "base_executable"):
                row = fs.file(name, "/usr/bin/python3")
                self.assertEqual((row["status"], row["resolved"], row["sha256"]),
                    ("read", "/usr/bin/python3.12", overlay.digest(b"synthetic executable never run")))
                self.assertEqual(row["links"], descriptor.interpreter_links())
            self.assertEqual(fs.file("site.py", "/usr/bin/python3")["reason"], "unsafe-target")
        report = self.collect()
        descriptor.validate_report(overlay, report, self.contract)
        self.assertNotIn("executable-unverified", report["canaries"]["blockers"])
        for change in ({"resolved": "/usr/bin/other"}, {"links": descriptor.interpreter_links()[:1]},
                       {"links": [{"path": "/usr/bin/python3", "target": "/etc/SYNTHETIC_PRIVATE_VALUE"}]}):
            changed = copy.deepcopy(report)
            changed["files"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                descriptor.validate_report(overlay, changed, self.contract)

    def test_interpreter_chain_unknown_image_config_mode_and_role_never_read_terminal(self):
        self.alternatives_chain()
        with overlay.directory(self.root) as fd, mock.patch.object(overlay.Tree, "read", side_effect=AssertionError("no terminal read")):
            for contract in (None, {}, {**self.contract, "base_image": "unknown"},
                             {**self.contract, "official_config": {"sha256": "0" * 64}},
                             {**self.contract, "official_config": None}, {**self.contract, "diagnostic_mode": "integrity"}):
                row = descriptor.Filesystem(overlay, fd, contract).file("executable", "/usr/bin/python3")
                self.assertEqual((row["status"], row["reason"]), ("rejected", "unsafe-target"))
            fs = descriptor.Filesystem(overlay, fd, self.contract)
            (self.root / "usr/bin/alias").symlink_to("/etc/alternatives/python3")
            for name, path in (("other", "/usr/bin/python3"), ("executable", "/usr/bin/alias"),
                               ("executable", "/etc/alternatives/python3")):
                self.assertEqual(fs.file(name, path)["status"], "rejected")

    def test_interpreter_chain_literal_mismatch_loop_extra_hop_and_dangling_are_sanitized(self):
        self.alternatives_chain()
        for name, _ in descriptor.INTERPRETER_CHAIN:
            for target in ("/home/SYNTHETIC_PRIVATE_VALUE", "/etc/alternatives/private", "/usr/bin/python3",
                           "/usr/bin/extra", "/usr/bin/../bin/python3.12"):
                path = self.root / name[1:]
                path.unlink()
                path.symlink_to(target)
                with self.subTest(name=name, target=target), overlay.directory(self.root) as fd, \
                        mock.patch.object(overlay.Tree, "read", side_effect=AssertionError("no terminal read")):
                    row = descriptor.Filesystem(overlay, fd, self.contract).file("executable", "/usr/bin/python3")
                    self.assertEqual(row["status"], "rejected")
                    self.assertFalse(row["links"])
                    self.assertIsNone(row["sha256"])
                    self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(row))
                self.alternatives_chain()
        terminal = self.root / "usr/bin/python3.12"
        terminal.unlink()
        with overlay.directory(self.root) as fd:
            row = descriptor.Filesystem(overlay, fd, self.contract).file("executable", "/usr/bin/python3")
        self.assertEqual(row["reason"], "interpreter-chain-unavailable")
        terminal.symlink_to("/usr/bin/extra")
        with overlay.directory(self.root) as fd:
            row = descriptor.Filesystem(overlay, fd, self.contract).file("executable", "/usr/bin/python3")
        self.assertEqual(row["reason"], "interpreter-chain-mismatch")

    def test_interpreter_chain_terminal_fifo_hardlink_and_size_fail_before_content_read(self):
        self.alternatives_chain()
        terminal = self.root / "usr/bin/python3.12"
        for kind, reason in (("fifo", "not-regular"), ("hardlink", "link-count"), ("size", "file-size-limit")):
            terminal.unlink()
            if kind == "fifo":
                os.mkfifo(terminal)
            elif kind == "hardlink":
                os.link(self.root / "usr/lib/python3.12/site.py", terminal)
            else:
                terminal.write_bytes(b"large")
            with self.subTest(kind=kind), overlay.directory(self.root) as fd, \
                    mock.patch.dict(descriptor.LIMITS, file_bytes=1), \
                    mock.patch.object(overlay.Tree, "read", side_effect=AssertionError("no terminal read")):
                row = descriptor.Filesystem(overlay, fd, self.contract).file("executable", "/usr/bin/python3")
                self.assertEqual(row["reason"], reason)
                self.assertIsNone(row["sha256"])

    def test_interpreter_chain_ancestors_are_nofollow_and_failures_prevent_canaries(self):
        self.alternatives_chain()
        parent = self.root / "etc/alternatives"
        moved = self.root / "etc/synthetic"
        parent.rename(moved)
        parent.symlink_to("synthetic")
        report = self.collect()
        row = report["files"][0]
        self.assertEqual(row["reason"], "interpreter-chain-ancestor")
        processes = mock.Mock()
        children = descriptor.canaries(overlay, self.facts, report["scans"], row, processes, self.root)
        processes.execute_process_group.assert_not_called()
        self.assertEqual(children["status"], "not-run")
        self.assertIn("executable-unverified", children["blockers"])
        parent.unlink()
        parent.write_bytes(b"not a directory")
        with overlay.directory(self.root) as fd:
            row = descriptor.Filesystem(overlay, fd, self.contract).file("executable", "/usr/bin/python3")
        self.assertEqual(row["reason"], "interpreter-chain-ancestor")

    def test_interpreter_chain_rechecks_links_ancestors_and_terminal_after_hash(self):
        self.alternatives_chain()
        original = overlay.Tree.read
        for target in ("usr/bin/python3", "etc/alternatives/python3", "usr/bin/python3.12", "etc/alternatives"):
            def raced(tree, name, **kwargs):
                item = original(tree, name, **kwargs)
                node = self.root / target
                moved = node.with_name(node.name + "-old")
                node.rename(moved)
                if moved.is_symlink():
                    node.symlink_to(os.readlink(moved))
                    moved.unlink()
                elif moved.is_file():
                    node.write_bytes(moved.read_bytes())
                    moved.unlink()
                else:
                    node.mkdir()
                    for child in moved.iterdir():
                        child.rename(node / child.name)
                    moved.rmdir()
                return item
            with self.subTest(target=target), overlay.directory(self.root) as fd, \
                    mock.patch.object(overlay.Tree, "read", new=raced):
                row = descriptor.Filesystem(overlay, fd, self.contract).file("executable", "/usr/bin/python3")
                self.assertEqual((row["status"], row["stage"]), ("rejected", "identity"))
                self.assertIsNone(row["sha256"])
                self.assertFalse(row["links"])

    def test_interpreter_chain_read_errors_do_not_publish_arbitrary_targets_or_diagnostics(self):
        self.alternatives_chain()
        for operation in ("readlink", "stat", "open"):
            original = getattr(descriptor.os, operation)
            def denied(path, *args, **kwargs):
                if path == "alternatives" or (path == "python3" and operation == "readlink"):
                    raise PermissionError("SYNTHETIC_PRIVATE_VALUE")
                return original(path, *args, **kwargs)
            with self.subTest(operation=operation), overlay.directory(self.root) as fd, \
                    mock.patch.object(descriptor.os, operation, side_effect=denied):
                row = descriptor.Filesystem(overlay, fd, self.contract).file("executable", "/usr/bin/python3")
                self.assertEqual(row["status"], "rejected")
                self.assertIsNone(row["sha256"])
                self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(row))

    def collect(self):
        with mock.patch.object(descriptor, "runtime_facts", return_value=copy.deepcopy(self.facts)), \
                mock.patch.object(descriptor.platform, "system", return_value="Linux"), \
                mock.patch.object(descriptor.platform, "machine", return_value="x86_64"), \
                mock.patch.object(descriptor.sys, "version_info", (3, 12)):
            return descriptor.collect(overlay, diagnostic, self.contract, filesystem_root=str(self.root))

    def test_bounded_source_hashes_links_and_partial_independent_sections(self):
        report = self.collect()
        descriptor.validate_report(overlay, report, self.contract)
        self.assertEqual(report["collection"], "partial")
        self.assertEqual(report["overlay_admission"], "not-evaluated")
        executable = report["files"][0]
        self.assertEqual(executable["resolved"], "/usr/bin/python3.12")
        self.assertEqual(executable["links"], descriptor.interpreter_links())
        self.assertEqual(executable["sha256"], overlay.digest(b"synthetic executable never run"))
        self.assertEqual(report["scans"][2]["status"], "pass")
        self.assertTrue(report["worker_sources"][0]["matches"])
        self.assertEqual(report["preparation"]["stages"], 5)
        self.assertFalse(report["preparation"]["image_writes"])
        self.assertEqual(report["packaging"]["status"], "rejected")

    def test_source_hash_mismatch_is_reported_without_false_match(self):
        self.write(self.selected + "/vllm/fixture.py", b"changed")
        report = self.collect()
        self.assertFalse(report["worker_sources"][0]["matches"])
        self.assertEqual(report["worker_sources"][0]["status"], "read")
        changed = copy.deepcopy(report)
        changed["worker_sources"][0]["matches"] = True
        with self.assertRaises(ValueError):
            descriptor.validate_report(overlay, changed, self.contract)

    def test_no_content_reads_or_link_following_during_cache_scan(self):
        self.write(self.selected + "/module.py", b"raise AssertionError('do not import')")
        self.write(self.selected + "/__pycache__/module.cpython-312.pyc", b"forged bytecode")
        self.write(self.selected + "/orphan.pyc", b"forged bytecode")
        self.write(self.selected + "/__pycache__/dotted.name.cpython-312.pyc", b"forged bytecode")
        self.write(self.selected + "/dotted.py", b"source")
        self.write(self.selected + "/dotted.name.py", b"source")
        self.write(self.dsl + "/module.py", b"never imported")
        os.mkfifo(self.root / self.selected[1:] / "fifo")
        (self.root / self.selected[1:] / "link").symlink_to("/home/SYNTHETIC_PRIVATE_VALUE")
        with overlay.directory(self.root) as fd, mock.patch.object(overlay.Tree, "read", side_effect=AssertionError("metadata only")):
            row, imports = descriptor.scan(descriptor.Filesystem(overlay, fd), diagnostic, "selected", self.selected)
        self.assertEqual(row["status"], "pass")
        self.assertEqual(row["bytecode"], dict(total=3, with_source=1, without_source=1, ambiguous=1))
        self.assertEqual(row["types"]["special"], 1)
        self.assertEqual(row["types"]["symlink"], 1)
        self.assertIn("module", imports)
        report = self.collect()
        self.assertIn("module", report["collisions"][2]["sample"])
        self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(report))

    def test_reader_rejects_fifo_hardlink_escape_loop_and_size_before_open(self):
        fifo = "/usr/lib/pipe"
        os.mkfifo(self.root / fifo[1:])
        os.link(self.root / "usr/bin/python3.12", self.root / "usr/bin/hardlink")
        (self.root / "usr/lib/escape").symlink_to("/home/SYNTHETIC_PRIVATE_VALUE")
        (self.root / "usr/lib/loop").symlink_to("loop")
        with overlay.directory(self.root) as fd:
            fs = descriptor.Filesystem(overlay, fd)
            for path in (fifo, "/usr/bin/hardlink", "/usr/lib/escape", "/usr/lib/loop", "/home/SYNTHETIC_PRIVATE_VALUE"):
                with self.subTest(path=path):
                    row = fs.file("test", path)
                    self.assertEqual(row["status"], "rejected")
                    self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(row))
            with mock.patch.dict(descriptor.LIMITS, file_bytes=1), mock.patch.object(overlay.Tree, "read", side_effect=AssertionError("must reject before read")):
                self.assertEqual(fs.file("test", self.stdlib + "/site.py")["status"], "rejected")

    def test_file_rejection_stages_observe_only_bounded_stat_fields(self):
        self.write("/usr/lib/plain", b"plain")
        os.mkfifo(self.root / "usr/lib/pipe")
        os.link(self.root / "usr/lib/plain", self.root / "usr/lib/hardlink")
        for name, target in (("dangling", "missing"), ("loop", "loop"), ("escape", "/home/SYNTHETIC_PRIVATE_VALUE")):
            (self.root / "usr/lib" / name).symlink_to(target)
        cases = {"/home/SYNTHETIC_PRIVATE_VALUE": "unsafe-path", "/usr/lib/missing": "confirmed-absent",
                 "/usr/missing/child": "ancestor-absent", "/usr/lib/dangling": "dangling-link",
                 "/usr/lib/loop": "link-limit", "/usr/lib/escape": "unsafe-target",
                 "/usr/lib/pipe": "not-regular", "/usr/lib": "not-regular",
                 "/usr/lib/plain": "link-count", "/usr/lib/plain/child": "ancestor-not-directory"}
        with overlay.directory(self.root) as fd, mock.patch.object(overlay.Tree, "read", side_effect=AssertionError("no rejected-file read")):
            fs = descriptor.Filesystem(overlay, fd)
            for path, reason in cases.items():
                with self.subTest(reason=reason, path=path):
                    row = fs.file("test", path)
                    self.assertEqual(row["reason"], reason)
                    self.assertEqual(row["stage"], descriptor.FILE_REASON_STAGES[reason])
                    self.assertEqual(row["status"], "absent" if reason == "confirmed-absent" else "rejected")
                    self.assertIsNone(row["sha256"])
                    self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(row))
                    self.assertEqual(set(row["observed"]), {"type", "mode", "size", "nlink"})
            self.assertEqual(fs.file("test", "/usr/lib/pipe")["observed"]["type"], "fifo")
            self.assertEqual(fs.file("test", "/usr/lib/plain")["observed"]["nlink"], 2)
            self.assertEqual(fs.file("test", "/usr/lib/missing")["observed"], descriptor.observed_stat())
            path = self.stdlib + "/site.py"
            for limits, reason in (({"file_bytes": 1}, "file-size-limit"), ({"hashed_bytes": 1}, "total-size-limit")):
                with mock.patch.dict(descriptor.LIMITS, limits):
                    row = fs.file("test", path)
                    self.assertEqual(row["reason"], reason)
                    self.assertEqual(row["observed"]["type"], "regular")
            fs.deadline = 0
            self.assertEqual(fs.file("test", path)["reason"], "time-limit")

    def test_file_syscall_failures_and_identity_races_keep_fixed_reasons(self):
        secret = "SYNTHETIC_PRIVATE_VALUE"
        with overlay.directory(self.root) as fd:
            path = self.stdlib + "/site.py"
            fs = descriptor.Filesystem(overlay, fd)
            original_stat, original_open = descriptor.os.stat, descriptor.os.open
            def denied(part, **kwargs):
                if part == "site.py":
                    raise PermissionError(secret)
                return original_stat(part, **kwargs)
            def open_denied(part, *args, **kwargs):
                if part == "usr":
                    raise PermissionError(secret)
                return original_open(part, *args, **kwargs)
            def check(reason, patch, target=path):
                with patch:
                    row = descriptor.Filesystem(overlay, fd).file("test", target)
                self.assertEqual(row["reason"], reason)
                self.assertEqual(row["stage"], descriptor.FILE_REASON_STAGES[reason])
                self.assertNotIn(secret, json.dumps(row))
                self.assertIsNone(row["sha256"])
                return row
            check("stat-failed", mock.patch.object(descriptor.os, "stat", side_effect=denied))
            check("directory-open-failed", mock.patch.object(descriptor.os, "open", side_effect=open_denied))
            check("link-read-failed", mock.patch.object(descriptor.os, "readlink", side_effect=OSError(secret)), "/usr/bin/python3")
            check("resolution-failed", mock.patch.object(descriptor.Filesystem, "resolve", side_effect=OSError(secret)))
            for exception, reason in ((ValueError(secret), "read-rejected"), (OSError(secret), "read-failed")):
                row = check(reason, mock.patch.object(overlay.Tree, "read", side_effect=exception))
                self.assertEqual(row["observed"]["type"], "regular")
            first = fs.resolve(path)
            check("identity-recheck-failed", mock.patch.object(descriptor.Filesystem, "resolve", side_effect=[first, OSError(secret)]))
            other = fs.resolve("/usr/bin/python3.12")
            check("identity-changed", mock.patch.object(descriptor.Filesystem, "resolve", side_effect=[first, other]))
            hits = 0
            def appeared(part, **kwargs):
                nonlocal hits
                if part == "site.py":
                    hits += 1
                    if hits == 1:
                        raise FileNotFoundError(secret)
                return original_stat(part, **kwargs)
            check("absence-unconfirmed", mock.patch.object(descriptor.os, "stat", side_effect=appeared))

    def test_optional_absence_is_role_and_path_based_not_site_index(self):
        optional = self.stdlib + "/dist-packages"
        self.facts["locations"].append(descriptor.location("site_root_0", optional))
        report = self.collect()
        row = report["scans"][-1]
        self.assertEqual((row["status"], row["reason"]), ("absent", "confirmed-absent"))
        self.assertTrue(descriptor.scan_acceptable(row, report["scans"], self.facts))
        self.assertNotIn("scan:site_root_0", report["canaries"]["blockers"])
        for change in ({"resolved": optional}, {"mode": 0o755}, {"regular_bytes": 1}):
            changed = copy.deepcopy(report)
            changed["scans"][-1].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                descriptor.validate_report(overlay, changed, self.contract)
        for role, path in (("selected", self.selected), ("stdlib", self.stdlib), ("dynload", self.stdlib + "/lib-dynload"), ("cutlass", self.dsl)):
            absent = {**descriptor.scan_report(role, path), "status": "absent", "reason": "confirmed-absent"}
            self.assertFalse(descriptor.scan_acceptable(absent, [absent], self.facts))
            facts = copy.deepcopy(self.facts)
            facts["locations"][-1]["path"] = path
            alias = {**absent, "name": "site_root_0"}
            self.assertFalse(descriptor.scan_acceptable(alias, [alias], facts))
        for path in ("/usr", "/usr/lib", self.selected):
            facts = copy.deepcopy(self.facts)
            facts["locations"][-1]["path"] = path
            self.assertFalse(descriptor.scan_acceptable({**row, "path": path}, [], facts))
        contradiction = {**report["scans"][0], "resolved": optional}
        self.assertFalse(descriptor.scan_acceptable(row, [row, contradiction], self.facts))
        for reason in ("dangling-link", "ancestor-absent", "absence-unconfirmed", "stat-failed"):
            self.assertFalse(descriptor.scan_acceptable({**row, "status": "rejected", "reason": reason}, [], self.facts))
        (self.root / optional[1:]).symlink_to("missing")
        report = self.collect()
        self.assertEqual(report["scans"][-1]["reason"], "dangling-link")
        self.assertIn("scan:site_root_0", report["canaries"]["blockers"])

    def test_all_prerequisite_blockers_prevent_any_execution(self):
        self.facts["status"] = "rejected"
        self.facts["flags"]["no_site"] = False
        for row in self.facts["locations"]:
            if row["name"] == "pycache_prefix":
                row.update(path="/usr/cache", status="observed")
            if row["name"] == "platlib":
                row["path"] = "/usr/other"
            if row["name"] == "dynload":
                row.update(path=None, status="rejected")
        executable = self.root / "usr/bin/python3.12"
        executable.unlink()
        os.mkfifo(executable)
        report = self.collect()
        expected = ["executable-unverified", "location:dynload", "proposed-paths", "pycache-prefix", "runtime-flags",
                    "runtime-unobserved", "scan:dynload", "split-library-roots", "unavailable:payload", "unavailable:processes"]
        self.assertEqual(report["canaries"]["blockers"], sorted(expected))
        processes = mock.Mock()
        result = descriptor.canaries(overlay, self.facts, report["scans"], report["files"][0], processes, self.root)
        processes.execute_process_group.assert_not_called()
        self.assertEqual(result["blockers"], sorted(expected[:-2]))
        self.assertEqual(result["status"], "not-run")
        self.assertIsNone(result["path_sha256"])
        report["canaries"] = result
        descriptor.validate_report(overlay, report, self.contract)
        for mutate in (lambda r: r["canaries"]["blockers"].pop(),
                       lambda r: r["canaries"]["attempts"]["fresh"].update(status="failed"),
                       lambda r: r["canaries"].update(path_sha256="0" * 64)):
            changed = copy.deepcopy(report)
            mutate(changed)
            with self.assertRaises(ValueError):
                descriptor.validate_report(overlay, changed, self.contract)

    def test_canary_attempts_preserve_success_without_inferred_child_results(self):
        base = self.collect()
        digest = overlay.digest(overlay.json_bytes(descriptor.proposed_paths(self.facts)))
        state = {"flags": dict.fromkeys(("isolated", "no_site", "dont_write_bytecode", "ignore_environment"), True),
                 "path_sha256": digest, "before_unpickle": True, "no_engine_modules": True}
        children = {key: copy.deepcopy(state) for key in ("fork", "spawn", "forkserver")}
        children["spawn"]["grandchild"] = copy.deepcopy(state)
        good = [subprocess.CompletedProcess([], 0, overlay.json_bytes(data), b"") for data in (state, children)]
        secret = b"SYNTHETIC_PRIVATE_VALUE"
        failures = [(OSError(secret.decode()), "launch-or-io-failed"),
                    (subprocess.TimeoutExpired(secret.decode(), 45, output=secret), "time-limit"),
                    (ValueError(secret.decode()), "execution-rejected"),
                    (subprocess.CompletedProcess([], 1, secret, secret), "nonzero-exit"),
                    (subprocess.CompletedProcess([], 0, secret, b""), "output-rejected"),
                    (subprocess.CompletedProcess([], 0, b"{}", b""), "output-rejected"),
                    (subprocess.CompletedProcess([], 0, good[1].stdout, secret), "output-rejected")]
        for failure, reason in failures:
            for failed in (0, 1):
                with self.subTest(reason=reason, failed=failed):
                    outputs = list(good)
                    outputs[failed] = failure
                    processes = mock.Mock()
                    processes.execute_process_group.side_effect = outputs
                    result = descriptor.canaries(overlay, self.facts, base["scans"], base["files"][0], processes, self.root)
                    self.assertEqual(processes.execute_process_group.call_count, 2)
                    self.assertEqual(result["status"], "failed")
                    key = ("fresh", "children")[failed]
                    self.assertEqual(result["attempts"][key], dict(status="failed", reason=reason))
                    self.assertEqual(result["fresh"], None if failed == 0 else state)
                    for key in ("fork", "spawn", "grandchild", "forkserver"):
                        self.assertEqual(result[key], None if failed == 1 else state)
                    self.assertNotIn(secret.decode(), json.dumps(result))
                    report = {**base, "canaries": result}
                    descriptor.validate_report(overlay, report, self.contract)
                    result[("fresh", "fork")[failed]] = state
                    with self.assertRaises(ValueError):
                        descriptor.validate_report(overlay, report, self.contract)
        processes = mock.Mock()
        processes.execute_process_group.side_effect = good
        result = descriptor.canaries(overlay, self.facts, base["scans"], base["files"][0], processes, self.root)
        self.assertEqual(result["status"], "pass")
        descriptor.validate_report(overlay, {**base, "canaries": result}, self.contract)
        for call in processes.execute_process_group.call_args_list:
            self.assertEqual(call.args[0][1:4], ["-I", "-S", "-B"])
            self.assertEqual((call.kwargs["timeout"], call.kwargs["max_output_bytes"]), (45, 8192))

    def test_file_observation_validator_rejects_forged_stages_and_metadata(self):
        report = self.collect()
        for change in ({"stage": "read"}, {"reason": "SYNTHETIC_PRIVATE_VALUE"},
                       {"observed": {"type": "regular", "mode": 0o644, "size": -1, "nlink": 1}},
                       {"observed": {"type": None, "mode": 0o644, "size": 1, "nlink": 1}},
                       {"observed": {"type": "regular", "mode": 0o644, "size": 1, "nlink": 1, "inode": 42}}):
            modified = copy.deepcopy(report)
            modified["files"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                descriptor.validate_report(overlay, modified, self.contract)

    def test_source_failure_preserves_other_sections_and_rejects_complete_claim(self):
        path = self.root / (self.stdlib + "/site.py")[1:]
        path.unlink()
        os.mkfifo(path)
        report = self.collect()
        self.assertEqual(next(row for row in report["files"] if row["name"] == "site.py")["status"], "rejected")
        self.assertEqual(report["files"][0]["status"], "read")
        report["collection"] = "pass"
        with self.assertRaises(ValueError):
            descriptor.validate_report(overlay, report, self.contract)

    def test_scan_failure_preserves_counts_and_cannot_claim_complete(self):
        self.write(self.selected + "/a.py", b"source")
        with overlay.directory(self.root) as fd, mock.patch.dict(descriptor.LIMITS, scan_entries=1):
            row, _ = descriptor.scan(descriptor.Filesystem(overlay, fd), diagnostic, "selected", self.selected)
        self.assertEqual(row["status"], "partial")
        self.assertEqual(row["entries"], 1)

    def test_validator_rejects_paths_counts_unknown_fields_and_false_canaries(self):
        original = self.collect()
        mutations = [lambda r: r.update(extra="SYNTHETIC_PRIVATE_VALUE"),
                     lambda r: r["runtime"]["locations"][0].update(path="/home/SYNTHETIC_PRIVATE_VALUE"),
                     lambda r: r["scans"][0].update(entries=-1),
                     lambda r: r["files"][0].update(path="/usr/bin/unrelated"),
                     lambda r: r["scans"][0].update(path="/usr/lib/unrelated"),
                     lambda r: r["canaries"].update(status="pass", reason=None),
                     lambda r: r["preparation"].update(stages=4),
                     lambda r: r.update(overlay_admission="accepted")]
        for mutate in mutations:
            report = copy.deepcopy(original)
            mutate(report)
            with self.subTest(mutate=mutate), self.assertRaises((ValueError, TypeError)):
                descriptor.validate_report(overlay, report, self.contract)

    def test_packaging_uses_real_inventory_record_checks_without_removal(self):
        fixture = fixtures.OverlayTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with fixture.packaging_console():
            name = "vllm/__pycache__/envs.cpython-312.pyc"
            fixture.write(name, b"opaque bytecode never interpreted")
            shutil.copytree(fixture.root, self.root / "usr/site")
            shutil.copyfile(fixture.work / "bin/vllm", self.root / "usr/bin/vllm")
        # Synthetic foreign ELF bytes, never published/vendor assets or loaded.
        stub = bytearray(64)
        stub[:7], stub[16:24] = b"\x7fELF\x02\x01\x01", b"\x03\x00\xb7\x00\x01\x00\x00\x00"
        policy = {**overlay.CUTLASS_STUB_POLICY, "sha256": overlay.digest(stub), "bytes": len(stub)}
        for path in policy["paths"]:
            self.write("/usr/site/" + path, stub)
        contract = {**fixture.contract, "diagnostic_inventory_policy": overlay.DIAGNOSTIC_INVENTORY_POLICY,
                    "diagnostic_nonhost_policy": policy}
        paths = {"purelib": "/usr/site", "scripts": "/usr/bin"}
        with mock.patch.dict(overlay.CUTLASS_STUB_POLICY, policy), overlay.directory(self.root) as fd:
            result = descriptor.packaging_observations(descriptor.Filesystem(overlay, fd), contract, paths)
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["entrypoints"], overlay.PACKAGING_POLICY["entrypoints"])
            self.assertEqual(result["removal_count"], 1)
            self.assertEqual(result["removal_bytes"], len(b"opaque bytecode never interpreted"))
            self.assertTrue((self.root / "usr/site" / name).is_file())
            self.assertEqual(len(result["classified_nonhost_assets"]), 4)
            for hook in diagnostic.HOOKS:
                self.write("/usr/site/" + hook, b"never executed")
            (self.root / "usr/site/nvidia_cutlass_dsl/dsl_packages").mkdir()
            for location in self.facts["locations"]:
                if location["name"] in ("purelib", "platlib"):
                    location["path"] = "/usr/site"
            self.contract = {**contract, **self.contract, "descriptor_source_pins": {"revision": overlay.BASE_REVISION,
                "files": {"vllm/envs.py": overlay.digest((self.root / "usr/site/vllm/envs.py").read_bytes())}}}
            proposed = [self.stdlib, self.stdlib + "/lib-dynload", "/usr/site/nvidia_cutlass_dsl/dsl_packages", "/usr/site"]
            state = {"flags": dict.fromkeys(("isolated", "no_site", "dont_write_bytecode", "ignore_environment"), True),
                     "path_sha256": overlay.digest(overlay.json_bytes(proposed)), "before_unpickle": True, "no_engine_modules": True}
            children = {**descriptor.canary_report(), "status": "pass", "reason": None, "path_sha256": state["path_sha256"],
                        "available": {"processes": True, "payload": True},
                        "attempts": {key: {"status": "pass", "reason": None} for key in ("fresh", "children")},
                        **{key: copy.deepcopy(state) for key in ("fresh", "fork", "spawn", "grandchild", "forkserver")}}
            self.facts["locations"].append(descriptor.location("site_root_0", self.stdlib + "/dist-packages"))
            with mock.patch.object(descriptor, "canaries", return_value=children):
                complete = self.collect()
            self.assertEqual(complete["collection"], "pass")
            self.assertEqual(complete["overlay_admission"], "not-evaluated")
            complete["canaries"]["path_sha256"] = "0" * 64
            with self.assertRaises(ValueError):
                descriptor.validate_report(overlay, complete, self.contract)
            record_path = self.root / "usr/site" / fixture.dist / "RECORD"
            version_path = self.root / "usr/site/vllm/_version.py"
            record_data, version_data = record_path.read_bytes(), version_path.read_bytes()
            for corrupt in ("record", "version"):
                with self.subTest(parser=corrupt):
                    if corrupt == "record":
                        record_path.write_bytes(b'"SYNTHETIC_PRIVATE_VALUE')
                    else:
                        data = b"__version__ = ( # SYNTHETIC_PRIVATE_VALUE\n"
                        version_path.write_bytes(data)
                        rows = list(csv.reader(io.StringIO(record_data.decode())))
                        for row in rows:
                            if row[0] == "vllm/_version.py":
                                row[1:] = ["sha256=" + base64.urlsafe_b64encode(bytes.fromhex(overlay.digest(data))).decode().rstrip("="), str(len(data))]
                        out = io.StringIO()
                        csv.writer(out).writerows(rows)
                        record_path.write_text(out.getvalue())
                    partial = self.collect()
                    self.assertEqual(partial["collection"], "partial")
                    self.assertEqual(partial["packaging"]["status"], "rejected")
                    self.assertEqual(partial["packaging"]["reason"], "packaging-rejected")
                    self.assertEqual(partial["files"][0]["status"], "read")
                    self.assertEqual(partial["scans"][0]["status"], "pass")
                    self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(partial))
                    record_path.write_bytes(record_data)
                    version_path.write_bytes(version_data)
            for bad in ("vllm/legacy.pyc", "vllm/__pycache__/orphan.cpython-312.pyc"):
                item = self.write("/usr/site/" + bad, b"opaque")
                rejected = descriptor.packaging_observations(descriptor.Filesystem(overlay, fd), contract, paths)
                self.assertEqual(rejected["status"], "rejected")
                self.assertEqual(rejected["inventory"], "pass")
                self.assertTrue(rejected["entrypoints_match"])
                self.assertIsNone(rejected["removal_plan_sha256"])
                item.unlink()
            (self.root / "usr/site" / policy["paths"][0]).unlink()
            self.assertEqual(descriptor.packaging_observations(descriptor.Filesystem(overlay, fd), contract, paths)["inventory"], "rejected")

    def test_read_bound_only_tightens_and_rejects_growth_after_initial_stat(self):
        path = self.write("/usr/lib/small.py", b"a")
        with overlay.directory(self.root) as fd:
            tree = overlay.Tree(fd)
            self.assertEqual(tree.read("usr/lib/small.py", max_bytes=1), b"a")
            for bound in (-1, True, overlay.MAX_SMALL + 1):
                with self.assertRaises(ValueError):
                    tree.read("usr/lib/small.py", max_bytes=bound)
            original = overlay.Tree.read
            def grow(tree, name, **kwargs):
                path.write_bytes(b"larger")
                return original(tree, name, **kwargs)
            with mock.patch.object(overlay.Tree, "read", grow):
                self.assertEqual(descriptor.Filesystem(overlay, fd).file("test", "/usr/lib/small.py")["status"], "rejected")

    def test_runtime_parse_failure_retains_sanitized_partial_observations(self):
        with mock.patch.object(descriptor.sysconfig, "get_paths", side_effect=ValueError("SYNTHETIC_PRIVATE_VALUE")), \
                mock.patch.object(descriptor.site, "getsitepackages", side_effect=ValueError("SYNTHETIC_PRIVATE_VALUE")):
            facts = descriptor.runtime_facts()
        self.assertEqual(facts["status"], "rejected")
        self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(facts))
        self.assertIsNone(next(r for r in facts["locations"] if r["name"] == "stdlib")["path"])

    def canary_payload(self):
        payload = self.root / "payload"
        payload.mkdir()
        script = payload / "python-canary.py"
        script.write_bytes((ROOT / "scripts/engine-python-canary.py").read_bytes())
        wrapper = payload / "python-canary"
        wrapper.write_text('#!/bin/sh\nexec "' + sys.executable + '" -I -S -B "' + str(script) + '" "$@"\n')
        wrapper.chmod(0o555)
        query = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", "import json,sysconfig; print(json.dumps([sysconfig.get_paths(),sysconfig.get_config_var('DESTSHARED')]))"],
                               env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"}, capture_output=True, check=True, timeout=10)
        paths, dynload = json.loads(query.stdout)
        ordered = list(dict.fromkeys([paths["stdlib"], dynload,
                                      paths["purelib"] + "/nvidia_cutlass_dsl/dsl_packages", paths["purelib"]]))
        layout = {"paths": ordered, "executable": os.path.realpath(sys.executable), "python": platform.python_version(), "payload": str(payload)}
        env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "TMPDIR": str(self.root), "NIB_PYTHON_CANARY": json.dumps(layout)}
        return script, env, ordered

    def test_real_stdlib_fresh_spawn_grandchild_forkserver_before_dispatch(self):
        script, env, paths = self.canary_payload()
        results = {}
        for mode in ("fresh", "children"):
            process = snapshot.execute_process_group([sys.executable, "-I", "-S", "-B", str(script), mode], env=env, timeout=45, max_output_bytes=8192)
            self.assertEqual(process.returncode, 0, process.stderr.decode())
            self.assertEqual(process.stderr, b"")
            results[mode] = json.loads(process.stdout)
        expected = overlay.digest(overlay.json_bytes(paths))
        grandchild = results["children"]["spawn"].pop("grandchild")
        for value in (results["fresh"], results["children"]["fork"], results["children"]["spawn"], results["children"]["forkserver"], grandchild):
            descriptor.validate_canary(overlay, value, expected)

    def test_canary_rejects_arbitrary_commands_overrides_and_nonisolated_start(self):
        script, env, _ = self.canary_payload()
        for args in (["-c", "print('SYNTHETIC_PRIVATE_VALUE')"], ["-m", "vllm"], ["--apply"],
                     ["-B", "-S", "-I", "-c", "from os import system; system('SYNTHETIC_PRIVATE_VALUE')"]):
            result = snapshot.execute_process_group([sys.executable, "-I", "-S", "-B", str(script), *args], env=env, timeout=10, max_output_bytes=4096)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(b"SYNTHETIC_PRIVATE_VALUE", result.stdout + result.stderr)
        layout = json.loads(env["NIB_PYTHON_CANARY"])
        layout["paths"].insert(0, str(self.root))
        result = snapshot.execute_process_group([sys.executable, "-I", "-S", "-B", str(script), "fresh"],
            env={**env, "NIB_PYTHON_CANARY": json.dumps(layout)}, timeout=10, max_output_bytes=4096)
        self.assertNotEqual(result.returncode, 0)
        result = snapshot.execute_process_group([sys.executable, "-S", "-B", str(script), "fresh"], env=env, timeout=10, max_output_bytes=4096)
        self.assertNotEqual(result.returncode, 0)

    def test_canary_refuses_dependency_import_before_module_body(self):
        script, env, _ = self.canary_payload()
        library = self.root / "fake-site"
        library.mkdir()
        (library / "nib_dependency_probe.py").write_text("raise RuntimeError('SYNTHETIC_PRIVATE_VALUE')\n")
        # Run the actual bootstrap with synthetic sysconfig, then attempt a real
        # filesystem import. No writes to the host's installed package roots.
        code = """import json,os,sys,sysconfig
paths=sysconfig.get_paths(); paths.update(purelib=sys.argv[2],platlib=sys.argv[2])
sysconfig.get_paths=lambda: paths
layout=json.loads(os.environ['NIB_PYTHON_CANARY'])
layout['paths']=list(dict.fromkeys([paths['stdlib'],sysconfig.get_config_var('DESTSHARED'),paths['purelib']+'/nvidia_cutlass_dsl/dsl_packages',paths['purelib']]))
os.environ['NIB_PYTHON_CANARY']=json.dumps(layout)
with open(sys.argv[1],'rb') as f: data=f.read()
exec(compile(data,sys.argv[1],'exec'),{'__name__':'canary_test','__file__':sys.argv[1]})
try: import nib_dependency_probe
except ValueError: print('blocked')
else: raise AssertionError('dependency imported')
"""
        result = snapshot.execute_process_group([sys.executable, "-I", "-S", "-B", "-c", code, str(script), str(library)],
            env=env, timeout=10, max_output_bytes=4096)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"blocked\n", b""))


if __name__ == "__main__":
    unittest.main()
