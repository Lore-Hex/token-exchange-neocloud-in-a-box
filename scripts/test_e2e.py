# SPDX-License-Identifier: Apache-2.0
"""Harness policy tests run in make check; container execution is separate."""

import importlib.util
import io
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / "e2e" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


runner = module("run")
exercise = module("exercise")


class E2EPolicyTest(unittest.TestCase):
    def test_pins_require_immutable_inputs(self):
        original = runner.load_pins(engine_only=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pins.json"
            for field in ("go_image", "python_image", "platform", "schema"):
                bad = {**original, field: "floating"}
                path.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):
                    runner.load_pins(path, engine_only=True)
            path.write_text(json.dumps({**original, "trcs": None}))
            with self.assertRaises(ValueError):
                runner.load_pins(path)
            good = {**original, "trcs": {"commit": "1" * 40, "archive_sha256": "2" * 64, "binary_sha256": "3" * 64}}
            path.write_text(json.dumps(good))
            self.assertEqual(runner.load_pins(path), good)

    def test_runtime_confinement(self):
        command = runner.runtime_command("nib-e2e-local:test", "nib-e2e-test")
        for flag, value in (("--network", "none"), ("--cap-drop", "ALL"),
                            ("--user", "65532:65532"), ("--security-opt", "no-new-privileges")):
            self.assertEqual(command[command.index(flag) + 1], value)
        self.assertIn("--read-only", command)
        self.assertIn("--rm", command)
        self.assertIn("--memory", command)
        self.assertNotIn("--privileged", command)
        self.assertNotIn("-v", command)
        self.assertNotIn("--mount", command)
        self.assertNotIn("--env", command)
        self.assertNotIn("--device", command)
        self.assertNotIn("--publish", command)

    def test_credentials_not_inherited(self):
        with patch.dict(os.environ, {"UNEXPECTED_CREDENTIAL": "do-not-inherit", "HTTPS_PROXY": "do-not-inherit"}):
            self.assertNotIn("UNEXPECTED_CREDENTIAL", exercise.clean_env())
            self.assertNotIn("HTTPS_PROXY", exercise.clean_env())

    def test_build_context_confined_and_credential_free(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "guest/test.go"
            source.parent.mkdir()
            source.write_text("package test\n")
            with patch.object(runner.subprocess, "check_output", return_value=b"guest/test.go\0.env\0private.key\0build/secrets.json\0"):
                self.assertEqual(list(runner.source_paths(root)), [Path("guest/test.go")])
                source.unlink()
                source.symlink_to("/etc/passwd")
                with self.assertRaises(ValueError):
                    list(runner.source_paths(root))

    def report(self, statuses=None):
        checks = {k: "pass" for k in exercise.REQUIRED}
        checks.update(statuses or {})
        return {"suite_version": "0.1.8", "checks": [{"id": k, "status": v} for k, v in checks.items()],
                "target": {"authentication": {"authorization_header_sent": False}}}

    def test_no_vacuous_provider_success_or_raw_report_logs(self):
        for bad in ("skip", "warn", "fail"):
            report = self.report({"stream.incremental-delivery": bad})
            result = subprocess.CompletedProcess([], 0, json.dumps(report).encode(), b"")
            with patch.object(exercise.subprocess, "run", return_value=result):
                with self.assertRaises(exercise.CheckFailure):
                    exercise.provider_check("http://127.0.0.1:18000")
        report = self.report()
        report["checks"][0]["error_message"] = exercise.CANARY
        result = subprocess.CompletedProcess([], 0, json.dumps(report).encode(), b"private")
        with patch.object(exercise.subprocess, "run", return_value=result):
            summary = exercise.provider_check("http://127.0.0.1:18000")
        self.assertNotIn(exercise.CANARY, json.dumps(summary))

    def test_release_negative_must_reach_appraisal(self):
        result = subprocess.CompletedProcess([], 1, b"", b"failed to launch")
        with patch.object(exercise.subprocess, "run", return_value=result):
            with self.assertRaises(exercise.CheckFailure):
                exercise.verify([], release=True)

    def test_http_connection_is_closed(self):
        with patch.object(exercise.http.client, "HTTPConnection") as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 200
            response.read.return_value = b"{}"
            response.getheaders.return_value = []
            self.assertEqual(exercise.request(18000, "/health")[0], 200)
            connection.return_value.close.assert_called_once()

    def test_slow_rejection_requires_actual_socket_close(self):
        for closed in (True, False):
            conn = Mock()
            conn.makefile.return_value = io.BytesIO(
                b"HTTP/1.1 429 Too Many Requests\r\nRetry-After: 1\r\nConnection: close\r\n"
                b"X-Request-Id: 0123456789abcdef0123456789abcdef\r\nContent-Length: 0\r\n\r\n")
            conn.recv.return_value = b"" if closed else b"x"
            if closed:
                exercise.slow_rejection(conn)
            else:
                with self.assertRaises(exercise.CheckFailure):
                    exercise.slow_rejection(conn)
            conn.sendall.assert_called_once()

    def test_mutation_matrix_covers_all_measurements(self):
        self.assertEqual(len(exercise.MUTATIONS), len(set(exercise.MUTATIONS)))
        self.assertTrue({"cpu-binding", "cpu-nonce", "cpu-measurement", "cpu-rtmr0",
                         "cpu-rtmr1", "cpu-rtmr2", "cpu-debug", "cpu-migratable",
                         "cpu-truncated", "gpu-nonce"} <= set(exercise.MUTATIONS))

    def test_private_log_canary_positive_control(self):
        processes = exercise.Processes(None)
        processes.logs = [io.BytesIO(b"fixed operational diagnostic\n")]
        processes.privacy()
        processes.logs = [io.BytesIO(exercise.CANARY.encode())]
        with self.assertRaises(exercise.CheckFailure):
            processes.privacy()
        processes.logs = [io.BytesIO(b"x" * ((1 << 20) + 1))]
        with self.assertRaises(exercise.CheckFailure):
            processes.privacy()

    def test_full_node_runs_mutations_and_restores_valid_state(self):
        # Mock process/network boundaries only. Execute the real orchestration
        # so a missing matrix or skipped mutation cannot hide in engine-only CI.
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            state = root / "collector-state"
            calls = []

            def checked(args, **options):
                calls.append((state.read_text(), options))
                return {"status": "fake_pass" if options.get("success") else "fail"}

            for name, value in {
                "wait_ready": None, "bootstrap_test_cert": (root / "ca.pem", object()),
                "catalog_server": "http://127.0.0.1:18002", "provider_check": {},
                "policies": [], "privacy_requests": None, "upstream_unavailable": None,
                "request_ids": None, "admission": None,
            }.items():
                stack.enter_context(patch.object(exercise, name, return_value=value))
            stack.enter_context(patch.object(exercise, "verify", side_effect=checked))
            summary = {}
            exercise.full_node(stack, root, Mock(), summary)
            expected = [("valid", {"success": True}), ("valid", {"release": True})]
            expected.extend((mode, {}) for mode in exercise.MUTATIONS)
            expected.append(("valid", {"success": True}))
            self.assertEqual(calls, expected)
            self.assertEqual(set(summary["mutations"]), set(exercise.MUTATIONS))
            self.assertEqual(state.read_text(), "valid")


if __name__ == "__main__":
    unittest.main()
