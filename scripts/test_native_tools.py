# SPDX-License-Identifier: Apache-2.0
"""Synthetic fetch/materialization/lifecycle checks; no network or namespaces."""
import ast
import copy
import io
import json
from pathlib import Path
import runpy
import subprocess
import tarfile
import tempfile
import types
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
host = types.SimpleNamespace(**runpy.run_path(str(ROOT / "host/probe-mkosi-native-tools.py"), run_name="test_native_tools"))
fetcher, tools, base = host.fetcher, host.tools, host.base
ORIGINAL = [name + ":[100]" for name in ("user", "mnt", "net", "ipc")]


def lock():
    return {"scope": "mkosi-build-tools-dependency-coverage", "archive_bytes": 3,
            "indexes": [{"id": "noble-main", "file": "noble-main.xz", "encoding": "xz",
                "base_url": "https://archive.ubuntu.com/ubuntu/", "bytes": 3,
                "sha256": base.digest(b"idx"), "index_path": "main/binary-amd64/Packages.xz",
                "inrelease_url": "https://archive.ubuntu.com/ubuntu/dists/noble/InRelease"}],
            "packages": [{"Package": "synthetic", "index": "noble-main", "Size": "3",
                "SHA256": base.digest(b"deb"), "Filename": "pool/main/s/synthetic/synthetic_1_amd64.deb"}]}


def files(tree=None):
    tree = copy.deepcopy(tree or {"usr/bin/tool": {"kind": "file", "mode": 0o755, "data": b"synthetic"},
                                 "bin": {"kind": "link", "mode": 0o777, "target": "usr/bin"}})
    raw = tools.tar_bytes(tree)
    return {"tools-root.tar": raw, "root-inventory.json": base.encoded(tools.observation(tree))}


