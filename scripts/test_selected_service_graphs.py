# SPDX-License-Identifier: Apache-2.0
"""Closed graph profile joins using existing fake clocks, owners and admission."""

from contextlib import redirect_stderr, redirect_stdout
import copy
from dataclasses import replace
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import selected_service as service
import selected_service_process as process
import test_selected_service as lifecycle_tests
import test_selected_service_lifetime as lifetime_tests
import test_selected_service_process as process_tests
import test_selected_service_smoke as smoke_tests

engine = process.engine_entry
host, smoke = lifetime_tests.host, smoke_tests.smoke
GRAPH, SHA = engine.GRAPHS_PROFILE, "a" * 64


def graph_lab():
    lab = process_tests.lab_contract(GRAPH)
    lab.update(execution_profile=GRAPH, tokenizer_parity=True, runtime_observation=True,
        tokenizer_materialization={"schema": "synthetic-authenticated-input"},
        stream_cohort={"schema": "nib.owned-stream-input/v1", "profile": "chat-2k-512-c8-600s"})
    return lab


class EngineTests(unittest.TestCase):
    def test_original_c8_hardware_contract_is_accepted_without_rewriting_it(self):
        raw = (process_tests.ROOT / "evidence/p2/2026-10-24-selected-c8-stream/input-contract.json").read_bytes()
        lab = json.loads(raw)
        arguments = engine.canonical_arguments(lab)
        expected = list(lab["arguments"])
        expected[expected.index("--served-model-name") + 1] = service.MODEL
        self.assertEqual(arguments, expected)
        self.assertEqual(json.loads(raw), lab)

    def test_exact_existing_graph_arguments_only_canonical_name_changes(self):
        lab = graph_lab()
        before = copy.deepcopy(lab)
        arguments = engine.canonical_arguments(lab)
        changed = [index for index, pair in enumerate(zip(lab["arguments"], arguments)) if pair[0] != pair[1]]
        self.assertEqual(changed, [arguments.index("--served-model-name") + 1])
        self.assertEqual(arguments[changed[0]], service.MODEL)
        self.assertEqual(lab, before)
        for flag in ("--tool-call-parser", "--reasoning-parser", "--tokenizer-mode"):
            self.assertEqual(arguments[arguments.index(flag) + 1], "deepseek_v41")

    def test_metadata_profile_capacity_and_capture_mutations_rejected(self):
        for change in ({"stream_cohort": {}}, {"stream_cohort": {"schema": "nib.owned-stream-input/v1",
                "profile": "chat-2k-512-c1-600s"}}, {"tokenizer_parity": False},
                {"runtime_observation": False}, {"tokenizer_materialization": None},
                {"execution_profile": "batch8"}, {"execution_profile": engine.EAGER_PROFILE},
                {"gpu_security": "confidential"}, {"tau_owner": {}}, {"provider_conformance": {}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                engine.canonical_arguments({**graph_lab(), **change})
        for flag, value in (("--max-model-len", "8192"), ("--max-num-seqs", "16"),
                ("--max-num-batched-tokens", "8192"), ("--data-parallel-size", "1"),
                ("--compilation-config", '{"cudagraph_capture_sizes":[1,2,4,8,16]}')):
            lab = graph_lab()
            lab["arguments"][lab["arguments"].index(flag) + 1] = value
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                engine.canonical_arguments(lab)
        for extra in (["--enforce-eager"], ["--speculative-config", "{}"], ["--max-num-seqs", "8"]):
            lab = graph_lab()
            lab["arguments"] += extra
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                engine.canonical_arguments(lab)

    def test_existing_derivation_and_entry_preserve_graph_metadata_without_lab_requests(self):
        lab = graph_lab()
        # Reuse the real source/wrapper derivation tests with the graph contract.
        with mock.patch.object(process_tests, "lab_contract", return_value=lab):
            fixture = process_tests.EngineEntryTests()
            fixture.test_payload_derivation_does_not_mutate_original_or_fake_its_receipt()
            fixture.test_composed_launcher_preserves_definitions_and_original_worker_dispatch()
        # The entry test supplies only run_engine, recheck_sources and cache init;
        # cohort/parity launch helpers are deliberately absent from its globals.


class ContractTests(unittest.TestCase):
    def test_closed_selection_lifetimes_and_no_independent_capacity_knobs(self):
        clock = lifecycle_tests.Clock()
        with mock.patch.object(service, "time", clock), mock.patch.object(service.pilot, "time", clock):
            for execution, context, slots in ((engine.EAGER_PROFILE, 8192, 1), (GRAPH, 4096, 8)):
                for lifetime, seconds in ((service.SMOKE_PROFILE, 4800), (service.ORDINARY_PROFILE, 86400)):
                    row = service.Contract.begin(clock.timestamp(seconds), input_price="1", output_price="2",
                        lifetime_profile=lifetime, execution_profile=execution)
                    self.assertEqual((row.context_tokens, row.max_output_tokens, row.max_inflight), (context, 256, slots))
                    self.assertEqual(row.hard_monotonic, clock.mono + seconds)
                    self.assertEqual((row.memory_bytes, row.swap_bytes), (512 << 30, 0))
                    args = row.guard_arguments(service.Inputs(Path("/unused"), {}))
                    self.assertEqual(args[args.index("--profile") + 1], row.execution["guard_profile"])
                    # CPython 3.14 changed dataclasses.replace's exception type.
                    with self.assertRaises((ValueError, TypeError)):
                        replace(row, max_inflight=16)
            for invalid in (None, True, [], {}, "batch8-graphs ", "PRIVATE_CANARY"):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    service.Contract.begin(clock.timestamp(3600), input_price="1", output_price="2",
                                           execution_profile=invalid)

    def test_readiness_profile_and_observed_context_are_both_required(self):
        with lifecycle_tests.LifecycleTests().setup_service() as (supervisor, backend, _, _):
            supervisor.contract = replace(supervisor.contract, execution_profile=GRAPH)
            child = lifecycle_tests.Child(1000)
            supervisor.children["engine"] = child
            for observed_profile, context in ((GRAPH, 4096), (engine.EAGER_PROFILE, 8192), (GRAPH, 8192)):
                backend.observation = service.EngineReady(lifecycle_tests.endpoint(1000),
                    json.dumps({"data": [{"id": service.MODEL, "max_model_len": context}]}).encode(),
                    context, service.MEMORY_BYTES, 0, observed_profile)
                if observed_profile == GRAPH and context == 4096:
                    self.assertTrue(supervisor.ready("engine", 1000))
                else:
                    with self.assertRaises(ValueError):
                        supervisor.ready("engine", 1000)

    def test_cli_mismatch_stops_before_private_inputs_and_emits_closed_failure(self):
        clock = lifecycle_tests.Clock()
        args = ["--prepared-sha256", SHA, "--provider-config", "/PRIVATE_CANARY",
            "--api-key-file", "/PRIVATE_CANARY", "--cert", "/PRIVATE_CANARY", "--key", "/PRIVATE_CANARY",
            "--hostname", "private.invalid", "--input-price", "1", "--output-price", "2",
            "--hard-stop-at", clock.timestamp(3600), "--execution-profile", GRAPH]
        output = io.StringIO()
        with mock.patch.object(service, "time", clock), mock.patch.object(service.pilot, "time", clock), \
                mock.patch.object(process, "Prepared", return_value=SimpleNamespace(
                    lab={"execution_profile": engine.EAGER_PROFILE})), \
                mock.patch.object(service, "snapshot_inputs") as inputs, \
                mock.patch.object(process.subprocess, "Popen") as spawn, redirect_stdout(output):
            self.assertEqual(process.main(args), 1)
        inputs.assert_not_called()
        spawn.assert_not_called()
        self.assertNotIn("PRIVATE_CANARY", output.getvalue())
        row = smoke.lifecycle_failure(output.getvalue().encode(), execution_profile=GRAPH, prepared_sha256=SHA)
        self.assertEqual(row["reason"], "preparation")

    def test_process_tls_guard_slots_and_mismatch_before_child_creation(self):
        fixture = process_tests.ProcessTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.prepared.lab = graph_lab()
        fixture.contract = replace(fixture.contract, execution_profile=GRAPH)
        children = [fixture.child(9000 + index) for index in range(4)]
        with mock.patch.object(process.subprocess, "Popen", side_effect=children) as spawn, \
                mock.patch.object(fixture.owner, "reap", return_value=True):
            for role in service.ROLES:
                fixture.owner.start(role, fixture.contract, fixture.inputs, mock.Mock(), 1900)
        guard, tls = [call.args[0] for call in spawn.call_args_list[-2:]]
        self.assertEqual(guard[guard.index("--profile") + 1], "selected-deepseek-graphs")
        self.assertEqual(tls[tls.index("--max-inflight") + 1], "8")
        fixture.prepared.lab["execution_profile"] = engine.EAGER_PROFILE
        with mock.patch.object(process.subprocess, "Popen") as spawn, self.assertRaises(ValueError):
            fixture.owner.start("engine", fixture.contract, fixture.inputs, mock.Mock(), 1900)
        spawn.assert_not_called()

    def test_clock_cancel_and_cleanup_behavior_unchanged_for_both_lifetimes(self):
        for lifetime in (service.SMOKE_PROFILE, service.ORDINARY_PROFILE):
            for fault in (None, "cancel", "reap", "profile", "clock"):
                fixture = lifetime_tests.LifetimeTests()
                fixture.setUp()
                try:
                    supervisor, backend = fixture.supervisor(lifetime)
                    supervisor.contract = replace(supervisor.contract, execution_profile=GRAPH)
                    backend.observation = service.EngineReady(lifecycle_tests.endpoint(1000),
                        json.dumps({"data": [{"id": service.MODEL, "max_model_len": 4096}]}).encode(),
                        4096, service.MEMORY_BYTES, 0, GRAPH)
                    if fault == "cancel":
                        supervisor.shutdown.stopped = True
                    elif fault == "reap":
                        backend.reap = lambda *_: False
                    elif fault == "profile":
                        backend.observation = replace(backend.observation, execution_profile=engine.EAGER_PROFILE)
                    elif fault == "clock":
                        fixture.clock.wall += 86401
                    row = supervisor.run()
                    kwargs = {"execution_profile": GRAPH, "prepared_sha256": SHA}
                    if lifetime == service.ORDINARY_PROFILE:
                        kwargs["lifetime"] = supervisor.contract.lifetime
                    if fault is None:
                        self.assertEqual(smoke.lifecycle(smoke_tests.encoded(row), **kwargs), row)
                    else:
                        with self.assertRaises(ValueError):
                            smoke.lifecycle(smoke_tests.encoded(row), **kwargs)
                    if fault == "reap":
                        self.assertFalse(row["cleanup_confirmed"])
                    else:
                        self.assertTrue(all(child.poll() is not None for child in backend.children.values()))
                    self.assertLessEqual(fixture.clock.mono, supervisor.contract.hard_monotonic)
                    self.assertFalse(row["deployable"] or row["hardware_validated"] or row["confidential"])
                finally:
                    fixture.doCleanups()


class AdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        smoke_tests.SmokeTests.setUpClass()

    def test_real_shared_admission_binds_lab_selection_and_current_source_before_inputs(self):
        fixture = smoke_tests.SmokeTests()
        fixture.setUp()
        args = copy.copy(fixture.args)
        args.execution_profile = GRAPH
        files = {"context/service/" + name: (smoke.ROOT / name).read_bytes() for name in
                 ("scripts/selected_service.py", "scripts/selected_service_process.py",
                  "scripts/selected_service_engine.py")}
        fixture.lab_contract["execution_profile"] = GRAPH
        for mutation in (None, "profile", *files):
            selected_files = dict(files)
            lab = dict(fixture.lab_contract)
            if mutation == "profile":
                lab["execution_profile"] = engine.EAGER_PROFILE
            elif mutation is not None:
                selected_files[mutation] = b"stale source"
            with self.subTest(mutation=mutation), fixture.admission() as (inspect, _), \
                    mock.patch.object(smoke.image_builder, "validated_files", return_value=(
                        {"prepared_sha256": fixture.build["prepared_sha256"]}, selected_files, lab)):
                if mutation is not None:
                    with self.assertRaises(ValueError), smoke.admitted_runtime(args, smoke.report()):
                        self.fail("stale profile/source admitted")
                    inspect.assert_not_called()
                else:
                    with smoke.admitted_runtime(args, smoke.report()) as admitted:
                        self.assertEqual(admitted[0].execution, engine.execution_limits(GRAPH))
                        self.assertEqual(admitted[3], fixture.lab.controlled_env(args.docker_host))

    def test_default_container_command_unchanged_and_graph_command_exactly_opt_in(self):
        fixture = smoke_tests.SmokeTests()
        fixture.setUp()
        args = fixture.args
        def command():
            return smoke.container_command("nib-selected-" + "1" * 32, "2" * 32, fixture.build,
                fixture.base, args.weights, "/private/stage", args, args.hard_stop_at)
        original = command()
        args.execution_profile = engine.EAGER_PROFILE
        self.assertEqual(command(), original)
        args.execution_profile = GRAPH
        actual = command()
        index = actual.index("--execution-profile")
        self.assertEqual(actual[index:index + 2], ["--execution-profile", GRAPH])
        self.assertEqual(actual[:index] + actual[index + 2:], original)
        self.assertNotIn("--publish", actual)


class ReceiptTests(unittest.TestCase):
    def test_closed_graph_envelope_cannot_relabel_old_results_or_change_limits(self):
        base = smoke_tests.lifecycle()
        self.assertIs(service.execution_result(base, engine.EAGER_PROFILE, SHA), base)
        row = service.execution_result(base, GRAPH, SHA)
        kwargs = {"execution_profile": GRAPH, "prepared_sha256": SHA}
        self.assertEqual(smoke.lifecycle(smoke_tests.encoded(row), **kwargs), row)
        for changed in (base, {**row, "schema": base["schema"]}, {**row, "extra": "PRIVATE_CANARY"},
                {**row, "execution_prepared_sha256": "b" * 64},
                *[{**row, "execution": {**row["execution"], key: value}} for key, value in
                  (("context_tokens", 8192), ("max_output_tokens", 512), ("max_inflight", 16),
                   ("profile", engine.EAGER_PROFILE), ("guard_profile", "selected-deepseek"))],
                {**row, "cleanup_confirmed": False}, {**row, "lifecycle_schema": service.EXECUTION_SCHEMA}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                smoke.lifecycle(smoke_tests.encoded(changed), **kwargs)
        with self.assertRaises(ValueError):
            smoke.lifecycle(smoke_tests.encoded(row))
        failed = service.execution_result({**base, "status": "failed", "reason": "deadline"}, GRAPH, SHA)
        self.assertEqual(smoke.lifecycle_failure(smoke_tests.encoded(failed), **kwargs), failed)

    def test_mode_failure_preserves_unknown_usage_and_requires_matching_mode(self):
        row = {**smoke_tests.lifecycle(), "schema": service.INFERENCE_SCHEMA,
               "mode": service.INFERENCE_MODE, "status": "failed", "reason": "owner-failed",
               "inference": None}
        row = service.execution_result(row, GRAPH, SHA)
        kwargs = {"execution_profile": GRAPH, "prepared_sha256": SHA}
        self.assertEqual(smoke.lifecycle_failure(smoke_tests.encoded(row), inference_smoke=True, **kwargs), row)
        for join in ({}, {"tool_smoke": True}, {"protocol_smoke": True}, {"conformance_smoke": True}):
            with self.subTest(join=join), self.assertRaises(ValueError):
                smoke.lifecycle_failure(smoke_tests.encoded(row), **kwargs, **join)

    def test_day_progress_and_terminal_are_bound_to_both_profiles(self):
        fixture = lifetime_tests.ProgressTests()
        fixture.setUp()
        rows = [service.progress_record(phase, fixture.lifetime, SHA, GRAPH) for phase in service.PROGRESS_PHASES]
        terminal = service.execution_result(fixture.final, GRAPH, SHA)
        wire = b"".join(smoke_tests.encoded(row) + b"\n" for row in [*rows, terminal])
        sink, output = host.AttachedProgress(fixture.lifetime, SHA, GRAPH), io.StringIO()
        with redirect_stderr(output), mock.patch.object(host, "time", fixture.clock):
            for offset in range(0, len(wire), 7):
                sink.write(wire[offset:offset + 7])
        self.assertEqual(sink.finish(success=True), smoke_tests.encoded(terminal))
        self.assertEqual(len(output.getvalue().splitlines()), len(rows))
        for profile, row in ((engine.EAGER_PROFILE, rows[0]), (GRAPH, fixture.rows[0]),
                            (GRAPH, {**rows[0], "execution": engine.execution_limits(engine.EAGER_PROFILE)})):
            with redirect_stderr(io.StringIO()), self.assertRaises(ValueError):
                host.AttachedProgress(fixture.lifetime, SHA, profile).write(smoke_tests.encoded(row) + b"\n")


if __name__ == "__main__":
    unittest.main()
