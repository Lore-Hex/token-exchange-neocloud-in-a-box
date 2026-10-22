# SPDX-License-Identifier: Apache-2.0
"""Retain actual owned-runtime observations without claiming tau admission."""

import hashlib
import json
from pathlib import Path
import subprocess
import unittest

import engine_local_correctness as correctness
import engine_runtime_observation as observation
import engine_tokenizer_parity as parity

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p2/2026-10-22-tokenizer-parity-runtime-pass"
PRODUCER = "2b39b366c801f9cb970cc164c97baded87630c6f"
CONTRACT_SHA = "69a9c4b0f32604bac6d7361fad2e621f8c534cba1b5c2199f881ca9e04380d13"


def original(name):
    return json.loads((EVIDENCE / name).read_bytes())


class RuntimeObservationEvidenceTests(unittest.TestCase):
    def test_original_bytes_and_producer_binding(self):
        hashes = {
            "run.json": "5573454eabe8f3d3f489e602eadc4249bffdb33d5d298e5501c946ccf9fb3d44",
            "output.log": "62188f7b6b99776b93884b1751858916ecb2d7e6181738566348c56096171b61",
            "input-contract.json": CONTRACT_SHA,
        }
        for name, expected in hashes.items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest(), expected)
        envelope, report, contract = (original(name) for name in hashes)
        self.assertEqual(envelope["git_sha"], PRODUCER)
        self.assertEqual(contract["git_sha"], PRODUCER)
        self.assertEqual(report["provenance"]["git_sha"], PRODUCER)
        self.assertEqual(envelope["started_utc"], "2026-10-22T17:54:48Z")
        self.assertEqual(envelope["ended_utc"], "2026-10-22T18:21:17Z")
        self.assertEqual(report["provenance"]["receipt_sha256"],
                         "b04fced3ccc7e0be7f0c5c3965f7fc31e11b28ae29e401fd23d55d6319f89f43")
        for name in ("pack_revision", "source_bundle_sha256", "manifest_sha256"):
            self.assertEqual(report["provenance"][name], contract[name])
        sources = {
            "runtime-observation.py": "scripts/engine_runtime_observation.py",
            "correctness.py": "scripts/engine_local_correctness.py",
            "tokenizer-parity.py": "scripts/engine_tokenizer_parity.py",
            "tokenizer-adapter.py": "scripts/engine_lab_bootstrap.py",
        }
        for name, source in sources.items():
            raw = subprocess.check_output(["git", "show", f"{PRODUCER}:{source}"], cwd=ROOT, timeout=10)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), contract["sources"][name])

    def test_two_stable_runtime_samples_and_original_limits(self):
        contract = original("input-contract.json")
        runtime = original("output.log")["experiment"]["runtime_observation"]
        self.assertIs(observation.validate(runtime, contract, CONTRACT_SHA, complete=True), runtime)
        before, after = runtime["samples"]
        self.assertEqual([before["event"], after["event"]], list(observation.EVENTS))
        self.assertTrue(all(before[key] == after[key] for key in observation.DIGESTS))
        self.assertEqual(before["exe_sha256"], contract["interpreter"]["sha256"])
        for sample in runtime["samples"]:
            self.assertTrue(sample["stdio_null"])
            self.assertEqual(sample["cgroup"]["memory.max"], 1024 << 30)
            self.assertEqual(sample["cgroup"]["memory.swap.max"], 0)
            self.assertEqual(sample["cgroup"]["pids.max"], 1024)
        self.assertEqual(before["cgroup"]["memory.peak"], 41216716800)
        self.assertEqual(after["cgroup"]["memory.peak"], 41216843776)
        self.assertEqual([before["cgroup"]["pids.current"], after["cgroup"]["pids.current"]], [582, 583])
        self.assertFalse(runtime["tau_admission"])
        self.assertFalse(runtime["quality_eligible"])

    def test_parity_cleanup_and_no_qualification_upgrade(self):
        envelope, report = original("run.json"), original("output.log")
        experiment = report["experiment"]
        result = experiment["tokenizer_parity"]
        self.assertIs(parity.validate(result, correctness), result)
        self.assertTrue(result["all_cases_pass"])
        self.assertEqual(result["requests_started"], 8)
        self.assertEqual(result["elapsed_ms"], 11868)
        previous = json.loads((ROOT / "evidence/p2/2026-10-22-tokenizer-parity-fixed-pass/output.log").read_bytes())
        old = previous["experiment"]["tokenizer_parity"]
        self.assertEqual(old["binding_sha256"], result["binding_sha256"])
        self.assertEqual(old["cases"], result["cases"])
        self.assertEqual(report["cleanup"], "confirmed")
        self.assertEqual(report["container_observation"],
                         {"exit_code": 0, "oom_killed": False, "status": "exited"})
        self.assertTrue(experiment["engine_stopped"])
        self.assertEqual(experiment["startup"]["outcome"], "ready")
        self.assertEqual(experiment["startup"]["wait_ms"], 1219344)
        self.assertEqual(experiment["hardware"]["selected_gpus"], 8)
        self.assertFalse(experiment["hardware"]["attestation_verified"])
        for row in (report, experiment):
            self.assertTrue(row["functional_pass"])
            self.assertEqual(row["status"], "BLOCKED")
            self.assertFalse(row["deployable"])
            self.assertFalse(row["confidential"])
        self.assertEqual(envelope["result"], "fail")
        self.assertEqual(report["result"], "fail")
        self.assertFalse(envelope["hardware_validated"])
        self.assertFalse(result["tau_acceptance"])
        self.assertFalse(result["quality_accepted"])


if __name__ == "__main__":
    unittest.main()
