# SPDX-License-Identifier: Apache-2.0
"""Real selected-checkpoint API evidence; no quality or confidentiality upgrade."""

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import unittest

import deepseek_model_profile as profiles
import engine_local_correctness as correctness
import engine_runtime_observation as observation
import engine_tokenizer_parity as parity
import provider_conformance_session as session
import provider_conformance_worker as worker

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p2/2026-10-24-selected-provider-conformance"
PRODUCER = "68ab473fda5b29bf85be07099494702b63aeba69"
HASHES = {
    "input-contract.json": "a5d6dd9394586dbab3c20e101b6997cf9fab5702bf57e54995cadd4266d30ac6",
    "output.log": "b11355af3939447ecd9acdbe5c09cd222a8bf23cb2606a9128d7fc1114c98fa4",
    "receipt.json": "e351ea244bbb8731b260d5f352010c45478b884fb3639cd659080d81249bf836",
    "run.json": "825018186a4809e62407c72764527e0e675ae3b9bb45adb568f00a3f9272ca86",
}


def original(name):
    return json.loads((EVIDENCE / name).read_bytes())


def ready(report):
    value = {"schema": "nib.owned-provider-ready/v1", "contract_sha256": HASHES["input-contract.json"],
             "parity_pass": True, "sample": report["experiment"]["runtime_observation"]["samples"][0]}
    return session.validate_ready(session.encoded(value), original("input-contract.json"),
                                  HASHES["input-contract.json"], observation)


class SelectedProviderEvidenceTests(unittest.TestCase):
    def test_original_bytes_checkpoint_and_source_binding(self):
        for name, sha in HASHES.items():
            self.assertEqual(hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest(), sha)
        run, report, contract, receipt = (original(name) for name in
                                        ("run.json", "output.log", "input-contract.json", "receipt.json"))
        for row in (run, report["provenance"], contract, receipt):
            self.assertEqual(row["git_sha"], PRODUCER)
        self.assertEqual(run["started_utc"], "2026-10-24T12:32:54Z")
        self.assertEqual(run["ended_utc"], "2026-10-24T12:54:20Z")
        self.assertEqual(contract["model_profile"], profiles.select("uncensored"))
        self.assertEqual(report["provenance"]["model_profile"], contract["model_profile"])
        self.assertEqual(report["provenance"]["receipt_sha256"], HASHES["receipt.json"])
        self.assertEqual(receipt["files"]["payload/lab.json"], {
            "bytes": (EVIDENCE / "input-contract.json").stat().st_size, "sha256": HASHES["input-contract.json"]})
        for key in ("source_bundle_sha256", "payload_sha256"):
            self.assertEqual(report["provenance"][key], receipt[key])
        for key in ("manifest_sha256", "pack_revision", "source_bundle_sha256"):
            self.assertEqual(report["provenance"][key], contract[key])
        self.assertEqual(len(receipt["files"]), 101)
        for name, sha in contract["sources"].items():
            self.assertEqual(receipt["files"]["payload/" + name]["sha256"], sha)
        sources = {"record-deepseek-lab.py": "host/record-deepseek-lab.py"}
        for alias, path in {
            "provider-session.py": "provider_conformance_session.py", "provider-worker.py": "provider_conformance_worker.py",
            "runtime-observation.py": "engine_runtime_observation.py", "correctness.py": "engine_local_correctness.py",
            "probe.py": "engine_lab_probe.py", "exception.py": "engine_exception.py", "model-profile.py": "deepseek_model_profile.py",
            "tokenizer-adapter.py": "engine_lab_bootstrap.py", "tokenizer-parity.py": "engine_tokenizer_parity.py",
            "processes.py": "e2e/snapshot.py",
        }.items():
            sources["payload/" + alias] = "scripts/" + path
        sources.update({name: name.removeprefix("support/") for name in receipt["files"] if name.startswith("support/")})
        for name, source in sources.items():
            raw = subprocess.check_output(["git", "show", f"{PRODUCER}:{source}"], cwd=ROOT, timeout=10)
            self.assertEqual(receipt["files"][name], {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})

    def test_checker_parity_identity_and_cleanup_pass(self):
        report, contract = original("output.log"), original("input-contract.json")
        row, experiment = report["provider_conformance"], report["experiment"]
        session.validate_join(row, ready(report), experiment, worker)
        observation.validate(experiment["runtime_observation"], contract, HASHES["input-contract.json"], complete=True)
        self.assertEqual(row["worker"]["projection"]["checks"],
                         {**dict.fromkeys(worker.MANDATORY, "pass"), "catalog.declared-v2": "skip"})
        self.assertEqual(len(worker.MANDATORY), 17)
        self.assertTrue(row["accepted"])
        result = parity.validate(experiment["tokenizer_parity"], correctness)
        self.assertTrue(result["all_cases_pass"])
        self.assertEqual((len(result["cases"]), result["requests_started"]), (4, 8))
        self.assertEqual(experiment["startup"]["wait_ms"], 835146)
        self.assertEqual((report["cleanup"], row["cleanup"]), ("confirmed", "confirmed"))
        self.assertTrue(experiment["engine_stopped"])
        self.assertEqual(report["container_observation"], {"exit_code": 0, "oom_killed": False, "status": "exited"})
        for sample in experiment["runtime_observation"]["samples"]:
            self.assertEqual(sample["cgroup"]["memory.max"], 512 << 30)
            self.assertEqual(sample["cgroup"]["memory.swap.max"], 0)
            self.assertTrue(sample["stdio_null"])

    def test_changed_checker_or_runtime_cannot_keep_acceptance(self):
        for change in ("usage", "namespace", "cleanup", "exit", "ack", "identity"):
            report = copy.deepcopy(original("output.log"))
            row, experiment = report["provider_conformance"], report["experiment"]
            if change == "usage": row["worker"]["projection"]["checks"]["chat.usage"] = "warn"
            elif change == "namespace": row["namespace_verified"] = False
            elif change == "cleanup": row["cleanup"] = "unconfirmed"
            elif change == "exit": row["worker_exit_code"] = 1
            elif change == "ack": experiment["provider_conformance"]["report_sha256"] = "ab" * 32
            else: experiment["runtime_observation"]["samples"][0]["listener_sha256"] = "ab" * 32
            with self.subTest(change=change), self.assertRaises(ValueError):
                session.validate_join(row, ready(report), experiment, worker)

    def test_ordinary_api_pass_is_not_deployment_quality_or_confidentiality(self):
        report, run = original("output.log"), original("run.json")
        for row in (report, report["experiment"]):
            self.assertTrue(row["functional_pass"])
            self.assertEqual(row["status"], "BLOCKED")
            self.assertIs(row["deployable"], False)
            self.assertIs(row["confidential"], False)
        self.assertEqual((run["result"], report["result"]), ("fail", "fail"))
        self.assertIs(run["hardware_validated"], False)
        self.assertEqual(report["experiment"]["hardware"]["cc_feature"], "disabled")
        self.assertIs(report["experiment"]["hardware"]["attestation_verified"], False)
        self.assertIs(report["experiment"]["runtime_observation"]["tau_admission"], False)
        self.assertIs(report["provider_conformance"]["worker"]["quality_accepted"], False)


if __name__ == "__main__":
    unittest.main()
