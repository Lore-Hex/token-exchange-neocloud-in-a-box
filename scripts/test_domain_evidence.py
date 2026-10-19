# SPDX-License-Identifier: Apache-2.0
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("dns_record", Path(__file__).with_name("record-domain-check.py"))
recorder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recorder)


class EvidenceTests(unittest.TestCase):
    def execute(self, root, args):
        if args[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(args, 0, "a" * 40 + "\n", "")
        if args[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(args, 0, getattr(self, "dirty", ""), "")
        if args[2] == "./cmd/nib-domain-check":
            return subprocess.CompletedProcess(args, self.exit_code, json.dumps(self.report) + "\n", "private stderr")
        relative = "evidence/p1/2026-10-19-dns-test"
        destination = root / relative
        if destination.exists():
            return subprocess.CompletedProcess(args, 1, "", "private path")
        destination.mkdir(parents=True)
        (destination / "run.json").write_text('{"result":"not_run"}')
        (destination / "output.log").write_text("")
        return subprocess.CompletedProcess(args, 0, relative + "\n", "")

    def prepare(self, passed):
        self.exit_code = 0 if passed else 1
        self.report = {"scope": "dns-only", "assurance": "DNS only",
                       "checks": [{"name": name, "status": "resolved" if passed else "missing-address",
                                   "address_count": 1 if passed else 0}
                                  for name in ("example.invalid", "api.example.invalid")]}

    def record(self, root):
        return recorder.record(root, "example.invalid", "dns-test", self.execute,
                               lambda: "2026-10-19T03:00:00Z")

    def test_records_success_and_failure_without_overwriting_or_leaking(self):
        for passed in (True, False):
            self.prepare(passed)
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                relative, code = self.record(root)
                self.assertEqual(code, 0 if passed else 1)
                result = json.loads((root / relative / "run.json").read_text())
                self.assertEqual(result["git_sha"], "a" * 40)
                self.assertEqual(result["result"], "pass" if passed else "fail")
                self.assertFalse(result["hardware_validated"])
                self.assertEqual((root / relative / "output.log").read_text(), json.dumps(self.report) + "\n")
                before = (root / relative / "run.json").read_bytes()
                with self.assertRaises(ValueError):
                    self.record(root)
                self.assertEqual((root / relative / "run.json").read_bytes(), before)

    def test_dirty_source_and_bad_reports_never_record(self):
        for mode in ("dirty", "wrong-name", "wrong-code", "bad-count", "extra-field", "bad-scope"):
            self.prepare(False)
            self.dirty = " M source.go\n" if mode == "dirty" else ""
            if mode == "wrong-name":
                self.report["checks"][0]["name"] = "different.invalid"
            if mode == "wrong-code":
                self.exit_code = 0
            if mode == "bad-count":
                self.report["checks"][0]["address_count"] = True
            if mode == "extra-field":
                self.report["checks"][0]["addresses"] = ["198.51.100.1"]
            if mode == "bad-scope":
                self.report["scope"] = "hardware"
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with self.assertRaises(ValueError):
                    self.record(root)
                self.assertFalse((root / "evidence").exists())

    def test_invalid_slug_never_executes(self):
        def forbidden(*_):
            self.fail("invalid slug reached subprocess")
        for slug in ("", "../outside", "two--hyphens", "a" * 65):
            with self.assertRaises(ValueError):
                recorder.record(Path("."), "example.invalid", slug, forbidden)
