# SPDX-License-Identifier: Apache-2.0
"""Synthetic stream regressions; no network, model, prompt logs or hardware claims."""

import copy
import json
import math
import unittest

import stream_measurement as measure

MODEL = "nib-benchmark-model"


def chunk(delta=None, finish=None, usage=None):
    return {"model": MODEL, "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}],
            "usage": usage}


def usage(prompt=2048, output=512, cached=None):
    row = {"prompt_tokens": prompt, "completion_tokens": output, "total_tokens": prompt + output}
    if cached is not None:
        row["prompt_tokens_details"] = {"cached_tokens": cached}
    return row


def event(row, newline=b"\n"):
    body = row if type(row) is bytes else json.dumps(row, separators=(",", ":"), ensure_ascii=False).encode()
    return b"data: " + body + newline + newline


def meter():
    return measure.StreamMeasurement(MODEL, timeout_seconds=120, max_prompt_tokens=4096, max_output_tokens=512)


def prefix(m):
    m.feed(event(chunk({"role": "assistant", "content": ""})), 0.1)
    m.feed(event(chunk({"reasoning_content": "synthetic reasoning"})), 0.5)
    m.feed(event(chunk({"content": "synthetic answer"})), 1.5)


def finish(m, *, count=512, cached=None):
    m.feed(event(chunk(finish="stop")), 2)
    m.feed(event({"model": MODEL, "choices": [], "usage": usage(output=count, cached=cached)}), 3)
    m.feed(event(b"[DONE]"), 4)
    return m.close(5)


