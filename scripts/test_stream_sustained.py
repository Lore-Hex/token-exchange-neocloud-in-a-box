# SPDX-License-Identifier: Apache-2.0
"""Synthetic clocks, metadata and private child pipes; no benchmark claim."""

import copy
from contextlib import ExitStack, nullcontext
import hashlib
import json
import os
from pathlib import Path
import signal
import tempfile
import time
import types
import unittest
from unittest import mock

import stream_sustained as sustained
import test_stream_c8 as c8_tests
from test_stream_cohort import workload
import test_deepseek_tuning as tuning_tests
from test_evidence_history import git

c8, cohort, binding = sustained.c8, sustained.cohort, sustained.binding
MODEL, endpoint, selected = c8_tests.MODEL, c8_tests.endpoint, c8_tests.selected
ROOT = Path(__file__).absolute().parents[1]
BASE = "3a33577f1f666ad294a65046fe0a7580bf20a2bc"
SUSTAINED_SOURCE = "dd4705c3483bb9f333642c5a904c9d7b74396e09"


def contract():
    row = selected()
    row["stream_cohort"]["profile"] = binding.SUSTAINED_PROFILE
    row["sources"]["stream/stream_sustained.py"] = hashlib.sha256(
        (ROOT / "scripts/stream_sustained.py").read_bytes()).hexdigest()
    return row


class Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


def sample(seconds=4.0, *, failed=False, unknown=False):
    return c8.meter.Sample("transport-error" if failed else "completed", seconds,
        None if failed else .1, None if failed else .2, None if failed else seconds - .1,
        0 if unknown else 2, None if unknown else 2048, None if unknown else 5,
        None if unknown else 0, None if unknown else "stop").metadata()


def wave_report(seconds=5.0):
    samples = [sample(seconds - 1) for _ in range(8)]
    return {"schema": c8.WAVE_SCHEMA, "model": MODEL, "workload": workload().metadata(), "concurrency": 8,
        "prompt_tokens_per_request": 2048, "max_output_tokens_per_request": 512,
        "reserved_requests": 8, "reserved_total_tokens": 20480, "started_requests": 8, "not_run_requests": 0,
        "known_prompt_tokens": 16384, "known_output_tokens": 40, "requests_with_unknown_usage": 0,
        "warmup": None, "samples": samples,
        "measured": c8.meter._summarize_rows(samples, scheduled=8, wall_seconds=seconds - .25, gpu_count=8),
        "intervals": [{"index": i, "start": .25, "end": seconds - .75, "reported": True, "exit_code": 0}
                      for i in range(1, 9)],
        "peak_in_flight": 8, "collectors_reaped": True, "elapsed_seconds": seconds,
        "stop_reason": "complete", "completed": True, "pricing_measured": False,
        "quality_accepted": False, "confidential": False, "sweep_accepted": False}


def settle_wave(row):
    rows = row["samples"]
    row.update(known_prompt_tokens=sum(s["prompt_tokens"] or 0 for s in rows),
        known_output_tokens=sum(s["completion_tokens"] or 0 for s in rows),
        requests_with_unknown_usage=sum(s["completion_tokens"] is None for s in rows),
        measured=c8.meter._summarize_rows(rows, scheduled=8,
            wall_seconds=row["measured"]["wall_seconds"], gpu_count=8))


