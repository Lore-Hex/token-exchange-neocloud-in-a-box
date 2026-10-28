# SPDX-License-Identifier: Apache-2.0
"""Official runtime identity boundaries with fakes; no GPU or network calls."""

import copy
from dataclasses import replace
import io
import json
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest import mock

import selected_long_qualification as qualification
import selected_service_engine as engine
import selected_service_process as process
import test_original_deepseek_lab as labs
import test_selected_service as lifecycles
import test_selected_service_image as images
import test_selected_service_process as processes
import test_selected_service_smoke as smokes

service, PROFILE = process.service, engine.ORIGINAL_MILLION_PROFILE
MODEL = service.models.select("original")


class OriginalRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        labs.OriginalLabTests.setUpClass()

    def payload(self):
        return labs.OriginalLabTests().payload()

    def test_contract_binds_identity_limits_cache_price_and_unchanged_cutoff(self):
        clock = lifecycles.Clock()
        with mock.patch.object(service, "time", clock), mock.patch.object(service.pilot, "time", clock):
            for lifetime, seconds in ((service.ORDINARY_PROFILE, 18000), (service.WEEKLY_PROFILE, 604800)):
                contract = service.Contract.begin(clock.timestamp(seconds), input_price="1", output_price="2",
                    cached_input_price="0.2", lifetime_profile=lifetime, execution_profile=PROFILE)
                self.assertEqual((contract.model, contract.revision, contract.manifest_sha256),
                    (engine.ORIGINAL_MODEL, MODEL["hf_revision"], MODEL["manifest_sha256"]))
                self.assertEqual((contract.context_tokens, contract.max_output_tokens, contract.max_inflight),
                                 (1048576, 393216, 1))
                self.assertEqual((contract.request_seconds, contract.cleanup_reserve), (3540, 7240))
                self.assertEqual(contract.admission_stop_at, clock.timestamp(seconds - 7240))
                self.assertEqual(service.request_bytes(PROFILE), 16 << 20)
                arguments = contract.guard_arguments(SimpleNamespace(root=Path("/private/synthetic")))
                self.assertEqual(arguments[arguments.index("--profile") + 1], "original-deepseek-million")
                self.assertEqual(arguments[arguments.index("--cached-input-price") + 1], "0.2")
                self.assertEqual(arguments[arguments.index("--retirement-at") + 1], contract.admission_stop_at)
                legacy = replace(contract, execution_profile=engine.MILLION_PROFILE)
                self.assertEqual(legacy.model, engine.SERVICE_MODEL)
                self.assertEqual(legacy.hard_stop_at, contract.hard_stop_at)
                self.assertNotEqual(legacy.manifest_sha256, contract.manifest_sha256)
            for seconds in (7240, 604801):
                with self.assertRaises(ValueError):
                    service.Contract.begin(clock.timestamp(seconds), input_price="1", output_price="2",
                        lifetime_profile=service.WEEKLY_PROFILE, execution_profile=PROFILE)
            with self.assertRaises(ValueError):
                replace(contract, cached_input_price="1.1")

    def test_new_payload_preserves_original_and_rejects_selected_pack(self):
        payload, lab = self.payload()
        original = copy.deepcopy(payload)
        derived = process.prepare_payload(payload)
        self.assertEqual(payload, original)
        self.assertEqual({name for name in derived if derived[name] != original[name]},
                         {"launch.py", "lab.json", "python"})
        contract = json.loads(derived["lab.json"])
        self.assertEqual(contract["model_profile"], MODEL)
        arguments = engine.canonical_arguments(contract)
        self.assertEqual(arguments[arguments.index("--served-model-name") + 1], engine.ORIGINAL_MODEL)
        expected = list(lab["arguments"])
        expected[expected.index("--served-model-name") + 1] = engine.ORIGINAL_MODEL
        self.assertEqual(arguments, expected)
        for change in ({"model_profile": service.models.select("uncensored")},
                       {"execution_profile": engine.MILLION_PROFILE}):
            with self.assertRaises(ValueError):
                engine.canonical_arguments({**contract, **change})
        changed = dict(payload)
        changed["pack/files.sha256"] = (labs.ROOT / service.models.select("uncensored")["pack"] /
                                        "files.sha256").read_bytes()
        contract = copy.deepcopy(lab)
        contract["sources"]["pack/files.sha256"] = process.digest(changed["pack/files.sha256"])
        changed["lab.json"] = json.dumps(contract).encode()
        changed["python"] = service.engine_lab.wrapper_bytes(contract["interpreter"]["resolved"],
            contract["environment"], process.digest(changed["lab.json"]))
        with self.assertRaises(ValueError):
            process.prepare_payload(changed)

    def test_prepared_runtime_accepts_only_profile_bound_manifest(self):
        _, contract = self.payload()
        with mock.patch.object(processes, "lab_contract", side_effect=lambda: copy.deepcopy(contract)), \
                processes.PackagingTests().prepared_fixture() as (_, payload, sha):
            prepared = process.Prepared(sha, time.monotonic() + 30)
            self.assertEqual(prepared.lab["model_profile"], MODEL)
            (payload / "pack/files.sha256").write_bytes(b"wrong checkpoint")
            with self.assertRaises(ValueError):
                process.Prepared(sha, time.monotonic() + 30)

    def test_image_preparation_roundtrip_and_legacy_checker_denial(self):
        _, contract = self.payload()
        images.ImageTests.setUpClass()
        with mock.patch.object(images, "lab_contract", side_effect=lambda: copy.deepcopy(contract)):
            with images.ImageTests().preparation() as (root, bundle, trcs, _, _, compiler):
                report = images.image.prepare(bundle, "a" * 64, trcs, root / "output")
                compiler.assert_called_once()
                _, _, prepared = images.image.validated_files(root / "output", report["receipt_sha256"])
                self.assertEqual(prepared["model_profile"], MODEL)
                self.assertEqual(prepared["execution_profile"], PROFILE)
            with images.ImageTests().preparation() as (root, bundle, trcs, _, _, compiler):
                with self.assertRaises(ValueError):
                    images.image.prepare(bundle, "a" * 64, trcs, root / "output",
                                         checker_wheelhouse=root / "checker")
                compiler.assert_not_called()

    def test_engine_readiness_cannot_relabel_another_checkpoint(self):
        child = lifecycles.Child(1234)
        observed = service.EngineReady(lifecycles.endpoint(child.pid), json.dumps({"data": [
            {"id": engine.ORIGINAL_MODEL, "max_model_len": 1048576}]}).encode(),
            1048576, service.MEMORY_BYTES, 0, PROFILE)
        with mock.patch.object(service.identity.Endpoint, "check"):
            observed.check(child, time.monotonic() + 1)
            for model in (engine.SERVICE_MODEL, MODEL["served_model_name"], "other/model"):
                changed = replace(observed, models_json=json.dumps({"data": [
                    {"id": model, "max_model_len": 1048576}]}).encode())
                with self.subTest(model=model), self.assertRaises(ValueError):
                    changed.check(child, time.monotonic() + 1)
            with self.assertRaises(ValueError):
                replace(observed, execution_profile=engine.MILLION_PROFILE).check(child, time.monotonic() + 1)

    def test_finite_supervisor_retires_and_cleanup_receipt_stays_unqualified(self):
        with lifecycles.LifecycleTests().setup_service() as (supervisor, owner, clock, shutdown):
            supervisor.contract = service.Contract.begin(clock.timestamp(18000), input_price="1", output_price="2",
                lifetime_profile=service.ORDINARY_PROFILE, execution_profile=PROFILE)
            supervisor.hard_deadline = supervisor.contract.hard_monotonic
            owner.prepared = SimpleNamespace(receipt_sha256="a" * 64)
            owner.observation = service.EngineReady(lifecycles.endpoint(1000), json.dumps({"data": [
                {"id": engine.ORIGINAL_MODEL, "max_model_len": 1048576}]}).encode(),
                1048576, service.MEMORY_BYTES, 0, PROFILE)
            probe = owner.probe
            def observed(role, *args):
                result = probe(role, *args)
                if role == "tls":
                    shutdown.retire = supervisor.contract.hard_monotonic - supervisor.contract.cleanup_reserve
                return result
            owner.probe = observed
            with mock.patch.object(service, "progress"):
                row = supervisor.run()
            self.assertEqual(row["status"], "retired", row)
            self.assertTrue(row["cleanup_confirmed"])
            self.assertTrue(all(child.poll() is not None for child in owner.children.values()))
            self.assertLess(clock.monotonic(), supervisor.contract.hard_monotonic)
            self.assertTrue(all(row[key] is False for key in
                ("deployable", "hardware_validated", "tools", "reasoning", "zdr", "confidential", "e2ee")))
            self.assertEqual(row["execution"]["profile"], PROFILE)

    def test_legacy_qualification_modes_reject_before_preparation(self):
        clock = lifecycles.Clock()
        base = ["--prepared-sha256", "a" * 64, "--provider-config", "/private/synthetic",
            "--api-key-file", "/private/synthetic", "--cert", "/private/synthetic", "--key", "/private/synthetic",
            "--hostname", "synthetic.invalid", "--input-price", "1", "--output-price", "2",
            "--hard-stop-at", clock.timestamp(18000), "--execution-profile", PROFILE,
            "--lifetime-profile", service.ORDINARY_PROFILE]
        for flag in ("--inference-smoke", "--conformance-smoke", "--protocol-smoke", "--tool-smoke",
                     "--long-qualification"):
            with self.subTest(flag=flag), ExitStack() as stack:
                for module in (service, service.pilot, process):
                    stack.enter_context(mock.patch.object(module, "time", clock))
                prepared = stack.enter_context(mock.patch.object(process, "Prepared"))
                spawn = stack.enter_context(mock.patch.object(process.subprocess, "Popen"))
                stack.enter_context(redirect_stdout(io.StringIO()))
                self.assertEqual(process.main(base + [flag]), 1)
                prepared.assert_not_called()
                spawn.assert_not_called()
        with self.assertRaises(ValueError):
            qualification.workload_profile(PROFILE)

    def test_official_container_keeps_closed_runtime_and_finite_stop(self):
        smokes.SmokeTests.setUpClass()
        fixture = smokes.SmokeTests()
        fixture.setUp()
        fixture.args.execution_profile = PROFILE
        fixture.args.lifetime_profile = service.WEEKLY_PROFILE
        fixture.args.hard_stop_at = fixture.clock.timestamp(604800)
        command = smokes.smoke.container_command("nib-selected-" + "1" * 32, "2" * 32,
            fixture.build, fixture.base, fixture.args.weights, "/private/stage", fixture.args,
            fixture.args.hard_stop_at)
        info = fixture.container(command)
        smokes.smoke.check_container(info, info["Id"], fixture.name, fixture.token, fixture.build,
            fixture.image, command, fixture.args.weights, "/private/stage")
        self.assertEqual(command[command.index("--execution-profile") + 1], PROFILE)
        self.assertEqual(command[command.index("--hard-stop-at") + 1], fixture.args.hard_stop_at)
        for flag, value in (("--network", "none"), ("--memory", "512g"), ("--memory-swap", "512g"),
                            ("--log-driver", "none"), ("--restart", "no")):
            self.assertEqual(command[command.index(flag) + 1], value)

    def test_host_inventory_selects_all_83_official_files(self):
        smoke = smokes.smoke
        lab, _ = smoke.host_recorder()
        cache = lab.load("original_inventory_test", smoke.ROOT / "host/cache-public-weights.py", smoke.CACHE_SHA)
        expected = object()
        with mock.patch.object(cache, "strict_inventory", side_effect=RuntimeError("synthetic stop")) as inventory, \
                mock.patch.object(lab, "load", return_value=cache):
            with self.assertRaisesRegex(RuntimeError, "synthetic stop"):
                smoke.weights_view(lab, expected, time.monotonic() + 10, execution_profile=PROFILE)
        self.assertIs(inventory.call_args.args[0], expected)
        rows = inventory.call_args.args[1]
        self.assertEqual(len(rows), 83)
        self.assertEqual(sum(row[1] for row in rows), MODEL["total_bytes"])


if __name__ == "__main__":
    unittest.main()