class FetchTests(unittest.TestCase):
    def test_real_committed_plan_has_exact_bounded_inputs(self):
        value = json.loads((ROOT / fetcher.prep.LOCK).read_bytes())
        items = fetcher.plan(value)
        self.assertEqual(len(items), 209)
        self.assertEqual(len([p for p in items if p.startswith("repo/pool/")]), 203)
        for name, (url, _, sha) in items.items():
            self.assertTrue(url.startswith(("https://archive.ubuntu.com/", "https://security.ubuntu.com/")))
            if name.startswith("metadata/"):
                self.assertTrue(url.endswith("/by-hash/SHA256/" + sha))

    def test_fetch_is_exact_content_bounded_and_scrubs_environment(self):
        raw = base.encoded(lock())
        responses = [b"idx", b"deb"]
        def execute(argv, **kw):
            self.assertEqual(argv[:4], ["/usr/bin/curl", "--disable", "--noproxy", "*"])
            self.assertNotIn("--location", argv)
            self.assertNotIn("--retry", argv)
            self.assertEqual(argv[argv.index("--max-filesize") + 1], "3")
            self.assertEqual(kw, {"env": fetcher.ENV, "timeout": 60,
                "max_output_bytes": 65539, "stdin": subprocess.DEVNULL})
            return subprocess.CompletedProcess(argv, 0, responses.pop(0), b"")
        result = fetcher.fetch(raw, execute, clock=lambda: 0)
        self.assertEqual(result["mkosi-tools-inputs.json"], raw)
        self.assertEqual(result["metadata/noble-main.xz"], b"idx")
        self.assertEqual(result["repo/pool/synthetic_" + base.digest(b"deb") + ".deb"], b"deb")

    def test_wrong_hash_length_stderr_and_http_failure_reject(self):
        for output, error, code in ((b"bad", b"", 0), (b"id", b"", 0),
                                     (b"idx", b"private", 0), (b"idx", b"", 22)):
            execute = mock.Mock(return_value=subprocess.CompletedProcess([], code, output, error))
            with self.subTest(output=output, error=error, code=code), \
                    self.assertRaises((ValueError, subprocess.SubprocessError)):
                fetcher.fetch(base.encoded(lock()), execute, clock=lambda: 0)
            self.assertEqual(execute.call_count, 1)

    def test_total_deadline_does_not_reset_on_each_fetch(self):
        execute = mock.Mock(return_value=subprocess.CompletedProcess([], 0, b"idx", b""))
        clock = mock.Mock(side_effect=[0, 590, 601])
        with self.assertRaises(ValueError):
            fetcher.fetch(base.encoded(lock()), execute, clock=clock)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(execute.call_args.kwargs["timeout"], 10)

    def test_no_untrusted_network_destinations_or_paths(self):
        for url in ("http://archive.ubuntu.com/ubuntu/a", "https://evil.test/ubuntu/a",
                    "https://user@archive.ubuntu.com/ubuntu/a", "https://archive.ubuntu.com:443/ubuntu/a",
                    "https://archive.ubuntu.com/ubuntu/../a", "https://archive.ubuntu.com/ubuntu/%2e/a",
                    "https://archive.ubuntu.com/ubuntu/a?x=1", "https://archive.ubuntu.com/ubuntu/a#x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                fetcher.public_url(url)

    def test_duplicate_oversized_and_nonhash_inputs_reject(self):
        candidates = []
        value = lock(); value["packages"] *= 2; candidates.append(value)
        value = lock(); value["indexes"] *= 2; candidates.append(value)
        value = lock(); value["indexes"][0]["bytes"] = 33 << 20; candidates.append(value)
        value = lock(); value["packages"][0]["SHA256"] = "x" * 64; candidates.append(value)
        value = lock(); value["archive_bytes"] = 4; candidates.append(value)
        value = lock(); value["packages"][0]["Filename"] = "pool/../../bad"; candidates.append(value)
        value = lock(); value["packages"][0]["Package"] = "../../bad"; candidates.append(value)
        value = lock(); value["packages"][0]["Size"] = True; candidates.append(value)
        for value in candidates:
            with self.subTest(value=value), self.assertRaises(ValueError):
                fetcher.plan(value)


class MaterializationTests(unittest.TestCase):
    def test_files_written_before_links_without_owner_escalation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            host.materialize(files(), root)
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertEqual((root / "usr/bin/tool").read_bytes(), b"synthetic")
            self.assertEqual((root / "usr/bin/tool").stat().st_mode & 0o7777, 0o755)
            self.assertTrue((root / "bin").is_symlink())
            self.assertTrue((root / "proc/self/ns").is_dir())

    def test_absolute_link_is_created_but_never_traversed(self):
        value = files({"usr/share/data": {"kind": "file", "mode": 0o444, "data": b"synthetic"},
                       "link": {"kind": "link", "mode": 0o777, "target": "/usr/share/data"}})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            host.materialize(value, root)
            self.assertEqual((root / "link").readlink(), Path("/usr/share/data"))
            self.assertEqual((root / "usr/share/data").read_bytes(), b"synthetic")

    def test_changed_inventory_and_tar_cannot_materialize(self):
        for name in ("tools-root.tar", "root-inventory.json"):
            value = files()
            value[name] = value[name].replace(b"synthetic", b"corrupted", 1) if name.endswith("tar") else b"{}"
            with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
                host.materialize(value, Path(temporary) / "root")

    def test_existing_destination_or_symlink_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            root.symlink_to(Path(temporary) / "absent")
            with self.assertRaises(ValueError):
                host.materialize(files(), root)
            self.assertTrue(root.is_symlink())

    def test_unsafe_tar_types_and_paths_fail_before_publication(self):
        for name, kind in (("../escape", tarfile.REGTYPE), ("/escape", tarfile.REGTYPE),
                           ("device", tarfile.CHRTYPE), ("hard", tarfile.LNKTYPE)):
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w") as archive:
                row = tarfile.TarInfo(name)
                row.type, row.mtime, row.mode = kind, tools.EPOCH, 0o644
                archive.addfile(row)
            value = {"tools-root.tar": stream.getvalue(), "root-inventory.json": base.encoded({name: {}})}
            with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
                host.materialize(value, Path(temporary) / "root")

    def test_reserved_mountpoint_cannot_traverse_a_package_link(self):
        value = files({"proc": {"kind": "link", "mode": 0o777, "target": "/outside"}})
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
            host.materialize(value, Path(temporary) / "root")


class LifecycleTests(unittest.TestCase):
    def run_probe(self, *, stdout=host.SUCCESS, stderr=b"", code=0, error=None):
        execute = mock.Mock(side_effect=error, return_value=subprocess.CompletedProcess([], code, stdout, stderr))
        real_readlink = host.os.readlink
        def readlink(path, *args, **kwargs):
            names = ("user", "mnt", "net", "ipc")
            for name, original in zip(names, ORIGINAL):
                if str(path) == "/proc/self/ns/" + name:
                    return original
            return real_readlink(path, *args, **kwargs)
        with mock.patch.object(host.os, "readlink", side_effect=readlink):
            report = host.run(files(), {"mkosi-sandbox.py": b"synthetic"}, execute)
        return report, execute

    def test_pinned_root_replaces_host_runtime_and_cleanup_passes(self):
        report, execute = self.run_probe()
        argv = execute.call_args.args[0]
        self.assertEqual(argv[0], "/usr/bin/python3.10")
        self.assertEqual(argv[5], "--ro-bind")
        self.assertEqual(argv[7], "/")
        self.assertNotIn("/usr", argv)
        self.assertNotIn("/proc", argv)
        self.assertEqual(argv[-9:], ["/usr/bin/python3.12", "-I", "-S", "-B", "/nib/native-tools.py", *ORIGINAL])
        self.assertFalse(Path(argv[6]).exists())
        self.assertTrue(report["payload_removed"])
        self.assertTrue(report["process_cleanup_confirmed"])
        self.assertEqual(report["tools_checked"], 7)
        self.assertEqual(report["result"], "pass")
        for key in ("package_installation_executed", "maintainer_scripts_executed",
                    "mkosi_build_executed", "abi_validated", "bootable", "deployable"):
            self.assertIs(report[key], False)
        self.assertEqual(execute.call_args.kwargs, {"env": host.native.worker.ENV,
            "timeout": 90, "max_output_bytes": 65536, "stdin": subprocess.DEVNULL})

    def test_no_partial_success_or_diagnostic_leaks(self):
        for options in ({"stdout": b"wrong"}, {"stderr": b"PRIVATE"}, {"code": 1},
                        {"error": subprocess.TimeoutExpired(["PRIVATE"], 90)}, {"error": OSError("PRIVATE")}):
            report, _ = self.run_probe(**options)
            self.assertEqual(report["result"], "fail")
            self.assertFalse(report["namespaces_validated"])
            self.assertEqual(report["tools_checked"], 0)
            self.assertTrue(report["payload_removed"])
            self.assertNotIn("PRIVATE", json.dumps(report))

    def test_materialization_failure_never_executes(self):
        execute = mock.Mock()
        report = host.run({"tools-root.tar": b"bad", "root-inventory.json": b"{}"}, {}, execute)
        execute.assert_not_called()
        self.assertEqual(report["stage"], "materialization")
        self.assertTrue(report["payload_removed"])

    def test_syntax_and_workflow_restrictions(self):
        for path in (host.SCRIPT, host.FETCHER):
            ast.parse((ROOT / path).read_bytes(), feature_version=(3, 10))
        workflow = (ROOT / ".github/workflows/mkosi-native-tools.yml").read_text()
        for forbidden in ("sudo", "secrets.", "sysctl", "--privileged", "apt-get", "pip install"):
            self.assertNotIn(forbidden, workflow)
        value = yaml.safe_load(workflow)
        self.assertEqual(value["jobs"]["probe"]["runs-on"], "ubuntu-22.04")
        self.assertEqual(value["permissions"], {"contents": "read"})


class ChildTests(unittest.TestCase):
    def run_child(self, *, namespace_error=None, probe_result="pass", bad_snapshot=False):
        module = types.ModuleType("native_tools_child_test")
        exec(compile((ROOT / host.CHILD).read_bytes(), host.CHILD, "exec"), module.__dict__)
        snapshot = b"def execute_process_group(*args, **kwargs): raise AssertionError('synthetic')\n"
        config = {"schema": "nib.mkosi-tools-probe-input/v1", "git_sha": "a" * 40,
                  "tools_receipt_sha256": "b" * 64,
                  "snapshot_sha256": "c" * 64 if bad_snapshot else base.digest(snapshot)}
        check = mock.Mock(side_effect=namespace_error)
        probe = mock.Mock(return_value={"result": probe_result, "checks": [None] * 7})
        worker = dict(host.native.worker.__dict__)
        reader = mock.Mock(side_effect=[base.encoded(config), snapshot])
        worker.update(read=reader, probe=probe)
        output = io.BytesIO()
        error = None
        with mock.patch.object(module.sys, "platform", "linux"), \
                mock.patch.object(module.sys, "version_info", (3, 12)), \
                mock.patch.object(module.sys, "flags", types.SimpleNamespace(isolated=True, no_site=True, dont_write_bytecode=True)), \
                mock.patch.object(module.sys, "argv", ["native-tools.py", *ORIGINAL]), \
                mock.patch.object(module.sys, "stdout", types.SimpleNamespace(buffer=output)), \
                mock.patch.object(module.os, "geteuid", return_value=0), \
                mock.patch.object(module.os, "getegid", return_value=0), \
                mock.patch.object(module.runpy, "run_path", side_effect=[worker, {"check": check}]):
            try:
                module.main()
            except ValueError as exc:
                error = exc
        return error, output.getvalue(), reader, check, probe

    def test_child_requires_namespace_proof_before_seven_tools(self):
        error, output, reader, check, probe = self.run_child()
        self.assertIsNone(error)
        self.assertEqual(output, host.SUCCESS)
        check.assert_called_once_with(ORIGINAL)
        self.assertEqual(reader.call_count, 2)
        self.assertEqual(probe.call_count, 1)

    def test_namespace_or_snapshot_failure_never_starts_tools(self):
        for options in ({"namespace_error": ValueError("synthetic")}, {"bad_snapshot": True}):
            error, output, _, _, probe = self.run_child(**options)
            self.assertIsNotNone(error)
            self.assertEqual(output, b"")
            probe.assert_not_called()

    def test_failed_tools_do_not_emit_success(self):
        error, output, _, _, _ = self.run_child(probe_result="fail")
        self.assertIsNotNone(error)
        self.assertEqual(output, b"")


if __name__ == "__main__":
    unittest.main()
