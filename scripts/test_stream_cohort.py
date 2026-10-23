# SPDX-License-Identifier: Apache-2.0
"""Controller tests with synthetic tokenizers/streams, not hardware evidence."""

import contextlib
import io
import json
import time
import unittest
from unittest import mock

import stream_cohort as cohort
from test_stream_transport import client, event, server


class Tokenizer:
    def __init__(self, *, factor=1, base=31):
        self.factor, self.base, self.calls = factor, base, 0

    def apply_chat_template(self, messages, **options):
        self.calls += 1
        assert options == {"tools": None, "tokenize": True, "add_generation_prompt": True,
                           "continue_final_message": False, "reasoning_effort": "none", "enable_thinking": False}
        padding = messages[0]["content"].count(" x")
        return [42] * (self.base + padding * self.factor)


def workload():
    return cohort.Workload(tuple((2017, cohort.digest([42] * 2048)) for _ in range(cohort.TOTAL_REQUESTS)))


def sample(status="completed", *, known=True, elapsed=1, output=50):
    return cohort.meter.Sample(status, elapsed, 0.1, 0.1, 0.9, 2,
        2048 if known else None, output if known else None, None, "stop" if known else None)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@contextlib.contextmanager
def fake_collection(rows, *, advance=1):
    clock, observed = Clock(), []

    def collect(client, messages, **options):
        observed.append((messages, options))
        clock.now += advance
        row = rows[len(observed) - 1]
        if isinstance(row, BaseException):
            raise row
        return row

    with mock.patch.object(cohort.time, "monotonic", side_effect=clock), \
            mock.patch.object(cohort.stream.OwnedStreamHTTP, "collect", autospec=True, side_effect=collect):
        yield clock, observed


def endpoint():
    return cohort.stream.identity.Endpoint(8000, {"process": {}})


