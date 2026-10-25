# SPDX-License-Identifier: Apache-2.0
"""Offline producer reachability, independent of retained feature branches."""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
GIT_ENV = {
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": "/nonexistent",
    "LANG": "C", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": "", "GIT_TERMINAL_PROMPT": "0",
}
# Newer Git detaches automatic maintenance before the calling command returns.
# Fixtures own their entire repository lifetime, including maintenance children.
GIT_OPTIONS = ["-c", "core.hooksPath=" + os.devnull,
               "-c", "maintenance.autoDetach=false", "-c", "gc.autoDetach=false"]


def git(root, *args):
    result = subprocess.run(
        ["git", *GIT_OPTIONS, "-C", str(root), *args],
        env=GIT_ENV, stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
    if result.returncode or len(result.stdout) > 1 << 20 or len(result.stderr) > 1 << 20:
        raise ValueError("offline Git operation failed")
    return result.stdout


def producer(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate evidence field")
            value[key] = item
        return value

    def nonfinite(_):
        raise ValueError("nonfinite evidence value")

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
    if (type(value) is not dict or value.get("schema") != "nib.evidence/v1"
            or type(value.get("git_sha")) is not str
            or not re.fullmatch(r"[0-9a-f]{40}", value["git_sha"])
            or value["git_sha"] == "0" * 40):
        raise ValueError("exact evidence producer required")
    return value["git_sha"]


def audit(root):
    # Read immutable tracked blobs, not ignored receipts, symlinks or vendor pins.
    producers, records = set(), 0
    for entry in git(root, "ls-tree", "-r", "-z", "HEAD", "--", "evidence").split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        if not name.endswith(b"/run.json"):
            continue
        mode, kind, oid = metadata.split()
        if mode not in (b"100644", b"100755") or kind != b"blob":
            raise ValueError("regular evidence record required")
        oid = oid.decode("ascii")
        size = int(git(root, "cat-file", "-s", oid))
        if not 0 < size <= 65536:
            raise ValueError("evidence record size bound")
        producers.add(producer(git(root, "cat-file", "blob", oid)))
        records += 1
    if not records:
        raise ValueError("no committed evidence records")
    ancestors = set(git(root, "rev-list", "HEAD").decode("ascii").splitlines())
    missing = sorted(producers - ancestors)
    if missing:
        raise ValueError("evidence producers unreachable from HEAD: " + ", ".join(missing))
    return records, len(producers)


class ProducerHistoryTests(unittest.TestCase):
    def test_all_committed_run_producers_are_reachable_from_head(self):
        records, producers = audit(ROOT)
        self.assertGreater(records, 0)
        self.assertGreater(producers, 0)

    def test_records_require_exact_producers_without_duplicate_keys(self):
        valid = {"schema": "nib.evidence/v1", "git_sha": "a" * 40}
        self.assertEqual(producer(json.dumps(valid).encode()), "a" * 40)
        for sha in (None, True, "a" * 39, "0" * 40, "HEAD", "a" * 40 + "\n"):
            with self.subTest(sha=sha), self.assertRaises(ValueError):
                producer(json.dumps({**valid, "git_sha": sha}).encode())
        for raw in (b"[]", b"{}", b'{"git_sha":"a","git_sha":"b"}',
                    b'{"schema":"nib.evidence/v1","git_sha":NaN}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                producer(raw)

    def test_git_cannot_lazy_fetch_or_use_replacements_or_ambient_credentials(self):
        with mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as run:
            git(ROOT, "rev-list", "HEAD")
        options = run.call_args.kwargs
        self.assertEqual(options["env"]["GIT_ALLOW_PROTOCOL"], "")
        self.assertEqual(options["env"]["GIT_NO_LAZY_FETCH"], "1")
        self.assertEqual(options["env"]["GIT_NO_REPLACE_OBJECTS"], "1")
        self.assertEqual(options["env"]["HOME"], "/nonexistent")
        self.assertNotIn("SSH_AUTH_SOCK", options["env"])
        self.assertEqual(options["timeout"], 10)
        command = run.call_args.args[0]
        self.assertIn("maintenance.autoDetach=false", command)
        self.assertIn("gc.autoDetach=false", command)


class CloneHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name).resolve()
        self.root = self.parent / "source"
        self.root.mkdir()
        git(self.root, "init", "--initial-branch=main")
        self.source = self.root / "producer.py"
        self.source.write_bytes(b"original producer bytes\n")
        self.commit()
        git(self.root, "checkout", "-b", "historical-producer")
        self.source.write_bytes(b"historical producer bytes\n")
        self.historical = self.commit()
        self.original = git(self.root, "cat-file", "commit", self.historical)
        git(self.root, "checkout", "main")
        self.record = self.root / "evidence/p0/2000-01-01-synthetic/run.json"
        self.record.parent.mkdir(parents=True)
        self.record.write_text(json.dumps({"schema": "nib.evidence/v1", "git_sha": self.historical}))
        self.commit()

    def commit(self):
        git(self.root, "add", ".")
        git(self.root, "-c", "user.name=Synthetic Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-m", "synthetic fixture")
        return git(self.root, "rev-parse", "HEAD").decode().strip()

    def test_retained_branch_or_existing_object_is_not_sufficient(self):
        self.assertEqual(git(self.root, "cat-file", "-t", self.historical), b"commit\n")
        with self.assertRaisesRegex(ValueError, "unreachable from HEAD"):
            audit(self.root)

    def test_append_only_preservation_survives_isolated_single_branch_clone(self):
        tree = git(self.root, "rev-parse", "HEAD^{tree}")
        git(self.root, "-c", "user.name=Synthetic Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false", "merge", "-s", "ours", "--no-ff",
            "-m", "preserve original producer, no tree change", self.historical)
        self.assertEqual(git(self.root, "rev-parse", "HEAD^{tree}"), tree)
        self.assertEqual(git(self.root, "cat-file", "commit", self.historical), self.original)
        clone = self.parent / "clone"
        # The fixture alone permits local file transport; audit() itself never fetches.
        result = subprocess.run(
            ["git", *GIT_OPTIONS, "clone", "--no-local",
             "--single-branch", "--no-tags", "--branch", "main", str(self.root), str(clone)],
            env={**GIT_ENV, "GIT_ALLOW_PROTOCOL": "file"}, stdin=subprocess.DEVNULL,
            capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(git(clone, "rev-parse", "--is-shallow-repository"), b"false\n")
        self.assertFalse((clone / ".git/objects/info/alternates").exists())
        self.assertNotIn(b"historical-producer", git(clone, "show-ref"))
        self.assertEqual(audit(clone), (1, 1))
        self.assertEqual(git(clone, "cat-file", "commit", self.historical), self.original)
        self.assertEqual(git(clone, "show", self.historical + ":producer.py"), b"historical producer bytes\n")

    def test_missing_producer_is_not_fetched_or_silently_skipped(self):
        self.record.write_text(json.dumps({"schema": "nib.evidence/v1", "git_sha": "f" * 40}))
        self.commit()
        with self.assertRaisesRegex(ValueError, "unreachable from HEAD"):
            audit(self.root)

    def test_symlink_record_is_not_followed(self):
        self.record.unlink()
        self.record.symlink_to("../../../producer.py")
        self.commit()
        with self.assertRaisesRegex(ValueError, "regular evidence record"):
            audit(self.root)

    def test_oversized_record_is_not_loaded(self):
        self.record.write_bytes(b" " * 65537)
        self.commit()
        with self.assertRaisesRegex(ValueError, "size bound"):
            audit(self.root)

    def test_repository_settings_cannot_detach_fixture_maintenance(self):
        for key in ("maintenance.autoDetach", "gc.autoDetach"):
            git(self.root, "config", "--local", key, "true")
            self.assertEqual(git(self.root, "config", "--bool", key), b"false\n")
        trace = self.parent / "maintenance-trace.jsonl"
        git(self.root, "config", "maintenance.loose-objects.enabled", "true")
        git(self.root, "config", "maintenance.loose-objects.auto", "1")
        self.source.write_bytes(b"force a real maintenance-capable commit\n")
        with mock.patch.dict(GIT_ENV, {"GIT_TRACE2_EVENT": str(trace)}):
            self.commit()
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        maintenance = [event["argv"] for event in events
                       if event.get("event") == "child_start"
                       and event.get("argv", [])[:3] == ["git", "maintenance", "run"]]
        self.assertTrue(maintenance)
        for command in maintenance:
            self.assertIn("--no-detach", command)
            self.assertNotIn("--detach", command)
