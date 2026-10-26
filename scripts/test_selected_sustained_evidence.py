# SPDX-License-Identifier: Apache-2.0
"""Real 32-wave evidence: requests completed, duration qualification did not."""

import copy
import hashlib
import json
import re
import tomllib
import types
import unittest

from test_evidence_history import git
from test_stream_failure_evidence import ROOT, SourceBoundStreamEvidence

DIRECTORY = ROOT / "evidence/p2/2026-10-26-selected-sustained-c8"
PRODUCER = "dd4705c3483bb9f333642c5a904c9d7b74396e09"
HASHES = {
    "run.json": "8634e331a58e1022ca65035158567b5c8c826a84021de4f9011c4154ddd6c342",
    "output.log": "0bfb1213bea86f7343dbcddd0099ba04709ce80ed6af4cf484b6520a862bc453",
    "input-contract.json": "ca91d7625920c83316a5c58a2351e3a5e1e1bfbf01f3f70ae170f32728b4bf3c",
    "receipt.json": "d171d4db5c7072b89ec512ebbc42b9a272bc36fab300a52c32319e09156a0490",
}


def document(name):
    return json.loads((DIRECTORY / name).read_bytes())


class SelectedSustainedEvidenceTests(SourceBoundStreamEvidence, unittest.TestCase):
    directory, hashes, producer, file_count = DIRECTORY, HASHES, PRODUCER, 115

    @classmethod
    def setUpClass(cls):
        cls.contract = document("input-contract.json")
        raw = git(ROOT, "show", PRODUCER + ":scripts/stream_lab.py")
        if hashlib.sha256(raw).hexdigest() != cls.contract["sources"]["stream-lab.py"]:
            raise AssertionError("historical producer differs")
        cls.binding = types.ModuleType("sustained_evidence_binding")
        exec(compile(raw, "original_stream_lab", "exec", dont_inherit=True), cls.binding.__dict__)
        sources = {name: git(ROOT, "show", PRODUCER + ":scripts/" + name.removeprefix("stream/"))
                   for name in cls.contract["sources"] if name.startswith("stream/")}
        cls.cohort = cls.binding.load(sources, cls.contract["sources"])

    def validate(self, stream):
        return self.binding.validate(stream, self.contract, HASHES["input-contract.json"], self.cohort,
                                     model=self.contract["model_profile"]["served_model_name"])

    def test_scan_exceptions_are_exact_public_values_in_original_files_only(self):
        rules = tomllib.loads((ROOT / ".gitleaks.toml").read_text())["rules"]
        entries = [entry for rule in rules if rule["id"] == "generic-api-key"
                   for entry in rule["allowlists"] if "2026-10-26-selected-sustained-c8" in str(entry["paths"])]
        self.assertEqual(len(entries), 2)
        output = document("output.log")["experiment"]
        values = [[key + '\": \"' + self.contract["sources"][key] + '\"'
                   for key in ("tokenizer-adapter.py", "tokenizer-parity.py")],
                  ['token_ids_sha256\": \"' + row["token_ids_sha256"] + '\"'
                   for row in output["stream_cohort"]["cohort"]["workload"]["cases"]]
                  + ['tokens_sha256\": \"' + row["local"]["tokens_sha256"] + '\"'
                     for row in output["tokenizer_parity"]["cases"]]]
        values[0].append('engine_tokenizer_parity.py\": \"' + self.contract["sources"]["stream/engine_tokenizer_parity.py"] + '\"')
        for entry, name, matches in zip(entries, ("input-contract.json", "output.log"), values):
            self.assertEqual((entry["condition"], entry["regexTarget"]), ("AND", "match"))
            self.assertEqual(len(entry["paths"]), 1)
            self.assertRegex(str(DIRECTORY / name), entry["paths"][0])
            self.assertIsNone(re.search(entry["paths"][0], str(ROOT / name)))
            self.assertIsNone(re.search(entry["paths"][0], str(DIRECTORY / (name + ".other"))))
            for match in matches:
                self.assertTrue(any(re.fullmatch(regex, match) for regex in entry["regexes"]))
                changed = match[:-2] + ("0" if match[-2] != "0" else "1") + match[-1]
                self.assertFalse(any(re.fullmatch(regex, changed) for regex in entry["regexes"]))

    def test_original_validator_retains_insufficient_duration(self):
        report = document("output.log")
        stream = report["experiment"]["stream_cohort"]
        self.assertFalse(self.validate(stream))
        row, measured = stream["cohort"], stream["cohort"]["measured"]
        self.assertEqual((row["stop_reason"], row["completed"], row["minimum_measured_seconds"]),
                         ("insufficient-duration", False, 120))
        self.assertEqual(row["reserved_waves"], len(row["waves"]))
        self.assertEqual((row["reserved_requests"], row["started_requests"], row["not_run_requests"]), (257, 257, 0))
        self.assertEqual(row["reserved_total_tokens"], 657920)
        self.assertEqual((row["known_prompt_tokens"], row["known_output_tokens"]), (526336, 102800))
        self.assertEqual((measured["completed"], measured["failed"], measured["requests_with_unknown_usage"]), (256, 0, 0))
        self.assertEqual(measured["completed_output_tokens"], 102400)
        self.assertAlmostEqual(measured["wall_seconds"], 114.27935880600126)
        self.assertAlmostEqual(measured["wall_seconds"], row["elapsed_seconds"] - row["measurement_start_seconds"])
        rate = measured["completed_output_tokens"] / measured["wall_seconds"]
        self.assertEqual(round(rate, 2), 896.05)
        self.assertAlmostEqual(rate / 8, measured["completed_output_tokens_per_second_per_gpu"])
        self.assertEqual(round(measured["ttft_seconds"]["p50"] * 1000, 2), 205.68)
        self.assertEqual(round(measured["ttft_seconds"]["p95"] * 1000, 2), 1738.12)
        for wave in row["waves"]:
            self.assertEqual(wave["cohort"]["peak_in_flight"], 8)
            self.assertTrue(wave["cohort"]["completed"] and wave["cohort"]["collectors_reaped"])
            for sample in wave["cohort"]["samples"]:
                self.assertEqual((sample["status"], sample["prompt_tokens"], sample["completion_tokens"]),
                                 ("completed", 2048, 400))
                self.assertIsNone(sample["cached_prompt_tokens"])
        self.assertEqual((row["cache_state"], row["rank_identity"]), ("uncontrolled-repeated-fixture", "unknown"))
        self.assertTrue(row["collectors_reaped"] and stream["terminal_identity_verified"])
        for field in ("quality_accepted", "sweep_accepted", "confidential", "pricing_measured", "saturation_demonstrated"):
            self.assertIs(row[field], False)

    def test_cleanup_and_failure_are_not_confidential_or_deployment_qualification(self):
        run, report = document("run.json"), document("output.log")
        self.assertEqual((run["started_utc"], run["ended_utc"]), ("2026-10-26T02:17:15Z", "2026-10-26T02:39:09Z"))
        self.assertEqual((run["result"], run["hardware_validated"]), ("fail", False))
        experiment = report["experiment"]
        for item in (report, experiment):
            self.assertEqual(item["status"], "BLOCKED")
            for key in ("functional_pass", "confidential", "deployable"):
                self.assertIs(item[key], False)
        self.assertEqual(report["cleanup"], "confirmed")
        self.assertTrue(experiment["engine_stopped"])
        self.assertEqual(report["container_observation"], {"status": "exited", "exit_code": 0, "oom_killed": False})
        self.assertEqual((experiment["startup"]["outcome"], experiment["startup"]["wait_ms"]), ("ready", 888152))
        self.assertEqual(experiment["hardware"]["cc_feature"], "disabled")
        self.assertFalse(experiment["hardware"]["attestation_verified"])

    def test_fabricated_success_rate_usage_cache_and_cleanup_are_rejected(self):
        stream = document("output.log")["experiment"]["stream_cohort"]
        mutations = (
            lambda r: r["cohort"].update(completed=True, stop_reason="complete"),
            lambda r: r["cohort"].update(minimum_measured_seconds=110),
            lambda r: r["cohort"]["measured"].update(wall_seconds=120.0),
            lambda r: r["cohort"]["measured"].update(completed_output_tokens=131072),
            lambda r: r["cohort"].update(known_output_tokens=131584),
            lambda r: r["cohort"].update(cache_state="warm", rank_identity="verified"),
            lambda r: r["cohort"].update(collectors_reaped=False),
            lambda r: r["cohort"].update(saturation_demonstrated=True),
        )
        for mutate in mutations:
            changed = copy.deepcopy(stream)
            mutate(changed)
            with self.assertRaises(ValueError):
                self.validate(changed)


if __name__ == "__main__":
    unittest.main()
