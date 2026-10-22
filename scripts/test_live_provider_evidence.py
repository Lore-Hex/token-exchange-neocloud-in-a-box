# SPDX-License-Identifier: Apache-2.0
"""Original real API conformance is not broader quality or qualification."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import unittest

import engine_local_correctness as correctness
import engine_runtime_observation as observation
import engine_tokenizer_parity as parity
import provider_conformance_session as session
import provider_conformance_worker as worker

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p2/2026-10-22-provider-conformance-pass"
PRODUCER = "efa6a7e8bd38794d1187f15b35aa77b6dc78912f"
HASHES = {
    "run.json": "63ea3c9ed84e6c080403811c4aefd27232a4ec6eeabd5d25c67d2ab13a2627f3",
    "output.log": "4cd4474a220a3955aa4f63547cff29ddfcb6360293ee35bbdc936fca671251b2",
    "input-contract.json": "c8e90e034a03a7b46edf2b43ac03e36068103712492a8b9763c3c3bdce69dad6",
    "receipt.json": "08b5f25d85c0fcf0b37ae14344611935a130cd2ff95193e9365fb465f5c2a278",
}


def original(name):
    return json.loads((EVIDENCE / name).read_bytes())


def ready(report):
    value = {"schema": "nib.owned-provider-ready/v1", "contract_sha256": HASHES["input-contract.json"],
             "parity_pass": True, "sample": report["experiment"]["runtime_observation"]["samples"][0]}
    return session.validate_ready(session.encoded(value), original("input-contract.json"),
                                  HASHES["input-contract.json"], observation)


class LiveProviderEvidenceTests(unittest.TestCase):
    def test_original_bytes_and_committed_source_binding(self):
        for name, expected in HASHES.items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest(), expected)
        run, report, contract, receipt = (original(name) for name in HASHES)
        for row in (run, report["provenance"], contract, receipt):
            self.assertEqual(row["git_sha"], PRODUCER)
        self.assertEqual(run["started_utc"], "2026-10-22T20:24:20Z")
        self.assertEqual(run["ended_utc"], "2026-10-22T20:51:36Z")
        self.assertEqual(report["provenance"]["receipt_sha256"], HASHES["receipt.json"])
        self.assertEqual(receipt["files"]["payload/lab.json"], {
            "sha256": HASHES["input-contract.json"], "bytes": (EVIDENCE / "input-contract.json").stat().st_size})
        self.assertEqual(report["provenance"]["payload_sha256"], receipt["payload_sha256"])
        self.assertEqual(report["provenance"]["image"], contract["base_image"])
        self.assertEqual(report["provenance"]["config_sha256"], contract["official_config"]["sha256"])
        for key in ("pack_revision", "source_bundle_sha256", "manifest_sha256"):
            self.assertEqual(report["provenance"][key], contract[key])
        self.assertEqual(receipt["source_bundle_sha256"], contract["source_bundle_sha256"])
        self.assertEqual(len(receipt["files"]), 100)
        for name, sha in contract["sources"].items():
            self.assertEqual(receipt["files"]["payload/" + name]["sha256"], sha)
        sources = {"record-deepseek-lab.py": "host/record-deepseek-lab.py"}
        for alias, path in {
            "provider-session.py": "provider_conformance_session.py",
            "provider-worker.py": "provider_conformance_worker.py",
            "runtime-observation.py": "engine_runtime_observation.py",
            "correctness.py": "engine_local_correctness.py", "probe.py": "engine_lab_probe.py",
            "exception.py": "engine_exception.py", "tokenizer-adapter.py": "engine_lab_bootstrap.py",
            "tokenizer-parity.py": "engine_tokenizer_parity.py", "processes.py": "e2e/snapshot.py",
        }.items():
            sources["payload/" + alias] = "scripts/" + path
        sources.update({name: name.removeprefix("support/") for name in receipt["files"]
                        if name.startswith("support/")})
        for name, source in sources.items():
            raw = subprocess.check_output(["git", "show", f"{PRODUCER}:{source}"], cwd=ROOT, timeout=10)
            self.assertEqual(receipt["files"][name], {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        lock = subprocess.check_output(["git", "show", f"{PRODUCER}:scripts/e2e/requirements.lock"],
                                       cwd=ROOT, timeout=10)
        self.assertEqual(hashlib.sha256(lock).hexdigest(), worker.LOCK_SHA)

    def test_real_checker_and_owned_runtime_join(self):
        report, contract = original("output.log"), original("input-contract.json")
        row = report["provider_conformance"]
        self.assertTrue(session.validate_worker(row["worker"], worker, row["worker_exit_code"]))
        session.validate_join(row, ready(report), report["experiment"], worker)
        self.assertEqual(row["worker"]["projection"]["checks"],
                         {**dict.fromkeys(worker.MANDATORY, "pass"), "catalog.declared-v2": "skip"})
        self.assertEqual(len(worker.MANDATORY), 17)
        self.assertTrue(row["accepted"])
        self.assertTrue(row["namespace_verified"])
        self.assertEqual(row["cleanup"], "confirmed")
        self.assertEqual(row["worker_exit_code"], 0)
        self.assertEqual(row["image_manifest"], "sha256:6e13e65c55e33adf203d77ee371cf8bf5d81bd4902ef07565721f46bf44917af")
        self.assertEqual(session.enabled(contract)["payload_sha256"],
                         "ba3768f58f82afd3b034998b6284470fab0ce8267b0ea8a1c94647f48deca446")

    def test_changed_check_and_join_cannot_preserve_acceptance(self):
        original_report = original("output.log")
        for kind in ("check", "cleanup", "namespace", "worker-exit", "ack", "sample"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                report = copy.deepcopy(original_report)
                row, experiment = report["provider_conformance"], report["experiment"]
                if kind == "check":
                    row["worker"]["projection"]["checks"]["chat.usage"] = "warn"
                elif kind == "cleanup":
                    row["cleanup"] = "unconfirmed"
                elif kind == "namespace":
                    row["namespace_verified"] = False
                elif kind == "worker-exit":
                    row["worker_exit_code"] = 1
                elif kind == "ack":
                    experiment["provider_conformance"]["report_sha256"] = "ab" * 32
                else:
                    experiment["runtime_observation"]["samples"][0]["listener_sha256"] = "ab" * 32
                session.validate_join(row, ready(report), experiment, worker)

    def test_runtime_parity_cleanup_and_original_limits(self):
        report, contract = original("output.log"), original("input-contract.json")
        experiment = report["experiment"]
        runtime = experiment["runtime_observation"]
        observation.validate(runtime, contract, HASHES["input-contract.json"], complete=True)
        result = parity.validate(experiment["tokenizer_parity"], correctness)
        self.assertTrue(result["all_cases_pass"])
        self.assertEqual(result["requests_started"], 8)
        self.assertEqual(len(result["cases"]), 4)
        self.assertEqual(experiment["startup"]["outcome"], "ready")
        self.assertEqual(experiment["startup"]["wait_ms"], 1208301)
        self.assertTrue(experiment["engine_stopped"])
        self.assertEqual(report["cleanup"], "confirmed")
        self.assertEqual(report["container_observation"],
                         {"exit_code": 0, "oom_killed": False, "status": "exited"})
        self.assertEqual(runtime["samples"][1]["cgroup"]["memory.peak"], 41233301504)
        for sample in runtime["samples"]:
            self.assertTrue(sample["stdio_null"])
            self.assertEqual(sample["cgroup"]["memory.max"], 1024 << 30)
            self.assertEqual(sample["cgroup"]["memory.swap.max"], 0)
            self.assertEqual(sample["cgroup"]["pids.max"], 1024)

    def test_api_pass_does_not_upgrade_qualification(self):
        run, report = original("run.json"), original("output.log")
        experiment = report["experiment"]
        for row in (report, experiment):
            self.assertTrue(row["functional_pass"])
            self.assertEqual(row["status"], "BLOCKED")
            self.assertIs(row["deployable"], False)
            self.assertIs(row["confidential"], False)
        self.assertEqual(run["result"], "fail")
        self.assertEqual(report["result"], "fail")
        self.assertIs(run["hardware_validated"], False)
        self.assertIs(experiment["hardware"]["attestation_verified"], False)
        for key in ("catalog_checked", "endpoint_identity_verified", "confidential", "quality_accepted"):
            self.assertIs(report["provider_conformance"]["worker"][key], False)
        for key in ("tau_admission", "quality_eligible"):
            self.assertIs(experiment["runtime_observation"][key], False)
        for key in ("tau_acceptance", "quality_accepted"):
            self.assertIs(experiment["tokenizer_parity"][key], False)


if __name__ == "__main__":
    unittest.main()
