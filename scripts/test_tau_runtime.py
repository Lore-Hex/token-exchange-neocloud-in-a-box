#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Synthetic archive tests only. No upstream imports, installations, or endpoints."""

import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).absolute().parent.parent
spec = importlib.util.spec_from_file_location("tau_runtime", ROOT / "scripts/tau_runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def wheel(name="alpha", version="1.0", *, members=None, metadata=None, wheel_metadata=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name + "/__init__.py", "raise AssertionError('UPSTREAM_MUST_NOT_EXECUTE')\n")
        archive.writestr(name + "-" + version + ".dist-info/METADATA",
                         metadata if metadata is not None else f"Metadata-Version: 2.3\nName: {name}\nVersion: {version}\n")
        archive.writestr(name + "-" + version + ".dist-info/WHEEL",
                         wheel_metadata if wheel_metadata is not None else "Wheel-Version: 1.0\nTag: py3-none-any\n")
        for path, raw in members or []:
            archive.writestr(path, raw)
    return output.getvalue()


def package(name, version, raw, dependencies=()):
    filename = f"{name}-{version}-py3-none-any.whl"
    return {"name": name, "version": version, "source": {"registry": "https://pypi.org/simple"},
            "dependencies": list(dependencies), "wheels": [{
                "url": "https://files.pythonhosted.org/packages/fixture/" + filename,
                "hash": "sha256:" + runtime.digest(raw), "size": len(raw)}]}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir="/private/tmp" if sys.platform == "darwin" else None)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.wheels = {"alpha": wheel(), "beta": wheel("beta", "2.0")}
        self.project = {
            "project": {"name": "tau2", "version": "1.0.1", "requires-python": ">=3.12,<3.14",
                        "dependencies": ["alpha>=1.0"]},
            "build-system": {"requires": ["hatchling"], "build-backend": "hatchling.build"}}
        self.lock = {"package": [
            {"name": "tau2", "version": "1.0.0", "source": {"editable": "."},
             "dependencies": [{"name": "alpha"}],
             "metadata": {"requires-dist": [{"name": "alpha", "specifier": ">=1.0"}]}},
            package("alpha", "1.0", self.wheels["alpha"], [{"name": "beta"},
                    {"name": "windows-only", "marker": "sys_platform == 'win32'"}]),
            package("beta", "2.0", self.wheels["beta"]),
        ]}
        self.sources = {
            "README.md": b"public fixture\n",
            "LICENSE": b"synthetic license\n",
            "src/tau2/__init__.py": b"raise AssertionError('UPSTREAM_MUST_NOT_EXECUTE')\n",
            "src/tau2/empty.py": b"",
            "src/tau2/sub/module.py": b"print('PLANTED_SECRET_DO_NOT_RETAIN')\n",
            "pyproject.toml": b"fixture project",
            "uv.lock": b"fixture upstream lock",
        }
        self.rows = {name: {"bytes": len(raw), "sha256": runtime.digest(raw),
                    "git_blob": runtime.blob(raw), "mode": "100644"} for name, raw in self.sources.items()}
        self.old_pins = {"files": {name: {k: v for k, v in row.items() if k != "mode"}
                          for name, row in self.rows.items() if not name.startswith("src/")}}
        self.tree = runtime.git_tree({name.removeprefix("src/tau2/"): row for name, row in self.rows.items()
                                      if name.startswith("src/tau2/")})
        self.deadline = time.monotonic() + 60

    def toml(self, value):
        if value == "fixture project":
            return copy.deepcopy(self.project)
        if value == "fixture upstream lock":
            return copy.deepcopy(self.lock)
        raise AssertionError("unexpected parser input")

    def source_archive(self, extras=(), removed=()):
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode="w:gz") as archive:
            for name, raw in self.sources.items():
                if name in removed:
                    continue
                info = tarfile.TarInfo(runtime.PREFIX + name)
                info.mode, info.size = 0o644, len(raw)
                archive.addfile(info, io.BytesIO(raw))
            for info, raw in extras:
                archive.addfile(info, io.BytesIO(raw))
        return out.getvalue()

    def source_check(self, raw):
        with mock.patch.object(runtime, "ARCHIVE_SHA", runtime.digest(raw)), \
                mock.patch.object(runtime, "SOURCE_TREE", self.tree):
            return runtime.source_inputs(raw, self.old_pins, self.deadline)

    def catalog(self):
        with mock.patch.object(runtime.tomllib, "loads", side_effect=self.toml):
            return runtime.make_catalog(self.rows, self.sources)

    def expected_wheel(self, raw=None):
        value = self.wheels["alpha"] if raw is None else raw
        return {"filename": "alpha-1.0-py3-none-any.whl", "version": "1.0",
                "sha256": runtime.digest(value), "bytes": len(value)}

    def test_complete_source_tree_includes_empty_files_and_never_executes(self):
        files, sources = self.source_check(self.source_archive())
        self.assertEqual(files, self.rows)
        self.assertEqual(sources, self.sources)
        self.assertNotIn("tau2", sys.modules)

    def test_missing_added_changed_source_and_pin_drift_reject(self):
        for mutate in ("missing", "added", "changed", "pin"):
            with self.subTest(mutate=mutate):
                original = copy.deepcopy(self.sources)
                if mutate == "missing":
                    raw = self.source_archive(removed=("src/tau2/empty.py",))
                else:
                    if mutate == "added":
                        self.sources["src/tau2/new.py"] = b"unexpected"
                    if mutate == "changed":
                        self.sources["src/tau2/sub/module.py"] = b"changed"
                    if mutate == "pin":
                        self.sources["LICENSE"] = b"changed"
                    raw = self.source_archive()
                with self.assertRaises(ValueError):
                    self.source_check(raw)
                self.sources = original

    def test_archive_links_duplicates_traversal_and_bounds_reject(self):
        for kind in ("symlink", "hardlink", "duplicate", "traversal", "oversize"):
            with self.subTest(kind=kind):
                info = tarfile.TarInfo(runtime.PREFIX + "src/tau2/extra.py")
                if kind == "symlink":
                    info.type, info.linkname = tarfile.SYMTYPE, "/secret"
                elif kind == "hardlink":
                    info.type, info.linkname = tarfile.LNKTYPE, runtime.PREFIX + "LICENSE"
                elif kind == "duplicate":
                    info.name = runtime.PREFIX + "LICENSE"
                elif kind == "traversal":
                    info.name = runtime.PREFIX + "../secret"
                raw = self.source_archive(((info, b""),))
                with mock.patch.object(runtime, "SOURCE_LIMIT", 1 if kind == "oversize" else runtime.SOURCE_LIMIT):
                    with self.assertRaises(ValueError):
                        self.source_check(raw)
        self.deadline = 0
        with self.assertRaises(ValueError):
            self.source_check(self.source_archive())

    def test_original_archive_digest_is_required_before_tar_parse(self):
        with mock.patch.object(runtime.tarfile, "open", side_effect=AssertionError("must not parse")):
            with self.assertRaises(ValueError):
                runtime.source_inputs(b"untrusted", self.old_pins, self.deadline)

    def test_declared_base_closure_has_transitive_pins_and_excludes_windows(self):
        value = self.catalog()
        self.assertEqual(set(value["wheels"]), {"alpha", "beta"})
        self.assertEqual(value["wheels"]["alpha"]["dependencies"], ["beta"])
        self.assertEqual(value["root"]["source_version"], "1.0.1")
        self.assertEqual(value["root"]["upstream_lock_version"], "1.0.0")
        self.assertFalse(value["root"]["building_allowed"])
        self.assertFalse(value["root"]["editable_installation_allowed"])
        self.assertFalse(value["root"]["upstream_lock_modified"])
        self.assertFalse(value["runtime_imports_verified"])
        self.assertEqual(self.sources["uv.lock"], b"fixture upstream lock")

    def test_unknown_markers_extras_missing_dependencies_and_no_wheel_reject(self):
        for key in ("marker", "extras", "missing", "wheel", "registry"):
            with self.subTest(key=key):
                original = copy.deepcopy(self.lock)
                row = self.lock["package"][1]
                if key == "marker":
                    row["dependencies"][0]["marker"] = "python_full_version < '999'"
                elif key == "extras":
                    row["dependencies"][0]["extras"] = ["voice"]
                elif key == "missing":
                    row["dependencies"][0]["name"] = "not-locked"
                elif key == "wheel":
                    row["wheels"] = []
                else:
                    row["source"] = {"git": "arbitrary"}
                with self.assertRaises((ValueError, KeyError)):
                    self.catalog()
                self.lock = original

    def test_root_mismatch_is_narrow_and_cannot_hide_dependency_changes(self):
        for mutate in ("project-version", "lock-version", "build", "requirement", "editable"):
            with self.subTest(mutate=mutate):
                project, lock = copy.deepcopy(self.project), copy.deepcopy(self.lock)
                if mutate == "project-version":
                    self.project["project"]["version"] = "1.0.2"
                elif mutate == "lock-version":
                    self.lock["package"][0]["version"] = "1.0.1"
                elif mutate == "build":
                    self.project["build-system"]["requires"].append("unreviewed")
                elif mutate == "requirement":
                    self.project["project"]["dependencies"] = ["alpha>=99"]
                else:
                    self.lock["package"][0]["source"] = {"editable": "/other"}
                with self.assertRaises(ValueError):
                    self.catalog()
                self.project, self.lock = project, lock

    def test_target_compatibility_rejects_other_interpreter_os_arch_and_libc(self):
        for tag in ("cp312-cp312-manylinux_2_28_x86_64", "cp39-abi3-manylinux2014_x86_64",
                    "py2.py3-none-any", "cp312-cp312-manylinux_2_36_x86_64"):
            self.assertTrue(runtime.wheel_tags("alpha-1.0-" + tag + ".whl"), tag)
        for tag in ("cp313-cp313-manylinux_2_28_x86_64", "cp312-cp312-win_amd64",
                    "cp312-cp312-manylinux_2_28_aarch64", "cp312-cp312-musllinux_1_2_x86_64",
                    "cp312-cp312-manylinux_2_37_x86_64", "py2-none-any", "cp311-cp311-any"):
            self.assertFalse(runtime.wheel_tags("alpha-1.0-" + tag + ".whl"), tag)

    def test_wheel_integrity_without_import_or_extraction(self):
        before = set(self.root.iterdir())
        self.assertGreater(runtime.check_wheel(self.wheels["alpha"], self.expected_wheel(), self.deadline), 0)
        self.assertEqual(set(self.root.iterdir()), before)
        self.assertNotIn("alpha", sys.modules)
        with self.assertRaises(ValueError):
            runtime.check_wheel(self.wheels["alpha"] + b"x", self.expected_wheel(), self.deadline)
        with self.assertRaises(ValueError):
            runtime.check_wheel(self.wheels["alpha"], self.expected_wheel(), 0)

    def test_wheel_paths_symlinks_duplicates_metadata_and_zip_limits_reject(self):
        for kind in ("traversal", "absolute", "symlink", "duplicate", "name", "version",
                     "multiple-name", "multiple-metadata", "format", "tags", "budget"):
            with self.subTest(kind=kind):
                kwargs = {}
                if kind == "traversal":
                    kwargs["members"] = [("../secret", b"x")]
                elif kind == "absolute":
                    kwargs["members"] = [("/secret", b"x")]
                elif kind == "symlink":
                    info = zipfile.ZipInfo("alpha/link")
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    kwargs["members"] = [(info, b"/secret")]
                elif kind == "duplicate":
                    kwargs["members"] = [("alpha/__init__.py", b"x")]
                elif kind in ("name", "version", "multiple-name"):
                    kwargs["metadata"] = {"name": "Name: wrong\nVersion: 1.0\n",
                        "version": "Name: alpha\nVersion: 2.0\n",
                        "multiple-name": "Name: alpha\nName: alpha\nVersion: 1.0\n"}[kind]
                elif kind == "multiple-metadata":
                    kwargs["members"] = [("other.dist-info/METADATA", b"Name: other\nVersion: 1\n")]
                elif kind == "format":
                    kwargs["wheel_metadata"] = "Wheel-Version: 2.0\n"
                elif kind == "tags":
                    kwargs["wheel_metadata"] = "Wheel-Version: 1.0\nTag: cp313-cp313-win_amd64\n"
                raw = wheel(**kwargs)
                if kind == "budget":
                    with mock.patch.object(runtime.time, "monotonic", return_value=self.deadline):
                        with self.assertRaises(ValueError):
                            runtime.check_wheel(raw, self.expected_wheel(raw), self.deadline)
                else:
                    with self.assertRaises(ValueError):
                        runtime.check_wheel(raw, self.expected_wheel(raw), self.deadline)

    def test_input_reader_links_fifo_bounds_and_parent_symlink_reject(self):
        path = self.root / "input"
        path.write_bytes(b"data")
        self.assertEqual(runtime.read_file(path, 4, self.deadline), b"data")
        for kind in ("symlink", "hardlink", "fifo", "parent"):
            destination = self.root / kind
            if kind == "symlink":
                destination.symlink_to(path)
            elif kind == "hardlink":
                os.link(path, destination)
            elif kind == "fifo":
                os.mkfifo(destination)
            else:
                destination.symlink_to(self.root, target_is_directory=True)
                destination /= "input"
            with self.assertRaises((ValueError, OSError)):
                runtime.read_file(destination, 4, self.deadline)
            if kind == "hardlink":
                destination.unlink()
        with self.assertRaises(ValueError):
            runtime.read_file(path, 3, self.deadline)
        with self.assertRaises(ValueError):
            runtime.read_file(path, 4, 0)

    def test_synthetic_end_to_end_receipt_is_private_and_never_runtime_success(self):
        raw = self.source_archive()
        wheelhouse = self.root / "wheels"
        wheelhouse.mkdir()
        for name, value in self.wheels.items():
            version = "1.0" if name == "alpha" else "2.0"
            (wheelhouse / f"{name}-{version}-py3-none-any.whl").write_bytes(value)
        with mock.patch.object(runtime, "ARCHIVE_SHA", runtime.digest(raw)), \
                mock.patch.object(runtime, "SOURCE_TREE", self.tree), \
                mock.patch.object(runtime.tomllib, "loads", side_effect=self.toml):
            catalog = runtime.make_catalog(self.rows, self.sources)
            report = runtime.validate(raw, wheelhouse, catalog, self.old_pins, {"synthetic": True}, self.deadline)
            self.assertEqual(report["verified_wheels"], 2)
            for key in ("runtime_verified", "dependencies_installed", "upstream_executed",
                        "benchmark_executed", "quality_accepted", "confidential"):
                self.assertIs(report[key], False)
            self.assertNotIn("PLANTED_SECRET", runtime.encoded(report).decode())
            destination = self.root / "receipt"
            runtime.tau.publish(destination, report, filename="summary.json")
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((destination / "summary.json").stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                runtime.tau.publish(destination, report, filename="summary.json")
            (wheelhouse / "beta-2.0-py3-none-any.whl").unlink()
            with self.assertRaises(FileNotFoundError):
                runtime.validate(raw, wheelhouse, catalog, self.old_pins, {}, self.deadline)
            source_only = runtime.validate(raw, None, catalog, self.old_pins, {}, self.deadline)
            self.assertEqual(source_only["status"], "source-inputs-verified")
            self.assertEqual(source_only["verified_wheels"], 0)
            self.assertEqual(source_only["wheel_bytes"], 0)
            self.assertFalse(source_only["selected_wheel_bytes_verified"])
            self.assertFalse(source_only["runtime_verified"])

    def test_committed_catalog_binds_existing_pins_and_never_claims_runtime_closure(self):
        raw = (ROOT / runtime.CATALOG).read_bytes()
        self.assertEqual(runtime.digest(raw), runtime.CATALOG_SHA)
        catalog = json.loads(raw)
        old = json.loads((ROOT / runtime.tau.PINS).read_bytes())
        for name, row in old["files"].items():
            self.assertEqual({key: catalog["source_files"][name][key] for key in row}, row)
        source = {name.removeprefix("src/tau2/"): row for name, row in catalog["source_files"].items()
                  if name.startswith("src/tau2/")}
        self.assertEqual(len(source), 252)
        self.assertEqual(runtime.git_tree(source), runtime.SOURCE_TREE)
        self.assertEqual(len(catalog["wheels"]), 74)
        self.assertEqual(sum(row["bytes"] for row in catalog["wheels"].values()), 66315648)
        self.assertEqual(catalog["target"]["image"],
                         json.loads((ROOT / "scripts/e2e/pins.json").read_bytes())["python_image"])
        self.assertFalse(catalog["runtime_imports_verified"])
        self.assertFalse(catalog["content_logging_suppression_verified"])

    def test_cli_fixed_failure_cannot_emit_private_arguments_or_tracebacks(self):
        marker = "PRIVATE_SENTINEL_DO_NOT_PRINT"
        result = subprocess.run([sys.executable, "-I", "-S", "-B", str(ROOT / runtime.SCRIPT),
                                 "--" + marker], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=10, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertNotIn(marker.encode(), result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)
        self.assertEqual(result.stderr, b"Tau runtime inputs rejected; no package or benchmark was executed.\n")


if __name__ == "__main__":
    unittest.main()
