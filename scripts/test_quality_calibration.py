# SPDX-License-Identifier: Apache-2.0
"""Calibration transport accounting; synthetic replies only, no inference."""
import importlib.util
import json
from pathlib import Path
import time
import types
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("quality_calibration", Path(__file__).with_name("record-quality-calibration.py"))
quality = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quality)
report_spec = importlib.util.spec_from_file_location("quality_summary", Path(__file__).with_name("summarize-quality-calibration.py"))
summary = importlib.util.module_from_spec(report_spec)
report_spec.loader.exec_module(summary)


def reply():
    return {"model": quality.MODEL, "usage": {"prompt_tokens": 20, "completion_tokens": 3, "total_tokens": 23},
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "SYNTHETIC_REPLY_CANARY"}}]}


class CalibrationTests(unittest.TestCase):
    def test_summary_full_denominator_and_no_input_text(self):
        manifest = {"schema": "nib.quality-calibration/v1", "suite": "gsm8k", "expected": 20,
                    "started_at": "2026-10-26T23:00:00+00:00", "git_sha": "a" * 40,
                    "trbench_revision": "b" * 40, "dataset_sha256": "c" * 64,
                    "trbench_tree_sha256": "d" * 64, "endpoint_binding_sha256": "e" * 64}
        item = {"kind": "item", "index": 0, "status": "evaluated", "score": {"correct": 1, "total": 1},
                "usage": {"prompt_tokens": 20, "completion_tokens": 3}, "elapsed_seconds": 1.5,
                "unexpected_text": "SYNTHETIC_PRIVATE_CANARY"}
        terminal = {"kind": "summary", "expected": 20, "attempted": 1, "evaluated": 1, "missing": 19,
                    "ended_at": "2026-10-26T23:01:00+00:00"}
        raw = lambda: b"\n".join(json.dumps(row).encode() for row in (manifest, item, terminal))
        record = summary.report(raw())
        self.assertEqual((record["result"], record["correct"], record["expected"]), ("fail", 1, 20))
        self.assertNotIn("CANARY", json.dumps(record))
        terminal["missing"] = 0
        with self.assertRaises(ValueError):
            summary.report(raw())

    def test_rejects_wrong_model_counts_and_nontext_outputs(self):
        for mutate in (lambda r: r.update(model="wrong"), lambda r: r["usage"].update(prompt_tokens=21),
                       lambda r: r["usage"].update(completion_tokens=True),
                       lambda r: r["usage"].update(total_tokens=24), lambda r: r.update(choices=[]),
                       lambda r: r["choices"][0]["message"].update(tool_calls=[{}]),
                       lambda r: r["choices"][0]["message"].update(reasoning_content="reasoning")):
            value = reply()
            mutate(value)
            with self.assertRaises(ValueError):
                quality.numeric_reply(value, 20)

    def test_real_scoring_and_truncation_keep_usage_without_content(self):
        for finish in ("stop", "length"):
            value = reply()
            value["choices"][0]["finish_reason"] = finish
            http = types.SimpleNamespace(last_status=500, post=mock.Mock(return_value=value))
            counter = types.SimpleNamespace(count=mock.Mock(return_value=20))
            grade = mock.Mock(return_value={"correct": int(finish == "stop"), "total": 1})
            record = quality.evaluate(0, {}, lambda r: "SYNTHETIC_PROMPT_CANARY", grade, counter,
                                      http, b"SYNTHETIC_KEY_CANARY", time.monotonic() + 10)
            self.assertEqual(record["status"], "evaluated" if finish == "stop" else "truncated")
            self.assertEqual(record["usage"], {"prompt_tokens": 20, "completion_tokens": 3})
            self.assertIsNone(record["http_status"])
            grade.assert_called_once_with({}, "SYNTHETIC_REPLY_CANARY" if finish == "stop" else "")
            http.post.assert_called_once()
            self.assertNotIn("CANARY", json.dumps(record))

    def test_failure_stage_and_usage_remain_honest(self):
        for stage in ("tokenize", "request", "response-validation", "grade"):
            counter = types.SimpleNamespace(count=mock.Mock(return_value=20))
            http = types.SimpleNamespace(last_status=500, post=mock.Mock(return_value=reply()))
            grade = mock.Mock(return_value={"correct": 1})
            if stage == "tokenize": counter.count.side_effect = ValueError("PRIVATE_CANARY")
            if stage == "request": http.post.side_effect = OSError("PRIVATE_CANARY")
            if stage == "response-validation": http.post.return_value = {}
            if stage == "grade": grade.side_effect = ValueError("PRIVATE_CANARY")
            record = quality.evaluate(0, {}, lambda r: "PRIVATE_CANARY", grade, counter, http,
                                      b"PRIVATE_CANARY", time.monotonic() + 10)
            self.assertEqual(record["stage"], stage)
            self.assertEqual(record["status"], "protocol_or_transport_error")
            self.assertEqual(record["usage"] is not None, stage == "grade")
            self.assertNotIn("CANARY", json.dumps(record))
            self.assertLessEqual(http.post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
