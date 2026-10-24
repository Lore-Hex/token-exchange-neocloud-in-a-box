# SPDX-License-Identifier: Apache-2.0
"""Selected checkpoint timeout is not throughput, usage, or qualification."""

import copy
import json
import types
import unittest

import stream_lab as binding
from test_evidence_history import git
from test_stream_failure_evidence import ROOT, SourceBoundStreamEvidence


class SelectedStreamEvidenceTests(SourceBoundStreamEvidence, unittest.TestCase):
    directory = ROOT / "evidence/p2/2026-10-24-selected-stream-timeout"
    producer = "5b4e2f54f2b9c14ff46fb9c1efcfbcdbf37b49f1"
    file_count = 111
    hashes = {
        "run.json": "21cc4b97fbb6f840b1b02c67063808a0a06880f487c53009149cd24b21cb7a3d",
        "output.log": "fd80715520af19828dd8ebad16e2bbce03e3f63e5d5f0356d1d0f3552d3e86a4",
        "input-contract.json": "a116183eb63340cdc8c1e6999f0c920447572a6a8161d3daa5023f47276d974f",
        "receipt.json": "61362b060cc76cf0fedb15b558e8324240266145ab191a5195b47b9a2dadd9f0",
    }

    def setUp(self):
        self.run, self.report, self.contract, self.receipt = (
            json.loads((self.directory / name).read_bytes()) for name in self.hashes)
        sources = {name: git(ROOT, "show", self.producer + ":scripts/" + name.removeprefix("stream/"))
                   for name in self.contract["sources"] if name.startswith("stream/")}
        self.cohort = binding.load(sources, self.contract["sources"])
        source = git(ROOT, "show", self.producer + ":scripts/stream_lab.py")
        self.validator = types.ModuleType("selected_stream_original")
        exec(compile(source, "stream_lab.py", "exec"), self.validator.__dict__)
        self.stream = self.report["experiment"]["stream_cohort"]
        self.model = "deepseek-v4.1-flash-1017-uncensored-fp8"

    def validate(self, value):
        return self.validator.validate(value, self.contract, self.hashes["input-contract.json"],
                                       self.cohort, model=self.model)

    def test_timeout_preserves_unknown_usage_and_zero_measured_requests(self):
        self.assertFalse(self.validate(self.stream))
        row = self.stream["cohort"]
        self.assertEqual((self.run["started_utc"], self.run["ended_utc"]),
                         ("2026-10-24T09:53:09Z", "2026-10-24T10:20:15Z"))
        self.assertEqual((row["completed"], row["stop_reason"]), (False, "warmup-failed"))
        self.assertEqual((row["started_requests"], row["not_run_requests"]), (1, 8))
        self.assertEqual(row["requests_with_unknown_usage"], 1)
        self.assertEqual(row["samples"], [])
        self.assertIsNone(row["measured"])
        self.assertEqual(row["warmup"]["status"], "timeout")
        self.assertEqual(row["warmup"]["output_events"], 371)
        self.assertGreaterEqual(row["warmup"]["elapsed_seconds"], 60)
        for field in ("completion_tokens", "prompt_tokens", "cached_prompt_tokens", "ttft_seconds"):
            self.assertIsNone(row["warmup"][field])
        self.assertEqual((row["known_prompt_tokens"], row["known_output_tokens"]), (0, 0))
        self.assertTrue(self.stream["terminal_identity_verified"])

    def test_selected_identity_parity_cleanup_and_limits(self):
        experiment = self.report["experiment"]
        model = self.contract["model_profile"]
        self.assertEqual(model["served_model_name"], self.model)
        self.assertEqual(model["hf_revision"], "c19ca5ca4eaeb79d29fdf08aea8327da95b1116a")
        for parent in (experiment, self.report["provenance"]):
            self.assertEqual(parent["model_profile"], model)
        self.assertEqual(self.contract["gpu_security"], "nonconfidential")
        self.assertNotIn("execution_profile", self.contract)
        self.assertEqual((experiment["startup"]["outcome"], experiment["startup"]["wait_ms"]),
                         ("ready", 1163493))
        parity = experiment["tokenizer_parity"]
        self.assertTrue(parity["all_cases_pass"])
        self.assertEqual((len(parity["cases"]), parity["requests_started"]), (4, 8))
        self.assertEqual([r["local"]["count"] for r in parity["cases"]], [12, 16, 275, 332])
        self.assertTrue(experiment["engine_stopped"])
        self.assertEqual(self.report["cleanup"], "confirmed")
        self.assertEqual(self.report["container_observation"],
                         {"exit_code": 0, "oom_killed": False, "status": "exited"})
        self.assertEqual(experiment["hardware"]["cc_feature"], "disabled")
        self.assertFalse(experiment["hardware"]["attestation_verified"])
        for value in (self.report, experiment):
            self.assertEqual(value["status"], "BLOCKED")
            for field in ("functional_pass", "confidential", "deployable"):
                self.assertIs(value[field], False)
        self.assertEqual((self.run["result"], self.report["result"]), ("fail", "fail"))
        self.assertFalse(self.run["hardware_validated"])

    def test_fabricated_usage_success_or_quality_rejected(self):
        for mutate in (
                lambda r: r["cohort"].update(known_output_tokens=371),
                lambda r: r["cohort"].update(completed=True, stop_reason="complete"),
                lambda r: r["cohort"].update(requests_with_unknown_usage=0),
                lambda r: r["cohort"].update(quality_accepted=True),
                lambda r: r["cohort"].update(confidential=True),
                lambda r: r["cohort"].update(model="deepseek-v4.1-flash-1017")):
            changed = copy.deepcopy(self.stream)
            mutate(changed)
            with self.assertRaises(ValueError):
                self.validate(changed)


if __name__ == "__main__":
    unittest.main()
