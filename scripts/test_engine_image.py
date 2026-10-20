# SPDX-License-Identifier: Apache-2.0
"""Filesystem and fake Docker lifecycle only; never execute an actual image."""

import base64
from contextlib import contextmanager
import copy
import csv
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
RECORDED = ROOT / "evidence/p0/2026-10-19-official-vllm-image-diagnostic"
CENSUS_RECORDED = ROOT / "evidence/p0/2026-10-19-official-vllm-metadata-census"
BOUNDED_RECORDED = ROOT / "evidence/p0/2026-10-19-official-vllm-bounded-integrity"
STUBS_RECORDED = ROOT / "evidence/p0/2026-10-19-official-vllm-cutlass-stubs"
INTEGRITY_RECORDED = ROOT / "evidence/p0/2026-10-19-official-vllm-record-diagnosis"
PYTHON_RECORDED = ROOT / "evidence/p0/2026-10-20-official-vllm-python-layout"
PREREQUISITES_RECORDED = ROOT / "evidence/p0/2026-10-20-official-vllm-python-prerequisites"
PREREQUISITES_SECOND = ROOT / "evidence/p0/2026-10-20-official-vllm-python-prerequisites-2"
CHAIN_RECORDED = ROOT / "evidence/p0/2026-10-20-official-vllm-python-chain"


def source(name, relative):
    module = types.ModuleType(name)
    module.__file__ = str(ROOT / relative)
    exec(compile(Path(module.__file__).read_bytes(), module.__file__, "exec"), module.__dict__)
    return module


recorder = source("image_recorder_test", "scripts/record-engine-image.py")
fixtures = source("image_wheel_test", "scripts/test_engine_overlay.py")
overlay, diagnostic = recorder.overlay, recorder.diagnostic


def historical_payload(revision, expected_recorder_sha):
    """Reconstruct with the actual historical helper closure, not current code."""
    names = (recorder.SCRIPT, recorder.prep.SCRIPT, recorder.prep.INSPECTOR,
             recorder.checker.SCRIPT, recorder.SNAPSHOT, recorder.DIAGNOSTIC, recorder.DESCRIPTOR)
    with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as temporary:
        root = Path(temporary)
        for name in names:
            data = recorder.checker.git(ROOT, "show", revision + ":" + name)
            overlay.require(len(data) <= recorder.MAX_REPORT)
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        data = (root / recorder.SCRIPT).read_bytes()
        overlay.require(overlay.digest(data) == expected_recorder_sha)
        historical = types.ModuleType("historical_image_recorder")
        historical.__file__ = str(root / recorder.SCRIPT)
        # The recorder validates every transitive helper hash before compilation.
        exec(compile(data, historical.__file__, "exec"), historical.__dict__)
        original_git = historical.checker.git
        def source_git(repo, *args, **kwargs):
            if args == ("rev-parse", "--verify", "HEAD^{commit}"):
                return (revision + "\n").encode()
            return original_git(repo, *args, **kwargs)
        with mock.patch.object(historical.checker, "git", side_effect=source_git):
            return historical.payload_files(*historical.inputs(ROOT), mode="python-descriptor")


class FakeDocker:
    def __init__(self, image, report, fault=None):
        self.image, self.report, self.fault = image, report, fault
        self.exists, self.started, self.removed = False, False, False
        self.calls, self.info = [], None

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if args[1:3] == ["image", "inspect"]:
            data = self.image
        elif args[1:3] == ["container", "ls"]:
            if self.fault == "unknown-list" and self.exists:
                raise subprocess.TimeoutExpired(args, 1)
            return subprocess.CompletedProcess(args, 0, b"a" * 64 if self.exists else b"", b"")
        elif args[1:3] == ["container", "create"]:
            def flag(name):
                return args[args.index(name) + 1]
            env = [args[i + 1] for i, value in enumerate(args) if value == "--env"]
            self.exists = True
            self.info = {"Id": "a" * 64, "Image": self.image["Id"], "State": {"Status": "created"},
                "Config": {"Labels": {"nib.diagnostic": flag("--label").split("=", 1)[1]},
                    "Image": overlay.BASE_IMAGE, "Entrypoint": ["python3"], "Cmd": args[-5:], "User": "65532:65532",
                    "WorkingDir": "/", "Healthcheck": {"Test": ["NONE"]}, "Env": env},
                "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True, "Privileged": False,
                    "Tmpfs": None,
                    "Runtime": "runc", "CapDrop": ["ALL"], "IpcMode": "none", "CgroupnsMode": "private",
                    "Memory": 1 << 30, "MemorySwap": 1 << 30, "NanoCpus": 2000000000, "PidsLimit": 32,
                    "RestartPolicy": {"Name": "no"}, "LogConfig": {"Type": "none"}, "SecurityOpt": ["no-new-privileges"]},
                "Mounts": [{"Type": "bind", "Source": flag("--mount").split(",src=", 1)[1].split(",dst=", 1)[0],
                            "Destination": "/opt/nib", "RW": False}]}
            if "--tmpfs" in args:
                self.info["HostConfig"]["Tmpfs"] = {"/tmp": flag("--tmpfs").split(":", 1)[1]}
            if self.fault == "create-timeout":
                raise subprocess.TimeoutExpired(args, 1)
            return subprocess.CompletedProcess(args, 0, b"a" * 64 + b"\n", b"")
        elif args[1:3] == ["container", "inspect"]:
            data = copy.deepcopy(self.info)
            if self.fault == "foreign-owner":
                data["Config"]["Labels"]["nib.diagnostic"] = "someone-else"
            if self.fault == "network":
                data["HostConfig"]["NetworkMode"] = "host"
            if self.fault == "devices":
                data["HostConfig"]["DeviceRequests"] = [{"Count": -1}]
            if self.fault == "extra-env":
                data["Config"]["Env"].append("PYTHONPATH=SYNTHETIC_PRIVATE_VALUE")
            if self.fault == "tmpfs":
                data["HostConfig"]["Tmpfs"] = {"/tmp": "rw,exec,size=1g"}
        elif args[1:3] == ["container", "start"]:
            self.started = True
            if self.fault == "cancel":
                raise InterruptedError("SYNTHETIC_PRIVATE_VALUE")
            if self.fault == "run-timeout":
                raise subprocess.TimeoutExpired(args, 1)
            self.info["State"] = {"Status": "exited", "ExitCode": 0, "OOMKilled": False}
            if self.fault == "bad-result":
                return subprocess.CompletedProcess(args, 0, b"SYNTHETIC_PRIVATE_VALUE", b"")
            if self.fault == "overflow":
                return subprocess.CompletedProcess(args, 0, b"x" * (recorder.MAX_REPORT + 1), b"")
            if self.fault == "signal":
                return subprocess.CompletedProcess(args, -9, b"", b"")
            data = self.report
        elif args[1:3] == ["container", "rm"]:
            self.removed = True
            if self.fault != "still-present":
                self.exists = False
            return subprocess.CompletedProcess(args, 0, b"", b"")
        else:
            raise AssertionError("unexpected command")
        return subprocess.CompletedProcess(args, 0, overlay.json_bytes(data), b"")


class ImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.OverlayTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.OverlayTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.work, self.root = self.fixture.work, self.fixture.root
        self.contract = copy.deepcopy(self.fixture.contract)
        scripts = self.work / "bin"
        scripts.mkdir()
        data = b"#!/usr/bin/python3\n# synthetic only\n"
        (scripts / "vllm").write_bytes(data)
        self.fixture.write(self.fixture.dist + "/entry_points.txt", b"[console_scripts]\nvllm = vllm.entrypoints.cli.main:main\n")
        encoded = "sha256=" + base64.urlsafe_b64encode(bytes.fromhex(overlay.digest(data))).decode().rstrip("=")
        self.fixture.refresh_record([("../bin/vllm", encoded, len(data))])
        self.paths = {"purelib": str(self.root), "platlib": str(self.root), "scripts": str(scripts)}
        self.config = {"os": "linux", "architecture": "amd64", "config": {"Env": ["PATH=/public/image/path", "PYTHONPATH=bad"]},
                       "rootfs": {"type": "layers", "diff_ids": ["sha256:" + "b" * 64]}}
        self.image = {"Id": overlay.BASE_IMAGE.rsplit("@", 1)[1], "Os": "linux", "Architecture": "amd64",
                      "RepoDigests": [overlay.BASE_IMAGE], "RootFS": {"Type": "layers", "Layers": self.config["rootfs"]["diff_ids"]}}
        self.payload = self.work / "payload"
        recorder.prep.publish(self.payload, {"contract.json": overlay.json_bytes(self.contract)})

    @contextmanager
    def layout(self, others=()):
        with mock.patch.object(overlay.platform, "system", return_value="Linux"), \
                mock.patch.object(overlay.platform, "machine", return_value="x86_64"), \
                mock.patch.object(overlay.platform, "python_version", return_value="3.12.3"), \
                mock.patch.object(overlay.sys, "version_info", (3, 12)), \
                mock.patch.object(overlay.sysconfig, "get_paths", return_value=self.paths), \
                mock.patch.object(overlay.site, "getsitepackages", return_value=[str(self.root), *map(str, others)]):
            yield

    def collect(self, others=()):
        with self.layout(others):
            return diagnostic.collect(overlay, self.contract)

    def census(self):
        with self.layout():
            return diagnostic.collect(overlay, {**self.contract, "diagnostic_mode": "metadata-census"})

    def empty_census_root(self):
        root = self.work / "census"
        root.mkdir()
        self.paths.update(purelib=str(root), platlib=str(root))
        return root

    def test_census_never_reads_content_targets_or_opens_non_directories(self):
        root = self.empty_census_root()
        (root / "vllm").mkdir()
        (root / "vllm/native.so").write_bytes(b"not native; never inspected")
        (root / "sitecustomize.py").write_bytes(b"raise AssertionError('never execute')")
        os.mkfifo(root / "SYNTHETIC_PRIVATE_FIFO")
        (root / "SYNTHETIC_PRIVATE_LINK").symlink_to(self.root, target_is_directory=True)
        os.link(root / "vllm/native.so", root / "hardlink")
        original_open = os.open
        def directory_only(path, flags, *args, **kwargs):
            self.assertTrue(flags & os.O_DIRECTORY)
            return original_open(path, flags, *args, **kwargs)
        with mock.patch.object(overlay.Tree, "read", side_effect=AssertionError("no content")), \
                mock.patch.object(overlay.Tree, "inventory", side_effect=AssertionError("no admission")), \
                mock.patch.object(os, "readlink", side_effect=AssertionError("no link targets")), \
                mock.patch.object(os, "open", side_effect=directory_only):
            report = self.census()
        self.assertEqual(report["collection"], "pass")
        self.assertEqual(report["overlay_admission"], "not-evaluated")
        self.assertEqual(report["totals"]["entries"], 6)
        self.assertEqual(report["totals"]["types"], dict(regular=3, directory=1, symlink=1, fifo=1,
                                                        socket=0, block=0, character=0, other=0))
        self.assertEqual(report["totals"]["multiply_linked_regular_entries"], 2)
        self.assertEqual(report["groups"]["vllm"]["entries"], 2)
        self.assertNotIn("SYNTHETIC_PRIVATE", json.dumps(report))
        self.assertNotIn(str(self.work), json.dumps(report))

    def test_census_size_distribution_and_fixed_groups(self):
        root = self.empty_census_root()
        sizes = [0, 1, 4096, 4097, 1 << 20, (1 << 20) + 1, 64 << 20, (64 << 20) + 1,
                 1 << 30, (1 << 30) + 1]
        for index, size in enumerate(sizes):
            path = root / str(index)
            path.touch()
            os.truncate(path, size)  # Sparse fixture: no large data allocation/read.
        report = self.census()
        self.assertEqual(report["totals"]["regular_bytes"], sum(sizes))
        self.assertEqual(report["totals"]["largest_regular_bytes"], max(sizes))
        self.assertEqual(report["totals"]["regular_sizes"],
                         dict(empty=1, le_4k=2, le_1m=2, le_64m=2, le_1g=2, gt_1g=1))
        self.assertEqual(report["groups"]["other"], report["totals"])
        self.assertEqual(set(report["groups"]), {"vllm", "torch", "nvidia", "triton", "other"})

    def test_census_limits_are_partial_and_do_not_change_admission(self):
        root = self.empty_census_root()
        for index in range(3):
            (root / str(index)).write_bytes(b"x")
        self.assertEqual(overlay.MAX_FILES, 100000)
        self.assertEqual(diagnostic.CENSUS_LIMITS["entries"], 1000000)
        with mock.patch.dict(diagnostic.CENSUS_LIMITS, entries=2):
            report = self.census()
            self.assertEqual((report["collection"], report["reason"], report["totals"]["entries"]),
                             ("partial", "entry-limit", 2))
        with mock.patch.object(overlay, "MAX_FILES", 2):
            self.assertEqual(self.census()["collection"], "pass")
            with overlay.directory(root) as fd, self.assertRaises(overlay.InventoryRejected):
                overlay.Tree(fd).inventory()
        with mock.patch.dict(diagnostic.CENSUS_LIMITS, seconds=0):
            report = self.census()
        self.assertEqual((report["collection"], report["reason"]), ("partial", "time-limit"))
        with mock.patch.object(diagnostic, "CENSUS_FILE", 0):
            report = self.census()
        self.assertEqual((report["collection"], report["reason"]), ("partial", "size-limit"))

    def test_census_depth_bound_and_empty_root(self):
        root = self.empty_census_root()
        self.assertEqual(self.census()["totals"]["entries"], 0)
        (root / "a/b/c").mkdir(parents=True)
        with mock.patch.dict(diagnostic.CENSUS_LIMITS, depth=2):
            report = self.census()
        self.assertEqual((report["collection"], report["reason"], report["max_depth"]),
                         ("partial", "depth-limit", 2))
        self.assertEqual(report["totals"]["entries"], 2)

    def test_census_metadata_errors_retain_counts_without_exception_or_name(self):
        root = self.empty_census_root()
        (root / "SYNTHETIC_PRIVATE_DIRECTORY").mkdir()
        original_scan = os.scandir
        scans = 0
        def interrupted(fd):
            nonlocal scans
            scans += 1
            if scans == 2:
                raise PermissionError("SYNTHETIC_PRIVATE_EXCEPTION")
            return original_scan(fd)
        with mock.patch.object(os, "scandir", side_effect=interrupted):
            report = self.census()
        self.assertEqual(report["reason"], "metadata-rejected")
        self.assertEqual(report["collection"], "partial")
        self.assertEqual(report["totals"]["entries"], 1)
        self.assertNotIn("SYNTHETIC_PRIVATE", json.dumps(report))
        (root / "bad\nSYNTHETIC_PRIVATE_NAME").touch()
        report = self.census()
        self.assertEqual(report["reason"], "unsafe-name")
        self.assertNotIn("SYNTHETIC_PRIVATE", json.dumps(report))

    def test_census_directory_swap_never_follows_link(self):
        root = self.empty_census_root()
        (root / "vllm").mkdir()
        original_open = os.open
        def swap(path, flags, *args, **kwargs):
            if path == "vllm":
                (root / "vllm").rename(root / "old")
                (root / "vllm").symlink_to(self.root, target_is_directory=True)
            return original_open(path, flags, *args, **kwargs)
        with mock.patch.object(os, "open", side_effect=swap):
            report = self.census()
        self.assertEqual((report["collection"], report["reason"]), ("partial", "metadata-rejected"))
        self.assertEqual(report["totals"]["entries"], 1)

    def test_census_detects_replaced_directory_and_changing_file_metadata(self):
        root = self.empty_census_root()
        (root / "vllm").mkdir()
        original_open = os.open
        def replace(path, flags, *args, **kwargs):
            if path == "vllm":
                (root / "vllm").rename(root / "old")
                (root / "vllm").mkdir()
            return original_open(path, flags, *args, **kwargs)
        with mock.patch.object(os, "open", side_effect=replace):
            report = self.census()
        self.assertEqual((report["collection"], report["reason"]), ("partial", "directory-changed"))
        (root / "file").write_bytes(b"old")
        original_stat = os.stat
        def change(path, *args, **kwargs):
            if path == "file" and "dir_fd" in kwargs:
                (root / "file").write_bytes(b"changed")
            return original_stat(path, *args, **kwargs)
        with mock.patch.object(os, "stat", side_effect=change):
            report = self.census()
        self.assertEqual((report["collection"], report["reason"]), ("partial", "metadata-rejected"))

    def test_census_ambiguous_or_symlinked_root_is_rejected(self):
        root = self.empty_census_root()
        self.paths["platlib"] = str(self.root)
        self.assertEqual(self.census()["reason"], "root-selection")
        root.rmdir()
        root.symlink_to(self.root, target_is_directory=True)
        self.paths["platlib"] = str(root)
        report = self.census()
        self.assertEqual(report["reason"], "metadata-rejected")
        self.assertEqual(report["totals"]["entries"], 0)

    def test_census_report_rejects_tamper_and_cross_mode_reports(self):
        report = self.census()
        contract = {**self.contract, "diagnostic_mode": "metadata-census"}
        mutations = [("overlay_admission", "accepted"), ("deployable", True), ("content_read", 0),
                     ("reason", "SYNTHETIC_PRIVATE"), ("collection", "partial"), ("max_depth", 33),
                     ("selected_root", str(self.root)), ("path", "SYNTHETIC_PRIVATE"),
                     ("limits", {**report["limits"], "entries": 999999999})]
        for key, value in mutations:
            with self.subTest(key=key), self.assertRaises(ValueError):
                diagnostic.validate_report(overlay, {**report, key: value}, contract)
        for field in ("entries", "regular_bytes", "largest_regular_bytes", "multiply_linked_regular_entries"):
            broken = copy.deepcopy(report)
            broken["totals"][field] += 1
            with self.subTest(field=field), self.assertRaises(ValueError):
                diagnostic.validate_report(overlay, broken, contract)
        with self.assertRaises(ValueError):
            diagnostic.validate_report(overlay, report, self.contract)
        with self.assertRaises(ValueError):
            diagnostic.validate_report(overlay, self.collect(), contract)

    def test_census_lifecycle_never_passes_admission_and_preserves_cleanup(self):
        contract = {**self.contract, "diagnostic_mode": "metadata-census"}
        for partial in (False, True):
            child = self.census()
            if partial:
                child.update(collection="partial", reason="entry-limit")
            for fault in (None, "network", "run-timeout", "still-present", "bad-result"):
                with self.subTest(partial=partial, fault=fault):
                    fake = FakeDocker(self.image, child, fault)
                    report = recorder.run_container(self.payload, contract, self.config, {}, fake)
                    self.assertEqual(report["result"], "fail")
                    if fault is None:
                        self.assertEqual(report["stage"], "metadata-census-partial" if partial else "metadata-census-complete")
                        self.assertEqual(report["cleanup"], "confirmed")
                        self.assertEqual(report["diagnostic"], child)
                    if fault == "network":
                        self.assertFalse(fake.started)
                    self.assertNotIn("SYNTHETIC_PRIVATE", json.dumps(report))

    def test_read_only_collection_preserves_admission_and_files(self):
        with overlay.directory(self.root) as fd:
            before = overlay.Tree(fd).inventory()
        with mock.patch.object(overlay.Tree, "write", side_effect=AssertionError("no writes")), \
                mock.patch.object(overlay.Tree, "unlink", side_effect=AssertionError("no writes")):
            report = self.collect()
        self.assertEqual(report["overlay_admission"], "accepted")
        self.assertEqual(report["checks"]["wheel_integrity"], "pass")
        self.assertEqual(report["candidate_status"], "BLOCKED")
        with overlay.directory(self.root) as fd:
            self.assertEqual(overlay.Tree(fd).inventory(), before)

    def test_image_entry_policy_is_exact_and_not_generic_admission(self):
        policy = overlay.DIAGNOSTIC_INVENTORY_POLICY
        self.assertEqual(overlay.MAX_FILES, 100000)
        self.assertEqual(policy["entry_limit"], 225000)
        self.assertEqual(policy["base_image"], self.contract["base_image"])
        self.assertEqual(policy["config_sha256"], self.contract["official_config"]["sha256"])
        self.assertEqual(overlay.digest(recorder.prep.read_source(ROOT / recorder.CENSUS)), policy["census_output_sha256"])
        with overlay.directory(self.root) as fd, mock.patch.object(overlay, "MAX_FILES", 2):
            tree = overlay.Tree(fd)
            with self.assertRaises(overlay.InventoryRejected):
                tree.inventory()
            self.assertGreater(len(tree.inventory(diagnostic_policy=policy)), 2)
            with self.assertRaises(ValueError):
                overlay.inspect(tree, {**self.contract, "diagnostic_inventory_policy": policy})
            for key, value in (("base_image", "unknown"), ("config_sha256", "0" * 64),
                               ("census_output_sha256", "0" * 64), ("entry_limit", 225001), ("extra", True)):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    tree.inventory(diagnostic_policy={**policy, key: value})

    def test_image_entry_policy_enforces_exact_finite_boundaries(self):
        # Lazy synthetic directory entries avoid creating 225001 fixture files;
        # the real inventory loop, including its <= boundary, still executes.
        info = types.SimpleNamespace(st_mode=stat.S_IFREG, st_nlink=1, st_size=0)
        def metadata(*, follow_symlinks):
            self.assertFalse(follow_symlinks)
            return info
        def zero_file(name, *, retain):
            self.assertFalse(retain)
            return {"bytes": 0, "mode": 0o600, "sha256": "0" * 64, "header": b""}
        for policy, ceiling in ((None, 100000), (overlay.DIAGNOSTIC_INVENTORY_POLICY, 225000)):
            for count in (ceiling, ceiling + 1):
                @contextmanager
                def entries(fd):
                    yield (types.SimpleNamespace(name="f" + str(index), stat=metadata) for index in range(count))
                with self.subTest(ceiling=ceiling, count=count), overlay.directory(self.root) as fd, \
                        mock.patch.object(os, "scandir", entries):
                    tree = overlay.Tree(fd)
                    tree.read = zero_file
                    if count == ceiling:
                        self.assertEqual(len(tree.inventory(diagnostic_policy=policy)), ceiling)
                    else:
                        with self.assertRaises(overlay.InventoryRejected) as failure:
                            tree.inventory(diagnostic_policy=policy)
                        self.assertEqual(failure.exception.reason, "entry-limit")

    def test_image_entry_policy_retains_other_inventory_gates(self):
        policy = overlay.DIAGNOSTIC_INVENTORY_POLICY
        for key, reason in (("MAX_BYTES", "total-size-limit"), ("MAX_FILE", "file-size-limit")):
            with self.subTest(key=key), overlay.directory(self.root) as fd, \
                    mock.patch.object(overlay, key, 1), self.assertRaises(overlay.InventoryRejected) as failure:
                overlay.Tree(fd).inventory(diagnostic_policy=policy)
            self.assertEqual(failure.exception.reason, reason)
        with overlay.directory(self.root) as fd:
            tree = overlay.Tree(fd)
            tree.deadline = 0
            with self.assertRaises(overlay.InventoryRejected) as failure:
                tree.inventory(diagnostic_policy=policy)
            self.assertEqual(failure.exception.reason, "time-limit")
        path = self.root / "unrelated.so"
        for kind, reason in (("symlink", "symlink"), ("fifo", "non-regular-file"),
                             ("hardlink", "hardlink"), ("native", "native-header")):
            if kind == "symlink":
                path.symlink_to("missing")
            elif kind == "fifo":
                os.mkfifo(path)
            elif kind == "hardlink":
                os.link(self.root / "vllm/_version.py", path)
            else:
                path.write_bytes(b"not ELF")
            try:
                with self.subTest(kind=kind), overlay.directory(self.root) as fd, \
                        self.assertRaises(overlay.InventoryRejected) as failure:
                    overlay.Tree(fd).inventory(diagnostic_policy=policy)
                self.assertEqual(failure.exception.reason, reason)
            finally:
                path.unlink()
        path = self.root
        for _ in range(33):
            path = path / "nested"
            path.mkdir()
        with overlay.directory(self.root) as fd, self.assertRaises(overlay.InventoryRejected) as failure:
            overlay.Tree(fd).inventory(diagnostic_policy=policy)
        self.assertEqual(failure.exception.reason, "depth-limit")

    def test_image_entry_policy_cannot_bypass_hooks_roots_or_report_binding(self):
        policy = overlay.DIAGNOSTIC_INVENTORY_POLICY
        self.contract["diagnostic_inventory_policy"] = dict(policy)
        self.fixture.write("unknown.pth", b"raise AssertionError('never execute')\n")
        other = self.work / "other"
        other.mkdir()
        (other / "os-package.py").write_bytes(b"never imported")
        report = self.collect([other])
        self.assertEqual(report["inventory_policy"], policy)
        self.assertEqual(report["checks"]["filesystem_inventory"], "pass")
        self.assertEqual(report["checks"]["wheel_integrity"], "pass")
        self.assertEqual(report["checks"]["root_admission"], "rejected")
        self.assertEqual(report["checks"]["selected_root_admission"], "rejected")
        self.assertEqual(report["overlay_admission"], "rejected")
        for broken in ({key: value for key, value in report.items() if key != "inventory_policy"},
                       {**report, "inventory_policy": {**policy, "entry_limit": 225001}}):
            with self.assertRaises(ValueError):
                diagnostic.validate_report(overlay, broken, self.contract)
        for key, value in (("base_image", "unknown"), ("official_config", {"sha256": "0" * 64})):
            with self.subTest(key=key), mock.patch.dict(self.contract, {key: value}):
                report = self.collect([other])
                self.assertEqual(report["checks"]["filesystem_inventory"], "rejected")
                self.assertNotIn("inventory_policy", report)

    def record_rows(self):
        return list(csv.reader(io.StringIO((self.root / self.fixture.dist / "RECORD").read_text())))

    def write_record_rows(self, rows):
        output = io.StringIO()
        csv.writer(output).writerows(rows)
        self.fixture.write(self.fixture.dist + "/RECORD", output.getvalue().encode())

    def details(self):
        self.contract["diagnostic_integrity_details"] = True
        report = self.collect()
        self.assertEqual(report["overlay_admission"], "rejected")
        self.assertFalse(report["deployable"])
        return report, report["integrity_details"]

    def test_integrity_details_reuse_valid_strict_checks_without_admission(self):
        report, details = self.details()
        self.assertTrue(details["complete"])
        self.assertTrue(all(value == "pass" for value in report["checks"].values()))
        self.assertEqual(details["findings"], [])
        self.assertEqual(details["record"]["unlisted_owned"], 0)
        self.assertEqual(details["record"]["listed_missing"], 0)
        self.assertEqual(details["record"]["listed_unowned"], 0)
        self.assertEqual(details["bytecode"]["owned"], 0)
        self.assertTrue(details["metadata"]["entrypoints_match"])
        self.assertTrue(details["metadata"]["generated_version_match"])
        self.assertEqual(details["metadata"]["wheel_tags"], ["cp38-abi3-linux_x86_64"])
        self.assertTrue(all(row["installed_matches"] for row in details["dependencies"]))
        fake = FakeDocker(self.image, report)
        run = recorder.run_container(self.payload, self.contract, self.config, {}, fake)
        self.assertEqual((run["result"], run["cleanup"]), ("fail", "confirmed"))

    def test_integrity_details_record_bytecode_accounting_is_not_a_policy_change(self):
        rows = self.record_rows()
        listed = "vllm/__pycache__/envs.cpython-312.pyc"
        omitted = "vllm/__pycache__/__init__.cpython-312.opt-1.pyc"
        orphan = "vllm/__pycache__/orphan.cpython-312.pyc"
        for name in (listed, omitted, orphan, "vllm/unlisted.txt", "unrelated/data.txt"):
            self.fixture.write(name, b"original synthetic data, never executed")
        for name in (listed, "unrelated/data.txt"):
            data = (self.root / name).read_bytes()
            rows.append([name, "sha256=" + base64.urlsafe_b64encode(bytes.fromhex(overlay.digest(data))).decode().rstrip("="), str(len(data))])
        rows.append(["vllm/deleted.py", "sha256=" + "a" * 43, "1"])
        self.write_record_rows(rows)
        report, details = self.details()
        self.assertTrue(details["complete"])
        self.assertEqual(report["checks"]["record_integrity"], "rejected")
        self.assertEqual(details["record"]["listed_missing"], 1)
        self.assertEqual(details["record"]["listed_unowned"], 2)
        self.assertEqual(details["record"]["unlisted_owned"], 3)
        self.assertEqual(details["bytecode"], dict(complete=True, owned=3, with_source=2, without_source=1,
            source_recorded=2, listed_empty=0, listed_hashed=1, listed_hash_matches=1, listed_other=0, unlisted=2,
            unlisted_with_recorded_source=1, unlisted_without_recorded_source=1))
        reasons = {row["reason"] for row in details["findings"]}
        self.assertTrue({"record-blank-fields", "record-missing-file", "record-unowned", "record-unlisted-owned"} <= reasons)
        with overlay.directory(self.root) as fd, self.assertRaises(ValueError):
            overlay.inspect_record(overlay.Tree(fd), overlay.Tree(fd).inventory(), self.fixture.dist)

    def test_integrity_details_independent_metadata_failures_survive_record_failure(self):
        self.fixture.write("vllm/unlisted.pyc", b"not executable bytecode")
        self.fixture.write(self.fixture.dist + "/entry_points.txt", b"SYNTHETIC_PRIVATE_VALUE")
        self.fixture.write(self.fixture.dist + "/WHEEL", b"Wheel-Version: 9.0\nRoot-Is-Purelib: true\nTag: cp312-cp312-linux_aarch64\n")
        self.fixture.write("vllm/_version.py", b"__version__ = '0.32.99'\n")
        self.fixture.write(self.fixture.dist + "/direct_url.json", b'{"url":"https://SYNTHETIC_PRIVATE_VALUE.invalid","dir_info":{"editable":true}}')
        name = self.fixture.dist + "/METADATA"
        data = (self.root / name).read_bytes().replace(b">=3.11,<3.15", b">=3.9")
        data = data.replace(b"torch==2.13.0", b"torch @ https://SYNTHETIC_PRIVATE_VALUE.invalid")
        self.fixture.write(name, data)
        (self.root / "torch-2.13.0.dist-info/METADATA").unlink()
        (self.root / "torch-2.13.0.dist-info").rmdir()
        self.fixture.write("torch-2.12.0.dist-info/METADATA", b"Name: torch\nVersion: 2.12.0\n")
        report, details = self.details()
        self.assertFalse(details["complete"])
        self.assertEqual(report["collection"], "partial")
        self.assertEqual(report["checks"]["record_integrity"], "rejected")
        self.assertEqual(report["checks"]["wheel_integrity"], "rejected")
        reasons = {row["reason"] for row in details["findings"]}
        self.assertTrue({"record-unlisted-owned", "entrypoints", "wheel-version", "wheel-purelib", "wheel-tags",
                        "requires-python", "requirement-pin", "dependency-version", "source-version", "editable-source"} <= reasons)
        self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(report))
        torch = next(row for row in details["dependencies"] if row["name"] == "torch")
        self.assertEqual(torch["installed"], ["2.12.0"])
        self.assertFalse(torch["installed_matches"])
        self.assertFalse(torch["declared_matches"])

    def test_integrity_details_duplicate_requirements_and_distributions_are_counted(self):
        name = self.fixture.dist + "/METADATA"
        data = (self.root / name).read_bytes()
        data = data.replace(b"Requires-Dist: torch==2.13.0\n", b"Requires-Dist: torch==2.13.0\n" * 2 + b"Requires-Dist: torch==2.12.0\n")
        self.fixture.write(name, data)
        for version in ("2.12.0", "2.11.0"):
            self.fixture.write(f"torch-{version}.dist-info/METADATA", f"Name: torch\nVersion: {version}\n".encode())
        _, details = self.details()
        torch = next(row for row in details["dependencies"] if row["name"] == "torch")
        self.assertEqual(torch["installed_count"], 3)
        self.assertEqual(len(torch["installed"]), 2)
        self.assertEqual(torch["declared_count"], 3)
        self.assertFalse(torch["declared_matches"])
        self.assertFalse(torch["installed_matches"])
        counts = {row["reason"]: row["count"] for row in details["findings"]}
        self.assertEqual(counts["duplicate-distribution"], 2)
        self.assertEqual(counts["requirement-duplicate"], 2)

    def test_integrity_details_extra_entrypoint_groups_without_values_or_acceptance(self):
        self.fixture.write(self.fixture.dist + "/entry_points.txt", b"[console_scripts]\nvllm = vllm.entrypoints.cli.main:main\n"
                           b"[vllm.general_plugins]\nfixture = SYNTHETIC_PRIVATE_VALUE:never_import\n")
        report, details = self.details()
        self.assertTrue(details["complete"])
        self.assertEqual(report["collection"], "pass")
        self.assertFalse(details["metadata"]["entrypoints_match"])
        self.assertEqual(details["metadata"]["entrypoint_groups"], [
            {"name": "console_scripts", "entries": 1}, {"name": "vllm.general_plugins", "entries": 1}])
        self.assertEqual(report["checks"]["record_integrity"], "rejected")
        self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(report))
        for data in (b"[bad/group]\nname = hidden\n", b"[DEFAULT]\nname = hidden\n",
                     b"[group]\nname = first\nname = duplicate\n"):
            with self.subTest(data=data):
                self.fixture.write(self.fixture.dist + "/entry_points.txt", data)
                report, details = self.details()
                self.assertFalse(details["complete"])
                self.assertEqual(report["collection"], "partial")
                self.assertEqual(details["metadata"]["entrypoint_groups"], [])
                self.assertIn("entrypoints-metadata", {row["reason"] for row in details["findings"]})

    def assert_incomplete_integrity_report(self, report, reason):
        details = report["integrity_details"]
        self.assertFalse(details["complete"])
        self.assertEqual(report["collection"], "partial")
        self.assertIn(reason, {row["reason"] for row in details["findings"]})
        self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(report))
        for complete, collection in ((True, "partial"), (False, "pass"), (True, "pass")):
            changed = {**report, "collection": collection, "integrity_details": {**details, "complete": complete}}
            with self.assertRaises(ValueError):
                diagnostic.validate_report(overlay, changed, self.contract)

    def test_integrity_details_suppressed_or_unparsed_metadata_is_partial(self):
        dist = self.fixture.dist
        # Keep root selection observable even while the selected METADATA is malformed.
        self.fixture.write("vllm.egg-info/PKG-INFO", b"Name: vllm\nVersion: 0.32.0\n")
        metadata = (self.root / dist / "METADATA").read_bytes()
        wheel = (self.root / dist / "WHEEL").read_bytes()
        cases = [
            (dist + "/WHEEL", wheel.replace(b"cp38-abi3-linux_x86_64", b"https://SYNTHETIC_PRIVATE_VALUE.invalid"), "metadata-value"),
            (dist + "/WHEEL", b"not a header\n", "wheel-metadata"),
            (dist + "/METADATA", b"not a header\n", "metadata-invalid"),
            (dist + "/METADATA", metadata.replace(b">=3.11,<3.15", b"https://SYNTHETIC_PRIVATE_VALUE.invalid"), "metadata-value"),
            (dist + "/METADATA", metadata.replace(b"\n\n", b"\nRequires-Dist: @SYNTHETIC_PRIVATE_VALUE\n\n", 1), "requirement-malformed"),
            (dist + "/entry_points.txt", b"not ini\n", "entrypoints-metadata"),
            (dist + "/entry_points.txt", b"\xff", "entrypoints-metadata"),
            (dist + "/direct_url.json", b"{SYNTHETIC_PRIVATE_VALUE", "direct-url"),
            (dist + "/direct_url.json", b"[]", "direct-url"),
            (dist + "/direct_url.json", b'{"dir_info":{"editable":"SYNTHETIC_PRIVATE_VALUE"}}', "direct-url"),
            ("vllm/_version.py", b"not python syntax\n", "source-version-unavailable"),
            ("vllm/_version.py", b"__version__ = call_never_executed()\n", "source-version-unavailable"),
        ]
        for name, data, reason in cases:
            path = self.root / name
            original = path.read_bytes() if path.exists() else None
            with self.subTest(name=name, reason=reason, data=data):
                try:
                    self.fixture.write(name, data)
                    report, details = self.details()
                    self.assert_incomplete_integrity_report(report, reason)
                    self.assertEqual(details["record"]["rows"], len(self.record_rows()))
                    self.assertEqual(len(details["dependencies"]), len(self.contract["dependency_pins"]))
                    if name != dist + "/WHEEL":
                        self.assertEqual(details["metadata"]["wheel_version"], ["1.0"])
                    if name not in (dist + "/METADATA", "vllm/_version.py"):
                        self.assertTrue(details["metadata"]["generated_version_match"])
                finally:
                    if original is None:
                        path.unlink()
                    else:
                        self.fixture.write(name, original)

    def test_integrity_details_unreportable_version_is_partial(self):
        version = "1" * 181
        self.fixture.write("extra-" + version + ".dist-info/METADATA", ("Name: extra\nVersion: " + version + "\n").encode())
        report, details = self.details()
        self.assert_incomplete_integrity_report(report, "metadata-value")
        self.assertTrue(details["metadata"]["generated_version_match"])
        self.assertEqual(details["metadata"]["wheel_version"], ["1.0"])

    def test_integrity_details_read_and_collision_failures_require_partial(self):
        dist = self.fixture.dist
        self.fixture.write("vllm.egg-info/PKG-INFO", b"Name: vllm\nVersion: 0.32.0\n")
        self.fixture.write(dist + "/direct_url.json", b"{}")
        original = overlay.Tree.read
        for name, reason in ((dist + "/WHEEL", "wheel-read"), (dist + "/METADATA", "metadata-read"),
                             (dist + "/entry_points.txt", "entrypoints-read"), (dist + "/direct_url.json", "direct-url"),
                             (dist + "/RECORD", "record-read"), ("vllm/_version.py", "source-version-unavailable"),
                             ("vllm", "console-read")):
            def read(tree, path, *, retain=True):
                if retain and path == name:
                    raise OSError("SYNTHETIC_PRIVATE_VALUE")
                return original(tree, path, retain=retain)
            with self.subTest(name=name), mock.patch.object(overlay.Tree, "read", read):
                report, details = self.details()
                self.assert_incomplete_integrity_report(report, reason)
                self.assertEqual(report["checks"]["filesystem_inventory"], "pass")
                self.assertEqual(len(details["dependencies"]), len(self.contract["dependency_pins"]))
        with mock.patch.object(overlay.Tree, "check_module_collisions", side_effect=OSError("SYNTHETIC_PRIVATE_VALUE")):
            report, _ = self.details()
            self.assert_incomplete_integrity_report(report, "module-collision")

    def test_integrity_details_observed_policy_mismatches_remain_complete(self):
        self.fixture.write(self.fixture.dist + "/WHEEL", b"Wheel-Version: 9.0\nRoot-Is-Purelib: true\nTag: cp312-cp312-linux_aarch64\n")
        self.fixture.write("vllm/_version.py", b"__version__ = '0.32.99'\n")
        self.fixture.write(self.fixture.dist + "/direct_url.json", b'{"dir_info":{"editable":true}}')
        report, details = self.details()
        self.assertTrue(details["complete"])
        self.assertEqual(report["collection"], "pass")
        self.assertEqual(details["metadata"]["wheel_tags"], ["cp312-cp312-linux_aarch64"])
        self.assertFalse(details["metadata"]["generated_version_match"])
        reasons = {row["reason"] for row in details["findings"]}
        self.assertTrue({"wheel-version", "wheel-purelib", "wheel-tags", "source-version", "editable-source"} <= reasons)
        self.assertEqual(report["overlay_admission"], "rejected")

    def test_integrity_details_validator_requires_partial_for_unavailable_reasons(self):
        report, details = self.details()
        for reason in diagnostic.INTEGRITY_INCOMPLETE_REASONS:
            with self.subTest(reason=reason):
                details["findings"] = [{"reason": reason, "count": 1, "paths": []}]
                with self.assertRaises(ValueError):
                    diagnostic.validate_report(overlay, report, self.contract)
        details["findings"] = []
        for key in ("distribution", "engine_version", "entrypoints_match", "generated_version_match"):
            changed = copy.deepcopy(report)
            changed["integrity_details"]["metadata"][key] = None
            with self.subTest(key=key), self.assertRaises(ValueError):
                diagnostic.validate_report(overlay, changed, self.contract)
        changed = copy.deepcopy(report)
        row = changed["integrity_details"]["dependencies"][0]
        row.update(installed=[None], installed_matches=False)
        with self.assertRaises(ValueError):
            diagnostic.validate_report(overlay, changed, self.contract)

    def test_integrity_details_retains_candidate_absence_and_shadow_checks(self):
        self.fixture.write("vllm/v1/__pycache__/conf_compute_utils.cpython-312.pyc", b"never executed")
        self.fixture.write("vllm.py", b"raise AssertionError('never imported')")
        report, details = self.details()
        reasons = {row["reason"] for row in details["findings"]}
        self.assertTrue({"engine-shadow", "candidate-source"} <= reasons)
        self.assertEqual(report["checks"]["wheel_integrity"], "rejected")
        self.assertTrue(all(row["matches_base"] for row in report["source_files"]))

    def test_integrity_details_reject_record_paths_and_bound_samples(self):
        rows = self.record_rows()
        rows.extend([["/SYNTHETIC_PRIVATE_VALUE", "", ""], ["../SYNTHETIC_PRIVATE_VALUE", "", ""], ["bad-row"]])
        for index in range(12):
            self.fixture.write(f"vllm/missing-{index}.txt", b"fixture")
        self.write_record_rows(rows)
        report, details = self.details()
        self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(report))
        finding = next(row for row in details["findings"] if row["reason"] == "record-unlisted-owned")
        self.assertEqual(finding["count"], 12)
        self.assertEqual(len(finding["paths"]), 4)
        self.assertLess(len(overlay.json_bytes(details)), diagnostic.INTEGRITY_LIMITS["report_bytes"])

    def test_integrity_details_limits_preserve_partial_observations(self):
        for key, value, reason in (("metadata_bytes", 1, "diagnostic-byte-limit"), ("metadata_files", 0, "diagnostic-file-limit"),
                                   ("seconds", 0, "diagnostic-time-limit"), ("record_rows", 1, "record-row-limit"),
                                   ("requirements", 1, "metadata-field-limit")):
            with self.subTest(key=key), mock.patch.dict(diagnostic.INTEGRITY_LIMITS, {key: value}):
                report, details = self.details()
                self.assertEqual(report["collection"], "partial")
                self.assertFalse(details["complete"])
                self.assertIn(reason, {row["reason"] for row in details["findings"]})
                self.assertEqual(report["checks"]["filesystem_inventory"], "pass")

    def test_integrity_details_deadline_cuts_preserve_valid_partial_counts(self):
        self.fixture.write("vllm/__pycache__/envs.cpython-312.pyc", b"synthetic never executed")
        with overlay.directory(self.root) as fd, overlay.directory(Path(self.paths["scripts"])) as console_fd:
            tree = overlay.Tree(fd)
            inventory = tree.inventory()
            console = ("../bin/vllm", overlay.Tree(console_fd))
            for cut in (1, 8, 20, 40, 80, 160, 320):
                calls = 0
                def clock():
                    nonlocal calls
                    calls += 1
                    return 0 if calls <= cut else 31
                with self.subTest(cut=cut), mock.patch.object(diagnostic.time, "monotonic", clock):
                    details = diagnostic.integrity_observations(overlay, tree, inventory, self.fixture.dist, self.contract, console)
                diagnostic.validate_integrity_observations(overlay, details, self.contract)
                if not details["complete"]:
                    self.assertIn("diagnostic-time-limit", {row["reason"] for row in details["findings"]})

    def test_integrity_details_missing_console_root_retains_observations(self):
        self.paths["scripts"] = str(self.work / "missing")
        report, details = self.details()
        self.assertEqual(report["collection"], "partial")
        self.assertEqual(report["checks"]["filesystem_inventory"], "pass")
        self.assertFalse(details["complete"])
        self.assertEqual(details["findings"], [{"reason": "diagnostic-prerequisite", "count": 1, "paths": []}])
        self.assertEqual(len(report["source_files"]), 13)

    def test_integrity_details_metadata_fifo_is_bounded_without_raw_errors(self):
        with overlay.directory(self.root) as fd, overlay.directory(Path(self.paths["scripts"])) as console_fd:
            tree = overlay.Tree(fd)
            inventory = tree.inventory()
            path = self.root / self.fixture.dist / "WHEEL"
            path.unlink()
            os.mkfifo(path)
            details = diagnostic.integrity_observations(overlay, tree, inventory, self.fixture.dist, self.contract,
                                                       ("../bin/vllm", overlay.Tree(console_fd)))
            diagnostic.validate_integrity_observations(overlay, details, self.contract)
        self.assertFalse(details["complete"])
        self.assertIn("wheel-read", {row["reason"] for row in details["findings"]})
        self.assertTrue(details["metadata"]["generated_version_match"])

    def test_integrity_details_malformed_record_retains_other_stage_results(self):
        self.fixture.write(self.fixture.dist + "/RECORD", b'"unterminated\n')
        report, details = self.details()
        self.assertFalse(details["record"]["complete"])
        self.assertIsNone(details["record"]["unlisted_owned"])
        self.assertFalse(details["complete"])
        self.assertEqual(details["metadata"]["wheel_version"], ["1.0"])
        self.assertTrue(details["metadata"]["generated_version_match"])
        self.assertEqual(report["collection"], "partial")

    def test_integrity_details_report_tamper_and_wrong_identity_fail_closed(self):
        report, details = self.details()
        mutations = [{**details, "complete": 1}, {**details, "unknown": "SYNTHETIC_PRIVATE_VALUE"},
                     {**details, "findings": [{"reason": "unknown", "count": 1, "paths": []}]},
                     {**details, "findings": [{"reason": "record-size", "count": 1, "paths": ["../escape"]}]},
                     {**details, "dependencies": []}]
        for broken in mutations:
            with self.assertRaises((ValueError, KeyError, TypeError)):
                diagnostic.validate_report(overlay, {**report, "integrity_details": broken}, self.contract)
        for contract in ({k: v for k, v in self.contract.items() if k != "diagnostic_integrity_details"},
                         {**self.contract, "official_config": {"sha256": "0" * 64}}):
            with self.assertRaises(ValueError):
                diagnostic.validate_report(overlay, report, contract)
        with self.assertRaises(ValueError):
            diagnostic.validate_report(overlay, {k: v for k, v in report.items() if k != "integrity_details"}, self.contract)

    def test_hooks_and_populated_roots_remain_rejected_but_integrity_observable(self):
        self.fixture.write("distutils-precedence.pth", b"import forbidden_SENSITIVE_VALUE\n")
        other = self.work / "other"
        (other / "cryptography-41.0.7.dist-info").mkdir(parents=True)
        (other / "cryptography-41.0.7.dist-info/METADATA").write_text("Name: cryptography\nVersion: 41.0.7\n")
        report = self.collect([other])
        self.assertEqual(report["overlay_admission"], "rejected")
        self.assertEqual(report["checks"]["wheel_integrity"], "pass")
        self.assertEqual(report["checks"]["root_admission"], "rejected")
        hooks = [hook for root in report["roots"] for hook in root["hooks"]]
        self.assertEqual(hooks[0]["text"], None)
        self.assertNotIn("SENSITIVE_VALUE", json.dumps(report))
        with self.layout([other]), self.assertRaises(ValueError):
            overlay.installed_root()

    @contextmanager
    def cutlass_stubs(self):
        # Synthetic bytes only. The real filesystem/hash/header path executes;
        # only the expected digest is substituted, never a runtime override.
        data = bytearray(71320)
        data[:7] = b"\x7fELF\x02\x01\x01"
        data[16:24] = b"\x03\x00\xb7\x00\x01\x00\x00\x00"
        data = bytes(data)
        with mock.patch.dict(overlay.CUTLASS_STUB_POLICY, sha256=overlay.digest(data)):
            self.contract["diagnostic_inventory_policy"] = copy.deepcopy(overlay.DIAGNOSTIC_INVENTORY_POLICY)
            self.contract["diagnostic_nonhost_policy"] = copy.deepcopy(overlay.CUTLASS_STUB_POLICY)
            for name in overlay.CUTLASS_STUB_POLICY["paths"]:
                self.fixture.write(name, data)
            yield data

    def stub_inventory(self, classified):
        with overlay.directory(self.root) as fd:
            return overlay.Tree(fd).inventory(diagnostic_policy=overlay.DIAGNOSTIC_INVENTORY_POLICY,
                                              nonhost_policy=overlay.CUTLASS_STUB_POLICY, classified=classified)

    def test_all_four_stubs_classified_without_generic_admission_or_writes(self):
        self.assertEqual(overlay.CUTLASS_STUB_POLICY["sha256"],
                         "0e0fc2fbde506983308e2d02a9c7673f43e07b7638224cebd00ebcba1b1e5645")
        with self.cutlass_stubs() as data:
            rows = []
            inventory = self.stub_inventory(rows)
            self.assertEqual([row["path"] for row in rows], overlay.CUTLASS_STUB_POLICY["paths"])
            for row in rows:
                self.assertEqual(row["classification"], "published-cutlass-aot-crosscompile-stub")
                self.assertEqual(row["sha256"], overlay.digest(data))
                self.assertEqual(row["bytes"], 71320)
                self.assertEqual(inventory[row["path"]]["sha256"], row["sha256"])
            with overlay.directory(self.root) as fd, \
                    mock.patch.object(overlay.Tree, "write", side_effect=AssertionError("no writes")), \
                    mock.patch.object(overlay.Tree, "unlink", side_effect=AssertionError("no writes")):
                tree = overlay.Tree(fd)
                for action in (lambda: tree.inventory(), lambda: overlay.inspect(tree, self.contract),
                               lambda: overlay.apply_overlay(tree, self.contract, self.fixture.stages, {})):
                    with self.assertRaises(overlay.InventoryRejected) as failure:
                        action()
                    self.assertEqual(failure.exception.reason, "native-header")
            report = self.collect()
            self.assertEqual(report["classified_nonhost_assets"], rows)
            self.assertEqual(report["collection"], "pass")
            for check in ("filesystem_inventory", "record_integrity", "wheel_integrity"):
                self.assertEqual(report["checks"][check], "pass")
            self.assertEqual(report["checks"]["selected_root_admission"], "rejected")
            self.assertEqual(report["overlay_admission"], "rejected")
            fake = FakeDocker(self.image, report)
            run = recorder.run_container(self.payload, self.contract, self.config, {}, fake)
            self.assertEqual((run["result"], run["cleanup"]), ("fail", "confirmed"))

    def test_stub_full_digest_size_and_every_required_header_field(self):
        with self.cutlass_stubs() as data:
            names = overlay.CUTLASS_STUB_POLICY["paths"]
            for name in names:
                self.fixture.write(name, data[:-1] + b"x")  # Beyond the retained 64-byte header.
                with self.subTest(name=name), self.assertRaises(overlay.InventoryRejected) as failure:
                    self.stub_inventory([])
                self.assertEqual((failure.exception.reason, failure.exception.path), ("nonhost-asset-mismatch", name))
                self.fixture.write(name, data)
            for changed in (data[:-1], data + b"x"):
                self.fixture.write(names[0], changed)
                with self.assertRaises(overlay.InventoryRejected):
                    self.stub_inventory([])
            # Match each synthetic digest so header and size requirements are
            # tested independently, including an x86 replacement at a known path.
            variants = [data[:-1], data + b"x"]
            for offset, value in ((0, 0), (4, 1), (5, 2), (6, 0), (16, 2), (18, 62), (20, 0)):
                changed = bytearray(data)
                changed[offset] = value
                variants.append(bytes(changed))
            for changed in variants:
                self.fixture.write(names[0], changed)
                with mock.patch.dict(overlay.CUTLASS_STUB_POLICY, sha256=overlay.digest(changed)), \
                        self.assertRaises(overlay.InventoryRejected) as failure:
                    self.stub_inventory([])
                self.assertEqual(failure.exception.reason, "nonhost-asset-mismatch")

    def test_stub_missing_asset_is_failure_with_bounded_partial_classifications(self):
        with self.cutlass_stubs() as data:
            names = overlay.CUTLASS_STUB_POLICY["paths"]
            for name in names:
                (self.root / name).unlink()
                report = self.collect()
                self.assertEqual(report["inventory_failure"], {"reason": "nonhost-asset-missing", "path": name})
                self.assertEqual(report["collection"], "partial")
                self.assertEqual(len(report["classified_nonhost_assets"]), 3)
                self.assertEqual(report["checks"]["wheel_integrity"], "unavailable")
                self.assertEqual(report["overlay_admission"], "rejected")
                forged = {**report, "inventory_failure": {"reason": "nonhost-asset-missing",
                                                         "path": report["classified_nonhost_assets"][0]["path"]}}
                with self.assertRaises(ValueError):
                    diagnostic.validate_report(overlay, forged, self.contract)
                self.fixture.write(name, data)
            for name in names:
                (self.root / name).unlink()
            report = self.collect()
            self.assertEqual(report["inventory_failure"]["reason"], "nonhost-asset-missing")
            self.assertEqual(report["classified_nonhost_assets"], [])

    def test_stub_relocation_extra_native_and_nonregular_files_remain_rejected(self):
        with self.cutlass_stubs() as data:
            names = overlay.CUTLASS_STUB_POLICY["paths"]
            for extra in ("foreign.so", names[0] + ".1", names[0].replace("/stubs/", "/other/")):
                self.fixture.write(extra, data)
                with self.subTest(extra=extra), self.assertRaises(overlay.InventoryRejected) as failure:
                    self.stub_inventory([])
                self.assertEqual((failure.exception.reason, failure.exception.path), ("native-header", extra))
                (self.root / extra).unlink()
            path = self.root / names[0]
            for kind, reason in (("symlink", "symlink"), ("hardlink", "hardlink"), ("fifo", "non-regular-file")):
                path.unlink()
                if kind == "symlink":
                    path.symlink_to(self.root / names[1])
                elif kind == "hardlink":
                    os.link(self.root / "vllm/_version.py", path)
                else:
                    os.mkfifo(path)
                with self.subTest(kind=kind), self.assertRaises(overlay.InventoryRejected) as failure:
                    self.stub_inventory([])
                self.assertEqual(failure.exception.reason, reason)
                path.unlink()
                path.write_bytes(data)

    def test_stub_policy_retains_root_hook_and_resource_gates(self):
        with self.cutlass_stubs():
            self.fixture.write("unknown.pth", b"raise AssertionError('never execute')\n")
            other = self.work / "other"
            other.mkdir()
            (other / "os-package.py").write_bytes(b"never imported")
            report = self.collect([other])
            self.assertEqual(report["checks"]["filesystem_inventory"], "pass")
            self.assertEqual(report["checks"]["wheel_integrity"], "pass")
            self.assertEqual(report["checks"]["root_admission"], "rejected")
            self.assertEqual(report["checks"]["selected_root_admission"], "rejected")
            self.assertEqual(report["overlay_admission"], "rejected")
            for key in ("MAX_FILE", "MAX_BYTES"):
                with self.subTest(key=key), mock.patch.object(overlay, key, 1), \
                        self.assertRaises(overlay.InventoryRejected):
                    self.stub_inventory([])

    def test_stub_policy_unknown_identity_and_changed_policy_rejected(self):
        with self.cutlass_stubs():
            for key, value in (("base_image", "unknown"), ("official_config", {"sha256": "0" * 64}),
                               ("diagnostic_nonhost_policy", {**overlay.CUTLASS_STUB_POLICY, "bytes": 71321})):
                with self.subTest(key=key), mock.patch.dict(self.contract, {key: value}):
                    report = self.collect()
                    self.assertEqual(report["checks"]["filesystem_inventory"], "rejected")
                    self.assertNotIn("classified_nonhost_assets", report)
            with overlay.directory(self.root) as fd:
                tree = overlay.Tree(fd)
                for kwargs in ({"nonhost_policy": overlay.CUTLASS_STUB_POLICY, "classified": []},
                               {"diagnostic_policy": overlay.DIAGNOSTIC_INVENTORY_POLICY,
                                "nonhost_policy": {**overlay.CUTLASS_STUB_POLICY, "extra": True}, "classified": []}):
                    with self.assertRaises(ValueError):
                        tree.inventory(**kwargs)

    def test_stub_report_cannot_forge_complete_inventory_or_admission(self):
        with self.cutlass_stubs():
            report = self.collect()
            rows = report["classified_nonhost_assets"]
            for changed in (rows[:-1], rows + rows[:1], list(reversed(rows)),
                            [{**rows[0], "sha256": "0" * 64}] + rows[1:],
                            [{**rows[0], "path": "foreign.so"}] + rows[1:]):
                with self.assertRaises(ValueError):
                    diagnostic.validate_report(overlay, {**report, "classified_nonhost_assets": changed}, self.contract)
            for key in ("classified_nonhost_assets", "nonhost_policy", "inventory_policy"):
                with self.subTest(key=key), self.assertRaises((ValueError, KeyError)):
                    diagnostic.validate_report(overlay, {k: v for k, v in report.items() if k != key}, self.contract)
            with self.assertRaises(ValueError):
                diagnostic.validate_report(overlay, {**report, "overlay_admission": "accepted"}, self.contract)
            with self.assertRaises(ValueError):
                diagnostic.validate_report(overlay, {**report, "checks": {k: "pass" for k in report["checks"]}}, self.contract)

    def test_unknown_or_forged_hook_content_never_published(self):
        report = self.collect()
        report["roots"][0]["hooks"] = [{"name": "distutils-precedence.pth", "sha256": "c" * 64,
                                        "text": "SYNTHETIC_PRIVATE_VALUE", "classification": "known-public-content"}]
        with self.assertRaises(ValueError):
            diagnostic.validate_report(overlay, report, self.contract)

    def test_known_hook_bytes_are_observed_not_executed(self):
        data = b"raise RuntimeError('must never execute')\n"
        self.fixture.write("distutils-precedence.pth", data)
        with mock.patch.dict(diagnostic.HOOKS, {"distutils-precedence.pth": overlay.digest(data)}):
            report = self.collect()
            hook = report["roots"][0]["hooks"][0]
            self.assertEqual(hook["text"], data.decode())
            self.assertEqual(report["overlay_admission"], "rejected")

    def test_record_rejection_and_source_mismatch_are_visible(self):
        self.fixture.write("vllm/envs.py", b"changed without updating RECORD\n")
        report = self.collect()
        self.assertEqual(report["checks"]["record_integrity"], "rejected")
        self.assertEqual(report["checks"]["wheel_integrity"], "rejected")
        self.assertFalse(report["source_files"][0]["matches_base"])

    def test_malformed_record_is_a_fixed_rejection_not_lost_observations(self):
        self.fixture.write(self.fixture.dist + "/RECORD", b'"SYNTHETIC_PRIVATE_VALUE')
        child = self.collect()
        self.assertEqual(child["checks"]["record_integrity"], "rejected")
        self.assertEqual(child["checks"]["wheel_integrity"], "rejected")
        self.assertTrue(child["roots"][0]["packages"])
        self.assertEqual(len(child["source_files"]), len(self.contract["files"]))
        self.assertEqual(child["overlay_admission"], "rejected")
        self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(child))

    def test_inventory_failure_retains_observations_and_fails_run(self):
        self.fixture.write("distutils-precedence.pth", b"import must_never_execute\n")
        for failure in (ValueError("SYNTHETIC_PRIVATE_VALUE"), TimeoutError("SYNTHETIC_PRIVATE_VALUE")):
            with self.subTest(failure=type(failure).__name__), \
                    mock.patch.object(overlay.Tree, "inventory", side_effect=failure):
                child = self.collect()
            self.assertEqual(child["collection"], "partial")
            self.assertEqual(child["checks"]["filesystem_inventory"], "rejected")
            self.assertEqual(child["checks"]["wheel_integrity"], "unavailable")
            self.assertEqual(child["checks"]["record_integrity"], "unavailable")
            self.assertEqual(child["checks"]["selected_root_admission"], "unavailable")
            self.assertTrue(child["roots"][0]["packages"])
            self.assertEqual(child["roots"][0]["hooks"][0]["name"], "distutils-precedence.pth")
            self.assertTrue(all(item["matches_base"] for item in child["source_files"]))
            fake = FakeDocker(self.image, child)
            report = recorder.run_container(self.payload, self.contract, self.config, {}, fake)
            self.assertEqual(report["diagnostic"], child)
            self.assertEqual(report["result"], "fail")
            self.assertEqual(report["cleanup"], "confirmed")
            self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(report))
            child["collection"] = "pass"
            with self.assertRaises(ValueError):
                diagnostic.validate_report(overlay, child, self.contract)

    def test_unsupported_inventory_entries_preserve_prior_hooks(self):
        self.fixture.write("unknown.pth", b"raise AssertionError('must not execute')\n")
        path = self.root / "unrelated.so"
        for kind, reason in (("symlink", "symlink"), ("fifo", "non-regular-file"),
                             ("native", "native-header"), ("hardlink", "hardlink")):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    path.symlink_to("missing")
                elif kind == "fifo":
                    os.mkfifo(path)
                elif kind == "hardlink":
                    target = self.work / "outside-content"
                    target.write_bytes(b"SYNTHETIC_PRIVATE_VALUE")
                    os.link(target, path)
                else:
                    path.write_bytes(b"not an ELF library")
                try:
                    child = self.collect()
                finally:
                    path.unlink()
                self.assertEqual(child["collection"], "partial")
                self.assertEqual(child["checks"]["filesystem_inventory"], "rejected")
                self.assertEqual(child["inventory_failure"], {"reason": reason, "path": "unrelated.so"})
                self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(child))
                self.assertTrue(child["roots"][0]["hooks"])
                self.assertEqual(len(child["source_files"]), len(self.contract["files"]))

    def test_inventory_diagnostic_limits_and_unsafe_names_do_not_leak_paths(self):
        with mock.patch.object(overlay, "MAX_FILES", 0):
            child = self.collect()
        self.assertEqual(child["inventory_failure"], {"reason": "entry-limit", "path": None})
        path = self.root / "unsafe\nSYNTHETIC_PRIVATE_VALUE"
        path.write_bytes(b"must never be read")
        child = self.collect()
        self.assertEqual(child["inventory_failure"], {"reason": "unsafe-name", "path": None})
        self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(child))

    def test_inventory_failure_report_is_bounded_and_cannot_claim_acceptance(self):
        report = self.collect()
        report["collection"] = "partial"
        report["checks"].update(filesystem_inventory="rejected", wheel_integrity="unavailable",
                               record_integrity="unavailable", selected_root_admission="unavailable")
        report["overlay_admission"] = "rejected"
        report["inventory_failure"] = {"reason": "symlink", "path": "public-package/library.so"}
        diagnostic.validate_report(overlay, report, self.contract)
        for failure in ({"reason": "unbounded-error", "path": None},
                        {"reason": "symlink", "path": "../escape"},
                        {"reason": "symlink", "path": "/absolute/path"},
                        {"reason": "symlink", "path": "a" * 513},
                        {"reason": "symlink", "path": "ok", "target": "SYNTHETIC_PRIVATE_VALUE"}):
            with self.subTest(failure=failure), self.assertRaises(ValueError):
                diagnostic.validate_report(overlay, {**report, "inventory_failure": failure}, self.contract)
        with self.assertRaises(ValueError):
            diagnostic.validate_report(overlay, {**report, "collection": "pass"}, self.contract)

    def test_malformed_metadata_and_inaccessible_roots_preserve_other_observations(self):
        self.fixture.write("aaa-1.dist-info/METADATA", b"Name: SYNTHETIC_PRIVATE_VALUE\n")
        self.fixture.write("distutils-precedence.pth", b"import must_never_execute\n")
        other = self.work / "other"
        other.symlink_to("absent")
        child = self.collect([other])
        self.assertEqual(child["collection"], "partial")
        self.assertEqual(child["overlay_admission"], "rejected")
        self.assertEqual(child["roots"][0]["directory_inventory"], "rejected")
        self.assertEqual(child["roots"][1]["metadata_inventory"], "rejected")
        self.assertTrue(child["roots"][1]["packages"])
        self.assertTrue(child["roots"][1]["hooks"])
        self.assertEqual(child["checks"]["wheel_integrity"], "rejected")
        self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(child))

    def test_unreadable_source_never_counts_as_confirmed_absence(self):
        helper = next(item for item in self.contract["files"] if item["sha256"][0] is None)
        path = self.root / helper["path"]
        os.mkfifo(path)
        child = self.collect()
        item = next(item for item in child["source_files"] if item["path"] == helper["path"])
        self.assertEqual(item["status"], "rejected")
        self.assertFalse(item["matches_base"])
        self.assertIsNone(item["sha256"])
        self.assertEqual(child["collection"], "partial")
        item["matches_base"] = True
        with self.assertRaises(ValueError):
            diagnostic.validate_report(overlay, child, self.contract)

    def test_distro_egg_metadata_is_observed_without_acceptance(self):
        other = self.work / "other"
        (other / "PyJWT-2.7.0.egg-info").mkdir(parents=True)
        (other / "PyJWT-2.7.0.egg-info/PKG-INFO").write_bytes(b"Name: PyJWT\nVersion: 2.7.0\n")
        report = self.collect([other])
        self.assertIn({"name": "PyJWT", "version": "2.7.0"}, report["roots"][0]["packages"])
        self.assertEqual(report["overlay_admission"], "rejected")

    def test_raw_oci_exact_bytes_and_id_not_config_hash(self):
        metadata = self.work / "metadata"
        metadata.mkdir()
        config_data = overlay.json_bytes(self.config)
        manifest = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "config": {"digest": "sha256:" + overlay.digest(config_data), "size": len(config_data),
                               "mediaType": "application/vnd.oci.image.config.v1+json"}, "layers": [{}]}
        manifest_data = overlay.json_bytes(manifest)
        pins = {"base_image": "vllm/vllm-openai@sha256:" + overlay.digest(manifest_data),
                "official_config": {"sha256": overlay.digest(config_data), "bytes": len(config_data)}}
        (metadata / "manifest.json").write_bytes(manifest_data)
        (metadata / "config.json").write_bytes(config_data)
        self.assertEqual(recorder.raw_metadata(metadata, pins), self.config)
        bound = recorder.image_binding(self.image, self.config, self.contract)
        self.assertNotEqual(bound["docker_reported_id"], "sha256:" + bound["raw_config_sha256"])
        self.assertFalse(bound["local_config_bytes_rehashed"])
        self.image["RepoDigests"] = []
        with self.assertRaises(ValueError):
            recorder.image_binding(self.image, self.config, self.contract)
        (metadata / "config.json").write_bytes(config_data + b" ")
        with self.assertRaises(ValueError):
            recorder.raw_metadata(metadata, pins)

    def test_valid_lifecycle_retains_cached_image_and_reports_rejection(self):
        self.fixture.write("unknown.pth", b"import must_not_execute\n")
        fake = FakeDocker(self.image, self.collect())
        report = recorder.run_container(self.payload, self.contract, self.config, {}, fake)
        self.assertTrue(fake.started)
        self.assertTrue(fake.removed)
        self.assertEqual(report["stage"], "overlay-rejected")
        self.assertEqual(report["cleanup"], "confirmed")
        self.assertEqual(report["result"], "fail")
        self.assertFalse(any(call[1:3] in (["image", "rm"], ["image", "pull"]) for call in fake.calls))

    def test_python_descriptor_partial_lifecycle_and_tmpfs_are_sealed(self):
        module = source("descriptor_lifecycle_fixture", "scripts/test_engine_python_descriptor.py")
        fixture = module.DescriptorTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        child = fixture.collect()
        contract = {**self.contract, **fixture.contract, "official_config": self.contract["official_config"]}
        payload = self.work / "descriptor-payload"
        recorder.prep.publish(payload, {"contract.json": overlay.json_bytes(contract)})
        for fault in (None, "tmpfs", "bad-result", "run-timeout"):
            with self.subTest(fault=fault):
                fake = FakeDocker(self.image, child, fault)
                report = recorder.run_container(payload, contract, self.config, {}, fake)
                self.assertEqual(report["result"], "fail")
                self.assertTrue(fake.removed)
                self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(report))
                if fault is None:
                    self.assertEqual(report["stage"], "python-descriptor-partial")
                    self.assertEqual(report["cleanup"], "confirmed")
                    self.assertEqual(report["diagnostic"], child)
                elif fault == "tmpfs":
                    self.assertFalse(fake.started)
                else:
                    self.assertEqual(report["cleanup"], "completion-unknown")
                self.assertFalse(any(call[1:3] in (["image", "pull"], ["image", "rm"]) for call in fake.calls))

    def test_lifecycle_faults_fail_closed_without_raw_output(self):
        child = self.collect()
        for fault in ("create-timeout", "network", "devices", "extra-env", "foreign-owner", "run-timeout",
                      "bad-result", "overflow", "signal", "cancel", "still-present", "unknown-list"):
            with self.subTest(fault=fault):
                fake = FakeDocker(self.image, child, fault)
                report = recorder.run_container(self.payload, self.contract, self.config, {}, fake)
                self.assertEqual(report["result"], "fail")
                self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(report))
                if fault in {"network", "devices", "extra-env", "foreign-owner", "create-timeout"}:
                    self.assertFalse(fake.started)
                if fault == "foreign-owner":
                    self.assertFalse(fake.removed)
                if fault in {"create-timeout", "run-timeout", "bad-result", "overflow", "signal", "cancel"}:
                    self.assertEqual(report["cleanup"], "completion-unknown")

    def test_unbound_local_image_never_creates_container(self):
        self.image["RepoDigests"] = []
        fake = FakeDocker(self.image, self.collect())
        report = recorder.run_container(self.payload, self.contract, self.config, {}, fake)
        self.assertEqual(report["result"], "fail")
        self.assertFalse(any(call[1:3] == ["container", "create"] for call in fake.calls))

    def test_publication_requires_schema_validation_and_never_overwrites(self):
        destination = self.work / "evidence"
        report, record = {"result": "fail"}, {"schema": "nib.evidence/v1"}
        with mock.patch.object(recorder, "call", side_effect=ValueError("schema failed")):
            with self.assertRaises(ValueError):
                recorder.publish_evidence(record, report, self.work / "nib", self.work, destination, {})
        self.assertFalse(destination.exists())
        validation = self.work / "validation/evidence/p0/candidate"
        (validation / "run.json").unlink()
        validation.rmdir()
        with mock.patch.object(recorder, "call", return_value=b"") as validate:
            recorder.publish_evidence(record, report, self.work / "nib", self.work, destination, {})
            self.assertEqual(validate.call_args.args[0][1], "lint")
        self.assertEqual((destination / "run.json").stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            recorder.prep.publish(destination, {"run.json": b"changed"})

    def test_source_binding_uses_committed_bytes(self):
        repo = self.work / "repo"
        repo.mkdir()
        extra = {name: (ROOT / name).read_bytes() for name in (
            recorder.prep.SCRIPT, recorder.prep.INSPECTOR, recorder.prep.PINS, recorder.prep.TEST,
            recorder.SCRIPT, recorder.PROBE, recorder.DIAGNOSTIC, recorder.SNAPSHOT, recorder.TEST, recorder.CENSUS,
            recorder.DESCRIPTOR, recorder.CANARY, recorder.DESCRIPTOR_TEST, recorder.PYTHON_PINS)}
        for name, data in {**self.fixture.bundle, **extra}.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        env = {**recorder.checker.tool_env(), "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
               "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
        def git(*args):
            return subprocess.run(["git", "-C", str(repo), *args], env=env, capture_output=True, check=True, timeout=10)
        git("init", "-q")
        git("add", ".")
        git("commit", "-qm", "synthetic diagnostic source")
        result = recorder.inputs(repo)
        for mode in ("integrity", "metadata-census", "python-descriptor"):
            files, contract = recorder.payload_files(*result, mode=mode)
            self.assertEqual(contract["diagnostic_mode"], mode)
            self.assertEqual(overlay.load_json(files["contract.json"]), contract)
            self.assertEqual(files["engine_image_diagnostic.py"], extra[recorder.DIAGNOSTIC])
            self.assertEqual(contract["payload_sources"]["engine_image_diagnostic.py"], recorder.DIAGNOSTIC_SHA)
            if mode in ("integrity", "python-descriptor"):
                self.assertEqual(contract["diagnostic_inventory_policy"], overlay.DIAGNOSTIC_INVENTORY_POLICY)
                self.assertEqual(contract["diagnostic_nonhost_policy"], overlay.CUTLASS_STUB_POLICY)
                self.assertIs(contract["diagnostic_integrity_details"], True)
                if mode == "python-descriptor":
                    self.assertEqual(files["engine_python_descriptor.py"], extra[recorder.DESCRIPTOR])
                    self.assertEqual(files["python-canary.py"], extra[recorder.CANARY])
                    self.assertEqual(files["processes.py"], extra[recorder.SNAPSHOT])
                    self.assertEqual(contract["descriptor_preparation"]["stages"], 5)
                    self.assertFalse(contract["descriptor_preparation"]["image_writes"])
            else:
                self.assertNotIn("diagnostic_inventory_policy", contract)
                self.assertNotIn("diagnostic_nonhost_policy", contract)
                self.assertNotIn("diagnostic_integrity_details", contract)
        revision, bundle, captured = result
        for replacement in (b"{}\n", b"forged census"):
            with self.assertRaises(ValueError):
                recorder.payload_files(revision, bundle, {**captured, recorder.CENSUS: replacement})
        pins = overlay.load_json(captured[recorder.prep.PINS])
        pins["official_config"]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            recorder.payload_files(revision, bundle, {**captured, recorder.prep.PINS: overlay.json_bytes(pins)})
        (repo / recorder.PROBE).write_bytes(b"uncommitted source must not be used")
        (repo / "scripts/ignored.pyc").write_bytes(b"ignored bytecode must not execute")
        self.assertEqual(recorder.inputs(repo), result)
        with mock.patch.object(recorder, "BOOTSTRAP", b"changed"), self.assertRaises(ValueError):
            recorder.inputs(repo)

    def test_bootstrap_fifo_and_forbidden_cli_are_bounded_and_quiet(self):
        fifo = self.work / "SYNTHETIC_PRIVATE_VALUE"
        os.mkfifo(fifo)
        with self.assertRaises(ValueError):
            recorder.bootstrap_read(fifo)
        for option in ("--image", "--entry-limit"):
            completed = subprocess.run([sys.executable, "-I", "-S", str(ROOT / recorder.SCRIPT),
                                        option, "SYNTHETIC_PRIVATE_VALUE"], capture_output=True, timeout=5)
            self.assertEqual(completed.returncode, 1)
            self.assertNotIn(b"SYNTHETIC_PRIVATE_VALUE", completed.stdout + completed.stderr)

    def test_repeat_cancellation_allows_bounded_cleanup(self):
        with mock.patch.object(recorder, "CANCELLED", False):
            with self.assertRaises(InterruptedError):
                recorder.cancel(None, None)
            recorder.cancel(None, None)

    def test_offline_environment_pins_toolchain_and_inherits_only_caches(self):
        control = self.work / "control"
        ambient = {"GOTOOLCHAIN": "local", "GOENV": "SYNTHETIC_PRIVATE_VALUE",
                   "GOWORK": "SYNTHETIC_PRIVATE_VALUE", "GOFLAGS": "-overlay=SYNTHETIC_PRIVATE_VALUE",
                   "GOPROXY": "https://example.invalid", "GOSUMDB": "SYNTHETIC_PRIVATE_VALUE",
                   "CGO_ENABLED": "1", "GOOS": "SYNTHETIC_PRIVATE_VALUE", "PATH": "SYNTHETIC_PRIVATE_VALUE",
                   "HTTP_PROXY": "SYNTHETIC_PRIVATE_VALUE", "GOCACHE": str(self.work / "existing-cache"),
                   "GOMODCACHE": str(self.work / "existing-modules"), "TEST_TELEMETRY_DIR": "SYNTHETIC_PRIVATE_VALUE",
                   "GO_TELEMETRY_CHILD": "1", "GO_TELEMETRY_CHILD_UPLOAD": "1"}
        with mock.patch.dict(os.environ, ambient, clear=True):
            env = recorder.offline_environment(control)
        for key, value in {"GOTOOLCHAIN": "go1.27.2", "GOENV": "off", "GOWORK": "off",
                           "GOFLAGS": "-mod=readonly", "CGO_ENABLED": "0", "GOPROXY": "off", "GOSUMDB": "off",
                           "TEST_TELEMETRY_DIR": str(control / "telemetry"), "GO_TELEMETRY_CHILD": "2",
                           "PATH": recorder.checker.tool_env()["PATH"], "HOME": str(control / "home"),
                           "GOCACHE": ambient["GOCACHE"], "GOMODCACHE": ambient["GOMODCACHE"]}.items():
            self.assertEqual(env[key], value)
        self.assertNotIn("GOOS", env)
        self.assertNotIn("HTTP_PROXY", env)
        self.assertNotIn("GO_TELEMETRY_CHILD_UPLOAD", env)
        self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(env))
        self.assertEqual((control / "telemetry/mode").read_bytes(), b"off\n")
        self.assertEqual((control / "telemetry").stat().st_mode & 0o777, 0o700)
        self.assertEqual((control / "telemetry/mode").stat().st_mode & 0o777, 0o600)
        control = self.work / "default-control"
        with mock.patch.dict(os.environ, {}, clear=True):
            defaults = recorder.offline_environment(control)
        self.assertEqual(defaults["GOCACHE"], str(control / "go-cache"))
        self.assertEqual(defaults["GOMODCACHE"], str(control / "go-mod"))

    def test_generated_records_use_authoritative_offline_schema(self):
        revision = recorder.checker.git(ROOT, "rev-parse", "HEAD").decode().strip()
        env = recorder.offline_environment(self.work / "control")
        binary, source_hash = recorder.build_validator(ROOT, revision, self.work, env)
        self.assertRegex(source_hash, r"^[0-9a-f]{64}$")
        for index, source_directory in enumerate((RECORDED, CENSUS_RECORDED, BOUNDED_RECORDED, STUBS_RECORDED, INTEGRITY_RECORDED,
                                                  PYTHON_RECORDED, PREREQUISITES_RECORDED, PREREQUISITES_SECOND, CHAIN_RECORDED)):
            recorded = self.work / "recorded/evidence/p0" / ("official-image-" + str(index))
            recorded.mkdir(parents=True)
            (recorded / "run.json").write_bytes(recorder.prep.read_source(source_directory / "run.json"))
        recorder.call([str(binary), "lint", str(self.work / "recorded")], env, timeout=20)
        for result, mode in (("pass", "integrity"), ("fail", "integrity"), ("fail", "metadata-census"), ("fail", "python-descriptor")):
            work = self.work / (result + "-" + mode)
            work.mkdir()
            report = {"result": result, "provenance": {"payload_sha256": "a" * 64}}
            record = recorder.evidence_record(revision, "fixture", recorder.now(), report, mode=mode)
            destination = work / "published"
            recorder.publish_evidence(record, report, binary, work, destination, env)
            self.assertEqual(json.loads((destination / "run.json").read_bytes())["execution"], "software")
            if mode == "metadata-census":
                self.assertIn("--metadata-census --slug fixture", record["command"])
                self.assertIn("does not evaluate admission", record["notes"])
            elif mode == "python-descriptor":
                self.assertIn("--python-descriptor --slug fixture", record["command"])
                self.assertIn("synthetic child canaries do not evaluate admission", record["notes"])
        with self.assertRaises(ValueError):
            recorder.evidence_record(revision, "fixture", recorder.now(), {**report, "result": "pass"}, mode="metadata-census")
        with self.assertRaises(ValueError):
            recorder.evidence_record(revision, "fixture", recorder.now(), {**report, "result": "pass"}, mode="python-descriptor")
        rejected = self.work / "rejected"
        rejected.mkdir()
        record["hardware_validated"] = True
        with self.assertRaises(subprocess.CalledProcessError):
            recorder.publish_evidence(record, report, binary, rejected, rejected / "published", env)
        self.assertFalse((rejected / "published").exists())

    def test_recorded_partial_diagnostic_preserves_failure_and_contract(self):
        expected = {"run.json": "f541746a194270e0d2d312860b27fe8cec8fff5b8ffb341ae91bf5621b609e96",
                    "output.log": "d7b4bced18c59da0da9b7d4ca0175062e1e34c95139832e99790458cf74cb0c9"}
        for name, digest in expected.items():
            self.assertEqual(overlay.digest((RECORDED / name).read_bytes()), digest)
        run = overlay.load_json((RECORDED / "run.json").read_bytes())
        report = overlay.load_json((RECORDED / "output.log").read_bytes())
        diagnostic.validate_report(overlay, report["diagnostic"], self.contract)
        self.assertEqual(run["git_sha"], "694394cfe631ab6d09cd5695087a70351f203fae")
        self.assertEqual(report["provenance"]["git_sha"], run["git_sha"])
        self.assertEqual(run["execution"], "software")
        self.assertFalse(run["hardware_validated"])
        self.assertEqual(run["result"], report["result"])
        self.assertEqual(report["result"], "fail")
        self.assertEqual(report["cleanup"], "confirmed")
        self.assertEqual(report["diagnostic"]["collection"], "partial")
        self.assertEqual(report["diagnostic"]["checks"]["filesystem_inventory"], "rejected")
        self.assertEqual(report["diagnostic"]["checks"]["wheel_integrity"], "unavailable")
        self.assertEqual(report["diagnostic"]["checks"]["record_integrity"], "unavailable")
        self.assertEqual([root["entries"] for root in report["diagnostic"]["roots"]], [42, 516])
        sources = report["diagnostic"]["source_files"]
        self.assertEqual(sum(item["status"] == "read" for item in sources), 12)
        self.assertEqual(sum(item["status"] == "absent" for item in sources), 1)
        self.assertTrue(all(item["matches_base"] for item in sources))

    def test_recorded_entry_limit_failure_is_preserved(self):
        directory = ROOT / "evidence/p0/2026-10-19-official-vllm-inventory-rejection"
        hashes = {"run.json": "b0d2f1c6f02f85d58be9535f49dd806741f897d2f31d98118238493cb92e0c6a",
                  "output.log": "67c3b93d733ffc8ef9769c32f8b63f08cc99bf2e8d02b443cb9d91abc9f50b64"}
        for name, digest in hashes.items():
            self.assertEqual(overlay.digest((directory / name).read_bytes()), digest)
        run = overlay.load_json((directory / "run.json").read_bytes())
        report = overlay.load_json((directory / "output.log").read_bytes())
        diagnostic.validate_report(overlay, report["diagnostic"], self.contract)
        self.assertEqual(run["git_sha"], "4f97ef0b9116571620706c9080f40986c9624baa")
        self.assertEqual(report["provenance"]["git_sha"], run["git_sha"])
        self.assertEqual(run["result"], report["result"])
        self.assertEqual(run["result"], "fail")
        self.assertFalse(run["hardware_validated"])
        self.assertEqual(report["cleanup"], "confirmed")
        self.assertEqual(report["diagnostic"]["inventory_failure"], {"reason": "entry-limit", "path": None})
        self.assertEqual(overlay.MAX_FILES, 100000)

    def test_recorded_census_preserves_measurements_without_qualification(self):
        hashes = {"run.json": "1abb42f1746bcddc0e7e70de128b6684cbc9b2994717d8b777ffe9922f02bd66",
                  "output.log": "006235bf2a5ce571a307efc942b33978f4ef79ab4be91d88db1824470e8f0fd0"}
        files = {}
        for name, digest in hashes.items():
            data = recorder.prep.read_source(CENSUS_RECORDED / name)
            self.assertLessEqual(len(data), 16384)
            self.assertEqual(overlay.digest(data), digest)
            files[name] = overlay.load_json(data)
        run, report = files["run.json"], files["output.log"]
        child, provenance = report["diagnostic"], report["provenance"]
        diagnostic.validate_report(overlay, child, {**self.contract, "diagnostic_mode": "metadata-census"})
        self.assertEqual(run["git_sha"], "e753c351e8e5ec025c172fb3193b4d4268183923")
        self.assertEqual(provenance["git_sha"], run["git_sha"])
        self.assertEqual(provenance["payload_sha256"], "6c4697b6e7a397efad1642f05e86e425a007df5c7cb7a33c3af621c7f8d56777")
        self.assertEqual(provenance["source_bundle_sha256"], "5d60e8dadb3f25e42562ffe08335eede526b92d456dbca65875b9549432f986d")
        self.assertEqual(provenance["diagnostic_mode"], "metadata-census")
        self.assertEqual(run["execution"], "software")
        self.assertFalse(run["hardware_validated"])
        self.assertEqual(run["result"], report["result"])
        self.assertEqual(report["result"], "fail")
        self.assertEqual(report["cleanup"], "confirmed")
        self.assertEqual(report["stage"], "metadata-census-complete")
        self.assertEqual(child["collection"], "pass")
        self.assertEqual(child["overlay_admission"], "not-evaluated")
        for item in (child, report):
            self.assertEqual(item["candidate_status"], "BLOCKED")
            self.assertTrue(all(item[key] is False for key in ("deployable", "abi_validated", "hardware_validated")))
        self.assertEqual(child["totals"], {
            "entries": 204969, "regular_bytes": 19176556448, "largest_regular_bytes": 541607888,
            "multiply_linked_regular_entries": 0,
            "types": dict(regular=188365, directory=16604, symlink=0, fifo=0, socket=0, block=0, character=0, other=0),
            "regular_sizes": dict(empty=43898, le_4k=48213, le_1m=95324, le_64m=898, le_1g=32, gt_1g=0)})
        self.assertEqual(child["max_depth"], 15)
        self.assertFalse(child["content_read"])
        self.assertFalse(child["link_targets_read"])
        self.assertEqual(overlay.MAX_FILES, 100000)

    def test_recorded_bounded_inventory_preserves_native_rejection(self):
        hashes = {"run.json": "8ba3b4732f3684611c695ce6b5446447a2de4471a83e55bc899603d7e05af07e",
                  "output.log": "1ca430527afa8fb98a77637cff0efff4fab0f0cfe51720812b13ccbfca480fb7"}
        files = {}
        for name, digest in hashes.items():
            data = recorder.prep.read_source(BOUNDED_RECORDED / name)
            self.assertLessEqual(len(data), 65536)
            self.assertEqual(overlay.digest(data), digest)
            files[name] = overlay.load_json(data)
        run, report = files["run.json"], files["output.log"]
        child, provenance = report["diagnostic"], report["provenance"]
        contract = {**self.contract, "diagnostic_inventory_policy": overlay.DIAGNOSTIC_INVENTORY_POLICY}
        diagnostic.validate_report(overlay, child, contract)
        self.assertEqual(run["git_sha"], "472f998df3d128d5dd5f1ad4b26cb628e7042fc8")
        self.assertEqual(provenance["git_sha"], run["git_sha"])
        self.assertEqual(provenance["payload_sha256"], "16752cb9ce4d1e1adba509ff25e9bb789b08e158444823b340e302495694599c")
        self.assertEqual(provenance["source_bundle_sha256"], "50d5389784b20235719081608fecd0faa85530ad533ec391598327dd98a30982")
        self.assertEqual(provenance["diagnostic_mode"], "integrity")
        self.assertEqual(run["execution"], "software")
        self.assertFalse(run["hardware_validated"])
        self.assertEqual(run["result"], report["result"])
        self.assertEqual(report["result"], "fail")
        self.assertEqual(report["cleanup"], "confirmed")
        self.assertEqual(report["stage"], "overlay-rejected")
        self.assertEqual(child["collection"], "partial")
        self.assertEqual(child["overlay_admission"], "rejected")
        self.assertEqual(child["inventory_policy"], overlay.DIAGNOSTIC_INVENTORY_POLICY)
        self.assertEqual(child["inventory_policy"]["entry_limit"], 225000)
        self.assertEqual(child["inventory_failure"], {"reason": "native-header",
            "path": "nvidia_cutlass_dsl/cu12/lib/stubs/aarch64-unknown-linux-gnu/libcute_dsl_runtime.so"})
        self.assertEqual(child["checks"], dict(root_admission="rejected", filesystem_inventory="rejected",
            record_integrity="unavailable", wheel_integrity="unavailable", selected_root_admission="unavailable"))
        self.assertEqual(len(child["source_files"]), 13)
        self.assertTrue(all(item["matches_base"] for item in child["source_files"]))
        for item in (child, report):
            self.assertEqual(item["candidate_status"], "BLOCKED")
            self.assertTrue(all(item[key] is False for key in ("deployable", "abi_validated", "hardware_validated")))
        self.assertEqual(overlay.MAX_FILES, 100000)

    def test_recorded_stubs_complete_inventory_without_integrity_or_admission(self):
        hashes = {"run.json": "d9d8aade573234c5db08ac3cd9e9adf026e130cb43686a3e852675474402aee8",
                  "output.log": "53888d555e37984f67dbe526a6dff482250c12c577971bade0007278ec007f19"}
        files = {}
        for name, digest in hashes.items():
            data = recorder.prep.read_source(STUBS_RECORDED / name)
            self.assertLessEqual(len(data), 65536)
            self.assertEqual(overlay.digest(data), digest)
            files[name] = overlay.load_json(data)
        run, report = files["run.json"], files["output.log"]
        child, provenance = report["diagnostic"], report["provenance"]
        contract = {**self.contract, "diagnostic_inventory_policy": overlay.DIAGNOSTIC_INVENTORY_POLICY,
                    "diagnostic_nonhost_policy": overlay.CUTLASS_STUB_POLICY}
        diagnostic.validate_report(overlay, child, contract)
        self.assertEqual(run["git_sha"], "2dea53cd9ab7556de9dd1a705266496c949735b7")
        self.assertEqual(provenance["git_sha"], run["git_sha"])
        self.assertEqual(provenance["payload_sha256"], "4abc71cc78671cd1dfa0cbdcb03c560da0de6520a2ec56657d30848737ce4525")
        self.assertEqual(provenance["source_bundle_sha256"], "a0c9377d29f76e6e5677b7e27274feebc8fef5983ae968e6b5400a92d9437821")
        self.assertEqual(provenance["diagnostic_mode"], "integrity")
        self.assertEqual(run["execution"], "software")
        self.assertEqual(run["command"], "python3 -I -S scripts/record-engine-image.py --slug official-vllm-cutlass-stubs")
        self.assertEqual((run["started_utc"], run["ended_utc"]), ("2026-10-19T23:18:25Z", "2026-10-19T23:19:34Z"))
        self.assertFalse(run["hardware_validated"])
        self.assertEqual(run["result"], report["result"])
        self.assertEqual(report["result"], "fail")
        self.assertEqual(report["cleanup"], "confirmed")
        self.assertEqual(report["stage"], "overlay-rejected")
        self.assertEqual(child["collection"], "pass")
        self.assertEqual(child["overlay_admission"], "rejected")
        self.assertNotIn("inventory_failure", child)
        self.assertEqual(child["checks"], dict(filesystem_inventory="pass", record_integrity="rejected",
            wheel_integrity="rejected", root_admission="rejected", selected_root_admission="rejected"))
        self.assertEqual(child["inventory_policy"], overlay.DIAGNOSTIC_INVENTORY_POLICY)
        self.assertEqual(child["nonhost_policy"], overlay.CUTLASS_STUB_POLICY)
        self.assertEqual(child["classified_nonhost_assets"], [
            {"path": name, "bytes": 71320, "sha256": "0e0fc2fbde506983308e2d02a9c7673f43e07b7638224cebd00ebcba1b1e5645",
             "classification": "published-cutlass-aot-crosscompile-stub"} for name in overlay.CUTLASS_STUB_POLICY["paths"]])
        self.assertEqual(len(child["source_files"]), 13)
        self.assertTrue(all(item["matches_base"] for item in child["source_files"]))
        self.assertEqual(sum(item["status"] == "absent" for item in child["source_files"]), 1)
        for item in (child, report):
            self.assertEqual(item["candidate_status"], "BLOCKED")
            self.assertTrue(all(item[key] is False for key in ("deployable", "abi_validated", "hardware_validated")))
        self.assertTrue(report["image"]["raw_config_link_verified"])
        self.assertEqual(report["image"]["raw_config_sha256"], overlay.CUTLASS_STUB_POLICY["config_sha256"])
        self.assertFalse(report["image"]["local_config_bytes_rehashed"])
        self.assertFalse(report["image"]["local_layers_rehashed"])
        self.assertEqual(overlay.MAX_FILES, 100000)

    def test_recorded_integrity_causes_preserve_failure_and_exact_observations(self):
        hashes = {"run.json": "8ac7a81e9984c83a22d0c56d0014ef7177219d433818682fa58b5fa81fbd23e2",
                  "output.log": "abf7f9fbb4dce9ace3da05fa2873f4e27041baf13a18405ddce8c71e7febfc76"}
        files = {}
        for name, digest in hashes.items():
            data = recorder.prep.read_source(INTEGRITY_RECORDED / name)
            self.assertLessEqual(len(data), 65536)
            self.assertEqual(overlay.digest(data), digest)
            files[name] = overlay.load_json(data)
        run, report = files["run.json"], files["output.log"]
        child, provenance = report["diagnostic"], report["provenance"]
        contract = {**self.contract, "diagnostic_inventory_policy": overlay.DIAGNOSTIC_INVENTORY_POLICY,
                    "diagnostic_nonhost_policy": overlay.CUTLASS_STUB_POLICY, "diagnostic_integrity_details": True}
        diagnostic.validate_report(overlay, child, contract)
        self.assertEqual(run["git_sha"], "aed2389f4fcb56aeaad9a573f59c5b351b15718b")
        self.assertEqual(provenance["git_sha"], run["git_sha"])
        self.assertEqual(provenance["payload_sha256"], "16378fdd3216a2b44282b2c62db3bc7a5ce58c00e56a18ba1c6e69f5aaa4cdc8")
        self.assertEqual(provenance["source_bundle_sha256"], "e0a4af2583f7592a67f1fbe7a912ba10827fbd9f7676dd3e88865f6b23357fc2")
        self.assertEqual(provenance["validator_source_sha256"], "c18f7f7028758f683f01a67806cbe5db9d44f503c6f58357393329b01a243232")
        self.assertEqual(run["command"], "python3 -I -S scripts/record-engine-image.py --slug official-vllm-record-diagnosis")
        self.assertEqual((run["started_utc"], run["ended_utc"]), ("2026-10-19T23:57:58Z", "2026-10-19T23:59:06Z"))
        self.assertEqual(run["execution"], "software")
        self.assertFalse(run["hardware_validated"])
        self.assertEqual(run["result"], report["result"])
        self.assertEqual((report["result"], report["stage"], report["cleanup"]), ("fail", "overlay-rejected", "confirmed"))
        self.assertEqual((child["collection"], child["overlay_admission"]), ("pass", "rejected"))
        self.assertEqual(child["checks"], dict(filesystem_inventory="pass", record_integrity="rejected",
            wheel_integrity="rejected", root_admission="rejected", selected_root_admission="rejected"))
        details = child["integrity_details"]
        self.assertTrue(details["complete"])
        self.assertEqual({row["reason"]: row["count"] for row in details["findings"]},
                         {"entrypoints": 1, "record-unlisted-owned": 2879})
        self.assertEqual(details["record"], dict(complete=True, rows=5449, unique_paths=5449, owned_files=8327,
            listed_missing=0, listed_unowned=0, unlisted_owned=2879))
        self.assertEqual(details["bytecode"], dict(complete=True, owned=2879, with_source=2879, without_source=0,
            source_recorded=2879, listed_empty=0, listed_hashed=0, listed_hash_matches=0, listed_other=0,
            unlisted=2879, unlisted_with_recorded_source=2879, unlisted_without_recorded_source=0))
        self.assertEqual(details["metadata"], dict(distribution="vllm-0.32.0.dist-info", engine_version="0.32.0",
            entrypoint_groups=[{"name": "console_scripts", "entries": 1}, {"name": "vllm.general_plugins", "entries": 2}],
            entrypoints_match=False, generated_version_match=True, requires_python=["<3.15,>=3.11"],
            root_is_purelib=["false"], wheel_tags=["cp38-abi3-linux_x86_64"], wheel_version=["1.0"]))
        dependencies = details["dependencies"]
        self.assertEqual(len(dependencies), 14)
        self.assertTrue(all(row["installed_count"] == 1 and row["installed_matches"] for row in dependencies))
        for row in dependencies:
            self.assertEqual((row["declared_count"], row["declared_matches"]),
                             (0, False) if row["name"] == "nvidia-nccl-cu13" else (1, True))
        self.assertEqual(len(child["source_files"]), 13)
        self.assertTrue(all(row["matches_base"] for row in child["source_files"]))
        self.assertEqual(sum(row["status"] == "absent" for row in child["source_files"]), 1)
        self.assertEqual(len(child["classified_nonhost_assets"]), 4)
        for item in (child, report):
            self.assertEqual(item["candidate_status"], "BLOCKED")
            self.assertTrue(all(item[key] is False for key in ("deployable", "abi_validated", "hardware_validated")))
        self.assertTrue(report["image"]["raw_config_link_verified"])
        self.assertEqual(report["image"]["raw_config_sha256"], overlay.CUTLASS_STUB_POLICY["config_sha256"])
        self.assertFalse(report["image"]["local_config_bytes_rehashed"])
        self.assertFalse(report["image"]["local_layers_rehashed"])

    def test_recorded_python_layout_preserves_partial_and_unattempted_children(self):
        # Validate the unchanged v1 artifact with its exact reviewed producer,
        # not a compatibility bypass in the current v2 recorder.
        historical_source = recorder.checker.git(ROOT, "show", "af35ad100e1c9a0522b13211daa780dfeb8eddcc:scripts/engine_python_descriptor.py")
        self.assertEqual(overlay.digest(historical_source), "352ef1903cc306bf15429b9d29810cf040a19e00dfd471dd34453b3a8f461d1b")
        historical = types.ModuleType("recorded_python_descriptor_v1")
        exec(compile(historical_source, "recorded_python_descriptor_v1", "exec"), historical.__dict__)
        hashes = {"run.json": "beeddd0b1f86a97e822589861fe548274d74495134492e357bec106343e2a0f5",
                  "output.log": "259e57d317b64d750b251a8940f3de0dcd51dc220cec6c4e19e818e3cf56cb18"}
        files = {}
        for name, digest in hashes.items():
            data = recorder.prep.read_source(PYTHON_RECORDED / name)
            self.assertLessEqual(len(data), recorder.descriptor.LIMITS["report_bytes"])
            self.assertEqual(overlay.digest(data), digest)
            files[name] = overlay.load_json(data)
        run, report = files["run.json"], files["output.log"]
        child, provenance = report["diagnostic"], report["provenance"]
        pins = overlay.load_json(recorder.prep.read_source(ROOT / recorder.PYTHON_PINS))
        preparation = dict(stages=5, image_writes=False,
            candidate_bundle_sha256="8b0b2aca8c20395e99dabc930e7bdf1605cc50f85f5d41d93a291f01cbf5a766",
            context_sha256="3c80b022258448f24172e24e2c2085173ae0af11c80b612bbd4abdc3e7dd9c35")
        contract = {**self.contract, "diagnostic_mode": "python-descriptor", "descriptor_preparation": preparation,
                    "descriptor_source_pins": {"revision": pins["revision"],
                        "files": {name: item["sha256"] for name, item in pins["files"].items()}}}
        historical.validate_report(overlay, child, contract)
        self.assertEqual(run["git_sha"], "af35ad100e1c9a0522b13211daa780dfeb8eddcc")
        self.assertEqual(provenance["git_sha"], run["git_sha"])
        self.assertEqual(provenance["payload_sha256"], "87535c54124f5186710961bb8c303343708c1f63bff982c82da261917f2771d5")
        self.assertEqual(provenance["source_bundle_sha256"], "541dcd191956dc805384d68ca0b11f992983bd2c89f679bf8fc388f4a56b7322")
        self.assertEqual(provenance["validator_source_sha256"], "c18f7f7028758f683f01a67806cbe5db9d44f503c6f58357393329b01a243232")
        self.assertEqual(provenance["diagnostic_mode"], "python-descriptor")
        self.assertEqual(run["command"], "python3 -I -S scripts/record-engine-image.py --python-descriptor --slug official-vllm-python-layout")
        self.assertEqual((run["started_utc"], run["ended_utc"]), ("2026-10-20T01:03:13Z", "2026-10-20T01:04:41Z"))
        self.assertEqual(run["execution"], "software")
        self.assertFalse(run["hardware_validated"])
        self.assertEqual(run["result"], report["result"])
        self.assertEqual((report["result"], report["stage"], report["cleanup"]), ("fail", "python-descriptor-partial", "confirmed"))
        self.assertEqual((child["collection"], child["overlay_admission"]), ("partial", "not-evaluated"))
        self.assertEqual(child["preparation"], preparation)
        self.assertEqual((child["runtime"]["status"], child["runtime"]["python"]), ("observed", "3.12.3"))
        for row in child["files"][:2]:
            self.assertEqual((row["path"], row["status"], row["sha256"]), ("/usr/bin/python3", "rejected", None))
        roots = {row["name"]: row for row in child["scans"]}
        self.assertEqual((roots["site_root_2"]["path"], roots["site_root_2"]["status"]), ("/usr/lib/python3.12/dist-packages", "absent"))
        self.assertTrue(all(row["status"] == "pass" for name, row in roots.items() if name != "site_root_2"))
        packaging = child["packaging"]
        self.assertEqual((packaging["status"], packaging["inventory"], packaging["entrypoints_match"]), ("pass", "pass", True))
        self.assertEqual(packaging["entrypoints"], overlay.PACKAGING_POLICY["entrypoints"])
        self.assertEqual((packaging["removal_count"], packaging["removal_bytes"]), (2879, 48909952))
        self.assertEqual(packaging["removal_plan_sha256"], "210a50805b36797d06e1d17425f6eacba6f90f1e04aaa7c0560692c05ffb3b3f")
        self.assertTrue(all(packaging[key] == 0 for key in ("legacy_bytecode", "without_source", "ambiguous")))
        self.assertEqual(len(packaging["classified_nonhost_assets"]), 4)
        self.assertEqual(len(child["worker_sources"]), 14)
        self.assertTrue(all(row["status"] == "read" and row["matches"] for row in child["worker_sources"]))
        # The recorded prerequisites reject before any child launch; the fixed
        # reason is not evidence of a subprocess failure.
        processes = mock.Mock()
        self.assertEqual(historical.canaries(overlay, child["runtime"], child["scans"], child["files"][0],
            processes, Path("/opt/nib")), child["canaries"])
        processes.execute_process_group.assert_not_called()
        self.assertTrue(all(child["canaries"][key] is None for key in ("path_sha256", "fresh", "fork", "spawn", "grandchild", "forkserver")))
        for item in (child, report):
            self.assertEqual(item["candidate_status"], "BLOCKED")
            self.assertTrue(all(item[key] is False for key in ("deployable", "abi_validated", "hardware_validated")))
        self.assertTrue(provenance["raw_oci_metadata_verified"])
        self.assertTrue(report["image"]["raw_config_link_verified"])
        self.assertFalse(report["image"]["local_config_bytes_rehashed"])
        self.assertFalse(report["image"]["local_layers_rehashed"])

    def test_recorded_prerequisites_preserve_setup_failure_and_unattempted_canaries(self):
        revision = "8ceec40b867460f9273a321a16798bb36bbfe1fa"
        historical_recorder = recorder.checker.git(ROOT, "show", revision + ":" + recorder.SCRIPT)
        historical_descriptor = recorder.checker.git(ROOT, "show", revision + ":" + recorder.DESCRIPTOR)
        self.assertEqual(overlay.digest(historical_recorder), "bfe76a51bba24b73dbe727e41a1f70c3972120ee450b040e900bce199e703dfd")
        self.assertEqual(overlay.digest(historical_descriptor), "5330337b9e12650a964136b2b5497d6c6dc64cd632899a6e2a302511128f89b5")
        # Reconstruct all five stages and the payload from the recorded commit,
        # not today's test/doc/artifact bytes. No image or upstream import.
        files, contract = historical_payload(revision, "bfe76a51bba24b73dbe727e41a1f70c3972120ee450b040e900bce199e703dfd")
        payload = overlay.bundle_digest(files)
        self.assertEqual(payload, "2142fd5e894744adb1f92749f0a9d61f99615ee90562c64e22726f685592ac8b")
        pairs = ((PREREQUISITES_RECORDED, "c29e8217b4ef21584061a3906075d8da120eaf29a41eb1f761963509c1d420f0",
                  "0bc8bb5067de253754dc7d86c1fbb3e887e41e173885d287f50f49c7f2287704"),
                 (PREREQUISITES_SECOND, "3bec632c2d4ae59e5881d126b7c318a56477188b6a7b0640a67f500255d123c5",
                  "c2d41a0ca5747a5617075f289720f8066df9cfacc56d6d86a437178ba879fa04"))
        reports = []
        for directory, run_hash, output_hash in pairs:
            documents = []
            for name, expected in (("run.json", run_hash), ("output.log", output_hash)):
                data = recorder.prep.read_source(directory / name)
                self.assertLessEqual(len(data), recorder.descriptor.LIMITS["report_bytes"])
                self.assertEqual(overlay.digest(data), expected)
                documents.append(overlay.load_json(data))
            run, report = documents
            reports.append(report)
            self.assertEqual(run["git_sha"], revision)
            self.assertEqual(run["result"], "fail")
            self.assertEqual(run["execution"], "software")
            self.assertFalse(run["hardware_validated"])
            provenance = report["provenance"]
            self.assertEqual(provenance["git_sha"], revision)
            self.assertEqual(provenance["payload_sha256"], payload)
            self.assertEqual(provenance["source_bundle_sha256"], contract["source_bundle_sha256"])
            self.assertEqual(provenance["validator_source_sha256"], "c18f7f7028758f683f01a67806cbe5db9d44f503c6f58357393329b01a243232")
            self.assertEqual(provenance["requested_image"], overlay.BASE_IMAGE)
            self.assertEqual(provenance["expected_config_sha256"], contract["official_config"]["sha256"])
            self.assertEqual(provenance["diagnostic_mode"], "python-descriptor")
            self.assertEqual(report["result"], "fail")
            self.assertEqual(report["candidate_status"], "BLOCKED")
            self.assertTrue(all(report[key] is False for key in ("deployable", "abi_validated", "hardware_validated")))
        setup, report = reports
        self.assertEqual((setup["stage"], setup["cleanup"], setup["failure"]), ("raw-oci-metadata", "not-created", "preflight-failed"))
        self.assertFalse(setup["provenance"]["raw_oci_metadata_verified"])
        self.assertNotIn("diagnostic", setup)
        self.assertNotIn("image", setup)
        self.assertEqual((report["stage"], report["cleanup"]), ("python-descriptor-partial", "confirmed"))
        self.assertTrue(report["provenance"]["raw_oci_metadata_verified"])
        self.assertTrue(report["image"]["raw_config_link_verified"])
        self.assertFalse(report["image"]["local_config_bytes_rehashed"])
        self.assertFalse(report["image"]["local_layers_rehashed"])
        child = report["diagnostic"]
        recorder.descriptor.validate_report(overlay, child, contract)
        self.assertEqual((child["collection"], child["overlay_admission"]), ("partial", "not-evaluated"))
        self.assertEqual(child["preparation"], contract["descriptor_preparation"])
        for row in child["files"][:2]:
            self.assertEqual((row["path"], row["status"], row["stage"], row["reason"]),
                             ("/usr/bin/python3", "rejected", "resolution", "unsafe-target"))
            self.assertEqual(row["observed"], dict(type="symlink", mode=0o777, size=25, nlink=1))
            self.assertEqual(row["links"], [])
            self.assertTrue(all(row[key] is None for key in ("resolved", "sha256", "bytes", "mode")))
        self.assertTrue(all(row["status"] == "read" for row in child["files"][2:-1]))
        self.assertEqual(child["files"][-1]["status"], "absent")
        self.assertEqual(len(child["worker_sources"]), 14)
        self.assertTrue(all(row["status"] == "read" and row["matches"] for row in child["worker_sources"]))
        self.assertTrue(all(recorder.descriptor.scan_acceptable(row, child["scans"], child["runtime"]) for row in child["scans"]))
        missing = child["scans"][-1]
        self.assertEqual((missing["path"], missing["status"], missing["reason"]),
                         ("/usr/lib/python3.12/dist-packages", "absent", "confirmed-absent"))
        previous = overlay.load_json(recorder.prep.read_source(PYTHON_RECORDED / "output.log"))
        self.assertEqual(child["packaging"], previous["diagnostic"]["packaging"])
        self.assertEqual(child["canaries"]["blockers"], ["executable-unverified"])
        self.assertEqual(child["canaries"]["status"], "not-run")
        processes = mock.Mock()
        self.assertEqual(recorder.descriptor.canaries(overlay, child["runtime"], child["scans"], child["files"][0],
                         processes, Path("/opt/nib")), child["canaries"])
        processes.execute_process_group.assert_not_called()

    def test_recorded_interpreter_chain_complete_collection_is_not_qualification(self):
        revision = "d033d2198866f7fdbd509e56329702942f22de14"
        files, contract = historical_payload(revision, "f589ca6c9ad22d88433941cad27387ad80403e0def2c69be79406ef2d0e350cc")
        payload = overlay.bundle_digest(files)
        self.assertEqual(payload, "864ebf136e71eafc570677378547990140f1ace563f1d7a8ddf21fe4b672ff1e")
        documents = []
        for name, expected in (("run.json", "263c5454ed01385925bedbd6f0c466448e8749fdd1a7a9aa439dc53f76c2322d"),
                               ("output.log", "52a15aafc3044821740c5d408e5fc44f6635bea36c30f5af51f5866120810440")):
            data = recorder.prep.read_source(CHAIN_RECORDED / name)
            self.assertLessEqual(len(data), recorder.descriptor.LIMITS["report_bytes"])
            self.assertEqual(overlay.digest(data), expected)
            documents.append(overlay.load_json(data))
        run, report = documents
        self.assertEqual((run["git_sha"], run["result"], run["execution"]), (revision, "fail", "software"))
        self.assertFalse(run["hardware_validated"])
        self.assertEqual((run["started_utc"], run["ended_utc"]), ("2026-10-20T02:11:53Z", "2026-10-20T02:13:17Z"))
        self.assertEqual(run["command"], "python3 -I -S scripts/record-engine-image.py --python-descriptor --slug official-vllm-python-chain")
        self.assertEqual((report["result"], report["stage"], report["cleanup"]), ("fail", "python-descriptor-complete", "confirmed"))
        provenance = report["provenance"]
        self.assertEqual(provenance["git_sha"], revision)
        self.assertEqual(provenance["payload_sha256"], payload)
        self.assertEqual(provenance["source_bundle_sha256"], contract["source_bundle_sha256"])
        self.assertEqual(provenance["validator_source_sha256"], "c18f7f7028758f683f01a67806cbe5db9d44f503c6f58357393329b01a243232")
        self.assertTrue(provenance["raw_oci_metadata_verified"])
        self.assertEqual(provenance["requested_image"], overlay.BASE_IMAGE)
        self.assertEqual(provenance["expected_config_sha256"], contract["official_config"]["sha256"])
        self.assertTrue(report["image"]["raw_config_link_verified"])
        self.assertFalse(report["image"]["local_layers_rehashed"])
        self.assertFalse(report["image"]["local_config_bytes_rehashed"])
        child = report["diagnostic"]
        recorder.descriptor.validate_report(overlay, child, contract)
        self.assertEqual((child["collection"], child["overlay_admission"]), ("pass", "not-evaluated"))
        self.assertEqual(child["preparation"], contract["descriptor_preparation"])
        for row in child["files"][:2]:
            self.assertEqual((row["path"], row["resolved"], row["status"]), ("/usr/bin/python3", "/usr/bin/python3.12", "read"))
            self.assertEqual(row["links"], recorder.descriptor.interpreter_links())
            self.assertEqual(row["sha256"], "e50d468e8b0adfb05733f5b87b3cff34829c4a8c1aea50c865aa8bdfe4bb150f")
            self.assertEqual(row["bytes"], 8020928)
        canaries = child["canaries"]
        self.assertEqual(canaries["status"], "pass")
        self.assertEqual(canaries["blockers"], [])
        expected = "ecf3cf1ec68e98d1e5aca0ea5d79b2645d24746714e824b839a6a42bda96dd77"
        self.assertEqual(canaries["path_sha256"], expected)
        for name in ("fresh", "fork", "spawn", "forkserver", "grandchild"):
            recorder.descriptor.validate_canary(overlay, canaries[name], expected)
        previous = overlay.load_json(recorder.prep.read_source(PREREQUISITES_SECOND / "output.log"))
        self.assertEqual(child["packaging"], previous["diagnostic"]["packaging"])
        self.assertEqual(len(child["worker_sources"]), 14)
        self.assertTrue(all(row["status"] == "read" and row["matches"] for row in child["worker_sources"]))
        for item in (child, report):
            self.assertEqual(item["candidate_status"], "BLOCKED")
            self.assertTrue(all(item[key] is False for key in ("deployable", "abi_validated", "hardware_validated")))

    def test_validator_toolchain_resolution_is_cache_only_and_exact(self):
        env = recorder.offline_environment(self.work / "control")
        info = {"GOVERSION": "go1.24.2", "GOHOSTOS": "darwin", "GOHOSTARCH": "arm64",
                "GOMODCACHE": str(self.work / "modules"), "GOCACHE": str(self.work / "compile-cache")}
        binary = str(self.work / "modules/golang.org/toolchain@v0.0.1-go1.27.2.darwin-arm64/bin/go")
        selection = mock.patch.object(recorder.shutil, "which", return_value="/host/toolchain/bin/go")
        selection.start()
        self.addCleanup(selection.stop)
        with mock.patch.object(recorder, "call", side_effect=[overlay.json_bytes(info), b"go version go1.27.2 darwin/arm64\n"]) as calls:
            selected, qualified = recorder.validator_toolchain(env)
            self.assertEqual(selected, binary)
            self.assertEqual(qualified["GOMODCACHE"], info["GOMODCACHE"])
            self.assertEqual(qualified["GOCACHE"], info["GOCACHE"])
            self.assertEqual(qualified["GOTOOLCHAIN"], "go1.27.2")
        self.assertEqual(calls.call_args_list[0].args[0][0], "/host/toolchain/bin/go")
        self.assertEqual(calls.call_args_list[1].args[0], [binary, "version"])
        for invocation in calls.call_args_list:
            local = invocation.args[1]
            self.assertEqual(local["GOTOOLCHAIN"], "local")
            self.assertEqual(local["GOPROXY"], "off")
            self.assertEqual(local["GOSUMDB"], "off")
            self.assertEqual(local["TEST_TELEMETRY_DIR"], env["TEST_TELEMETRY_DIR"])
            self.assertEqual(local["GO_TELEMETRY_CHILD"], "2")
        self.assertEqual(env["GOTOOLCHAIN"], "go1.27.2")
        with mock.patch.object(recorder, "call", side_effect=[overlay.json_bytes(info), b"go version go1.24.2 darwin/arm64\n"]):
            with self.assertRaises(ValueError):
                recorder.validator_toolchain(env)
        with mock.patch.object(recorder, "call", side_effect=[overlay.json_bytes(info), FileNotFoundError()]):
            with self.assertRaises(FileNotFoundError):
                recorder.validator_toolchain(env)
        info["GOVERSION"] = "go1.27.2"
        with mock.patch.object(recorder, "call", return_value=overlay.json_bytes(info)) as calls:
            self.assertEqual(recorder.validator_toolchain(env)[0], "/host/toolchain/bin/go")
            self.assertEqual(calls.call_count, 1)

    def test_hosted_go_outside_fixed_path_discovers_default_caches(self):
        hosted = self.work / "hosted-toolchain/bin"
        hosted.mkdir(parents=True)
        binary = hosted / "go"
        info = {"GOVERSION": "go1.27.2", "GOHOSTOS": "linux", "GOHOSTARCH": "amd64",
                "GOMODCACHE": str(self.work / "existing-modules"), "GOCACHE": str(self.work / "existing-cache")}
        # Execute a real filesystem fixture, not the engine or a fake validator.
        # Only discovery is synthetic; the authoritative schema test builds Go.
        binary.write_text("#!/bin/sh\nset -eu\n"
            'test "$GOTOOLCHAIN" = local\ntest "$GOENV" = off\ntest "$GOWORK" = off\n'
            'test "$GOPROXY" = off\ntest "$GOSUMDB" = off\n'
            'test "$GO_TELEMETRY_CHILD" = 2\nIFS= read -r mode < "$TEST_TELEMETRY_DIR/mode"\ntest "$mode" = off\n'
            'test -z "${GOPATH+x}${GOMODCACHE+x}${GOCACHE+x}"\n'
            'test "$1" = env\ntest "$2" = -json\n'
            "printf '%s\\n' '" + json.dumps(info) + "'\n")
        binary.chmod(0o700)
        with mock.patch.dict(os.environ, {"PATH": str(hosted), "GOENV": "SYNTHETIC_PRIVATE_VALUE",
                                          "GOPATH": "SYNTHETIC_PRIVATE_VALUE"}, clear=True):
            env = recorder.offline_environment(self.work / "control")
            self.assertNotIn(str(hosted), env["PATH"])
            selected, qualified = recorder.validator_toolchain(env)
        self.assertEqual(selected, str(binary))
        self.assertEqual(qualified["GOMODCACHE"], info["GOMODCACHE"])
        self.assertEqual(qualified["GOCACHE"], info["GOCACHE"])
        self.assertEqual(qualified["HOME"], env["HOME"])
        self.assertEqual(qualified["GOTOOLCHAIN"], "go1.27.2")

    def test_real_go_ignores_account_telemetry_and_preserves_default_cache_discovery(self):
        env = recorder.offline_environment(self.work / "control")
        _, baseline = recorder.validator_toolchain(env)
        account = self.work / "account"
        config = account / ("Library/Application Support" if sys.platform == "darwin" else ".config")
        mode = config / "go/telemetry/mode"
        mode.parent.mkdir(parents=True)
        mode.write_bytes(b"on 2000-01-01\n")
        modules = account / "go/pkg/mod"
        cache = account / ("Library/Caches/go-build" if sys.platform == "darwin" else ".cache/go-build")
        for link, target in ((modules, baseline["GOMODCACHE"]), (cache, baseline["GOCACHE"])):
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target, target_is_directory=True)

        def account_snapshot():
            result = {}
            for path in account.rglob("*"):
                info = path.lstat()
                content = os.readlink(path) if path.is_symlink() else path.read_bytes() if path.is_file() else None
                result[path.relative_to(account).as_posix()] = (info.st_mode, info.st_mtime_ns, content)
            return result

        before = account_snapshot()
        # Real discovery/version calls; only the account identity is synthetic.
        # Both cache environment keys are absent, as on hosted CI.
        with mock.patch.object(recorder.pwd, "getpwuid", return_value=types.SimpleNamespace(pw_dir=str(account))), \
                mock.patch.dict(os.environ, {"PATH": os.environ["PATH"]}, clear=True):
            selected, qualified = recorder.validator_toolchain(env)
        self.assertEqual(qualified["GOMODCACHE"], str(modules))
        self.assertEqual(qualified["GOCACHE"], str(cache))
        self.assertEqual(qualified["GO_TELEMETRY_CHILD"], "2")
        recorder.call([selected, "version"], qualified, timeout=10, limit=4096)
        telemetry = Path(env["TEST_TELEMETRY_DIR"])
        self.assertEqual({path.name for path in telemetry.iterdir()}, {"mode"})
        self.assertEqual((telemetry / "mode").read_bytes(), b"off\n")
        self.assertEqual(account_snapshot(), before)

        # Positive counter control, confined to our temporary directory. Keep
        # CHILD=2 so even this telemetry-on control cannot fork or upload.
        positive = self.work / "positive-telemetry"
        recorder.prep.publish(positive, {"mode": b"on 2000-01-01\n"})
        control = {**qualified, "GOTOOLCHAIN": "local", "HOME": str(account),
                   "TEST_TELEMETRY_DIR": str(positive), "GO_TELEMETRY_CHILD": "2"}
        recorder.call([selected, "version"], control, timeout=10, limit=4096)
        self.assertTrue(list((positive / "local").glob("*.count")))
        self.assertEqual(account_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
