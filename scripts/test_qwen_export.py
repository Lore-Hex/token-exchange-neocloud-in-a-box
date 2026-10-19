# SPDX-License-Identifier: Apache-2.0
from contextlib import ExitStack
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_qwen_pilot import recorder
from test_qwen_container import container

export, _ = recorder.load_source("test_export", Path(__file__).parent / "pilot/export.py")


def encoded(value):
    return json.dumps(value).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def packed(files):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w") as archive:
        for name, value in files.items():
            entry = tarfile.TarInfo(name)
            if isinstance(value, tarfile.TarInfo):
                archive.addfile(value)
            else:
                entry.size = len(value)
                archive.addfile(entry, io.BytesIO(value))
    return result.getvalue()


def fixture(*, oci=False, compressed=False, user="65532:65532", platform="amd64"):
    layer = packed({"payload": b"synthetic model"})
    config = encoded({"os": "linux", "architecture": platform, "config": {"User": user},
                      "rootfs": {"type": "layers", "diff_ids": ["sha256:" + sha(layer)]}})
    layer = gzip.compress(layer, mtime=0) if compressed else layer
    config_name = "blobs/sha256/" + sha(config) if oci else sha(config) + ".json"
    layer_name = "blobs/sha256/" + sha(layer) if oci else "layer/layer.tar"
    files = {config_name: config, layer_name: layer,
             "manifest.json": encoded([{"Config": config_name, "Layers": [layer_name], "RepoTags": None}])}
    identity = "sha256:" + sha(config)
    if oci:
        files["oci-layout"] = encoded({"imageLayoutVersion": "1.0.0"})
        manifest = encoded({"schemaVersion": 2, "config": {"digest": identity, "size": len(config)},
                            "layers": [{"digest": "sha256:" + sha(layer), "size": len(layer)}]})
        files["blobs/sha256/" + sha(manifest)] = manifest
        identity = "sha256:" + sha(manifest)
        files["index.json"] = encoded({"schemaVersion": 2, "manifests": [
            {"digest": identity, "size": len(manifest)}]})
    return files, identity


