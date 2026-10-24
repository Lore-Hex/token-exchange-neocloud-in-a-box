# SPDX-License-Identifier: Apache-2.0
"""Original selected-checkpoint measurements; no tau or confidential upgrade."""

import copy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import types
import unittest

from test_evidence_history import git

ROOT = Path(__file__).resolve().parents[1]
RUNS = {
    "selected-tau8k-parity": {
        "producer": "68ab473fda5b29bf85be07099494702b63aeba69", "profile": "tau8k-eager", "file_count": 100,
        "window": ("2026-10-24T12:55:30Z", "2026-10-24T13:14:34Z"),
        "hashes": {
            "run.json": "a5ccda1a7f92c21d09701d6fb4fc002b2185428c88a2c34fed19ac479a32a21c",
            "output.log": "7a78fda2e68e1ff6e3b3ba723071c31beaf62a2eba57b4f7071a2e411da31c6f",
            "input-contract.json": "d2586ab41b05244549e74641bd5ac312eb4b4c697e980c6aed2bc5a4d359cc3e",
            "receipt.json": "a08e63fe445c592c1b53de584cafad2bb55370028f0411f7c0d473e2681dbf74",
        },
    },
    "selected-graphs-stream": {
        "producer": "5b4e2f54f2b9c14ff46fb9c1efcfbcdbf37b49f1", "profile": "batch8-graphs", "file_count": 112,
        "window": ("2026-10-24T13:18:07Z", "2026-10-24T13:38:44Z"),
        "hashes": {
            "run.json": "b1a7c57d04c8ddcd8010af721174f62373eb6bab69edf89f5ad50d6af4afaccf",
            "output.log": "de50886162172a0cb2bef5abb675317f5bacd15afbfd07996a619492d46b5f22",
            "input-contract.json": "758efdb8468fa50bbc864b3efa4957e1f45bd2a30128e6998ad60ba62ad17eee",
            "receipt.json": "4627505f3504986fc021d484f727a4bf3a6e902eb071d1a1154f9415498fbb99",
        },
    },
    "selected-dspark-startup-failure": {
        "producer": "5b4e2f54f2b9c14ff46fb9c1efcfbcdbf37b49f1", "profile": "batch8-dspark", "file_count": 97,
        "window": ("2026-10-24T13:46:23Z", "2026-10-24T13:56:24Z"),
        "hashes": {
            "run.json": "55ec37b53b028e0446971fcb2bf326263f97d775e30520cc514721cc971a04d6",
            "output.log": "dbd8663345178b07d2d34579fc168aa0823805f1d06e2deb9b2392cb5ce78c08",
            "input-contract.json": "09a0cccb3714f24375eb908683807e794ccc790bff76ed8a0500272a66d5813c",
            "receipt.json": "9bdba26312e6dcc9333940a3b4aed2eb31626ed675ccab63e84a5b6c11d2d915",
        },
    },
    "selected-dspark-cause-diagnostics": {
        "producer": "e627eac092f42fdeb172aab747f71bd5d7d61660", "profile": "batch8-dspark", "file_count": 97,
        "window": ("2026-10-24T16:19:43Z", "2026-10-24T16:29:42Z"),
        "hashes": {
            "run.json": "a1c949d3938b987f9b28b27a35b3a1da028f87c15e31873ac06d9253ac60bd80",
            "output.log": "0a1bfcfe544201efbd50188260ff3325382ac88f09594d4b478164bd93bb086b",
            "input-contract.json": "1c5ecd8aa582291b76c326a56ee2d4c9aaa5517b2b85e25d655c78968a461b1b",
            "receipt.json": "10595f028b60118f9149e164c0870f0b92316197d94bc74c45dfaaf1cdbbf327",
        },
    },
}
ALIASES = {
    "correctness.py": "engine_local_correctness.py", "exception.py": "engine_exception.py",
    "probe.py": "engine_lab_probe.py", "model-profile.py": "deepseek_model_profile.py",
    "tuning.py": "deepseek_tuning.py", "processes.py": "e2e/snapshot.py",
    "tokenizer-adapter.py": "engine_lab_bootstrap.py", "tokenizer-parity.py": "engine_tokenizer_parity.py",
    "runtime-observation.py": "engine_runtime_observation.py", "stream-lab.py": "stream_lab.py",
}


