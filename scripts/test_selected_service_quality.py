# SPDX-License-Identifier: Apache-2.0
"""Quality profile plumbing, not quality scores or GPU qualification."""

import copy
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest import mock

import deepseek_tuning as tuning
import selected_service as service
import selected_service_engine as engine
import test_deepseek_tuning as tuning_tests
import test_selected_service as lifecycle_tests
import test_selected_service_process as process_tests
import test_selected_service_smoke as smoke_tests
import test_selected_service_graphs as graphs_tests

PROFILE, SHA = engine.QUALITY_PROFILE, "a" * 64


def quality_lab():
    return {**process_tests.lab_contract(PROFILE), "tokenizer_parity": True,
            "runtime_observation": True, "tokenizer_materialization": {"schema": "test-input"}}


class QualityProfileTests(unittest.TestCase):
    def test_only_two_engine_limits_change_and_canonical_name_is_not_an_alias(self):
        lab = quality_lab()
        original = copy.deepcopy(lab)
        result = engine.canonical_arguments(lab)
        expected = list(lab["arguments"])
        expected[expected.index("--served-model-name") + 1] = service.MODEL
        self.assertEqual(result, expected)
        self.assertEqual(lab, original)
        baseline = process_tests.lab_contract()["arguments"]
        for flag in ("--max-model-len", "--max-num-batched-tokens"):
            baseline[baseline.index(flag) + 1] = "32768"
        self.assertEqual(lab["arguments"], baseline)
        for flag in ("--tokenizer-mode", "--tool-call-parser", "--reasoning-parser"):
            self.assertEqual(result[result.index(flag) + 1], "deepseek_v41")

    def test_no_graph_speculative_confidential_or_incomplete_quality_contract(self):
        for change in ({"stream_cohort": {}}, {"tau_owner": {}}, {"provider_conformance": {}},
                {"gpu_security": "confidential"}, {"gpu_family": "B200"}, {"data_parallel_size": 1},
                {"tokenizer_parity": False}, {"runtime_observation": False},
                {"tokenizer_materialization": None}, {"execution_profile": engine.EAGER_PROFILE}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                engine.canonical_arguments({**quality_lab(), **change})
        for flag in ("--max-model-len", "--max-num-batched-tokens", "--max-num-seqs"):
            lab = quality_lab()
            lab["arguments"][lab["arguments"].index(flag) + 1] = "65536"
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                engine.canonical_arguments(lab)
            lab = quality_lab()
            lab["arguments"] += [flag, "32768"]
            with self.subTest(duplicate=flag), self.assertRaises(ValueError):
                engine.canonical_arguments(lab)
        for extra in (["--speculative-config", "{}"], ["--enforce-eager"]):
            lab = quality_lab()
            lab["arguments"] += extra
            with self.assertRaises(ValueError): engine.canonical_arguments(lab)
        lab = quality_lab()
        lab["arguments"].remove("--enforce-eager")
        with self.assertRaises(ValueError): engine.canonical_arguments(lab)

    def test_catalog_guard_receipt_and_lifetimes_share_the_closed_limits(self):
        clock = lifecycle_tests.Clock()
        expected = {"profile": PROFILE, "context_tokens": 32768, "max_output_tokens": 2048,
                    "max_inflight": 1, "guard_profile": "selected-deepseek-quality"}
        self.assertEqual(engine.execution_limits(PROFILE), expected)
        with mock.patch.object(service, "time", clock), mock.patch.object(service.pilot, "time", clock):
            for lifetime, seconds in ((service.SMOKE_PROFILE, 4800), (service.ORDINARY_PROFILE, 86400)):
                contract = service.Contract.begin(clock.timestamp(seconds), input_price="1", output_price="2",
                    lifetime_profile=lifetime, execution_profile=PROFILE)
                self.assertEqual((contract.context_tokens, contract.max_output_tokens, contract.max_inflight),
                                 (32768, 2048, 1))
                self.assertEqual((contract.memory_bytes, contract.swap_bytes), (512 << 30, 0))
                self.assertEqual(contract.hard_monotonic, clock.mono + seconds)
                args = contract.guard_arguments(service.Inputs(Path("/unused"), {}))
                self.assertEqual(args[args.index("--profile") + 1], expected["guard_profile"])
                with self.assertRaises((ValueError, TypeError)):
                    replace(contract, context_tokens=65536)
        base = smoke_tests.lifecycle()
        row = service.execution_result(base, PROFILE, SHA)
        kwargs = {"execution_profile": PROFILE, "prepared_sha256": SHA}
        self.assertEqual(smoke_tests.smoke.lifecycle(smoke_tests.encoded(row), **kwargs), row)
        for bad in (base, {**row, "execution_prepared_sha256": "b" * 64},
                {**row, "execution": engine.execution_limits(engine.EAGER_PROFILE)},
                {**row, "execution": {**expected, "max_output_tokens": 4096}}):
            with self.assertRaises(ValueError):
                smoke_tests.smoke.lifecycle(smoke_tests.encoded(bad), **kwargs)
        with self.assertRaises(ValueError): smoke_tests.smoke.lifecycle(smoke_tests.encoded(row))

    def test_observed_engine_must_match_selected_context_and_profile(self):
        with lifecycle_tests.LifecycleTests().setup_service() as (supervisor, backend, _, _):
            supervisor.contract = replace(supervisor.contract, execution_profile=PROFILE)
            supervisor.children["engine"] = lifecycle_tests.Child(1000)
            for profile, context in ((PROFILE, 32768), (PROFILE, 8192), (engine.EAGER_PROFILE, 32768)):
                backend.observation = service.EngineReady(lifecycle_tests.endpoint(1000),
                    json.dumps({"data": [{"id": service.MODEL, "max_model_len": context}]}).encode(),
                    context, service.MEMORY_BYTES, 0, profile)
                if profile == PROFILE and context == 32768:
                    self.assertTrue(supervisor.ready("engine", 1000))
                else:
                    with self.assertRaises(ValueError): supervisor.ready("engine", 1000)

    def test_shared_source_admission_and_early_failure_bind_quality_profile(self):
        with mock.patch.object(graphs_tests, "GRAPH", PROFILE):
            graphs_tests.AdmissionTests.setUpClass()
            check = graphs_tests.AdmissionTests()
            check.test_real_shared_admission_binds_lab_selection_and_current_source_before_inputs()
            check.test_default_container_command_unchanged_and_graph_command_exactly_opt_in()
            graphs_tests.ContractTests().test_cli_mismatch_stops_before_private_inputs_and_emits_closed_failure()


class PreparedQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tuning_tests.TuningProfiles.setUpClass()

    def test_real_payload_derivation_requires_exact_prerequisites(self):
        fixture = tuning_tests.TuningProfiles()
        options = dict(tokenizer_parity=True, tokenizer_materialization=True, runtime_observation=True)
        files, contract = fixture.payload(PROFILE, **options)
        self.assertEqual(fixture.validate(files, contract), tuning_tests.fixtures.NEW)
        self.assertEqual(tuning.context_limit(contract), 32768)
        self.assertFalse(contract["deployable"] or contract["confidential"])
        for changes in ({}, {"tokenizer_parity": True}, {**options, "stream_cohort": True},
                        {**options, "stream_profile": "anything"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                fixture.payload(PROFILE, **changes)
        with self.assertRaises(ValueError):
            fixture.validate({**files, "tuning.py": files["tuning.py"] + b"\n"}, contract)


if __name__ == "__main__":
    unittest.main()
