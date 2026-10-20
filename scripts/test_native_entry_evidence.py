# SPDX-License-Identifier: Apache-2.0
"""Reconstruct the native failure's producer without Docker or upstream execution."""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

import test_engine_cpu_entry as cpu_tests

ROOT = Path(__file__).resolve().parents[1]
REVISION = "badaab20717e9c8f14f45f0821a87f9d50bc6b9a"
DIRECTORY = ROOT / "evidence/p2/2026-10-20-cpu-native-39462843995-1"
RECORDER_SHA = "418a78ef884dfcc1ff6d8232c20e4e0293161d0641b0f48b09ae2f75d8f0f6b1"


@contextmanager
def producer_payload():
    names = (
        "scripts/record-engine-cpu-entry.py", "scripts/engine_cpu_entry.py", "scripts/engine_lab.py",
        "scripts/engine_lab_bootstrap.py", "scripts/record-engine-import-smoke.py",
        "scripts/prepare-engine-application.py", "scripts/engine_application.py",
        "scripts/engine_python_bootstrap.py", "scripts/engine_import_smoke.py",
        "scripts/record-engine-image.py", "scripts/prepare-engine-overlay.py",
        "scripts/engine_overlay.py", "scripts/check-engine-candidate.py",
        "scripts/e2e/snapshot.py", "scripts/engine_image_diagnostic.py",
        "scripts/engine_python_descriptor.py",
    )
    git = cpu_tests.tool.checker.git
    with tempfile.TemporaryDirectory() as temporary:
        source_root = Path(temporary).resolve()
        for name in names:
            raw = git(ROOT, "show", REVISION + ":" + name)
            if len(raw) > 2 << 20:
                raise ValueError("historical helper exceeds bound")
            path = source_root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        source = (source_root / names[0]).read_bytes()
        if hashlib.sha256(source).hexdigest() != RECORDER_SHA:
            raise ValueError("historical recorder differs")
        historical = types.ModuleType("native_failure_producer")
        historical.__file__ = str(source_root / names[0])
        exec(compile(source, historical.__file__, "exec"), historical.__dict__)
        original_git = historical.checker.git

        def source_git(repo, *args, **kwargs):
            if args == ("rev-parse", "--verify", "HEAD^{commit}"):
                return (REVISION + "\n").encode()
            return original_git(repo, *args, **kwargs)

        with mock.patch.object(historical.checker, "git", side_effect=source_git):
            files, contract = historical.prepare_files(*historical.inputs(ROOT))
        validator = {}
        entries = original_git(ROOT, "ls-tree", "-r", "-z", REVISION, "--",
                               "go.mod", "go.sum", "cmd/nib", "internal", "schemas")
        for entry in entries.split(b"\0"):
            if not entry:
                continue
            meta, name = entry.decode().split("\t")
            mode, kind, oid = meta.split()
            if mode not in ("100644", "100755") or kind != "blob":
                raise ValueError("historical validator entry differs")
            if int(original_git(ROOT, "cat-file", "-s", oid)) > historical.checker.LIMIT:
                raise ValueError("historical validator exceeds bound")
            validator[name] = original_git(ROOT, "cat-file", "blob", oid)
        oci = source_root / "oci"
        oci.mkdir()
        for name in ("manifest.json", "config.json"):
            (oci / name).write_bytes(original_git(ROOT, "show", REVISION + ":bench/engine-cpu-entry/oci/" + name))
        historical.recorder.raw_metadata(oci, contract)
        yield historical, files, contract, historical.overlay.bundle_digest(validator)


class NativeFailureEvidenceTests(unittest.TestCase):
    def test_original_native_failure_is_source_bound_without_entry_or_cleanup_success(self):
        documents = []
        for name, digest in (
            ("run.json", "9a267a1c656619ba3387362b4dfe9314dbb468abe5c7baac725e8faa104e49ea"),
            ("output.log", "f579dc91dce55ad4f0cb01cd00bc7bef05dc1c1a45fbe872fe3ea0a86ad1cf0c"),
        ):
            raw = (DIRECTORY / name).read_bytes()
            self.assertLessEqual(len(raw), 65536)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            documents.append(json.loads(raw))
        run, report = documents
        self.assertEqual((run["started_utc"], run["ended_utc"]),
                         ("2026-10-20T17:10:14Z", "2026-10-20T17:10:32Z"))
        self.assertEqual(run["host"], {"provider": "github-actions", "machine": "linux-amd64-software-runner",
                                      "gpus": "none", "driver": None, "cuda": None})
        self.assertEqual((report["stage"], report["cleanup"], report["failure"]),
                         ("application-and-entry", "completion-unknown", "cleanup-unresolved"))
        self.assertNotIn("experiment", report)
        self.assertEqual(report["candidate_status"], "BLOCKED")
        for name in ("entry_pass", "confidential", "deployable", "inference_started", "hardware_validated", "abi_validated"):
            self.assertIs(report[name], False)
        self.assertEqual((run["result"], report["result"], run["execution"], report["execution"]),
                         ("fail", "fail", "software", "software"))
        with producer_payload() as (historical, files, contract, validator_sha):
            overlay = historical.overlay
            payload = overlay.bundle_digest(files)
            self.assertEqual(payload, "f041cf7acb3280670534cb8d9d85d12b14f986c9cf2a417181439c570e43bcd0")
            self.assertEqual(report["provenance"], {
                "git_sha": REVISION, "source_bundle_sha256": contract["source_bundle_sha256"],
                "payload_sha256": payload, "requested_image": overlay.BASE_IMAGE,
                "expected_config_sha256": contract["official_config"]["sha256"],
                "measurement_sha256": contract["measurement_sha256"], "raw_oci_metadata_verified": True,
                "validator_source_sha256": validator_sha,
            })
            self.assertEqual(validator_sha, "0705178bc3a5cd9fb0397924798e58b44f92e9cc6c7e6a0f553bc39ea3724ee1")
            self.assertEqual(report["image"], {
                "binding": "local-manifest-reference-platform-and-diffids",
                "daemon_os_interpreter_trusted": True,
                "docker_reported_id": "sha256:" + contract["official_config"]["sha256"],
                "local_config_bytes_rehashed": False, "local_layers_rehashed": False,
                "manifest_sha256": overlay.BASE_IMAGE.split("@sha256:")[1], "platform": "linux/amd64",
                "raw_config_link_verified": True, "raw_config_sha256": contract["official_config"]["sha256"],
            })
            self.assertEqual(set(report), set(historical.report_start()) | {"image", "provenance", "failure"})
            with mock.patch.object(historical.recorder, "now", return_value=run["ended_utc"]):
                self.assertEqual(run, historical.evidence_record(
                    REVISION, "cpu-native-39462843995-1", run["started_utc"], report, "github-actions"))
            for changes in ({}, {"cleanup": "confirmed"}, {"entry_pass": True}):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    historical.check_evidence(run, {**report, **changes}, REVISION,
                        "cpu-native-39462843995-1", files, contract, "github-actions", validator_sha)
            workflow = historical.checker.git(ROOT, "show", REVISION + ":.github/workflows/engine-cpu-entry.yml")
            self.assertEqual(hashlib.sha256(workflow).hexdigest(),
                             "c826afbb695657b00031ef3c78b5959dda05615e3987e91cc34394f960421772")


if __name__ == "__main__":
    unittest.main()
