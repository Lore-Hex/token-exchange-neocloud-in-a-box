# SPDX-License-Identifier: Apache-2.0
"""Source-binding faults for the recorder; no Docker or external calls."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from e2e import prove, snapshot


class SnapshotTest(unittest.TestCase):
    def commit(self, root, env):
        snapshot.git(root, env, "add", ".", stdout=subprocess.PIPE)
        snapshot.git(root, env, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                     "-c", "commit.gpgsign=false", "commit", "-qm", "fixture", stdout=subprocess.PIPE)
        return snapshot.git(root, env, "rev-parse", "HEAD", stdout=subprocess.PIPE).stdout.decode().strip()

    def test_snapshot_ignores_hooks_edits_replacements_and_inherited_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            root, control, source = base / "original", base / "control", base / "snapshot"
            root.mkdir()
            env = snapshot.controlled_env(control)
            snapshot.git(root, env, "init", "-q", stdout=subprocess.PIPE)
            (root / ".gitignore").write_text("ignored.go\n")
            (root / "go.mod").write_text("module example.invalid/snapshot\n\ngo 1.23\n")
            (root / "Makefile").write_text("test-e2e:\n\t@printf 'committed harness ran\\n'\n")
            clean = 'package main\nfunc main() { println("committed source") }\n'
            (root / "main.go").write_text(clean)
            original = self.commit(root, env)
            (root / "main.go").write_text('package main\nfunc main() { panic("replacement commit") }\n')
            replacement = self.commit(root, env)
            snapshot.git(root, env, "update-ref", "HEAD", original, stdout=subprocess.PIPE)
            snapshot.git(root, env, "replace", original, replacement, stdout=subprocess.PIPE)
            (root / "ignored.go").write_text('package main\nfunc init() { panic("ignored init hook") }\n')
            shell = control / "forged-shell"
            shell.write_text("#!/bin/sh\nprintf 'FORGED_PASS\\n'\n")
            shell.chmod(0o700)
            poison = {"MAKEFLAGS": "-e SHELL=" + str(shell), "MAKEFILES": str(root / "poison.mk"),
                      "GOFLAGS": "-overlay=poison.json", "GOWORK": "poison.work", "GOENV": "poison.env",
                      "GIT_INDEX_FILE": "poison-index", "GIT_CONFIG_COUNT": "1",
                      "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": "poison-hooks",
                      "GIT_ALTERNATE_OBJECT_DIRECTORIES": "poison-objects", "PYTHONPATH": "poison",
                      "DOCKER_HOST": "tcp://unapproved.invalid:2375"}
            with patch.dict(os.environ, poison):
                controlled = snapshot.controlled_env(control)
                sha, expected, archive_hash = snapshot.snapshot_commit(root, source, control, controlled)
            self.assertEqual(sha, original)
            self.assertEqual(len(archive_hash), 64)
            self.assertEqual((source / "main.go").read_text(), clean)
            self.assertFalse((source / "ignored.go").exists())
            self.assertEqual(controlled["GOFLAGS"], "-mod=readonly")
            self.assertEqual(controlled["GOENV"], "off")
            self.assertEqual(controlled["GOWORK"], "off")
            self.assertEqual(controlled["GOTOOLCHAIN"], "go1.27.2")
            self.assertEqual(controlled["GIT_NO_REPLACE_OBJECTS"], "1")
            for key in ("MAKEFLAGS", "MAKEFILES", "GIT_INDEX_FILE", "GIT_CONFIG_COUNT",
                        "GIT_ALTERNATE_OBJECT_DIRECTORIES", "PYTHONPATH", "DOCKER_HOST"):
                self.assertNotIn(key, controlled)
            bypassed = subprocess.run(["make", "test-e2e"], cwd=source,
                                      env={**controlled, "MAKEFLAGS": poison["MAKEFLAGS"]},
                                      check=True, capture_output=True, timeout=5)
            self.assertIn(b"FORGED_PASS", bypassed.stdout)
            with patch.dict(os.environ, poison):
                checked = subprocess.run(["make", "test-e2e"], cwd=source, env=controlled,
                                         check=True, capture_output=True, timeout=5)
            self.assertEqual(checked.stdout, b"committed harness ran\n")
            # Editing the original after snapshotting cannot change executed code.
            (root / "main.go").write_text('package main\nfunc main() { panic("concurrent edit") }\n')
            # This stdlib-only fixture can use the installed compiler offline;
            # the actual recorder uses the exact pinned toolchain above.
            no_network = {**controlled, "GOPROXY": "off", "GOTOOLCHAIN": "local"}
            subprocess.run(["go", "mod", "verify"], cwd=source, env=no_network, check=True, capture_output=True, timeout=60)
            binary = control / "fixture"
            subprocess.run(["go", "build", "-o", str(binary), "."], cwd=source, env=no_network,
                           check=True, capture_output=True, timeout=120)
            executed = subprocess.run([str(binary)], env=controlled, check=True, capture_output=True, timeout=5)
            self.assertEqual(executed.stderr, b"committed source\n")
            snapshot.validate_snapshot(source, expected)
            (source / "ignored.go").write_text('package main\nfunc init() {}\n')
            with self.assertRaises(ValueError):
                snapshot.validate_snapshot(source, expected)
            (source / "ignored.go").unlink()
            (source / "main.go").write_text("changed while running")
            with self.assertRaises(ValueError):
                snapshot.validate_snapshot(source, expected)

    def test_reported_context_must_match_committed_snapshot(self):
        original = {"git_sha": "1" * 40, "source_sha256": "2" * 64,
                    "scope": "full-fake-node", "pins": {"trcs": "committed"}}
        prove.validate_inputs(original, "1" * 40, "2" * 64, original["pins"])
        for field in original:
            with self.subTest(field=field), self.assertRaises(ValueError):
                prove.validate_inputs({**original, field: "tainted"}, "1" * 40, "2" * 64, original["pins"])

    def test_fifo_artifacts_are_rejected_without_blocking(self):
        for name in ("inputs", "result"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                artifacts = root / "build/e2e/0123456789ab"
                artifacts.mkdir(parents=True)
                for record in ("inputs", "result"):
                    (artifacts / (record + ".json")).write_text("{}")
                fifo = artifacts / (name + ".json")
                fifo.unlink()
                os.mkfifo(fifo)
                code = ("import sys; from pathlib import Path; "
                        "sys.path.insert(0, sys.argv[1]); import prove; "
                        "prove.collect_results(('Software-only test artifacts: ' + sys.argv[2]).encode(), Path(sys.argv[3]))")
                checked = subprocess.run([sys.executable, "-I", "-c", code,
                                          str(Path(prove.__file__).parent), str(artifacts), str(root)],
                                         capture_output=True, timeout=3)
                self.assertNotEqual(checked.returncode, 0)
                self.assertIn(b"artifact must be a bounded regular file", checked.stderr)

    def test_artifact_limits_and_types(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "result.json"
            for data in (b"[1]", b" " * ((1 << 20) + 1)):
                path.write_bytes(data)
                with self.assertRaises(ValueError):
                    prove.read_record(path)
            with self.assertRaises(ValueError):
                prove.read_record(root)

    def test_docker_endpoint_must_be_local_and_ignore_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = snapshot.controlled_env(root / "control")
            for endpoint in (b"unix:///local/docker.sock\n", b"tcp://remote.invalid:2375\n"):
                result = subprocess.CompletedProcess([], 0, endpoint, b"")
                with patch.dict(os.environ, {"DOCKER_HOST": "tcp://poison.invalid:2375"}), \
                        patch.object(snapshot.subprocess, "run", return_value=result) as command:
                    if endpoint.startswith(b"unix:"):
                        self.assertEqual(snapshot.local_docker_endpoint(env, root), "unix:///local/docker.sock")
                    else:
                        with self.assertRaises(ValueError):
                            snapshot.local_docker_endpoint(env, root)
                    self.assertNotIn("DOCKER_HOST", command.call_args.kwargs["env"])


if __name__ == "__main__":
    unittest.main()
