# SPDX-License-Identifier: Apache-2.0
"""Replay preparation receipts without network, model I/O or runtime promotion."""
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import unittest

import selected_host_baseline as baseline
import selected_service_image as image
from test_evidence_history import git
from test_weight_stage_recorder import stage

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evidence/p2/2026-10-26-million-prerequisites"
SOURCE = "41058d99a2b2dee790cdd9679bdcf5dabaa325be"
STAGE_SOURCE = "cec513f7224659a6f5e59704c07398ff67dd62b1"
HASHES = {
    "weights/run.json": "06ec78736b1846b3bd5f2ab9784e1b5021d3ecb9bd5c8ff9c485d3e163a0a0ad",
    "weights/output.log": "f219994dd26013862b2025bf02983c32b818cfb02da9663de14b7120d644dabc",
    "network/summary.json": "d6b09d1a99d3545c5e887f7153f55c72d7703873cb570eb19ef14a6f60fc3b04",
    "network/observation.json": "62641632ae34c19145aebbe45a1fc7b682abb90f7c470588c2aac7993c8358b0",
    "original-failure/summary.json": "568c93565e8b3c147e968b99f41275fe6d8cc270880e1a6e73c6e517895b04d7",
    "weight-view.json": "4a17ac4a049587940e9d81fc33cbb23c09703e9891d1f1a78043283dc573815c",
    "preparation.json": "86d6f7eea1a0452b296442aedcf87d30142cba743e25b1c684a1903259803cad",
    "build.json": "67300d9b7e37937aafd9a62aa1daf9cb87759966d28c8dcb08b26190aa3bf77a",
    "inspect.json": "018ed0a4d8d7fd757334a95db2f84c2b66d4fb367c216e1396a71092c47219aa",
}


class MillionPrerequisiteEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.raw, self.rows = {}, {}
        for name, expected in HASHES.items():
            raw = (DIRECTORY / name).read_bytes()
            self.assertLess(len(raw), 256 << 10)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), expected, name)
            for marker in (b"PRIVATE KEY", b"Bearer ", b"hixtube", b"/root/", b"/dev/shm/"):
                self.assertNotIn(marker, raw)
            self.raw[name], self.rows[name] = raw, json.loads(raw)

    def test_exact_inventory_and_reachable_producers(self):
        self.assertEqual({str(p.relative_to(DIRECTORY)) for p in DIRECTORY.rglob("*") if p.is_file()},
                         {*HASHES, "README.md"})
        for revision in (SOURCE, STAGE_SOURCE):
            git(ROOT, "merge-base", "--is-ancestor", revision, "HEAD")

    def test_complete_staging_manifest_and_unchanged_validator(self):
        run, row = self.rows["weights/run.json"], self.rows["weights/output.log"]
        self.assertEqual((run["git_sha"], row["source_git_sha"]), (STAGE_SOURCE, STAGE_SOURCE))
        historical = git(ROOT, "show", STAGE_SOURCE + ":host/record-stage.py")
        current = (ROOT / "host/record-stage.py").read_bytes()
        old_pin = b'PROFILE_SHA256 = "11475b07991fb510704d64143fcd9a0084327e586699604f14f88436960b754a"'
        new_pin = b'PROFILE_SHA256 = "aba5b55574e6b1c959b4e2c017973b6ec0025d3a0081a0427a00bdc5a9f37c5f"'
        self.assertEqual(historical.count(old_pin), 1)
        self.assertEqual(current.count(new_pin), 1)
        self.assertEqual(hashlib.sha256((ROOT / "scripts/deepseek_model_profile.py").read_bytes()).hexdigest(),
                         "aba5b55574e6b1c959b4e2c017973b6ec0025d3a0081a0427a00bdc5a9f37c5f")
        # Rebinding the reviewed helper does not change the historical staging
        # validator. Every other byte, plus the original evidence, stays exact.
        self.assertEqual(historical.replace(old_pin, new_pin), current)
        self.assertEqual((run["result"], row["result"], row["stage"]), ("pass", "pass", "complete"))
        self.assertFalse(run["hardware_validated"])
        for key in ("hardware_validated", "runtime_ready", "confidential_qualified"):
            self.assertIs(row[key], False)
        stage.validate_plan(row["plan"])
        stage.validate_proof(row["proof"], row["plan"], row["body_byte_budget"])
        self.assertEqual((row["proof"]["files"], row["proof"]["verified_bytes"], row["proof"]["reused_files"]),
                         (79, 510311056155, 0))
        self.assertEqual((row["cleanup"], row["cleanup_scope"]), ("confirmed", "local-process-group-only"))
        self.assertFalse(row["last_observation"]["verified"])
        elapsed = (datetime.fromisoformat(run["ended_utc"]) - datetime.fromisoformat(run["started_utc"])).total_seconds()
        self.assertEqual(elapsed, 8341)
        self.assertLess(elapsed, row["deadline_seconds"])
        manifest = git(ROOT, "show", STAGE_SOURCE + ":models/deepseek-v4.1-flash-1017-uncensored-fp8/1/files.sha256")
        self.assertEqual(hashlib.sha256(manifest).hexdigest(), row["plan"]["manifest_sha256"])
        self.assertEqual(sum(int(line.split()[1]) for line in manifest.splitlines()), 510311056155)
        with self.assertRaises(ValueError):
            stage.validate_proof(row["last_observation"], row["plan"], row["body_byte_budget"])

    def test_isolation_positive_controls_and_failure_preserved(self):
        summary, outer = self.rows["network/summary.json"], self.rows["network/observation.json"]
        baseline.check_summary(summary)
        self.assertEqual(summary["proof_receipt_sha256"], HASHES["network/observation.json"])
        for key in ("baseline_complete", "cleanup_confirmed", "dns_equal", "firewall_equal", "namespace_reaped", "proof_pass"):
            self.assertIs(summary[key], True)
        self.assertEqual(outer["git_sha"], SOURCE)
        for name, expected in outer["source_sha256"].items():
            self.assertEqual(hashlib.sha256(git(ROOT, "show", SOURCE + ":" + name)).hexdigest(), expected)
        inner = outer["observation"]
        baseline.proof.check_report(inner)
        baseline.proof.check_observations(inner["positive_controls"], False)
        baseline.proof.check_observations(inner["observations"], True)
        baseline.proof.check_observations(inner["quarantine"]["observations"], True, quarantined=True)
        for row in (summary, outer, inner):
            for key in ("gpu_used", "published", "deployment_eligible"):
                self.assertIs(row[key], False)
        failure = self.rows["original-failure/summary.json"]
        baseline.check_summary(failure)
        self.assertEqual((failure["result"], failure["stage"]), ("fail", "pre-snapshot"))
        self.assertFalse(failure["proof_pass"])
        self.assertFalse(failure["cleanup_confirmed"])
        self.assertIsNone(failure["proof_receipt_sha256"])

    def test_read_only_copy_source_identity_and_original_bounds(self):
        row = self.rows["weight-view.json"]
        self.assertEqual(row["producer"], SOURCE)
        for name, field in (("prepare-selected-weight-view.py", "script_sha256"),
                            ("cache-public-weights.py", "cache_helper_sha256")):
            self.assertEqual(hashlib.sha256(git(ROOT, "show", SOURCE + ":host/" + name)).hexdigest(), row[field])
        self.assertEqual(row["manifest_sha256"], self.rows["weights/output.log"]["proof"]["manifest_sha256"])
        self.assertEqual((row["files"], row["verified_bytes"]), (79, 510311056155))
        self.assertEqual((row["file_mode"], row["directory_mode"], row["publication"]), ("0444", "0555", "atomic-no-replace"))
        self.assertTrue(row["source_preserved"])
        self.assertTrue(row["worker_exit_confirmed"])
        self.assertFalse(row["runtime_ready"])
        self.assertFalse(row["confidential"])
        self.assertEqual(row["timeout_seconds"], 2700)
        self.assertLess((datetime.fromisoformat(row["ended_utc"]) - datetime.fromisoformat(row["started_utc"])).total_seconds(), 2700)

    def build(self, raw=None):
        prep = self.rows["preparation.json"]
        files = prep["files"]
        contract = {"base_image": "vllm/vllm-openai@sha256:" + files["metadata/manifest.json"]["sha256"],
                    "official_config": {"sha256": files["metadata/config.json"]["sha256"]}}
        raw = self.raw["build.json"] if raw is None else raw
        return image.validate_build_record(raw, image.digest(raw), prep, HASHES["preparation.json"], contract)

    def test_source_build_and_preflight_do_not_claim_inference(self):
        prep, inspect = self.rows["preparation.json"], self.rows["inspect.json"]
        self.assertEqual(prep["git_sha"], SOURCE)
        self.assertEqual(prep["prepared_sha256"], prep["files"]["context/service/prepared.json"]["sha256"])
        for name, value in prep["files"].items():
            if name.startswith("context/service/scripts/"):
                source = git(ROOT, "show", SOURCE + ":" + name.removeprefix("context/service/"))
                self.assertEqual(value, {"bytes": len(source), "sha256": image.digest(source)})
        build = self.build()
        for name in ("prepared_sha256", "image_id"):
            self.assertEqual(inspect[name], build[name])
        self.assertEqual(inspect["build_sha256"], HASHES["build.json"])
        self.assertEqual(inspect["preparation_sha256"], HASHES["preparation.json"])
        self.assertEqual((inspect["result"], inspect["reason"]), ("fail", "not-run"))
        self.assertTrue(inspect["preflight_pass"])
        self.assertTrue(inspect["private_inputs_removed"])
        self.assertEqual(inspect["execution"], {"profile": "long1m-graphs", "guard_profile": "selected-deepseek-million",
            "context_tokens": 1048576, "max_output_tokens": 393216, "max_inflight": 1})
        for name in ("runtime_started", "container_created", "hardware_validated", "deployable", "published", "confidential", "e2ee", "zdr"):
            self.assertIs(inspect[name], False)
        for name in ("runtime_started", "hardware_validated", "deployable", "published"):
            changed = copy.deepcopy(build)
            changed[name] = True
            with self.subTest(claim=name), self.assertRaises(ValueError):
                self.build(image.encoded(changed))


if __name__ == "__main__":
    unittest.main()
