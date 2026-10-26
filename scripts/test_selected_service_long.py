# SPDX-License-Identifier: Apache-2.0
"""Long-context candidate contracts; not GPU/cache/quality measurements."""

import copy
from dataclasses import replace
import json
import types
import unittest
from unittest import mock

import deepseek_tuning as tuning
import selected_service as service
import selected_service_engine as engine
import test_deepseek_tuning as tuning_tests
import test_selected_service as lifecycle_tests
import test_selected_service_process as process_tests
import test_selected_service_graphs as graphs_tests
import test_selected_service_smoke as smoke_tests


def long_lab():
    return {**process_tests.lab_contract(engine.LONG_PROFILE), "tokenizer_parity": True,
            "runtime_observation": True, "tokenizer_materialization": {"schema": "test-input"}}


class LongProfileTests(unittest.TestCase):
    def test_full_long_lifecycle_reserves_both_drains_and_every_reap(self):
        with lifecycle_tests.LifecycleTests().setup_service(lifetime=4800) as (supervisor, owner, clock, shutdown):
            contract = service.Contract.begin(clock.timestamp(4800), input_price="1", output_price="2",
                execution_profile=engine.LONG_PROFILE)
            supervisor.contract = contract
            owner.prepared = types.SimpleNamespace(receipt_sha256="a" * 64)
            owner.observation = service.EngineReady(lifecycle_tests.endpoint(1000),
                json.dumps({"data": [{"id": service.MODEL, "max_model_len": 131072}]}).encode(),
                131072, service.MEMORY_BYTES, 0, engine.LONG_PROFILE)
            probe = owner.probe
            def ready(role, *args):
                result = probe(role, *args)
                if role == "tls":
                    shutdown.retire = contract.hard_monotonic - contract.cleanup_reserve
                return result
            def drain(role, child, deadline):
                self.assertEqual(deadline - clock.mono, 930)
                clock.advance(930)
                child.returncode = 0
                return True
            def reap(role, child, deadline):
                self.assertEqual(deadline - clock.mono, 25)
                clock.advance(25)
                child.returncode = 0
                return True
            def sweep(deadline):
                self.assertEqual(deadline - clock.mono, 25)
                clock.advance(25)
                return True
            owner.probe, owner.drain, owner.reap, owner.reap_unregistered = ready, drain, reap, sweep
            result = supervisor.run()
            self.assertEqual(result["status"], "retired")
            self.assertTrue(result["cleanup_confirmed"])
            self.assertEqual(clock.mono, contract.hard_monotonic)

    def test_long_budget_is_reserved_before_admission_and_never_renews(self):
        clock = lifecycle_tests.Clock()
        with mock.patch.object(service, "time", clock), mock.patch.object(service.pilot, "time", clock):
            for seconds in (0, 375, 1960):
                with self.assertRaises(ValueError):
                    service.Contract.begin(clock.timestamp(seconds), input_price="1", output_price="2",
                        execution_profile=engine.LONG_PROFILE)
            contract = service.Contract.begin(clock.timestamp(4800), input_price="1", output_price="2",
                execution_profile=engine.LONG_PROFILE)
            self.assertEqual((contract.request_seconds, contract.drain_seconds, contract.cleanup_reserve),
                             (900, 930, 1960))
            self.assertEqual(contract.lifetime["admission_stop_at"], clock.timestamp(4800 - 1960))
            self.assertLessEqual(contract.startup_deadline, contract.hard_monotonic - 1960)
            clock.advance(4800)
            with self.assertRaises(ValueError):
                service.Contract.begin(contract.hard_stop_at, input_price="1", output_price="2",
                    execution_profile=engine.LONG_PROFILE)

    def test_wait_drain_uses_profile_but_never_exceeds_absolute_deadline(self):
        fixture = process_tests.ProcessTests()
        fixture.setUp()
        try:
            fixture.prepared.lab = process_tests.lab_contract(engine.LONG_PROFILE)
            child = mock.Mock()
            child.wait.return_value = 0
            with mock.patch.object(process_tests.process.time, "monotonic", return_value=100):
                self.assertTrue(fixture.owner.wait_drain("tls", child, 1100))
                child.wait.assert_called_once_with(timeout=920)
                child.wait.reset_mock()
                self.assertTrue(fixture.owner.wait_drain("tls", child, 150))
                child.wait.assert_called_once_with(timeout=50)
        finally:
            fixture.doCleanups()

    def test_watchdog_derives_long_cleanup_from_owned_container_profile(self):
        budget = smoke_tests.smoke.cleanup_budget
        self.assertEqual(budget({"Config": {"Cmd": ["--execution-profile", engine.LONG_PROFILE]}}), 1960)
        for command in (None, [], ["--execution-profile"],
                        ["--execution-profile", engine.LONG_PROFILE, "--execution-profile", engine.EAGER_PROFILE],
                        ["--execution-profile", engine.EAGER_PROFILE], ["--execution-profile", "invalid"]):
            self.assertEqual(budget({"Config": {"Cmd": command}}), service.CLEANUP_RESERVE)

    def test_tls_body_bound_and_retirement_budget_match_profile(self):
        for profile, limit in ((engine.LONG_PROFILE, 4 << 20), (engine.EAGER_PROFILE, 65536),
                               (engine.QUALITY_PROFILE, 65536), (engine.GRAPHS_PROFILE, 65536)):
            fixture = process_tests.ProcessTests()
            fixture.setUp()
            try:
                contract = replace(fixture.contract, execution_profile=profile)
                fixture.prepared.lab = process_tests.lab_contract(profile)
                with mock.patch.object(process_tests.process.subprocess, "Popen", return_value=fixture.child()) as popen:
                    fixture.owner.start("tls", contract, fixture.inputs, lambda child: None, 1900)
                args = popen.call_args.args[0]
                self.assertEqual(args[args.index("--max-request-bytes") + 1], str(limit))
                self.assertEqual(args[args.index("--max-inflight") + 1], str(contract.max_inflight))
                if profile == engine.LONG_PROFILE:
                    self.assertEqual(args[args.index("--shutdown-grace") + 1], "910s")
                    self.assertEqual(contract.cleanup_reserve, 1960)
                    self.assertEqual(contract.admission_stop_at,
                        service.retirement_at(contract.hard_wall - 1960))
                else:
                    self.assertNotIn("--shutdown-grace", args)
                    self.assertEqual(contract.admission_stop_at, fixture.contract.admission_stop_at)
                    self.assertEqual(contract.lifetime, fixture.contract.lifetime)
            finally:
                fixture.doCleanups()

    def test_source_bound_flags_and_limits(self):
        lab = long_lab()
        before = copy.deepcopy(lab)
        args = engine.canonical_arguments(lab)
        expected = process_tests.lab_contract()["arguments"]
        expected[expected.index("--max-model-len") + 1] = "131072"
        expected[expected.index("--max-num-batched-tokens") + 1] = "4096"
        expected[expected.index("--served-model-name") + 1] = service.MODEL
        expected += ["--enable-chunked-prefill", "--enable-prefix-caching", "--enable-prompt-tokens-details"]
        self.assertEqual(args, expected)
        self.assertEqual(lab, before)
        self.assertEqual(engine.execution_limits(engine.LONG_PROFILE), {
            "profile": engine.LONG_PROFILE, "context_tokens": 131072, "max_output_tokens": 16384,
            "max_inflight": 1, "guard_profile": "selected-deepseek-long"})

    def test_missing_disabled_duplicate_and_incompatible_flags_rejected(self):
        flags = ("--enable-chunked-prefill", "--enable-prefix-caching", "--enable-prompt-tokens-details")
        for flag in flags:
            for change in ("remove", "duplicate", "disable"):
                lab = long_lab()
                if change == "remove": lab["arguments"].remove(flag)
                elif change == "duplicate": lab["arguments"].append(flag)
                else: lab["arguments"].append(flag.replace("--enable-", "--no-enable-"))
                with self.subTest(flag=flag, change=change), self.assertRaises(ValueError):
                    engine.canonical_arguments(lab)
        for change in ({"tokenizer_parity": False}, {"runtime_observation": False}, {"stream_cohort": {}},
                       {"gpu_security": "confidential"}, {"data_parallel_size": 1}, {"tau_owner": {}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                engine.canonical_arguments({**long_lab(), **change})
        for flag, wrong in (("--max-model-len", "1048576"), ("--max-num-batched-tokens", "131072"),
                            ("--max-num-seqs", "8")):
            lab = long_lab()
            lab["arguments"][lab["arguments"].index(flag) + 1] = wrong
            with self.assertRaises(ValueError): engine.canonical_arguments(lab)

    def test_shared_admission_binds_new_profile(self):
        with mock.patch.object(graphs_tests, "GRAPH", engine.LONG_PROFILE):
            graphs_tests.AdmissionTests.setUpClass()
            check = graphs_tests.AdmissionTests()
            check.test_real_shared_admission_binds_lab_selection_and_current_source_before_inputs()
            check.test_default_container_command_unchanged_and_graph_command_exactly_opt_in()
            graphs_tests.ContractTests().test_cli_mismatch_stops_before_private_inputs_and_emits_closed_failure()

    def test_observation_must_match_profile_not_just_context(self):
        with lifecycle_tests.LifecycleTests().setup_service() as (supervisor, backend, _, _):
            supervisor.contract = replace(supervisor.contract, execution_profile=engine.LONG_PROFILE)
            supervisor.children["engine"] = lifecycle_tests.Child(1000)
            for profile, context in ((engine.LONG_PROFILE, 131072), (engine.LONG_PROFILE, 32768), (engine.QUALITY_PROFILE, 131072)):
                backend.observation = service.EngineReady(lifecycle_tests.endpoint(1000),
                    json.dumps({"data": [{"id": service.MODEL, "max_model_len": context}]}).encode(),
                    context, service.MEMORY_BYTES, 0, profile)
                if profile == engine.LONG_PROFILE and context == 131072:
                    self.assertTrue(supervisor.ready("engine", 1000))
                else:
                    with self.assertRaises(ValueError): supervisor.ready("engine", 1000)


class PreparedLongTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tuning_tests.TuningProfiles.setUpClass()

    def test_prepared_payload_and_source_tampering(self):
        fixture = tuning_tests.TuningProfiles()
        options = dict(tokenizer_parity=True, tokenizer_materialization=True, runtime_observation=True)
        files, contract = fixture.payload(engine.LONG_PROFILE, **options)
        self.assertEqual(fixture.validate(files, contract), tuning_tests.fixtures.NEW)
        self.assertEqual(tuning.context_limit(contract), 131072)
        for change in ({}, {"tokenizer_parity": True}, {**options, "stream_cohort": True}):
            with self.assertRaises(ValueError): fixture.payload(engine.LONG_PROFILE, **change)
        with self.assertRaises(ValueError):
            fixture.validate({**files, "tuning.py": files["tuning.py"] + b"\n"}, contract)


if __name__ == "__main__":
    unittest.main()
