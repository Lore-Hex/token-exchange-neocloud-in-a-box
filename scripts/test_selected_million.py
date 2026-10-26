# SPDX-License-Identifier: Apache-2.0
"""Source-bound 1M candidate tests; no GPU, performance or deployment claim."""

import copy
import io
from contextlib import ExitStack, redirect_stdout
from dataclasses import replace
import json
import sys
import time
from types import SimpleNamespace
import unittest
from unittest import mock

import selected_long_qualification as q
import selected_service_engine as engine
import test_selected_long_hook as hooks
import test_selected_service as lifecycle
import test_selected_service_process as processes
import test_selected_service_long as legacy
import test_selected_long_graphs as graphs
import test_stream_measurement as stream_tests
import test_deepseek_tuning as tuning_tests

PROFILE = engine.MILLION_PROFILE
service = q.service


def workload():
    profile = q.workload_profile(PROFILE)
    return {"schema": "nib.selected-long-workload/v2", "execution_profile": PROFILE, "cases": [
        {"level": level, "padding": 100 + level, "prompt_tokens": target,
         "tokens_sha256": "a" * 64,
         "messages_sha256": q.digest(q.messages(level, 100 + level, execution_profile=PROFILE))}
        for level, target in enumerate(profile.prompts)]}


def result(*, short=False):
    profile = q.workload_profile(PROFILE)
    report = q.empty_report(PROFILE)
    report.update(prepared_sha256="a" * 64, request_seconds=3540, cleanup_reserve_seconds=7240,
                  workload=workload(), engine_identity_before_sha256="b" * 64)
    def collect(work, level, namespace, deadline, row):
        row["attempted"] = True
        output = profile.output if level == 3 and not short else 4
        sample = q.measurement.Sample("completed", 3.0, 1.0, 1.5, 2.0, 2,
            profile.prompts[level], output, 64 if row["scope"] == "warm" else 0,
            "length" if level == 3 and not short else "stop")
        return q.Observation(sample, True, True)
    q.sequence(report, report["workload"], collect, deadline=100, clock=lambda: 0)
    report.update(result="completed", failure="none", elapsed_seconds=50.0,
                  engine_identity_after_sha256="b" * 64)
    q.summarize(report)
    return q.validate_report(q.encoded(report), execution_profile=PROFILE)