class SustainedTests(unittest.TestCase):
    def execute(self, *, seconds=5.0, budget=600, change=None, interrupt=None, identity_failure=None,
                warmup_failure=False, gap=0):
        clock, owned, calls = Clock(), endpoint(), []
        def warmup(*_):
            clock.now += 1
            return c8.meter.Sample("transport-error" if warmup_failure else "completed", 1,
                None if warmup_failure else .1, None if warmup_failure else .2,
                None if warmup_failure else .9, 2, 2048, 5, 0, "stop")
        def wave(ep, work, record, *, deadline, model):
            self.assertIs(ep, owned)
            self.assertEqual((model, deadline), (MODEL, 1000 + min(budget, 600)))
            calls.append(deadline)
            clock.now += seconds
            if interrupt == "lost":
                raise KeyboardInterrupt("PRIVATE_CANARY")
            record.update(wave_report(seconds))
            if change:
                change(record, len(calls))
            if interrupt == "settled":
                raise KeyboardInterrupt("PRIVATE_CANARY")
        probes = 0
        def check(_):
            nonlocal probes
            probes += 1
            if probes == identity_failure:
                raise ValueError("PRIVATE_CANARY")
            if probes > 1:
                clock.now += gap
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(sustained, "time", clock))
            collect = stack.enter_context(mock.patch.object(cohort, "_collect", side_effect=warmup))
            stack.enter_context(mock.patch.object(c8, "wave", side_effect=wave))
            stack.enter_context(mock.patch.object(owned, "check", side_effect=check))
            stack.enter_context(mock.patch.object(sustained.stream.worker, "alarm", return_value=nullcontext()))
            row = sustained.run(owned, workload(), deadline=clock.now + budget,
                gpu_count=8, cohort_seconds=600, model=MODEL)
        self.validate(row)
        self.assertNotIn("PRIVATE_CANARY", json.dumps(row))
        return row, calls, collect.call_count

    def validate(self, row):
        with mock.patch.object(cohort, "c8", c8, create=True), \
                mock.patch.object(cohort, "sustained", sustained, create=True):
            value = {"schema": binding.RESULT_SCHEMA, "contract_sha256": "a" * 64,
                "engine_identity_sha256": "b" * 64, "terminal_identity_verified": True, "cohort": row}
            self.assertEqual(binding.validate(value, contract(), "a" * 64, cohort, model=MODEL), row["completed"])
        return value

    def test_fixed_counts_one_warmup_full_window_and_unchanged_requests(self):
        row, calls, warmups = self.execute(gap=.1)
        self.assertEqual((warmups, len(calls), row["started_requests"]), (1, 32, 257))
        self.assertEqual(row["reserved_total_tokens"], 657920)
        self.assertTrue(row["completed"] and row["collectors_reaped"])
        self.assertEqual(row["known_output_tokens"], 1285)
        self.assertEqual(row["measured"]["completed_output_tokens"], 1280)
        self.assertGreater(row["measured"]["wall_seconds"], sum(w["cohort"]["elapsed_seconds"] for w in row["waves"]))
        self.assertEqual(row["measured"]["wall_seconds"], row["elapsed_seconds"] - row["measurement_start_seconds"])
        self.assertFalse(row["saturation_demonstrated"])
        self.assertLess(len(json.dumps(row)), 450000)

    def test_short_run_is_insufficient_duration_not_padded_or_retried(self):
        row, calls, _ = self.execute(seconds=2)
        self.assertEqual((row["stop_reason"], len(calls)), ("insufficient-duration", 32))
        self.assertFalse(row["completed"])

    def test_deadline_never_renews_and_not_run_is_explicit(self):
        row, calls, _ = self.execute(budget=80)
        self.assertEqual((len(calls), row["stop_reason"]), (2, "deadline"))
        self.assertEqual(row["not_run_requests"], 240)
        self.assertEqual(len(set(calls)), 1)

    def test_expired_deadline_does_not_warm_or_dispatch(self):
        row, calls, warmups = self.execute(budget=0)
        self.assertEqual((calls, warmups, row["started_requests"]), ([], 0, 0))
        self.assertEqual(row["stop_reason"], "deadline")

    def test_failed_warmup_preserves_known_usage_without_waves(self):
        row, calls, _ = self.execute(warmup_failure=True)
        self.assertEqual((row["stop_reason"], calls), ("warmup-failed", []))
        self.assertEqual(row["known_output_tokens"], 5)

    def test_identity_drift_stops_before_next_wave_preserving_completed_usage(self):
        row, calls, _ = self.execute(identity_failure=3)
        self.assertEqual((row["stop_reason"], len(calls)), ("identity-failed", 1))
        self.assertEqual(row["known_output_tokens"], 45)

    def test_cancellation_after_settlement_keeps_usage_and_cleanup(self):
        row, _, _ = self.execute(interrupt="settled")
        self.assertEqual(row["stop_reason"], "cancelled")
        self.assertEqual(row["known_output_tokens"], 45)
        self.assertTrue(row["collectors_reaped"])
        self.assertEqual(row["requests_with_unknown_usage"], 0)

    def test_cancellation_during_final_restoration_retains_all_waves(self):
        restore = c8.restore_handlers
        def interrupt(handlers):
            restore(handlers)
            raise KeyboardInterrupt("PRIVATE_CANARY")
        handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        with mock.patch.object(c8, "restore_handlers", side_effect=interrupt):
            row, calls, _ = self.execute()
        self.assertEqual((row["stop_reason"], len(calls), row["known_output_tokens"]), ("cancelled", 32, 1285))
        self.assertTrue(row["collectors_reaped"])
        for sig, handler in handlers.items():
            self.assertEqual(signal.getsignal(sig), handler)

    def test_lost_wave_never_claims_free_work_or_reap(self):
        row, _, _ = self.execute(interrupt="lost")
        self.assertEqual(row["dispatch_unknown_requests"], 8)
        self.assertEqual(row["requests_with_unknown_usage"], 8)
        self.assertEqual(row["not_run_requests"], 248)
        self.assertEqual(row["measured"]["not_run"], 248)
        self.assertEqual(row["measured"]["requests_with_unknown_usage"], 8)
        self.assertIsNone(row["measured"]["started_error_rate"])
        self.assertFalse(row["collectors_reaped"])

    def test_late_known_failure_counts_failed_usage_not_success_throughput(self):
        def fail(row, index):
            if index == 2:
                row.update(completed=False, stop_reason="request-failed")
                row["samples"][0] = sample(4, failed=True)
                settle_wave(row)
        row, calls, _ = self.execute(change=fail)
        self.assertEqual((row["stop_reason"], len(calls)), ("wave-failed", 2))
        self.assertEqual(row["known_output_tokens"], 85)
        self.assertEqual(row["measured"]["reported_failed_output_tokens"], 5)
        self.assertEqual(row["measured"]["completed_output_tokens"], 75)

    def test_unconfirmed_cleanup_blocks_all_future_dispatch(self):
        def fail(row, _):
            row.update(completed=False, stop_reason="collector-failed", collectors_reaped=False)
        row, calls, _ = self.execute(change=fail)
        self.assertEqual(len(calls), 1)
        self.assertFalse(row["completed"] or row["collectors_reaped"])

    def test_report_tampering_and_false_claims_are_rejected(self):
        row, _, _ = self.execute()
        for mutate in (lambda r: r.update(saturation_demonstrated=True), lambda r: r.update(quality_accepted=True),
                       lambda r: r.update(rank_identity="0"), lambda r: r.update(reserved_requests=258),
                       lambda r: r.update(known_output_tokens=999), lambda r: r.update(elapsed_seconds=100),
                       lambda r: r.update(dispatch_unknown_requests=8), lambda r: r.update(extra="PRIVATE_CANARY"),
                       lambda r: r["waves"][1].update(start=0), lambda r: r["waves"][0].update(index=2),
                       lambda r: r["measured"].update(wall_seconds=1),
                       lambda r: r["waves"][0]["cohort"].update(collectors_reaped=False)):
            changed = copy.deepcopy(row)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.validate(changed)

    def test_actual_wave_has_no_warmup_and_reaps_private_children(self):
        fixture, record = c8_tests.ProcessTests(), {}
        handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        with fixture.children(), mock.patch.object(cohort, "_collect") as warmup:
            result = c8.wave(endpoint(), workload(), record, deadline=time.monotonic() + 10, model=MODEL)
        self.assertIs(result, record)
        warmup.assert_not_called()
        self.assertTrue(record["completed"] and record["collectors_reaped"])
        self.assertEqual(record["known_output_tokens"], 40)
        with mock.patch.object(cohort, "c8", c8, create=True):
            self.assertTrue(binding.validate_cohort(record, contract(), cohort, MODEL, wave=True))
        for sig, handler in handlers.items():
            self.assertEqual(signal.getsignal(sig), handler)

    def test_real_wave_spawn_and_report_failures_keep_reap_and_usage(self):
        fixture = c8_tests.ProcessTests()
        for modes, fail_at in (((), 3), (("lost",) * 8, None), (("lost-synchronized",) * 8, None),
                              (("known-failure",) * 8, None)):
            record = {}
            synchronized = modes and modes[0] == "lost-synchronized"
            with self.subTest(modes=modes, fail_at=fail_at), tempfile.TemporaryDirectory() as temporary:
                with fixture.children(modes, fail_at=fail_at, usage_dir=temporary if synchronized else None):
                    c8.wave(endpoint(), workload(), record, deadline=time.monotonic() + 10, model=MODEL)
                if synchronized:
                    self.assertEqual(len(list(Path(temporary).iterdir())), 8)
            self.assertFalse(record["completed"])
            self.assertTrue(record["collectors_reaped"])
            with mock.patch.object(cohort, "c8", c8, create=True):
                self.assertFalse(binding.validate_cohort(record, contract(), cohort, MODEL, wave=True))
            if synchronized:
                self.assertEqual(record["requests_with_unknown_usage"], 8)
                self.assertEqual((record["started_requests"], record["not_run_requests"]), (8, 0))
            elif modes and modes[0] == "lost":
                self.assertGreater(record["started_requests"], 0)
                self.assertEqual(record["requests_with_unknown_usage"], record["started_requests"])
                self.assertEqual(record["started_requests"] + record["not_run_requests"], 8)
            if modes and modes[0] == "known-failure":
                self.assertGreater(record["measured"]["reported_failed_output_tokens"], 0)

    def test_signal_during_wave_handler_restoration_cannot_erase_settlement(self):
        fixture, record, triggered = c8_tests.ProcessTests(), {}, False
        original = signal.signal
        def install(sig, handler):
            nonlocal triggered
            result = original(sig, handler)
            if record and not triggered:
                triggered = True
                raise KeyboardInterrupt("PRIVATE_CANARY")
            return result
        handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            with fixture.children(), mock.patch.object(signal, "signal", side_effect=install):
                with self.assertRaises(KeyboardInterrupt):
                    c8.wave(endpoint(), workload(), record, deadline=time.monotonic() + 10, model=MODEL)
            self.assertTrue(record["collectors_reaped"])
            self.assertEqual(record["known_output_tokens"], 40)
            for sig, handler in handlers.items():
                self.assertEqual(signal.getsignal(sig), handler)
        finally:
            for sig, handler in handlers.items():
                original(sig, handler)


