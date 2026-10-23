# SPDX-License-Identifier: Apache-2.0
"""A complete streaming baseline is not quality or confidential qualification."""

import copy
import json
import unittest

import stream_cohort as cohort
import stream_lab as binding
from test_stream_failure_evidence import ROOT, SourceBoundStreamEvidence


class StreamCompleteEvidenceTests(SourceBoundStreamEvidence, unittest.TestCase):
    directory = ROOT / "evidence/p2/2026-10-23-stream-600s-complete"
    producer = "ecddc03f2fd1e39501b92048afe9ab66bc1c8c78"
    hashes = {
        "run.json": "1d6fcb7144803e790e5265c313b33a0bfdc5e3b213123f20acd984cb4bbd43e8",
        "output.log": "320b369a53f9b136d0aa0193f148b206b28a2067777e12e1244fd6b469e600b5",
        "input-contract.json": "09d4f92574af24e21c4fe48861d2967bf8fb8adef11ad249ac5bf0733e9e2de3",
        "receipt.json": "074b04fe7da20465f9cbc0f11a3823a88361c281d441368314b87ba6c9c86e82",
    }

    def document(self, name):
        return json.loads((self.directory / name).read_bytes())

    def test_complete_baseline_preserves_actual_usage_and_qualification_limits(self):
        run, report, contract = (self.document(name) for name in ("run.json", "output.log", "input-contract.json"))
        experiment = report["experiment"]
        stream = experiment["stream_cohort"]
        self.assertEqual(contract["stream_cohort"]["profile"], binding.EXTENDED_PROFILE)
        self.assertTrue(binding.validate(stream, contract, self.hashes["input-contract.json"], cohort))
        self.assertEqual((run["started_utc"], run["ended_utc"]),
                         ("2026-10-23T03:41:08Z", "2026-10-23T04:13:29Z"))
        self.assertEqual(experiment["startup"]["wait_ms"], 1225332)
        self.assertTrue(experiment["tokenizer_parity"]["all_cases_pass"])
        self.assertEqual(experiment["tokenizer_parity"]["requests_started"], 8)
        self.assertEqual(experiment["stage"], "complete")
        row = stream["cohort"]
        self.assertTrue(row["completed"])
        self.assertEqual(row["stop_reason"], "complete")
        self.assertAlmostEqual(row["elapsed_seconds"], 344.3090259180026)
        self.assertEqual((row["started_requests"], row["not_run_requests"]), (9, 0))
        self.assertEqual((row["known_prompt_tokens"], row["known_output_tokens"]), (18432, 3600))
        self.assertEqual(row["requests_with_unknown_usage"], 0)
        for sample in [row["warmup"], *row["samples"]]:
            self.assertEqual((sample["status"], sample["prompt_tokens"], sample["completion_tokens"]),
                             ("completed", 2048, 400))
            self.assertEqual((sample["output_events"], sample["finish_reason"]), (399, "stop"))
            self.assertIsNone(sample["cached_prompt_tokens"])
        measured = row["measured"]
        self.assertEqual((measured["scheduled"], measured["completed"], measured["failed"], measured["not_run"]),
                         (8, 8, 0, 0))
        self.assertEqual(measured["completed_output_tokens"], 3200)
        self.assertAlmostEqual(measured["wall_seconds"], 301.0780012059986)
        self.assertAlmostEqual(measured["completed_output_tokens_per_second_per_gpu"],
                               3200 / measured["wall_seconds"] / 8)
        self.assertEqual(measured["ttft_seconds"]["samples"], 8)
        self.assertAlmostEqual(measured["ttft_seconds"]["p50"], 0.2868238669980201)
        self.assertAlmostEqual(measured["ttft_seconds"]["p95"], 0.5652224569996176)
        self.assertTrue(stream["terminal_identity_verified"])
        self.assertTrue(experiment["engine_stopped"])
        self.assertEqual(report["cleanup"], "confirmed")
        self.assertEqual(report["container_observation"], {"status": "exited", "exit_code": 0, "oom_killed": False})
        for value in (report, experiment):
            self.assertTrue(value["functional_pass"])
            self.assertEqual(value["status"], "BLOCKED")
            self.assertFalse(value["deployable"])
            self.assertFalse(value["confidential"])
        self.assertEqual(run["result"], "fail")
        self.assertFalse(run["hardware_validated"])
        self.assertFalse(experiment["hardware"]["attestation_verified"])
        for field in ("quality_accepted", "sweep_accepted", "confidential", "pricing_measured"):
            self.assertIs(row[field], False)

    def test_complete_receipt_rejects_qualification_upgrade_or_invented_usage(self):
        original = self.document("output.log")["experiment"]["stream_cohort"]
        contract = self.document("input-contract.json")
        for mutate in (
                lambda r: r["cohort"].update(quality_accepted=True),
                lambda r: r["cohort"].update(sweep_accepted=True),
                lambda r: r["cohort"].update(confidential=True),
                lambda r: r["cohort"].update(known_output_tokens=4608),
                lambda r: r["cohort"]["samples"][0].update(completion_tokens=399)):
            with self.subTest(mutate=mutate):
                changed = copy.deepcopy(original)
                mutate(changed)
                with self.assertRaises((ValueError, AssertionError)):
                    binding.validate(changed, contract, self.hashes["input-contract.json"], cohort)
        changed = copy.deepcopy(original)
        changed["terminal_identity_verified"] = False
        self.assertFalse(binding.validate(changed, contract, self.hashes["input-contract.json"], cohort))


if __name__ == "__main__":
    unittest.main()