class MillionTests(unittest.TestCase):
    def test_exact_legacy_normalization_rejects_unreviewed_limit_changes(self):
        from fixtures.stream_profile_compat import legacy_source
        for name in ("stream_transport", "stream_measurement"):
            raw = (hooks.smoke.ROOT / "scripts" / (name + ".py")).read_bytes()
            normalized = legacy_source(raw, name)
            self.assertNotIn(b"MILLION_PROFILE", normalized)
            for original, changed in ((b"profile=None", b"profile='auto'"),
                    (b"stream_limit(profile)", b"stream_limit(None)")):
                with self.assertRaises(AssertionError):
                    legacy_source(raw.replace(original, changed), name)

    def test_engine_and_closed_guard_agree_and_old_profile_unchanged(self):
        lab = {**legacy.long_lab(), **processes.lab_contract(PROFILE)}
        old = engine.canonical_arguments(graphs.lab())
        expected = list(old)
        expected[expected.index("--max-model-len") + 1] = "1048576"
        self.assertEqual(engine.canonical_arguments(lab), expected)
        self.assertEqual(engine.execution_limits(PROFILE), {"profile": PROFILE,
            "context_tokens": 1048576, "max_output_tokens": 393216, "max_inflight": 1,
            "guard_profile": "selected-deepseek-million"})
        for flag in ("--max-model-len", "--max-num-batched-tokens", "--max-num-seqs"):
            tampered = copy.deepcopy(lab)
            tampered["arguments"][tampered["arguments"].index(flag) + 1] = "9999"
            with self.assertRaises(ValueError): engine.canonical_arguments(tampered)
        with self.assertRaises(ValueError):
            engine.canonical_arguments({**lab, "gpu_security": "confidential"})
        for name in engine.LONG_PROFILES:
            self.assertEqual(service.request_seconds(name), 900)
            self.assertEqual(service.request_bytes(name), 4 << 20)
            self.assertEqual(service.cleanup_reserve(name), 1960)

    def test_fixed_deadlines_reserve_both_drains_and_tls_cap(self):
        self.assertEqual((service.request_seconds(PROFILE), service.drain_seconds(PROFILE),
                          service.cleanup_reserve(PROFILE)), (3540, 3570, 7240))
        self.assertLessEqual(service.request_seconds(PROFILE) + 10, 3600)
        self.assertEqual(hooks.smoke.cleanup_budget({"Config": {"Cmd": ["--execution-profile", PROFILE]}}), 7240)
        clock = lifecycle.Clock()
        with mock.patch.object(service, "time", clock), mock.patch.object(service.pilot, "time", clock):
            for seconds in (4800, 7240):
                with self.assertRaises(ValueError):
                    service.Contract.begin(clock.timestamp(seconds), input_price="1", output_price="2",
                        lifetime_profile=service.ORDINARY_PROFILE, execution_profile=PROFILE)
            contract = service.Contract.begin(clock.timestamp(18000), input_price="1", output_price="2",
                lifetime_profile=service.ORDINARY_PROFILE, execution_profile=PROFILE)
            self.assertEqual(contract.admission_stop_at, clock.timestamp(10760))
            self.assertEqual((contract.context_tokens, contract.max_output_tokens), (1048576, 393216))
            clock.advance(18000)
            with self.assertRaises(ValueError):
                service.Contract.begin(contract.hard_stop_at, input_price="1", output_price="2",
                    lifetime_profile=service.ORDINARY_PROFILE, execution_profile=PROFILE)

    def test_frontend_body_admission_and_grace_match(self):
        fixture = processes.ProcessTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.prepared.lab = processes.lab_contract(PROFILE)
        contract = replace(fixture.contract, execution_profile=PROFILE)
        with mock.patch.object(processes.process.subprocess, "Popen", return_value=fixture.child()) as spawn:
            fixture.owner.start("tls", contract, fixture.inputs, lambda _: None, 1900)
        args = spawn.call_args.args[0]
        self.assertEqual(args[args.index("--max-request-bytes") + 1], str(16 << 20))
        self.assertEqual(args[args.index("--shutdown-grace") + 1], "3550s")
        self.assertEqual(args[args.index("--max-inflight") + 1], "1")

    def test_tokenizer_exact_counts_and_shared_context_budget(self):
        class Tokenizer:
            def apply_chat_template(self, messages, **kwargs):
                return [3] * (messages[0]["content"].count(" x") + 24)
        row = q.prepare(Tokenizer(), deadline=time.monotonic() + 30, execution_profile=PROFILE)
        profile = q.workload_profile(PROFILE)
        self.assertEqual(profile.prompts, (261888, 524032, 1048320, 655360))
        self.assertEqual(profile.prompts[-1] + profile.output, 1048576)
        for level, target in enumerate(profile.prompts):
            self.assertEqual(row["cases"][level]["prompt_tokens"], target)
            request = json.loads(q.request_bytes(row, level, "a" * 64))
            self.assertEqual(request["max_tokens"], profile.outputs[level])
            self.assertLessEqual(len(q.encoded(request)), service.request_bytes(PROFILE))
        with self.assertRaises(ValueError): q.validate_workload(row, execution_profile=engine.LONG_PROFILE)
        with self.assertRaises(ValueError): q.validate_workload(legacy.long_lab())

    def test_report_requires_real_full_output_and_cannot_be_relabelled(self):
        row = result()
        self.assertTrue(row["checks_pass"])
        self.assertTrue(row["long_output_limit_reached"])
        self.assertEqual(row["reserved_prompt_tokens"], 6158080)
        self.assertEqual(row["reserved_output_tokens"], 395520)
        self.assertTrue(all(row[key] is False for key in q.CLAIMS))
        self.assertFalse(result(short=True)["checks_pass"])
        for change in ({"execution_profile": engine.LONG_GRAPHS_PROFILE},
                       {"schema": q.SCHEMA}, {"request_seconds": 3541},
                       {"cleanup_reserve_seconds": 1960}):
            with self.assertRaises(ValueError): q.validate_report(q.encoded({**row, **change}))
        changed = copy.deepcopy(row)
        changed["samples"][-1]["measurement"]["completion_tokens"] = 393217
        with self.assertRaises(ValueError): q.validate_report(q.encoded(changed))

    def test_profile_specific_stream_ceiling_does_not_widen_old_clients(self):
        args = dict(timeout_seconds=3540, max_prompt_tokens=1048576, max_output_tokens=393216)
        with self.assertRaises(ValueError): q.measurement.StreamMeasurement(service.MODEL, **args)
        meter = q.measurement.StreamMeasurement(service.MODEL, profile=q.measurement.MILLION_PROFILE, **args)
        meter.bytes = 64 << 20
        meter.feed(b": keepalive\n\n", 1801)
        self.assertIsNone(meter.failure)
        meter.bytes = 512 << 20
        meter.feed(b": keepalive\n\n", 1802)
        self.assertEqual(meter.failure, "size-limit")
        for change in ({"timeout_seconds": 3541}, {"max_output_tokens": 393217}, {"max_prompt_tokens": 1048577}):
            with self.assertRaises(ValueError):
                q.measurement.StreamMeasurement(service.MODEL, profile=q.measurement.MILLION_PROFILE, **{**args, **change})

    def test_full_output_usage_is_parsed_without_retaining_content(self):
        for count in (393216, 393217):
            meter = q.measurement.StreamMeasurement(stream_tests.MODEL, profile=q.measurement.MILLION_PROFILE,
                timeout_seconds=3540, max_prompt_tokens=1048576, max_output_tokens=393216)
            meter.feed(stream_tests.event(stream_tests.chunk({"content": "synthetic private output"})), 1)
            meter.feed(stream_tests.event(stream_tests.chunk({"content": "continued output"})), 1801)
            meter.feed(stream_tests.event(stream_tests.chunk(finish="length",
                usage=stream_tests.usage(prompt=655360, output=count, cached=655296))), 3000)
            meter.feed(stream_tests.event(b"[DONE]"), 3001)
            sample = meter.close(3002)
            self.assertEqual(sample.status, "completed" if count == 393216 else "protocol-error")
            if count == 393216:
                self.assertEqual((sample.prompt_tokens, sample.completion_tokens, sample.cached_prompt_tokens),
                                 (655360, 393216, 655296))
            self.assertNotIn("synthetic private output", repr(vars(meter)) + repr(sample))

    def test_tokenizer_child_and_report_size_are_bounded(self):
        owner = SimpleNamespace(prepared=SimpleNamespace(interpreter="/pinned/python", receipt_sha256="a" * 64,
                                lab={"environment": {}}, check=mock.Mock()))
        supervisor = SimpleNamespace(owner=owner, contract=SimpleNamespace(execution_profile=PROFILE))
        payload = q.encoded({"prepared_sha256": "a" * 64, "workload": workload()})
        self.assertLessEqual(len(payload), 8192)
        child = SimpleNamespace(returncode=0, stdout=payload, stderr=b"")
        with mock.patch.object(q.time, "monotonic", return_value=100), \
                mock.patch.object(q.process.snapshot, "execute_process_group", return_value=child) as spawn:
            self.assertEqual(q.prepare_owned(supervisor, 1000), workload())
        self.assertEqual(spawn.call_args.args[0][-1], PROFILE)
        self.assertEqual(spawn.call_args.kwargs["timeout"], 600)
        self.assertLessEqual(len(q.encoded(result())), q.MAX_REPORT)

    def test_stage_deadline_prevents_dispatch_and_preserves_failure_report(self):
        row = result()
        row.update(samples=[], result="fail", failure="deadline")
        with mock.patch.object(q, "collect") as collector, self.assertRaises(q.Rejected):
            q.sequence(row, workload(), collector, deadline=1, clock=lambda: 1)
        collector.assert_not_called()
        q.summarize(row)
        self.assertEqual(q.validate_report(q.encoded(row))["samples"], [])

    def test_process_rejects_exact_budget_before_any_inputs(self):
        clock = lifecycle.Clock()
        # The 7200s stage includes its 600s tokenizer child; cleanup is separate.
        args = ["--prepared-sha256", "a" * 64, "--provider-config", "/PRIVATE_CANARY", "--api-key-file", "/PRIVATE_CANARY",
            "--cert", "/PRIVATE_CANARY", "--key", "/PRIVATE_CANARY", "--hostname", "private.invalid",
            "--input-price", "1", "--output-price", "2", "--hard-stop-at", clock.timestamp(16240),
            "--execution-profile", PROFILE, "--lifetime-profile", service.ORDINARY_PROFILE, "--long-qualification"]
        with ExitStack() as stack:
            for module in (service, service.pilot, q.process):
                stack.enter_context(mock.patch.object(module, "time", clock))
            prepared = stack.enter_context(mock.patch.object(q.process, "Prepared"))
            inputs = stack.enter_context(mock.patch.object(service, "snapshot_inputs"))
            spawn = stack.enter_context(mock.patch.object(q.process.subprocess, "Popen"))
            output = io.StringIO()
            stack.enter_context(redirect_stdout(output))
            self.assertEqual(q.process.main(args), 1)
        for call in (prepared, inputs, spawn):
            call.assert_not_called()
        self.assertNotIn("PRIVATE_CANARY", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["reason"], "preparation")

    def test_million_report_joins_real_lifecycle_and_cleanup_validation(self):
        with hooks.HookTests().supervisor() as (supervisor, backend, clock, _):
            supervisor.contract = service.Contract.begin(clock.timestamp(18000), input_price="1", output_price="2",
                lifetime_profile=service.ORDINARY_PROFILE, execution_profile=PROFILE)
            supervisor.hard_deadline = supervisor.contract.hard_monotonic
            backend.observation = replace(backend.observation, context_tokens=1048576,
                models_json=json.dumps({"data": [{"id": service.MODEL, "max_model_len": 1048576}]}).encode(),
                execution_profile=PROFILE)
            with mock.patch.object(q, "run_owned", return_value=result()):
                report = supervisor.run(long_qualification=True)
            self.assertEqual(report["status"], "retired", report)
            self.assertTrue(report["cleanup_confirmed"])
            self.assertEqual(hooks.smoke.lifecycle(q.encoded(report), lifetime=supervisor.contract.lifetime,
                prepared_sha256="a" * 64, execution_profile=PROFILE, long_qualification=True), report)


class PreparedMillionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tuning_tests.TuningProfiles.setUpClass()

    def test_real_preparation_binding_requires_tokenizer_and_observation(self):
        fixture = tuning_tests.TuningProfiles()
        options = dict(tokenizer_parity=True, tokenizer_materialization=True, runtime_observation=True)
        files, contract = fixture.payload(PROFILE, **options)
        self.assertEqual(fixture.validate(files, contract), tuning_tests.fixtures.NEW)
        self.assertEqual(tuning_tests.tuning.context_limit(contract), 1048576)
        self.assertEqual(engine.canonical_arguments(contract),
                         engine.canonical_arguments({**legacy.long_lab(), **processes.lab_contract(PROFILE)}))
        for key in options:
            with self.assertRaises(ValueError): fixture.payload(PROFILE, **{**options, key: False})
        with self.assertRaises(ValueError):
            fixture.validate({**files, "tuning.py": files["tuning.py"] + b"\n"}, contract)

    def test_operator_preparation_admits_only_exact_million_options(self):
        import itertools
        host = tuning_tests.host
        for choices in itertools.product((False, True), repeat=4):
            if choices == (True, True, True, False):
                host.tuning_measurement_options(*choices, None, None, PROFILE)
            else:
                with self.assertRaises(ValueError):
                    host.tuning_measurement_options(*choices, None, None, PROFILE)
        for profile, provider in (("chat-2k-512", None), (None, "provider")):
            with self.assertRaises(ValueError):
                host.tuning_measurement_options(True, True, True, False, profile, provider, PROFILE)
        flags = ["--tokenizer-parity", "--tokenizer-materialization", "--runtime-observation"]
        command = [host.SCRIPT, "--prepare-nonconfidential-uncensored-b300-dp2", "/unused",
                   "--model-profile", "uncensored", "--execution-profile", PROFILE]
        with mock.patch.object(sys, "argv", command + flags), mock.patch.object(host, "prepare") as prepare:
            self.assertEqual(host.main(), 0)
            prepare.assert_called_once_with("/unused", "local-correctness", "B300", "nonconfidential", 2,
                model_profile="uncensored", execution_profile=PROFILE, tokenizer_parity=True,
                tokenizer_materialization=True, runtime_observation=True)
        for missing in flags:
            with mock.patch.object(sys, "argv", command + [flag for flag in flags if flag != missing]), \
                    mock.patch.object(host, "prepare") as prepare, self.assertRaises(ValueError):
                host.main()
            prepare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
