# SPDX-License-Identifier: Apache-2.0
"""Native routing and fixed-input controls; no model execution or downloads."""
import ast
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import time
import types
import unittest
from unittest import mock

import tau_cpu_parity as cpu
from test_evidence_history import git

ROOT = Path(__file__).resolve().parent.parent
native = types.SimpleNamespace(**runpy.run_path(str(ROOT / "scripts/prepare-tau-cpu-parity-native.py")))
rec = native.rec


class NativeCPUParityTests(unittest.TestCase):
    def test_original_argv_failure_sources_and_current_kernel_guards(self):
        root = ROOT / "evidence/p2/2026-10-22-cpu-parity-argv-failure"
        producer = "8ae250569bc73b6b79acef448584c6f129967911"
        for name, sha in (("run.json", "919e7f6d12a6a6624f36c2fec2a82a5f8be8cfa3b7c4ad705a092f2b1f9af929"),
                          ("output.log", "b42bb2d3e7d0e15b4084da69868ff1c9a168663dc60b12737301ba2b45a760de")):
            raw = (root / name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), sha)
            row = json.loads(raw)
            self.assertEqual((row["git_sha"], row["result"]), (producer, "fail"))
        for path, sha in row["sources"].items():
            self.assertEqual(hashlib.sha256(git(ROOT, "show", producer + ":" + path)).hexdigest(), sha)
        self.assertEqual(row["cleanup"], dict.fromkeys(("container", "image", "build_completion"), "confirmed"))
        failure = row["runtime"]["failure"]
        self.assertEqual((failure["kind"], failure["errno"], failure["server_exit"]), ("rejected", None, None))
        self.assertEqual([frame["line"] for frame in failure["frames"]], [329, 203, 34])
        self.assertEqual(row["runtime"]["models"][0]["cases"], [])
        self.assertTrue(all(row[key] is False for key in ("quality_eligible", "confidential", "live_admission")))
        for name in ("scripts/tau_cpu_parity.py", "scripts/pilot/run.py"):
            self.assertEqual((ROOT / name).read_bytes(), git(ROOT, "show", producer + ":" + name))
        # Mixed-runtime admission changes this module, not the original receipt
        # or the kernel/process/socket guards used by the CPU parity producer.
        name = "scripts/tau_live_identity.py"
        def guards(raw):
            names = {"private_read", "source_receipt", "proc_read", "process_facts", "sockets", "tcp_rows", "Endpoint"}
            return {node.name: ast.dump(node) for node in ast.parse(raw).body
                    if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names}
        self.assertEqual(guards((ROOT / name).read_bytes()), guards(git(ROOT, "show", producer + ":" + name)))

    def test_exact_kernel_argv_stays_required_including_trailing_nul(self):
        argv, port = ["/fixed/server", "--offline"], 12345
        expected = b"/fixed/server\0--offline\0"
        facts = {"argv_sha256": hashlib.sha256(expected).hexdigest()}
        process = mock.Mock(pid=7)
        process.poll.return_value = None
        rows = [("0100007F:3039", "00000000:0000", "0A", 12)]
        for raw in (expected, expected[:-1], b"emulator\0" + expected, expected + b"\0"):
            observed = {**facts, "argv_sha256": hashlib.sha256(raw).hexdigest()}
            with mock.patch.object(cpu.identity, "tcp_rows", return_value=rows), \
                    mock.patch.object(cpu.identity, "process_facts", return_value=observed), \
                    mock.patch.object(cpu.identity, "Endpoint") as endpoint:
                if raw == expected:
                    cpu.endpoint(process, port, "a" * 64, argv, time.monotonic() + 1)
                    endpoint.return_value.check.assert_called_once()
                else:
                    with self.assertRaises(ValueError):
                        cpu.endpoint(process, port, "a" * 64, argv, time.monotonic() + 1)
                    endpoint.assert_not_called()

    def test_native_host_label_fails_closed_without_native_hosted_linux(self):
        env = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted"}
        self.assertEqual(rec.host("local")["provider"], "local")
        for platform, machine, environment, okay in (("linux", "x86_64", env, True),
                ("darwin", "arm64", env, False), ("linux", "aarch64", env, False),
                ("linux", "x86_64", {}, False),
                ("linux", "x86_64", {**env, "RUNNER_ENVIRONMENT": "self-hosted"}, False)):
            with mock.patch.object(rec.sys, "platform", platform), \
                    mock.patch.object(rec.os, "uname", return_value=types.SimpleNamespace(machine=machine)), \
                    mock.patch.dict(os.environ, environment, clear=True):
                if okay:
                    self.assertEqual(rec.host("github-actions")["provider"], "github-actions")
                else:
                    with self.assertRaises(ValueError): rec.host("github-actions")

    def test_plan_reuses_all_existing_pins_with_fixed_urls_and_sizes(self):
        plan = native.plan(cpu.settings())
        self.assertEqual(set(plan), {"llama.tar.gz", "0.gguf", "1.gguf", rec.WHEEL})
        for index, model in enumerate(cpu.MODEL_IDS):
            pin = cpu.settings()["models"][model]
            self.assertEqual(plan[str(index) + ".gguf"], ("https://huggingface.co/" + pin["repository"]
                + "/resolve/" + pin["revision"] + "/" + pin["filename"], pin["bytes"], pin["sha256"]))
        self.assertEqual(plan[rec.WHEEL][1:], (27800904, rec.WHEEL_SHA))
        self.assertEqual(plan["llama.tar.gz"][1:], (38195486, cpu.settings()["engine"]["archive_sha256"]))

    def test_fetch_streams_with_parent_byte_deadline_and_hash_checks(self):
        raw = b"fixed public input"
        for bad in (None, "hash", "short", "exit", "stderr", "timeout"):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as directory:
                fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    snapshot = mock.Mock()
                    def execute(argv, **kwargs):
                        self.assertEqual(argv[:5], ["curl", "-q", "--fail", "--silent", "--location"])
                        self.assertEqual(kwargs["max_output_bytes"], len(raw))
                        self.assertEqual(kwargs["max_stderr_bytes"], 4096)
                        self.assertLessEqual(kwargs["timeout"], 5)
                        self.assertNotIn("--output", argv)
                        if bad == "timeout": raise subprocess.TimeoutExpired("fixed", 5)
                        kwargs["stdout_file"].write(raw[:-1] if bad == "short" else raw)
                        return subprocess.CompletedProcess(argv, 1 if bad == "exit" else 0, b"",
                                                           b"private-canary" if bad == "stderr" else b"")
                    snapshot.execute_process_group.side_effect = execute
                    spec = ("https://fixed.invalid/input", len(raw),
                            "0" * 64 if bad == "hash" else hashlib.sha256(raw).hexdigest())
                    if bad is None:
                        native.fetch(fd, "input", spec, {}, snapshot, time.monotonic() + 5)
                        self.assertEqual((Path(directory) / "input").read_bytes(), raw)
                    else:
                        with self.assertRaises((ValueError, subprocess.TimeoutExpired)):
                            native.fetch(fd, "input", spec, {}, snapshot, time.monotonic() + 5)
                finally:
                    os.close(fd)

    def test_fetch_never_overwrites_or_follows_existing_leaf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_bytes(b"untouched")
            (root / "link").symlink_to(target)
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                for name in ("target", "link"):
                    with self.assertRaises(FileExistsError):
                        native.fetch(fd, name, ("https://fixed.invalid", 9, "a" * 64), {}, mock.Mock(), time.monotonic() + 5)
                self.assertEqual(target.read_bytes(), b"untouched")
            finally:
                os.close(fd)

    def test_workflow_is_explicit_native_main_only_and_exports_only_receipts(self):
        workflow = (ROOT / ".github/workflows/tau-cpu-parity-native.yml").read_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("pull_request:", workflow)
        self.assertNotIn("  push:", workflow)
        for text in ("github.ref == 'refs/heads/main'", "runs-on: ubuntu-24.04",
                     'test "$(uname -m)" = x86_64', "persist-credentials: false", "fetch-depth: 0",
                     "--host-kind github-actions", "timeout-minutes: 60", "timeout-minutes: 40",
                     "build/tau-cpu-native/proof/run.json", "build/tau-cpu-native/proof/output.log",
                     'run: test "$OUTCOME" = success'):
            self.assertIn(text, workflow)
        self.assertIn("scripts/prepare-tau-cpu-parity-native.py", rec.SOURCES)
        self.assertIn(".github/workflows/tau-cpu-parity-native.yml", rec.SOURCES)
        self.assertNotIn("sudo ", workflow)
        self.assertNotIn("secrets.", workflow)

    def test_preparation_pulls_only_two_pins_after_all_inputs_and_stops_on_failure(self):
        bases = json.loads((ROOT / "scripts/e2e/pins.json").read_bytes())
        for failure in (None, "fetch", "pull"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                snapshot, fetched = mock.Mock(), []
                def fetch(fd, name, spec, env, tools, deadline):
                    self.assertIs(tools, snapshot)
                    fetched.append(name)
                    if failure == "fetch": raise ValueError("fixed")
                def execute(argv, **kwargs):
                    self.assertEqual(set(fetched), set(native.plan(cpu.settings())))
                    self.assertEqual(argv[:4], ["docker", "pull", "--platform", "linux/amd64"])
                    self.assertIn(argv[4], (bases["go_image"], bases["python_image"]))
                    self.assertLessEqual(kwargs["timeout"], 180)
                    self.assertFalse(kwargs["capture_output"])
                    return subprocess.CompletedProcess(argv, 1 if failure == "pull" else 0)
                snapshot.execute_process_group.side_effect = execute
                with mock.patch.dict(native.prepare.__globals__, fetch=fetch):
                    args = (Path(directory).resolve() / "new", ROOT, {}, snapshot, time.monotonic() + 200)
                    if failure:
                        with self.assertRaises(ValueError): native.prepare(*args)
                    else:
                        native.prepare(*args)
                self.assertEqual(snapshot.execute_process_group.call_count,
                                 0 if failure == "fetch" else 1 if failure == "pull" else 2)

    def test_expired_preparation_budget_does_not_start_a_download(self):
        snapshot = mock.Mock()
        with tempfile.TemporaryDirectory() as directory:
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaises(ValueError):
                    native.fetch(fd, "new", ("https://fixed.invalid", 1, "a" * 64), {}, snapshot,
                                 time.monotonic() - 1)
                self.assertEqual(list(Path(directory).iterdir()), [])
                snapshot.execute_process_group.assert_not_called()
            finally:
                os.close(fd)


if __name__ == "__main__":
    unittest.main()
