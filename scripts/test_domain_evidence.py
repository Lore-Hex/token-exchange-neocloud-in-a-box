# SPDX-License-Identifier: Apache-2.0
import importlib.util
import contextlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("dns_record", Path(__file__).with_name("record-domain-check.py"))
recorder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recorder)


class EvidenceTests(unittest.TestCase):
    def execute(self, root, args):
        if args[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(args, 0, "a" * 40 + "\n", "")
        if args[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(args, 0, getattr(self, "dirty", ""), "")
        if args[0] == "/snapshot/nib-domain-check":
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
        @contextlib.contextmanager
        def builder(repository, sha):
            self.assertEqual(repository, root)
            self.assertEqual(sha, "a" * 40)
            yield {"nib-domain-check": "/snapshot/nib-domain-check", "nib": "/snapshot/nib"}
        return recorder.record(root, "example.invalid", "dns-test", self.execute,
                               lambda: "2026-10-19T03:00:00Z", builder)

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

    def test_ambient_go_overlays_and_workspace_are_disabled(self):
        with mock.patch.dict(os.environ, {
            "GOFLAGS": "-overlay=/private/overlay.json -tags=synthetic",
            "GOWORK": "/private/go.work", "GOENV": "/private/go.env", "GOTOOLCHAIN": "private-toolchain",
            "GOOS": "windows", "GOARCH": "arm64", "GOEXPERIMENT": "private-experiment",
            "GODEBUG": "private-debug", "GIT_DIR": "/private/repo", "CGO_CFLAGS": "private-flags",
        }):
            env = recorder.command_environment()
        self.assertEqual(env["GOFLAGS"], "-mod=readonly")
        self.assertEqual(env["GOENV"], "off")
        self.assertEqual(env["GOWORK"], "off")
        self.assertEqual(env["GOTOOLCHAIN"], "auto")
        for key in ("GOOS", "GOARCH", "GOEXPERIMENT", "GODEBUG", "GIT_DIR", "CGO_CFLAGS"):
            self.assertNotIn(key, env)

    def test_snapshot_excludes_ignored_and_untracked_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            def git(*args):
                result = recorder.command(root, ["git", *args])
                self.assertEqual(result.returncode, 0, "fixture Git command failed")
                return result.stdout.strip()
            git("init", "-q")
            (root / "committed.go").write_text("package main\n")
            (root / ".gitignore").write_text("ignored.go\n")
            (root / "go.mod").write_text("module example.invalid/snapshot\n\ngo 1.27.2\n")
            for name in ("nib-domain-check", "nib"):
                package = root / "cmd" / name
                package.mkdir(parents=True)
                (package / "main.go").write_text('package main\nimport "fmt"\nfunc main() { fmt.Print("committed") }\n')
            git("add", "committed.go", ".gitignore", "go.mod", "cmd")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
            sha = git("rev-parse", "HEAD")
            (root / "ignored.go").write_text("package main // must not execute\n")
            for name in ("nib-domain-check", "nib"):
                (root / "cmd" / name / "ignored.go").write_text(
                    'package main\nimport ("fmt"; "os")\nfunc init() { fmt.Print("forged"); os.Exit(0) }\n')
            self.assertEqual(git("status", "--porcelain"), "")
            (root / "untracked.go").write_text("package main // must not execute\n")
            target = Path(directory) / "extracted"
            target.mkdir()
            snapshot = recorder.extract_snapshot(root, sha, target)
            self.assertEqual((snapshot / "committed.go").read_text(), "package main\n")
            self.assertFalse((snapshot / "ignored.go").exists())
            self.assertFalse((snapshot / "untracked.go").exists())
            # Exercise the actual builder, not only archive membership. Both
            # ignored init() hooks and ambient overlays/workspaces must be inert.
            with mock.patch.dict(os.environ, {"GOFLAGS": "-overlay=/missing/private-overlay.json",
                                               "GOWORK": "/missing/private-go.work"}):
                with recorder.build_snapshot(root, sha) as binaries:
                    for binary in binaries.values():
                        checked = recorder.command(root, [binary])
                        self.assertEqual(checked.returncode, 0)
                        self.assertEqual(checked.stdout, "committed")
