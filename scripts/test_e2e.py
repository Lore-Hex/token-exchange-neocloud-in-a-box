# SPDX-License-Identifier: Apache-2.0
"""Harness policy tests run in make check; container execution is separate."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


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


if __name__ == "__main__":
    unittest.main()
