# SPDX-License-Identifier: Apache-2.0
"""Synthetic staging fixtures and local stdlib children; no weight/network/GPU use."""

from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
stage = types.ModuleType("stage_recorder_test")
stage.__file__ = str(ROOT / "host/record-stage.py")
exec(compile(Path(stage.__file__).read_bytes(), stage.__file__, "exec"), stage.__dict__)


class StageRecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nib-stage-test-", dir="/private/tmp" if sys.platform == "darwin" else None)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.bundle = self.root / "bundle"
        self.destination = self.root / "weights"
        self.evidence = self.root / "evidence"
        self.files = {"record-stage.py": stage.BOOTSTRAP, "nib": b"synthetic CLI, never executed",
                      "pack.yaml": b"synthetic pack", "files.sha256": b"synthetic manifest"}
        self.plan = dict(hf_repo="synthetic/model", hf_revision="a" * 40, manifest_sha256=stage.digest(self.files["files.sha256"]),
                         files=2, total_bytes=10, minimum_budget_bytes=12)
        self.receipt = dict(schema="nib.stage-bundle/v1", git_sha="b" * 40, build_source_sha256="c" * 64,
            builder_sha256=stage.BUILDER_SHA256, builder_git_sha=stage.BUILDER_REVISION,
            toolchain="go1.27.2", platform="linux/amd64", plan=self.plan,
            files={name: dict(sha256=stage.digest(data), bytes=len(data)) for name, data in self.files.items()},
            scope="trusted-offline-builder-receipt-not-reproducibility-or-attestation")
        with stage.new_directory(self.bundle) as fd:
            for name, data in self.files.items():
                stage.write(fd, name, data)
            stage.write(fd, "receipt.json", stage.json_bytes(self.receipt))
        self.receipt_hash = stage.digest(stage.json_bytes(self.receipt))
        self.proof = {key: self.plan[key] for key in ("hf_repo", "hf_revision", "manifest_sha256", "files")}
        self.proof.update(verified_bytes=10, downloaded_bytes=10, reused_files=0)
        self.disk = dict(available_bytes=100 << 30, required_bytes=(10 << 30) + 10, overhead_bytes=10 << 30, sufficient=True)

    def run_fixture(self, outputs=None, *, download=True, disk=None, disk_error=None, workers=1):
        results = iter(outputs or [(0, stage.json_bytes(self.plan)), (0, stage.json_bytes(self.proof))])
        def execute(argv, timeout, progress=None, state=None):
            self.assertEqual(stage.read(Path(argv[0]), stage.MAX_BINARY), self.files["nib"])
            if state is not None:
                state["cleanup"] = "confirmed"
            if progress is not None:
                self.assertEqual(argv[1:4], ["model", "stage", "--download"])
                self.assertEqual(timeout, 60)
                progress(0)
            item = next(results)
            if isinstance(item, Exception):
                raise item
            return item
        with mock.patch.object(stage.sys, "platform", "linux"), \
                mock.patch.object(stage.os, "uname", return_value=types.SimpleNamespace(machine="x86_64")), \
                mock.patch.object(stage, "capacity", return_value=disk or self.disk, side_effect=disk_error), \
                mock.patch.object(stage, "execute", side_effect=execute) as calls, redirect_stdout(io.StringIO()) as printed:
            code = stage.run(self.bundle, self.receipt_hash, self.destination, self.evidence, 60, 12, download, workers)
        return code, printed.getvalue(), calls

    def test_parallel_worker_limit_is_forwarded_and_recorded(self):
        code, _, calls = self.run_fixture(workers=4)
        self.assertEqual(code, 0)
        argv = calls.call_args_list[-1].args[0]
        self.assertEqual(argv[argv.index("--workers") + 1], "4")
        report = stage.parse(stage.read(self.evidence / "output.log"))
        record = stage.parse(stage.read(self.evidence / "run.json"))
        self.assertEqual(report["download_workers"], 4)
        self.assertIn("--workers 4", record["command"])

    def test_invalid_workers_rejected_before_execution(self):
        for workers in (0, -1, 5, True):
            with self.assertRaises(ValueError):
                self.run_fixture(workers=workers)
            self.assertFalse(self.evidence.exists())

    def test_receipt_and_source_tamper_fail_before_execution(self):
        receipt, files = stage.bundle(self.bundle, self.receipt_hash)
        self.assertEqual((receipt, files), (self.receipt, self.files))
        for name in self.files:
            path = self.bundle / name
            original = path.read_bytes()
            path.write_bytes(b"SYNTHETIC_PRIVATE_VALUE")
            with self.subTest(name=name), self.assertRaises(ValueError):
                stage.bundle(self.bundle, self.receipt_hash)
            path.write_bytes(original)
        with self.assertRaises(ValueError):
            stage.bundle(self.bundle, "0" * 64)
        (self.bundle / "extra").write_bytes(b"extra")
        with self.assertRaises(ValueError):
            stage.bundle(self.bundle, self.receipt_hash)

    def test_nonregular_source_and_unsafe_ancestors_do_not_block_or_follow(self):
        path = self.root / "source"
        os.mkfifo(path)
        with self.assertRaises(ValueError):
            stage.read(path)
        path.unlink()
        path.symlink_to(self.bundle / "pack.yaml")
        with self.assertRaises(OSError):
            stage.read(path)
        path.unlink()
        os.link(self.bundle / "pack.yaml", path)
        with self.assertRaises(ValueError):
            stage.read(path)
        path.unlink()
        link = self.root / "alias"
        link.symlink_to(self.bundle, target_is_directory=True)
        with self.assertRaises(OSError):
            stage.read(link / "pack.yaml")
        with self.assertRaises(ValueError):
            stage.read(self.bundle / "pack.yaml", 1)

    def test_capacity_uses_target_filesystem_and_conservative_full_pack(self):
        for existing in (False, True):
            if existing:
                self.destination.mkdir(mode=0o700)
            expected = self.destination.stat() if existing else self.root.stat()
            def statvfs(fd):
                actual = os.fstat(fd)
                self.assertEqual((actual.st_dev, actual.st_ino), (expected.st_dev, expected.st_ino))
                return types.SimpleNamespace(f_bavail=100, f_frsize=4096)
            with mock.patch.object(stage.os, "fstatvfs", side_effect=statvfs):
                disk = stage.capacity(self.destination, self.plan)
            self.assertEqual(disk["required_bytes"], (10 << 30) + 10)
            self.assertFalse(disk["sufficient"])
        huge = {**self.plan, "total_bytes": 510311045295}
        self.assertEqual(stage.capacity(self.destination, huge)["required_bytes"], 561342149825)
        self.destination.chmod(0o755)
        with self.assertRaises(ValueError):
            stage.capacity(self.destination, self.plan)

    def test_plan_only_and_disk_failure_never_start_download(self):
        code, printed, calls = self.run_fixture(download=False)
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(printed)["download"])
        calls.assert_not_called()
        self.assertFalse(self.evidence.exists())
        self.assertFalse(self.destination.exists())
        code, _, calls = self.run_fixture(disk={**self.disk, "sufficient": False, "available_bytes": 0})
        self.assertEqual(code, 1)
        calls.assert_not_called()
        report = json.loads((self.evidence / "output.log").read_bytes())
        self.assertEqual((report["stage"], report["cleanup"]), ("disk-preflight", "not-started"))

    def test_success_private_nooverwrite_and_no_runtime_claim(self):
        code, printed, calls = self.run_fixture()
        self.assertEqual((code, calls.call_count), (0, 2))
        self.assertFalse(json.loads(printed)["verified"])
        report = json.loads((self.evidence / "output.log").read_bytes())
        run = json.loads((self.evidence / "run.json").read_bytes())
        self.assertEqual(report["proof"], self.proof)
        self.assertEqual(report["cleanup"], "confirmed")
        self.assertEqual(run["git_sha"], self.receipt["git_sha"])
        self.assertEqual((run["phase"], run["task"], run["execution"]), ("P0", "T4", "software"))
        self.assertEqual(report["receipt_sha256"], self.receipt_hash)
        self.assertFalse(report["runtime_ready"] or report["hardware_validated"] or report["confidential_qualified"])
        self.assertEqual(self.evidence.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.evidence / "run.json").stat().st_mode & 0o777, 0o600)
        original = (self.evidence / "run.json").read_bytes()
        with self.assertRaises(FileExistsError):
            self.run_fixture()
        self.assertEqual((self.evidence / "run.json").read_bytes(), original)
        self.assertNotIn(str(self.root), json.dumps(report) + json.dumps(run))

    def test_failure_malformed_proof_and_plan_mismatch_are_recorded_sanitized(self):
        bad = {**self.proof, "verified_bytes": 10.0}
        for name, outputs in (("plan", [(0, b'{}')]), ("exit", [(0, stage.json_bytes(self.plan)), (1, b"SYNTHETIC_PRIVATE_VALUE")]),
                              ("proof", [(0, stage.json_bytes(self.plan)), (0, stage.json_bytes(bad))]),
                              ("timeout", [(0, stage.json_bytes(self.plan)), ValueError("SYNTHETIC_PRIVATE_VALUE")])):
            with self.subTest(name=name):
                self.evidence = self.root / name
                code, _, _ = self.run_fixture(outputs)
                self.assertEqual(code, 1)
                raw = (self.evidence / "output.log").read_bytes()
                self.assertNotIn(b"SYNTHETIC_PRIVATE_VALUE", raw)
                report = json.loads(raw)
                self.assertEqual(report["result"], "fail")
                self.assertIsNone(report["proof"])
                self.assertEqual(report["cleanup"], "confirmed")

    def test_disk_observation_error_records_failure_without_execution(self):
        code, _, calls = self.run_fixture(disk_error=OSError("SYNTHETIC_PRIVATE_VALUE"))
        self.assertEqual(code, 1)
        calls.assert_not_called()
        raw = (self.evidence / "output.log").read_bytes()
        self.assertNotIn(b"SYNTHETIC_PRIVATE_VALUE", raw)
        report = json.loads(raw)
        self.assertEqual((report["result"], report["stage"], report["cleanup"]), ("fail", "disk-preflight", "not-started"))
        self.assertIsNone(report["disk"])

    def test_nonisolated_bootstrap_rejects_before_helper_execution(self):
        result = subprocess.run([sys.executable, "-S", str(ROOT / stage.SCRIPT)],
                                env=stage.ENV, capture_output=True, timeout=5, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"Use python3 -I -S host/record-stage.py\n")

    def test_overlapping_paths_reject_before_writes_or_execution(self):
        for destination, evidence in ((self.bundle, self.evidence), (self.bundle / "weights", self.evidence),
            (self.root, self.evidence), (self.destination, self.bundle / "evidence"),
            (self.destination, self.root), (self.destination, self.destination),
            (self.destination, self.destination / "evidence"), (self.evidence / "weights", self.evidence)):
            with self.subTest(destination=destination, evidence=evidence), \
                    mock.patch.object(stage.sys, "platform", "linux"), \
                    mock.patch.object(stage.os, "uname", return_value=types.SimpleNamespace(machine="x86_64")), \
                    mock.patch.object(stage, "execute") as execute, \
                    mock.patch.object(stage, "new_directory") as create, self.assertRaises(ValueError):
                stage.run(self.bundle, self.receipt_hash, destination, evidence, 60, 12, True)
            execute.assert_not_called()
            create.assert_not_called()

    def test_precancelled_operation_never_spawns_and_records_failure(self):
        with mock.patch.object(stage, "CANCELLED", True), mock.patch.object(stage.subprocess, "Popen") as spawn:
            with self.assertRaises(ValueError):
                stage.execute([sys.executable, "-I", "-S", "-c", "raise SystemExit(0)"], 5)
            code, _, calls = self.run_fixture()
        spawn.assert_not_called()
        calls.assert_not_called()
        self.assertEqual(code, 1)
        report = json.loads((self.evidence / "output.log").read_bytes())
        self.assertEqual((report["result"], report["cleanup"]), ("fail", "not-started"))
        self.assertIsNone(report["proof"])

    def test_progress_is_metadata_only_bounded_and_not_integrity(self):
        self.assertEqual(stage.observation(self.destination, self.plan)["observed_bytes"], 0)
        self.destination.mkdir(mode=0o700)
        (self.destination / "file").write_bytes(b"abc")
        (self.destination / ".nib-part-synthetic").write_bytes(b"de")
        with mock.patch.object(stage, "read", side_effect=AssertionError("no content reads")):
            counts = stage.observation(self.destination, self.plan)
        self.assertEqual(counts, dict(observed_files=1, observed_bytes=3, partial_files=1, partial_bytes=2))
        (self.destination / "link").symlink_to("/SYNTHETIC_PRIVATE_VALUE")
        self.assertIsNone(stage.observation(self.destination, self.plan))
        (self.destination / "link").unlink()
        os.mkfifo(self.destination / "fifo")
        self.assertIsNone(stage.observation(self.destination, self.plan))

    def test_real_process_success_failure_output_bound_and_cleanup(self):
        state, samples = {}, []
        code, output = stage.execute([sys.executable, "-I", "-S", "-c", "print('{}')"], 5, samples.append, state)
        self.assertEqual((code, output, state["cleanup"]), (0, b"{}\n", "confirmed"))
        self.assertTrue(samples)
        code, _ = stage.execute([sys.executable, "-I", "-S", "-c", "raise SystemExit(1)"], 5, state=state)
        self.assertEqual(code, 1)
        with self.assertRaises(ValueError):
            stage.execute([sys.executable, "-I", "-S", "-c", "print('x'*100000)"], 5, state=state)
        self.assertEqual(state["cleanup"], "confirmed")
        with self.assertRaises(ValueError):
            stage.execute([sys.executable, "-I", "-S", "-c", "import sys;sys.stderr.write('SYNTHETIC_PRIVATE_VALUE')"], 5, state=state)
        self.assertEqual(state["cleanup"], "confirmed")

    def test_real_timeout_cancel_and_descendant_cleanup(self):
        marker = self.root / "late"
        descendant = "import time,pathlib;time.sleep(1);pathlib.Path(" + repr(str(marker)) + ").touch()"
        code = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-I','-S','-c'," + repr(descendant) + "]);time.sleep(10)"
        state = {}
        with mock.patch.object(stage, "GRACE", 0.2):
            for cancelled in (False, True):
                def progress(_):
                    if cancelled:
                        stage.cancel(None, None)
                with mock.patch.object(stage, "CANCELLED", False), self.assertRaises(ValueError):
                    stage.execute([sys.executable, "-I", "-S", "-c", code], 0.2, progress, state)
                self.assertEqual(state["cleanup"], "confirmed")
        time.sleep(1.1)
        self.assertFalse(marker.exists())

    def test_prepare_reuses_offline_builder_and_binds_binary_and_pack(self):
        builder = stage.load_builder()
        self.assertEqual(stage.digest(builder.BOOTSTRAP), stage.BUILDER_SHA256)
        source = {stage.SCRIPT: stage.BOOTSTRAP,
                  stage.PACK + "/pack.yaml": self.files["pack.yaml"], stage.PACK + "/files.sha256": self.files["files.sha256"]}
        builds = []
        def build(root, revision, work, env):
            builds.append(dict(env))
            binary = work / "nib"
            binary.write_bytes(b"fixture compiler output")
            return binary, "c" * 64
        destination = self.root / "prepared"
        with mock.patch.object(stage, "load_builder", return_value=builder), \
                mock.patch.object(stage, "committed", side_effect=lambda _, revision, name: source[name]), \
                mock.patch.object(builder.checker, "git", return_value=("b" * 40 + "\n").encode()), \
                mock.patch.object(builder, "build_validator", side_effect=build), \
                mock.patch.object(builder, "call", return_value=stage.json_bytes(self.plan)), redirect_stdout(io.StringIO()) as printed:
            stage.prepare(destination)
        received = json.loads(printed.getvalue())
        receipt, files = stage.bundle(destination, received["receipt_sha256"])
        self.assertEqual(receipt["git_sha"], "b" * 40)
        self.assertEqual(receipt["builder_git_sha"], stage.BUILDER_REVISION)
        self.assertEqual(files["nib"], b"fixture compiler output")
        self.assertEqual((builds[1]["GOOS"], builds[1]["GOARCH"]), ("linux", "amd64"))
        for env in builds:
            self.assertEqual((env["GOPROXY"], env["GOSUMDB"], env["GOWORK"], env["GOENV"]), ("off", "off", "off", "off"))
            self.assertEqual(env["GO_TELEMETRY_CHILD"], "2")
        self.assertEqual((destination / "nib").stat().st_mode & 0o777, 0o500)

    def test_builder_uses_only_pinned_git_bytes_not_checkout_or_replacements(self):
        repo = self.root / "source"
        repo.mkdir()
        env = {**stage.GIT_ENV, "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
               "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
        def git(*args, root=repo):
            return subprocess.run(["git", "-C", str(root), *args], env=env, capture_output=True, check=True, timeout=10).stdout
        git("init", "-q")
        for name, expected in stage.BUILDER_FILES.items():
            data = git("show", stage.BUILDER_REVISION + ":" + name, root=ROOT)
            self.assertEqual(stage.digest(data), expected)
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        git("add", ".")
        git("commit", "-qm", "reviewed helper fixture")
        revision = git("rev-parse", "HEAD").decode().strip()
        path = repo / stage.BUILDER
        path.write_bytes(b"raise RuntimeError('SYNTHETIC_PRIVATE_VALUE')\n")
        git("add", ".")
        git("commit", "-qm", "forged helper")
        forged = git("rev-parse", "HEAD").decode().strip()
        git("replace", revision, forged)
        ordinary = subprocess.run(["git", "-C", str(repo), "show", revision + ":" + stage.BUILDER],
                                  env={k: v for k, v in env.items() if k != "GIT_NO_REPLACE_OBJECTS"},
                                  capture_output=True, check=True, timeout=10).stdout
        self.assertIn(b"SYNTHETIC_PRIVATE_VALUE", ordinary)
        path.unlink()
        os.mkfifo(path)
        with mock.patch.object(stage, "ROOT", repo), mock.patch.object(stage, "BUILDER_REVISION", revision):
            builder = stage.load_builder()
        self.assertEqual(stage.digest(builder.BOOTSTRAP), stage.BUILDER_SHA256)
        self.assertFalse(Path(builder.__file__).exists())
        self.assertEqual(builder.GO_TOOLCHAIN, "go1.27.2")
        oid = git("rev-parse", revision + ":" + stage.BUILDER).decode().strip()
        (repo / ".git/objects" / oid[:2] / oid[2:]).unlink()
        marker, helper = repo / "transport-called", repo / "transport.py"
        helper.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()\nraise SystemExit(1)\n")
        git("config", "remote.origin.url", "ext::" + sys.executable + " " + str(helper))
        git("config", "remote.origin.promisor", "true")
        git("config", "remote.origin.partialclonefilter", "blob:none")
        git("config", "protocol.ext.allow", "always")
        for ignored in (None, "GIT_NO_LAZY_FETCH"):
            with mock.patch.object(stage, "ROOT", repo), mock.patch.object(stage, "BUILDER_REVISION", revision), \
                    mock.patch.object(stage, "GIT_ENV", {k: v for k, v in stage.GIT_ENV.items() if k != ignored}), \
                    self.assertRaises(ValueError):
                stage.load_builder()
            self.assertFalse(marker.exists())
        # Local positive control only; this helper never contacts a server.
        result = subprocess.run(["git", "-C", str(repo), "cat-file", "-s", oid],
                                env={k: v for k, v in env.items() if k not in ("GIT_NO_LAZY_FETCH", "GIT_ALLOW_PROTOCOL")},
                                capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(marker.exists())

    def test_builder_missing_or_tampered_history_never_loads_and_disables_transports(self):
        with mock.patch.object(stage, "BUILDER_REVISION", "0" * 40), self.assertRaises(ValueError):
            stage.load_builder()
        original = stage.execute
        seen = []
        def tampered(argv, timeout, **kwargs):
            env = kwargs["env"]
            seen.append(dict(env))
            for key, value in (("GIT_ALLOW_PROTOCOL", ""), ("GIT_NO_LAZY_FETCH", "1"),
                               ("GIT_NO_REPLACE_OBJECTS", "1"), ("GIT_TERMINAL_PROMPT", "0"),
                               ("GIT_CONFIG_NOSYSTEM", "1"), ("GIT_CONFIG_GLOBAL", os.devnull)):
                self.assertEqual(env[key], value)
            self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", str(env))
            code, data = original(argv, timeout, **kwargs)
            if argv[-3:-1] == ["cat-file", "blob"]:
                data += b"# SYNTHETIC_PRIVATE_VALUE\n"
            return code, data
        with mock.patch.dict(os.environ, {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "SYNTHETIC_PRIVATE_VALUE"}), \
                mock.patch.object(stage, "execute", side_effect=tampered), self.assertRaises(ValueError):
            stage.load_builder()
        self.assertTrue(seen)

    def test_published_records_use_authoritative_offline_schema(self):
        self.run_fixture()
        successful = (self.evidence / "run.json").read_bytes()
        self.evidence = self.root / "failed"
        self.run_fixture(disk={**self.disk, "sufficient": False, "available_bytes": 0})
        builder = stage.load_builder()
        work = self.root / "validator"
        work.mkdir()
        env = builder.offline_environment(work / "control")
        revision = builder.checker.git(ROOT, "rev-parse", "HEAD").decode().strip()
        binary, _ = builder.build_validator(ROOT, revision, work, env)
        for name, data in (("success", successful), ("fail", (self.evidence / "run.json").read_bytes())):
            target = work / "validation/evidence/p0" / name
            target.mkdir(parents=True)
            (target / "run.json").write_bytes(data)
        builder.call([str(binary), "lint", str(work / "validation")], env, timeout=20)


if __name__ == "__main__":
    unittest.main()
