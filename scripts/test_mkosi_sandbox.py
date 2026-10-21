# SPDX-License-Identifier: Apache-2.0
"""Synthetic namespace/engine fakes only; these tests never unshare or mount."""
import copy
import errno
import json
from pathlib import Path
import subprocess
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    module = types.ModuleType("sandbox_fixture")
    module.__file__ = str(ROOT / path)
    exec(compile(Path(module.__file__).read_bytes(), module.__file__, "exec",
                 dont_inherit=True), module.__dict__)
    return module


host = load("host/probe-mkosi-tools.py")
worker, prep = host.worker, host.prep
child = load("scripts/mkosi_namespace_check.py")
fixture = load("scripts/test_mkosi_probe.py")
INPUT = {**fixture.INPUT, "schema": "nib.mkosi-tools-probe-input/v2", "sandbox": True,
         "sandbox_sha256": "d" * 64, "checker_sha256": "e" * 64}
ORIGINAL = [name + ":[100]" for name in child.NAMES]


def run_probe(output=worker.SANDBOX_SUCCESS, *, error=None):
    results = [subprocess.CompletedProcess([], 0, b"version", b"")] * 7
    results.append(error or subprocess.CompletedProcess([], 0, output, b""))
    call = mock.Mock(side_effect=results)
    with mock.patch.object(worker.os, "readlink", side_effect=ORIGINAL):
        result = worker.probe(INPUT, call)
    return result, call


