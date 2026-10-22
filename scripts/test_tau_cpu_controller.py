# SPDX-License-Identifier: Apache-2.0
"""Synthetic private-pipe controls; no Docker, model or tau execution."""
import copy
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import tau_cpu_session as session
import test_tau_cpu_session as fixtures
from test_evidence_history import git

ROOT = Path(__file__).resolve().parent.parent
control = types.SimpleNamespace(**runpy.run_path(str(ROOT / "scripts/record-tau-cpu-session.py")))
rec = control.rec
snapshot = rec.load(ROOT / rec.SNAPSHOT, "controller_test_snapshot")
CANARY = "PRIVATE-CONTENT-MUST-NOT-APPEAR"


def report():
    return {"stage": "producer", "ready": None, "cleanup": {"process_group": "unknown", "producer": "unknown"}}


def write(directory, name, raw):
    path = directory / name
    path.write_bytes(raw)
    path.chmod(0o600)


def ready(directory):
    value = fixtures.packet()
    with mock.patch.object(session.identity, "source_receipt"):
        originals, held = session.accept(session.probes.encoded(value), fixtures.PRODUCER,
                                          fixtures.IMAGE, time.monotonic() + 5)
    for name, raw in originals.items():
        write(directory, name, raw)
    return held


class CPUControllerTests(unittest.TestCase):
    def test_preflight_and_verified_envelopes_have_complete_evidence_host_schema(self):
        schema = json.loads((ROOT / "schemas/evidence.schema.json").read_bytes())["$defs"]["host"]
        for verified in (False, True):
            host = control.evidence_host(verified)
            self.assertEqual(set(host), set(schema["required"]))
            self.assertEqual(host, {"provider": "github-actions" if verified else "unverified",
                "machine": "native-cpu-controller" if verified else "unverified",
                "gpus": "none", "driver": None, "cuda": None})
        for value in (None, 1, "true"):
            with self.assertRaises(ValueError): control.evidence_host(value)

    def test_preflight_rejects_non_native_non_main_and_insufficient_resources(self):
        environment = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
                       "GITHUB_REF": "refs/heads/main"}
        for fault in (None, "platform", "machine", "provider", "branch", "total", "available", "cpus", "disk"):
            with self.subTest(fault=fault), mock.patch.dict(os.environ, environment, clear=True), \
                    mock.patch.object(sys, "platform", "darwin" if fault == "platform" else "linux"), \
                    mock.patch.object(os, "uname", return_value=types.SimpleNamespace(
                        machine="arm64" if fault == "machine" else "x86_64")), \
                    mock.patch.object(os, "cpu_count", return_value=3 if fault == "cpus" else 4), \
                    mock.patch.object(control.shutil, "disk_usage", return_value=types.SimpleNamespace(
                        free=1 if fault == "disk" else 12 << 30)), \
                    mock.patch.object(Path, "read_bytes", return_value=(
                        f"MemTotal: {1 if fault == 'total' else 10 << 20} kB\n"
                        f"MemAvailable: {1 if fault == 'available' else 8 << 20} kB\n").encode()):
                if fault == "provider": os.environ["RUNNER_ENVIRONMENT"] = "self-hosted"
                if fault == "branch": os.environ["GITHUB_REF"] = "refs/heads/topic"
                if fault is None:
                    self.assertEqual(control.preflight()["platform"], "linux/amd64")
                else:
                    with self.assertRaises(ValueError): control.preflight()

    def test_originals_revalidate_exact_current_producer_frame_and_private_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            held = ready(directory)
            before = {name: (directory / name).read_bytes() for name in session.FILES}
            with mock.patch.object(session.identity, "source_receipt") as source:
                image = control.originals(session, directory, held, fixtures.PRODUCER, time.monotonic() + 5)
            self.assertEqual(image, fixtures.IMAGE)
            self.assertEqual(source.call_count, 4)
            self.assertEqual(before, {name: (directory / name).read_bytes() for name in session.FILES})
            for field, value in (("ready_sha256", "e" * 64), ("expires_unix", int(time.time()) - 1),
                                 ("expires_unix", True), ("body", CANARY)):
                altered = {**held, field: value}
                with self.subTest(field=field), mock.patch.object(session.identity, "source_receipt"), \
                        self.assertRaises((ValueError, session.llama.worker.Abort)):
                    control.originals(session, directory, altered, fixtures.PRODUCER, time.monotonic() + 5)
            with mock.patch.object(session.identity, "source_receipt"), self.assertRaises(ValueError):
                control.originals(session, directory, held, {**fixtures.PRODUCER, "git_sha": "e" * 40}, time.monotonic() + 5)

    def test_originals_reject_drift_symlink_permissions_and_missing_file(self):
        for fault in ("drift", "symlink", "mode", "missing", "directory-mode"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary).resolve()
                held = ready(directory)
                path = directory / session.FILES[0]
                if fault == "drift": path.write_bytes(b"{}")
                if fault == "mode": path.chmod(0o644)
                if fault == "directory-mode": directory.chmod(0o755)
                if fault in ("symlink", "missing"):
                    path.unlink()
                    if fault == "symlink": path.symlink_to(directory / session.FILES[1])
                with mock.patch.object(session.identity, "source_receipt"), \
                        self.assertRaises((ValueError, OSError, session.llama.worker.Abort)):
                    control.originals(session, directory, held, fixtures.PRODUCER, time.monotonic() + 5)

    def run_pipe(self, script, *, seconds=3, identity_failure=False, cleanup_failure=False):
        current = report()
        tools = mock.Mock()
        def cleanup(process):
            snapshot.cleanup_process_group(process)
            self.assertIsNotNone(process.poll())
            if cleanup_failure: raise ValueError(CANARY)
        tools.cleanup_process_group.side_effect = cleanup
        with mock.patch.dict(control.supervise.__globals__,
                originals=mock.Mock(side_effect=ValueError(CANARY) if identity_failure else None,
                                    return_value=fixtures.IMAGE), CLEANUP_SECONDS=0.02):
            try:
                result = control.supervise([sys.executable, "-I", "-S", "-B", "-c", script], {}, tools,
                    session, Path("/unused"), fixtures.PRODUCER, current, time.monotonic() + seconds)
                return result, current
            finally:
                tools.cleanup_process_group.assert_called_once()
                self.assertNotIn(CANARY, json.dumps(current))

    def test_real_private_pipes_release_once_only_after_valid_ready(self):
        held = {"schema": "nib.tau-cpu-pair-held/v1", "ready_sha256": "a" * 64,
                "expires_unix": int(time.time()) + 60, "receipts": dict.fromkeys(session.FILES, "b" * 64)}
        frame = session.READY + session.probes.encoded(held) + b"\n"
        ack = session.DONE + session.probes.encoded({"ready_sha256": "a" * 64, "released": True}) + b"\n"
        script = ("import sys;sys.stdout.buffer.write(" + repr(frame) + ");sys.stdout.flush();"
                  "assert sys.stdin.buffer.readline()==" + repr(ack) + ";"
                  "assert sys.stdin.buffer.read()==b'';"
                  "sys.stderr.write('CPU session: pass; no qualification claim.\\n')")
        result, current = self.run_pipe(script)
        self.assertEqual(result, (held, fixtures.IMAGE))
        self.assertEqual(current["cleanup"], {"process_group": "confirmed", "producer": "unknown"})
        self.assertEqual(current["stage"], "release")
        with self.assertRaises(ValueError): self.run_pipe(script, identity_failure=True)
        with self.assertRaises(ValueError): self.run_pipe(script, cleanup_failure=True)

    def test_protocol_failure_timeout_output_limit_and_child_exit_are_failed(self):
        prefix = "import sys,time;"
        scripts = (
            prefix + "sys.stdout.write('wrong\\n');sys.stdout.flush();sys.stdin.read()",
            prefix + "sys.stdout.write('x'*4097);sys.stdout.flush();sys.stdin.read()",
            prefix + "sys.stderr.write('x'*1025);sys.stderr.flush();sys.stdin.read()",
            prefix + "sys.exit(0)",
            prefix + "sys.stdin.read()",
            prefix + "time.sleep(30)",
            prefix + "sys.stdout.buffer.write(" + repr(session.READY + b'{}\nextra\n') + ");sys.stdout.flush();sys.stdin.read()",
        )
        for script in scripts:
            with self.subTest(script=script), self.assertRaises((ValueError, session.llama.worker.Abort)):
                self.run_pipe(script, seconds=0.2)

    def test_final_originals_require_source_image_cleanup_and_release_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            held = ready(directory)
            sources = {name: rec.digest((ROOT / name).read_bytes()) for name in rec.SOURCES}
            record = {"schema": "nib.evidence/v1", "phase": "P2", "task": "T3", "git_sha": "a" * 40,
                "result": "pass", "execution": "software", "hardware_validated": False,
                "host": rec.host("local"), "started_utc": rec.now(), "ended_utc": rec.now(),
                "command": "python3 -I -S -B scripts/record-tau-cpu-parity.py --archive PRIVATE --model-06 PRIVATE --model-17 PRIVATE --cmake-wheel PRIVATE --output PRIVATE --session",
                "notes": "Fresh held CPU-pair receipts only. No target, tau trial, quality, live admission or confidential claim."}
            pins = json.loads((ROOT / "scripts/e2e/pins.json").read_bytes())
            row = {"schema": "nib.tau-cpu-session-run/v1", "result": "pass", "stage": "complete",
                "git_sha": "a" * 40, "source_archive_sha256": "b" * 64, "context_sha256": "d" * 64,
                "runtime": fixtures.result(held), "sources": sources, "image_id": "sha256:" + fixtures.IMAGE,
                "base_images": {key: pins[key] for key in ("go_image", "python_image")},
                "cleanup": dict.fromkeys(("container", "image", "build_completion"), "confirmed"),
                "quality_eligible": False, "confidential": False, "live_admission": False}
            write(directory, "run.json", session.tau.encoded(record))
            mutations = (None, lambda r: r.update(git_sha="e" * 40), lambda r: r.update(source_archive_sha256="e" * 64),
                lambda r: r["sources"].update({rec.SCRIPT: "e" * 64}), lambda r: r.update(image_id="sha256:" + "e" * 64),
                lambda r: r["cleanup"].update(container="unknown"), lambda r: r["runtime"].update(acknowledged=False),
                lambda r: r.update(live_admission=True), lambda r: r.update(response_body=CANARY))
            for mutate in mutations:
                altered = copy.deepcopy(row)
                if mutate: mutate(altered)
                write(directory, "output.log", session.tau.encoded(altered))
                args = (session, directory, held, fixtures.IMAGE, "a" * 40, "b" * 64, sources, time.monotonic() + 5)
                if mutate is None:
                    self.assertEqual(set(control.final_receipts(*args)), {"run.json", "output.log"})
                else:
                    with self.assertRaises((ValueError, session.llama.worker.Abort)): control.final_receipts(*args)

    def test_execute_uses_only_fixed_recorder_and_requires_terminal_receipt_validation(self):
        source, directory = Path("/private/source"), Path("/private/proof")
        inputs = [Path("/private") / str(index) for index in range(4)]
        held = {"ready_sha256": "a" * 64}
        for failure in (False, True):
            current = {**report(), "git_sha": "a" * 40, "source_archive_sha256": "b" * 64,
                       "sources": {rec.SCRIPT: "c" * 64}}
            observe = mock.Mock(return_value=(held, fixtures.IMAGE))
            finish = mock.Mock(return_value={"run.json": "d" * 64, "output.log": "e" * 64})
            if failure: finish.side_effect = ValueError(CANARY)
            with mock.patch.object(rec, "toolkit", return_value=session.cpu), \
                    mock.patch.dict(sys.modules, tau_cpu_session=session), \
                    mock.patch.dict(control.execute.__globals__, supervise=observe, final_receipts=finish):
                if failure:
                    with self.assertRaises(ValueError):
                        control.execute(source, {}, snapshot, inputs, directory, current, time.monotonic() + 5)
                else:
                    control.execute(source, {}, snapshot, inputs, directory, current, time.monotonic() + 5)
            argv = observe.call_args.args[0]
            self.assertEqual(argv, [sys.executable, "-I", "-S", "-B", str(source / rec.SCRIPT),
                "--archive", str(inputs[0]), "--model-06", str(inputs[1]), "--model-17", str(inputs[2]),
                "--cmake-wheel", str(inputs[3]), "--output", str(directory / "pair"), "--session"])
            self.assertEqual(current["cleanup"]["producer"], "unknown" if failure else "confirmed")
            self.assertNotIn("result", current)

    def test_workflow_is_explicit_and_default_route_is_unchanged(self):
        text = (ROOT / control.WORKFLOW).read_text()
        for required in ("workflow_dispatch:", "github.ref == 'refs/heads/main'", "runs-on: ubuntu-24.04",
                         "persist-credentials: false", "group: native-tau-cpu-parity", "cancel-in-progress: false",
                         "timeout-minutes: 60", "timeout-minutes: 40", "retention-days: 7",
                         "continue-on-error: true", 'run: test "$OUTCOME" = success'):
            self.assertIn(required, text)
        for forbidden in ("pull_request:", "  push:", "secrets.", "sudo ", "--gpus", "--privileged", "**"):
            self.assertNotIn(forbidden, text)
        paths = [line.strip() for line in text.splitlines() if line.strip().startswith("build/tau-cpu-session/proof/")]
        self.assertEqual(len(paths), 8)
        self.assertEqual(set(path.rsplit("/", 1)[1] for path in paths), {"run.json", "output.log", *session.FILES})
        for path in (".github/workflows/tau-cpu-parity-native.yml", "scripts/prepare-tau-cpu-parity-native.py",
                     "scripts/tau_cpu_parity.py", "scripts/pilot/run.py"):
            self.assertEqual((ROOT / path).read_bytes(), git(ROOT, "show", "0a89d7f5193fab48f1b1e3cfa8e88fd772436f4f:" + path))
        self.assertNotIn(control.SCRIPT, rec.SOURCES)
        self.assertNotIn(control.WORKFLOW, rec.SOURCES)


if __name__ == "__main__":
    unittest.main()
