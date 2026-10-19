# SPDX-License-Identifier: Apache-2.0
"""Filesystem and fake Docker lifecycle only; never execute an actual image."""

import base64
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
RECORDED = ROOT / "evidence/p0/2026-10-19-official-vllm-image-diagnostic"


def source(name, relative):
    module = types.ModuleType(name)
    module.__file__ = str(ROOT / relative)
    exec(compile(Path(module.__file__).read_bytes(), module.__file__, "exec"), module.__dict__)
    return module


recorder = source("image_recorder_test", "scripts/record-engine-image.py")
fixtures = source("image_wheel_test", "scripts/test_engine_overlay.py")
overlay, diagnostic = recorder.overlay, recorder.diagnostic


class FakeDocker:
    def __init__(self, image, report, fault=None):
        self.image, self.report, self.fault = image, report, fault
        self.exists, self.started, self.removed = False, False, False
        self.calls, self.info = [], None

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if args[1:3] == ["image", "inspect"]:
            data = self.image
        elif args[1:3] == ["container", "ls"]:
            if self.fault == "unknown-list" and self.exists:
                raise subprocess.TimeoutExpired(args, 1)
            return subprocess.CompletedProcess(args, 0, b"a" * 64 if self.exists else b"", b"")
        elif args[1:3] == ["container", "create"]:
            def flag(name):
                return args[args.index(name) + 1]
            env = [args[i + 1] for i, value in enumerate(args) if value == "--env"]
            self.exists = True
            self.info = {"Id": "a" * 64, "Image": self.image["Id"], "State": {"Status": "created"},
                "Config": {"Labels": {"nib.diagnostic": flag("--label").split("=", 1)[1]},
                    "Image": overlay.BASE_IMAGE, "Entrypoint": ["python3"], "Cmd": args[-5:], "User": "65532:65532",
                    "WorkingDir": "/", "Healthcheck": {"Test": ["NONE"]}, "Env": env},
                "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True, "Privileged": False,
                    "Runtime": "runc", "CapDrop": ["ALL"], "IpcMode": "none", "CgroupnsMode": "private",
                    "Memory": 1 << 30, "MemorySwap": 1 << 30, "NanoCpus": 2000000000, "PidsLimit": 32,
                    "RestartPolicy": {"Name": "no"}, "LogConfig": {"Type": "none"}, "SecurityOpt": ["no-new-privileges"]},
                "Mounts": [{"Type": "bind", "Source": flag("--mount").split(",src=", 1)[1].split(",dst=", 1)[0],
                            "Destination": "/opt/nib", "RW": False}]}
            if self.fault == "create-timeout":
                raise subprocess.TimeoutExpired(args, 1)
            return subprocess.CompletedProcess(args, 0, b"a" * 64 + b"\n", b"")
        elif args[1:3] == ["container", "inspect"]:
            data = copy.deepcopy(self.info)
            if self.fault == "foreign-owner":
                data["Config"]["Labels"]["nib.diagnostic"] = "someone-else"
            if self.fault == "network":
                data["HostConfig"]["NetworkMode"] = "host"
            if self.fault == "devices":
                data["HostConfig"]["DeviceRequests"] = [{"Count": -1}]
            if self.fault == "extra-env":
                data["Config"]["Env"].append("PYTHONPATH=SYNTHETIC_PRIVATE_VALUE")
        elif args[1:3] == ["container", "start"]:
            self.started = True
            if self.fault == "cancel":
                raise InterruptedError("SYNTHETIC_PRIVATE_VALUE")
            if self.fault == "run-timeout":
                raise subprocess.TimeoutExpired(args, 1)
            self.info["State"] = {"Status": "exited", "ExitCode": 0, "OOMKilled": False}
            if self.fault == "bad-result":
                return subprocess.CompletedProcess(args, 0, b"SYNTHETIC_PRIVATE_VALUE", b"")
            if self.fault == "overflow":
                return subprocess.CompletedProcess(args, 0, b"x" * (recorder.MAX_REPORT + 1), b"")
            if self.fault == "signal":
                return subprocess.CompletedProcess(args, -9, b"", b"")
            data = self.report
        elif args[1:3] == ["container", "rm"]:
            self.removed = True
            if self.fault != "still-present":
                self.exists = False
            return subprocess.CompletedProcess(args, 0, b"", b"")
        else:
            raise AssertionError("unexpected command")
        return subprocess.CompletedProcess(args, 0, overlay.json_bytes(data), b"")


class ImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.OverlayTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.OverlayTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.work, self.root = self.fixture.work, self.fixture.root
        self.contract = copy.deepcopy(self.fixture.contract)
        scripts = self.work / "bin"
        scripts.mkdir()
        data = b"#!/usr/bin/python3\n# synthetic only\n"
        (scripts / "vllm").write_bytes(data)
        self.fixture.write(self.fixture.dist + "/entry_points.txt", b"[console_scripts]\nvllm = vllm.entrypoints.cli.main:main\n")
        encoded = "sha256=" + base64.urlsafe_b64encode(bytes.fromhex(overlay.digest(data))).decode().rstrip("=")
        self.fixture.refresh_record([("../bin/vllm", encoded, len(data))])
        self.paths = {"purelib": str(self.root), "platlib": str(self.root), "scripts": str(scripts)}
        self.config = {"os": "linux", "architecture": "amd64", "config": {"Env": ["PATH=/public/image/path", "PYTHONPATH=bad"]},
                       "rootfs": {"type": "layers", "diff_ids": ["sha256:" + "b" * 64]}}
        self.image = {"Id": overlay.BASE_IMAGE.rsplit("@", 1)[1], "Os": "linux", "Architecture": "amd64",
                      "RepoDigests": [overlay.BASE_IMAGE], "RootFS": {"Type": "layers", "Layers": self.config["rootfs"]["diff_ids"]}}
        self.payload = self.work / "payload"
        recorder.prep.publish(self.payload, {"contract.json": overlay.json_bytes(self.contract)})

    @contextmanager
    def layout(self, others=()):
        with mock.patch.object(overlay.platform, "system", return_value="Linux"), \
                mock.patch.object(overlay.platform, "machine", return_value="x86_64"), \
                mock.patch.object(overlay.platform, "python_version", return_value="3.12.3"), \
                mock.patch.object(overlay.sys, "version_info", (3, 12)), \
                mock.patch.object(overlay.sysconfig, "get_paths", return_value=self.paths), \
                mock.patch.object(overlay.site, "getsitepackages", return_value=[str(self.root), *map(str, others)]):
            yield

    def collect(self, others=()):
        with self.layout(others):
            return diagnostic.collect(overlay, self.contract)

    def test_read_only_collection_preserves_admission_and_files(self):
        with overlay.directory(self.root) as fd:
            before = overlay.Tree(fd).inventory()
        with mock.patch.object(overlay.Tree, "write", side_effect=AssertionError("no writes")), \
                mock.patch.object(overlay.Tree, "unlink", side_effect=AssertionError("no writes")):
            report = self.collect()
        self.assertEqual(report["overlay_admission"], "accepted")
        self.assertEqual(report["checks"]["wheel_integrity"], "pass")
        self.assertEqual(report["candidate_status"], "BLOCKED")
        with overlay.directory(self.root) as fd:
            self.assertEqual(overlay.Tree(fd).inventory(), before)

    def test_hooks_and_populated_roots_remain_rejected_but_integrity_observable(self):
        self.fixture.write("distutils-precedence.pth", b"import forbidden_SENSITIVE_VALUE\n")
        other = self.work / "other"
        (other / "cryptography-41.0.7.dist-info").mkdir(parents=True)
        (other / "cryptography-41.0.7.dist-info/METADATA").write_text("Name: cryptography\nVersion: 41.0.7\n")
        report = self.collect([other])
        self.assertEqual(report["overlay_admission"], "rejected")
        self.assertEqual(report["checks"]["wheel_integrity"], "pass")
        self.assertEqual(report["checks"]["root_admission"], "rejected")
        hooks = [hook for root in report["roots"] for hook in root["hooks"]]
        self.assertEqual(hooks[0]["text"], None)
        self.assertNotIn("SENSITIVE_VALUE", json.dumps(report))
        with self.layout([other]), self.assertRaises(ValueError):
            overlay.installed_root()

    def test_unknown_or_forged_hook_content_never_published(self):
        report = self.collect()
        report["roots"][0]["hooks"] = [{"name": "distutils-precedence.pth", "sha256": "c" * 64,
                                        "text": "SYNTHETIC_PRIVATE_VALUE", "classification": "known-public-content"}]
        with self.assertRaises(ValueError):
            diagnostic.validate_report(overlay, report, self.contract)

    def test_known_hook_bytes_are_observed_not_executed(self):
        data = b"raise RuntimeError('must never execute')\n"
        self.fixture.write("distutils-precedence.pth", data)
        with mock.patch.dict(diagnostic.HOOKS, {"distutils-precedence.pth": overlay.digest(data)}):
            report = self.collect()
            hook = report["roots"][0]["hooks"][0]
            self.assertEqual(hook["text"], data.decode())
            self.assertEqual(report["overlay_admission"], "rejected")

    def test_record_rejection_and_source_mismatch_are_visible(self):
        self.fixture.write("vllm/envs.py", b"changed without updating RECORD\n")
        report = self.collect()
        self.assertEqual(report["checks"]["record_integrity"], "rejected")
        self.assertEqual(report["checks"]["wheel_integrity"], "rejected")
        self.assertFalse(report["source_files"][0]["matches_base"])

    def test_malformed_record_is_a_fixed_rejection_not_lost_observations(self):
        self.fixture.write(self.fixture.dist + "/RECORD", b'"SYNTHETIC_PRIVATE_VALUE')
        child = self.collect()
        self.assertEqual(child["checks"]["record_integrity"], "rejected")
        self.assertEqual(child["checks"]["wheel_integrity"], "rejected")
        self.assertTrue(child["roots"][0]["packages"])
        self.assertEqual(len(child["source_files"]), len(self.contract["files"]))
        self.assertEqual(child["overlay_admission"], "rejected")
        self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(child))

    def test_inventory_failure_retains_observations_and_fails_run(self):
        self.fixture.write("distutils-precedence.pth", b"import must_never_execute\n")
        for failure in (ValueError("SYNTHETIC_PRIVATE_VALUE"), TimeoutError("SYNTHETIC_PRIVATE_VALUE")):
            with self.subTest(failure=type(failure).__name__), \
                    mock.patch.object(overlay.Tree, "inventory", side_effect=failure):
                child = self.collect()
            self.assertEqual(child["collection"], "partial")
            self.assertEqual(child["checks"]["filesystem_inventory"], "rejected")
            self.assertEqual(child["checks"]["wheel_integrity"], "unavailable")
            self.assertEqual(child["checks"]["record_integrity"], "unavailable")
            self.assertEqual(child["checks"]["selected_root_admission"], "unavailable")
            self.assertTrue(child["roots"][0]["packages"])
            self.assertEqual(child["roots"][0]["hooks"][0]["name"], "distutils-precedence.pth")
            self.assertTrue(all(item["matches_base"] for item in child["source_files"]))
            fake = FakeDocker(self.image, child)
            report = recorder.run_container(self.payload, self.contract, self.config, {}, fake)
            self.assertEqual(report["diagnostic"], child)
            self.assertEqual(report["result"], "fail")
            self.assertEqual(report["cleanup"], "confirmed")
            self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(report))
            child["collection"] = "pass"
            with self.assertRaises(ValueError):
                diagnostic.validate_report(overlay, child, self.contract)

    def test_unsupported_inventory_entries_preserve_prior_hooks(self):
        self.fixture.write("unknown.pth", b"raise AssertionError('must not execute')\n")
        path = self.root / "unrelated.so"
        for kind in ("symlink", "fifo", "native"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    path.symlink_to("missing")
                elif kind == "fifo":
                    os.mkfifo(path)
                else:
                    path.write_bytes(b"not an ELF library")
                try:
                    child = self.collect()
                finally:
                    path.unlink()
                self.assertEqual(child["collection"], "partial")
                self.assertEqual(child["checks"]["filesystem_inventory"], "rejected")
                self.assertTrue(child["roots"][0]["hooks"])
                self.assertEqual(len(child["source_files"]), len(self.contract["files"]))

    def test_malformed_metadata_and_inaccessible_roots_preserve_other_observations(self):
        self.fixture.write("aaa-1.dist-info/METADATA", b"Name: SYNTHETIC_PRIVATE_VALUE\n")
        self.fixture.write("distutils-precedence.pth", b"import must_never_execute\n")
        other = self.work / "other"
        other.symlink_to("absent")
        child = self.collect([other])
        self.assertEqual(child["collection"], "partial")
        self.assertEqual(child["overlay_admission"], "rejected")
        self.assertEqual(child["roots"][0]["directory_inventory"], "rejected")
        self.assertEqual(child["roots"][1]["metadata_inventory"], "rejected")
        self.assertTrue(child["roots"][1]["packages"])
        self.assertTrue(child["roots"][1]["hooks"])
        self.assertEqual(child["checks"]["wheel_integrity"], "rejected")
        self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(child))

    def test_unreadable_source_never_counts_as_confirmed_absence(self):
        helper = next(item for item in self.contract["files"] if item["sha256"][0] is None)
        path = self.root / helper["path"]
        os.mkfifo(path)
        child = self.collect()
        item = next(item for item in child["source_files"] if item["path"] == helper["path"])
        self.assertEqual(item["status"], "rejected")
        self.assertFalse(item["matches_base"])
        self.assertIsNone(item["sha256"])
        self.assertEqual(child["collection"], "partial")
        item["matches_base"] = True
        with self.assertRaises(ValueError):
            diagnostic.validate_report(overlay, child, self.contract)

    def test_distro_egg_metadata_is_observed_without_acceptance(self):
        other = self.work / "other"
        (other / "PyJWT-2.7.0.egg-info").mkdir(parents=True)
        (other / "PyJWT-2.7.0.egg-info/PKG-INFO").write_bytes(b"Name: PyJWT\nVersion: 2.7.0\n")
        report = self.collect([other])
        self.assertIn({"name": "PyJWT", "version": "2.7.0"}, report["roots"][0]["packages"])
        self.assertEqual(report["overlay_admission"], "rejected")

    def test_raw_oci_exact_bytes_and_id_not_config_hash(self):
        metadata = self.work / "metadata"
        metadata.mkdir()
        config_data = overlay.json_bytes(self.config)
        manifest = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "config": {"digest": "sha256:" + overlay.digest(config_data), "size": len(config_data),
                               "mediaType": "application/vnd.oci.image.config.v1+json"}, "layers": [{}]}
        manifest_data = overlay.json_bytes(manifest)
        pins = {"base_image": "vllm/vllm-openai@sha256:" + overlay.digest(manifest_data),
                "official_config": {"sha256": overlay.digest(config_data), "bytes": len(config_data)}}
        (metadata / "manifest.json").write_bytes(manifest_data)
        (metadata / "config.json").write_bytes(config_data)
        self.assertEqual(recorder.raw_metadata(metadata, pins), self.config)
        bound = recorder.image_binding(self.image, self.config, self.contract)
        self.assertNotEqual(bound["docker_reported_id"], "sha256:" + bound["raw_config_sha256"])
        self.assertFalse(bound["local_config_bytes_rehashed"])
        self.image["RepoDigests"] = []
        with self.assertRaises(ValueError):
            recorder.image_binding(self.image, self.config, self.contract)
        (metadata / "config.json").write_bytes(config_data + b" ")
        with self.assertRaises(ValueError):
            recorder.raw_metadata(metadata, pins)

    def test_valid_lifecycle_retains_cached_image_and_reports_rejection(self):
        self.fixture.write("unknown.pth", b"import must_not_execute\n")
        fake = FakeDocker(self.image, self.collect())
        report = recorder.run_container(self.payload, self.contract, self.config, {}, fake)
        self.assertTrue(fake.started)
        self.assertTrue(fake.removed)
        self.assertEqual(report["stage"], "overlay-rejected")
        self.assertEqual(report["cleanup"], "confirmed")
        self.assertEqual(report["result"], "fail")
        self.assertFalse(any(call[1:3] in (["image", "rm"], ["image", "pull"]) for call in fake.calls))

    def test_lifecycle_faults_fail_closed_without_raw_output(self):
        child = self.collect()
        for fault in ("create-timeout", "network", "devices", "extra-env", "foreign-owner", "run-timeout",
                      "bad-result", "overflow", "signal", "cancel", "still-present", "unknown-list"):
            with self.subTest(fault=fault):
                fake = FakeDocker(self.image, child, fault)
                report = recorder.run_container(self.payload, self.contract, self.config, {}, fake)
                self.assertEqual(report["result"], "fail")
                self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(report))
                if fault in {"network", "devices", "extra-env", "foreign-owner", "create-timeout"}:
                    self.assertFalse(fake.started)
                if fault == "foreign-owner":
                    self.assertFalse(fake.removed)
                if fault in {"create-timeout", "run-timeout", "bad-result", "overflow", "signal", "cancel"}:
                    self.assertEqual(report["cleanup"], "completion-unknown")

    def test_unbound_local_image_never_creates_container(self):
        self.image["RepoDigests"] = []
        fake = FakeDocker(self.image, self.collect())
        report = recorder.run_container(self.payload, self.contract, self.config, {}, fake)
        self.assertEqual(report["result"], "fail")
        self.assertFalse(any(call[1:3] == ["container", "create"] for call in fake.calls))

    def test_publication_requires_schema_validation_and_never_overwrites(self):
        destination = self.work / "evidence"
        report, record = {"result": "fail"}, {"schema": "nib.evidence/v1"}
        with mock.patch.object(recorder, "call", side_effect=ValueError("schema failed")):
            with self.assertRaises(ValueError):
                recorder.publish_evidence(record, report, self.work / "nib", self.work, destination, {})
        self.assertFalse(destination.exists())
        validation = self.work / "validation/evidence/p0/candidate"
        (validation / "run.json").unlink()
        validation.rmdir()
        with mock.patch.object(recorder, "call", return_value=b"") as validate:
            recorder.publish_evidence(record, report, self.work / "nib", self.work, destination, {})
            self.assertEqual(validate.call_args.args[0][1], "lint")
        self.assertEqual((destination / "run.json").stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            recorder.prep.publish(destination, {"run.json": b"changed"})

    def test_source_binding_uses_committed_bytes(self):
        repo = self.work / "repo"
        repo.mkdir()
        extra = {name: (ROOT / name).read_bytes() for name in (
            recorder.prep.SCRIPT, recorder.prep.INSPECTOR, recorder.prep.PINS, recorder.prep.TEST,
            recorder.SCRIPT, recorder.PROBE, recorder.DIAGNOSTIC, recorder.SNAPSHOT, recorder.TEST)}
        for name, data in {**self.fixture.bundle, **extra}.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        env = {**recorder.checker.tool_env(), "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
               "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
        def git(*args):
            return subprocess.run(["git", "-C", str(repo), *args], env=env, capture_output=True, check=True, timeout=10)
        git("init", "-q")
        git("add", ".")
        git("commit", "-qm", "synthetic diagnostic source")
        result = recorder.inputs(repo)
        (repo / recorder.PROBE).write_bytes(b"uncommitted source must not be used")
        (repo / "scripts/ignored.pyc").write_bytes(b"ignored bytecode must not execute")
        self.assertEqual(recorder.inputs(repo), result)
        with mock.patch.object(recorder, "BOOTSTRAP", b"changed"), self.assertRaises(ValueError):
            recorder.inputs(repo)

    def test_bootstrap_fifo_and_forbidden_cli_are_bounded_and_quiet(self):
        fifo = self.work / "SYNTHETIC_PRIVATE_VALUE"
        os.mkfifo(fifo)
        with self.assertRaises(ValueError):
            recorder.bootstrap_read(fifo)
        completed = subprocess.run([sys.executable, "-I", "-S", str(ROOT / recorder.SCRIPT),
                                    "--image", "SYNTHETIC_PRIVATE_VALUE"], capture_output=True, timeout=5)
        self.assertEqual(completed.returncode, 1)
        self.assertNotIn(b"SYNTHETIC_PRIVATE_VALUE", completed.stdout + completed.stderr)

    def test_repeat_cancellation_allows_bounded_cleanup(self):
        with mock.patch.object(recorder, "CANCELLED", False):
            with self.assertRaises(InterruptedError):
                recorder.cancel(None, None)
            recorder.cancel(None, None)

    def test_offline_environment_pins_toolchain_and_inherits_only_caches(self):
        control = self.work / "control"
        ambient = {"GOTOOLCHAIN": "local", "GOENV": "SYNTHETIC_PRIVATE_VALUE",
                   "GOWORK": "SYNTHETIC_PRIVATE_VALUE", "GOFLAGS": "-overlay=SYNTHETIC_PRIVATE_VALUE",
                   "GOPROXY": "https://example.invalid", "GOSUMDB": "SYNTHETIC_PRIVATE_VALUE",
                   "CGO_ENABLED": "1", "GOOS": "SYNTHETIC_PRIVATE_VALUE", "PATH": "SYNTHETIC_PRIVATE_VALUE",
                   "HTTP_PROXY": "SYNTHETIC_PRIVATE_VALUE", "GOCACHE": str(self.work / "existing-cache"),
                   "GOMODCACHE": str(self.work / "existing-modules"), "TEST_TELEMETRY_DIR": "SYNTHETIC_PRIVATE_VALUE",
                   "GO_TELEMETRY_CHILD": "1", "GO_TELEMETRY_CHILD_UPLOAD": "1"}
        with mock.patch.dict(os.environ, ambient, clear=True):
            env = recorder.offline_environment(control)
        for key, value in {"GOTOOLCHAIN": "go1.27.2", "GOENV": "off", "GOWORK": "off",
                           "GOFLAGS": "-mod=readonly", "CGO_ENABLED": "0", "GOPROXY": "off", "GOSUMDB": "off",
                           "TEST_TELEMETRY_DIR": str(control / "telemetry"), "GO_TELEMETRY_CHILD": "2",
                           "PATH": recorder.checker.tool_env()["PATH"], "HOME": str(control / "home"),
                           "GOCACHE": ambient["GOCACHE"], "GOMODCACHE": ambient["GOMODCACHE"]}.items():
            self.assertEqual(env[key], value)
        self.assertNotIn("GOOS", env)
        self.assertNotIn("HTTP_PROXY", env)
        self.assertNotIn("GO_TELEMETRY_CHILD_UPLOAD", env)
        self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(env))
        self.assertEqual((control / "telemetry/mode").read_bytes(), b"off\n")
        self.assertEqual((control / "telemetry").stat().st_mode & 0o777, 0o700)
        self.assertEqual((control / "telemetry/mode").stat().st_mode & 0o777, 0o600)
        control = self.work / "default-control"
        with mock.patch.dict(os.environ, {}, clear=True):
            defaults = recorder.offline_environment(control)
        self.assertEqual(defaults["GOCACHE"], str(control / "go-cache"))
        self.assertEqual(defaults["GOMODCACHE"], str(control / "go-mod"))

    def test_generated_records_use_authoritative_offline_schema(self):
        revision = recorder.checker.git(ROOT, "rev-parse", "HEAD").decode().strip()
        env = recorder.offline_environment(self.work / "control")
        binary, source_hash = recorder.build_validator(ROOT, revision, self.work, env)
        self.assertRegex(source_hash, r"^[0-9a-f]{64}$")
        recorded = self.work / "recorded/evidence/p0/official-image"
        recorded.mkdir(parents=True)
        (recorded / "run.json").write_bytes((RECORDED / "run.json").read_bytes())
        recorder.call([str(binary), "lint", str(self.work / "recorded")], env, timeout=20)
        for result in ("pass", "fail"):
            work = self.work / result
            work.mkdir()
            report = {"result": result, "provenance": {"payload_sha256": "a" * 64}}
            record = recorder.evidence_record(revision, "fixture", recorder.now(), report)
            destination = work / "published"
            recorder.publish_evidence(record, report, binary, work, destination, env)
            self.assertEqual(json.loads((destination / "run.json").read_bytes())["execution"], "software")
        rejected = self.work / "rejected"
        rejected.mkdir()
        record["hardware_validated"] = True
        with self.assertRaises(subprocess.CalledProcessError):
            recorder.publish_evidence(record, report, binary, rejected, rejected / "published", env)
        self.assertFalse((rejected / "published").exists())

    def test_recorded_partial_diagnostic_preserves_failure_and_contract(self):
        expected = {"run.json": "f541746a194270e0d2d312860b27fe8cec8fff5b8ffb341ae91bf5621b609e96",
                    "output.log": "d7b4bced18c59da0da9b7d4ca0175062e1e34c95139832e99790458cf74cb0c9"}
        for name, digest in expected.items():
            self.assertEqual(overlay.digest((RECORDED / name).read_bytes()), digest)
        run = overlay.load_json((RECORDED / "run.json").read_bytes())
        report = overlay.load_json((RECORDED / "output.log").read_bytes())
        diagnostic.validate_report(overlay, report["diagnostic"], self.contract)
        self.assertEqual(run["git_sha"], "694394cfe631ab6d09cd5695087a70351f203fae")
        self.assertEqual(report["provenance"]["git_sha"], run["git_sha"])
        self.assertEqual(run["execution"], "software")
        self.assertFalse(run["hardware_validated"])
        self.assertEqual(run["result"], report["result"])
        self.assertEqual(report["result"], "fail")
        self.assertEqual(report["cleanup"], "confirmed")
        self.assertEqual(report["diagnostic"]["collection"], "partial")
        self.assertEqual(report["diagnostic"]["checks"]["filesystem_inventory"], "rejected")
        self.assertEqual(report["diagnostic"]["checks"]["wheel_integrity"], "unavailable")
        self.assertEqual(report["diagnostic"]["checks"]["record_integrity"], "unavailable")
        self.assertEqual([root["entries"] for root in report["diagnostic"]["roots"]], [42, 516])
        sources = report["diagnostic"]["source_files"]
        self.assertEqual(sum(item["status"] == "read" for item in sources), 12)
        self.assertEqual(sum(item["status"] == "absent" for item in sources), 1)
        self.assertTrue(all(item["matches_base"] for item in sources))

    def test_validator_toolchain_resolution_is_cache_only_and_exact(self):
        env = recorder.offline_environment(self.work / "control")
        info = {"GOVERSION": "go1.24.2", "GOHOSTOS": "darwin", "GOHOSTARCH": "arm64",
                "GOMODCACHE": str(self.work / "modules"), "GOCACHE": str(self.work / "compile-cache")}
        binary = str(self.work / "modules/golang.org/toolchain@v0.0.1-go1.27.2.darwin-arm64/bin/go")
        selection = mock.patch.object(recorder.shutil, "which", return_value="/host/toolchain/bin/go")
        selection.start()
        self.addCleanup(selection.stop)
        with mock.patch.object(recorder, "call", side_effect=[overlay.json_bytes(info), b"go version go1.27.2 darwin/arm64\n"]) as calls:
            selected, qualified = recorder.validator_toolchain(env)
            self.assertEqual(selected, binary)
            self.assertEqual(qualified["GOMODCACHE"], info["GOMODCACHE"])
            self.assertEqual(qualified["GOCACHE"], info["GOCACHE"])
            self.assertEqual(qualified["GOTOOLCHAIN"], "go1.27.2")
        self.assertEqual(calls.call_args_list[0].args[0][0], "/host/toolchain/bin/go")
        self.assertEqual(calls.call_args_list[1].args[0], [binary, "version"])
        for invocation in calls.call_args_list:
            local = invocation.args[1]
            self.assertEqual(local["GOTOOLCHAIN"], "local")
            self.assertEqual(local["GOPROXY"], "off")
            self.assertEqual(local["GOSUMDB"], "off")
            self.assertEqual(local["TEST_TELEMETRY_DIR"], env["TEST_TELEMETRY_DIR"])
            self.assertEqual(local["GO_TELEMETRY_CHILD"], "2")
        self.assertEqual(env["GOTOOLCHAIN"], "go1.27.2")
        with mock.patch.object(recorder, "call", side_effect=[overlay.json_bytes(info), b"go version go1.24.2 darwin/arm64\n"]):
            with self.assertRaises(ValueError):
                recorder.validator_toolchain(env)
        with mock.patch.object(recorder, "call", side_effect=[overlay.json_bytes(info), FileNotFoundError()]):
            with self.assertRaises(FileNotFoundError):
                recorder.validator_toolchain(env)
        info["GOVERSION"] = "go1.27.2"
        with mock.patch.object(recorder, "call", return_value=overlay.json_bytes(info)) as calls:
            self.assertEqual(recorder.validator_toolchain(env)[0], "/host/toolchain/bin/go")
            self.assertEqual(calls.call_count, 1)

    def test_hosted_go_outside_fixed_path_discovers_default_caches(self):
        hosted = self.work / "hosted-toolchain/bin"
        hosted.mkdir(parents=True)
        binary = hosted / "go"
        info = {"GOVERSION": "go1.27.2", "GOHOSTOS": "linux", "GOHOSTARCH": "amd64",
                "GOMODCACHE": str(self.work / "existing-modules"), "GOCACHE": str(self.work / "existing-cache")}
        # Execute a real filesystem fixture, not the engine or a fake validator.
        # Only discovery is synthetic; the authoritative schema test builds Go.
        binary.write_text("#!/bin/sh\nset -eu\n"
            'test "$GOTOOLCHAIN" = local\ntest "$GOENV" = off\ntest "$GOWORK" = off\n'
            'test "$GOPROXY" = off\ntest "$GOSUMDB" = off\n'
            'test "$GO_TELEMETRY_CHILD" = 2\nIFS= read -r mode < "$TEST_TELEMETRY_DIR/mode"\ntest "$mode" = off\n'
            'test -z "${GOPATH+x}${GOMODCACHE+x}${GOCACHE+x}"\n'
            'test "$1" = env\ntest "$2" = -json\n'
            "printf '%s\\n' '" + json.dumps(info) + "'\n")
        binary.chmod(0o700)
        with mock.patch.dict(os.environ, {"PATH": str(hosted), "GOENV": "SYNTHETIC_PRIVATE_VALUE",
                                          "GOPATH": "SYNTHETIC_PRIVATE_VALUE"}, clear=True):
            env = recorder.offline_environment(self.work / "control")
            self.assertNotIn(str(hosted), env["PATH"])
            selected, qualified = recorder.validator_toolchain(env)
        self.assertEqual(selected, str(binary))
        self.assertEqual(qualified["GOMODCACHE"], info["GOMODCACHE"])
        self.assertEqual(qualified["GOCACHE"], info["GOCACHE"])
        self.assertEqual(qualified["HOME"], env["HOME"])
        self.assertEqual(qualified["GOTOOLCHAIN"], "go1.27.2")

    def test_real_go_ignores_account_telemetry_and_preserves_default_cache_discovery(self):
        env = recorder.offline_environment(self.work / "control")
        _, baseline = recorder.validator_toolchain(env)
        account = self.work / "account"
        config = account / ("Library/Application Support" if sys.platform == "darwin" else ".config")
        mode = config / "go/telemetry/mode"
        mode.parent.mkdir(parents=True)
        mode.write_bytes(b"on 2000-01-01\n")
        modules = account / "go/pkg/mod"
        cache = account / ("Library/Caches/go-build" if sys.platform == "darwin" else ".cache/go-build")
        for link, target in ((modules, baseline["GOMODCACHE"]), (cache, baseline["GOCACHE"])):
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target, target_is_directory=True)

        def account_snapshot():
            result = {}
            for path in account.rglob("*"):
                info = path.lstat()
                content = os.readlink(path) if path.is_symlink() else path.read_bytes() if path.is_file() else None
                result[path.relative_to(account).as_posix()] = (info.st_mode, info.st_mtime_ns, content)
            return result

        before = account_snapshot()
        # Real discovery/version calls; only the account identity is synthetic.
        # Both cache environment keys are absent, as on hosted CI.
        with mock.patch.object(recorder.pwd, "getpwuid", return_value=types.SimpleNamespace(pw_dir=str(account))), \
                mock.patch.dict(os.environ, {"PATH": os.environ["PATH"]}, clear=True):
            selected, qualified = recorder.validator_toolchain(env)
        self.assertEqual(qualified["GOMODCACHE"], str(modules))
        self.assertEqual(qualified["GOCACHE"], str(cache))
        self.assertEqual(qualified["GO_TELEMETRY_CHILD"], "2")
        recorder.call([selected, "version"], qualified, timeout=10, limit=4096)
        telemetry = Path(env["TEST_TELEMETRY_DIR"])
        self.assertEqual({path.name for path in telemetry.iterdir()}, {"mode"})
        self.assertEqual((telemetry / "mode").read_bytes(), b"off\n")
        self.assertEqual(account_snapshot(), before)

        # Positive counter control, confined to our temporary directory. Keep
        # CHILD=2 so even this telemetry-on control cannot fork or upload.
        positive = self.work / "positive-telemetry"
        recorder.prep.publish(positive, {"mode": b"on 2000-01-01\n"})
        control = {**qualified, "GOTOOLCHAIN": "local", "HOME": str(account),
                   "TEST_TELEMETRY_DIR": str(positive), "GO_TELEMETRY_CHILD": "2"}
        recorder.call([selected, "version"], control, timeout=10, limit=4096)
        self.assertTrue(list((positive / "local").glob("*.count")))
        self.assertEqual(account_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
