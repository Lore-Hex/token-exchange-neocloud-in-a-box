# SPDX-License-Identifier: Apache-2.0
"""Selected ordinary checkpoint: real correctness, not quality or confidentiality."""

import copy
import hashlib
import json
from pathlib import Path
import types
import unittest

from test_evidence_history import git

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p2/2026-10-24-ordinary-selected-correctness"
PRODUCER = "5b4e2f54f2b9c14ff46fb9c1efcfbcdbf37b49f1"
HASHES = {
    "run.json": "1f1242ee8b206e9c7d3b1ad9f1431796ab3780f8e2bc41075b19d9ed0cccaf3a",
    "output.log": "e4e3d9f00f288258536bd2177690cb5ec57769ce79362aeb21cae8da94d4c992",
    "input-contract.json": "2006bd9a3d6b12e6c1681ebcc8fbd1638fe2894063ab82c07cb1b53c39781ff0",
    "receipt.json": "021fab9a9d54c5f15dc212f9c7dfbd9134170ae379db99063c951d9791105960",
}


def original(name):
    raw = (EVIDENCE / name).read_bytes()
    if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != HASHES[name]:
        raise AssertionError("original receipt changed")
    return json.loads(raw)


class SelectedCorrectnessEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.run, self.report, self.contract, self.receipt = (original(name) for name in HASHES)
        source = git(ROOT, "show", PRODUCER + ":scripts/engine_local_correctness.py")
        self.assertEqual(hashlib.sha256(source).hexdigest(), self.contract["sources"]["correctness.py"])
        self.validator = types.ModuleType("selected_correctness_original")
        self.validator.__file__ = str(ROOT / "scripts/engine_local_correctness.py")
        exec(compile(source, self.validator.__file__, "exec"), self.validator.__dict__)

    def test_original_artifacts_and_producer_closure(self):
        git(ROOT, "merge-base", "--is-ancestor", PRODUCER, "HEAD")
        for row in (self.run, self.report["provenance"], self.contract, self.receipt):
            self.assertEqual(row["git_sha"], PRODUCER)
        provenance = self.report["provenance"]
        self.assertEqual(provenance["receipt_sha256"], HASHES["receipt.json"])
        self.assertEqual(self.receipt["files"]["payload/lab.json"], {
            "sha256": HASHES["input-contract.json"], "bytes": (EVIDENCE / "input-contract.json").stat().st_size})
        self.assertEqual(provenance["payload_sha256"], self.receipt["payload_sha256"])
        self.assertEqual(provenance["source_bundle_sha256"], self.receipt["source_bundle_sha256"])
        self.assertEqual(provenance["image"], self.contract["base_image"])
        self.assertEqual(provenance["config_sha256"], self.contract["official_config"]["sha256"])
        for name, expected in self.contract["sources"].items():
            self.assertEqual(self.receipt["files"]["payload/" + name]["sha256"], expected)
        sources = {"record-deepseek-lab.py": "host/record-deepseek-lab.py"}
        for name, path in {"correctness.py": "engine_local_correctness.py",
                           "model-profile.py": "deepseek_model_profile.py",
                           "probe.py": "engine_lab_probe.py", "exception.py": "engine_exception.py",
                           "processes.py": "e2e/snapshot.py"}.items():
            sources["payload/" + name] = "scripts/" + path
        sources.update({name: name.removeprefix("support/") for name in self.receipt["files"]
                        if name.startswith("support/")})
        for name, source in sources.items():
            raw = git(ROOT, "show", PRODUCER + ":" + source)
            self.assertEqual(self.receipt["files"][name], {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})

    def test_selected_checkpoint_and_fixed_launch_are_not_original_or_tuned(self):
        identity = {
            "name": "uncensored", "hf_repo": "dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8",
            "hf_revision": "c19ca5ca4eaeb79d29fdf08aea8327da95b1116a",
            "manifest_sha256": "5c6891a9649702b1ac0327b945600acb40c571a069942517cf14ba661a3cfcbc",
            "pack": "models/deepseek-v4.1-flash-1017-uncensored-fp8/1",
            "served_model_name": "deepseek-v4.1-flash-1017-uncensored-fp8", "total_bytes": 510311056155}
        for row in (self.contract, self.report["provenance"], self.report["experiment"]):
            self.assertEqual(row["model_profile"], identity)
        for name in ("pack_revision", "manifest_sha256", "source_bundle_sha256"):
            self.assertEqual(self.report["provenance"][name], self.contract[name])
        args = self.contract["arguments"]
        for flag, value in (("--tensor-parallel-size", "4"), ("--data-parallel-size", "2"),
                            ("--max-model-len", "4096"), ("--max-num-seqs", "1"),
                            ("--served-model-name", identity["served_model_name"])):
            self.assertEqual(args.count(flag), 1)
            self.assertEqual(args[args.index(flag) + 1], value)
        self.assertIn("--enforce-eager", args)
        self.assertNotIn("execution_profile", self.contract)
        self.assertEqual((self.contract["gpu_family"], self.contract["gpu_security"]), ("B300", "nonconfidential"))

    def test_real_correctness_and_cleanup_keep_their_narrow_meaning(self):
        experiment = self.report["experiment"]
        row = self.validator.validate(experiment["correctness"])
        self.assertIs(row["all_cases_pass"], True)
        self.assertEqual((len(row["cases"]), row["requests_started"], row["request_phase_elapsed_ms"]), (8, 10, 34109))
        self.assertEqual([len(case["requests"]) for case in row["cases"]], [1, 1, 1, 1, 1, 1, 2, 2])
        self.assertEqual((self.run["started_utc"], self.run["ended_utc"]),
                         ("2026-10-24T09:18:03Z", "2026-10-24T09:51:02Z"))
        self.assertEqual((experiment["startup"]["outcome"], experiment["startup"]["wait_ms"]), ("ready", 1153420))
        self.assertEqual(self.report["cleanup"], "confirmed")
        self.assertIs(experiment["engine_stopped"], True)
        self.assertEqual(self.report["container_observation"], {"exit_code": 0, "oom_killed": False, "status": "exited"})
        self.assertEqual((experiment["hardware"]["selected_gpus"], experiment["hardware"]["cc_feature"]), (8, "disabled"))
        self.assertIs(experiment["hardware"]["attestation_verified"], False)
        self.assertIs(row["tau_acceptance"], False)
        for item in (self.report, experiment):
            self.assertIs(item["functional_pass"], True)
            self.assertIs(item["confidential"], False)
            self.assertIs(item["deployable"], False)
            self.assertEqual(item["status"], "BLOCKED")
        self.assertEqual((self.run["result"], self.report["result"]), ("fail", "fail"))
        self.assertIs(self.run["hardware_validated"], False)

    def test_forged_case_accounting_or_quality_is_rejected(self):
        for mutation in ("missing-case", "missing-tool-turn", "request-count", "missing-usage", "negative-usage", "quality"):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                row = copy.deepcopy(self.report["experiment"]["correctness"])
                if mutation == "missing-case":
                    row["cases"].pop()
                elif mutation == "missing-tool-turn":
                    row["cases"][-1]["requests"].pop()
                elif mutation == "request-count":
                    row["requests_started"] = 9
                elif mutation == "missing-usage":
                    row["cases"][0]["requests"][0]["usage"] = None
                elif mutation == "negative-usage":
                    row["cases"][0]["requests"][0]["usage"]["prompt_tokens"] = -1
                else:
                    row["tau_acceptance"] = True
                self.validator.validate(row)


if __name__ == "__main__":
    unittest.main()
