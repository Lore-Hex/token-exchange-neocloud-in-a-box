# SPDX-License-Identifier: Apache-2.0
"""Original first-lookup failure, not customer identity or model attribution."""

import copy
import json
import sys
import unittest

from test_selected_tau_attribution_evidence import AttributionAssertions, ROOT
from test_selected_tau_failure_evidence import historical_cpu


class ResolutionEvidenceTests(AttributionAssertions, unittest.TestCase):
    directory = ROOT / "evidence/p2/2026-10-25-selected-customer-resolution-1117"
    producer = "975738d7eb26e3d315a41857ece0d70d757db4ab"
    source_count = 39
    completed_requests = 5
    started = "2026-10-25T11:17:59Z"
    ended = "2026-10-25T11:37:54Z"
    hashes = {
        "cpu/evaluator-runtime.json": (3088, "f3b2efe42ec9ffd235715e790f2bf6a805448b3f60668c25f3e1650a99af7520"),
        "cpu/evaluator-tokenizer.json": (1935, "f16cd5c46c9cdb24fccba8be1c07222f8632f42a3c2eb541de5b8f6f38bef79b"),
        "cpu/output.log": (12314, "e397e2f87552f07bd8489131d977919847411dc7ee310378c30119290f4fe51f"),
        "cpu/run.json": (737, "5fa0a485fdc5fe72e50509145f09dc36dd06db644568b8ed5e42173395b40ddd"),
        "cpu/simulator-runtime.json": (3087, "2b87ccab80c32185d5238ef591de10f923f38c47a2c99d491abb964d9ba9a78c"),
        "cpu/simulator-tokenizer.json": (1935, "c712f03c74f4046360aaa5f36e74b15685e24329f9627ac3c3d679be896a765d"),
        "evidence/output.log": (7145, "6cc24aa561ff38b3054aff9951da7984d919f0fb69641e7e31e35c3a47ad9b02"),
        "evidence/run.json": (1049, "1084b7387a6aaf1310509ef2623d6fe652bb69b3958fa5827f96f8667fbda04c"),
        "gpu/output.log": (10575, "85c833f5b63d47e68bcb8aaaaade34641fe4b7f9cfa8cece28ef6de3a737bdd2"),
        "gpu/run.json": (1078, "423fcc40949df2cdfee7e32d0e063abfc40ed31237d4d47c1a452c59991eb2a5"),
        "scorer/output.log": (6242, "1e9fb3eb4e6705fc810a574452e0eebd1b284acaf8250a614e8413b024ea540c"),
        "scorer/run.json": (668, "010d0f27513c40a8f981a87cde0475eb42cf8c0596f65a6997d47733c5f21d3b"),
    }

    def test_exact_inventory_sources_and_failed_trial(self):
        self.assert_original_inventory_and_sources()
        self.assertEqual(self.scorer["toolkit"]["git_sha"], self.producer)
        trial = self.scorer["diagnostic"]["trial"]
        self.assertEqual(trial["schema"], "nib.tau-selected-customer-resolution-trial/v1")
        self.assertEqual((trial["status"], trial["elapsed_ms"], trial["requests"]),
                         ("protocol_error", 35821, 10))
        self.assertEqual(trial["role_requests"], {"agent": 5, "simulator": 5, "evaluator": 0})
        self.assertEqual(trial["usage"], {"prompt_tokens": 27618, "completion_tokens": 375,
                                         "total_tokens": 27993, "cost_usd": None})
        self.assertIsNone(trial["reward"])
        self.assertIsNone(trial["upstream_termination"])
        self.assertEqual(trial["customer_resolution"], {
            "schema": "nib.tau-selected-customer-resolution/v1", "tool": "email",
            "outcome": "lookup_error", "intended_customer_match": None})
        self.assertEqual(trial["selection"],
                         {"domain": "retail", "role": "candidate", "seed": 42, "task_id": "2", "trial": 0})
        self.assertEqual(trial["manifest_sha256"], self.scorer["manifest_sha256"])
        for row in (trial, self.scorer, self.controller):
            for key in ("quality_eligible", "live_admission", "confidential"):
                self.assertIs(row[key], False)
        diagnostic = self.scorer["diagnostic"]
        self.assertIs(diagnostic["quality_accepted"], False)
        self.assertIs(diagnostic["usage_known"], True)
        self.assertEqual(diagnostic["reserved_tokens"], 27993)
        self.assertEqual((diagnostic["scorer_exit_code"], diagnostic["attach_cleanup"]), (0, "reaped"))

    def test_original_17b_process_binding_and_failure_cannot_be_promoted(self):
        trial = self.scorer["diagnostic"]["trial"]
        runtime = self.rows["cpu/evaluator-runtime.json"]
        with historical_cpu(self.producer, self.cpu):
            support = sys.modules["tau_support_probe"]
            policy = sys.modules["tau_selected_policy"]
            self.assertEqual(trial["simulator_binding"], support.runtime_binding(runtime))
            self.assertEqual(trial["simulator_binding"]["runtime_sha256"], self.hashes["cpu/evaluator-runtime.json"][1])
            self.assertEqual(runtime["role"], "evaluator")
            self.assertEqual(runtime["model"], "qwen/qwen3-1.7b")
            self.assertEqual(self.rows["cpu/simulator-runtime.json"]["model"], "qwen/qwen3-0.6b")
            observed = trial["customer_resolution"]
            self.assertIs(policy.resolution_result(observed), observed)
            self.assertRaises(ValueError, policy.resolution_result, observed, completed=True)
            for fields in ({"intended_customer_match": False}, {"intended_customer_match": True},
                           {"origin": {"line": 330}}, {"prior_user_text_contains_argument": True},
                           {"content": "PRIVATE_MODEL_TEXT_CANARY"}):
                changed = copy.deepcopy(observed)
                changed.update(fields)
                self.assertRaises(ValueError, policy.resolution_result, changed)
        tokenizer = self.rows["cpu/evaluator-tokenizer.json"]
        self.assertEqual(tokenizer["runtime_receipt_sha256"], self.hashes["cpu/evaluator-runtime.json"][1])
        self.assertEqual(tokenizer["tokenizer_sha256"], trial["simulator_binding"]["tokenizer_sha256"])

    def test_startup_and_cleanup_are_not_quality_or_deployment(self):
        experiment = self.gpu["experiment"]
        self.assertEqual((experiment["startup"]["outcome"], experiment["startup"]["wait_ms"]), ("ready", 847167))
        self.assertIs(experiment["tokenizer_parity"]["all_cases_pass"], True)
        self.assertEqual(len(experiment["tokenizer_parity"]["cases"]), 4)
        self.assertEqual((experiment["hardware"]["family"], experiment["hardware"]["selected_gpus"],
                          experiment["hardware"]["cc_feature"]), ("B300", 8, "disabled"))
        self.assertIs(experiment["hardware"]["attestation_verified"], False)
        self.assertEqual(self.gpu["status"], "BLOCKED")
        for row in (self.gpu, experiment):
            self.assertIs(row["deployable"], False)
            self.assertIs(row["functional_pass"], False)
        for value in self.rows.values():
            encoded = json.dumps(value)
            self.assertNotIn("PRIVATE_MODEL_TEXT_CANARY", encoded)
        # The private operator wrapper is not a committed producer receipt.
        self.assertNotIn("exit_code", self.controller)
        self.assertNotIn("launch.json", self.hashes)


if __name__ == "__main__":
    unittest.main()