class SandboxTests(unittest.TestCase):
    def test_exact_command_and_result_claims(self):
        result, call = run_probe()
        self.assertEqual(result["result"], "pass")
        self.assertEqual(result["programs_attempted"], 8)
        self.assertIs(result["mkosi_executed"], True)
        self.assertIs(result["namespaces_validated"], True)
        self.assertIs(result["mkosi_build_executed"], False)
        argv = call.call_args.args[0]
        self.assertEqual(argv[-4:], ORIGINAL)
        self.assertEqual(argv[:5], ["/usr/bin/python3.12", "-I", "-S", "-B", "/nib/mkosi-sandbox.py"])
        self.assertEqual(argv[5:20], [part for p in ("/usr", "/lib", "/lib64", "/nib", "/proc")
                                     for part in ("--ro-bind", p, p)])
        self.assertEqual(argv[20:-4], ["--dev", "/dev", "--tmpfs", "/tmp", "--unshare-net", "--unshare-ipc",
            "--become-root", "/usr/bin/python3.12", "-I", "-S", "-B", "/nib/namespace-check.py"])
        self.assertNotIn("100", json.dumps(result))
        host.validate_result(result, {**INPUT, "sandbox": True})

    def test_nonsemantic_success_timeout_and_permission_failure_are_closed(self):
        for output, error in ((b"version", None), (worker.SANDBOX_SUCCESS + b"extra", None),
                (b"", subprocess.TimeoutExpired(["PRIVATE"], 10)),
                (b"", subprocess.CompletedProcess([], 1, b"", b"PRIVATE"))):
            with self.subTest(output=output, error=error):
                result, _ = run_probe(output, error=error)
                self.assertEqual((result["result"], result["stage"]), ("fail", "sandbox"))
                self.assertEqual(len(result["checks"]), 7)
                self.assertIs(result["mkosi_executed"], True)
                self.assertIs(result["namespaces_validated"], False)
                self.assertNotIn("PRIVATE", json.dumps(result))
                host.validate_result(result, INPUT)

    def test_early_failure_never_claims_sandbox_execution(self):
        result = worker.probe(INPUT, mock.Mock(side_effect=ValueError("private")))
        self.assertIs(result["mkosi_executed"], False)
        self.assertIs(result["namespaces_validated"], False)
        host.validate_result(result, INPUT)

    def test_permission_failure_is_classified_without_raw_output(self):
        result, _ = run_probe(error=subprocess.CompletedProcess(
            [], 1, b"", b"PRIVATE path: Operation not permitted"))
        self.assertEqual(result["failure_kind"], "permission-denied")
        self.assertNotIn("PRIVATE", json.dumps(result))
        host.validate_result(result, INPUT)

    def test_mode_receipt_and_semantic_result_cannot_be_substituted(self):
        result, _ = run_probe()
        for field, value in (("sandbox", 1), ("mkosi_executed", False), ("namespaces_validated", False),
                             ("mkosi_build_executed", True), ("schema", "nib.mkosi-tools-probe/v1"),
                             ("failure_kind", "permission-denied")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                host.validate_result({**result, field: value}, INPUT)
        with self.assertRaises(ValueError):
            host.validate_result(result, fixture.INPUT)
        changed = copy.deepcopy(result)
        changed["checks"][-1]["stdout_sha256"] = "f" * 64
        with self.assertRaises(ValueError):
            host.validate_result(changed, INPUT)
        for field, value in (("sandbox", 1), ("sandbox", False), ("checker_sha256", "x"),
                             ("schema", "nib.mkosi-tools-probe-input/v1"), ("command", "arbitrary")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                worker.validate_input({**INPUT, field: value})

    def test_changed_upstream_and_child_bytes_rejected_before_execution(self):
        for bad_index in (1, 2):
            sandbox, checker = b"authenticated upstream", b"authenticated checker"
            inputs = {**INPUT, "sandbox_sha256": prep.base.digest(sandbox),
                      "checker_sha256": prep.base.digest(checker)}
            reads = [json.dumps(inputs).encode(), sandbox, checker]
            reads[bad_index] = b"tampered"
            with mock.patch.object(worker.sys, "platform", "linux"), \
                    mock.patch.object(worker.sys, "argv", ["probe.py"]), \
                    mock.patch.object(worker.os, "geteuid", return_value=65534), \
                    mock.patch.object(worker, "read", side_effect=reads), \
                    mock.patch.object(worker, "probe") as run, self.assertRaises(ValueError):
                worker.main()
            run.assert_not_called()

    def test_authenticated_source_and_license_are_bound_into_overlay(self):
        files, report, sources = fixture.ImageTests().fixture()
        license_path = "LICENSES/LGPL-2.1-or-later.txt"
        pin = {"license_files": [license_path]}
        sources.update({prep.CHECKER: b"synthetic checker",
                        prep.prep.mkosi.EXTRA[1]: json.dumps(pin).encode()})
        upstream = {"mkosi/sandbox.py": b"synthetic upstream", license_path: b"synthetic license"}
        with mock.patch.object(prep.prep, "assemble", return_value=(files, report)), \
                mock.patch.object(prep.base, "read", return_value=b"authenticated tar") as read, \
                mock.patch.object(prep.prep.mkosi, "verify_source", return_value=({}, upstream)) as verify:
            output, receipt = prep.build(Path("/synthetic"), fixture.REVISION, sources, sandbox=True)
        read.assert_called_once_with(Path("/synthetic/mkosi-source.tar.gz"), prep.prep.mkosi.MAX_SOURCE)
        verify.assert_called_once_with(b"authenticated tar", pin)
        image = fixture.members(output["image.tar"])
        payload = fixture.members(image["layer1.tar"])
        inputs = json.loads(payload["nib/input.json"])
        worker.validate_input(inputs)
        self.assertEqual(payload["nib/" + license_path], upstream[license_path])
        for path, field in worker.SANDBOX_FILES.items():
            self.assertEqual(inputs[field], prep.base.digest(payload[path.lstrip("/")]))
            self.assertEqual(receipt[field], inputs[field])
        self.assertTrue(receipt["sandbox"])
        self.assertFalse(receipt["mkosi_executed"])
        with mock.patch.object(prep.prep, "assemble", return_value=(files, report)), \
                mock.patch.object(prep.base, "read", return_value=b"bad tar"), \
                mock.patch.object(prep.prep.mkosi, "verify_source", side_effect=ValueError), \
                self.assertRaises(ValueError):
            prep.build(Path("/synthetic"), fixture.REVISION, sources, sandbox=True)

    def test_sandbox_mode_reuses_unweakened_lifecycle_and_preserves_cleanup(self):
        runner_fixture = load("scripts/test_mkosi_probe_runner.py").ProbeRunnerTests()
        runner_fixture.setUp()
        runner_fixture.receipt["sandbox"] = True
        runner_fixture.result, _ = run_probe()
        result = runner_fixture.execute()
        self.assertEqual((result["result"], result["cleanup"]), ("pass", "confirmed"))
        self.assertIs(result["mkosi_executed"], True)
        self.assertIs(result["namespaces_validated"], True)
        self.assertIs(result["mkosi_build_executed"], False)
        create = next(a for a, _ in runner_fixture.calls if a[:2] == ["container", "create"])
        for flag in ("--network=none", "--read-only", "--cap-drop=ALL",
                     "--security-opt=no-new-privileges", "--user=65534:65534"):
            self.assertIn(flag, create)
        self.assertNotIn("--privileged", create)
        runner_fixture.row["State"]["ExitCode"] = 1
        result = runner_fixture.execute()
        self.assertEqual((result["result"], result["cleanup"]), ("fail", "confirmed"))
        self.assertIsNone(result["probe"])
        self.assertIsNone(result["mkosi_executed"])
        self.assertIs(result["namespaces_validated"], False)

    def test_namespace_lookup_failure_is_not_reported_as_execution(self):
        call = mock.Mock(return_value=subprocess.CompletedProcess([], 0, b"version", b""))
        with mock.patch.object(worker.os, "readlink", side_effect=OSError("PRIVATE")):
            result = worker.probe(INPUT, call)
        self.assertEqual(result["stage"], "sandbox")
        self.assertEqual(result["programs_attempted"], 7)
        self.assertIs(result["mkosi_executed"], False)
        self.assertEqual(result["failure_kind"], "startup-error")
        host.validate_result(result, INPUT)


class ChildTests(unittest.TestCase):
    def check(self, *, same=None, interfaces=None, write_error=errno.EROFS, uid=0):
        links = [name + (":[100]" if name == same else ":[101]") for name in child.NAMES]
        with mock.patch.object(child.os, "geteuid", return_value=uid), \
                mock.patch.object(child.os, "getegid", return_value=0), \
                mock.patch.object(child.os, "readlink", side_effect=links), \
                mock.patch.object(child.socket, "if_nameindex", return_value=interfaces or [(1, "lo")]), \
                mock.patch.object(child.os, "open", side_effect=OSError(write_error, "synthetic")), \
                mock.patch.object(child.Path, "open", mock.mock_open()) as opened, \
                mock.patch.object(child.Path, "read_bytes", return_value=b"synthetic"), \
                mock.patch.object(child.Path, "unlink") as unlink:
            child.check(ORIGINAL)
        opened.assert_called_once_with("xb")
        unlink.assert_called_once_with()

    def test_namespace_and_mount_success_is_explicit(self):
        self.check()
        self.assertEqual(child.SUCCESS, worker.SANDBOX_SUCCESS)

    def test_any_shared_namespace_or_nonisolated_network_or_writable_worker_rejects(self):
        for kwargs in ([{"same": name} for name in child.NAMES]
                + [{"interfaces": [(1, "lo"), (2, "eth0")]}, {"write_error": errno.EACCES}, {"uid": 65534}]):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.check(**kwargs)

    def test_invalid_namespace_identifiers_rejected(self):
        with mock.patch.object(child.os, "geteuid", return_value=0), \
                mock.patch.object(child.os, "getegid", return_value=0), \
                mock.patch.object(child.os, "readlink", return_value="user:[101]"), \
                self.assertRaises(ValueError):
            child.check(["PRIVATE", *ORIGINAL[1:]])


if __name__ == "__main__":
    unittest.main()
