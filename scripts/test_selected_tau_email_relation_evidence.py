# SPDX-License-Identifier: Apache-2.0
"""Original substring-relation diagnostic, not identity or model attribution."""

import unittest

from test_selected_tau_attribution_evidence import AttributionAssertions, ROOT


class EmailRelationEvidenceTests(AttributionAssertions, unittest.TestCase):
    directory = ROOT / "evidence/p2/2026-10-25-selected-tau-email-relation-0429"
    producer = "32771fb4ca03cc25ab3177b70c431813cd1a2bf0"
    source_count = 37
    elapsed_ms = 25040
    prompt_tokens = 16092
    completion_tokens = 225
    started = "2026-10-25T04:29:40Z"
    ended = "2026-10-25T04:49:13Z"
    origin = {"origin": {"source_sha256":
        "34f4fe9702d36f0dd4697700fbcd63d0cf5386e6eb8e7499aaa336243bad8750", "line": 330},
        "prior_user_text_contains_argument": True}
    hashes = {
        "cpu/evaluator-runtime.json": (3088, "dca83eabb1c0ce1c3a145af553a335ce434a8a360d55907617ff8e9f09ce6188"),
        "cpu/evaluator-tokenizer.json": (1935, "5399ca695c17ed4a152ae834f258daab23b5fc92a58b67124f86ffc7cb0f0393"),
        "cpu/output.log": (12211, "721e2cfdc07eaed443f0d06f24b226786ddf965700c585ca5ea4070cd72cc92c"),
        "cpu/run.json": (737, "f1bd275fd7b99a8ac111f5d503677e21ffae28a745eaed2439c187009426021c"),
        "cpu/simulator-runtime.json": (3087, "296018a1653f8d58bb564e4e06a4f18c644a39484a3674961ffa0b784a072665"),
        "cpu/simulator-tokenizer.json": (1935, "477d1e86f6fbd5083acaf2560e78abf4b014371c71cc750d68fd1edbaf0ca09a"),
        "evidence/output.log": (6566, "f2ba013c541b0c696091b1dba82a78168146e41fec382c51d96264cb2f4bcda7"),
        "evidence/run.json": (1049, "3871a3748ef98e27d14334bf2315ea3dc7244d8bb2c9766c1485b25ec9fee76e"),
        "gpu/output.log": (10575, "6b5a870e92f9f0a8a638d3f270782a98e4c70de9060373d9b994c40c2c0da3d1"),
        "gpu/run.json": (1078, "864c0a8d12dec2f925e9fef2bf42ad18738cecddce2e83f5f1a69381bc4fd159"),
        "scorer/output.log": (5663, "9ed4357bf1b02069365e77ae78f89f9b7b4caa5bf169f723370e2dfe778df94c"),
        "scorer/run.json": (668, "85870420d565d61b7a716e7a0408a3c190fbcc4d81f913f49d60ea75d7f4dea2"),
    }

    def test_startup_does_not_promote_failed_trial_or_support_models(self):
        experiment = self.gpu["experiment"]
        self.assertEqual((experiment["startup"]["outcome"], experiment["startup"]["wait_ms"]), ("ready", 840203))
        self.assertIs(experiment["tokenizer_parity"]["all_cases_pass"], True)
        self.assertEqual(len(experiment["tokenizer_parity"]["cases"]), 4)
        hardware = experiment["hardware"]
        self.assertEqual((hardware["family"], hardware["selected_gpus"], hardware["cc_feature"]),
                         ("B300", 8, "disabled"))
        self.assertIs(hardware["attestation_verified"], False)
        for row in (self.gpu, experiment):
            self.assertIs(row["deployable"], False)
            self.assertIs(row["functional_pass"], False)
        counts = self.scorer["diagnostic"]["trial"]["upstream_termination"]["tool_error_attribution"]["counts"]
        self.assertEqual(set(counts[0]), {"requestor", "known_tool", "category", "count", "origin",
                                        "prior_user_text_contains_argument"})
        self.assertIs(counts[0]["prior_user_text_contains_argument"], True)


if __name__ == "__main__":
    unittest.main()
