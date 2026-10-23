# SPDX-License-Identifier: Apache-2.0
"""Synthetic local files only; never invokes the native launcher or a workflow."""
import copy
import json
import os
from pathlib import Path
import stat
import shlex
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import yaml
from test_mkosi_build import load, ROOT
import test_mkosi_export as fixtures

validator = load("host/validate-native-boot-export.py")
base, export, publication = fixtures.base, fixtures.export, fixtures.publication


def recorded(report):
    value = copy.deepcopy(report)
    value["failure_kind"] = None
    value["worker"].update(installer_exit_code=0, failure_kind=None, package_installation_executed=True,
                           reproducibility_verified=False)
    return value


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ExportTests()
        self.validator_source = {validator.SCRIPT: "cd" * 32, validator.WORKFLOW: "ef" * 32}

    def run_validation(self, evidence, source, outcome="success"):
        return validator.validate(evidence, evidence.parent / "export-validation", source,
                                  self.validator_source, outcome, export, publication)

    def bundle(self, evidence, stage, report, source):
        self.fixture.prepare(evidence, stage, report)
        return export.finish(evidence, base, publication, source)

    def test_success_rehashes_exact_files_removes_images_then_exposes_only_json(self):
        with self.fixture.fixture() as (evidence, stage, raws, report, source):
            bundle = self.bundle(evidence, stage, report, source)
            original = {name: (bundle / name).read_bytes() for name in (*validator.RECEIPTS, "manifest.json")}
            publish = validator.base.publish
            def after_cleanup(destination, files):
                self.assertFalse(any(os.path.lexists(p) for p in export.paths(evidence)))
                self.assertTrue(set(files) <= set(original) | {"validation.json"})
                publish(destination, files)
            with mock.patch.object(validator.base, "publish", side_effect=after_cleanup), \
                    mock.patch.object(export, "transfer", wraps=export.transfer) as rehash:
                self.assertEqual(self.run_validation(evidence, source), "pass")
            self.assertEqual([call.args[0].name for call in rehash.call_args_list], list(export.FILES))
            metadata = evidence.parent / "export-validation"
            self.assertEqual({p.name for p in metadata.iterdir()}, set(original) | {"validation.json"})
            for name, raw in original.items():
                self.assertEqual((metadata / name).read_bytes(), raw)
                self.assertEqual(stat.S_IMODE((metadata / name).stat().st_mode), 0o600)
            result = json.loads((metadata / "validation.json").read_bytes())
            self.assertEqual(result["source"], source)
            self.assertEqual(result["validator_source"], self.validator_source)
            self.assertIs(result["private_images_removed"], True)
            self.assertIs(result["artifacts_verified"], True)
            for name in ("signed", "boot_verified", "reproducibility_verified", "confidentiality_verified"):
                self.assertIs(result[name], False)

    def test_rejection_deletes_private_images_and_only_writes_fixed_failure_metadata(self):
        mutations = ("image", "image-link", "extra", "source", "manifest", "receipt", "evidence",
                     "resource-fail", "pending", "missing", "outcome", "timeout")
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.fixture.fixture() as (evidence, stage, _, report, source):
                bundle = self.bundle(evidence, stage, report, source)
                supplied = copy.deepcopy(source)
                if mutation == "image": (bundle / "root.raw").write_bytes(b"PRIVATE-MUTATION")
                elif mutation == "image-link":
                    (bundle / "root.raw").unlink()
                    (bundle / "root.raw").symlink_to(stage / "root.raw")
                elif mutation == "extra": (bundle / "extra").write_bytes(b"PRIVATE-MUTATION")
                elif mutation == "source": supplied["git_sha"] = "ef" * 20
                elif mutation == "manifest": (bundle / "manifest.json").write_bytes(b'{"local_only": 1}')
                elif mutation == "receipt": (bundle / "result.json").write_bytes(b"{}")
                elif mutation == "evidence": (evidence / "run.json").write_bytes(b"{}")
                elif mutation == "resource-fail":
                    path = bundle / "resources.json"
                    data = json.loads(path.read_bytes())
                    data["result"] = "fail"
                    path.write_bytes(export.encoded(data))
                elif mutation == "pending": export.paths(evidence)[0].mkdir(mode=0o700)
                elif mutation == "outcome":
                    (evidence / "result.json").write_bytes(export.encoded(recorded(report)))
                elif mutation == "missing":
                    fd = publication.open_parent(bundle)
                    try:
                        base.inputs.remove_flat_directory(fd, bundle.name, bundle.stat())
                    finally:
                        os.close(fd)
                with mock.patch.object(export, "transfer", side_effect=ValueError("PRIVATE")
                                       if mutation == "timeout" else export.transfer):
                    self.assertEqual(self.run_validation(evidence, supplied,
                                     "failure" if mutation == "outcome" else "success"), "fail")
                self.assertFalse(any(os.path.lexists(p) for p in export.paths(evidence)))
                metadata = evidence.parent / "export-validation"
                self.assertEqual({p.name for p in metadata.iterdir()}, {"validation.json"})
                raw = (metadata / "validation.json").read_bytes()
                self.assertNotIn(b"PRIVATE", raw)
                result = json.loads(raw)
                self.assertIs(result["artifacts_verified"], False)
                self.assertIs(result["private_images_removed"], True)

    def test_unknown_cleanup_or_unsafe_trees_never_prepare_upload(self):
        for mutation in ("cleanup", "nested", "linked-directory", "resources", "collision"):
            with self.subTest(mutation=mutation), self.fixture.fixture() as (evidence, stage, _, report, source):
                bundle = self.bundle(evidence, stage, report, source)
                metadata = evidence.parent / "export-validation"
                if mutation == "nested": (bundle / "nested").mkdir()
                elif mutation == "linked-directory":
                    bundle.rename(bundle.with_name("original"))
                    bundle.symlink_to(bundle.with_name("original"))
                elif mutation == "resources":
                    resource = evidence.with_name(evidence.name + "-resources.json")
                    value = json.loads(resource.read_bytes())
                    value["empty_verified"] = False
                    resource.write_bytes(export.encoded(value))
                elif mutation == "collision": metadata.mkdir()
                remove = validator.base.inputs.remove_flat_directory
                with mock.patch.object(validator.base.inputs, "remove_flat_directory",
                                       side_effect=OSError("PRIVATE") if mutation == "cleanup" else remove):
                    with self.assertRaises((OSError, ValueError)):
                        self.run_validation(evidence, source)
                self.assertFalse((metadata / "validation.json").exists())

    def test_failed_build_with_confirmed_cleanup_retains_only_failure_metadata(self):
        with self.fixture.fixture() as (evidence, stage, _, report, source):
            self.fixture.prepare(evidence, stage, report)
            (evidence / "result.json").write_bytes(export.encoded(recorded(report)))
            self.assertEqual(self.run_validation(evidence, source, "failure"), "fail")
            self.assertFalse(any(os.path.lexists(p) for p in export.paths(evidence)))
            self.assertEqual({p.name for p in (evidence.parent / "export-validation").iterdir()}, {"validation.json"})

    def test_failure_projection_retains_stage_oom_status_without_raw_details(self):
        with self.fixture.fixture() as (evidence, stage, _, report, source):
            self.fixture.prepare(evidence, stage, report)
            report = recorded(report)
            report.update(result="fail", stage="installation", failure_kind="process-exit")
            report["worker"].update(result="fail", stage="guest-network", failure_kind="rejected",
                failure_frames=[{"PRIVATE_RAW_TRACE": "PRIVATE_ERROR"}],
                private_process_states={"PRIVATE_PROCESS": "PRIVATE_PATH"})
            raw = export.encoded(report)
            (evidence / "result.json").write_bytes(raw)
            resources = evidence.with_name(evidence.name + "-resources.json")
            state = json.loads(resources.read_bytes())
            state.update(result="fail", child_exit_code=-9, oom_kill=2, memory_peak_bytes=8 << 30,
                         populated_before_cleanup=1)
            resources.write_bytes(export.encoded(state))
            self.assertEqual(self.run_validation(evidence, source, "failure"), "fail")
            published = (evidence.parent / "export-validation/validation.json").read_bytes()
            self.assertNotIn(b"PRIVATE", published)
            observed = json.loads(published)
            self.assertEqual(observed["failure"], {"schema": "nib.native-build-failure/v1",
                "git_sha": source["git_sha"], "installation": {"result": "fail", "stage": "installation",
                    "failure_kind": "process-exit", "payload_removed": True, "process_cleanup_confirmed": True,
                    "receipt": {"bytes": len(raw), "sha256": base.digest(raw)},
                    "worker": {"result": "fail", "stage": "guest-network", "failure_kind": "rejected",
                               "installed_packages": 212, "installer_exit_code": 0}},
                "resources": {"result": "fail", "limits_read_back": True, "child_exit_code": -9,
                    "oom_kill": 2, "memory_peak_bytes": 8 << 30, "populated_before_cleanup": 1}})
            self.assertTrue(observed["private_images_removed"])
            for key in ("artifacts_verified", "boot_verified", "signed", "confidentiality_verified",
                        "reproducibility_verified"):
                self.assertIs(observed[key], False)
            self.assertEqual((evidence / "result.json").read_bytes(), raw)
            self.assertFalse(any(os.path.lexists(p) for p in export.paths(evidence)))

    def test_missing_report_worker_and_optional_counters_are_unknown_not_zero(self):
        for missing in ("report", "worker", "skipped"):
            with self.subTest(missing=missing), self.fixture.fixture() as (evidence, _, _, report, source):
                if missing in ("report", "skipped"):
                    (evidence / "result.json").unlink()
                else:
                    value = recorded(report)
                    value.update(result="fail", stage="guest-helper-compilation", worker=None, failure_kind="timeout")
                    (evidence / "result.json").write_bytes(export.encoded(value))
                resource = evidence.with_name(evidence.name + "-resources.json")
                if missing == "skipped":
                    resource.unlink()
                else:
                    state = json.loads(resource.read_bytes())
                    state.update(result="fail", child_exit_code=None)
                    state.pop("oom_kill")
                    resource.write_bytes(export.encoded(state))
                self.assertEqual(self.run_validation(evidence, source,
                    "skipped" if missing == "skipped" else "cancelled"), "fail")
                value = json.loads((evidence.parent / "export-validation/validation.json").read_bytes())["failure"]
                if missing == "worker":
                    self.assertIsNone(value["installation"]["worker"])
                    self.assertEqual(value["installation"]["stage"], "guest-helper-compilation")
                else:
                    self.assertIsNone(value["installation"])
                if missing == "skipped":
                    self.assertIsNone(value["resources"])
                else:
                    for key in ("oom_kill", "memory_peak_bytes", "populated_before_cleanup", "child_exit_code"):
                        self.assertIsNone(value["resources"][key])

    def test_projection_and_receipt_keep_existing_json_size_and_deadline_bounds(self):
        for failure in ("size", "deadline"):
            with self.subTest(failure=failure), self.fixture.fixture() as (evidence, stage, _, report, source):
                self.fixture.prepare(evidence, stage, report)
                (evidence / "result.json").write_bytes(export.encoded(recorded(report)))
                encoded = export.encoded
                project = validator.failure_receipt
                def encoding(value):
                    raw = encoded(value)
                    return b" " * export.MAX_JSON + raw if value.get("schema") == "nib.native-private-export-validation/v1" else raw
                def slow_projection(*args):
                    time.sleep(1)
                    return project(*args)
                with mock.patch.object(export, "encoded", side_effect=encoding if failure == "size" else encoded), \
                        mock.patch.object(export, "TIMEOUT", 0.01 if failure == "deadline" else export.TIMEOUT), \
                        mock.patch.object(validator, "failure_receipt", side_effect=slow_projection
                                          if failure == "deadline" else project):
                    with self.assertRaises(ValueError):
                        self.run_validation(evidence, source, "failure")
                self.assertFalse(any(os.path.lexists(p) for p in export.paths(evidence)))
                self.assertFalse((evidence.parent / "export-validation").exists())

    def test_bad_present_failure_records_reject_upload_but_still_clean_images(self):
        mutations = ("missing-field", "unknown-field", "source", "schema", "stage", "kind", "true-qualification",
            "worker-field", "worker-missing-field", "worker-schema", "worker-stage", "worker-kind", "worker-count",
            "worker-status", "worker-bool", "resource-field", "resource-status", "resource-oom", "resource-peak",
            "resource-populated", "resource-bool", "resource-limits", "duplicate", "nonfinite", "malformed",
            "oversized", "empty", "symlink", "hardlink", "directory", "writable", "ownership", "changed", "disappeared")
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.fixture.fixture() as (evidence, stage, _, report, source):
                self.fixture.prepare(evidence, stage, report)
                value = recorded(report)
                resource = evidence.with_name(evidence.name + "-resources.json")
                state = json.loads(resource.read_bytes())
                if mutation == "missing-field": del value["stage"]
                elif mutation == "unknown-field": value["PRIVATE"] = "PRIVATE"
                elif mutation == "source": value["git_sha"] = "ef" * 20
                elif mutation == "schema": value["schema"] = "unknown"
                elif mutation == "stage": value["stage"] = "PRIVATE"
                elif mutation == "kind": value["failure_kind"] = "PRIVATE"
                elif mutation == "true-qualification": value["bootable"] = True
                elif mutation == "worker-field": value["worker"]["PRIVATE"] = "PRIVATE"
                elif mutation == "worker-missing-field": del value["worker"]["installer_exit_code"]
                elif mutation == "worker-schema": value["worker"]["schema"] = "unknown"
                elif mutation == "worker-stage": value["worker"]["stage"] = "PRIVATE"
                elif mutation == "worker-kind": value["worker"]["failure_kind"] = "PRIVATE"
                elif mutation == "worker-count": value["worker"]["installed_packages"] = 213
                elif mutation == "worker-status": value["worker"]["installer_exit_code"] = 256
                elif mutation == "worker-bool": value["worker"]["installed_packages"] = True
                elif mutation == "resource-field": state["PRIVATE"] = "PRIVATE"
                elif mutation == "resource-status": state["child_exit_code"] = -65
                elif mutation == "resource-oom": state["oom_kill"] = -1
                elif mutation == "resource-peak": state["memory_peak_bytes"] = 1 << 63
                elif mutation == "resource-populated": state["populated_before_cleanup"] = 2
                elif mutation == "resource-bool": state["oom_kill"] = False
                elif mutation == "resource-limits": state["limits"]["memory.max"] = "max"
                resource.write_bytes(export.encoded(state))
                path = evidence / "result.json"
                path.write_bytes(export.encoded(value))
                if mutation == "duplicate": path.write_bytes(b'{"stage":"preflight","stage":"PRIVATE"}')
                elif mutation == "nonfinite": path.write_bytes(b'{"stage":NaN}')
                elif mutation == "malformed": path.write_bytes(b'{"PRIVATE"')
                elif mutation == "oversized": path.write_bytes(b" " * (export.MAX_JSON + 1))
                elif mutation == "empty": path.write_bytes(b"")
                elif mutation in ("symlink", "hardlink"):
                    original = evidence / "other"
                    path.rename(original)
                    path.symlink_to(original) if mutation == "symlink" else os.link(original, path)
                elif mutation == "directory":
                    path.unlink()
                    path.mkdir()
                elif mutation == "writable": path.chmod(0o666)
                real_read = validator.base.read_at
                real_stat = validator.os.stat
                def changing_read(*args):
                    raw = real_read(*args)
                    if mutation == "changed" and args[1] == "result.json": path.chmod(0o644)
                    if mutation == "disappeared" and args[1] == "result.json": path.unlink()
                    return raw
                def observed_stat(name, *args, **kwargs):
                    info = real_stat(name, *args, **kwargs)
                    if mutation == "ownership" and name == "result.json":
                        return types.SimpleNamespace(st_uid=os.getuid() + 1, st_mode=info.st_mode)
                    return info
                with mock.patch.object(validator.base, "read_at", side_effect=changing_read), \
                        mock.patch.object(validator.os, "stat", side_effect=observed_stat):
                    with self.assertRaises((ValueError, OSError, KeyError, TypeError)):
                        self.run_validation(evidence, source, "failure")
                self.assertFalse(any(os.path.lexists(p) for p in export.paths(evidence)))
                self.assertFalse((evidence.parent / "export-validation").exists())

    def test_actual_deadline_rejects_verification_and_removes_images(self):
        with self.fixture.fixture() as (evidence, stage, _, report, source):
            self.bundle(evidence, stage, report, source)
            with mock.patch.object(export, "TIMEOUT", 0.01), \
                    mock.patch.object(validator, "verify_bundle", side_effect=lambda *a: time.sleep(1)):
                with self.assertRaises(ValueError): self.run_validation(evidence, source)
            self.assertFalse(any(os.path.lexists(p) for p in export.paths(evidence)))
            self.assertFalse((evidence.parent / "export-validation").exists())

    def test_cleanup_identity_replacement_and_metadata_failure_disable_upload(self):
        for mutation in ("replacement", "parent", "publish", "metadata-rehash"):
            with self.subTest(mutation=mutation), self.fixture.fixture() as (evidence, stage, _, report, source):
                bundle = self.bundle(evidence, stage, report, source)
                original_verify = validator.verify_bundle
                def verify(*args):
                    metadata = original_verify(*args)
                    if mutation == "replacement":
                        bundle.rename(bundle.with_name("original"))
                        bundle.mkdir(mode=0o700)
                        (bundle / "sentinel").write_text("untouched replacement")
                    elif mutation == "parent":
                        # Parent rename is undone so fixture cleanup owns both trees.
                        evidence.parent.rename(evidence.parent.with_name(evidence.parent.name + "-moved"))
                        evidence.parent.mkdir(mode=0o700)
                    return metadata
                publish = validator.base.publish
                check = validator.base.verify
                try:
                    with mock.patch.object(validator, "verify_bundle", side_effect=verify), \
                            mock.patch.object(validator.base, "publish",
                                side_effect=OSError("PRIVATE") if mutation == "publish" else publish), \
                            mock.patch.object(validator.base, "verify",
                                side_effect=ValueError("PRIVATE") if mutation == "metadata-rehash" else check):
                        with self.assertRaises((OSError, ValueError)):
                            self.run_validation(evidence, source)
                    if mutation == "replacement":
                        self.assertEqual((bundle / "sentinel").read_text(), "untouched replacement")
                finally:
                    if mutation == "parent":
                        evidence.parent.rmdir()
                        evidence.parent.with_name(evidence.parent.name + "-moved").rename(evidence.parent)

    def test_entrypoint_requires_host_and_source_gates_and_fixed_paths(self):
        sources = {validator.native.EXPORT: (ROOT / validator.native.EXPORT).read_bytes()}
        for failure in (None, "host", "source", "self"):
            with self.subTest(failure=failure), \
                    mock.patch.object(validator.sys, "argv", ["verify", "--build-result", "success"]), \
                    mock.patch.object(validator.native.probe.native, "preflight",
                                      side_effect=ValueError("PRIVATE") if failure == "host" else None) as host_gate, \
                    mock.patch.object(validator.native, "committed_sources", return_value=("ab" * 20, sources),
                                      side_effect=ValueError("PRIVATE") if failure == "source" else None) as audit, \
                    mock.patch.object(validator, "committed_validator", return_value=self.validator_source,
                                      side_effect=ValueError("PRIVATE") if failure == "self" else None), \
                    mock.patch.object(validator.native, "export_helpers", return_value=(export, publication)), \
                    mock.patch.object(validator, "validate", return_value="pass") as validate, \
                    mock.patch("builtins.print") as output:
                if failure is None:
                    self.assertEqual(validator.main(), 0)
                    host_gate.assert_called_once_with()
                    audit.assert_called_once_with(True, True, True, True)
                    self.assertEqual(validate.call_args.args[:2], (validator.EVIDENCE, validator.METADATA))
                    output.assert_called_once_with("pass")
                else:
                    with self.assertRaises(ValueError): validator.main()
                    validate.assert_not_called()
                    output.assert_not_called()

    def test_fixed_workflow_audit_preserves_generic_source_gate_and_checks_exact_blob(self):
        revision = "ab" * 20
        raw = (ROOT / validator.WORKFLOW).read_bytes()
        oid = "cd" * 20
        for failure in (None, "revision", "self", "mode", "path", "bytes", "git"):
            with self.subTest(failure=failure):
                row = ("120000" if failure == "mode" else "100644") + " blob " + oid + "\t" + (
                    "other" if failure == "path" else validator.WORKFLOW) + "\n"
                replies = [subprocess.CompletedProcess([], 0, row.encode(), b""),
                           subprocess.CompletedProcess([], 1 if failure == "git" else 0,
                               b"changed" if failure == "bytes" else raw, b"PRIVATE")]
                with mock.patch.object(validator.native.tools, "committed", return_value=(
                        "ef" * 20 if failure == "revision" else revision,
                        {validator.SCRIPT: b"changed" if failure == "self" else validator.SELF})) as generic, \
                        mock.patch.object(validator.base.processes, "execute_process_group", side_effect=replies) as git:
                    if failure is not None:
                        with self.assertRaises((ValueError, subprocess.SubprocessError)):
                            validator.committed_validator(revision)
                    else:
                        self.assertEqual(validator.committed_validator(revision), {
                            validator.SCRIPT: base.digest(validator.SELF), validator.WORKFLOW: base.digest(raw)})
                        self.assertEqual(git.call_args.kwargs["max_output_bytes"], 65536)
                        self.assertEqual(git.call_args.kwargs["timeout"], 30)
                    generic.assert_called_once_with((validator.SCRIPT,))

    def test_real_workflow_shell_only_authorizes_prepared_metadata_and_fails_bad_result(self):
        workflow = yaml.safe_load((ROOT / validator.WORKFLOW).read_text())
        step = next(s for s in workflow["jobs"]["install"]["steps"] if s.get("id") == "export_validation")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub, marker = root / "stub.py", root / "outputs"
            stub.write_text("import os, sys\nprint(os.environ['FIXTURE_RESULT'])\nsys.exit(int(os.environ['FIXTURE_EXIT']))\n")
            shell = step["run"].replace("/usr/bin/python3.10 -I -S -B host/validate-native-boot-export.py",
                shlex.quote(sys.executable) + " -I -S -B " + shlex.quote(str(stub)))
            for result, code, ready, passed in (("pass", 0, True, True), ("fail", 0, True, False),
                    ("pass", 1, False, False), ("unexpected", 0, False, False),
                    ("pass\nmetadata_ready=true", 0, False, False)):
                with self.subTest(result=result, code=code):
                    marker.write_text("")
                    process = subprocess.run(["/bin/bash", "-e", "-o", "pipefail", "-c", shell],
                        env={"PATH": "/usr/bin:/bin", "BUILD_RESULT": "success", "GITHUB_OUTPUT": str(marker),
                             "FIXTURE_RESULT": result, "FIXTURE_EXIT": str(code)},
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=False)
                    self.assertEqual(process.returncode == 0, passed)
                    self.assertEqual(marker.read_text(), "metadata_ready=true\n" if ready else "")

    def test_debug_unused_export_keeps_single_json_and_no_upload_after_validator_rejection(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/debug-boot.yml").read_text())
        job = workflow["jobs"]["boot"]
        self.assertEqual((job["runs-on"], job["timeout-minutes"]), ("ubuntu-22.04", 35))
        step = next(s for s in job["steps"] if s.get("id") == "unused_export")
        self.assertEqual(step["if"], "${{ always() && steps.debug.outcome == 'skipped' }}")
        self.assertEqual(step["timeout-minutes"], 5)
        upload = job["steps"][-1]
        self.assertEqual(upload["if"], "${{ always() && steps.unused_export.outputs.metadata_ready == 'true' }}")
        self.assertEqual(upload["with"]["path"], "build/native-install/export-validation/validation.json")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub, marker = root / "stub.py", root / "outputs"
            stub.write_text("import os, sys\nprint(os.environ['FIXTURE_RESULT'])\nsys.exit(int(os.environ['FIXTURE_EXIT']))\n")
            shell = step["run"].replace("/usr/bin/python3.10 -I -S -B host/validate-native-boot-export.py",
                shlex.quote(sys.executable) + " -I -S -B " + shlex.quote(str(stub)))
            for result, code, ready in (("fail", 0, True), ("fail", 1, False), ("unknown", 0, False),
                                        ("fail\nmetadata_ready=true", 0, False)):
                with self.subTest(result=result, code=code):
                    marker.write_text("")
                    process = subprocess.run(["/bin/bash", "-e", "-o", "pipefail", "-c", shell],
                        env={"PATH": "/usr/bin:/bin", "BUILD_RESULT": "failure", "GUEST_NETWORK": "true",
                             "GITHUB_OUTPUT": str(marker), "FIXTURE_RESULT": result, "FIXTURE_EXIT": str(code)},
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=False)
                    self.assertEqual(process.returncode == 0, ready)
                    self.assertEqual(marker.read_text(), "metadata_ready=true\n" if ready else "")

    def test_workflow_keeps_default_gates_and_never_uploads_a_directory_in_export_mode(self):
        workflow = yaml.safe_load((ROOT / validator.WORKFLOW).read_text())
        job = workflow["jobs"]["install"]
        self.assertEqual(job["if"], "github.ref == 'refs/heads/main'")
        self.assertEqual(job["runs-on"], "ubuntu-22.04")
        steps = job["steps"]
        build = next(s for s in steps if s.get("id") == "build")
        validation = next(s for s in steps if s.get("id") == "export_validation")
        self.assertIn('args+=(--export-boot)', build["run"])
        for flag in ("BOOT_UKI", "VERITY_ROOT", "GUEST_RUNTIME", "INSTALL_DRIVER", "COMPILE_DRIVER"):
            self.assertIn('test "$' + flag + '" = true', build["run"].split('if [ "$VALIDATE_EXPORT" = true ]; then')[1])
        self.assertEqual(validation["timeout-minutes"], 5)
        self.assertIn("/usr/bin/python3.10 -I -S -B host/validate-native-boot-export.py", validation["run"])
        self.assertNotIn("sudo", validation["run"])
        self.assertLess(validation["run"].index("case"), validation["run"].index("metadata_ready=true"))
        self.assertIn('test "$result" = pass', validation["run"])
        uploads = [s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@")]
        self.assertEqual(len(uploads), 2)
        self.assertIn("!inputs.validate_export", uploads[0]["if"])
        self.assertIn("steps.export_validation.outputs.metadata_ready == 'true'", uploads[1]["if"])
        self.assertEqual(set(uploads[1]["with"]["path"].splitlines()), {
            "build/native-install/export-validation/" + name
            for name in (*validator.RECEIPTS, "manifest.json", "validation.json")})


if __name__ == "__main__":
    unittest.main()