class BindingTests(unittest.TestCase):
    def test_owned_runtime_dispatch_and_terminal_identity_remain_required(self):
        engine = mock.Mock(pid=12345)
        engine.poll.return_value = None
        before = {"parent": os.getpid(), "start": 44, "boot": "d" * 64, "namespaces": {"net": 555}, "argv": "e" * 64}
        facts = {"start_ticks": 44, "boot_id_sha256": "d" * 64, "netns_inode": 555, "argv_sha256": "e" * 64}
        observed, _, _ = SustainedTests().execute()
        for failure in (None, "parent", "initial", "terminal"):
            observer = types.SimpleNamespace(process=mock.Mock(return_value=before), listener=mock.Mock(return_value=66))
            guard = mock.Mock()
            if failure == "parent":
                observer.process.return_value = {**before, "parent": os.getpid() + 1}
            if failure == "initial":
                guard.check.side_effect = ValueError("PRIVATE_CANARY")
            if failure == "terminal":
                guard.check.side_effect = [None, ValueError("PRIVATE_CANARY")]
            with self.subTest(failure=failure), ExitStack() as stack:
                stack.enter_context(mock.patch.object(cohort, "c8", c8, create=True))
                stack.enter_context(mock.patch.object(cohort, "sustained", sustained, create=True))
                stack.enter_context(mock.patch.object(cohort.stream.identity, "process_facts", return_value=facts))
                stack.enter_context(mock.patch.object(cohort.stream.identity, "Endpoint", return_value=guard))
                run = stack.enter_context(mock.patch.object(sustained, "run", return_value=observed))
                old = stack.enter_context(mock.patch.object(c8, "run"))
                if failure in ("parent", "initial"):
                    with self.assertRaises(ValueError):
                        binding.run(engine, contract(), "a" * 64, workload(), cohort, observer,
                                    time.monotonic() + 600, model=MODEL)
                    run.assert_not_called()
                else:
                    row = binding.run(engine, contract(), "a" * 64, workload(), cohort, observer,
                                      time.monotonic() + 600, model=MODEL)
                    self.assertEqual(binding.validate(row, contract(), "a" * 64, cohort, model=MODEL), failure is None)
                    self.assertEqual(row["cohort"]["known_output_tokens"], 1285)
                    self.assertEqual(run.call_args.kwargs["cohort_seconds"], 600)
                old.assert_not_called()

    def test_host_cli_keeps_run_time_profile_override_forbidden(self):
        host = tuning_tests.host
        args = ["--prepare-nonconfidential-uncensored-b300-dp2", "/not-written",
            "--model-profile", "uncensored", "--execution-profile", "batch8-graphs",
            "--tokenizer-parity", "--tokenizer-materialization", "--runtime-observation", "--stream-cohort",
            "--stream-profile", binding.SUSTAINED_PROFILE]
        with mock.patch.object(host.sys, "argv", ["fixed", *args]), mock.patch.object(host, "prepare") as prepare:
            self.assertEqual(host.main(), 0)
            self.assertEqual(prepare.call_args.kwargs["stream_profile"], binding.SUSTAINED_PROFILE)
        with mock.patch.object(host.sys, "argv", ["fixed", "--run", "/not-read", "--stream-cohort",
                "--stream-profile", binding.SUSTAINED_PROFILE]), mock.patch.object(host, "run_container") as run:
            with self.assertRaises(ValueError):
                host.main()
            run.assert_not_called()

    def test_default_transport_engine_and_workload_source_bytes_unchanged(self):
        for name in ("stream_cohort.py", "stream_transport.py", "stream_measurement.py", "tau_live_identity.py",
                     "deepseek_tuning.py", "engine_lab.py"):
            with self.subTest(source=name):
                original = git(ROOT, "show", BASE + ":scripts/" + name)
                self.assertEqual(git(ROOT, "show", SUSTAINED_SOURCE + ":scripts/" + name), original)
                if name != "deepseek_tuning.py":
                    self.assertEqual((ROOT / "scripts" / name).read_bytes(), original)

    def test_existing_tuning_profiles_keep_historical_arguments_and_source_pins(self):
        # New closed profiles may extend the module, not alter a prior profile.
        old = types.ModuleType("historical_tuning")
        exec(compile(git(ROOT, "show", BASE + ":scripts/deepseek_tuning.py"), "historical_tuning", "exec"), old.__dict__)
        current = tuning_tests.tuning
        self.assertEqual((current.BATCH_NAMES, current.EAGER_CONTEXTS, current.SOURCES),
                         (old.BATCH_NAMES, old.EAGER_CONTEXTS, old.SOURCES))
        tuning_tests.TuningProfiles.setUpClass()
        fixture = tuning_tests.TuningProfiles()
        for name in old.NAMES:
            options = {**tuning_tests.fixtures.PayloadProfiles.options, "stream_cohort": name not in old.EAGER_CONTEXTS}
            _, row = fixture.payload(name, **options)
            with self.subTest(profile=name):
                self.assertEqual(current.arguments(fixture.baseline["arguments"], name),
                                 old.arguments(fixture.baseline["arguments"], name))
                self.assertEqual(current.enabled(row), old.enabled(row))
                self.assertEqual(current.context_limit(row), old.context_limit(row))

    def test_closed_profile_rejects_engine_and_source_drift(self):
        binding.enabled(contract())
        for field, value in (("execution_profile", "batch8-eager"), ("execution_profile", "batch8-dspark"),
                             ("model_profile", {}), ("gpu_family", "B200"), ("gpu_security", "confidential"),
                             ("data_parallel_size", 1), ("tau_owner", True), ("provider_conformance", {})):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                binding.enabled({**contract(), field: value})
        changed = contract()
        del changed["sources"]["stream/stream_sustained.py"]
        with self.assertRaises(ValueError):
            binding.enabled(changed)
        with self.assertRaises(ValueError):
            binding.source_names("PRIVATE_CANARY")

    def test_pinned_source_loader_not_ambient_modules_and_tampering_rejected(self):
        names = binding.source_names(binding.SUSTAINED_PROFILE)
        sources = {"stream/" + name + ".py": (ROOT / "scripts" / (name + ".py")).read_bytes() for name in names}
        pins = contract()["sources"]
        with mock.patch.dict("sys.modules", {name: types.ModuleType("untrusted") for name in names}):
            loaded = binding.load(sources, pins)
        self.assertIs(loaded.sustained.cohort, loaded)
        self.assertIs(loaded.sustained.c8, loaded.c8)
        for name in ("stream_sustained", "stream_c8", "stream_lab"):
            changed = {**sources, "stream/" + name + ".py": sources["stream/" + name + ".py"] + b"\n"}
            with self.assertRaises(ValueError):
                binding.load(changed, pins)

    def test_real_preparation_closure_preserves_engine_argv_and_old_report_caps(self):
        tuning_tests.TuningProfiles.setUpClass()
        args = list(tuning_tests.TuningProfiles.args)
        names = (*binding.C8_NAMES, "stream_sustained")
        args[3] = {**args[3], **{"scripts/" + name + ".py": (ROOT / "scripts" / (name + ".py")).read_bytes() for name in names}}
        options = dict(tuning_tests.fixtures.PayloadProfiles.options, model_profile="uncensored", execution_profile="batch8-graphs")
        host = tuning_tests.host
        old_files, old = host.make_payload(*args, **options, stream_profile=binding.C8_PROFILE)
        files, current = host.make_payload(*args, **options, stream_profile=binding.SUSTAINED_PROFILE)
        for name in ("arguments", "environment", "interpreter", "model_profile", "pack_bytes"):
            self.assertEqual(current[name], old[name])
        self.assertEqual(set(files) - set(old_files), {"stream/stream_sustained.py"})
        loaded = binding.load(files, current["sources"])
        self.assertIs(loaded.sustained.c8, loaded.c8)
        self.assertEqual(binding.result_limits(old), (65536, 60000))
        self.assertEqual(binding.result_limits(current), (524288, 500000))
        self.assertLessEqual(len(current["sources"]), 110)

        observed, _, _ = SustainedTests().execute()
        experiment = {"schema": "nib.deepseek-lab-tokenizer-parity/v1", "status": "BLOCKED",
            "deployable": False, "confidential": False, "functional_pass": False,
            "stage": "request", "engine_stopped": True, "application": None, "hardware": None,
            "completion": None, "model_profile": current["model_profile"], "tokenizer_parity": None,
            "tokenizer_materialization": current["tokenizer_materialization"],
            "runtime_observation": {"samples": []}, "startup": {"outcome": "ready", "wait_ms": 1,
                "exit_code": None, "health_probes": 1, "exception_diagnostic": None,
                "checkpoints": {"status": "eof", "last_id": 17}},
            "stream_cohort": {"schema": binding.RESULT_SCHEMA,
                "contract_sha256": hashlib.sha256(files["lab.json"]).hexdigest(),
                "engine_identity_sha256": "b" * 64, "terminal_identity_verified": True, "cohort": observed}}
        self.assertGreater(len(json.dumps(experiment)), 60000)
        load = host.load
        def fixture_load(name, path, sha):
            if name == "lab_runtime_validation":
                return types.SimpleNamespace(validate=lambda *a, **k: None)
            return load(name, path, sha)
        with tuning_tests.fixtures.payload_tree(files) as root, mock.patch.object(host, "load", side_effect=fixture_load):
            self.assertEqual(host.result_limits(root, current), (524288, 500000))
            host.validate_result(experiment, root, current, args[0].recorder)
            changed = copy.deepcopy(experiment)
            changed["stream_cohort"]["cohort"]["known_output_tokens"] += 1
            with self.assertRaises(ValueError):
                host.validate_result(changed, root, current, args[0].recorder)
            path = root / "payload/stream-lab.py"
            path.write_bytes(path.read_bytes() + b"\n")
            with self.assertRaises(ValueError):
                host.result_limits(root, current)
        self.assertEqual(host.result_limits(None, {}), (65536, 60000))
        with mock.patch.object(host, "load", return_value=types.SimpleNamespace()):
            self.assertEqual(host.result_limits(ROOT, old), (65536, 60000))


if __name__ == "__main__":
    unittest.main()
