# SPDX-License-Identifier: Apache-2.0
"""Synthetic metadata/transport tests. No service, weights, or network access."""

from contextlib import nullcontext
import copy
from dataclasses import replace
import io
import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
import unittest
from unittest import mock

import selected_long_qualification as q
import selected_long_tokenizer as child


def workload():
    return {"schema": q.WORKLOAD_SCHEMA, "cases": [
        {"level": i, "padding": 100 + i, "prompt_tokens": target,
         "tokens_sha256": "a" * 64, "messages_sha256": q.digest(q.messages(i, 100 + i))}
        for i, target in enumerate(q.PROMPTS)]}


def sample(level=0, cached=0, count=7, finish="stop"):
    return q.measurement.Sample("completed", 3.0, 1.0, 1.5, 2.0, 2,
                                q.PROMPTS[level], count, cached, finish)


def collector(work, level, namespace, deadline, row):
    row["attempted"] = True
    return q.Observation(sample(level, cached=64 if row["scope"] == "warm" else 0), True, True)


def run_sequence(collect=collector):
    row = q.empty_report()
    row.update(prepared_sha256="a" * 64, request_seconds=900, cleanup_reserve_seconds=1960,
        workload=workload(), engine_identity_before_sha256="b" * 64)
    try:
        q.sequence(row, row["workload"], collect, deadline=100, clock=lambda: 0)
        row.update(result="completed", failure="none", engine_identity_after_sha256="b" * 64)
    except BaseException as exc:
        row["failure"] = q.failure(exc)
    row["elapsed_seconds"] = 50.0
    q.summarize(row)
    return q.validate_report(q.encoded(row))


