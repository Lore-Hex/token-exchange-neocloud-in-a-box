# SPDX-License-Identifier: Apache-2.0
"""Synthetic offline cache preparation; no native tokenizer or endpoint calls."""

import contextlib
import io
import json
import time
import types
import unittest
from unittest import mock

import cache_workload as workload


class CacheWorkloadTests(unittest.TestCase):
    def prepare(self, measure=None):
        bodies = []

        def tokens(body):
            bodies.append(body)
            padding = body["messages"][0]["content"].count(" x")
            return [7] * (padding + 40) if measure is None else measure(padding)

        counter = types.SimpleNamespace(binding="a" * 64, tokens=tokens, close=mock.Mock())
        with mock.patch.object(workload.tokenizer, "Counter", return_value=counter) as factory:
            try:
                result = workload.prepare("source", "model", "wheel", deadline=time.monotonic() + 10)
            finally:
                counter.close.assert_called_once_with()
                factory.assert_called_once_with("source", "model", "wheel", context_tokens=262144)
        return result, bodies

    def test_exact_fixed_shapes_only_numeric_hash_output(self):
        row, bodies = self.prepare()
        self.assertEqual([item["prompt_tokens"] for item in row["cases"]], [2048, 32768, 131072])
        self.assertEqual([item["max_output_tokens"] for item in row["cases"]], [512, 1024, 1024])
        for item in row["cases"]:
            self.assertEqual(item["padding"], item["prompt_tokens"] - 40)
            self.assertEqual(item["tokens_sha256"], workload.probe.workload.digest([7] * item["prompt_tokens"]))
            self.assertEqual(item["messages_sha256"], workload.probe.workload.digest(
                workload.probe.workload.messages(3, item["padding"])))
        self.assertEqual(len(bodies), 9)
        self.assertTrue(all(body["cache_salt"] == "0" * 64 for body in bodies))
        self.assertTrue(all(body["reasoning_effort"] == "none" for body in bodies))
        self.assertEqual(row["tokenizer_binding_sha256"], "a" * 64)
        for key in ("inference_executed", "runtime_parity_verified", "billing_reconciled", "tariff_change_justified"):
            self.assertIs(row[key], False)
        self.assertNotIn("CEDAR42", json.dumps(row))

    def test_estimate_not_used_as_token_evidence(self):
        def nonlinear(padding):
            return [9] * (padding + (40 if padding in (64, 128) else 43))
        row, bodies = self.prepare(nonlinear)
        self.assertGreater(len(bodies), 9)
        self.assertEqual([item["padding"] for item in row["cases"]], [2005, 32725, 131029])

    def test_missing_exact_target_rejects_instead_of_rounding(self):
        with self.assertRaises(ValueError):
            self.prepare(lambda padding: [1] * (padding + 40 if padding in (64, 128) else 100))

    def test_invalid_token_ids_counts_or_calibration_reject(self):
        for measure in (lambda _: [], lambda _: [True], lambda _: [-1], lambda _: [2**31],
                        lambda _: [1] * 262145, lambda _: (1, 2), lambda _: [1] * 5):
            with self.subTest(measure=measure), self.assertRaises(ValueError):
                self.prepare(measure)

    def test_deadline_checked_before_native_code(self):
        with mock.patch.object(workload.tokenizer, "Counter") as factory:
            for deadline in (True, float("nan"), time.monotonic() - 1):
                with self.subTest(deadline=deadline), self.assertRaises(ValueError):
                    workload.prepare("source", "model", "wheel", deadline=deadline)
            factory.assert_not_called()

    def test_errors_and_paths_do_not_escape_cli(self):
        for args in (["--vllm-source", "PRIVATE_PATH"],
                     ["--vllm-source", "PRIVATE_PATH", "--tokenizer", "model", "--tokenizer-wheel", "wheel"]):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                    mock.patch.object(workload, "prepare", side_effect=RuntimeError("PRIVATE_CONTENT")):
                self.assertEqual(workload.main(args), 1)
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(err.getvalue(), "cache workload preparation rejected\n")

    def test_existing_counter_limits_still_reject_before_file_access(self):
        with mock.patch.object(workload.tokenizer, "verified") as read:
            for context in (True, 8193, 131072, 262145, 1048576):
                with self.subTest(context=context), self.assertRaises(ValueError):
                    workload.tokenizer.Counter("source", "model", "wheel", context_tokens=context)
            read.assert_not_called()

    def test_mid_preparation_timeout_is_closed_and_redacted(self):
        counter = types.SimpleNamespace(binding="a" * 64,
            tokens=mock.Mock(side_effect=workload.worker.Abort("timeout")), close=mock.Mock())
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(workload.tokenizer, "Counter", return_value=counter):
            result = workload.main(["--vllm-source", "PRIVATE_PATH", "--tokenizer", "model",
                                    "--tokenizer-wheel", "wheel"])
        self.assertEqual(result, 1)
        counter.tokens.assert_called_once()
        counter.close.assert_called_once_with()
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "cache workload preparation rejected\n")

    def test_existing_default_and_explicit_contexts_are_preserved(self):
        for kwargs, expected in (({}, 8192), ({"context_tokens": 32768}, 32768),
                                 ({"context_tokens": 262144}, 262144)):
            instance = workload.tokenizer.Counter.__new__(workload.tokenizer.Counter)
            # Stop at the mocked file read on an explicitly supported platform.
            with self.subTest(kwargs=kwargs), mock.patch.object(workload.tokenizer, "verified",
                    side_effect=OSError("synthetic file failure")) as read, \
                    mock.patch.object(workload.tokenizer.platform, "system", return_value="Linux"), \
                    mock.patch.object(workload.tokenizer.platform, "machine", return_value="x86_64"), \
                    self.assertRaises(OSError):
                instance.__init__("source", "model", "wheel", **kwargs)
            self.assertEqual(instance.context_tokens, expected)
            read.assert_called_once()
            instance.tokenizer = types.SimpleNamespace(apply_chat_template=lambda *a, **k: [7] * (expected + 1))
            with self.assertRaises(ValueError):
                instance.tokens({"messages": []})
            instance.close()

    def test_mid_preparation_failure_closes_counter_without_partial_result(self):
        for binding in ("bad", True):
            counter = types.SimpleNamespace(binding=binding, tokens=mock.Mock(), close=mock.Mock())
            with self.subTest(binding=binding), mock.patch.object(workload.tokenizer, "Counter", return_value=counter), \
                    self.assertRaises(ValueError):
                workload.prepare("source", "model", "wheel", deadline=time.monotonic() + 10)
            counter.close.assert_called_once_with()
            counter.tokens.assert_not_called()


if __name__ == "__main__":
    unittest.main()