class MeasurementTests(unittest.TestCase):
    def test_role_finish_usage_and_done_do_not_move_token_clock(self):
        m = meter()
        prefix(m)
        sample = finish(m, cached=1024)
        row = sample.metadata()
        self.assertEqual(sample.status, "completed")
        self.assertEqual(row["elapsed_seconds"], 5)
        self.assertEqual(row["ttft_seconds"], 0.5)
        self.assertEqual(row["first_content_seconds"], 1.5)
        self.assertEqual(row["mean_output_token_interval_estimate_seconds"], 1 / 511)
        self.assertEqual(row["output_events"], 2)
        self.assertEqual(row["cached_prompt_tokens"], 1024)
        self.assertFalse(m.pending or m.event)
        serialized = json.dumps(row)
        for text in ("synthetic reasoning", "synthetic answer"):
            self.assertNotIn(text, serialized)
            self.assertNotIn(text, repr(vars(m)))
        self.assertNotIn(MODEL, serialized)
        self.assertNotIn("tokens_per_chunk", row)

    def test_single_coalesced_event_and_one_token_have_no_invented_tpot(self):
        for count in (1, 512):
            with self.subTest(count=count):
                m = meter()
                m.feed(event(chunk({"content": "answer"})), 0.5)
                row = finish(m, count=count).metadata()
                self.assertEqual(row["ttft_seconds"], 0.5)
                self.assertIsNone(row["mean_output_token_interval_estimate_seconds"])
                self.assertIsNone(row["cached_prompt_tokens"])

    def test_coalesced_arrivals_and_reasoning_only_do_not_invent_visible_or_per_token_timing(self):
        m = meter()
        m.feed(event(chunk({"reasoning_content": "one"})) + event(chunk({"reasoning_content": "two"})), 1)
        row = finish(m).metadata()
        self.assertEqual(row["output_events"], 2)
        self.assertEqual(row["ttft_seconds"], 1)
        self.assertIsNone(row["first_content_seconds"])
        self.assertIsNone(row["mean_output_token_interval_estimate_seconds"])

    def test_fragmented_utf8_cr_lf_crlf_and_multiline_data(self):
        for newline in (b"\r", b"\n", b"\r\n"):
            for split in (1, 2, 7, 65536):
                with self.subTest(newline=newline, split=split):
                    m = meter()
                    raw = b": keepalive" + newline + newline
                    raw += event(chunk({"content": "\u00e9"}, finish="length", usage=usage(output=1)), newline)
                    raw += event(b"[DONE]", newline)
                    for start in range(0, len(raw), split):
                        m.feed(raw[start:start + split], 1)
                    self.assertEqual(m.close(2).status, "completed")
        m = meter()
        # JSON whitespace can span SSE data lines. Never dispatch partial data at EOF.
        raw = json.dumps(chunk({"content": "yes"}, finish="stop", usage=usage(output=1)), indent=2).encode()
        m.feed(b"\n".join(b"data: " + line for line in raw.split(b"\n")) + b"\n\n", 1)
        m.feed(event(b"[DONE]"), 2)
        self.assertEqual(m.close(3).status, "completed")

    def test_truncation_missing_usage_or_finish_and_done_alone_fail_closed(self):
        streams = [b"", event(b"[DONE]"), event(chunk({"content": "x"})),
                   event(chunk({"content": "x"}, finish="stop")) + event(b"[DONE]"),
                   event(chunk({"content": "x"}, usage=usage())),
                   event(chunk({"content": "x"}, finish="stop", usage=usage())) + b"data: [DONE]\n",
                   event(chunk(finish="stop", usage=usage())) + event(b"[DONE]")]
        for raw in streams:
            with self.subTest(raw_length=len(raw)):
                m = meter()
                m.feed(raw, 1)
                self.assertEqual(m.close(2).status, "protocol-error")

    def test_every_two_read_split_preserves_framing_usage_and_nonretention(self):
        raw = event(chunk({"content": "synthetic \u00e9 answer"}, finish="stop", usage=usage()), b"\r\n")
        raw += event(b"[DONE]", b"\r\n")
        self.assertIn(b"\xc3\xa9", raw)
        for boundary in range(len(raw) + 1):
            m = meter()
            m.feed(raw[:boundary], 0.5)
            m.feed(raw[boundary:], 1)
            row = m.close(2).metadata()
            self.assertEqual(row["status"], "completed")
            self.assertEqual((row["prompt_tokens"], row["completion_tokens"]), (2048, 512))
            self.assertFalse(m.event or m.pending)

    def test_duplicate_terminals_json_fields_choices_identity_and_errors_rejected(self):
        valid = chunk({"content": "x"}, finish="stop", usage=usage())
        mutations = [lambda r: r.update(error={"message": "private upstream error"}),
                     lambda r: r.update(model="another-model"), lambda r: r.update(choices=[{}, {}]),
                     lambda r: r["choices"][0].update(index=True),
                     lambda r: r["choices"][0].update(index=1),
                     lambda r: r["choices"][0].update(finish_reason="content_filter"),
                     lambda r: r["choices"][0].update(finish_reason="tool_calls"),
                     lambda r: r["choices"][0].update(delta={"tool_calls": []}),
                     lambda r: r["choices"][0].update(delta={"content": 2}),
                     lambda r: r["choices"][0].update(delta={"role": "user"})]
        for mutate in mutations:
            m = meter()
            row = copy.deepcopy(valid)
            mutate(row)
            m.feed(event(row), 1)
            self.assertEqual(m.close(2).status, "protocol-error")
            self.assertNotIn("private upstream error", json.dumps(vars(m), default=str))
        for raw in (event(valid) + event(valid), event(valid) + event(b"[DONE]") * 2,
                    event(valid) + event(b"[DONE]") + event(chunk({"content": "late"})),
                    event(b'{"model":"x","model":"y"}'), event(b'{"x":NaN}'),
                    event(b"\xff"), event(b"[" * 2000 + b"]" * 2000)):
            m = meter()
            m.feed(raw, 1)
            self.assertEqual(m.close(2).status, "protocol-error")

    def test_invalid_total_usage_remains_unknown(self):
        mutations = [lambda r: r.update(prompt_tokens=True), lambda r: r.update(completion_tokens=1.0),
                     lambda r: r.update(completion_tokens=0), lambda r: r.update(total_tokens=1),
                     lambda r: r.update(total_tokens=True), lambda r: r.update(prompt_tokens=-1),
                     lambda r: r.update(prompt_tokens=4097, total_tokens=4609),
                     lambda r: r.update(completion_tokens=513, total_tokens=2561)]
        for mutate in mutations:
            m = meter()
            row = usage()
            mutate(row)
            m.feed(event(chunk({"content": "x"}, finish="stop", usage=row)), 1)
            result = m.close(2)
            self.assertEqual(result.status, "protocol-error")
            self.assertIsNone(result.prompt_tokens)
            self.assertIsNone(result.completion_tokens)
            self.assertIsNone(result.cached_prompt_tokens)

    def test_bad_cache_details_reject_but_preserve_valid_total_usage(self):
        details = [[], "private-cache-canary", True,
                   *({"cached_tokens": value} for value in (-1, 2049, True, 0.0, "1024", [], {}))]
        for detail in details:
            with self.subTest(detail=detail):
                m = meter()
                row = usage()
                row["prompt_tokens_details"] = detail
                m.feed(event(chunk({"content": "private-output-canary"}, finish="stop", usage=row)), 1)
                m.feed(event(b"[DONE]"), 2)
                sample = m.close(3)
                self.assertEqual(sample.status, "protocol-error")
                self.assertEqual((sample.prompt_tokens, sample.completion_tokens), (2048, 512))
                self.assertIsNone(sample.cached_prompt_tokens)
                self.assertIsNone(sample.metadata()["ttft_seconds"])
                self.assertFalse(m.done or m.pending or m.event)
                report = measure.summarize([sample], scheduled=1, wall_seconds=3, gpu_count=8)
                self.assertEqual((report["completed"], report["failed"]), (0, 1))
                self.assertEqual(report["reported_failed_output_tokens"], 512)
                self.assertEqual(report["completed_output_tokens"], 0)
                self.assertEqual(report["requests_with_unknown_usage"], 0)
                serialized = json.dumps({"sample": sample.metadata(), "report": report, "meter": vars(m)}, default=str)
                self.assertNotIn("private-cache-canary", serialized)
                self.assertNotIn("private-output-canary", serialized)

    def test_bad_output_details_preserve_valid_cache_count_without_success(self):
        for cached in (None, 0, 1024):
            for detail in ([], "private-reasoning-canary", True,
                           {"reasoning_tokens": -1}, {"reasoning_tokens": 513},
                           {"reasoning_tokens": True}, {"reasoning_tokens": "1"}):
                with self.subTest(cached=cached, detail=detail):
                    m = meter()
                    row = usage(cached=cached)
                    row["completion_tokens_details"] = detail
                    m.feed(event(chunk({"content": "private-output-canary"}, finish="length", usage=row)), 1)
                    sample = m.close(2)
                    self.assertEqual(sample.status, "protocol-error")
                    self.assertEqual((sample.prompt_tokens, sample.completion_tokens,
                                      sample.cached_prompt_tokens), (2048, 512, cached))
                    self.assertNotIn("private-reasoning-canary", json.dumps(sample.metadata()))

    def test_invalid_totals_cannot_be_rescued_by_valid_optional_details(self):
        for change in ({"prompt_tokens": True}, {"total_tokens": 99}, {"completion_tokens": 513}):
            m = meter()
            row = usage(cached=1024)
            row.update(completion_tokens_details={"reasoning_tokens": 1}, **change)
            m.feed(event(chunk({"content": "x"}, finish="stop", usage=row)), 1)
            sample = m.close(2)
            self.assertEqual(sample.status, "protocol-error")
            self.assertEqual((sample.prompt_tokens, sample.completion_tokens,
                              sample.cached_prompt_tokens), (None, None, None))

    def test_time_limits_invalid_timestamps_and_sticky_failure(self):
        for time in (-1, True, math.nan, math.inf, 10**500, "1"):
            m = meter()
            m.feed(event(chunk({"content": "x"})), time)
            self.assertEqual(m.close(2).status, "protocol-error")
        m = meter()
        prefix(m)
        m.feed(b": backwards\n\n", 1)
        self.assertEqual(m.close(2).status, "protocol-error")
        m = meter()
        m.feed(event(chunk({"content": "x"})), 121)
        self.assertEqual(m.close(122, failure="transport-error").status, "timeout")
        m = meter()
        m.feed(event(chunk({"content": "x"})), 1)
        self.assertEqual(m.close(2, failure="cancelled").status, "cancelled")
        with self.assertRaisesRegex(ValueError, "stream measurement rejected"):
            m.feed(b"", 3)

    def test_size_bounds_and_unrecognized_framing(self):
        for raw in (b"x" * (measure.MAX_EVENT + 1), b"data: " + b"x" * (measure.MAX_EVENT - 6)):
            m = meter()
            m.feed(raw, 1)
            if not m.failure:
                m.feed(b"x", 2)
            self.assertEqual(m.close(3).status, "size-limit")
            self.assertFalse(m.pending or m.event)
        m = meter()
        m.bytes = measure.MAX_STREAM
        m.feed(b"x", 1)
        self.assertEqual(m.close(2).status, "size-limit")
        for raw in (b"event: error\n\n", b"id: private\n\n", b"not-sse\n\n"):
            m = meter()
            m.feed(raw, 1)
            self.assertEqual(m.close(2).status, "protocol-error")

    def test_valid_usage_survives_later_failure_without_becoming_success(self):
        m = meter()
        m.feed(event(chunk({"content": "x"}, finish="length", usage=usage())), 1)
        row = m.close(2, failure="transport-error").metadata()
        self.assertEqual(row["status"], "transport-error")
        self.assertEqual(row["completion_tokens"], 512)
        self.assertIsNone(row["ttft_seconds"])

    def test_constructor_rejects_ambient_or_unbounded_policy(self):
        for args in ({"model": "https://example.invalid/private?key=x"}, {"timeout_seconds": math.inf},
                     {"timeout_seconds": True}, {"max_prompt_tokens": 1048577}, {"max_output_tokens": 0}):
            values = dict(model=MODEL, timeout_seconds=120, max_prompt_tokens=4096, max_output_tokens=512)
            values.update(args)
            with self.assertRaisesRegex(ValueError, "stream measurement rejected"):
                measure.StreamMeasurement(**values)