class ArchiveTests(unittest.TestCase):
    def test_classic_and_oci_bind_exact_identity_and_layers(self):
        for oci in (False, True):
            for compressed in (False, True):
                files, identity = fixture(oci=oci, compressed=compressed)
                data = packed(files)
                result = export.validate_archive(io.BytesIO(data), identity)
                self.assertEqual(result["image_id"], identity)
                self.assertEqual(result["sha256"], sha(data))
                self.assertEqual(result["bytes"], len(data))
                self.assertEqual(result["layers"], 1)

    def test_nested_single_image_oci_index(self):
        files, _ = fixture(oci=True)
        nested = files["index.json"]
        identity = "sha256:" + sha(nested)
        files["blobs/sha256/" + sha(nested)] = nested
        files["index.json"] = encoded({"manifests": [{"digest": identity, "size": len(nested)}]})
        self.assertEqual(export.validate_archive(io.BytesIO(packed(files)), identity)["image_id"], identity)

    def test_identity_user_platform_and_limits_fail_closed(self):
        for options in ({}, {"user": "0"}, {"platform": "arm64"}):
            files, identity = fixture(**options)
            with self.assertRaises(ValueError):
                export.validate_archive(io.BytesIO(packed(files)), identity if options else "sha256:" + "0" * 64)
        files, identity = fixture(compressed=True)
        for limit in ("MAX_ARCHIVE", "MAX_EXPANDED", "MAX_JSON"):
            with self.subTest(limit=limit), patch.object(export, limit, 1), self.assertRaises(ValueError):
                export.validate_archive(io.BytesIO(packed(files)), identity)
        with patch.object(export, "TIMEOUT", -1), self.assertRaises(ValueError):
            export.validate_archive(io.BytesIO(packed(files)), identity)

    def test_tags_multiple_images_and_oci_disagreement_rejected(self):
        for mutation in ("tag", "images", "repositories", "oci-tag", "oci-name", "oci-images", "oci-size", "oci-digest"):
            files, identity = fixture(oci=True)
            manifest = json.loads(files["manifest.json"])
            index = json.loads(files["index.json"])
            descriptor = index["manifests"][0]
            if mutation == "tag":
                manifest[0]["RepoTags"] = ["unrelated:tag"]
            elif mutation == "images":
                manifest *= 2
            elif mutation == "repositories":
                files["repositories"] = encoded({"unrelated": {"tag": "other-image"}})
            elif mutation in {"oci-tag", "oci-name"}:
                key = "org.opencontainers.image.ref.name" if mutation == "oci-tag" else "io.containerd.image.name"
                descriptor["annotations"] = {key: "unrelated:tag"}
            elif mutation == "oci-images":
                index["manifests"] *= 2
            elif mutation == "oci-size":
                descriptor["size"] += 1
            else:
                descriptor["digest"] = "sha256:" + "0" * 64
            files["manifest.json"], files["index.json"] = encoded(manifest), encoded(index)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                export.validate_archive(io.BytesIO(packed(files)), identity)

    def test_corrupt_layers_missing_config_and_duplicate_members(self):
        for mutation in ("layer", "config", "duplicate", "blob"):
            files, identity = fixture(oci=mutation == "blob")
            manifest = json.loads(files["manifest.json"])[0]
            if mutation in {"layer", "blob"}:
                files[manifest["Layers"][0]] = b"corrupt"
            elif mutation == "config":
                del files[manifest["Config"]]
            else:
                entry = tarfile.TarInfo("manifest.json")
                files["duplicate"] = entry
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                export.validate_archive(io.BytesIO(packed(files)), identity)

    def test_traversal_links_and_special_members(self):
        for name, kind in (("../escape", tarfile.REGTYPE), ("/absolute", tarfile.REGTYPE),
                           ("a/../b", tarfile.REGTYPE), ("link", tarfile.SYMTYPE),
                           ("hardlink", tarfile.LNKTYPE), ("fifo", tarfile.FIFOTYPE)):
            files, identity = fixture()
            entry = tarfile.TarInfo(name)
            entry.type, entry.linkname = kind, "manifest.json"
            files[name] = entry
            with self.subTest(name=name), self.assertRaises(ValueError):
                export.validate_archive(io.BytesIO(packed(files)), identity)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.destination = self.root / "bundle"
        self.summary = {"result": "pass", "container_cleanup": {"result": "pass"},
                        "runtime": {"result": "pass"}, "container_inputs": {"fixture": True}}
        self.files, self.identity = fixture()
        self.data = packed(self.files)

    def bundle(self):
        return export.Bundle(self.destination, {"git_sha": "a" * 40, "archive_sha256": "b" * 64}, self.summary)

    def ready(self, bundle):
        bundle.write("image.tar", self.data)
        bundle.archive = export.validate_archive(io.BytesIO(self.data), self.identity)
        self.summary["export"]["load_checked"] = True
        bundle.license_files = {"licenses/fixture/LICENSE": bundle.write("licenses/fixture/LICENSE", b"license")}

    def test_private_atomic_bundle_and_manifest(self):
        with self.bundle() as bundle:
            self.ready(bundle)
            self.assertFalse(self.destination.exists())
            bundle.publish()
        manifest = json.loads((self.destination / "manifest.json").read_text())
        self.assertEqual(manifest["archive"]["image_id"], self.identity)
        self.assertEqual(manifest["source"]["git_sha"], "a" * 40)
        self.assertFalse(manifest["confidential"])
        self.assertTrue(manifest["local_only"])
        for path in [self.destination, *self.destination.rglob("*")]:
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)
        self.assertEqual(self.summary["export"]["result"], "pass")

    def test_existing_file_directory_and_dangling_link_never_overwritten(self):
        for kind in ("file", "directory", "symlink"):
            if kind == "file":
                self.destination.write_bytes(b"untouched")
            elif kind == "directory":
                self.destination.mkdir()
            else:
                self.destination.symlink_to(self.root / "absent")
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.bundle()
            if kind == "directory":
                self.destination.rmdir()
            else:
                self.destination.unlink()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_publish_collision_never_replaces_empty_directory(self):
        with self.assertRaises(OSError):
            with self.bundle() as bundle:
                self.ready(bundle)
                self.destination.mkdir()
                original = self.destination.stat().st_ino
                bundle.publish()
        self.assertEqual(self.destination.stat().st_ino, original)
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assertEqual(list(self.root.iterdir()), [self.destination])

    def test_parent_symlink_public_parent_and_traversal_rejected(self):
        link = self.root / "link"
        link.symlink_to(self.root, target_is_directory=True)
        for destination in (link / "bundle", self.root / ".." / "escape", Path("relative")):
            with self.subTest(destination=destination), self.assertRaises((OSError, ValueError)):
                export.Bundle(destination, {}, {})
        self.root.chmod(0o755)
        with self.assertRaises(ValueError):
            self.bundle()
        self.root.chmod(0o700)

    def test_staged_symlink_never_read_or_overwritten(self):
        outside = self.root / "outside"
        outside.write_bytes(b"untouched")
        with self.bundle() as bundle:
            (bundle.path / "image.tar").symlink_to(outside)
            for create in (False, True):
                with self.assertRaises(OSError), bundle.file("image.tar", create=create):
                    self.fail("followed symlink")
            with self.assertRaises(ValueError), bundle.file("../escape", create=True):
                self.fail("escaped staging")
        self.assertEqual(outside.read_bytes(), b"untouched")
        self.assertFalse(self.destination.exists())

    def test_stale_archive_license_failed_proof_or_cleanup_cannot_publish(self):
        for mutation in ("archive", "license", "proof", "cleanup"):
            with self.subTest(mutation=mutation), self.assertRaises((ValueError, tarfile.TarError)):
                with self.bundle() as bundle:
                    self.ready(bundle)
                    if mutation == "archive":
                        (bundle.path / "image.tar").write_bytes(b"changed")
                    elif mutation == "license":
                        (bundle.path / "licenses/fixture/LICENSE").write_bytes(b"changed")
                    elif mutation == "proof":
                        self.summary["result"] = "fail"
                    else:
                        self.summary["container_cleanup"]["result"] = "fail"
                    bundle.publish()
            self.summary["result"] = self.summary["container_cleanup"]["result"] = "pass"
            self.assertEqual(list(self.root.iterdir()), [])

    def test_late_failure_rolls_back_published_bundle(self):
        with self.assertRaises(OSError):
            with self.bundle() as bundle:
                self.ready(bundle)
                bundle.publish()
                raise OSError("evidence write failed")
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.summary["export"]["published"])

    def test_partial_cleanup_failure_is_explicit(self):
        bundle = self.bundle()
        try:
            with patch.object(export.shutil, "rmtree", side_effect=OSError("fixture")), \
                    patch.object(export.sys, "stderr", new_callable=io.StringIO) as diagnostic:
                with self.assertRaises(OSError):
                    bundle.abort()
                self.assertIn("cleanup unproven", diagnostic.getvalue())
            self.assertEqual(self.summary["export"]["cleanup"], "fail")
            self.assertFalse(self.summary["export"]["published"])
        finally:
            bundle.__exit__(None, None, None)

    def test_replaced_parent_is_not_used_and_anchored_staging_is_cleaned(self):
        parent = self.root / "parent"
        parent.mkdir(mode=0o700)
        self.destination = parent / "bundle"
        with self.assertRaises(ValueError):
            with self.bundle() as bundle:
                self.ready(bundle)
                parent.rename(self.root / "old-parent")
                parent.mkdir(mode=0o700)
                bundle.publish()
        self.assertEqual(list((self.root / "old-parent").iterdir()), [])
        self.assertEqual(list(parent.iterdir()), [])

    def test_save_uses_exact_id_and_validates_load_before_publication(self):
        calls = []

        def execute(args, **kwargs):
            calls.append(args)
            self.assertLessEqual(kwargs["timeout"], 300)
            self.assertIn("max_output_bytes", kwargs)
            if args[2] == "save":
                self.assertEqual(args[-1], self.identity)
                kwargs["stdout_file"].write(self.data)
            elif args[2] == "load":
                loaded = container.snapshot.execute_process_group(
                    [sys.executable, "-c", "import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())"],
                    stdin=kwargs["stdin"], env=os.environ, max_output_bytes=1024, timeout=5)
                loaded.check_returncode()
                self.assertEqual(loaded.stdout.strip().decode(), sha(self.data))
            else:
                return subprocess.CompletedProcess(args, 0, (self.identity + " linux/amd64\n").encode())
            return subprocess.CompletedProcess(args, 0, b"")

        with self.bundle() as bundle, patch.object(bundle, "licenses", return_value={}):
            bundle.save(self.identity, self.root, self.root, {}, execute, {})
            self.assertFalse(self.destination.exists())
            self.assertEqual(self.summary["export"]["result"], "verified-pending-cleanup")
            bundle.publish()
        self.assertEqual([args[2] for args in calls], ["save", "load", "inspect"])

    def test_save_load_inspect_failures_and_timeouts_leave_no_bundle(self):
        for failure in ("save", "load", "inspect", "timeout", "mismatch"):
            def execute(args, **kwargs):
                if args[2] == "save":
                    kwargs["stdout_file"].write(self.data)
                if failure == "timeout" and args[2] == "load":
                    raise subprocess.TimeoutExpired(args, 300)
                if args[2] == failure:
                    return subprocess.CompletedProcess(args, 1, b"")
                return subprocess.CompletedProcess(args, 0, b"mismatch" if failure == "mismatch" else b"")

            with self.subTest(failure=failure), self.assertRaises((ValueError, subprocess.SubprocessError)):
                with self.bundle() as bundle, patch.object(bundle, "licenses", return_value={}):
                    bundle.save(self.identity, self.root, self.root, {}, execute, {})
            self.assertEqual(list(self.root.iterdir()), [])
            self.assertFalse(self.summary["export"]["published"])

    def test_real_model_license_pin_and_nested_notices(self):
        for component in ("trcs", "engine"):
            path = self.root / component
            path.mkdir()
            (path / "LICENSE").write_bytes(b"fixture license")
            (path / "NOTICE").write_bytes(b"fixture notice")
            (path / "vendor").mkdir()
            (path / "vendor/LICENSE-MIT").write_bytes(b"fixture nested license")
        pins = {"model": {"revision": export.MODEL_LICENSE_REVISION, "repository": export.MODEL_LICENSE_REPOSITORY}}
        with self.bundle() as bundle:
            licenses = bundle.licenses(container.ROOT, self.root, pins)
            self.assertEqual(licenses["licenses/qwen/LICENSE"]["sha256"], export.MODEL_LICENSE_SHA256)
            self.assertIn("licenses/nib/NOTICE", licenses)
            self.assertIn("licenses/trcs/NOTICE", licenses)
            # Nested license naming conventions include extensions and suffixes.
            self.assertIn("licenses/llama/vendor/LICENSE-MIT", licenses)
            pins["model"]["revision"] = "changed"
            with self.assertRaisesRegex(ValueError, "revision"):
                bundle.licenses(container.ROOT, self.root, pins)


