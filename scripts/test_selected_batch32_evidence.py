# SPDX-License-Identifier: Apache-2.0
"""Original bounded zero-cache measurements, without outer gate promotion."""

import copy
from datetime import datetime
import hashlib
import json
import re
import tomllib
import types
import unittest

from test_evidence_history import git
from test_stream_failure_evidence import ROOT, SourceBoundStreamEvidence

DIRECTORY = ROOT / "evidence/p2/2026-10-26-selected-batch32"
PRODUCER = "ac6f33384fd91d90c1ac17db1e925bbc8d9b5d35"
HASHES = {
    "run.json": "c1e3c38bd4ac6bf81d795a4dc3593e0c68b741a8f0b0a1620c9a6a67228effa2",
    "output.log": "2c7aea735829235769c64859b0541b1fe20e7b0252f86bde3eb0790bed69d11c",
    "input-contract.json": "406fa8272656b40d488250cbf2dad9ffdf13a152df110a2529321080828eb63c",
    "receipt.json": "1588c1b04fbe087e3554dbe7a4557f0f2ad58be63c0f790a18cd2e17c0fda186",
}


def document(name):
    return json.loads((DIRECTORY / name).read_bytes())


class SelectedBatch32EvidenceTests(SourceBoundStreamEvidence, unittest.TestCase):
    directory, hashes, producer, file_count = DIRECTORY, HASHES, PRODUCER, 116

    @classmethod
    def setUpClass(cls):
        cls.contract = document("input-contract.json")
        raw = git(ROOT, "show", PRODUCER + ":scripts/stream_lab.py")
        if hashlib.sha256(raw).hexdigest() != cls.contract["sources"]["stream-lab.py"]:
            raise AssertionError("historical producer differs")
        cls.binding = types.ModuleType("batch32_evidence_binding")
        exec(compile(raw, "original_stream_lab", "exec", dont_inherit=True), cls.binding.__dict__)
        sources = {name: git(ROOT, "show", PRODUCER + ":scripts/" + name.removeprefix("stream/"))
                   for name in cls.contract["sources"] if name.startswith("stream/")}
        cls.cohort = cls.binding.load(sources, cls.contract["sources"])

    def validate(self, stream):
        return self.binding.validate(stream, self.contract, HASHES["input-contract.json"], self.cohort,
                                     model=self.contract["model_profile"]["served_model_name"])

    def test_original_validator_and_every_actual_sample(self):
        stream = document("output.log")["experiment"]["stream_cohort"]
        self.assertTrue(self.validate(stream))
        row = stream["cohort"]
        self.assertEqual((row["stop_reason"], row["completed"], row["condition"]), ("complete", True, "isolated"))
        self.assertEqual((row["admission_seconds"], row["minimum_measured_seconds"], row["duration_limit_seconds"]),
                         (150, 120, 900))
        self.assertEqual((row["reserved_requests"], row["started_requests"], row["not_run_requests"]), (12289, 3361, 8928))
        self.assertEqual(row["reserved_total_tokens"], 31459840)
        self.assertEqual((row["known_prompt_tokens"], row["known_output_tokens"]), (6883328, 1344400))
        self.assertAlmostEqual(row["elapsed_seconds"], 463.1774088999955)
        self.assertEqual((row["warmup"]["prompt_tokens"], row["warmup"]["completion_tokens"],
                          row["warmup"]["cached_prompt_tokens"]), (2048, 400, 0))
        self.assertAlmostEqual(row["warmup"]["elapsed_seconds"], 7.231305335997604)
        counts, outputs, seconds = (608, 1088, 1664), (243200, 435200, 665600), (
            153.5000124819926, 152.20206322497688, 150.1768685830175)
        rates, p50, p95 = (1584.365, 2859.357, 4432.107), (.384, .570, .986), (.640, .855, 1.637)
        for i, plateau in enumerate(row["plateaus"]):
            measured = plateau["measured"]
            self.assertEqual(plateau["concurrency"], (16, 32, 64)[i])
            self.assertEqual((measured["completed"], measured["failed"], measured["requests_with_unknown_usage"]),
                             (counts[i], 0, 0))
            self.assertEqual((plateau["known_cached_prompt_tokens"], plateau["cached_usage_unknown_requests"],
                              plateau["dispatch_unknown_requests"]), (0, 0, 0))
            self.assertAlmostEqual(measured["wall_seconds"], seconds[i])
            self.assertEqual(measured["wall_seconds"], plateau["end"] - plateau["start"])
            self.assertGreaterEqual(plateau["end"], plateau["admission_end"])
            self.assertEqual(measured["completed_output_tokens"], outputs[i])
            rate = outputs[i] / measured["wall_seconds"]
            self.assertEqual(round(rate, 3), rates[i])
            self.assertAlmostEqual(rate / 8, measured["completed_output_tokens_per_second_per_gpu"])
            self.assertEqual(round(measured["ttft_seconds"]["p50"], 3), p50[i])
            self.assertEqual(round(measured["ttft_seconds"]["p95"], 3), p95[i])
            samples = [sample for wave in plateau["waves"] for sample in wave["samples"]]
            self.assertEqual(len(samples), counts[i])
            self.assertEqual(sum(sample["completion_tokens"] for sample in samples), outputs[i])
            for sample in samples:
                self.assertEqual((sample["status"], sample["prompt_tokens"], sample["completion_tokens"],
                                  sample["cached_prompt_tokens"], sample["finish_reason"]),
                                 ("completed", 2048, 400, 0, "stop"))
            for wave in plateau["waves"]:
                self.assertTrue(wave["collectors_reaped"])
                self.assertTrue(all(interval["reported"] and interval["exit_code"] == 0
                                    for interval in wave["intervals"]))
        self.assertTrue(row["collectors_reaped"] and stream["terminal_identity_verified"])

    def test_shared_capacity_is_not_rank_or_utilization_proof(self):
        row = document("output.log")["experiment"]["stream_cohort"]["cohort"]
        self.assertEqual((row["rank_identity"], row["capacity_scope"]), ("unknown", "api-shared-cache-config"))
        for key in ("graph_capture_observed", "saturation_demonstrated", "pricing_measured",
                    "quality_accepted", "confidential"):
            self.assertIs(row[key], False)
        for snapshot in [row["capacity"]] + [p["capacity_after"] for p in row["plateaus"]]:
            self.assertEqual([entry["rank"] for entry in snapshot], [0, 1])
            for entry in snapshot:
                self.assertEqual(entry["kv_capacity_tokens"], 7372889)
                self.assertAlmostEqual(entry["kv_max_concurrency"], 1800.0218579234972)
                self.assertEqual((entry["preemptions"], entry["running"], entry["waiting"], entry["kv_fraction"]), (0, 0, 0, 0))
                self.assertFalse(entry["graph_capture_observed"])
                self.assertIsNone(entry["graph_memory_bytes"])

    def test_functional_pass_and_cleanup_do_not_promote_outer_gates(self):
        run, report = document("run.json"), document("output.log")
        self.assertEqual((run["started_utc"], run["ended_utc"]), ("2026-10-26T05:06:49Z", "2026-10-26T05:34:31Z"))
        self.assertEqual((run["result"], run["hardware_validated"], report["result"]), ("fail", False, "fail"))
        self.assertEqual((report["stage"], report["experiment_step"], report["cleanup"]), ("completed", "completed", "confirmed"))
        experiment = report["experiment"]
        for item in (report, experiment):
            self.assertTrue(item["functional_pass"])
            self.assertEqual(item["status"], "BLOCKED")
            self.assertFalse(item["deployable"] or item["confidential"])
        self.assertTrue(experiment["engine_stopped"])
        self.assertEqual(report["container_observation"], {"status": "exited", "exit_code": 0, "oom_killed": False})
        self.assertEqual((experiment["startup"]["outcome"], experiment["startup"]["wait_ms"]), ("ready", 893227))
        self.assertEqual(experiment["hardware"]["cc_feature"], "disabled")
        self.assertFalse(experiment["hardware"]["attestation_verified"])
        self.assertEqual(report["provenance"]["execution_profile"], "batch32-graphs")

    def test_coupled_mix_estimates_include_utilization_and_startup_limits(self):
        run = document("run.json")
        row = document("output.log")["experiment"]["stream_cohort"]["cohort"]
        hourly, input_price, output_price = 54.07, .65, 1.45  # Operator inputs, not measured billing.
        revenue_per_output = (2048 / 400 * input_price + output_price) / 1000000
        breakeven_rate = hourly / (3600 * revenue_per_output)
        self.assertAlmostEqual(breakeven_rate, 3143.4584437933113)
        for index, p in enumerate(row["plateaus"]):
            rate = p["measured"]["completed_output_tokens"] / p["measured"]["wall_seconds"]
            output_cost = hourly * 1000000 / (3600 * rate)
            revenue = rate * 3600 * revenue_per_output
            self.assertEqual(round(output_cost, 2), (9.48, 5.25, 3.39)[index])
            self.assertEqual(round(revenue, 2), (27.25, 49.18, 76.24)[index])
            self.assertEqual(rate >= breakeven_rate, index == 2)
            self.assertEqual(round(hourly * 1000000 / (3600 * rate * .5), 8), round(output_cost * 2, 8))
        self.assertAlmostEqual(hourly / revenue, .7092469134761491)
        duration = (datetime.fromisoformat(run["ended_utc"]) - datetime.fromisoformat(run["started_utc"])).total_seconds()
        self.assertEqual(duration, 1662)
        self.assertEqual(round(hourly * duration / 3600, 2), 24.96)
        self.assertEqual(round((row["known_prompt_tokens"] * input_price + row["known_output_tokens"] * output_price)
                              / 1000000, 2), 6.42)

    def test_tampered_usage_cache_capacity_and_claims_are_rejected(self):
        stream = document("output.log")["experiment"]["stream_cohort"]
        mutations = (
            lambda r: r["cohort"].update(known_output_tokens=1344401),
            lambda r: r["cohort"].update(rank_identity="verified"),
            lambda r: r["cohort"].update(graph_capture_observed=True),
            lambda r: r["cohort"].update(saturation_demonstrated=True),
            lambda r: r["cohort"].update(collectors_reaped=False),
            lambda r: r["cohort"]["plateaus"][2]["measured"].update(wall_seconds=120.0),
            lambda r: r["cohort"]["plateaus"][2]["measured"].update(completed_output_tokens=665601),
            lambda r: r["cohort"]["plateaus"][2]["waves"][0]["samples"][0].update(cached_prompt_tokens=1),
            lambda r: r["cohort"]["plateaus"][2]["waves"][0]["samples"][0].update(cached_prompt_tokens=None),
            lambda r: r["cohort"]["plateaus"][2]["capacity_after"][0].update(preemptions=1),
            lambda r: r["cohort"]["plateaus"][2]["capacity_after"][0].update(kv_capacity_tokens=7372890),
        )
        for mutate in mutations:
            changed = copy.deepcopy(stream)
            mutate(changed)
            with self.assertRaises(ValueError):
                self.validate(changed)
        changed = copy.deepcopy(stream)
        changed["terminal_identity_verified"] = False
        self.assertFalse(self.validate(changed))

    def test_scan_exceptions_are_exact_public_values_in_original_files_only(self):
        rules = tomllib.loads((ROOT / ".gitleaks.toml").read_text())["rules"]
        entries = [entry for rule in rules if rule["id"] == "generic-api-key"
                   for entry in rule["allowlists"] if "2026-10-26-selected-batch32" in str(entry["paths"])]
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


if __name__ == "__main__":
    unittest.main()