class AggregateTests(unittest.TestCase):
    def test_failure_and_not_run_denominators_and_unknown_usage(self):
        good = meter()
        prefix(good)
        good = finish(good)
        failed = meter()
        failed.feed(event(chunk({"content": "x"}, finish="stop", usage=usage(output=10))), 1)
        failed = failed.close(2, failure="transport-error")
        unknown = meter().close(2, failure="timeout")
        row = measure.summarize([good, failed, unknown], scheduled=5, wall_seconds=10, gpu_count=8)
        self.assertEqual((row["scheduled"], row["started"], row["completed"], row["failed"], row["not_run"]),
                         (5, 3, 1, 2, 2))
        self.assertEqual(row["started_error_rate"], 2 / 3)
        self.assertEqual(row["scheduled_noncompletion_rate"], 4 / 5)
        self.assertEqual(row["completed_output_tokens_per_second_per_gpu"], 512 / 10 / 8)
        self.assertEqual(row["reported_failed_output_tokens"], 10)
        self.assertEqual(row["requests_with_unknown_usage"], 1)
        self.assertEqual(row["ttft_seconds"], {"samples": 1, "p50": 0.5, "p95": 0.5})

    def test_empty_or_failed_cohort_has_null_latency_not_zero(self):
        for samples in ([], [meter().close(1, failure="transport-error")]):
            row = measure.summarize(samples, scheduled=1, wall_seconds=2, gpu_count=8)
            self.assertEqual(row["ttft_seconds"], {"samples": 0, "p50": None, "p95": None})
            self.assertEqual(row["scheduled_noncompletion_rate"], 1)
            self.assertEqual(row["completed_output_tokens"], 0)
        self.assertIsNone(measure.summarize([], scheduled=1, wall_seconds=2, gpu_count=8)["started_error_rate"])

    def test_percentile_interpolation_and_argument_bounds(self):
        self.assertEqual(measure.percentile([1, 2, 3, 4], 0.5), 2.5)
        self.assertAlmostEqual(measure.percentile([1, 2, 3, 4], 0.95), 3.85)
        for override in ({"scheduled": 0}, {"scheduled": True}, {"wall_seconds": 0},
                         {"wall_seconds": math.nan}, {"gpu_count": 0}, {"gpu_count": True}):
            args = dict(scheduled=1, wall_seconds=1, gpu_count=8)
            args.update(override)
            with self.assertRaisesRegex(ValueError, "stream measurement rejected"):
                measure.summarize([], **args)
        with self.assertRaisesRegex(ValueError, "stream measurement rejected"):
            measure.summarize([meter().close(3, failure="timeout")], scheduled=1, wall_seconds=2, gpu_count=8)


if __name__ == "__main__":
    unittest.main()