class LifecycleTests(unittest.TestCase):
    def test_export_follows_pass_and_precedes_cleanup(self):
        runtime = {"execution": "real-cpu-model", "confidential": False, "hardware_validated": False,
                   "result": "pass", "runtime_platform": {"system": "Linux", "machine": "x86_64"}}
        for failure in (None, "proof", "load-timeout", "cleanup", "stale-input", "dangling-image", "shared-image", "inventory"):
            summary = {"result": "fail", "export": {}}
            artifact = Mock()
            runtime["result"] = "fail" if failure == "proof" else "pass"
            events = []

            def execute(args, **kwargs):
                if args[1] == "build":
                    Path(args[args.index("--iidfile") + 1]).write_text("sha256:" + "a" * 64)
                    return subprocess.CompletedProcess(args, 0)
                events.append("proof")
                return subprocess.CompletedProcess(args, int(failure == "proof"), encoded(runtime))

            def save(identity, *args):
                self.assertEqual(identity, "sha256:" + "a" * 64)
                events.append("save")
                if failure == "load-timeout":
                    summary["export"]["load_started"] = True
                    raise subprocess.TimeoutExpired(["docker", "image", "load"], 300)

            def cleanup(*args, **kwargs):
                events.append("cleanup")
                return {"result": "fail" if failure == "cleanup" else "pass"}

            artifact.save.side_effect = save
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                stack.enter_context(patch.object(container.snapshot, "local_docker_endpoint", return_value="unix:///local"))
                stack.enter_context(patch.object(container.snapshot, "require_buildkit"))
                inventories = [{"sha256:" + "a" * 64} if failure == "shared-image" else set(),
                               {"sha256:" + "a" * 64} if failure in {"shared-image", "dangling-image"} else set()]
                if failure == "inventory":
                    inventories[1] = ValueError("inventory failed")
                stack.enter_context(patch.object(container, "image_ids", side_effect=inventories))
                context = stack.enter_context(patch.object(container, "build_context", return_value={
                    "base_images": {"platform": "linux/amd64", "go_image": "go", "python_image": "python"}, "model": {}}))
                if failure == "stale-input":
                    context.side_effect = container.pilot.PilotError("input-digest")
                stack.enter_context(patch.object(container.snapshot, "execute_process_group", side_effect=execute))
                stack.enter_context(patch.object(container.e2e, "cleanup_resources", side_effect=cleanup))
                container.run(Path(directory), summary, {}, Path(directory), export=artifact)
            self.assertEqual(summary["result"], "pass" if failure in {None, "shared-image"} else "fail")
            self.assertEqual(events, ["cleanup"] if failure == "stale-input" else
                             ["proof", "cleanup"] if failure == "proof" else ["proof", "save", "cleanup"])
            artifact.publish.assert_not_called()
            if failure == "load-timeout":
                self.assertEqual(summary["container_cleanup"]["load_completion"], "unknown")
            if failure in {"shared-image", "dangling-image", "inventory"}:
                self.assertEqual(summary["container_cleanup"]["exported_images"]["result"],
                                 "pass" if failure == "shared-image" else "fail")

    def test_inventory_is_read_only_bounded_and_fail_closed(self):
        identity = "sha256:" + "a" * 64
        for output, code in ((identity.encode() + b"\n", 0), (b"", 0), (b"short\n", 0), (b"", 1)):
            with patch.object(container.snapshot, "execute_process_group",
                              return_value=subprocess.CompletedProcess([], code, output)) as execute:
                if code or output == b"short\n":
                    with self.assertRaises((container.pilot.PilotError, subprocess.SubprocessError)):
                        container.image_ids({})
                else:
                    self.assertEqual(container.image_ids({}), {identity} if output else set())
                self.assertEqual(execute.call_args.args[0], ["docker", "image", "ls", "--all", "--quiet", "--no-trunc"])
                self.assertEqual(execute.call_args.kwargs["max_output_bytes"], 65536)

    def test_streaming_output_is_bounded_and_process_reaped(self):
        for mode in ("stdout", "stderr", "timeout"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                code = "import os,time; open('pid','w').write(str(os.getpid())); "
                code += "time.sleep(60)" if mode == "timeout" else f"os.write({1 if mode == 'stdout' else 2}, b'x'*70000)"
                with (root / "output").open("wb") as output, self.assertRaises((ValueError, subprocess.TimeoutExpired)):
                    container.snapshot.execute_process_group([sys.executable, "-c", code], env=os.environ,
                        cwd=root, timeout=0.5, stdout_file=output, max_output_bytes=1024)
                self.assertLessEqual((root / "output").stat().st_size, 1024)
                with self.assertRaises(ProcessLookupError):
                    os.killpg(int((root / "pid").read_text()), 0)

    def test_streaming_success_and_bounded_capture(self):
        with tempfile.TemporaryFile() as output:
            result = container.snapshot.execute_process_group(
                [sys.executable, "-c", "import os; os.write(1,b'archive'); os.write(2,b'diagnostic')"],
                env=os.environ, timeout=5, stdout_file=output, max_output_bytes=1024)
            result.check_returncode()
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"diagnostic")
            output.seek(0)
            self.assertEqual(output.read(), b"archive")


