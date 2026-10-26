# SPDX-License-Identifier: Apache-2.0
"""Synthetic clocks, source joins and private subprocesses; no model or GPU."""

import ast
import copy
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import time
import types
import unittest
from unittest import mock

import stream_scaling as scaling
from test_stream_cohort import workload
from test_stream_c8 import MODEL, endpoint, selected
from test_stream_sustained import Clock, sample
from test_evidence_history import git
import test_deepseek_tuning as tuning_tests

ROOT = Path(__file__).absolute().parents[1]
BASE = "650e6aa99fcda35b314aed4fa915512fc16658fa"
binding, cohort, capacity = scaling.binding, scaling.cohort, scaling.capacity
PROFILE = next(iter(binding.SCALING_PROFILES))


def contract(profile=PROFILE):
    row = selected()
    row.update(execution_profile="batch32-graphs", stream_cohort={"schema": binding.SCHEMA, "profile": profile})
    for name in binding.source_names(profile):
        row["sources"]["stream/" + name + ".py"] = hashlib.sha256((ROOT / "scripts" / (name + ".py")).read_bytes()).hexdigest()
    return row


def capacities():
    return [{"rank": i, "graph_memory_bytes": None, "graph_capture_observed": False,
        "kv_capacity_tokens": 262144, "kv_max_concurrency": 64., "running": 0,
        "waiting": 0, "kv_fraction": .1, "preemptions": 0} for i in (0, 1)]


def metrics():
    rows = []
    for rank in (0, 1):
        labels = f'engine="{rank}",model_name="{MODEL}"'
        rows.append(f'vllm:cache_config_info{{engine="{rank}",cache_dtype="fp8",enable_prefix_caching="True",'
                    'kv_offloading_size="None",kv_cache_size_tokens="262144",kv_cache_max_concurrency="64.0"} 1.0')
        for name, value in (("num_requests_running", 0), ("num_requests_waiting", 0),
                            ("kv_cache_usage_perc", .1), ("num_preemptions_total", 0)):
            rows.append(f'vllm:{name}{{{labels}}} {value}')
    return ("\n".join(rows) + "\n").encode()


class CapacityTests(unittest.TestCase):
    def test_exact_rank_capacity_without_graph_or_saturation_inference(self):
        self.assertEqual(capacity.parse(metrics(), MODEL), capacities())
        capacity.stable(capacities(), capacities())

    def test_missing_duplicate_wrong_model_low_capacity_offload_and_nonfinite_reject(self):
        raw = metrics()
        for changed in (raw.replace(b'engine="1"', b'engine="2"'), raw + raw,
                        raw.replace(MODEL.encode(), b'PRIVATE_CANARY'), raw.replace(b'262144', b'1024'),
                        raw.replace(b'64.0', b'NaN'), raw.replace(b'kv_offloading_size="None"', b'kv_offloading_size="1"'),
                        raw.replace(b'cache_dtype="fp8"', b'cache_dtype="fp16"')):
            with self.subTest(data=hashlib.sha256(changed).hexdigest()), self.assertRaises(ValueError):
                capacity.parse(changed, MODEL)

    def test_preemption_drift_rejected(self):
        changed = capacities()
        changed[0]["preemptions"] += 1
        with self.assertRaises(ValueError):
            capacity.stable(capacities(), changed)


