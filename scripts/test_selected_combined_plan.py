# SPDX-License-Identifier: Apache-2.0
"""Offline composition and fake-clock arithmetic; no engine, sockets or owner."""

from contextlib import ExitStack
import ast
import copy
from dataclasses import FrozenInstanceError
import json
import types
import unittest
from unittest import mock

import selected_combined_plan as plan
import selected_long_qualification as long
import selected_service as service
import stream_scaling as scaling
import test_deepseek_tuning as tuning_tests
import test_selected_service_smoke as smoke_tests
from test_evidence_history import git
from test_selected_long_qualification import workload
from test_stream_scaling import contract as scaling_contract

BASE = "5f5823c00e1e315eb8e20f03a2bc7952b6f9ff58"


def previous(name):
    module = types.ModuleType("previous_" + name)
    module.__file__ = str(tuning_tests.ROOT / "scripts" / (name + ".py"))
    exec(compile(git(tuning_tests.ROOT, "show", BASE + ":scripts/" + name + ".py"),
                 module.__file__, "exec"), module.__dict__)
    return module


def window(**changes):
    return plan.DeadlinePlan.begin(**{"execution_profile": plan.PROFILE,
        "preparation_seconds": 100, "now_wall": 100000, "now_monotonic": 1000,
        "hard_wall": 109000, "hard_monotonic": 10000, **changes})


class CompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tuning_tests.TuningProfiles.setUpClass()
        cls.baseline = tuning_tests.TuningProfiles.baseline["arguments"]

    def test_preview_changes_only_two_existing_controls(self):
        baseline = copy.deepcopy(self.baseline)
        result = plan.candidate_arguments(baseline, execution_profile=plan.PROFILE)
        existing_long = plan.tuning.arguments(baseline, plan.tuning.LONG_GRAPHS_PROFILE)
        existing_batch = plan.tuning.arguments(baseline, plan.tuning.SCALING_PROFILE)
        self.assertEqual(len(result), len(existing_long))
        changed = {i for i, (a, b) in enumerate(zip(result, existing_long)) if a != b}
        self.assertEqual(changed, {result.index(flag) + 1 for flag in
                                  ("--max-num-seqs", "--compilation-config")})
        for flag in ("--max-num-seqs", "--compilation-config"):
            self.assertEqual(result[result.index(flag) + 1], existing_batch[existing_batch.index(flag) + 1])
        for flag, value in (("--max-num-seqs", "32"), ("--max-model-len", "131072"),
                ("--max-num-batched-tokens", "4096"), ("--kv-cache-dtype", "fp8"),
                ("--data-parallel-size", "2"), ("--tensor-parallel-size", "4"),
                ("--engram-config", '{"cpu_offload":false}')):
            self.assertEqual(result[result.index(flag) + 1], value)
        graph = json.loads(result[result.index("--compilation-config") + 1])
        self.assertEqual(graph["cudagraph_capture_sizes"], [1, 2, 4, 8, 16, 32])
        flags = [item for item in result if item.startswith("--")]
        self.assertEqual(len(flags), len(set(flags)))
        self.assertEqual(baseline, self.baseline)
        self.assertNotIn("--enforce-eager", result)
        self.assertNotIn("--speculative-config", result)

    def test_only_explicit_closed_preview_and_canonical_transforms(self):
        for profile in (None, True, {}, [], "long128k-graphs", "batch32-graphs", "PRIVATE_CANARY"):
            with self.subTest(profile=profile), self.assertRaisesRegex(ValueError, "^combined qualification plan rejected$"):
                plan.candidate_arguments(self.baseline, execution_profile=profile)
        for extra in (["--max-num-seqs", "64"], ["--compilation-config", "{}"],
                      ["--enable-prefix-caching"], ["--speculative-config", "{}"]):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                plan.candidate_arguments(self.baseline + extra, execution_profile=plan.PROFILE)
        with self.assertRaises(TypeError):
            plan.candidate_arguments(self.baseline, execution_profile=plan.PROFILE, max_num_seqs=64)

    def test_preview_cannot_be_prepared_or_launched(self):
        self.assertNotIn(plan.PROFILE, plan.tuning.NAMES)
        self.assertNotIn(plan.PROFILE, service.engine_entry.BOUND_PROFILES)
        for validator in (plan.tuning.select, service.engine_entry.execution_limits):
            with self.assertRaises(ValueError):
                validator(plan.PROFILE)
        with self.assertRaises(ValueError):
            tuning_tests.host.make_payload(*tuning_tests.TuningProfiles.args,
                model_profile="uncensored", execution_profile=plan.PROFILE)
        contract = {**scaling_contract(), "execution_profile": plan.PROFILE}
        for validator in (plan.tuning.enabled, scaling.binding.enabled, service.engine_entry.canonical_arguments):
            with self.assertRaises(ValueError):
                validator(contract)
        with self.assertRaises(ValueError):
            service.qualification_options(True, service.ORDINARY_PROFILE, plan.PROFILE)

    def test_legacy_argv_and_request_bytes_identical_to_merged_base(self):
        old_tuning = previous("deepseek_tuning")
        self.assertEqual(tuple(name for name in plan.tuning.NAMES if name != plan.tuning.MILLION_PROFILE), old_tuning.NAMES)
        self.assertEqual(set(plan.tuning.NAMES) - set(old_tuning.NAMES), {plan.tuning.MILLION_PROFILE})
        for name in old_tuning.NAMES:
            self.assertEqual(plan.tuning.arguments(self.baseline, name), old_tuning.arguments(self.baseline, name))
        old_stream = previous("stream_transport")
        messages = [{"role": "user", "content": "Synthetic fixture"}]
        for salt in (None, "a" * 64):
            self.assertEqual(scaling.stream.request_body(messages, 2048, 512, cache_salt=salt),
                             old_stream.request_body(messages, 2048, 512, cache_salt=salt))
        # Reuse the old function bodies without re-executing the dataclass module.
        tree = ast.parse(git(tuning_tests.ROOT, "show", BASE + ":scripts/selected_long_qualification.py"))
        scope = dict(vars(long))
        tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "request_bytes"]
        exec(compile(tree, "old_long_request", "exec"), scope)
        for level in range(4):
            self.assertEqual(long.request_bytes(workload(), level, "a" * 64),
                             scope["request_bytes"](workload(), level, "a" * 64))

    def test_original_receipt_replays_but_cannot_be_relabelled_or_oversized(self):
        path = tuning_tests.ROOT / "evidence/p2/2026-10-26-selected-long-graphs/owner.json"
        raw = path.read_bytes()
        owner = json.loads(raw)
        inner, sha = owner["lifecycle"], owner["prepared_sha256"]
        arguments = {"lifetime": inner["lifetime"], "prepared_sha256": sha,
                     "execution_profile": plan.tuning.LONG_GRAPHS_PROFILE, "long_qualification": True}
        encoded = long.encoded(inner)
        self.assertEqual(smoke_tests.smoke.lifecycle(encoded, **arguments), inner)
        report = inner["qualification"]
        self.assertEqual(long.validate_report(long.encoded(report), sha), report)
        changed = {**report, "execution_profile": plan.PROFILE}
        with self.assertRaises(ValueError):
            long.validate_report(long.encoded(changed), sha)
        with self.assertRaises(ValueError):
            smoke_tests.smoke.lifecycle(encoded, **{**arguments, "execution_profile": plan.PROFILE})
        with self.assertRaises(ValueError):
            smoke_tests.smoke.lifecycle(encoded + b" " * (16385 - len(encoded)), **arguments)
        self.assertEqual(path.read_bytes(), raw)

    def test_preview_and_planning_have_no_io_or_implicit_clock(self):
        with ExitStack() as stack:
            for name in ("builtins.open", "subprocess.Popen", "socket.socket", "time.time", "time.monotonic"):
                stack.enter_context(mock.patch(name, side_effect=AssertionError("unexpected effect")))
            plan.candidate_arguments(self.baseline, execution_profile=plan.PROFILE)
            current = window().observe(now_wall=100010, now_monotonic=1010)
            self.assertEqual(current.deadline("throughput"), 7400)