def documents(key):
    directory = ROOT / "evidence/p2" / ("2026-10-24-" + key)
    values = []
    for name, expected in RUNS[key]["hashes"].items():
        raw = (directory / name).read_bytes()
        if not 0 < len(raw) <= 65536 or hashlib.sha256(raw).hexdigest() != expected:
            raise AssertionError("original evidence bytes changed")
        values.append(json.loads(raw))
    return values


@lru_cache(maxsize=256)
def source(producer, path):
    return git(ROOT, "show", producer + ":" + path)


class SelectedOperationalEvidenceTests(unittest.TestCase):
    def module(self, key, alias):
        contract, receipt = documents(key)[2:]
        path = "scripts/" + ALIASES[alias]
        raw = source(RUNS[key]["producer"], path)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), contract["sources"][alias])
        self.assertEqual(receipt["files"]["payload/" + alias],
                         {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        module = types.ModuleType("original_selected_evidence")
        module.__file__ = str(ROOT / path)
        exec(compile(raw, module.__file__, "exec", dont_inherit=True), module.__dict__)
        return module

    def stream(self):
        key = "selected-graphs-stream"
        contract = documents(key)[2]
        binding = self.module(key, "stream-lab.py")
        sources = {"stream/" + name + ".py": source(RUNS[key]["producer"], "scripts/" + name + ".py")
                   for name in binding.NAMES}
        return binding, binding.load(sources, contract["sources"])

    def test_original_hashes_producer_and_source_receipt_joins(self):
        for key, spec in RUNS.items():
            with self.subTest(run=key):
                run, report, contract, receipt = documents(key)
                for row in (run, report["provenance"], contract, receipt, report["experiment"]["application"]):
                    self.assertEqual(row["git_sha"], spec["producer"])
                self.assertEqual((run["started_utc"], run["ended_utc"]), spec["window"])
                self.assertEqual(report["provenance"]["receipt_sha256"], spec["hashes"]["receipt.json"])
                raw_contract = (ROOT / "evidence/p2" / ("2026-10-24-" + key) / "input-contract.json").read_bytes()
                self.assertEqual(receipt["files"]["payload/lab.json"],
                                 {"bytes": len(raw_contract), "sha256": spec["hashes"]["input-contract.json"]})
                for field in ("source_bundle_sha256", "manifest_sha256", "pack_revision"):
                    self.assertEqual(report["provenance"][field], contract[field])
                for field in ("source_bundle_sha256", "payload_sha256"):
                    self.assertEqual(report["provenance"][field], receipt[field])
                self.assertEqual(report["provenance"]["image"], contract["base_image"])
                self.assertEqual(report["provenance"]["config_sha256"], contract["official_config"]["sha256"])
                self.assertEqual(len(receipt["files"]), spec["file_count"])
                for name, sha in contract["sources"].items():
                    self.assertEqual(receipt["files"]["payload/" + name]["sha256"], sha)
                sources = {"record-deepseek-lab.py": "host/record-deepseek-lab.py"}
                sources.update({"payload/" + alias: "scripts/" + path for alias, path in ALIASES.items()
                                if alias in contract["sources"]})
                sources.update({name: name.removeprefix("support/") for name in receipt["files"]
                                if name.startswith("support/")})
                sources.update({"payload/" + name: "scripts/" + name.removeprefix("stream/")
                                for name in contract["sources"] if name.startswith("stream/")})
                for name in ("files.sha256", "pack.yaml"):
                    sources["payload/pack/" + name] = contract["model_profile"]["pack"] + "/" + name
                for name, path in sources.items():
                    raw = source(spec["producer"], path)
                    self.assertEqual(receipt["files"][name], {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
                prefix = b'import sys\nif not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):\n    sys.exit("Isolated lab required.")\n'
                launch = prefix + b"\n".join(source(spec["producer"], "scripts/" + name) for name in (
                    "engine_python_bootstrap.py", "engine_exception.py", "engine_lab_bootstrap.py", "engine_lab.py"))
                self.assertEqual(receipt["files"]["payload/launch.py"],
                                 {"bytes": len(launch), "sha256": hashlib.sha256(launch).hexdigest()})

    def test_selected_profiles_cleanup_and_nonqualification(self):
        for key, spec in RUNS.items():
            with self.subTest(run=key):
                run, report, contract, receipt = documents(key)
                experiment = report["experiment"]
                identity = self.module(key, "model-profile.py").select("uncensored")
                for row in (contract, report["provenance"], experiment):
                    self.assertEqual(row["model_profile"], identity)
                self.assertEqual(self.module(key, "tuning.py").enabled(contract), spec["profile"])
                self.assertEqual(report["provenance"]["execution_profile"], spec["profile"])
                args = contract["arguments"]
                for flag, value in (("--tensor-parallel-size", "4"), ("--data-parallel-size", "2"),
                                    ("--max-model-len", "8192" if key == "selected-tau8k-parity" else "4096")):
                    self.assertEqual(args.count(flag), 1)
                    self.assertEqual(args[args.index(flag) + 1], value)
                self.assertEqual((contract["gpu_family"], contract["gpu_security"]), ("B300", "nonconfidential"))
                self.module(key, "probe.py").validate_nonconfidential_report(experiment["hardware"], family="B300", expected=8)
                self.assertEqual(experiment["hardware"]["cc_feature"], "disabled")
                self.assertIs(experiment["hardware"]["attestation_verified"], False)
                self.assertEqual((run["result"], report["result"]), ("fail", "fail"))
                self.assertEqual(run["execution"], "hardware")
                self.assertIs(run["hardware_validated"], False)
                for row in (report, experiment, contract, receipt):
                    self.assertEqual(row["status"], "BLOCKED")
                    self.assertIs(row["deployable"], False)
                    self.assertIs(row["confidential"], False)
                self.assertEqual(report["cleanup"], "confirmed")
                self.assertIs(experiment["engine_stopped"], True)
                self.assertEqual(report["container_observation"], {"status": "exited", "exit_code": 0, "oom_killed": False})

    def test_parity_and_runtime_identity_are_validated_by_original_sources(self):
        for key, context in (("selected-tau8k-parity", 8192), ("selected-graphs-stream", 4096)):
            with self.subTest(run=key):
                run, report, contract, _ = documents(key)
                experiment = report["experiment"]
                parity, correctness = self.module(key, "tokenizer-parity.py"), self.module(key, "correctness.py")
                options = {"max_model_len": context} if key == "selected-tau8k-parity" else {}
                row = parity.validate(experiment["tokenizer_parity"], correctness, **options)
                self.assertTrue(row["all_cases_pass"])
                self.assertEqual((len(row["cases"]), row["requests_started"]), (4, 8))
                self.assertEqual([case["local"]["count"] for case in row["cases"]], [12, 16, 275, 332])
                self.assertFalse(row["tau_acceptance"] or row["quality_accepted"] or row["confidential"])
                self.assertTrue(report["functional_pass"] and experiment["functional_pass"])
                observer = self.module(key, "runtime-observation.py")
                observed = experiment["runtime_observation"]
                observer.validate(observed, contract, RUNS[key]["hashes"]["input-contract.json"], complete=True)
                self.assertFalse(observed["tau_admission"] or observed["quality_eligible"])
                for sample in observed["samples"]:
                    self.assertEqual(sample["cgroup"]["memory.max"], 512 << 30)
                    self.assertEqual(sample["cgroup"]["memory.swap.max"], 0)
                for mutate in (lambda r: r.update(tau_acceptance=True), lambda r: r.update(requests_started=7),
                               lambda r: r["cases"][0]["usage"].update(prompt_tokens=13)):
                    changed = copy.deepcopy(row)
                    mutate(changed)
                    with self.assertRaises(ValueError):
                        parity.validate(changed, correctness, **options)

    def test_graph_stream_metrics_exclude_warmup_and_use_actual_usage(self):
        key = "selected-graphs-stream"
        _, report, contract, _ = documents(key)
        binding, cohort = self.stream()
        stream = report["experiment"]["stream_cohort"]
        self.assertTrue(binding.validate(stream, contract, RUNS[key]["hashes"]["input-contract.json"], cohort,
                                         model=contract["model_profile"]["served_model_name"]))
        row, measured = stream["cohort"], stream["cohort"]["measured"]
        self.assertEqual((row["concurrency"], row["started_requests"], len(row["samples"])), (1, 9, 8))
        self.assertEqual((row["known_prompt_tokens"], row["known_output_tokens"], row["requests_with_unknown_usage"]),
                         (18432, 3600, 0))
        self.assertEqual((measured["completed"], measured["failed"], measured["not_run"]), (8, 0, 0))
        self.assertEqual(measured["completed_output_tokens"], 3200)
        self.assertAlmostEqual(measured["wall_seconds"], 22.320929104000243)
        rate = measured["completed_output_tokens"] / measured["wall_seconds"]
        self.assertAlmostEqual(rate, 143.3632079153243)
        self.assertEqual(round(rate, 2), 143.36)
        self.assertAlmostEqual(measured["completed_output_tokens_per_second_per_gpu"], rate / 8)
        self.assertEqual(round(1000 * measured["ttft_seconds"]["p50"], 2), 98.61)
        self.assertEqual(round(1000 * measured["ttft_seconds"]["p95"], 2), 117.70)
        for sample in [row["warmup"], *row["samples"]]:
            self.assertEqual((sample["status"], sample["prompt_tokens"], sample["completion_tokens"]), ("completed", 2048, 400))
        for flag in ("quality_accepted", "sweep_accepted", "confidential", "pricing_measured"):
            self.assertIs(row[flag], False)
        for mutate in (lambda r: r["cohort"].update(quality_accepted=True),
                       lambda r: r["cohort"].update(known_output_tokens=4608),
                       lambda r: r["cohort"]["measured"].update(completed_output_tokens=3600)):
            changed = copy.deepcopy(stream)
            mutate(changed)
            with self.assertRaises(ValueError):
                binding.validate(changed, contract, RUNS[key]["hashes"]["input-contract.json"], cohort,
                                 model=contract["model_profile"]["served_model_name"])

    def test_dspark_failures_preserve_legacy_and_cause_chain_diagnostics(self):
        for key, wait in (("selected-dspark-startup-failure", 331044), ("selected-dspark-cause-diagnostics", 323065)):
            with self.subTest(run=key):
                report = documents(key)[1]
                experiment, errors = report["experiment"], self.module(key, "exception.py")
                errors.require = self.module(key, "tuning.py").require
                startup = experiment["startup"]
                self.assertEqual((experiment["stage"], startup["outcome"], startup["exit_code"], startup["wait_ms"]),
                                 ("startup", "child-exited", 1, wait))
                self.assertFalse(report["functional_pass"] or experiment["functional_pass"])
                self.assertIsNone(experiment["completion"])
                self.assertIsNone(experiment["correctness"])
                errors.validate_exception(startup["exception_diagnostic"])
                row = startup["worker_exception_diagnostic"]
                if key == "selected-dspark-startup-failure":
                    errors.validate_exception(row)
                    self.assertEqual(row["type"], "other")
                    self.assertTrue(row["truncated"])
                else:
                    errors.validate_worker_exception(row)
                    self.assertEqual(row["schema"], "nib.engine-worker-exception/v2")
                    self.assertFalse(row["truncated"])
                    self.assertEqual(len(row["exceptions"]), 2)
                    accelerator, context = row["exceptions"]
                    self.assertEqual((accelerator["type"], accelerator["cause"], accelerator["context"]),
                                     ("accelerator", None, 1))
                    self.assertEqual((context["type"], context["cause"], context["context"]), ("runtime", None, None))
                    self.assertEqual(context["frames"][-1], {
                        "source_sha256": "dd146642fc7d7034f32feecf35ff4d7d9460f59a8a57444c0dd6f367fe8f15fb", "line": 955})
                    for node in row["exceptions"]:
                        self.assertTrue(node["truncated"])
                        self.assertFalse(node["source_unavailable"] or node["observer_failed"])
                        self.assertEqual(len(node["frames"]), 8)
                    self.assertEqual(accelerator["frames"][-1], {
                        "source_sha256": "5258e4552792fc48a9058766d3f7892d1a508bd1b0dbd567ca3af140e270dccc", "line": 270})
                    for mutate in (lambda r: r["exceptions"][0].update(context=99),
                                   lambda r: r["exceptions"][0].update(message="synthetic-disallowed")):
                        changed = copy.deepcopy(row)
                        mutate(changed)
                        with self.assertRaises(ValueError):
                            errors.validate_worker_exception(changed)


if __name__ == "__main__":
    unittest.main()
