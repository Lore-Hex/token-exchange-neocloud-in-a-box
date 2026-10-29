# SPDX-License-Identifier: Apache-2.0
"""Offline context/permission tests. No Docker, listener or hardware execution."""

from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import selected_service_image as image
import test_selected_service_process as process_fixture

ROOT, lab_contract = process_fixture.ROOT, process_fixture.lab_contract


class ImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lab = image.host_recorder()
        cls.recorder = cls.lab.load("service_image_test_application", ROOT / cls.lab.BUILDER,
                                   cls.lab.BUILDER_SHA).recorder

    @contextmanager
    def preparation(self, *, change=None):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root = Path(temporary).resolve()
            bundle = root / "lab"
            contract = lab_contract()
            contract.update(base_image=self.recorder.overlay.BASE_IMAGE,
                            official_config={"sha256": image.digest(b"synthetic base config")})
            payload = {"launch.py": image.owner.launcher_source(ROOT, selected=False),
                "pack/files.sha256": (ROOT / contract["model_profile"]["pack"] / "files.sha256").read_bytes(),
                "application/contract.json": b"{}", "nib": b"synthetic validator"}
            contract["sources"] = {name: image.digest(raw) for name, raw in payload.items()}
            if change:
                change(contract)
            payload["lab.json"] = image.encoded(contract)
            payload["python"] = image.owner.service.engine_lab.wrapper_bytes(contract["interpreter"]["resolved"],
                contract["environment"], image.digest(payload["lab.json"]))
            self.recorder.prep.publish(bundle, {**{"payload/" + name: raw for name, raw in payload.items()},
                "metadata/manifest.json": b"{}", "metadata/config.json": b"{}"})
            tls = b"synthetic pinned TLS binary"
            trcs = root / "trcs"
            trcs.write_bytes(tls)
            sources = {"scripts/selected_service_image.py": (ROOT / "scripts/selected_service_image.py").read_bytes(),
                "scripts/e2e/pins.json": image.encoded({"trcs": {"binary_sha256": image.digest(tls)}})}
            receipt = {"files": {"payload/" + name: {} for name in payload}}
            stack.enter_context(mock.patch.object(image, "host_recorder", return_value=self.lab))
            validation = stack.enter_context(mock.patch.object(self.lab, "bundle_inputs",
                return_value=(receipt, contract, {}, self.recorder)))
            stack.enter_context(mock.patch.object(image, "committed_sources", return_value=("0" * 40, sources)))
            compiler = stack.enter_context(mock.patch.object(image, "build_guard",
                return_value=(b"synthetic guard", image.digest(b"guard source"))))
            yield root, bundle, trcs, payload, validation, compiler

    def test_preparation_derives_new_payload_and_never_mutates_lab(self):
        with self.preparation() as (root, bundle, trcs, payload, validation, compiler):
            expected = image.digest(b"approved original receipt")
            output = root / "output"
            report = image.prepare(bundle, expected, trcs, output)
            validation.assert_called_once_with(bundle, expected)
            compiler.assert_called_once()
            for name, raw in payload.items():
                self.assertEqual((bundle / "payload" / name).read_bytes(), raw)
            receipt, files, contract = image.validated_files(output, report["receipt_sha256"])
            self.assertEqual(receipt["lab_receipt_sha256"], expected)
            self.assertFalse(receipt["image_built"])
            self.assertFalse(receipt["deployable"])
            self.assertEqual(contract["arguments"], json.loads(payload["lab.json"])["arguments"])
            self.assertEqual({name for name in payload if payload[name] != files["context/payload/" + name]},
                             {"launch.py", "lab.json", "python"})

    def test_rejects_nonselected_or_owner_payload_before_compile(self):
        for field, value in (("tau_owner", {}), ("stream_cohort", {}), ("provider_conformance", {}),
                             ("gpu_family", "B200"), ("execution_profile", "stream-c8")):
            with self.subTest(field=field), self.preparation(change=lambda lab: lab.update({field: value})) as fixture:
                root, bundle, trcs, _, _, compiler = fixture
                with self.assertRaises(ValueError):
                    image.prepare(bundle, image.digest(b"receipt"), trcs, root / "output")
                compiler.assert_not_called()

    def test_wrong_trcs_pin_rejected_before_compile(self):
        with self.preparation() as (root, bundle, trcs, _, _, compiler):
            trcs.write_bytes(b"not the pinned binary")
            with self.assertRaises(ValueError):
                image.prepare(bundle, image.digest(b"receipt"), trcs, root / "output")
            compiler.assert_not_called()
            self.assertFalse((root / "output").exists())

    def test_bad_original_receipt_stops_before_source_or_compiler(self):
        with self.preparation() as (root, bundle, trcs, _, validation, compiler):
            validation.side_effect = ValueError("synthetic rejected receipt")
            with self.assertRaises(ValueError):
                image.prepare(bundle, image.digest(b"receipt"), trcs, root / "output")
            compiler.assert_not_called()

    def test_preparation_cannot_overwrite_existing_destination(self):
        with self.preparation() as (root, bundle, trcs, _, _, _):
            output = root / "output"
            output.mkdir()
            marker = output / "keep"
            marker.write_bytes(b"existing operator data")
            with self.assertRaises(FileExistsError):
                image.prepare(bundle, image.digest(b"receipt"), trcs, output)
            self.assertEqual(marker.read_bytes(), b"existing operator data")

    def test_exact_context_rejects_tampering_extra_files_and_links(self):
        for change in ("source", "dockerfile", "extra", "symlink", "receipt"):
            with self.subTest(change=change), self.preparation() as (root, bundle, trcs, _, _, _):
                output = root / "output"
                report = image.prepare(bundle, image.digest(b"receipt"), trcs, output)
                paths = {"source": output / "context/service/scripts/selected_service_image.py",
                    "dockerfile": output / "context/Dockerfile", "receipt": output / "receipt.json",
                    "extra": output / "context/private-input"}
                if change == "symlink":
                    (output / "context/link").symlink_to(trcs)
                else:
                    paths[change].write_bytes(b"synthetic changed context")
                with self.assertRaises(ValueError):
                    image.validated_files(output, report["receipt_sha256"])

    def test_dockerfile_applies_overlay_and_seals_before_nonroot_entry(self):
        contract = lab_contract()
        contract.update(base_image=self.recorder.overlay.BASE_IMAGE,
                        sources={"application/contract.json": image.digest(b"application")})
        text = image.dockerfile(contract, image.digest(b"preparation")).decode()
        self.assertIn("FROM " + self.recorder.overlay.BASE_IMAGE, text)
        self.assertEqual(text.count("RUN --network=none"), 2)
        self.assertLess(text.index(" --apply"), text.index(" seal "))
        self.assertLess(text.index(" seal "), text.index("USER 65532:65532"))
        self.assertNotIn("EXPOSE", text)
        self.assertNotIn("/run/operator", text)
        self.assertIn('/usr/bin/python3.12", "-I", "-S", "-B"', text)
        contract["interpreter"]["resolved"] = "/usr/local/bin/python3"
        with self.assertRaises(ValueError):
            image.dockerfile(contract, image.digest(b"preparation"))

    def test_optional_import_step_is_exact_nonroot_and_default_bytes_unchanged(self):
        contract = lab_contract()
        contract.update(base_image="vllm/vllm-openai@sha256:" + "1" * 64,
                        sources={"application/contract.json": "2" * 64})
        old = image.dockerfile(contract, "3" * 64)
        self.assertEqual(image.digest(old), "18b55b9192c32d997bd9986da3d4f8f249346352286c789329e0e244875cc4b4")
        line = ("RUN --network=none /usr/bin/python3.12 -I -S -B "
            "/opt/nib-service/scripts/selected_service_image.py checker-imports --prepared-sha256 "
            + "3" * 64 + "\n").encode()
        self.assertEqual(image.dockerfile(contract, "3" * 64, checker=True),
                         old.replace(b"USER 65532:65532\n", b"USER 65532:65532\n" + line))
        for value in (None, 1, "true"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                image.dockerfile(contract, "3" * 64, checker=value)

    def test_rehashed_receipt_still_rejects_bad_types_and_unknown_claims(self):
        for change in ("bytes", "hash", "git", "claim", "extra"):
            with self.subTest(change=change), self.preparation() as (root, bundle, trcs, _, _, _):
                output = root / "prepared"
                image.prepare(bundle, image.digest(b"receipt"), trcs, output)
                receipt = json.loads((output / "receipt.json").read_bytes())
                if change == "bytes":
                    receipt["files"]["metadata/config.json"]["bytes"] = True
                elif change == "hash":
                    receipt["guard_source_sha256"] = "private-canary"
                elif change == "git":
                    receipt["git_sha"] = 0
                elif change == "claim":
                    receipt["hardware_validated"] = True
                else:
                    receipt["arbitrary"] = "private-canary"
                raw = image.encoded(receipt)
                (output / "receipt.json").write_bytes(raw)
                with self.assertRaises(ValueError):
                    image.validated_files(output, image.digest(raw))

    def test_seal_cli_imports_in_image_only_source_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for source in (ROOT / "scripts").rglob("*.py"):
                target = root / source.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            shutil.copyfile(ROOT / "scripts/e2e/pins.json", root / "scripts/e2e/pins.json")
            result = subprocess.run([sys.executable, "-I", "-S", "-B",
                str(root / "scripts/selected_service_image.py"), "seal", "--help"],
                cwd=root, env={"PATH": os.defpath}, capture_output=True, timeout=20, check=False)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertIn(b"--prepared-sha256", result.stdout)
            self.assertEqual({path.name for path in root.iterdir()}, {"scripts"})
            self.assertFalse(list(root.rglob("*.pyc")))

    def test_seal_public_files_without_touching_private_inputs(self):
        with process_fixture.PackagingTests().prepared_fixture() as (root, payload, expected), \
                mock.patch.object(image.os, "getuid", return_value=0):
            private = root.parent / "operator-key"
            private.write_bytes(b"synthetic private-input canary")
            private.chmod(0o600)
            try:
                image.seal(expected)
                self.assertEqual(stat.S_IMODE((root / "bin/trcs").stat().st_mode), 0o555)
                self.assertEqual(stat.S_IMODE((root / "scripts/engine_lab.py").stat().st_mode), 0o444)
                self.assertEqual(stat.S_IMODE((payload / "lab.json").stat().st_mode), 0o444)
                self.assertEqual(stat.S_IMODE((payload / "python").stat().st_mode), 0o555)
                self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o600)
                self.assertEqual(private.read_bytes(), b"synthetic private-input canary")
            finally:
                for base in (root, payload):
                    base.chmod(0o700)
                    for directory, dirs, _ in os.walk(base):
                        for name in dirs:
                            (Path(directory) / name).chmod(0o700)

    def test_seal_hash_failure_changes_no_modes(self):
        with process_fixture.PackagingTests().prepared_fixture() as (root, payload, expected), \
                mock.patch.object(image.os, "getuid", return_value=0):
            (payload / "launch.py").write_bytes(b"tampered public source")
            mode = (root / "bin/trcs").stat().st_mode
            with self.assertRaises(ValueError):
                image.seal(expected)
            self.assertEqual((root / "bin/trcs").stat().st_mode, mode)

    def test_seal_rejects_outside_source_before_read_or_chmod(self):
        for name in ("/outside", "../outside", "nested/../outside"):
            with self.subTest(name=name), process_fixture.PackagingTests().prepared_fixture() as fixture, \
                    mock.patch.object(image.os, "getuid", return_value=0), \
                    mock.patch.object(image, "read", wraps=image.read) as read:
                root, payload, _ = fixture
                lab = json.loads((payload / "lab.json").read_bytes())
                lab["sources"][name] = image.digest(b"synthetic private canary")
                raw = image.encoded(lab)
                (payload / "lab.json").write_bytes(raw)
                prepared = json.loads((root / "prepared.json").read_bytes())
                prepared["lab_sha256"] = image.digest(raw)
                raw = image.encoded(prepared)
                (root / "prepared.json").write_bytes(raw)
                mode = (root / "bin/trcs").stat().st_mode
                with self.assertRaises(ValueError):
                    image.seal(image.digest(raw))
                self.assertEqual((root / "bin/trcs").stat().st_mode, mode)
                self.assertNotIn(str(payload / name), [call.args[0] for call in read.call_args_list])

    def test_guard_build_is_offline_exact_platform_and_pinned_toolchain(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory).resolve()
            command = mock.Mock()
            def compile_guard(args, env, **kwargs):
                self.assertEqual(env["GOPROXY"], "off")
                self.assertEqual(env["GOSUMDB"], "off")
                self.assertEqual(env["GOTOOLCHAIN"], "local")
                self.assertEqual((env["GOOS"], env["GOARCH"], env["CGO_ENABLED"]), ("linux", "amd64", "0"))
                self.assertEqual(args[0], "/pinned/go")
                self.assertEqual(args[-1], "./cmd/nib-pilot-api")
                command(args)
                raw = bytearray(64)
                raw[:6], raw[18:20] = b"\x7fELF\x02\x01", b"\x3e\x00"
                Path(args[args.index("-o") + 1]).write_bytes(raw)
            with mock.patch.object(self.recorder, "validator_toolchain", return_value=("/pinned/go", {})), \
                    mock.patch.object(self.recorder, "call", side_effect=compile_guard):
                image.build_guard(self.recorder, {"go.mod": b"module synthetic\n",
                    "scripts/excluded.py": b"not compiler input"}, work, {})
            command.assert_called_once()
            self.assertFalse((work / "guard-source/scripts").exists())

    @contextmanager
    def fake_build(self, output, *, failure=None, references="expected"):
        with ExitStack() as stack:
            recorder = self.recorder
            stack.enter_context(mock.patch.object(self.lab, "load", return_value=SimpleNamespace(recorder=recorder)))
            metadata = {"rootfs": {"diff_ids": ["sha256:" + image.digest(b"base layer")]}}
            identifier = "sha256:" + image.digest(b"derived image")
            prepared = json.loads((output / "receipt.json").read_bytes())["prepared_sha256"]
            derived = {"Id": identifier, "Os": "linux", "Architecture": "amd64",
                "Config": {"User": "65532:65532", "Entrypoint": ["/usr/bin/python3.12", "-I", "-S", "-B",
                    "/opt/nib-service/scripts/selected_service_process.py", "--prepared-sha256", prepared],
                    "Cmd": [], "Volumes": None},
                "RootFS": {"Type": "layers", "Layers": metadata["rootfs"]["diff_ids"] +
                    ["sha256:" + image.digest(b"application layer")]}}
            stack.enter_context(mock.patch.object(recorder.snapshot, "require_buildkit"))
            stack.enter_context(mock.patch.object(recorder, "raw_metadata", return_value=metadata))
            lab = json.loads((output / "context/payload/lab.json").read_bytes())
            base_binding = {"manifest_sha256": lab["base_image"].rsplit("sha256:", 1)[1],
                "raw_config_sha256": lab["official_config"]["sha256"],
                "docker_reported_id": "sha256:" + image.digest(b"base image"), "platform": "linux/amd64",
                "binding": "local-manifest-reference-platform-and-diffids", "raw_config_link_verified": True,
                "local_config_bytes_rehashed": False, "local_layers_rehashed": False,
                "daemon_os_interpreter_trusted": True}
            binding = stack.enter_context(mock.patch.object(recorder, "image_binding", return_value=base_binding))
            inspect = stack.enter_context(mock.patch.object(recorder, "docker_json", side_effect=[{}, derived]))
            def invoke(args, env, **kwargs):
                self.assertEqual(env["DOCKER_HOST"], "unix:///var/run/docker.sock")
                self.assertEqual(env["DOCKER_BUILDKIT"], "1")
                self.assertEqual(args[:2], ["docker", "build"])
                for flag in ("--pull=false", "--network=none", "--no-cache"):
                    self.assertIn(flag, args)
                self.assertNotIn("--push", args)
                self.assertEqual(args.count("--tag"), 1)
                reference = args[args.index("--tag") + 1]
                self.assertRegex(reference, r"^nib-build[.]invalid/selected-service:[0-9a-f]{32}$")
                self.assertNotIn("--provenance=false", args)
                derived["RepoTags"] = [reference] if references == "expected" else references
                self.assertEqual(args[args.index("--platform") + 1], "linux/amd64")
                self.assertEqual(kwargs["timeout"], 1800)
                if failure:
                    raise failure
                Path(args[args.index("--iidfile") + 1]).write_text(identifier + "\n")
                return b""
            call = stack.enter_context(mock.patch.object(recorder, "call", side_effect=invoke))
            yield binding, inspect, call, derived

    def test_build_keeps_runtime_and_hardware_unproven(self):
        with self.preparation() as (root, bundle, trcs, _, _, _):
            output = root / "prepared"
            prepared = image.prepare(bundle, image.digest(b"receipt"), trcs, output)
            with self.fake_build(output) as (_, _, call, _):
                report = image.build(output, prepared["receipt_sha256"], root / "built", "unix:///var/run/docker.sock")
            self.assertEqual(report["result"], "pass")
            call.assert_called_once()
            for key in ("runtime_started", "hardware_validated", "deployable", "published"):
                self.assertIs(report[key], False)
            self.assertEqual(json.loads((root / "built/build.json").read_bytes()), report)

    def test_build_reference_is_fresh_and_wrong_readback_fails(self):
        references = []
        for readback in ("expected", "expected", None, [], ["foreign:tag"], ["foreign:tag", "other:tag"]):
            with self.subTest(readback=readback), self.preparation() as (root, bundle, trcs, _, _, _):
                output = root / "prepared"
                prepared = image.prepare(bundle, image.digest(b"receipt"), trcs, output)
                with self.fake_build(output, references=readback) as (_, _, call, _):
                    report = image.build(output, prepared["receipt_sha256"], root / "built", "unix:///var/run/docker.sock")
                args = call.call_args.args[0]
                references.append(args[args.index("--tag") + 1])
                self.assertEqual(report["result"], "pass" if readback == "expected" else "fail")
                self.assertFalse(report["runtime_started"])
                self.assertFalse(report["published"])
        self.assertEqual(len(references), len(set(references)))

    def test_host_handoff_binds_exact_build_receipt_without_current_head_lookup(self):
        with self.preparation() as (root, bundle, trcs, _, _, _):
            output = root / "prepared"
            prepared = image.prepare(bundle, image.digest(b"receipt"), trcs, output)
            with self.fake_build(output):
                image.build(output, prepared["receipt_sha256"], root / "built", "unix:///var/run/docker.sock")
            receipt, _, contract = image.validated_files(output, prepared["receipt_sha256"])
            raw = (root / "built/build.json").read_bytes()
            with mock.patch.object(image, "committed_sources", side_effect=AssertionError("no HEAD requirement")):
                accepted = image.validate_build_record(raw, image.digest(raw), receipt,
                                                      prepared["receipt_sha256"], contract)
                self.assertTrue(accepted["image_built"])
            for changed in (raw + b"\n", b"{}", raw[:10]):
                with self.assertRaises(ValueError):
                    image.validate_build_record(changed, image.digest(raw), receipt,
                                                prepared["receipt_sha256"], contract)

    def test_rehashed_build_record_rejects_claims_lineage_and_binding_changes(self):
        with self.preparation() as (root, bundle, trcs, _, _, _):
            output = root / "prepared"
            prepared = image.prepare(bundle, image.digest(b"receipt"), trcs, output)
            with self.fake_build(output):
                image.build(output, prepared["receipt_sha256"], root / "built", "unix:///var/run/docker.sock")
            receipt, _, contract = image.validated_files(output, prepared["receipt_sha256"])
            original = (root / "built/build.json").read_bytes()
            changes = [{"result": "fail"}, {"image_built": 1}, {"build_completed": False},
                {"image_id": "mutable-tag"}, {"preparation_sha256": image.digest(b"other preparation")},
                {"prepared_sha256": image.digest(b"other service")}, {"unexpected": "private-canary"}]
            changes += [{key: True} for key in ("runtime_started", "hardware_validated", "deployable", "published")]
            changes += [{"base_binding": {**json.loads(original)["base_binding"], key: value}} for key, value in (
                ("platform", "linux/arm64"), ("manifest_sha256", image.digest(b"other base")),
                ("raw_config_sha256", image.digest(b"other config")), ("raw_config_link_verified", 1),
                ("local_layers_rehashed", True), ("docker_reported_id", "mutable-tag"), ("extra", "private-canary"))]
            for change in changes:
                with self.subTest(change=change):
                    raw = image.encoded({**json.loads(original), **change})
                    with self.assertRaises(ValueError):
                        image.validate_build_record(raw, image.digest(raw), receipt,
                                                    prepared["receipt_sha256"], contract)

    def test_build_rejects_missing_or_wrong_base_before_build(self):
        for failure in ("missing", "binding"):
            with self.subTest(failure=failure), self.preparation() as (root, bundle, trcs, _, _, _):
                output = root / "prepared"
                prepared = image.prepare(bundle, image.digest(b"receipt"), trcs, output)
                with self.fake_build(output) as (binding, inspect, call, _):
                    if failure == "missing":
                        inspect.side_effect = subprocess.CalledProcessError(1, ["docker"])
                    else:
                        binding.side_effect = ValueError("not the pinned image")
                    report = image.build(output, prepared["receipt_sha256"], root / "built", "unix:///var/run/docker.sock")
                self.assertEqual(report["result"], "fail")
                self.assertFalse(report["build_started"])
                call.assert_not_called()

    def test_lost_build_completion_remains_unknown_without_output_canary(self):
        with self.preparation() as (root, bundle, trcs, _, _, _):
            output = root / "prepared"
            prepared = image.prepare(bundle, image.digest(b"receipt"), trcs, output)
            failure = subprocess.TimeoutExpired(["docker"], 1800, output=b"private-output-canary")
            with self.fake_build(output, failure=failure):
                report = image.build(output, prepared["receipt_sha256"], root / "built", "unix:///var/run/docker.sock")
            self.assertEqual(report["result"], "fail")
            self.assertFalse(report["build_completed"])
            self.assertEqual(report["cleanup"], "unknown")
            self.assertNotIn(b"private-output-canary", image.encoded(report))
            self.assertFalse((root / "built").exists())

    def test_wrong_user_or_entrypoint_or_layers_cannot_produce_build_receipt(self):
        for change in ("user", "entrypoint", "layers"):
            with self.subTest(change=change), self.preparation() as (root, bundle, trcs, _, _, _):
                output = root / "prepared"
                prepared = image.prepare(bundle, image.digest(b"receipt"), trcs, output)
                with self.fake_build(output) as (_, _, _, derived):
                    if change == "user":
                        derived["Config"]["User"] = "0:0"
                    elif change == "entrypoint":
                        derived["Config"]["Entrypoint"] = ["/bin/sh"]
                    else:
                        derived["RootFS"]["Layers"] = []
                    report = image.build(output, prepared["receipt_sha256"], root / "built", "unix:///var/run/docker.sock")
                self.assertEqual(report["result"], "fail")
                self.assertTrue(report["build_completed"])
                self.assertFalse((root / "built").exists())


if __name__ == "__main__":
    unittest.main()
