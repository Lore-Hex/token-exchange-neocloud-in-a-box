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
        (self.root / "usr/bin/python3").symlink_to("python3.12")
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
        self.assertEqual(executable["links"], [{"path": "/usr/bin/python3", "target": "/usr/bin/python3.12"}])
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
                        **{key: copy.deepcopy(state) for key in ("fresh", "fork", "spawn", "grandchild", "forkserver")}}
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
