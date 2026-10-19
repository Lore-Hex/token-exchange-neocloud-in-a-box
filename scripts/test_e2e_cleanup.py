# SPDX-License-Identifier: Apache-2.0
"""Fault injection for E2E cleanup; no Docker daemon or resources are used."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from e2e import run as runner


IMAGE = "nib-e2e-local:test"
CONTAINER = "nib-e2e-test"


def completed(code=0, output=b""):
    return subprocess.CompletedProcess([], code, stdout=output)


class CleanupTest(unittest.TestCase):
    def test_removal_and_absence_faults(self):
        cases = [
            ("removed", [completed(), completed(), completed(), completed()], "pass", "confirmed", "confirmed"),
            ("already-absent", [completed(1), completed(1), completed(), completed()], "pass", "confirmed", "confirmed"),
            ("both-removals-fail", [completed(1), completed(1), completed(0, b"id\n"), completed(0, b"id\n")], "fail", "present", "present"),
            ("container-removal-timeout", [subprocess.TimeoutExpired("rm", 30), completed(1), completed(0, b"id\n"), completed(0, b"id\n")], "fail", "present", "present"),
            ("image-removal-timeout", [completed(), subprocess.TimeoutExpired("rm", 30), completed(), completed(0, b"id\n")], "fail", "confirmed", "present"),
            ("timeout-but-absent", [subprocess.TimeoutExpired("rm", 30), subprocess.TimeoutExpired("rm", 30), completed(), completed()], "pass", "confirmed", "confirmed"),
            ("removal-success-but-present", [completed(), completed(), completed(0, b"id\n"), completed()], "fail", "present", "confirmed"),
            ("listing-fails-empty", [completed(), completed(), completed(1), completed(1)], "fail", "unknown", "unknown"),
            ("listing-timeout", [completed(), completed(), subprocess.TimeoutExpired("ls", 10), completed()], "fail", "unknown", "confirmed"),
            ("docker-unavailable", [OSError("private diagnostic"), completed(), OSError("private diagnostic"), completed()], "fail", "unknown", "confirmed"),
        ]
        for label, outcomes, result, container, image in cases:
            with self.subTest(label=label), patch.object(runner.subprocess, "run", side_effect=outcomes) as command:
                report = runner.cleanup_resources(IMAGE, CONTAINER)
                self.assertEqual(report["result"], result)
                self.assertEqual(report["container"]["absence"], container)
                self.assertEqual(report["image"]["absence"], image)
                self.assertNotIn("private diagnostic", json.dumps(report))
                calls = [call.args[0] for call in command.call_args_list]
                self.assertEqual(calls[:2], [["docker", "rm", "-f", CONTAINER], ["docker", "image", "rm", IMAGE]])
                self.assertEqual(len(calls), 4)
                self.assertEqual(calls[2][:4], ["docker", "container", "ls", "--all"])
                self.assertEqual(calls[3][:4], ["docker", "image", "ls", "--all"])
                if isinstance(outcomes[0], subprocess.TimeoutExpired):
                    self.assertEqual(report["container"]["removal"]["status"], "timeout")
                elif isinstance(outcomes[0], subprocess.CompletedProcess):
                    self.assertEqual(report["container"]["removal"]["exit_code"], outcomes[0].returncode)

    def test_main_does_not_report_success_before_cleanup(self):
        for removal_timeout, present in ((False, True), (True, True), (False, False), (True, False)):
            with self.subTest(timeout=removal_timeout, present=present), tempfile.TemporaryDirectory() as directory:
                calls = []

                def command(args, **kwargs):
                    calls.append(args)
                    if args[1] == "build":
                        return completed()
                    if args[1] == "run":
                        return completed(output=b'{"result":"pass","hardware_validated":false}')
                    if args[1] == "rm":
                        if removal_timeout:
                            raise subprocess.TimeoutExpired("rm", 30)
                        return completed(1)
                    if args[1:3] == ["image", "rm"]:
                        return completed(1)
                    if "ls" in args:
                        return completed(output=b"resource-id\n" if present else b"")
                    self.fail("unexpected host command")

                output = io.StringIO()
                with patch("sys.argv", ["run.py", "--engine-only"]), \
                        patch.object(runner, "ROOT", Path(directory)), \
                        patch.object(runner.uuid, "uuid4", return_value=Mock(hex="f" * 32)), \
                        patch.object(runner, "build_context", return_value={"scope": "engine-only"}), \
                        patch.object(runner.subprocess, "run", side_effect=command), redirect_stdout(output):
                    code = runner.main()
                summary = json.loads((Path(directory) / "build/e2e/ffffffffffff/result.json").read_text())
                printed = json.loads(output.getvalue().splitlines()[0])
                self.assertEqual(printed, summary)
                self.assertEqual(code, 1 if present else 0)
                self.assertEqual(summary["result"], "fail" if present else "pass")
                self.assertEqual(summary["cleanup"]["result"], summary["result"])
                if present:
                    self.assertEqual(summary["failed_check"], "cleanup-unresolved")
                self.assertEqual(sum(args[1:3] == ["image", "rm"] for args in calls), 1)

    def test_build_failure_still_attempts_and_records_both_cleanups(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, \
                patch("sys.argv", ["run.py", "--engine-only"]), \
                patch.object(runner, "ROOT", Path(directory)), \
                patch.object(runner.uuid, "uuid4", return_value=Mock(hex="f" * 32)), \
                patch.object(runner, "build_context", return_value={}), \
                patch.object(runner.subprocess, "run", side_effect=[
                    subprocess.CalledProcessError(1, "build"), completed(1), completed(1), completed(), completed(),
                ]) as command, redirect_stdout(output):
            with self.assertRaises(subprocess.CalledProcessError):
                runner.main()
            summary = json.loads((Path(directory) / "build/e2e/ffffffffffff/result.json").read_text())
            self.assertEqual(summary["result"], "fail")
            self.assertEqual(summary["cleanup"]["result"], "pass")
            self.assertEqual(command.call_count, 5)


if __name__ == "__main__":
    unittest.main()
