# SPDX-License-Identifier: Apache-2.0
"""Original CPU-only retry; no selected target, full tau or CC qualification."""

import ast
import copy
import hashlib
import json
from pathlib import Path
import unittest

from test_evidence_history import git
from test_selected_tau_failure_evidence import historical_cpu, RUNS

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evidence/p2/2026-10-24-cpu-http-threads-pass"
PRODUCER = "9eeebe1a56e2980929b8b3f637f8d3189e5bc797"
HASHES = {
    "run.json": (720, "904359775f389dbedb4c39d658c73b7603bb49599f64338d8c996b5abbaf7e7e"),
    "output.log": (10089, "4823103a1eafb13e0d844ae79a1442254783c96f3e2099c4bd51535b49cd06e1"),
}


class CPUHTTPThreadsEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.documents = {}
        for name, (size, sha) in HASHES.items():
            raw = (DIRECTORY / name).read_bytes()
            self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), (size, sha))
            self.documents[name] = json.loads(raw)
        self.run, self.report = self.documents["run.json"], self.documents["output.log"]

    def test_original_pass_retains_cleanup_and_nonqualification(self):
        git(ROOT, "merge-base", "--is-ancestor", PRODUCER, "HEAD")
        for row in (self.run, self.report):
            self.assertEqual((row["git_sha"], row["result"]), (PRODUCER, "pass"))
        self.assertEqual((self.run["started_utc"], self.run["ended_utc"]),
                         ("2026-10-24T18:13:08Z", "2026-10-24T18:16:29Z"))
        self.assertEqual(self.run["execution"], "software")
        self.assertIs(self.run["hardware_validated"], False)
        self.assertEqual(self.run["host"]["gpus"], "none")
        self.assertEqual((self.report["schema"], self.report["stage"]), ("nib.tau-cpu-parity-run/v1", "complete"))
        self.assertEqual(self.report["cleanup"], dict.fromkeys(("container", "image", "build_completion"), "confirmed"))
        self.assertTrue(all(self.report[key] is False for key in ("quality_eligible", "live_admission", "confidential")))
        self.assertEqual(self.report["source_archive_sha256"],
                         "9b4d50d4ebccb64645f7c2cd33465152d63998f5ad77d18c92910bdb1d2916bf")

    def test_exact_source_closure_and_bounded_command(self):
        with historical_cpu(PRODUCER, self.report) as (recorder, cpu, session):
            self.assertEqual(len(self.report["sources"]), 27)
            pins = json.loads((cpu.ROOT / "scripts/e2e/pins.json").read_bytes())
            self.assertEqual(self.report["base_images"], {key: pins[key] for key in ("go_image", "python_image")})
            self.assertIs(cpu.validate(self.report["runtime"]), self.report["runtime"])
            source = (cpu.ROOT / "scripts/pilot/run.py").read_bytes()
            command = next(node for node in ast.parse(source).body
                           if isinstance(node, ast.FunctionDef) and node.name == "engine_command")
            namespace = {"__builtins__": {"str": str}}
            # Only the pure argv constructor, not server startup or HTTP.
            exec(compile(ast.Module(body=[command], type_ignores=[]), "original-pilot", "exec"), namespace)
            argv = namespace["engine_command"]("binary", "weights", "synthetic", 8000, "keyfile")
            for flag, expected in (("--threads", "4"), ("--threads-http", "4"), ("--parallel", "1"),
                                   ("--ctx-size", "4096"), ("--n-predict", "256"), ("--gpu-layers", "0"),
                                   ("--device", "none"), ("--host", "127.0.0.1")):
                self.assertEqual(argv.count(flag), 1)
                self.assertEqual(argv[argv.index(flag) + 1], expected)
            self.assertTrue({"--offline", "--log-disable", "--no-webui", "--no-slots"}.issubset(argv))

    def test_eight_actual_probes_and_same_binaries_as_failed_attempt(self):
        runtime = self.report["runtime"]
        self.assertIsNone(runtime["failure"])
        self.assertEqual([model["model"] for model in runtime["models"]], ["qwen/qwen3-0.6b", "qwen/qwen3-1.7b"])
        self.assertEqual([model["ready_ms"] for model in runtime["models"]], [467, 566])
        for model in runtime["models"]:
            self.assertIs(model["owner_socket_checked"], True)
            self.assertEqual(model["process"]["exe_sha256"], runtime["binaries"]["server"])
            self.assertEqual([case["id"] for case in model["cases"]], ["text", "system", "tools", "tool-continuation"])
            for case, count in zip(model["cases"], (20, 28, 160, 198)):
                self.assertEqual(case["result"], "pass")
                self.assertEqual(case["local"], case["server"])
                self.assertEqual(case["local"]["count"], count)
                self.assertEqual(case["usage"], {"prompt_tokens": count, "completion_tokens": 1, "total_tokens": count + 1})
        failed = (ROOT / "evidence/p2/2026-10-24-selected-tau-readiness-failure-1800/cpu/output.log").read_bytes()
        self.assertEqual((len(failed), hashlib.sha256(failed).hexdigest()), RUNS["1800"]["hashes"]["cpu/output.log"])
        self.assertEqual(runtime["binaries"], json.loads(failed)["runtime"]["progress"]["parity"]["binaries"])

    def test_original_validator_rejects_claim_usage_and_content_mutations(self):
        with historical_cpu(PRODUCER, self.report) as (_, cpu, _session):
            for mutate in (lambda row: row.update(quality_eligible=True), lambda row: row.update(confidential=True),
                           lambda row: row.update(live_admission=True), lambda row: row.update(models=row["models"][:1]),
                           lambda row: row["models"][0]["cases"][0]["usage"].update(prompt_tokens=21),
                           lambda row: row["models"][0].update(ready_ms=90001),
                           lambda row: row.update(response_body="private-canary")):
                changed = copy.deepcopy(self.report["runtime"])
                mutate(changed)
                with self.assertRaises((ValueError, cpu.llama.worker.Abort)):
                    cpu.validate(changed)


if __name__ == "__main__":
    unittest.main()
