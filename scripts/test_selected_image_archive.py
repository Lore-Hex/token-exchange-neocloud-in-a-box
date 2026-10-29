# SPDX-License-Identifier: Apache-2.0
"""Synthetic archives and isolated verifier children; no Docker or image runtime."""

import copy
from contextlib import contextmanager, redirect_stdout
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

import test_selected_service_image as images
from test_qwen_export import export as qwen, packed

ROOT = Path(__file__).absolute().parents[1]
spec = importlib.util.spec_from_file_location("selected_archive", ROOT / "host/verify-selected-image-archive.py")
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


def archive_fixture(prepared="a" * 64, *, compressed=True, attestation=True, change=None):
    raw_layers = [packed({"one": b"a" * 2048}), packed({"two": b"b" * 4096}), packed({"one": b"a" * 2048})]
    diff_ids = ["sha256:" + verify.sha(raw) for raw in raw_layers]
    files = {}

    def blob(value, media):
        raw = verify.encoded(value) if type(value) is dict else value
        digest = verify.sha(raw)
        files["blobs/sha256/" + digest] = raw
        return {"digest": "sha256:" + digest, "size": len(raw), "mediaType": media}

    config = {"os": "linux", "architecture": "amd64", "config": {
        "User": "65532:65532", "WorkingDir": "/", "Healthcheck": {"Test": ["NONE"]},
        "Entrypoint": ["/usr/bin/python3.12", "-I", "-S", "-B",
            "/opt/nib-service/scripts/selected_service_process.py", "--prepared-sha256", prepared]},
        "rootfs": {"type": "layers", "diff_ids": diff_ids}}
    if change:
        change(config)
    config_descriptor = blob(config, "application/vnd.oci.image.config.v1+json")
    layers = [blob(gzip.compress(raw, mtime=0) if compressed else raw,
                   "application/vnd.oci.image.layer.v1.tar+gzip" if compressed
                   else "application/vnd.oci.image.layer.v1.tar") for raw in raw_layers]
    manifest = blob({"schemaVersion": 2, "config": config_descriptor, "layers": layers},
                    "application/vnd.oci.image.manifest.v1+json")
    manifest["platform"] = {"os": "linux", "architecture": "amd64"}
    descriptors = [manifest]
    if attestation:
        statement = blob({"subject": [{"digest": {"sha256": manifest["digest"][7:]}}]},
                         "application/vnd.in-toto+json")
        metadata_config = blob({"os": "unknown", "architecture": "unknown", "rootfs": {
            "type": "layers", "diff_ids": [statement["digest"]]}}, "application/vnd.oci.image.config.v1+json")
        metadata = blob({"schemaVersion": 2, "config": metadata_config, "layers": [statement]},
                        "application/vnd.oci.image.manifest.v1+json")
        metadata.update(platform={"os": "unknown", "architecture": "unknown"}, annotations={
            "vnd.docker.reference.type": "attestation-manifest",
            "vnd.docker.reference.digest": manifest["digest"]})
        descriptors.append(metadata)
    root = blob({"schemaVersion": 2, "manifests": descriptors}, "application/vnd.oci.image.index.v1+json")
    files["index.json"] = verify.encoded({"schemaVersion": 2, "manifests": [root]})
    files["oci-layout"] = verify.encoded({"imageLayoutVersion": "1.0.0"})
    files["manifest.json"] = verify.encoded([{"Config": "blobs/sha256/" + config_descriptor["digest"][7:],
        "Layers": ["blobs/sha256/" + row["digest"][7:] for row in layers], "RepoTags": None}])
    return files, {"image_id": root["digest"], "prepared_sha256": prepared}, diff_ids, sum(map(len, raw_layers))


