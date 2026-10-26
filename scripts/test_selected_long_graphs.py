# SPDX-License-Identifier: Apache-2.0
"""Closed C1 decode-graph candidate; no CUDA, inference or capacity claims."""

import copy
from dataclasses import replace
import json
import types
import unittest
from unittest import mock

import deepseek_tuning as tuning
import selected_service_engine as engine
import test_deepseek_tuning as tuning_tests
import test_selected_long_hook as hooks
import test_selected_long_qualification as qualification_tests
import test_selected_service_long as long_tests
import test_selected_service_process as process_tests
from fixtures import vllm_cudagraph as graphs
from test_evidence_history import git

GRAPH, EAGER = engine.LONG_GRAPHS_PROFILE, engine.LONG_PROFILE
q, service, process = hooks.q, hooks.service, hooks.process
BASE = "2630d2a1a977a76c13f0d62b8e0c0bf6b83e54bb"


def lab():
    return {**long_tests.long_lab(), **process_tests.lab_contract(GRAPH)}


class LongGraphsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tuning_tests.TuningProfiles.setUpClass()

    def test_only_decode_graph_switch_changes_long_arguments(self):
        eager = engine.canonical_arguments(long_tests.long_lab())
        candidate = lab()
        before = copy.deepcopy(candidate)
        expected = list(eager)
        expected.remove("--enforce-eager")
        expected[expected.index("--compilation-config") + 1] = (
            '{"pass_config":{"fuse_allreduce_rms":false},'
            '"cudagraph_mode":"FULL_DECODE_ONLY","cudagraph_capture_sizes":[1]}')
        self.assertEqual(engine.canonical_arguments(candidate), expected)
        self.assertEqual(candidate, before)
        self.assertEqual(engine.execution_limits(GRAPH), {**engine.execution_limits(EAGER), "profile": GRAPH})
        self.assertNotIn("--speculative-config", expected)
        for name in engine.LONG_PROFILES:
            self.assertEqual((service.request_seconds(name), service.drain_seconds(name), service.cleanup_reserve(name)),
                             (900, 930, 1960))
            self.assertEqual(hooks.smoke.cleanup_budget({"Config": {"Cmd": ["--execution-profile", name]}}), 1960)

    def test_all_existing_tuning_arguments_and_service_limits_are_identical(self):
        old = types.ModuleType("old_tuning")
        exec(compile(git(tuning_tests.ROOT, "show", BASE + ":scripts/deepseek_tuning.py"), "old_tuning", "exec"), old.__dict__)
        baseline = service.engine_lab.lab_args(tuning_tests.fixtures.PayloadProfiles.fixture.builder.recorder.prep.launch_plan(),
            2, model=service.models.select("uncensored")["served_model_name"])
        for name in old.NAMES:
            with self.subTest(profile=name):
                self.assertEqual(tuning.arguments(baseline, name), old.arguments(baseline, name))
        previous = types.ModuleType("old_engine")
        exec(compile(git(tuning_tests.ROOT, "show", BASE + ":scripts/selected_service_engine.py"), "old_engine", "exec"),
             previous.__dict__)
        for name in (previous.EAGER_PROFILE, *previous.BOUND_PROFILES):
            self.assertEqual(engine.execution_limits(name), previous.execution_limits(name))

    def test_pinned_graph_dispatch_only_selects_one_token_decode(self):
        args = engine.canonical_arguments(lab())
        config = json.loads(args[args.index("--compilation-config") + 1])
        state = types.SimpleNamespace(compilation_config=types.SimpleNamespace(
                cudagraph_capture_sizes=config["cudagraph_capture_sizes"], max_cudagraph_capture_size=1),
            cudagraph_mode=graphs.CUDAGraphMode.FULL_DECODE_ONLY, max_num_reqs=1, decode_query_len=1,
            varlen_decode=False, lora_capture_cases=[0], vllm_config=types.SimpleNamespace(speculative_config=None),
            _maybe_ubatch_twin=lambda _: None, _resolve_effective_loras=lambda n: n,
            _capture_descs={}, _candidates={}, _graphs_captured=True)
        graphs._init_candidates(state)
        self.assertEqual(graphs.dispatch(state, 1, 1, 1, 0, 1).cg_mode, graphs.CUDAGraphMode.FULL)
        for requests, tokens, uniform in ((1, 4096, None), (1, 1, None), (2, 2, 1), (8, 8, 1)):
            self.assertEqual(graphs.dispatch(state, requests, tokens, uniform, 0).cg_mode, graphs.CUDAGraphMode.NONE)
        self.assertEqual([(row.num_reqs, row.num_tokens) for row in state._capture_descs[graphs.CUDAGraphMode.FULL]], [(1, 1)])
        tuning_tests.TuningProfiles().test_graph_fixture_is_unchanged_pinned_upstream_code()

    def test_graph_tampering_and_cross_profile_relabeling_are_rejected(self):
        for flag, value in (("--max-model-len", "32768"), ("--max-num-seqs", "8"),
                ("--max-num-batched-tokens", "131072"), ("--compilation-config", "{}"),
                ("--compilation-config", '{"pass_config":{"fuse_allreduce_rms":false},'
                 '"cudagraph_mode":"FULL_DECODE_ONLY","cudagraph_capture_sizes":[1,2,4,8]}')):
            item = lab()
            item["arguments"][item["arguments"].index(flag) + 1] = value
            with self.subTest(flag=flag, value=value), self.assertRaises(ValueError):
                engine.canonical_arguments(item)
        for flag in ("--enable-chunked-prefill", "--enable-prefix-caching", "--enable-prompt-tokens-details"):
            for change in ("missing", "duplicate", "disabled"):
                item = lab()
                if change == "missing": item["arguments"].remove(flag)
                else: item["arguments"].append(flag if change == "duplicate" else flag.replace("--enable-", "--no-enable-"))
                with self.subTest(flag=flag, change=change), self.assertRaises(ValueError):
                    engine.canonical_arguments(item)
        for suffix in (["--enforce-eager"], ["--speculative-config", "{}"]):
            item = lab()
            item["arguments"] += suffix
            with self.assertRaises(ValueError): engine.canonical_arguments(item)
        for contract, name in ((lab(), EAGER), (long_tests.long_lab(), GRAPH)):
            with self.assertRaises(ValueError): engine.canonical_arguments({**contract, "execution_profile": name})
        for change in ({"data_parallel_size": 1}, {"gpu_security": "confidential"}, {"tau_owner": {}},
                       {"tokenizer_parity": False}, {"runtime_observation": False}, {"stream_cohort": {}}):
            with self.assertRaises(ValueError): engine.canonical_arguments({**lab(), **change})

    def test_tls_limits_and_owned_readiness_remain_long_family(self):
        fixture = process_tests.ProcessTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.prepared.lab = lab()
        contract = replace(fixture.contract, execution_profile=GRAPH)
        with mock.patch.object(process.subprocess, "Popen", return_value=fixture.child()) as popen:
            fixture.owner.start("tls", contract, fixture.inputs, lambda _: None, 1900)
        args = popen.call_args.args[0]
        self.assertEqual(args[args.index("--max-request-bytes") + 1], str(4 << 20))
        self.assertEqual(args[args.index("--shutdown-grace") + 1], "910s")
        self.assertEqual(args[args.index("--max-inflight") + 1], "1")
        child = mock.Mock()
        child.wait.return_value = 0
        with mock.patch.object(process.time, "monotonic", return_value=100):
            self.assertTrue(fixture.owner.wait_drain("tls", child, 1100))
        child.wait.assert_called_once_with(timeout=920)
        with hooks.HookTests().supervisor() as (supervisor, backend, _, _):
            supervisor.contract = replace(supervisor.contract, execution_profile=GRAPH)
            supervisor.children["engine"] = hooks.lifecycle_tests.Child(1000)
            with self.assertRaises(ValueError): supervisor.ready("engine", 1000)
            backend.observation = replace(backend.observation, execution_profile=GRAPH)
            self.assertTrue(supervisor.ready("engine", 1000))

    def test_report_profile_is_bound_to_lifecycle_and_unknown_usage_preserved(self):
        with mock.patch.object(hooks, "LONG", GRAPH), hooks.HookTests().supervisor() as (supervisor, _, _, _):
            row = hooks.successful()
            with mock.patch.object(q, "run_owned", return_value=row):
                result = supervisor.run(long_qualification=True)
            join = hooks.HookTests()
            self.assertEqual(join.join(result, supervisor.contract), result)
            wrong = copy.deepcopy(result)
            wrong["qualification"]["execution_profile"] = EAGER
            with self.assertRaises(ValueError): join.join(wrong, supervisor.contract)
        def lost(*args):
            args[-1]["attempted"] = True
            raise q.Rejected("deadline")
        failed = qualification_tests.run_sequence(lost, execution_profile=GRAPH)
        self.assertEqual((failed["execution_profile"], failed["usage_unknown_requests"]), (GRAPH, 1))
        self.assertEqual(q.validate_report(q.encoded(failed), execution_profile=GRAPH), failed)
        with self.assertRaises(ValueError): q.validate_report(q.encoded(failed), execution_profile=EAGER)
        for name in ("auto", "batch8-graphs", None, [GRAPH]):
            with self.assertRaises(ValueError): q.validate_report(q.encoded({**failed, "execution_profile": name}))

    def test_owned_hook_failure_records_actual_profile_before_any_request(self):
        with hooks.HookTests().supervisor() as (supervisor, _, _, _):
            supervisor.contract = replace(supervisor.contract, execution_profile=GRAPH)
            supervisor.used, supervisor.phase = True, "ready"
            supervisor.owner = object.__new__(process.ProcessOwner)
            supervisor.owner.prepared = types.SimpleNamespace(receipt_sha256=hooks.SHA)
            with mock.patch.object(supervisor, "cutoff", return_value=0), mock.patch.object(q, "collect") as collect:
                row = q.run_owned(supervisor)
            collect.assert_not_called()
            self.assertEqual((row["execution_profile"], row["failure"], row["samples"]), (GRAPH, "deadline", []))


class PreparedGraphsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tuning_tests.TuningProfiles.setUpClass()

    def test_real_preparation_binding_and_required_measurement_options(self):
        fixture = tuning_tests.TuningProfiles()
        options = dict(tokenizer_parity=True, tokenizer_materialization=True, runtime_observation=True)
        files, contract = fixture.payload(GRAPH, **options)
        self.assertEqual(fixture.validate(files, contract), tuning_tests.fixtures.NEW)
        self.assertEqual(tuning.context_limit(contract), 131072)
        self.assertEqual(engine.canonical_arguments(contract), engine.canonical_arguments(lab()))
        tuning_tests.host.tuning_measurement_options(True, True, True, False, None, None, GRAPH)
        for key in options:
            changed = {**options, key: False}
            with self.assertRaises(ValueError): fixture.payload(GRAPH, **changed)
        with self.assertRaises(ValueError): fixture.payload(GRAPH, **options, stream_cohort=True)
        with self.assertRaises(ValueError):
            fixture.validate({**files, "tuning.py": files["tuning.py"] + b"\n"}, contract)


class GraphFixture:
    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.object(hooks, "LONG", GRAPH))


class GraphHookTests(GraphFixture, hooks.HookTests):
    """Replay the same complete hook success/failure/drain tests for graphs."""


class GraphAdmissionTests(GraphFixture, hooks.AdmissionTests):
    """Replay actual host/process CLIs, journal and source-closure controls."""


if __name__ == "__main__":
    unittest.main()
