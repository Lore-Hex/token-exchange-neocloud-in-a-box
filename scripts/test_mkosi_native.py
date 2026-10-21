# SPDX-License-Identifier: Apache-2.0
"""Synthetic native-runner lifecycle; never unshare the test host."""
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
host = types.ModuleType("native_probe_tests")
host.__file__ = str(ROOT / "host/probe-mkosi-native.py")
exec(compile(Path(host.__file__).read_bytes(), host.__file__, "exec"), host.__dict__)
ORIGINAL = [name + ":[100]" for name in ("user", "mnt", "net", "ipc")]


class NativeProbeTests(unittest.TestCase):
    def call(self, *, stdout=host.worker.SANDBOX_SUCCESS, stderr=b"", code=0, error=None):
        execute = mock.Mock(side_effect=error, return_value=subprocess.CompletedProcess([], code, stdout, stderr))
        with mock.patch.object(host.worker.os, "readlink", side_effect=ORIGINAL):
            result = host.probe(Path("/private/payload"), execute)
        return result, execute

    def test_fixed_command_uses_read_only_payload_and_same_checker(self):
        result, execute = self.call()
        self.assertEqual(result["result"], "pass")
        self.assertTrue(result["namespaces_validated"])
        self.assertTrue(result["process_cleanup_confirmed"])
        argv = execute.call_args.args[0]
        self.assertEqual(argv[:5], ["/usr/bin/python3.12", "-I", "-S", "-B", "/private/payload/mkosi-sandbox.py"])
        self.assertEqual(argv[5:20], [part for src, dst in
            (("/usr", "/usr"), ("/lib", "/lib"), ("/lib64", "/lib64"),
             ("/private/payload", "/nib"), ("/proc/self/ns", "/proc/self/ns"))
            for part in ("--ro-bind", src, dst)])
        self.assertNotIn("/proc", argv)
        self.assertEqual(argv[20:], ["--dev", "/dev", "--tmpfs", "/tmp", "--unshare-net", "--unshare-ipc",
            "--become-root", "/usr/bin/python3.12", "-I", "-S", "-B", "/nib/namespace-check.py", *ORIGINAL])
        self.assertEqual(execute.call_args.kwargs, {"env": host.worker.ENV, "timeout": 10,
            "max_output_bytes": 65536, "stdin": subprocess.DEVNULL})
        for field in ("host_runtime_pinned", "mkosi_build_executed", "package_installation_executed",
                      "maintainer_scripts_executed", "bootable", "deployable"):
            self.assertIs(result[field], False)
        self.assertNotIn("100", json.dumps(result))

    def test_nonsemantic_success_never_promotes(self):
        for options in ({"stdout": b"version"}, {"stdout": host.worker.SANDBOX_SUCCESS + b"extra"},
                        {"stderr": b"PRIVATE"}, {"code": 1}):
            with self.subTest(options=options):
                result, _ = self.call(**options)
                self.assertEqual(result["result"], "fail")
                self.assertFalse(result["namespaces_validated"])
                self.assertNotIn("PRIVATE", json.dumps(result))

    def test_failures_are_classified_without_raw_diagnostics(self):
        for error, kind in ((subprocess.TimeoutExpired(["PRIVATE"], 10), "timeout"),
                            (OSError("PRIVATE"), "startup-error"),
                            (ValueError("PRIVATE"), "invalid-result-or-cleanup")):
            with self.subTest(kind=kind):
                result, _ = self.call(error=error)
                self.assertEqual(result["failure_kind"], kind)
                self.assertFalse(result["process_cleanup_confirmed"])
                self.assertNotIn("PRIVATE", json.dumps(result))
        result, _ = self.call(code=1, stderr=b"PRIVATE: Operation not permitted")
        self.assertEqual(result["failure_kind"], "permission-denied")
        self.assertTrue(result["process_cleanup_confirmed"])

    def test_namespace_lookup_failure_never_executes(self):
        execute = mock.Mock()
        with mock.patch.object(host.worker.os, "readlink", side_effect=OSError("PRIVATE")):
            result = host.probe(Path("/private/payload"), execute)
        execute.assert_not_called()
        self.assertFalse(result["mkosi_executed"])

    def test_temporary_payload_is_private_verified_and_removed(self):
        payloads = []
        files = {"mkosi-sandbox.py": b"synthetic upstream", "namespace-check.py": b"synthetic checker"}
        def check(payload, execute):
            payloads.append(payload)
            self.assertEqual(payload.stat().st_mode & 0o777, 0o700)
            self.assertEqual(payload.parent.stat().st_mode & 0o777, 0o700)
            for name, data in files.items():
                self.assertEqual((payload / name).read_bytes(), data)
            return {"result": "fail", "payload_removed": False}
        with mock.patch.object(host, "probe", side_effect=check):
            result = host.run(files, mock.Mock())
        self.assertTrue(result["payload_removed"])
        self.assertFalse(payloads[0].parent.exists())
        self.assertEqual(result["result"], "fail")

    def test_unexpected_failure_still_removes_payload(self):
        payloads = []
        def failed(payload, execute):
            payloads.append(payload)
            raise RuntimeError("synthetic")
        with mock.patch.object(host, "probe", side_effect=failed), self.assertRaises(RuntimeError):
            host.run({"synthetic": b"synthetic"}, mock.Mock())
        self.assertFalse(payloads[0].parent.exists())

    def test_unprivileged_native_hosted_runner_required(self):
        for system, machine, version, uid, gid, runner in (
                ("linux", "x86_64", (3, 12), 1001, 1001, "github-hosted"),
                ("darwin", "x86_64", (3, 12), 1001, 1001, "github-hosted"),
                ("linux", "aarch64", (3, 12), 1001, 1001, "github-hosted"),
                ("linux", "x86_64", (3, 11), 1001, 1001, "github-hosted"),
                ("linux", "x86_64", (3, 12), 0, 1001, "github-hosted"),
                ("linux", "x86_64", (3, 12), 1001, 0, "github-hosted"),
                ("linux", "x86_64", (3, 12), 1001, 1001, "self-hosted")):
            with mock.patch.object(host.sys, "platform", system), \
                    mock.patch.object(host.platform, "machine", return_value=machine), \
                    mock.patch.object(host.sys, "version_info", version), \
                    mock.patch.object(host.os, "geteuid", return_value=uid), \
                    mock.patch.object(host.os, "getegid", return_value=gid), \
                    mock.patch.dict(host.os.environ, {"RUNNER_ENVIRONMENT": runner}, clear=True):
                if system == "linux" and machine == "x86_64" and version == (3, 12) and uid and gid and runner == "github-hosted":
                    host.preflight()
                else:
                    with self.assertRaises(ValueError):
                        host.preflight()

    def test_source_authentication_precedes_execution_and_preserves_licenses(self):
        pin = {"license_files": ["LICENSES/LGPL-2.1-or-later.txt"]}
        sources = {host.tools.FILES[0]: host.mkosi.TOOLS_SOURCE, host.WORKER: host.WORKER_SOURCE,
                   host.mkosi.EXTRA[1]: json.dumps(pin).encode(), host.CHECKER: b"checker"}
        upstream = {"mkosi/sandbox.py": b"upstream", pin["license_files"][0]: b"license"}
        with mock.patch.object(host.tools, "committed", return_value=("a" * 40, sources)), \
                mock.patch.object(host.base, "read", return_value=b"archive"), \
                mock.patch.object(host.mkosi, "verify_source", return_value=({}, upstream)) as verify:
            revision, files, _ = host.inputs(Path("/private/archive"))
        verify.assert_called_once_with(b"archive", pin)
        self.assertEqual(revision, "a" * 40)
        self.assertEqual(files, {"mkosi-sandbox.py": b"upstream", "namespace-check.py": b"checker",
                                pin["license_files"][0]: b"license"})
        with mock.patch.object(host.tools, "committed", return_value=("a" * 40, sources)), \
                mock.patch.object(host.base, "read", return_value=b"archive"), \
                mock.patch.object(host.mkosi, "verify_source", side_effect=ValueError), self.assertRaises(ValueError):
            host.inputs(Path("/private/archive"))

    def test_evidence_does_not_claim_image_or_hardware(self):
        report, _ = self.call()
        evidence = host.evidence(report, "a" * 40, "2026-10-21T00:00:00Z", "2026-10-21T00:00:01Z")
        self.assertEqual(evidence["execution"], "software")
        self.assertIs(evidence["hardware_validated"], False)
        self.assertNotIn("/private/payload", json.dumps(evidence))

    def test_workflow_has_no_secrets_privilege_escalation_or_policy_changes(self):
        workflow = (ROOT / ".github/workflows/mkosi-native-probe.yml").read_text()
        for forbidden in ("sudo", "secrets.", "self-hosted", "sysctl", "--privileged", "seccomp=unconfined"):
            self.assertNotIn(forbidden, workflow)
        for required in ("persist-credentials: false", "contents: read", "refs/heads/main", "--max-time 60"):
            self.assertIn(required, workflow)

    def test_workflow_preparation_runs_in_a_clean_checkout_without_fetching(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/mkosi-native-probe.yml").read_text())
        steps = workflow["jobs"]["probe"]["steps"]
        script = next(step["run"] for step in steps if step.get("name", "").startswith("Fetch public"))
        # Execute the actual setup shell; only curl is replaced by a marker.
        script = 'curl() { test -d build/native-mkosi; touch fetch-reached; };\n' + script
        for runner in ("github-hosted", "self-hosted"):
            with tempfile.TemporaryDirectory() as temporary:
                result = subprocess.run(["/bin/bash", "-eu", "-c", script], cwd=temporary,
                    env={"PATH": "/usr/bin:/bin", "RUNNER_ENVIRONMENT": runner},
                    stdin=subprocess.DEVNULL, capture_output=True, timeout=5)
                root = Path(temporary)
                self.assertEqual(result.returncode == 0, runner == "github-hosted")
                self.assertEqual((root / "fetch-reached").exists(), runner == "github-hosted")
                if runner == "github-hosted":
                    self.assertEqual((root / "build/native-mkosi").stat().st_mode & 0o777, 0o700)
                else:
                    self.assertFalse((root / "build").exists())


if __name__ == "__main__":
    unittest.main()
