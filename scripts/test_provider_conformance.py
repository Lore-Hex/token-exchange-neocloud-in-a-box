#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Wrapper regressions plus opt-in real pinned checker / closed-endpoint Linux test."""

import copy
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
import uuid

import provider_conformance as prep
import provider_conformance_worker as worker
from test_tau_runtime import wheel

ROOT = Path(__file__).absolute().parent.parent
spec = importlib.util.spec_from_file_location("conformance_snapshot", ROOT / "scripts/e2e/snapshot.py")
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)


class ConformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp" if sys.platform == "darwin" else None)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def checks(self):
        return types.SimpleNamespace(selected_model=worker.MODEL, checks=[
            types.SimpleNamespace(id=name, tier=tier, status="skip" if name == "catalog.declared-v2" else "pass",
                                  measured={"model_ids": [worker.MODEL], "unreadable_row_count": 0,
                                            "body": "PRIVATE_CONTENT_CANARY"})
            for name, tier in worker.ALLOWED.items()])

    def test_host_and_worker_pin_the_exact_shared_support_source(self):
        raw = (ROOT / "scripts/tau_trial_worker.py").read_bytes()
        self.assertEqual(prep.SUPPORT_SHA, hashlib.sha256(raw).hexdigest())
        self.assertEqual(worker.SUPPORT_SHA, prep.SUPPORT_SHA)
        with self.assertRaises(ValueError):
            prep.source("scripts/tau_trial_worker.py", "0" * 64)

    def test_real_lock_is_unchanged_and_all_dependencies_required(self):
        raw = (ROOT / "scripts/e2e/requirements.lock").read_bytes()
        self.assertEqual(len(prep.requirements(raw)), 17)
        with self.assertRaises(ValueError):
            prep.requirements(raw + b"\n")
        with self.assertRaises(ValueError):
            prep.prepare(self.root, time.monotonic() + 10)

    def test_import_hooks_duplicates_and_wrong_wheel_hash_rejected(self):
        for name in ("alpha.pth", "alpha.pyc", "alpha.pyo", "alpha.egg-link", "alpha.data/a",
                     "sitecustomize.py", "usercustomize/__init__.py", "../outside.py"):
            with self.subTest(name=name):
                raw = wheel(members=[(name, b"never imported")])
                expected = {"filename": "alpha-1.0-py3-none-any.whl", "version": "1.0",
                            "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                with self.assertRaises(ValueError):
                    prep.add_wheel({}, raw, expected, time.monotonic() + 10)
        raw = wheel()
        expected = {"filename": "alpha-1.0-py3-none-any.whl", "version": "1.0",
                    "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        files = {}
        prep.add_wheel(files, raw, expected, time.monotonic() + 10)
        self.assertIn("packages/alpha/__init__.py", files)
        with self.assertRaises(ValueError):
            prep.add_wheel(files, raw, expected, time.monotonic() + 10)
        with self.assertRaises(ValueError):
            prep.add_wheel({}, raw + b"tamper", expected, time.monotonic() + 10)

    def test_all_pass_requires_exact_identity_and_preserves_upstream_statuses(self):
        run = self.checks()
        self.assertTrue(worker.project(run, True)["mandatory_checks_passed"])
        self.assertNotIn("PRIVATE_CONTENT_CANARY", json.dumps(worker.project(run, True)))
        for status in ("warn", "skip", "fail"):
            changed = copy.deepcopy(run)
            changed.checks[0].status = status
            result = worker.project(changed, status != "fail")
            self.assertFalse(result["mandatory_checks_passed"])
            self.assertEqual(result["checks"][changed.checks[0].id], status)
        for models in ([], ["other"], [worker.MODEL, "other"]):
            changed = copy.deepcopy(run)
            changed.checks[0].measured["model_ids"] = models
            self.assertFalse(worker.project(changed, True)["mandatory_checks_passed"])
        run.checks.pop()
        self.assertFalse(worker.project(run, True)["mandatory_checks_passed"])

    def test_malformed_projection_rejected_without_echoing_values(self):
        for key, value in (("id", "PRIVATE_CONTENT_CANARY"), ("tier", True), ("status", "success")):
            run = self.checks()
            setattr(run.checks[0], key, value)
            with self.assertRaisesRegex(ValueError, "^provider checker boundary rejected$"):
                worker.project(run, True)
        run = self.checks()
        run.checks[-1] = run.checks[0]
        with self.assertRaises(ValueError):
            worker.project(run, True)
        with self.assertRaises(ValueError):
            worker.project(self.checks(), False)

    def test_network_and_process_boundary(self):
        worker.audit("socket.connect", (None, ("127.0.0.1", 8000)))
        worker.audit("socket.getaddrinfo", ("127.0.0.1", 8000, 0, 0, 0))
        for event, args in (("socket.connect", (None, ("127.0.0.1", 8001))),
                            ("socket.connect", (None, ("192.0.2.1", 8000))),
                            ("socket.connect", (None, "/var/run/docker.sock")),
                            ("socket.getaddrinfo", ("example.invalid", 8000)),
                            ("subprocess.Popen", ()), ("os.fork", ()), ("socket.bind", ())):
            with self.subTest(event=event, args=args), self.assertRaises(worker.BoundaryAbort):
                worker.audit(event, args)

    def test_native_output_exception_and_timeout_are_content_free(self):
        for body in ("raise RuntimeError('PRIVATE_CONTENT_CANARY')", "while True: pass"):
            code = ("import importlib.util,os,sys\n"
                    f"s=importlib.util.spec_from_file_location('worker',{str(ROOT / 'scripts/provider_conformance_worker.py')!r})\n"
                    "m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n"
                    "m.SECONDS=0.05\n"
                    "def execute(*args):\n"
                    " os.write(1,b'PRIVATE_CONTENT_CANARY');os.write(2,b'PRIVATE_CONTENT_CANARY')\n"
                    f" {body}\n"
                    "m.execute=execute;sys.argv=['worker','0'*64];sys.exit(m.main())\n")
            completed = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code],
                                       capture_output=True, timeout=10, check=False)
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(completed.stderr, b"")
            self.assertNotIn(b"PRIVATE_CONTENT_CANARY", completed.stdout)
            self.assertEqual(json.loads(completed.stdout), worker.result("rejected"))

    def test_tree_tamper_extra_file_symlink_and_manifest_tamper(self):
        base = {"worker.py": (ROOT / "scripts/provider_conformance_worker.py").read_bytes(),
                "support.py": (ROOT / "scripts/tau_trial_worker.py").read_bytes(),
                "packages/example.py": b"raise AssertionError('NEVER_IMPORT')\n"}
        plan = {"schema": "nib.private-provider-payload/v1", "requirements_sha256": worker.LOCK_SHA,
                "checker_wheel_sha256": worker.CHECKER_SHA,
                "files": {name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                          for name, raw in base.items()}}
        raw = prep.runtime.encoded(plan)
        sha = hashlib.sha256(raw).hexdigest()
        for change in ("none", "bytes", "extra", "symlink", "manifest"):
            dest = self.root / change
            prep.publish(dest, {**base, "trial.json": raw})
            payload = dest / "payload"
            self.assertEqual(dest.stat().st_mode & 0o777, 0o700)
            for directory, dirs, names in os.walk(payload):
                os.chmod(directory, 0o700)
                for name in names:
                    os.chmod(Path(directory) / name, 0o600)
            path = payload / "packages/example.py"
            if change == "bytes": path.write_bytes(b"different")
            if change == "extra": (payload / "extra.py").write_bytes(b"extra")
            if change == "symlink":
                path.unlink()
                path.symlink_to(payload / "worker.py")
            if change == "manifest": (payload / "trial.json").write_bytes(raw + b"\n")
            if change == "none":
                self.assertEqual(worker.verify(payload, sha), plan)
            else:
                with self.assertRaises((ValueError, OSError)):
                    worker.verify(payload, sha)