class CohortTests(unittest.TestCase):
    def test_prepare_exact_counts_distinct_messages_and_bounded_storage(self):
        tokenizer = Tokenizer()
        result = cohort.prepare(tokenizer, deadline=time.monotonic() + 3)
        self.assertEqual(result.cases, workload().cases)
        self.assertEqual(tokenizer.calls, 27)
        rows = result.metadata()["cases"]
        self.assertEqual(len({row["messages_sha256"] for row in rows}), 9)
        self.assertEqual({row["prompt_tokens"] for row in rows}, {2048})
        self.assertNotIn("Read the filler", repr(result) + json.dumps(result.metadata()))
        self.assertEqual(result.metadata()["binding_sha256"], cohort.digest(rows))

    def test_prepare_measures_actual_counts_instead_of_assuming_one_token_padding(self):
        result = cohort.prepare(Tokenizer(factor=2, base=32), deadline=time.monotonic() + 3)
        self.assertEqual({row[0] for row in result.cases}, {1008})

    def test_prepare_no_nearby_exact_fit_fails_instead_of_approximating(self):
        tokenizer = Tokenizer(factor=2)
        with self.assertRaises(ValueError):
            cohort.prepare(tokenizer, deadline=time.monotonic() + 3)
        self.assertEqual(tokenizer.calls, 35)

    def test_prepare_rejects_bad_tokenizer_results(self):
        for ids in ([], [True], [-1], [2**31], (42,), [1] * 16385):
            with self.subTest(ids_type=type(ids)), \
                    mock.patch.object(Tokenizer, "apply_chat_template", return_value=ids), \
                    self.assertRaises(ValueError):
                cohort.prepare(Tokenizer(), deadline=time.monotonic() + 1)

    def test_prepare_hard_deadline_and_fixed_message_bounds(self):
        with self.assertRaises(cohort.stream.worker.Abort):
            cohort.prepare(Tokenizer(), deadline=time.monotonic() - 1)
        def stalled(*args, **kwargs):
            time.sleep(2)
            return [1]
        with mock.patch.object(Tokenizer, "apply_chat_template", side_effect=stalled), \
                self.assertRaises(cohort.stream.worker.Abort):
            cohort.prepare(Tokenizer(), deadline=time.monotonic() + 0.03)
        for index, padding in ((-1, 2), (9, 2), (True, 2), (0, -1), (0, True), (0, 4097)):
            with self.subTest(index=index, padding=padding), self.assertRaises(ValueError):
                cohort.messages(index, padding)

    def test_success_separates_warmup_and_all_accounting(self):
        with fake_collection([sample()] * 9) as (clock, seen):
            result = cohort.run(endpoint(), workload(), deadline=1000, gpu_count=8)
        self.assertTrue(result["completed"])
        self.assertEqual(result["elapsed_seconds"], 9)
        self.assertEqual(result["reserved_total_tokens"], 23040)
        self.assertEqual(result["started_requests"], 9)
        self.assertEqual(result["known_prompt_tokens"], 9 * 2048)
        self.assertEqual(result["known_output_tokens"], 450)
        self.assertEqual(result["measured"]["completed_output_tokens"], 400)
        self.assertEqual(result["measured"]["wall_seconds"], 8)
        self.assertEqual(result["measured"]["completed_output_tokens_per_second_per_gpu"], 6.25)
        self.assertEqual({row[1]["deadline"] for row in seen}, {280})
        self.assertEqual({row[1]["max_output_tokens"] for row in seen}, {512})
        self.assertEqual(len({row[0][0]["content"] for row in seen}), 9)
        for name in ("pricing_measured", "quality_accepted", "confidential", "sweep_accepted"):
            self.assertIs(result[name], False)

    def test_warmup_failure_stops_no_measured_cohort_no_retry(self):
        with fake_collection([sample("transport-error", known=False)]) as (_, seen):
            result = cohort.run(endpoint(), workload(), deadline=1000, gpu_count=4)
        self.assertEqual(len(seen), 1)
        self.assertEqual(result["stop_reason"], "warmup-failed")
        self.assertEqual(result["started_requests"], 1)
        self.assertEqual(result["not_run_requests"], 8)
        self.assertEqual(result["requests_with_unknown_usage"], 1)
        self.assertEqual(result["samples"], [])
        self.assertIsNone(result["measured"])

    def test_failed_known_usage_counted_not_in_throughput(self):
        with fake_collection([sample(), sample(), sample("protocol-error")]) as (_, seen):
            result = cohort.run(endpoint(), workload(), deadline=1000, gpu_count=8)
        self.assertEqual(len(seen), 3)
        measured = result["measured"]
        self.assertEqual((measured["scheduled"], measured["started"], measured["completed"],
                          measured["failed"], measured["not_run"]), (8, 2, 1, 1, 6))
        self.assertEqual(measured["completed_output_tokens"], 50)
        self.assertEqual(measured["reported_failed_output_tokens"], 50)
        self.assertEqual(result["known_output_tokens"], 150)
        self.assertEqual(measured["wall_seconds"], 2)
        self.assertEqual(measured["ttft_seconds"]["samples"], 1)
        self.assertEqual(result["stop_reason"], "request-failed")
        self.assertEqual(result["not_run_requests"], 6)

    def test_deadline_before_dispatch_and_between_requests(self):
        with fake_collection([]) as (_, seen):
            result = cohort.run(endpoint(), workload(), deadline=99, gpu_count=8)
        self.assertEqual(seen, [])
        self.assertEqual(result["not_run_requests"], 9)
        self.assertIsNone(result["warmup"])
        self.assertIsNone(result["measured"])
        with fake_collection([sample(), sample()]) as (_, seen):
            result = cohort.run(endpoint(), workload(), deadline=102, gpu_count=8)
        self.assertEqual(len(seen), 2)
        self.assertEqual(result["stop_reason"], "deadline")
        self.assertEqual(result["measured"]["not_run"], 7)
        self.assertFalse(result["completed"])

    def test_final_deadline_overrun_not_success(self):
        with fake_collection([sample()] * 9):
            result = cohort.run(endpoint(), workload(), deadline=109, gpu_count=8)
        self.assertEqual(result["measured"]["completed"], 8)
        self.assertEqual(result["stop_reason"], "deadline")
        self.assertFalse(result["completed"])

    def test_unexpected_error_cancel_and_timeout_fixed_metadata_only(self):
        canary = "private-exception-canary"
        exceptions = [(OSError(canary), "transport-error"), (ValueError(canary), "protocol-error"),
                      (KeyboardInterrupt(canary), "cancelled"), (SystemExit(canary), "cancelled"),
                      (TimeoutError(canary), "timeout"), (cohort.stream.worker.Abort("timeout"), "timeout")]
        for error, status in exceptions:
            with self.subTest(status=status), fake_collection([sample(), error]) as (_, seen), \
                    contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
                result = cohort.run(endpoint(), workload(), deadline=1000, gpu_count=4)
            self.assertEqual(len(seen), 2)
            self.assertEqual(result["samples"][0]["status"], status)
            self.assertEqual(result["requests_with_unknown_usage"], 1)
            self.assertNotIn(canary, json.dumps(result) + out.getvalue() + err.getvalue())

    def test_rejects_unreviewed_runtime_budget_and_workload_types(self):
        for gpu_count in (True, 0, 1, 3, 16):
            with self.subTest(gpu_count=gpu_count), self.assertRaises(ValueError):
                cohort.run(endpoint(), workload(), deadline=time.monotonic() + 1, gpu_count=gpu_count)
        for rows in ([], (), ((1, "bad"),) * 9, ((True, "a" * 64),) * 9):
            with self.subTest(rows=repr(rows)[:30]), self.assertRaises(ValueError):
                cohort.Workload(rows).validate()
        with self.assertRaises(ValueError):
            cohort.run(object(), workload(), deadline=time.monotonic() + 1, gpu_count=8)

    def test_real_socket_complete_cohort_and_no_content_in_result(self):
        data = (event([{"index": 0, "delta": {"content": "synthetic-output-canary"}, "finish_reason": None}])
                + event([{"index": 0, "delta": {}, "finish_reason": "stop"}])
                + event([], usage={"prompt_tokens": 2048, "completion_tokens": 5, "total_tokens": 2053})
                + b"data: [DONE]\n\n")
        with server(data) as seen, client() as (http, guard):
            result = cohort.run(http.endpoint, workload(), deadline=time.monotonic() + 3, gpu_count=8)
        self.assertTrue(result["completed"])
        self.assertEqual(len(seen), 9)
        self.assertEqual(guard.call_count, 27)
        self.assertEqual(result["known_output_tokens"], 45)
        self.assertEqual(result["measured"]["completed_output_tokens"], 40)
        encoded = json.dumps(result, allow_nan=False)
        self.assertNotIn("synthetic-output-canary", encoded)
        self.assertNotIn("Read the filler", encoded)


if __name__ == "__main__":
    unittest.main()
