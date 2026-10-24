# SPDX-License-Identifier: Apache-2.0
"""Original content-free storage and interruption receipts; no hardware I/O."""

import copy
import datetime
import hashlib
import json
from pathlib import Path
import types
import unittest

from test_evidence_history import git, producer

ROOT = Path(__file__).resolve().parents[1]
STAGE = "bca89c19f5e15ce0989ee34eac98b94afd5cd822"
INTERRUPTED = "323ab0eedacee474e5b12fcc80a169ef43400075"
MANIFEST = "5c6891a9649702b1ac0327b945600acb40c571a069942517cf14ba661a3cfcbc"


def original(directory, name, digest):
    raw = (ROOT / "evidence/p2" / directory / name).read_bytes()
    if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != digest:
        raise AssertionError("original receipt changed")
    return json.loads(raw)


class PersistentSelectedEvidenceTests(unittest.TestCase):
    def setUp(self):
        directory = "2026-10-24-persistent-selected-stage"
        self.run = original(directory, "run.json", "5e73c2de95e361c0f05e6bf9be9bb986585d728c4ce753aa0868487eb8b9e990")
        self.report = original(directory, "output.log", "79e8b05f3b16339c8ef889970a245c58fc9814f0c8a82ec4a1e09fd773aa6648")
        self.admission = original(directory, "admission.json", "7cdd069bb6833f92945c1a6700beb3aec3a2bcf9f4cf7e69b698386bc477ebaf")
        raw = git(ROOT, "show", STAGE + ":host/record-stage.py")
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         "19e6f58fef0a877d3fc13718bd03fa2baa1aba74cab396e1bcfb1600bb43177f")
        self.stage = types.ModuleType("persistent_stage_original")
        self.stage.__file__ = str(ROOT / "host/record-stage.py")
        exec(compile(raw, self.stage.__file__, "exec"), self.stage.__dict__)

    def test_complete_stage_identity_bounds_and_original_validator(self):
        run, report = self.run, self.report
        git(ROOT, "merge-base", "--is-ancestor", STAGE, "HEAD")
        self.assertEqual((run["git_sha"], report["source_git_sha"]), (STAGE, STAGE))
        self.assertEqual((run["result"], report["result"], report["stage"], report["cleanup"]),
                         ("pass", "pass", "complete", "confirmed"))
        self.assertEqual((run["started_utc"], run["ended_utc"]),
                         ("2026-10-24T07:57:40Z", "2026-10-24T09:16:46Z"))
        elapsed = datetime.datetime.fromisoformat(run["ended_utc"]) - datetime.datetime.fromisoformat(run["started_utc"])
        self.assertEqual(elapsed.total_seconds(), 4746)
        self.assertEqual((report["deadline_seconds"], report["download_workers"]), (5400, 8))
        self.assertLess(elapsed.total_seconds(), report["deadline_seconds"])
        self.stage.validate_plan(report["plan"])
        self.stage.validate_proof(report["proof"], report["plan"], report["body_byte_budget"])
        proof = report["proof"]
        self.assertEqual((proof["files"], proof["verified_bytes"], proof["downloaded_bytes"], proof["reused_files"]),
                         (79, 510311056155, 510311056155, 0))
        self.assertEqual(proof["manifest_sha256"], MANIFEST)
        self.assertEqual(proof["hf_repo"], "dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8")
        self.assertEqual(proof["hf_revision"], "c19ca5ca4eaeb79d29fdf08aea8327da95b1116a")
        self.assertEqual(report["body_byte_budget"], proof["verified_bytes"] + proof["files"])
        self.assertGreaterEqual(report["disk"]["available_bytes"], report["disk"]["required_bytes"])
        self.assertIs(run["hardware_validated"], False)
        for field in ("hardware_validated", "runtime_ready", "confidential_qualified"):
            self.assertIs(report[field], False)
        self.assertIs(report["last_observation"]["verified"], False)

    def test_progress_partial_or_wrong_identity_cannot_be_a_proof(self):
        report = self.report
        invalid = [report["last_observation"], report["last_observation"]["counts"]]
        for field, value in (("files", 78), ("verified_bytes", 510311056154),
                             ("manifest_sha256", "0" * 64), ("reused_files", 80),
                             ("downloaded_bytes", report["body_byte_budget"] + 1)):
            changed = copy.deepcopy(report["proof"])
            changed[field] = value
            invalid.append(changed)
        for proof in invalid:
            with self.subTest(proof=proof), self.assertRaises(ValueError):
                self.stage.validate_proof(proof, report["plan"], report["body_byte_budget"])

    def test_filesystem_admission_is_not_a_verified_pack_or_immutable_volume(self):
        row = self.admission
        source = git(ROOT, "show", STAGE + ":host/cache-public-weights.py")
        self.assertEqual(hashlib.sha256(source).hexdigest(), row["script_sha256"])
        self.assertEqual((row["producer"], row["operation"], row["result"], row["backend"]),
                         (STAGE, "admit", "pass", "virtiofs"))
        self.assertIs(row["filesystem_admitted"], True)
        self.assertIs(row["worker_exit_confirmed"], True)
        self.assertEqual(row["filesystem_checks"], ["private-owned-directory", "flock-contention",
                         "hardlink-no-replace", "file-and-directory-fsync"])
        self.assertEqual(row["target_manifest_sha256"], MANIFEST)
        self.assertEqual((row["files"], row["verified_bytes"]), (0, 0))
        for field in ("capacity_checked", "immutable", "pack_complete", "runtime_ready", "confidential", "hardware_validated"):
            self.assertIs(row[field], False)

    def test_interruption_does_not_become_success_or_confirmed_cleanup(self):
        directory = "2026-10-24-ordinary-interrupted"
        run = original(directory, "run.json", "13304f6c56fbe01a25ea8312474b9b3f85f896d7f43449486f865871a1fe964f")
        report = original(directory, "output.log", "8d6e050359557d5f47a1b1a1160d3b7edcf9b684d5a4c9aac4ba1041a3897dd8")
        git(ROOT, "merge-base", "--is-ancestor", INTERRUPTED, "HEAD")
        self.assertEqual(producer((ROOT / "evidence/p2" / directory / "run.json").read_bytes()), INTERRUPTED)
        self.assertEqual((run["git_sha"], report["provenance"]["git_sha"]), (INTERRUPTED, INTERRUPTED))
        self.assertEqual((run["result"], report["result"], report["cleanup"]), ("fail", "fail", "unconfirmed"))
        self.assertEqual((report["experiment_step"], report["output_bytes"]), ("decode", 0))
        self.assertEqual(report["container_observation"], {"status": "running", "exit_code": 0, "oom_killed": False})
        self.assertNotIn("experiment", report)
        self.assertIs(run["hardware_validated"], False)
        for field in ("functional_pass", "confidential", "deployable"):
            self.assertIs(report[field], False)
        self.assertEqual(report["provenance"]["model_profile"]["manifest_sha256"], MANIFEST)


if __name__ == "__main__":
    unittest.main()
