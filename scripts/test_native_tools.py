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
    def run_probe(self, *, stdout=host.SUCCESS, stderr=b"", code=0, error=None, pid_isolation=False):
        execute = mock.Mock(side_effect=error, return_value=subprocess.CompletedProcess([], code, stdout, stderr))
        real_readlink = host.os.readlink
        def readlink(path, *args, **kwargs):
            names = ("user", "mnt", "net", "ipc", "pid")
            for name, original in zip(names, [*ORIGINAL, "pid:[100]"]):
                if str(path) == "/proc/self/ns/" + name:
                    return original
            return real_readlink(path, *args, **kwargs)
        with mock.patch.object(host.os, "readlink", side_effect=readlink):
            report = host.run(files(), {"mkosi-sandbox.py": b"synthetic"}, execute, pid_isolation=pid_isolation)
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

    def test_pid_mode_uses_pinned_unshare_and_kills_descendants(self):
        report, execute = self.run_probe(pid_isolation=True)
        argv = execute.call_args.args[0]
        offset = argv.index("/usr/bin/unshare")
        proc = argv.index("/proc")
        self.assertEqual(argv[proc - 1:proc + 2], ["--ro-bind", "/proc", "/proc"])
        self.assertNotIn("/proc/self/ns", argv)
        self.assertLess(proc, offset)
        self.assertEqual(argv[offset:offset + 6], ["/usr/bin/unshare", "--pid", "--fork",
            "--mount-proc=/proc", "--kill-child=KILL", "--"])
        self.assertEqual(argv[-5:], [*ORIGINAL, "pid:[100]"])
        self.assertEqual(report["schema"], "nib.mkosi-native-tools/v2")
        self.assertTrue(report["pid_namespace_validated"])
        self.assertTrue(report["host_processes_hidden"])
        self.assertTrue(report["process_cleanup_confirmed"])
        self.assertFalse(report["package_installation_executed"])

    def test_pid_failure_never_claims_process_isolation(self):
        for options in ({"code": 1}, {"stdout": b"wrong"}, {"error": OSError("synthetic")}):
            report, _ = self.run_probe(pid_isolation=True, **options)
            self.assertEqual(report["result"], "fail")
            self.assertFalse(report["pid_namespace_validated"])
            self.assertFalse(report["host_processes_hidden"])
            self.assertTrue(report["payload_removed"])

    def test_failure_classes_never_persist_process_output(self):
        cases = {
            b"unshare: mount PRIVATE /proc failed: Operation not permitted\n": "unshare-permission-denied",
            b"unshare: PRIVATE: No such file or directory\n": "unshare-missing-path",
            b"unshare: PRIVATE: Invalid argument\n": "unshare-invalid-argument",
            b"unshare: PRIVATE\n": "unshare-exit",
            b"PRIVATE\n": "process-exit",
            b"NIB_NATIVE_TOOLS_REJECTED:pid\nPRIVATE\n": "process-exit",
        }
        cases.update({("NIB_NATIVE_TOOLS_REJECTED:" + stage + "\n").encode(): "worker-" + stage
                      for stage in host.FAILURE_STAGES})
        for stderr, expected in cases.items():
            with self.subTest(expected=expected):
                report, _ = self.run_probe(code=1, stderr=stderr, pid_isolation=True)
                self.assertEqual(report["failure_kind"], expected)
                self.assertEqual(report["result"], "fail")
                self.assertFalse(report["pid_namespace_validated"])
                self.assertTrue(report["payload_removed"])
                self.assertNotIn("PRIVATE", json.dumps(report))
        self.assertEqual(host.failure_kind(subprocess.TimeoutExpired("PRIVATE", 1)), "timeout")
        self.assertEqual(host.failure_kind(OSError("PRIVATE")), "startup-error")
        self.assertEqual(host.failure_kind(ValueError("PRIVATE")), "invalid-result-or-cleanup")

    def test_pid_mode_requires_an_actual_boolean(self):
        for value in ("true", 1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                host.run({}, {}, mock.Mock(), pid_isolation=value)

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
    def run_child(self, *, namespace_error=None, probe_result="pass", bad_snapshot=False,
                  pid=False, pid_error=None):
        module = types.ModuleType("native_tools_child_test")
        exec(compile((ROOT / host.CHILD).read_bytes(), host.CHILD, "exec"), module.__dict__)
        snapshot = b"def execute_process_group(*args, **kwargs): raise AssertionError('synthetic')\n"
        config = {"schema": "nib.mkosi-tools-probe-input/v1", "git_sha": "a" * 40,
                  "tools_receipt_sha256": "b" * 64,
                  "snapshot_sha256": "c" * 64 if bad_snapshot else base.digest(snapshot)}
        check = mock.Mock(side_effect=namespace_error)
        probe = mock.Mock(return_value={"result": probe_result, "checks": [None] * 7})
        pid_check = mock.Mock(side_effect=pid_error)
        worker = dict(host.native.worker.__dict__)
        reader = mock.Mock(side_effect=[base.encoded(config), snapshot])
        worker.update(read=reader, probe=probe)
        output = io.BytesIO()
        error = None
        with mock.patch.object(module.sys, "platform", "linux"), \
                mock.patch.object(module.sys, "version_info", (3, 12)), \
                mock.patch.object(module.sys, "flags", types.SimpleNamespace(isolated=True, no_site=True, dont_write_bytecode=True)), \
                mock.patch.object(module.sys, "argv", ["native-tools.py", *ORIGINAL, *(["pid:[100]"] if pid else [])]), \
                mock.patch.object(module.sys, "stdout", types.SimpleNamespace(buffer=output)), \
                mock.patch.object(module.os, "geteuid", return_value=0), \
                mock.patch.object(module.os, "getegid", return_value=0), \
                mock.patch.object(module.runpy, "run_path", side_effect=[worker, {"check": check}, {"check": pid_check}]):
            try:
                module.main()
            except ValueError as exc:
                error = exc
        if pid:
            pid_check.assert_called_once_with("pid:[100]")
        else:
            pid_check.assert_not_called()
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

    def test_pid_proof_precedes_every_tool_and_failure_stops(self):
        error, output, _, _, probe = self.run_child(pid=True)
        self.assertIsNone(error)
        self.assertEqual(output, host.SUCCESS)
        probe.assert_called_once()
        error, output, reader, _, probe = self.run_child(pid=True, pid_error=ValueError("synthetic"))
        self.assertIsNotNone(error)
        self.assertEqual(error.stage, "pid")
        self.assertEqual(output, b"")
        reader.assert_not_called()
        probe.assert_not_called()

    def test_child_reports_fixed_stage_not_exception_text(self):
        for options, expected in (({"namespace_error": OSError("PRIVATE")}, "namespaces"),
                                  ({"bad_snapshot": True}, "inputs"),
                                  ({"probe_result": "fail"}, "tools")):
            error, output, _, _, _ = self.run_child(**options)
            self.assertEqual(error.stage, expected)
            self.assertEqual(str(error), expected)
            self.assertEqual(output, b"")


class PIDTests(unittest.TestCase):
    def check(self, *, original="pid:[100]", current="pid:[101]", pid=1, ppid=0,
              self_link="1", entries=("1", "self", "sys"), status="NSpid:\t1\nPPid:\t0\n"):
        checker = types.SimpleNamespace(**runpy.run_path(str(ROOT / host.PID_CHECKER)))
        rows = mock.MagicMock()
        rows.__enter__.return_value = iter(types.SimpleNamespace(name=name) for name in entries)
        with mock.patch.object(checker.os, "getpid", return_value=pid), \
                mock.patch.object(checker.os, "getppid", return_value=ppid), \
                mock.patch.object(checker.os, "readlink", side_effect=[current, self_link]), \
                mock.patch.object(checker.os, "scandir", return_value=rows), \
                mock.patch.object(checker.Path, "read_text", return_value=status):
            checker.check(original)

    def test_fresh_pid_one_with_own_proc_passes(self):
        self.check()

    def test_same_namespace_or_wrong_process_identity_fails(self):
        for options in ({"current": "pid:[100]"}, {"current": "mnt:[101]"},
                        {"original": "bad"}, {"pid": 8}, {"ppid": 100}, {"self_link": "100"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.check(**options)

    def test_host_processes_visible_or_wrong_procfs_fails(self):
        for options in ({"entries": ("1", "100")}, {"entries": ()},
                        {"status": "NSpid:\t100 1\nPPid:\t0\n"},
                        {"status": "NSpid:\t1\nPPid:\t100\n"},
                        {"status": ""}, {"status": "a" * 16384}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.check(**options)


if __name__ == "__main__":
    unittest.main()