@unittest.skipUnless(os.environ.get("NIB_PROVIDER_WHEELHOUSE") and os.environ.get("NIB_PROVIDER_DOCKER_SOCKET"),
                     "opt-in local Linux closed-endpoint checker proof")
class RealCheckerClosedEndpointTest(unittest.TestCase):
    def test_real_locked_checker_in_network_none(self):
        import tau_trial
        now = lambda: datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        started = now()
        deadline = time.monotonic() + 60
        producer = prep.identity(deadline)
        sources = ("scripts/test_provider_conformance.py", "scripts/test_tau_runtime.py",
                   "scripts/tau_trial.py", "scripts/tau_aggregate.py", "scripts/e2e/snapshot.py", "host/probe.py",
                   "scripts/e2e/pins.json")
        for offset in range(0, len(sources), 4):
            current = prep.runtime.tau.committed_identity(deadline, extra_files=sources[offset:offset + 4])
            self.assertEqual(current["git_sha"], producer["git_sha"])
            producer["files"].update(current["files"])
        socket = Path(os.environ["NIB_PROVIDER_DOCKER_SOCKET"]).resolve(strict=True)
        self.assertTrue(socket.is_socket())
        wheels = Path(os.environ["NIB_PROVIDER_WHEELHOUSE"]).resolve(strict=True)
        files = prep.prepare(wheels, time.monotonic() + 60)
        sha = prep.runtime.digest(files["trial.json"])
        image = prep.runtime.TARGET["image"]
        owner = uuid.uuid4().hex
        name, label = "nib-checker-closed-" + owner, "nib.checker-closed-proof=" + owner
        report, cleaned, created = None, False, False
        with tempfile.TemporaryDirectory(dir="/private/tmp" if sys.platform == "darwin" else None) as tmp:
            private = Path(tmp).resolve()
            (private / "config.json").write_text('{"auths":{}}\n')
            prep.publish(private / "prepared", files)
            payload = private / "prepared/payload"
            worker.verify(payload, sha)
            docker = shutil.which("docker")
            self.assertIsNotNone(docker)
            env = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "HOME": str(private),
                   "DOCKER_CONFIG": str(private), "LANG": "C", "LC_ALL": "C"}
            def command(*args, seconds=20, checked=True):
                result = snapshot.execute_process_group(
                    [docker, "--host", "unix://" + str(socket), "--config", str(private), *args],
                    env=env, cwd=str(ROOT), timeout=seconds, max_output_bytes=65536, max_stderr_bytes=65536,
                    stdin=subprocess.DEVNULL)
                if checked:
                    self.assertEqual(result.returncode, 0, "bounded Docker operation failed")
                return result
            old_term = signal.getsignal(signal.SIGTERM)
            def cancelled(signum, frame):
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, cancelled)
            try:
                image_info = json.loads(command("image", "inspect", "--platform", "linux/amd64", image).stdout)[0]
                selected = tau_trial.check_image(image_info)
                command("create", "--name", name, "--label", label, "--pull", "never", "--platform", "linux/amd64",
                        "--runtime", "runc", "--network", "none", "--read-only", "--cap-drop", "ALL",
                        "--security-opt", "no-new-privileges", "--user", "65534:65534", "--memory", "1g",
                        "--memory-swap", "1g", "--cpus", "1", "--pids-limit", "32", "--ipc", "private",
                        "--cgroupns", "private", "--restart", "no", "--log-driver", "none", "--no-healthcheck",
                        "--workdir", "/scratch", "--mount", "type=bind,src=" + str(payload) + ",dst=/opt/nib,readonly",
                        "--tmpfs", "/scratch:rw,nosuid,nodev,noexec,size=16m,mode=0700,uid=65534,gid=65534",
                        "--entrypoint", "/usr/bin/env", image, "-i",
                        *[key + "=" + value for key, value in sorted(worker.ENVIRONMENT.items())],
                        "/usr/local/bin/python3", "-I", "-S", "-B", "/opt/nib/worker.py", sha)
                created = True
                info = json.loads(command("inspect", name).stdout)[0]
                self.assertEqual(tau_trial.manifest_identity(info["ImageManifestDescriptor"]), selected)
                self.assertEqual(info["HostConfig"]["NetworkMode"], "none")
                self.assertFalse(info["HostConfig"]["DeviceRequests"])
                completed = command("start", "--attach", name, seconds=200, checked=False)
                state = json.loads(command("inspect", name).stdout)[0]["State"]
                self.assertEqual(completed.stderr, b"")
                self.assertLessEqual(len(completed.stdout), 16384)
                self.assertTrue(completed.stdout, "missing private result; exit=" + str(state["ExitCode"]))
                report = json.loads(completed.stdout)
                self.assertEqual(completed.returncode, 1, json.dumps(report))
                self.assertEqual(report, worker.result("completed", report["projection"]))
                self.assertFalse(report["projection"]["mandatory_checks_passed"])
                # 0.1.8 treats connection failures as inconclusive warnings/skips.
                # Its permissive gate alone must never turn this control green.
                self.assertTrue(report["projection"]["upstream_conformance_gate"])
                self.assertIn("warn", report["projection"]["checks"].values())
                self.assertFalse(state["Running"])
                self.assertFalse(state["OOMKilled"])
                self.assertEqual(state["ExitCode"], 1)
            finally:
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                try:
                    found = command("ps", "-aq", "--filter", "label=" + label).stdout.decode().split()
                    self.assertLessEqual(len(found), 1)
                    if found:
                        command("rm", "--force", *found)
                    cleaned = created and not command("ps", "-aq", "--filter", "label=" + label).stdout.strip()
                finally:
                    signal.signal(signal.SIGTERM, old_term)
                    for directory, dirs, names in os.walk(payload):
                        os.chmod(directory, 0o700)
            self.assertTrue(cleaned)
        proof = os.environ.get("NIB_PROVIDER_PROOF")
        if proof:
            record = {"kind": "local-linux-closed-endpoint-negative-control", "image": image,
                      "selected_image": selected, "payload_sha256": sha,
                      "producer": producer, "started_utc": started, "ended_utc": now(),
                      "cleanup_confirmed": cleaned, "deepseek_executed": False, "worker_exit_code": 1,
                      "report": report}
            envelope = {"schema": "nib.evidence/v1", "phase": "P2", "task": "T3", "git_sha": producer["git_sha"],
                        "command": "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts NIB_PROVIDER_WHEELHOUSE=PRIVATE "
                                   "NIB_PROVIDER_DOCKER_SOCKET=PRIVATE NIB_PROVIDER_PROOF=PRIVATE python3 -m unittest -v "
                                   "test_provider_conformance.RealCheckerClosedEndpointTest",
                        "started_utc": started, "ended_utc": record["ended_utc"], "result": "pass",
                        "execution": "software", "hardware_validated": False,
                        "host": {"provider": "local", "machine": "isolated-linux-amd64-container",
                                 "gpus": "none", "driver": None, "cuda": None},
                        "notes": "Negative-control wrapper PASS: unchanged upstream checker completed against no endpoint, "
                                 "returned warn/skip with upstream gate true, wrapper rejected it with exit 1. "
                                 "No DeepSeek, conformance, quality or confidentiality acceptance."}
            tau_trial.publish_evidence(Path(proof), {"run.json": prep.runtime.encoded(envelope),
                                                    "output.log": prep.runtime.encoded(record)})


if __name__ == "__main__":
    unittest.main()