class RecorderTests(unittest.TestCase):
    def test_publication_requires_snapshot_schema_cleanup_and_evidence(self):
        for failure in (None, "snapshot", "schema", "cleanup", "evidence", "publication", "collision", "partial-cleanup"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                root = Path(directory).resolve()
                destination = root / "bundle"
                evidence = root / "evidence/p3/2026-10-19-test-export"
                events = []
                files, identity = fixture()
                source_loader = recorder.load_source
                artifact = None

                def runner(inputs, report, env, home, *, export):
                    nonlocal artifact
                    artifact = export
                    events.append("proof")
                    report.update(result="pass", runtime={"result": "pass"}, container_inputs={},
                                  container_cleanup={"result": "fail" if failure == "cleanup" else "pass"})
                    if failure == "cleanup":
                        report["result"] = "fail"
                    artifact.write("image.tar", packed(files))
                    artifact.archive = export_module.validate_archive(io.BytesIO(packed(files)), identity)
                    artifact.license_files = {}
                    report["export"]["load_checked"] = True
                    if failure == "publication":
                        artifact.publish = Mock(side_effect=ValueError("fixture"))
                    if failure == "partial-cleanup":
                        report["result"] = "fail"

                        def incomplete_cleanup():
                            report["export"].update(result="fail", cleanup="fail")
                            raise OSError("fixture incomplete cleanup")

                        artifact.abort = incomplete_cleanup

                export_module = export

                def load(name, path):
                    if path.name == "container.py":
                        return Mock(run=runner), b""
                    return source_loader(name, path)

                def snapshot(repository, source, control, env):
                    (source / "scripts/pilot").mkdir(parents=True)
                    (source / "scripts/pilot/export.py").write_bytes(Path(export.__file__).read_bytes())
                    return "a" * 40, {}, "b" * 64

                def check_snapshot(*args):
                    events.append("snapshot")
                    self.assertEqual(destination.exists(), failure == "collision")
                    if failure == "snapshot":
                        raise ValueError("snapshot changed")

                def schema(*args):
                    events.append("schema")
                    self.assertEqual(destination.exists(), failure == "collision")
                    if failure == "schema":
                        raise ValueError("invalid evidence")

                def command(args, **kwargs):
                    if args[1:3] == ["evidence", "new"]:
                        events.append("evidence")
                        if failure == "evidence":
                            raise OSError("evidence failed")
                        evidence.mkdir(parents=True)
                        return subprocess.CompletedProcess(args, 0, evidence.relative_to(root).as_posix())
                    return subprocess.CompletedProcess(args, 0)

                stack.enter_context(patch.object(recorder, "ROOT", root))
                stack.enter_context(patch.object(recorder, "controlled_env", return_value={}))
                stack.enter_context(patch.object(recorder, "git", return_value=Mock(stdout=b"")))
                stack.enter_context(patch.object(recorder, "snapshot_commit", side_effect=snapshot))
                stack.enter_context(patch.object(recorder, "validate_bootstrap"))
                stack.enter_context(patch.object(recorder, "validate_snapshot", side_effect=check_snapshot))
                stack.enter_context(patch.object(recorder, "validate_record", side_effect=schema))
                stack.enter_context(patch.object(recorder, "load_source", side_effect=load))
                stack.enter_context(patch.object(recorder.subprocess, "run", side_effect=command))
                stack.enter_context(patch.object(sys, "argv", ["recorder", "--inputs", str(root),
                    "--slug", "test-export", "--linux-container", "--export-image", str(destination)]))
                stack.enter_context(patch("builtins.print"))
                if failure == "collision":
                    destination.mkdir()
                if failure in {"snapshot", "schema", "evidence", "partial-cleanup"}:
                    with self.assertRaises((ValueError, OSError)):
                        recorder.main()
                    if failure == "partial-cleanup":
                        report = json.loads((evidence / "output.log").read_text())
                        record = json.loads((evidence / "run.json").read_text())
                        self.assertEqual(report["failure_code"], "export-cleanup-unresolved")
                        self.assertEqual(report["export"]["cleanup"], "fail")
                        self.assertEqual(record["result"], "fail")
                        self.assertEqual(len(list(root.glob(".qwen-export-*"))), 1)
                else:
                    code = recorder.main()
                    self.assertEqual(code, 0 if failure is None else 1)
                    report = json.loads((evidence / "output.log").read_text())
                    record = json.loads((evidence / "run.json").read_text())
                    self.assertEqual(record["task"], "T8")
                    self.assertEqual(record["result"], report["result"])
                self.assertEqual(destination.exists(), failure in {None, "collision"})
                if failure != "partial-cleanup":
                    self.assertEqual(list(root.glob(".qwen-export-*")), [])
                if failure is None:
                    self.assertEqual(events, ["proof", "snapshot", "schema", "evidence"])
                    self.assertTrue(artifact.published)

    def test_export_requires_linux_container(self):
        with patch.object(sys, "argv", ["recorder", "--inputs", "/unused", "--slug", "test", "--export-image", "/unused"]), \
                patch.object(sys, "stderr", new_callable=io.StringIO), self.assertRaises(SystemExit) as error:
            recorder.main()
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
