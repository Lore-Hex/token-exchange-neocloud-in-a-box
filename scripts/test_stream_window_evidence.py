# SPDX-License-Identifier: Apache-2.0
"""Partial measured responses do not turn an exhausted cohort into a pass."""

import json
import unittest

import stream_cohort as cohort
import stream_lab as binding
from test_stream_failure_evidence import ROOT, SourceBoundStreamEvidence


class StreamWindowEvidenceTests(SourceBoundStreamEvidence, unittest.TestCase):
    directory = ROOT / "evidence/p2/2026-10-23-stream-window-exhaustion"
    producer = "8d8d6167285dfe40b88d23fb813818f8f2b95222"
    hashes = {
        "run.json": "21b3671925389cfb052a03a2b3a94d92090acd6855d4b77b6509e7774a3f0613",
        "output.log": "f0326789829bffc3ab1c1531734b9244f5bd16ae49d9f8349a59375b70ba4a4a",
        "input-contract.json": "dc60227b728096e0aa15383f2adbf0c1624f298d110df5bcb7a54c7d4b403e3b",
        "receipt.json": "ea271fe5fa7eca2151da8bd25c5342ce57f924a94547d2494c76b9ee4270df69",
    }

    def document(self, name):
        return json.loads((self.directory / name).read_bytes())

    def test_original_partial_cohort_validates_without_success_or_invented_usage(self):
        run, report, contract = (self.document(name) for name in ("run.json", "output.log", "input-contract.json"))
        experiment = report["experiment"]
        stream = experiment["stream_cohort"]
        self.assertFalse(binding.validate(stream, contract, self.hashes["input-contract.json"], cohort))
        self.assertEqual((run["started_utc"], run["ended_utc"]),
                         ("2026-10-23T02:39:18Z", "2026-10-23T03:08:44Z"))
        self.assertEqual(experiment["startup"]["wait_ms"], 1213356)
        self.assertTrue(experiment["tokenizer_parity"]["all_cases_pass"])
        self.assertEqual(experiment["tokenizer_parity"]["requests_started"], 8)
        self.assertEqual(experiment["stage"], "complete")
        row = stream["cohort"]
        self.assertEqual(row["stop_reason"], "request-failed")
        self.assertFalse(row["completed"])
        self.assertGreaterEqual(row["elapsed_seconds"], 180)
        self.assertLess(row["elapsed_seconds"], 181)
        self.assertEqual((row["started_requests"], row["not_run_requests"]), (5, 4))
        self.assertEqual(row["known_output_tokens"], 1600)
        measured = row["measured"]
        self.assertEqual((measured["scheduled"], measured["completed"], measured["failed"], measured["not_run"]),
                         (8, 3, 1, 4))
        self.assertEqual(measured["completed_output_tokens"], 1200)
        self.assertEqual(measured["requests_with_unknown_usage"], 1)
        self.assertEqual(measured["ttft_seconds"]["samples"], 3)
        last = row["samples"][-1]
        self.assertEqual((last["status"], last["output_events"]), ("timeout", 235))
        self.assertIsNone(last["completion_tokens"])
        self.assertIsNone(last["prompt_tokens"])
        self.assertIsNone(last["ttft_seconds"])
        self.assertTrue(stream["terminal_identity_verified"])
        self.assertTrue(experiment["engine_stopped"])
        self.assertEqual(report["cleanup"], "confirmed")
        self.assertEqual(report["container_observation"], {"status": "exited", "exit_code": 0, "oom_killed": False})
        self.assertEqual(run["result"], "fail")
        self.assertFalse(run["hardware_validated"])
        for value in (report, experiment):
            self.assertEqual(value["status"], "BLOCKED")
            for field in ("functional_pass", "deployable", "confidential"):
                self.assertIs(value[field], False)
        for field in ("quality_accepted", "sweep_accepted", "confidential"):
            self.assertIs(row[field], False)


if __name__ == "__main__":
    unittest.main()