def stream(text="CEDAR42", done=True):
    first = {"model": q.service.MODEL, "choices": [{"index": 0,
        "delta": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": q.PROMPTS[0], "completion_tokens": 7,
            "total_tokens": q.PROMPTS[0] + 7, "prompt_tokens_details": {"cached_tokens": 0}}}
    raw = b"data: " + q.encoded(first) + b"\n\n"
    return raw + (b"data: [DONE]\n\n" if done else b"")


class QualificationTests(unittest.TestCase):
    def test_closed_counts_reserve_output_and_never_expand_default_request(self):
        work = workload()
        q.validate_workload(work)
        self.assertEqual(q.PROMPTS, (32512, 65280, 130816, 114688))
        self.assertEqual(len(q.CASES), 10)
        self.assertEqual(q.PROMPTS[-1] + q.LONG_OUTPUT, q.CONTEXT)
        self.assertEqual(q.empty_report()["reserved_prompt_tokens"], 800512)
        self.assertEqual(q.empty_report()["reserved_output_tokens"], 18688)
        for i in range(4):
            body = json.loads(q.request_bytes(work, i, "a" * 64))
            self.assertEqual(body["max_tokens"], q.OUTPUTS[i])
            self.assertEqual(body["model"], q.service.MODEL)
            self.assertEqual(body["reasoning_effort"], "none")
            self.assertTrue(body["stream_options"]["include_usage"])
            self.assertLessEqual(q.PROMPTS[i] + body["max_tokens"], q.CONTEXT)
        for salt in (None, "short", "a" * 65, "A" * 64):
            with self.assertRaises(q.Rejected):
                q.request_bytes(work, 0, salt)

    def test_exact_synthetic_tokenizer_search_has_no_monotonicity_assumption(self):
        class Tokenizer:
            def apply_chat_template(self, messages, **kwargs):
                self.kwargs = kwargs
                return [3] * (messages[0]["content"].count(" x") + 24)
        tokenizer = Tokenizer()
        result = q.prepare(tokenizer, deadline=time.monotonic() + 20)
        self.assertEqual([r["prompt_tokens"] for r in result["cases"]], list(q.PROMPTS))
        self.assertEqual(tokenizer.kwargs["reasoning_effort"], "none")
        self.assertFalse(tokenizer.kwargs["enable_thinking"])
        with self.assertRaises(q.service.identity.worker.Abort):
            q.prepare(tokenizer, deadline=time.monotonic() - 1)
        tokenizer.apply_chat_template = lambda *args, **kwargs: [True]
        with self.assertRaises(q.Rejected):
            q.prepare(tokenizer, deadline=time.monotonic() + 20)

    def test_workload_tampering_and_near_context_reserve_rejected(self):
        for key, value in (("padding", True), ("prompt_tokens", 131072),
                           ("messages_sha256", "b" * 64), ("tokens_sha256", "model text")):
            work = workload()
            work["cases"][2][key] = value
            with self.assertRaises(q.Rejected):
                q.validate_workload(work)

    def test_salts_repeat_only_for_warm_and_are_not_reported(self):
        seen = []
        def observe(*args):
            seen.append((args[1], args[2]))
            return collector(*args)
        result = run_sequence(observe)
        self.assertEqual(result["result"], "completed")
        self.assertTrue(result["context_retrieval_pass"])
        self.assertTrue(result["cache_reuse_observed"])
        self.assertTrue(result["cold_and_negative_zero_observed"])
        self.assertFalse(result["long_output_limit_reached"])
        self.assertTrue(all(result[key] is False for key in q.CLAIMS))
        for i in (0, 3, 6):
            self.assertEqual(seen[i], seen[i + 1])
            self.assertNotEqual(seen[i][1], seen[i + 2][1])
        self.assertEqual(len({salt for _, salt in seen}), 7)
        for _, salt in seen:
            self.assertNotIn(salt, q.encoded(result).decode())
        self.assertEqual(result["known_output_tokens"], 70)
        self.assertEqual(result["usage_unknown_requests"], 0)

    def test_cache_miss_is_observation_not_a_retry_or_rank_isolation_proof(self):
        def miss(*args):
            obs = collector(*args)
            return replace(obs, sample=replace(obs.sample, cached_prompt_tokens=0))
        row = run_sequence(miss)
        self.assertEqual(row["result"], "completed")
        self.assertFalse(row["cache_reuse_observed"])
        self.assertFalse(row["cache_isolation_qualified"])
        self.assertEqual(row["request_rank_identity"], "unknown")
        self.assertEqual(len(row["samples"]), 10)

    def test_no_promotion_of_requested_long_limit_to_generated_tokens(self):
        def limit(*args):
            obs = collector(*args)
            if args[1] == 3:
                obs = replace(obs, sample=replace(obs.sample, completion_tokens=q.LONG_OUTPUT, finish_reason="length"))
            return obs
        row = run_sequence(limit)
        self.assertTrue(row["long_output_limit_reached"])
        self.assertFalse(row["capacity_qualified"])
        self.assertEqual(row["known_output_tokens"], 63 + q.LONG_OUTPUT)

    def test_negative_cache_hit_missing_usage_retrieval_and_prompt_mismatch_stop(self):
        mutations = ((lambda s: replace(s, cached_prompt_tokens=1), "unexpected-cache-hit"),
                     (lambda s: replace(s, cached_prompt_tokens=None), "cache-usage-missing"),
                     (lambda s: replace(s, prompt_tokens=s.prompt_tokens - 1), "prompt-count"))
        for mutate, reason in mutations:
            with self.subTest(reason=reason):
                def bad(*args):
                    obs = collector(*args)
                    return replace(obs, sample=mutate(obs.sample))
                row = run_sequence(bad)
                self.assertEqual(row["failure"], reason)
                self.assertEqual(len(row["samples"]), 1)
                self.assertEqual(row["known_output_tokens"], 7)
        row = run_sequence(lambda *args: replace(collector(*args), retrieval=False))
        self.assertEqual(row["failure"], "retrieval")

    def test_attempt_registered_before_lost_response_or_settled_cancellation(self):
        def lost(*args):
            args[-1]["attempted"] = True
            raise KeyboardInterrupt()
        row = run_sequence(lost)
        self.assertEqual((row["failure"], row["usage_unknown_requests"]), ("cancelled", 1))
        self.assertEqual(len(row["samples"]), 1)
        def settled(*args):
            args[-1]["attempted"] = True
            q.record_sample(args[-1], replace(sample(), status="cancelled"))
            raise KeyboardInterrupt()
        row = run_sequence(settled)
        self.assertEqual((row["known_output_tokens"], row["usage_unknown_requests"]), (7, 0))
        self.assertEqual(row["samples"][0]["measurement"]["status"], "cancelled")

    def test_deadline_stops_before_next_dispatch_without_fabricated_attempt(self):
        row = q.empty_report()
        with self.assertRaises(q.Rejected):
            q.sequence(row, workload(), mock.Mock(side_effect=AssertionError), deadline=10, clock=lambda: 10)
        self.assertEqual(row["samples"], [])
        for exc, expected in ((KeyboardInterrupt(), "cancelled"), (TimeoutError(), "deadline"),
                              (OSError("PRIVATE-CANARY"), "transport")):
            self.assertEqual(q.failure(exc), expected)

    def test_contract_requires_integrated_budget_not_global_defaults(self):
        self.assertEqual(q.budgets(SimpleNamespace(request_seconds=900, cleanup_reserve=1960)), (900, 1960))
        for seconds, reserve in ((901, 1960), (True, 1960), (900, 375), (900, 900), (900, 3601)):
            with self.assertRaises(q.Rejected):
                q.budgets(SimpleNamespace(request_seconds=seconds, cleanup_reserve=reserve))
        with self.assertRaises(AttributeError):
            q.budgets(SimpleNamespace())
        with mock.patch.object(q.process.snapshot, "execute_process_group") as spawn:
            result = q.run_owned(SimpleNamespace())
        self.assertEqual(result["failure"], "contract")
        spawn.assert_not_called()

    def test_report_rejects_tampering_content_and_claim_promotion(self):
        original = run_sequence()
        changes = [lambda r: r.update(request_seconds="PRIVATE-CANARY"),
            lambda r: r.update(cleanup_reserve_seconds=375), lambda r: r.update(known_output_tokens=True),
            lambda r: r.update(cache_reuse_observed=1), lambda r: r.update(pricing_measured=True),
            lambda r: r.update(engine_identity_after_sha256="c" * 64),
            lambda r: r["samples"].reverse(), lambda r: r["samples"][0].update(extra="PRIVATE-CANARY"),
            lambda r: r["samples"][0]["measurement"].update(completion_tokens=True),
            lambda r: r["samples"][0]["measurement"].update(cached_prompt_tokens=q.CONTEXT),
            lambda r: r["samples"][0]["measurement"].update(finish_reason="tool_calls"),
            lambda r: r["samples"][0]["measurement"].update(mean_output_token_interval_estimate_seconds=3),
            lambda r: r["samples"][0].update(resources_closed=False),
            lambda r: r["samples"][0].update(decode_tokens_per_second_estimate=True)]
        for mutate in changes:
            value = copy.deepcopy(original)
            mutate(value)
            with self.assertRaises(ValueError) as caught:
                q.validate_report(q.encoded(value))
            self.assertNotIn("PRIVATE-CANARY", str(caught.exception))

    def test_tokenizer_child_is_bounded_and_uses_prepared_environment(self):
        prepared = SimpleNamespace(interpreter="/verified/python", receipt_sha256="a" * 64,
            lab={"environment": {"PATH": "/verified/bin"}}, check=mock.Mock())
        supervisor = SimpleNamespace(owner=SimpleNamespace(prepared=prepared))
        result = subprocess.CompletedProcess([], 0, q.encoded({"prepared_sha256": "a" * 64, "workload": workload()}), b"")
        with mock.patch.object(q.process.snapshot, "execute_process_group", return_value=result) as execute:
            self.assertEqual(q.prepare_owned(supervisor, time.monotonic() + 400), workload())
        args, kwargs = execute.call_args
        self.assertEqual(args[0], ["/verified/python", "-I", "-S", "-B", str(q.process.ROOT / q.CHILD), "a" * 64])
        self.assertEqual(kwargs["env"], prepared.lab["environment"])
        self.assertEqual((kwargs["cwd"], kwargs["timeout"], kwargs["max_output_bytes"], kwargs["max_stderr_bytes"]),
                         ("/", 180, 8192, 4096))
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        prepared.check.assert_called_once()
        for result in (subprocess.CompletedProcess([], 1, b"PRIVATE-CANARY", b""),
                       subprocess.CompletedProcess([], 0, b"{}", b"PRIVATE-CANARY")):
            with mock.patch.object(q.process.snapshot, "execute_process_group", return_value=result):
                with self.assertRaises(q.Rejected):
                    q.prepare_owned(supervisor, time.monotonic() + 400)
        with self.assertRaises(q.Rejected):
            child.prepare_runtime("a" * 64, time.monotonic() + 1)

    def test_cost_arithmetic_requires_measured_aggregate_and_utilization(self):
        self.assertAlmostEqual(q.cost_inputs(36, 1000, .5), 20)
        for values in ((True, 1, 1), (1, 0, 1), (1, 1, 0), (1, 1, 1.01),
                       (float("nan"), 1, 1), (10**500, 1, 1)):
            with self.assertRaises(q.Rejected):
                q.cost_inputs(*values)


class TransportTests(unittest.TestCase):
    def collect(self, body, *, tail_error=False, checkpoint_error=False, send_error=False, close_error=False):
        supervisor = SimpleNamespace(contract=SimpleNamespace(request_seconds=900),
            inputs=SimpleNamespace(root=Path("/private/test")), owner=SimpleNamespace(hostname="service.invalid"))
        connection = mock.Mock()
        if send_error:
            connection.sendall.side_effect = OSError("PRIVATE-CANARY")
        response = mock.MagicMock()
        response.__enter__.return_value = response
        if close_error:
            response.__exit__.side_effect = OSError("PRIVATE-CANARY")
        response.read1.side_effect = (body, b"")
        response.tail = io.BytesIO()
        if tail_error:
            response.tail = mock.Mock()
            response.tail.read1.side_effect = TimeoutError("PRIVATE-CANARY")
        row = {"attempted": False}
        with mock.patch.object(q.inference, "owned_tls", return_value=nullcontext(connection)), \
             mock.patch.object(q.service.identity, "private_read", return_value=b"synthetic-key"), \
             mock.patch.object(q.service.pilot, "validate_api_key"), \
             mock.patch.object(q.transport, "StreamResponse", return_value=response), \
             mock.patch.object(q.transport, "response_length", return_value=len(body)), \
             mock.patch.object(q.inference, "checkpoint", side_effect=q.Rejected("identity") if checkpoint_error else None):
            observation = q.collect(supervisor, {}, workload(), 0, "a" * 64, time.monotonic() + 10, row)
        self.assertTrue(row["attempted"])
        connection.sendall.assert_called_once()
        request = connection.sendall.call_args.args[0]
        self.assertEqual(request.count(b"POST /v1/chat/completions"), 1)
        self.assertNotIn(b"X-data-parallel-rank", request)
        self.assertNotIn("PRIVATE-CANARY", q.encoded(row).decode())
        return observation, row

    def test_one_closed_authenticated_post_and_actual_usage(self):
        observed, row = self.collect(stream())
        self.assertTrue(observed.resources_closed and observed.retrieval)
        self.assertEqual(observed.sample.status, "completed")
        self.assertEqual(row["measurement"]["completion_tokens"], 7)
        self.assertNotIn("CEDAR42", q.encoded(row).decode())

    def test_usage_survives_missing_done_tail_timeout_and_terminal_identity_failure(self):
        for kwargs in ({"body": stream(done=False)}, {"body": stream(), "tail_error": True},
                       {"body": stream(), "checkpoint_error": True}):
            observed, row = self.collect(**kwargs)
            self.assertNotEqual(observed.sample.status, "completed")
            self.assertEqual(row["measurement"]["completion_tokens"], 7)
            self.assertIsNone(row["measurement"]["ttft_seconds"])

    def test_lost_send_preserves_unknown_usage_despite_closed_connection(self):
        observed, row = self.collect(stream(), send_error=True)
        self.assertTrue(observed.resources_closed)
        self.assertEqual(observed.sample.status, "transport-error")
        self.assertIsNone(row["measurement"]["completion_tokens"])

    def test_response_close_failure_keeps_usage_but_cannot_confirm_resources(self):
        observed, row = self.collect(stream(), close_error=True)
        self.assertFalse(observed.resources_closed)
        self.assertNotEqual(observed.sample.status, "completed")
        self.assertEqual(row["measurement"]["completion_tokens"], 7)

    def test_model_text_canary_is_not_in_metadata(self):
        observed, row = self.collect(stream("PRIVATE-MODEL-TEXT-CANARY"))
        self.assertFalse(observed.retrieval)
        self.assertNotIn("PRIVATE-MODEL-TEXT-CANARY", q.encoded(row).decode())


if __name__ == "__main__":
    unittest.main()
