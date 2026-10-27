# SPDX-License-Identifier: Apache-2.0
"""Calibration transport accounting; synthetic replies only, no inference."""
import importlib.util
from contextlib import ExitStack, redirect_stdout, redirect_stderr
import io
import json
import os
import py_compile
from pathlib import Path
import subprocess
import sys
import time
import tempfile
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
    def test_real_writer_preserves_interrupted_transmission(self):
        def post(*args, before_send, **kwargs):
            before_send()
            raise KeyboardInterrupt
        contract = {"schema": "nib.tau-external-trial-endpoint/v2", "model": quality.MODEL,
                    "serving_profile": "long1m-graphs", "retire_at": time.time() + 7200,
                    "certificate": "/unused", "hostname": "synthetic.invalid", "address": "8.8.8.8",
                    "port": 18443, "certificate_sha256": "a" * 64}
        raw = json.dumps(contract).encode()
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            output = Path(temporary) / "receipt.jsonl"
            args = ["quality", "--suite", "gsm8k", "--count", "20", "--output", str(output),
                    "--contract-sha256", quality.digest(raw)]
            for name in ("trbench-root", "contract", "candidate-key", "vllm-source", "tokenizer", "tokenizer-wheel"):
                args += ["--" + name, "/unused"]
            stack.enter_context(mock.patch.object(quality.sys, "argv", args))
            stack.enter_context(mock.patch.object(quality.sys, "flags", types.SimpleNamespace(isolated=True)))
            stack.enter_context(mock.patch.object(quality, "source", return_value="a" * 64))
            stack.enter_context(mock.patch.object(quality, "cases", return_value=(
                [{}] * 20, lambda r: "SYNTHETIC_PROMPT_CANARY", mock.Mock(), "b" * 64)))
            stack.enter_context(mock.patch.object(quality.tau_live_identity, "private_read", side_effect=[raw, b"pem", b"key"]))
            stack.enter_context(mock.patch.object(quality.bounded_https, "HTTPS", return_value=types.SimpleNamespace(last_status=None, post=post)))
            stack.enter_context(mock.patch.object(quality.tau_external_tokenizer, "Counter", return_value=types.SimpleNamespace(count=lambda b: 20, close=lambda: None)))
            def git(argv, **kwargs):
                if "rev-parse" in argv:
                    return b"a" * 40
                return (quality.ROOT / argv[-1].split(":", 1)[1]).read_bytes()
            stack.enter_context(mock.patch.object(quality.subprocess, "check_output", side_effect=git))
            stack.enter_context(redirect_stdout(io.StringIO()))
            stack.enter_context(redirect_stderr(io.StringIO()))
            self.assertEqual(quality.main(), 1)
            rows = [json.loads(line) for line in output.read_bytes().splitlines()]
            self.assertEqual(rows[1]["status"], "cancelled")
            self.assertEqual(rows[-1]["generation_requests"], 1)
            self.assertEqual(rows[-1]["unknown_usage_requests"], 1)
            self.assertEqual(rows[-1]["missing"], 19)
            self.assertNotIn(b"CANARY", output.read_bytes())

    def test_summary_full_denominator_and_no_input_text(self):
        manifest = {"schema": "nib.quality-calibration/v1", "suite": "gsm8k", "expected": 20,
                    "started_at": "2026-10-26T23:00:00+00:00", "git_sha": "a" * 40,
                    "trbench_revision": "b" * 40, "dataset_sha256": "c" * 64,
                    "trbench_tree_sha256": "d" * 64, "endpoint_binding_sha256": "e" * 64}
        item = {"kind": "item", "index": 0, "status": "evaluated", "score": {"correct": 1, "total": 1, "errors": 0, "score": 100.0},
                "usage": {"prompt_tokens": 20, "completion_tokens": 3}, "elapsed_seconds": 1.5,
                "http_status": 200, "finish_reason": "stop", "generation_started": True,
                "unexpected_text": "SYNTHETIC_PRIVATE_CANARY"}
        terminal = {"kind": "summary", "expected": 20, "attempted": 1, "evaluated": 1, "missing": 19,
                    "errors": 0, "known_prompt_tokens": 20, "known_completion_tokens": 3,
                    "generation_requests": 1, "unknown_usage_requests": 0, "latency_median_seconds": 1.5,
                    "ended_at": "2026-10-26T23:01:00+00:00"}
        raw = lambda: b"\n".join(json.dumps(row).encode() for row in (manifest, item, terminal))
        record = summary.report(raw())
        self.assertEqual((record["result"], record["correct"], record["expected"]), ("fail", 1, 20))
        self.assertNotIn("CANARY", json.dumps(record))
        self.assertTrue(record["items"][0]["generation_started"])
        self.assertEqual(record["generation_requests"], 1)
        for target, key, invalid in ((item, "finish_reason", "length"),
                                     (item["usage"], "completion_tokens", 8193),
                                     (item["usage"], "prompt_tokens", 24577),
                                     (item, "generation_started", False),
                                     (terminal, "generation_requests", 2),
                                     (terminal, "unknown_usage_requests", 1),
                                     (terminal, "known_prompt_tokens", 19),
                                     (terminal, "errors", 1),
                                     (terminal, "ended_at", "2026-10-25T23:01:00+00:00")):
            original = target[key]
            target[key] = invalid
            with self.assertRaises(ValueError):
                summary.report(raw())
            target[key] = original
        terminal["missing"] = 0
        with self.assertRaises(ValueError):
            summary.report(raw())
        terminal["missing"] = 19
        for invalid in (None, {"correct": 9, "total": 1, "errors": 0, "score": 900.0}):
            item["score"] = invalid
            with self.assertRaises(ValueError):
                summary.report(raw())

    def test_verified_import_ignores_bytecode_and_untracked_modules(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "trbench"
            package.mkdir()
            file = package / "__init__.py"
            file.write_bytes(b"answer = 9\n")
            timestamp = int(time.time()) - 100
            os.utime(file, (timestamp, timestamp))
            py_compile.compile(str(file), doraise=True)
            verified = b"answer = 1\n"
            file.write_bytes(verified)
            os.utime(file, (timestamp, timestamp))
            (package / "untracked.py").write_text("answer = 9\n")
            with quality.verified_imports(root, {"trbench/__init__.py": verified}):
                import trbench
                self.assertEqual(trbench.answer, 1)
                with self.assertRaises(ImportError):
                    importlib.import_module("trbench.untracked")
            self.assertNotIn("trbench", sys.modules)

    def test_cli_failures_and_timestamp_never_echo_input(self):
        canary = "SYNTHETIC_PRIVATE_CANARY"
        with self.assertRaisesRegex(ValueError, "^Invalid calibration timestamp$"):
            summary.timestamp(canary)
        for script in ("record-quality-calibration.py", "summarize-quality-calibration.py"):
            proc = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).with_name(script)),
                                   "--suite", canary], capture_output=True, timeout=10)
            self.assertNotEqual(proc.returncode, 0)
            self.assertNotIn(canary.encode(), proc.stdout + proc.stderr)

    def test_pinned_source_enumeration_cannot_use_empty_index(self):
        with mock.patch.object(quality.subprocess, "check_output", side_effect=[b"a" * 40, b""]) as git:
            with self.assertRaises(ValueError):
                quality.source(Path("/unused"), "a" * 40)
            self.assertIn("ls-tree", git.call_args_list[1].args[0])

    def test_late_response_failure_retains_actual_usage(self):
        for mutate in (lambda r: r["choices"][0].update(finish_reason="content_filter"),
                       lambda r: r["usage"].update(prompt_tokens=21, total_tokens=24)):
            value = reply()
            mutate(value)
            http = types.SimpleNamespace(last_status=None, post=mock.Mock(return_value=value))
            record = quality.evaluate(0, {}, lambda r: "synthetic", mock.Mock(),
                types.SimpleNamespace(count=lambda b: 20), http, b"synthetic", time.monotonic() + 10)
            self.assertEqual(record["status"], "protocol_or_transport_error")
            self.assertIsNotNone(record["usage"])

    def test_interrupted_sent_request_is_unknown_but_tokenizer_failure_is_unsent(self):
        def interrupted(*args, before_send, **kwargs):
            before_send()
            raise KeyboardInterrupt
        http = types.SimpleNamespace(last_status=None, post=mock.Mock(side_effect=interrupted))
        counter = types.SimpleNamespace(count=mock.Mock(return_value=20))
        record = quality.evaluate(0, {}, lambda r: "synthetic", mock.Mock(), counter, http,
                                  b"synthetic", time.monotonic() + 10)
        self.assertEqual((record["status"], record["generation_started"], record["usage"]), ("cancelled", True, None))
        counter.count.side_effect = ValueError("private")
        record = quality.evaluate(0, {}, lambda r: "synthetic", mock.Mock(), counter, http,
                                  b"synthetic", time.monotonic() + 10)
        self.assertFalse(record["generation_started"])

    def test_absolute_deadline_covers_grading(self):
        http = types.SimpleNamespace(last_status=None, post=mock.Mock(return_value=reply()))
        record = quality.evaluate(0, {}, lambda r: "synthetic", lambda *args: time.sleep(0.1),
            types.SimpleNamespace(count=lambda b: 20), http, b"synthetic", time.monotonic() + 0.02)
        self.assertEqual(record["status"], "timeout")
        self.assertLess(record["elapsed_seconds"], 0.09)
        self.assertIsNotNone(record["usage"])

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
