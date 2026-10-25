# SPDX-License-Identifier: Apache-2.0
"""The original email-lookup failure is not a successful quality evaluation."""

import unittest

from test_selected_tau_attribution_evidence import AttributionAssertions, ROOT


class OriginEvidenceTests(AttributionAssertions, unittest.TestCase):
    directory = ROOT / "evidence/p2/2026-10-25-selected-tau-email-rejection-0333"
    producer = "71d28f9fa97bab1cbfd176d1559963f80541c663"
    source_count = 37
    elapsed_ms = 26470
    prompt_tokens = 16081
    completion_tokens = 225
    started = "2026-10-25T03:33:48Z"
    ended = "2026-10-25T03:53:54Z"
    origin = {"origin": {"source_sha256":
        "34f4fe9702d36f0dd4697700fbcd63d0cf5386e6eb8e7499aaa336243bad8750", "line": 330}}
    hashes = {
        "cpu/evaluator-runtime.json": (3088, "8482f04f0aacf3ca5ff4f1b8d4e8f344dc0f23cac12016bbcb6ab17453243c01"),
        "cpu/evaluator-tokenizer.json": (1935, "eb894f321f90d54d7c91d5810650094a380fc8b7d8848f77eb52d20366e42fcb"),
        "cpu/output.log": (12211, "042f168c7095dcc4b492e3afc08ffb068df5ab6774689f6fefb94f8dd81c54f6"),
        "cpu/run.json": (737, "c79a6b3079c99cc6902181ea5f3fdf782efd49b3dcf0570c67baf0111f884338"),
        "cpu/simulator-runtime.json": (3087, "da841159ccd4d81491a79d30249d9f1d4d1cb7a33ab4e071d6e286d50f4571b0"),
        "cpu/simulator-tokenizer.json": (1935, "4585805850dcd33c1e5dea474953ac2faa26325518143d90158c49b9b075adf5"),
        "evidence/output.log": (6525, "82e8c81140c81bd74bdba5cefbc11ec9ecd377b30c597ee0eabb583df5d50e27"),
        "evidence/run.json": (1049, "1bf57179d154b0ac954f898a3a6c0cbf3617c2ad7e898f528c2dda2461e28019"),
        "gpu/output.log": (10575, "022173e9e3c79251c70a7758e3039d8588fd2cdf4f38d1d04e58453eb9837a15"),
        "gpu/run.json": (1078, "09af4f2cbcedd05fa869de1871f8ed4420b81bb9f7327bd6e94910daa17bea41"),
        "scorer/output.log": (5622, "1c00e86b3b61277e6a54ea74ca06bed642d6e21a2d965e7a18bda734dc8e5b64"),
        "scorer/run.json": (668, "3ca25d5e06b00aaf0b2ac29b3c4ad3631645a2f688c92615363907a3cdb408ac"),
    }

    def test_startup_parity_and_ordinary_scope_are_not_trial_acceptance(self):
        experiment = self.gpu["experiment"]
        self.assertEqual((experiment["startup"]["outcome"], experiment["startup"]["wait_ms"]), ("ready", 870158))
        self.assertIs(experiment["tokenizer_parity"]["all_cases_pass"], True)
        self.assertEqual(len(experiment["tokenizer_parity"]["cases"]), 4)
        hardware = experiment["hardware"]
        self.assertEqual((hardware["family"], hardware["selected_gpus"], hardware["cc_feature"]),
                         ("B300", 8, "disabled"))
        self.assertIs(hardware["attestation_verified"], False)
        for row in (self.gpu, experiment):
            self.assertIs(row["deployable"], False)
            self.assertIs(row["functional_pass"], False)


if __name__ == "__main__":
    unittest.main()