class ArchiveTests(unittest.TestCase):
    def check(self, files, build, **expected):
        raw = packed(files)
        return verify.verify_archive(io.BytesIO(raw), expected.get("sha", verify.sha(raw)),
            expected.get("size", len(raw)), build, expected.get("deadline", time.monotonic() + 10))

    def test_pinned_helpers_and_qwen_defaults_are_unchanged(self):
        for name, digest in verify.HELPERS.items():
            self.assertEqual(verify.sha((ROOT / name).read_bytes()), digest)
        before = (qwen.MAX_ARCHIVE, qwen.MAX_EXPANDED, qwen.TIMEOUT, qwen.digest, qwen.json)
        files, build, _, _ = archive_fixture()
        self.check(files, build)
        self.assertEqual(before, (qwen.MAX_ARCHIVE, qwen.MAX_EXPANDED, qwen.TIMEOUT, qwen.digest, qwen.json))
        first, second = verify.load_pinned(verify.EXPORT, verify.EXPORT_SHA), verify.load_pinned(verify.EXPORT, verify.EXPORT_SHA)
        self.assertIsNot(first, second)
        self.assertEqual(second.MAX_ARCHIVE, 2 << 30)
        self.assertNotIn(first.__name__, sys.modules)

    def test_full_ordered_diffids_and_expansion_include_repeated_layers(self):
        for metadata in (False, True):
            files, build, diff_ids, expanded = archive_fixture(attestation=metadata)
            result = self.check(files, build)
            self.assertEqual(result["diff_ids"], diff_ids)
            self.assertEqual(result["expanded_bytes"], expanded)
            self.assertEqual(result["layers"], 3)
            self.assertEqual(result["image_id"], build["image_id"])
            self.assertNotEqual(result["config_id"], result["image_id"])

    def test_exact_archive_pin_is_checked_before_tar_parsing(self):
        files, build, _, _ = archive_fixture()
        raw = packed(files)
        with mock.patch("tarfile.open", side_effect=AssertionError("must not parse unbound bytes")):
            for changed in ({"sha": "0" * 64}, {"size": 1}):
                with self.assertRaises(ValueError):
                    verify.verify_archive(io.BytesIO(raw), changed.get("sha", verify.sha(raw)),
                        changed.get("size", len(raw)), build, time.monotonic() + 10)

    def test_pinned_helper_tampering_rejected(self):
        with mock.patch.object(verify, "read", return_value=b"raise AssertionError('do not execute')"):
            with self.assertRaises(ValueError):
                verify.load_pinned(verify.EXPORT, verify.EXPORT_SHA)

    def test_transitive_source_tampering_fails_before_any_import(self):
        original = verify.read
        for target in ("deepseek_model_profile", "engine_local_correctness", "engine_python_bootstrap"):
            def changed(path, limit):
                raw = original(path, limit)
                return b"raise AssertionError('must not execute')" if path.stem == target else raw
            with self.subTest(module=target), mock.patch.object(verify, "read", side_effect=changed), \
                    mock.patch.object(verify.importlib, "import_module") as execute:
                with self.assertRaises(ValueError), verify.preparation_helper():
                    self.fail("must reject before yielding")
                execute.assert_not_called()

    def test_authenticated_imports_are_fresh_and_restore_shared_modules(self):
        names = set(verify.PREPARATION_MODULES) | {"pilot"}
        before = {name: sys.modules.get(name) for name in names}
        search, loaders = list(sys.path), list(sys.meta_path)
        with verify.preparation_helper() as helper:
            self.assertIsNot(helper, before["selected_service_image"])
            self.assertIsNot(helper.owner.service.models, before["deepseek_model_profile"])
            self.assertEqual(helper.owner.service.models.select("original")["name"], "original")
            self.assertEqual(sys.modules["pilot"].__path__, [])
        self.assertEqual(before, {name: sys.modules.get(name) for name in names})
        self.assertEqual(sys.path, search)
        self.assertEqual(sys.meta_path, loaders)

    def test_authenticated_source_bytes_are_not_reopened_for_execution(self):
        original = verify.read
        seen = set()
        def once(path, limit):
            self.assertNotIn(path, seen)
            seen.add(path)
            return original(path, limit)
        with mock.patch.object(verify, "read", side_effect=once), verify.preparation_helper() as helper:
            self.assertEqual(helper.owner.service.models.select("original")["name"], "original")
        self.assertEqual(len(seen), len(verify.PREPARATION_MODULES))

    def test_closed_limits_and_expired_deadline_fail(self):
        files, build, _, _ = archive_fixture()
        for name in ("MAX_ARCHIVE", "MAX_EXPANDED"):
            with self.subTest(limit=name), mock.patch.object(verify, name, 1), self.assertRaises(ValueError):
                self.check(files, build)
        with self.assertRaises(ValueError):
            self.check(files, build, deadline=time.monotonic() - 1)

    def test_raw_layers_and_wrong_selected_entrypoint_rejected(self):
        for options in ({"compressed": False}, {"prepared": "b" * 64},
                        {"change": lambda c: c["config"].update(User="0")},
                        {"change": lambda c: c["config"].update(Volumes={"/private": {}})},
                        {"change": lambda c: c.update(architecture="arm64")}):
            with self.subTest(options=tuple(options)):
                files, build, _, _ = archive_fixture(**options)
                build["prepared_sha256"] = "a" * 64
                with self.assertRaises(ValueError):
                    self.check(files, build)

    def test_corrupt_unreferenced_blob_is_still_rejected(self):
        files, build, _, _ = archive_fixture()
        files["blobs/sha256/" + "0" * 64] = b"not the named content"
        with self.assertRaises(ValueError):
            self.check(files, build)

    def test_compressed_digest_does_not_replace_diffid_check(self):
        files, build, _, _ = archive_fixture(change=lambda c: c["rootfs"].update(diff_ids=["sha256:" + "0" * 64] * 3))
        with self.assertRaises(ValueError):
            self.check(files, build)

    def test_duplicate_json_keys_tags_and_other_image_fail(self):
        for mutation in ("duplicate", "tag", "image"):
            files, build, _, _ = archive_fixture()
            if mutation == "duplicate":
                raw = files["index.json"]
                files["index.json"] = b'{"schemaVersion":1,' + raw[1:]
            elif mutation == "tag":
                value = json.loads(files["manifest.json"])
                value[0]["RepoTags"] = ["unrelated:tag"]
                files["manifest.json"] = verify.encoded(value)
            else:
                value = json.loads(files["index.json"])
                value["manifests"] *= 2
                files["index.json"] = verify.encoded(value)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.check(files, build)


class HandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        images.ImageTests.setUpClass()

    @contextmanager
    def fixture(self):
        contract = images.lab_contract(verify.PROFILE)
        model = images.image.owner.service.models.select("original")
        contract.update(model_profile=model, pack_revision=model["hf_revision"], pack_bytes=model["total_bytes"],
            manifest_sha256=model["manifest_sha256"], tokenizer_parity=True,
            runtime_observation=True, tokenizer_materialization={})
        contract["arguments"][contract["arguments"].index("--served-model-name") + 1] = model["served_model_name"]
        with mock.patch.object(images, "lab_contract", side_effect=lambda: copy.deepcopy(contract)):
            with images.ImageTests().preparation() as (root, bundle, trcs, _, _, _):
                prepared = root / "prepared"
                summary = images.image.prepare(bundle, "a" * 64, trcs, prepared)
                receipt, _, lab = images.image.validated_files(prepared, summary["receipt_sha256"])
                files, build, _, _ = archive_fixture(prepared=receipt["prepared_sha256"])
                build.update(schema=images.image.BUILD_SCHEMA, result="pass", preparation_sha256=summary["receipt_sha256"],
                    base_binding={"binding": "local-manifest-reference-platform-and-diffids",
                        "daemon_os_interpreter_trusted": True, "docker_reported_id": "sha256:" + "b" * 64,
                        "local_config_bytes_rehashed": False, "local_layers_rehashed": False,
                        "manifest_sha256": lab["base_image"].rsplit("sha256:", 1)[1], "platform": "linux/amd64",
                        "raw_config_link_verified": True, "raw_config_sha256": lab["official_config"]["sha256"]},
                    image_built=True, build_completed=True, runtime_started=False,
                    hardware_validated=False, deployable=False, published=False)
                raw = packed(files)
                archive, build_path = root / "archive.tar", root / "build.json"
                archive.write_bytes(raw)
                build_path.write_bytes(verify.encoded(build))
                for path in (archive, build_path):
                    path.chmod(0o600)
                yield SimpleNamespace(archive=archive, archive_sha256=verify.sha(raw), archive_bytes=len(raw),
                    preparation=prepared, preparation_sha256=summary["receipt_sha256"], build_report=build_path,
                    build_sha256=verify.sha(build_path.read_bytes()), producer="c" * 40, output=root / "verified.json")

    def test_original_preparation_build_and_archive_are_joined(self):
        with self.fixture() as args, mock.patch("socket.socket", side_effect=AssertionError("no networking")), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("no tool or runtime execution")):
            result = verify.verify(args)
            self.assertEqual(result["source"]["build_producer"], "0" * 40)
            self.assertEqual(result["source"]["model_profile"]["name"], "original")
            self.assertEqual(result["source"]["build_sha256"], args.build_sha256)
            self.assertEqual(result["archive"]["sha256"], args.archive_sha256)

    def test_wrong_preparation_build_or_archive_pins_rejected(self):
        for field in ("preparation_sha256", "build_sha256", "archive_sha256", "archive_bytes"):
            with self.subTest(field=field), self.fixture() as args:
                setattr(args, field, 1 if field == "archive_bytes" else "0" * 64)
                with self.assertRaises(ValueError):
                    verify.verify(args)

    def test_build_claims_and_cross_preparation_rejected_even_with_new_hash(self):
        for field, value in (("runtime_started", True), ("preparation_sha256", "f" * 64),
                             ("prepared_sha256", "d" * 64), ("deployable", True)):
            with self.subTest(field=field), self.fixture() as args:
                row = json.loads(args.build_report.read_bytes())
                row[field] = value
                args.build_report.write_bytes(verify.encoded(row))
                args.build_sha256 = verify.sha(args.build_report.read_bytes())
                with self.assertRaises(ValueError):
                    verify.verify(args)

    def test_nonoriginal_preparation_is_not_admitted(self):
        with images.ImageTests().preparation() as (root, bundle, trcs, _, _, _):
            summary = images.image.prepare(bundle, "a" * 64, trcs, root / "prepared")
            args = SimpleNamespace(preparation=root / "prepared", preparation_sha256=summary["receipt_sha256"])
            with self.assertRaises(ValueError):
                verify.source_inputs(args)

    def test_link_fifo_hardlink_and_permissions_fail_without_reading(self):
        for kind in ("symlink", "fifo", "hardlink", "public"):
            with self.subTest(kind=kind), self.fixture() as args:
                source = args.archive
                if kind == "public":
                    source.chmod(0o644)
                else:
                    args.archive = source.parent / "replacement"
                    if kind == "symlink":
                        args.archive.symlink_to(source)
                    elif kind == "fifo":
                        os.mkfifo(args.archive, 0o600)
                    else:
                        os.link(source, args.archive)
                with self.assertRaises((ValueError, OSError)):
                    verify.verify(args)

    def test_archive_change_during_validation_rejected(self):
        with self.fixture() as args:
            original = verify.verify_archive
            def changed(*values):
                result = original(*values)
                args.archive.write_bytes(b"changed")
                return result
            with mock.patch.object(verify, "verify_archive", side_effect=changed), self.assertRaises(ValueError):
                verify.verify(args)

    @unittest.skipUnless(sys.platform == "linux", "bounded Linux worker")
    def test_real_worker_receipt_is_private_exclusive_and_nonruntime(self):
        with self.fixture() as args, redirect_stdout(io.StringIO()):
            self.assertEqual(verify.run(args), 0)
            raw = args.output.read_bytes()
            report = json.loads(raw)
            self.assertEqual(stat.S_IMODE(args.output.stat().st_mode), 0o600)
            self.assertEqual(args.output.stat().st_nlink, 1)
            self.assertEqual(report["producer"], args.producer)
            self.assertEqual(report["script_sha256"], verify.sha((ROOT / verify.SELF).read_bytes()))
            self.assertTrue(report["worker_exit_confirmed"])
            self.assertTrue(report["archive_verified"])
            self.assertEqual(report["helpers"], verify.HELPERS)
            self.assertEqual(report["source"]["validator_sources_sha256"],
                             verify.sha(verify.encoded(verify.PREPARATION_MODULES)))
            for flag in ("image_loaded", "runtime_started", "deployable", "published", "hardware_validated", "confidential"):
                self.assertIs(report[flag], False)
            self.assertNotIn(str(args.archive).encode(), raw)
            with self.assertRaises((ValueError, FileExistsError)):
                verify.run(args)
            self.assertEqual(args.output.read_bytes(), raw)

    @unittest.skipUnless(sys.platform == "linux", "bounded Linux worker")
    def test_failed_worker_preserves_failure_without_operator_paths(self):
        with self.fixture() as args, redirect_stdout(io.StringIO()):
            args.archive_sha256 = "0" * 64
            self.assertEqual(verify.run(args), 1)
            raw = args.output.read_bytes()
            report = json.loads(raw)
            self.assertFalse(report["archive_verified"])
            self.assertTrue(report["worker_exit_confirmed"])
            self.assertNotIn("archive", report)
            self.assertNotIn(str(args.archive).encode(), raw)

    @unittest.skipUnless(sys.platform == "linux", "bounded Linux worker")
    def test_timeout_or_cleanup_uncertainty_cannot_publish_pass(self):
        with self.fixture() as args, redirect_stdout(io.StringIO()):
            loader = verify.load_pinned
            def helper(name, digest):
                if name == verify.RUNNER:
                    return SimpleNamespace(execute_process_group=mock.Mock(side_effect=subprocess.TimeoutExpired("synthetic", 1)))
                return loader(name, digest)
            with mock.patch.object(verify, "load_pinned", side_effect=helper):
                self.assertEqual(verify.run(args), 1)
            report = json.loads(args.output.read_bytes())
            self.assertFalse(report["worker_exit_confirmed"])
            self.assertFalse(report["archive_verified"])

    @unittest.skipUnless(sys.platform == "linux", "bounded Linux worker")
    def test_crash_claim_prevents_replay_before_worker(self):
        with self.fixture() as args:
            args.output.with_name(args.output.name + ".claim").touch(mode=0o600)
            with mock.patch("subprocess.Popen") as spawn, self.assertRaises(FileExistsError):
                verify.run(args)
            spawn.assert_not_called()
            self.assertFalse(args.output.exists())


if __name__ == "__main__":
    unittest.main()