class ScalingTests(unittest.TestCase):
    def execute(self, *, budget=900, condition="isolated", wave_seconds=30, failure=None, cached=0):
        clock, owned, calls = Clock(), endpoint(), []
        def collect(*args, **kwargs):
            clock.now += 1
            return scaling.meter.Sample("completed", .5, .1, .2, .3, 2, 2048, 5, 0, "stop")
        def wave(ep, work, record, **kwargs):
            self.assertIs(ep, owned)
            self.assertEqual(kwargs["deadline"], 1000 + min(budget, 900))
            calls.append(kwargs)
            if failure == "lost":
                raise KeyboardInterrupt("PRIVATE_CANARY")
            start = clock.now - 1000
            clock.now += wave_seconds
            count = kwargs["concurrency"]
            elapsed = min(4, wave_seconds / 2)
            samples = [scaling.meter.Sample("completed", elapsed, elapsed / 10, elapsed / 10,
                        elapsed / 2, 2, 2048, 5, cached, "stop").metadata() for _ in range(count)]
            for s in samples:
                s["cached_prompt_tokens"] = cached
            if failure == "usage":
                samples[0] = sample(4, failed=True)
            record.update(start=start, end=clock.now - 1000, samples=samples,
                intervals=[{"index": i, "dispatch": start, "start": start, "end": start + elapsed,
                            "reported": True, "exit_code": 0} for i in range(count)],
                collectors_reaped=failure != "reap", stop_reason="request-failed" if failure == "usage" else
                "collector-failed" if failure == "reap" else "complete", peak_in_flight=count, dispatch_unknown_requests=0)
            if failure == "settled":
                raise KeyboardInterrupt("PRIVATE_CANARY")
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(scaling, "time", clock))
            stack.enter_context(mock.patch.object(scaling, "wave", side_effect=wave))
            stack.enter_context(mock.patch.object(capacity, "probe", side_effect=lambda *_: capacities()))
            stack.enter_context(mock.patch.object(scaling.stream.OwnedStreamHTTP, "collect", side_effect=collect))
            stack.enter_context(mock.patch.object(owned, "check"))
            row = scaling.run(owned, workload(), deadline=clock.now + budget,
                gpu_count=8, cohort_seconds=900, model=MODEL, condition=condition)
        self.assertNotIn("PRIVATE_CANARY", json.dumps(row))
        self.assertEqual(scaling.validate(row, contract(), cohort, MODEL, condition), row["completed"])
        return row, calls

    def test_three_finite_plateaus_actual_usage_full_denominator(self):
        row, calls = self.execute()
        self.assertTrue(row["completed"])
        self.assertEqual(len(calls), 15)
        self.assertEqual(row["reserved_requests"], 12289)
        self.assertEqual(row["reserved_total_tokens"], 31459840)
        self.assertEqual([p["concurrency"] for p in row["plateaus"]], [16, 32, 64])
        self.assertEqual(row["known_output_tokens"], (5 * (16 + 32 + 64) + 1) * 5)
        for p in row["plateaus"]:
            self.assertEqual(p["measured"]["wall_seconds"], 150)
            self.assertEqual(p["not_run_requests"], 4096 - 5 * p["concurrency"])
        self.assertFalse(row["saturation_demonstrated"] or row["graph_capture_observed"])

    def test_original_deadline_not_renewed_and_later_plateaus_not_run(self):
        row, calls = self.execute(budget=350)
        self.assertFalse(row["completed"])
        self.assertEqual(row["stop_reason"], "deadline")
        self.assertEqual(len(row["plateaus"]), 1)
        self.assertEqual(len({c["deadline"] for c in calls}), 1)

    def test_expired_budget_no_requests(self):
        row, calls = self.execute(budget=0)
        self.assertEqual((row["started_requests"], calls), (0, []))

    def test_lost_wave_is_unknown_not_free_or_reaped(self):
        row, calls = self.execute(failure="lost")
        self.assertEqual(len(calls), 1)
        self.assertEqual(row["dispatch_unknown_requests"], 16)
        self.assertEqual(row["requests_with_unknown_usage"], 16)
        self.assertFalse(row["collectors_reaped"])

    def test_known_failed_usage_and_failed_cleanup_stop_escalation(self):
        for failure in ("usage", "reap", "settled"):
            row, calls = self.execute(failure=failure)
            self.assertFalse(row["completed"])
            self.assertEqual(len(calls), 1)
            self.assertEqual(row["known_output_tokens"], 85)
            self.assertEqual(row["requests_with_unknown_usage"], 0)

    def test_cache_conditions_and_unknown_are_explicit(self):
        row, calls = self.execute(cached=None)
        self.assertFalse(row["completed"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(row["plateaus"][0]["cached_usage_unknown_requests"], 16)
        row, _ = self.execute(cached=1)
        self.assertFalse(row["completed"])
        self.assertEqual(row["stop_reason"], "cache-mismatch")

    def test_request_cap_stops_active_admission_without_padding(self):
        row, calls = self.execute(wave_seconds=.1)
        self.assertFalse(row["completed"])
        self.assertEqual(row["stop_reason"], "request-cap")
        self.assertEqual(len(calls), 256)
        self.assertEqual(row["plateaus"][0]["started_requests"], 4096)
        self.assertLess(row["elapsed_seconds"], 30)

    def test_maximum_report_is_bounded_without_dropping_sample_fields(self):
        # Exercise the largest sample count with all metadata fields retained.
        row, _ = self.execute()
        epoch = 1.
        for p in row["plateaus"]:
            count = p["concurrency"]
            waves = scaling.MAX_REQUESTS // count
            p.update(start=epoch, end=epoch + 150, admission_end=epoch + 150)
            template = p["waves"][0]
            p["waves"] = []
            for index in range(waves):
                w = copy.deepcopy(template)
                start, end = epoch + 150 * index / waves, epoch + 150 * (index + 1) / waves
                w.update(start=start, end=end)
                for s, i in zip(w["samples"], w["intervals"]):
                    s.update(elapsed_seconds=end - start, ttft_seconds=0., first_content_seconds=0.,
                             mean_output_token_interval_estimate_seconds=None,
                             output_events=scaling.meter.MAX_STREAM, completion_tokens=512)
                    i.update(dispatch=start, start=start, end=end)
                p["waves"].append(w)
            scaling.summarize(p)
            epoch += 150
        row["elapsed_seconds"] = epoch
        scaling.totals(row)
        self.assertTrue(scaling.validate(row, contract(), cohort, MODEL, "isolated"))
        self.assertEqual(row["started_requests"], 12289)
        self.assertLess(len(json.dumps(row, allow_nan=False)), 30 << 20)
        self.assertGreater(len(json.dumps(row)), 512 << 10)

    def test_tampering_and_fabricated_success_reject(self):
        row, _ = self.execute()
        for mutate in (lambda r: r.update(saturation_demonstrated=True), lambda r: r.update(known_output_tokens=0),
                       lambda r: r["plateaus"][0]["measured"].update(wall_seconds=1),
                       lambda r: r["plateaus"][0]["waves"][0]["intervals"][0].update(dispatch=151),
                       lambda r: r["capacity"][0].update(graph_memory_bytes=1),
                       lambda r: r.update(extra="PRIVATE_CANARY")):
            changed = copy.deepcopy(row)
            mutate(changed)
            with self.assertRaises(ValueError):
                scaling.validate(changed, contract(), cohort, MODEL, "isolated")


class BindingTests(unittest.TestCase):
    def test_profile_source_binding_and_scoped_report_cap(self):
        for p in binding.SCALING_PROFILES:
            row = contract(p)
            binding.enabled(row)
            self.assertEqual(binding.result_limits(row), (32 << 20, 30 << 20))
            sources = {"stream/" + name + ".py": (ROOT / "scripts" / (name + ".py")).read_bytes()
                       for name in binding.source_names(p)}
            loaded = binding.load(sources, row["sources"])
            self.assertEqual(binding.cohort_seconds(row, loaded), 900)
            sources["stream/stream_capacity.py"] += b"\n"
            with self.assertRaises(ValueError):
                binding.load(sources, row["sources"])
        self.assertEqual(binding.result_limits(selected()), (65536, 60000))
        changed = contract()
        changed["execution_profile"] = "batch8-graphs"
        with self.assertRaises(ValueError):
            binding.enabled(changed)

    def test_legacy_wire_bytes_and_rejection_unchanged_with_no_salt(self):
        old = types.ModuleType("old_transport")
        exec(compile(git(ROOT, "show", BASE + ":scripts/stream_transport.py"), "old_transport", "exec"), old.__dict__)
        for index in range(cohort.TOTAL_REQUESTS):
            messages = cohort.messages(index, workload().cases[index][0])
            self.assertEqual(old.request_body(messages, 2048, 512, model=MODEL),
                             scaling.stream.request_body(messages, 2048, 512, model=MODEL))
        with self.assertRaises(ValueError):
            scaling.stream.request_body(messages, 2048, 512, model=MODEL, cache_salt="PRIVATE_CANARY")

    def test_salt_isolation_without_prompt_or_rank_mutation(self):
        self.assertNotEqual(scaling.salt("a" * 64, "isolated", 1, 1), scaling.salt("a" * 64, "isolated", 2, 1))
        with self.assertRaises(ValueError):
            scaling.salt("a" * 64, "repeated", 1, 1)
        body = json.loads(scaling.stream.request_body(cohort.messages(1, 20), 2048, 512, model=MODEL, cache_salt="a" * 64))
        self.assertEqual(body.pop("cache_salt"), "a" * 64)
        self.assertEqual(body, json.loads(scaling.stream.request_body(cohort.messages(1, 20), 2048, 512, model=MODEL)))

    def test_owner_only_adds_two_closed_branches(self):
        old = ast.parse(git(ROOT, "show", BASE + ":scripts/engine_lab.py"))
        current = ast.parse((ROOT / "scripts/engine_lab.py").read_bytes())
        removed = []
        class Remove(ast.NodeTransformer):
            def visit_If(self, node):
                if "STREAM_LAB.scaling(contract)" in ast.unparse(node.test) or ast.unparse(node.test) == "mode == 'scaling-collector'":
                    removed.append(node)
                    return None
                return self.generic_visit(node)
        current = Remove().visit(current)
        self.assertEqual(len(removed), 2)
        self.assertEqual(ast.dump(old, include_attributes=False), ast.dump(current, include_attributes=False))

    def test_shared_transport_and_spawn_only_add_explicit_options(self):
        old = ast.parse(git(ROOT, "show", BASE + ":scripts/stream_transport.py"))
        current = ast.parse((ROOT / "scripts/stream_transport.py").read_bytes())
        class RemoveSalt(ast.NodeTransformer):
            def visit_FunctionDef(self, node):
                if node.name in ("request_body", "collect"):
                    self.assertions(node)
                    node.args.kwonlyargs.pop()
                    node.args.kw_defaults.pop()
                if node.name == "request_body":
                    node.body = [n for n in node.body if not isinstance(n, ast.Expr)
                                 or not ast.unparse(n).startswith("require(cache_salt is None")]
                    assignment, conditional, returned = node.body[-3:]
                    assert isinstance(assignment, ast.Assign) and ast.unparse(assignment.targets[0]) == "body"
                    assert ast.unparse(conditional.test) == "cache_salt is not None"
                    assert ast.unparse(returned.value.args[0]) == "body"
                    returned.value.args[0] = assignment.value
                    node.body[-3:] = [returned]
                return self.generic_visit(node)
            def assertions(self, node):
                assert node.args.kwonlyargs[-1].arg == "cache_salt" and node.args.kw_defaults[-1].value is None
            def visit_Call(self, node):
                if ast.unparse(node.func) == "request_body":
                    assert ast.unparse(node.keywords[-1]) == "cache_salt=cache_salt"
                    node.keywords.pop()
                return self.generic_visit(node)
        current = RemoveSalt().visit(current)
        self.assertEqual(ast.dump(old, include_attributes=False), ast.dump(current, include_attributes=False))
        old = ast.parse(git(ROOT, "show", BASE + ":scripts/stream_c8.py"))
        current = ast.parse((ROOT / "scripts/stream_c8.py").read_bytes())
        fn = next(n for n in current.body if isinstance(n, ast.FunctionDef) and n.name == "start_collector")
        self.assertEqual(ast.unparse(fn.args.kwonlyargs[0]), "factory")
        self.assertIsNone(fn.args.kw_defaults[0].value)
        fn.args.kwonlyargs.clear()
        fn.args.kw_defaults.clear()
        assignment = next(n for n in ast.walk(fn) if isinstance(n, ast.Assign)
                          and ast.unparse(n.targets[0]) == "process")
        self.assertEqual(ast.unparse(assignment.value), "(spawn if factory is None else factory)()")
        assignment.value = ast.parse("spawn()", mode="eval").body
        self.assertEqual(ast.dump(old, include_attributes=False), ast.dump(current, include_attributes=False))

    def test_all_legacy_engine_argv_and_source_pins_unchanged(self):
        old = types.ModuleType("old_tuning")
        exec(compile(git(ROOT, "show", BASE + ":scripts/deepseek_tuning.py"), "old_tuning", "exec"), old.__dict__)
        fixture = tuning_tests.TuningProfiles()
        fixture.setUpClass()
        current = tuning_tests.tuning
        for profile in old.NAMES:
            self.assertEqual(old.arguments(fixture.baseline["arguments"], profile), current.arguments(fixture.baseline["arguments"], profile))
        self.assertEqual(old.SOURCES, current.SOURCES)
        args = current.arguments(fixture.baseline["arguments"], current.SCALING_PROFILE)
        self.assertEqual(args[args.index("--max-num-seqs") + 1], "32")
        self.assertEqual(args[args.index("--max-model-len") + 1], "4096")
        self.assertEqual(json.loads(args[args.index("--compilation-config") + 1])["cudagraph_capture_sizes"], [1, 2, 4, 8, 16, 32])
        self.assertNotIn("--speculative-config", args)

    def test_exact_upstream_graph_dispatch_covers_32_decode_requests_not_prefill(self):
        graphs = tuning_tests.graphs
        state = types.SimpleNamespace(
            compilation_config=types.SimpleNamespace(cudagraph_capture_sizes=[1, 2, 4, 8, 16, 32],
                                                     max_cudagraph_capture_size=32),
            cudagraph_mode=graphs.CUDAGraphMode.FULL_DECODE_ONLY,
            max_num_reqs=32, decode_query_len=1, varlen_decode=False,
            lora_capture_cases=[0], vllm_config=types.SimpleNamespace(speculative_config=None),
            _maybe_ubatch_twin=lambda desc: None, _resolve_effective_loras=lambda n: n,
            _capture_descs={}, _candidates={}, _graphs_captured=True)
        graphs._init_candidates(state)
        for n in range(1, 33):
            self.assertEqual(graphs.dispatch(state, n, n, 1, 0, 1).cg_mode, graphs.CUDAGraphMode.FULL)
        self.assertEqual(graphs.dispatch(state, 33, 33, 1, 0, 1).cg_mode, graphs.CUDAGraphMode.NONE)
        self.assertEqual(graphs.dispatch(state, 1, 2048, None, 0, None).cg_mode, graphs.CUDAGraphMode.NONE)

    def test_complete_preparation_and_closed_profile_admission(self):
        fixture = tuning_tests.TuningProfiles()
        fixture.setUpClass()
        fixture.args = list(fixture.args)
        fixture.args[3] = {**fixture.args[3], **{"scripts/" + name + ".py":
            (ROOT / "scripts" / (name + ".py")).read_bytes()
            for name in (*binding.source_names(PROFILE), "stream_sustained")}}
        files, prepared = fixture.payload("batch32-graphs", **tuning_tests.fixtures.PayloadProfiles.options,
                                          stream_profile=PROFILE)
        self.assertEqual(fixture.validate(files, prepared), tuning_tests.fixtures.NEW)
        current = tuning_tests.tuning
        self.assertEqual(current.enabled(prepared), "batch32-graphs")
        binding.enabled(prepared)
        self.assertEqual(binding.cohort_seconds(prepared, binding.load(files, prepared["sources"])), 900)
        for profile in (None, binding.C8_PROFILE, binding.SUSTAINED_PROFILE,
                        "chat-2k-512-c16-32-64-repeated-900s"):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                fixture.payload("batch32-graphs", **tuning_tests.fixtures.PayloadProfiles.options, stream_profile=profile)
        with self.assertRaises(ValueError):
            fixture.payload("batch32-graphs")

    def test_closed_cli_and_collector_entry(self):
        host, lab = tuning_tests.host, tuning_tests.lab
        args = ["--prepare-nonconfidential-uncensored-b300-dp2", "/not-written", "--model-profile", "uncensored",
            "--execution-profile", "batch32-graphs", "--tokenizer-parity", "--tokenizer-materialization",
            "--runtime-observation", "--stream-cohort", "--stream-profile", PROFILE]
        with mock.patch.object(host.sys, "argv", ["fixed", *args]), mock.patch.object(host, "prepare") as prepare:
            self.assertEqual(host.main(), 0)
            self.assertEqual(prepare.call_args.kwargs["stream_profile"], PROFILE)
        with mock.patch.object(host.sys, "argv", ["fixed", "--run", "/not-read", "--stream-profile", PROFILE]), \
                mock.patch.object(host, "run_container") as run:
            with self.assertRaises(ValueError):
                host.main()
            run.assert_not_called()
        for bad in (False, True):
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(lab.sys, "argv", ["fixed", "digest", "scaling-collector"]))
                stack.enter_context(mock.patch.object(lab.sys, "flags", types.SimpleNamespace(isolated=True, no_site=True)))
                stack.enter_context(mock.patch.object(lab.sys, "dont_write_bytecode", True))
                stack.enter_context(mock.patch.object(lab, "lab_context", return_value=(selected() if bad else contract(), {}, (), {}, {})))
                stack.enter_context(mock.patch.object(lab, "STREAM_LAB", binding))
                stack.enter_context(mock.patch.object(lab, "STREAM_COHORT", cohort))
                stack.enter_context(mock.patch.object(cohort, "scaling", scaling, create=True))
                stack.enter_context(mock.patch.object(lab, "model_identity", return_value={"served_model_name": MODEL}))
                child = stack.enter_context(mock.patch.object(scaling, "child"))
                if bad:
                    with self.assertRaises(ValueError):
                        lab.lab_entry()
                    child.assert_not_called()
                else:
                    lab.lab_entry()
                    child.assert_called_once_with(MODEL)

    def test_terminal_identity_failure_preserves_observed_usage(self):
        observed, _ = ScalingTests().execute()
        ep = endpoint()
        before = {"parent": os.getpid(), "start": ep.expected["start_ticks"], "boot": ep.expected["boot_id_sha256"],
                  "namespaces": {"net": ep.expected["netns_inode"]}, "argv": ep.expected["argv_sha256"]}
        facts = {k: ep.expected[k] for k in ("start_ticks", "boot_id_sha256", "netns_inode", "argv_sha256")}
        engine = mock.Mock(pid=1234)
        engine.poll.return_value = None
        observer = mock.Mock()
        observer.process.return_value = before
        observer.listener.return_value = ep.expected["listener_inode"]
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(cohort, "scaling", scaling, create=True))
            stack.enter_context(mock.patch.object(cohort, "c8", scaling.c8, create=True))
            stack.enter_context(mock.patch.object(cohort.stream.identity, "process_facts", return_value=facts))
            stack.enter_context(mock.patch.object(cohort.stream.identity, "Endpoint", return_value=ep))
            stack.enter_context(mock.patch.object(ep, "check", side_effect=[None, ValueError("PRIVATE_CANARY")]))
            run = stack.enter_context(mock.patch.object(scaling, "run", return_value=observed))
            row = binding.run(engine, contract(), "a" * 64, workload(), cohort, observer, time.monotonic() + 900, model=MODEL)
            self.assertFalse(binding.validate(row, contract(), "a" * 64, cohort, model=MODEL))
            self.assertEqual(row["cohort"]["known_output_tokens"], observed["known_output_tokens"])
            self.assertEqual(run.call_args.kwargs["cohort_seconds"], 900)
        self.assertNotIn("PRIVATE_CANARY", json.dumps(row))


if __name__ == "__main__":
    unittest.main()