class BudgetTests(unittest.TestCase):
    def test_8440_is_derived_from_existing_code_not_a_new_owner_limit(self):
        actual = (("startup", service.engine_lab.STARTUP_SECONDS), ("long", long.RUN_SECONDS),
                  ("throughput", scaling.SECONDS),
                  ("service-cleanup", service.cleanup_reserve(service.engine_entry.LONG_GRAPHS_PROFILE)),
                  ("host-cleanup", smoke_tests.smoke.HOST_RESERVE))
        self.assertEqual(plan.STAGES, actual)
        self.assertEqual(plan.BASE_SECONDS, 8440)
        self.assertEqual(plan.STAGES[-2][1], 2 * (900 + 30) + 4 * service.REAP_SECONDS)
        self.assertEqual((long.PREPARE_SECONDS, scaling.cohort.PREPARE_SECONDS), (180, 60))
        self.assertEqual(service.lifetime_seconds(service.ORDINARY_PROFILE), plan.MAX_LIFETIME_SECONDS)
        self.assertEqual(service.lifetime_seconds(service.SMOKE_PROFILE), 4800)
        self.assertEqual(service.engine_lab.TOTAL_SECONDS, 4800)
        self.assertEqual((tuning_tests.host.MAX_SECONDS, scaling.binding.MAX_SECONDS), (4800, 4800))
        self.assertEqual(service.engine_entry.execution_limits(service.engine_entry.LONG_GRAPHS_PROFILE),
            {"profile": "long128k-graphs", "context_tokens": 131072, "max_output_tokens": 16384,
             "max_inflight": 1, "guard_profile": "selected-deepseek-long"})
        self.assertEqual((scaling.CONCURRENCIES, scaling.cohort.PROMPT_TOKENS,
                          scaling.cohort.OUTPUT_TOKENS), ((16, 32, 64), 2048, 512))
        self.assertEqual(scaling.binding.result_limits(scaling_contract()), (32 << 20, 30 << 20))
        self.assertEqual(long.MAX_REPORT, 12288)

    def test_preparation_is_explicit_finite_and_not_hidden_in_floor(self):
        with self.assertRaises(TypeError):
            plan.stage_budgets()
        for value in (None, True, 0, -1, 1.5, "100", float("inf"), 10**1000):
            with self.subTest(value=str(value)[:30]), self.assertRaises(ValueError):
                plan.stage_budgets(value)
        self.assertEqual(sum(seconds for _, seconds in plan.stage_budgets(100)), 8540)
        for remaining in (4800, 8440, 8539.99):
            with self.subTest(remaining=remaining), self.assertRaises(ValueError):
                window(hard_wall=100000 + remaining, hard_monotonic=1000 + remaining)
        accepted = window(hard_wall=108540, hard_monotonic=9540)
        self.assertEqual(accepted.deadline("host-cleanup"), 9540)

    def test_every_stage_preserves_all_later_reserves(self):
        current = window(hard_wall=108540, hard_monotonic=9540)
        deadlines = [current.deadline(name) for name, _ in plan.stage_budgets(100)]
        self.assertEqual(deadlines, [1100, 2900, 6500, 7400, 9360, 9540])
        self.assertEqual(current.cutoff_monotonic - current.deadline("throughput"), 1960 + 180)
        self.assertEqual(current.cutoff_monotonic - current.deadline("long"), 900 + 1960 + 180)
        self.assertEqual(current.cutoff_monotonic - current.deadline("startup"), 3600 + 900 + 1960 + 180)

    def test_both_original_clocks_limit_admission(self):
        for field in ("hard_wall", "hard_monotonic"):
            start = 100000 if field == "hard_wall" else 1000
            for remaining in (8540 - .1, 86400 + .1):
                with self.subTest(field=field, remaining=remaining), self.assertRaises(ValueError):
                    window(**{field: start + remaining})
        self.assertEqual(window(hard_wall=108600).cutoff_monotonic, 9600)
        self.assertEqual(window(hard_monotonic=9600).cutoff_monotonic, 9600)
        self.assertEqual(window(hard_wall=186400, hard_monotonic=87400).deadline("long"), 6500)

    def test_forward_wall_step_shortens_and_backward_step_never_renews(self):
        original = window()
        shortened = original.observe(now_wall=101010, now_monotonic=1010)
        self.assertEqual(shortened.cutoff_monotonic, 9000)
        self.assertEqual(shortened.original_hard_monotonic, original.original_hard_monotonic)
        self.assertEqual(shortened.deadline("throughput"), 9000 - 1960 - 180)
        backwards = shortened.observe(now_wall=100000, now_monotonic=1020)
        self.assertEqual(backwards.cutoff_monotonic, shortened.cutoff_monotonic)
        for name, _ in plan.stage_budgets(100):
            self.assertEqual(backwards.deadline(name), shortened.deadline(name))
        self.assertEqual(original.cutoff_monotonic, 10000)
        with self.assertRaises(FrozenInstanceError):
            shortened.cutoff_monotonic = 10000

    def test_late_start_and_expiry_do_not_move_stage_ends(self):
        current = window()
        for elapsed in (50, 3000, 9000, 10000):
            current = current.observe(now_wall=100000 + elapsed, now_monotonic=1000 + elapsed)
            self.assertEqual(current.deadline("startup"), 2900)
            self.assertEqual(current.deadline("throughput"), 7400)
            self.assertEqual(current.deadline("host-cleanup"), 9540)
        self.assertLess(current.deadline("throughput"), current.observed_monotonic)
        with self.assertRaises(ValueError):
            window(now_wall=109000, now_monotonic=10000)

    def test_invalid_clock_profile_and_stage_inputs_are_content_free(self):
        for field in ("now_wall", "now_monotonic", "hard_wall", "hard_monotonic"):
            for value in (True, None, -1, float("nan"), float("inf"), 10**1000, "PRIVATE_CANARY"):
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, "^combined qualification plan rejected$"):
                    window(**{field: value})
        for value in (True, None, {}, "startup ", "PRIVATE_CANARY"):
            with self.assertRaisesRegex(ValueError, "^combined qualification plan rejected$"):
                window().deadline(value)
        with self.assertRaises(ValueError):
            window(execution_profile="batch32-graphs")
        with self.assertRaises(ValueError):
            window().observe(now_wall=100000, now_monotonic=999)
        with self.assertRaises(ValueError):
            window().observe(now_wall=float("nan"), now_monotonic=1000)


if __name__ == "__main__":
    unittest.main()
