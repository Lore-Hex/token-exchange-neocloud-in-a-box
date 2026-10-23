# SPDX-License-Identifier: Apache-2.0
"""Original full-stage metadata and pinned source replay, without model I/O."""

import datetime
import hashlib
from pathlib import Path
import tempfile
import types
import unittest

from test_evidence_history import git, producer

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evidence/p0/2026-10-23-dealign-stage-complete"
REVISION = "3dbbd3aafc5e26adaac5bc4ebfc48a7fb4c4140d"
SCRIPT_SHA = "19e6f58fef0a877d3fc13718bd03fa2baa1aba74cab396e1bcfb1600bb43177f"
ORIGINALS = {
    "run.json": (1087, "1e9b01e39b67f47bcbc4ae4bed63aed838c2a7fdefd74186c0edce3bcb91ca95"),
    "output.log": (1727, "ab2b4c8af5544ff0cc76c34c0ce35a47759d219a4c1711e1b639971fe9f82e7a"),
}


class DealignStageEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = git(ROOT, "show", REVISION + ":host/record-stage.py")
        if hashlib.sha256(source).hexdigest() != SCRIPT_SHA:
            raise AssertionError("historical stage producer changed")
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        path = Path(temporary.name).resolve() / "record-stage.py"
        path.write_bytes(source)
        cls.stage = types.ModuleType("original_dealign_stage")
        cls.stage.__file__ = str(path)
        exec(compile(source, str(path), "exec"), cls.stage.__dict__)
        cls.stage.ROOT = ROOT
        records = {}
        for name, (size, digest) in ORIGINALS.items():
            raw = (DIRECTORY / name).read_bytes()
            if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
                raise AssertionError("original stage artifact changed")
            records[name] = cls.stage.parse(raw)
        cls.record, cls.report = records["run.json"], records["output.log"]

    def test_original_identity_timing_bounds_and_nonqualification(self):
        self.assertEqual({path.name for path in DIRECTORY.iterdir()}, {"run.json", "output.log", "README.md"})
        run, report = self.record, self.report
        self.assertEqual(producer((DIRECTORY / "run.json").read_bytes()), REVISION)
        self.assertEqual(report["source_git_sha"], REVISION)
        self.assertEqual((run["result"], report["result"], report["stage"]), ("pass", "pass", "complete"))
        self.assertEqual((run["phase"], run["task"], run["execution"]), ("P0", "T4", "software"))
        self.assertEqual(report["schema"], "nib.weight-stage-recording/v1")
        self.assertIs(run["hardware_validated"], False)
        for field in ("hardware_validated", "runtime_ready", "confidential_qualified"):
            self.assertIs(report[field], False)
        self.assertEqual((report["cleanup"], report["cleanup_scope"]), ("confirmed", "local-process-group-only"))
        self.assertEqual((run["started_utc"], run["ended_utc"]),
                         ("2026-10-23T09:28:00Z", "2026-10-23T11:01:56Z"))
        elapsed = datetime.datetime.fromisoformat(run["ended_utc"]) - datetime.datetime.fromisoformat(run["started_utc"])
        self.assertEqual(elapsed, datetime.timedelta(seconds=5636))
        self.assertEqual((report["deadline_seconds"], report["download_workers"], report["body_byte_budget"]),
                         (self.stage.MAX_SECONDS, self.stage.MAX_WORKERS, 510311056234))
        self.assertLess(elapsed.total_seconds(), report["deadline_seconds"])
        self.assertEqual(report["receipt_sha256"], "e4e1b38e6986b599b6fc13b70f51031b0e587351da2fb8a135527a5f36833faf")
        self.assertEqual(report["nib_sha256"], "7ee5f1e7cd29697c7dfc42cff9e4fdc3ff0cf6c0861f55fe6989190d29c3361c")
        self.assertEqual(run["command"],
                         "python3 -I -S record-stage.py run --download --seconds 10800 --max-bytes 510311056234 "
                         "--receipt-sha256 " + report["receipt_sha256"] + " --workers 8 --destination <private> --evidence <private>")

    def test_reachable_producer_helpers_profile_manifest_and_go_source_closure(self):
        git(ROOT, "merge-base", "--is-ancestor", REVISION, "HEAD")
        builder = self.stage.load_builder()
        profiles = self.stage.load_profiles(builder, REVISION)
        profile = profiles.select("uncensored")
        manifest = git(ROOT, "show", REVISION + ":" + profile["pack"] + "/files.sha256")
        self.assertEqual(profiles.validate_manifest("uncensored", manifest), profile)
        plan = self.report["plan"]
        for field in ("hf_repo", "hf_revision", "manifest_sha256", "total_bytes"):
            self.assertEqual(plan[field], profile[field])
        rows = [line.split() for line in manifest.decode("ascii").splitlines()]
        self.assertEqual((len(rows), sum(int(row[1]) for row in rows)), (79, plan["total_bytes"]))
        self.assertNotEqual(plan["hf_revision"], profiles.select()["hf_revision"])

        # Select exactly the historical builder's Go source closure, then use
        # its pinned digest implementation. No compiler, download or model runs.
        entries = git(ROOT, "ls-tree", "-r", "-z", REVISION, "--", "go.mod", "go.sum", "cmd/nib", "internal", "schemas")
        files = {}
        for entry in entries.split(b"\0"):
            if entry:
                metadata, name = entry.decode("ascii").split("\t")
                mode, kind, oid = metadata.split()
                self.assertIn(mode, {"100644", "100755"})
                self.assertEqual(kind, "blob")
                files[name] = git(ROOT, "cat-file", "blob", oid)
        self.assertEqual(builder.overlay.bundle_digest(files), self.report["build_source_sha256"])

    def test_complete_proof_reuse_arithmetic_capacity_and_unverified_progress(self):
        report = self.report
        self.stage.validate_plan(report["plan"])
        self.stage.validate_proof(report["proof"], report["plan"], report["body_byte_budget"])
        proof = report["proof"]
        self.assertEqual((proof["files"], proof["verified_bytes"], proof["reused_files"], proof["downloaded_bytes"]),
                         (79, 510311056155, 48, 199573065758))
        seed_raw = (ROOT / "evidence/p2/2026-10-23-dealign-partial-seed/receipt.json").read_bytes()
        self.assertEqual(hashlib.sha256(seed_raw).hexdigest(), "f0d7eb7839fbbc2e3290219c78baf3f66380df053168fc6a88c55b1e370f0145")
        seed = self.stage.parse(seed_raw)
        self.assertIs(seed["pack_complete"], False)
        self.assertEqual(seed["target_manifest_sha256"], proof["manifest_sha256"])
        self.assertEqual((seed["files"], seed["remaining_bytes"]), (proof["reused_files"], proof["downloaded_bytes"]))
        self.assertEqual(seed["verified_bytes"] + proof["downloaded_bytes"], proof["verified_bytes"])
        self.assertEqual(report["disk"], {"available_bytes": 618593964032, "overhead_bytes": 51031105616,
                                         "required_bytes": 561342161771, "sufficient": True})
        self.assertGreaterEqual(report["disk"]["available_bytes"], report["disk"]["required_bytes"])
        self.assertEqual(report["last_observation"], {
            "counts": {"observed_bytes": 510311056155, "observed_files": 79, "partial_bytes": 0, "partial_files": 0},
            "elapsed_seconds": 5616, "verified": False})

    def test_historical_validator_rejects_partial_progress_wrong_identity_and_budget(self):
        report = self.report
        invalid = [None, report["last_observation"], report["last_observation"]["counts"]]
        for change in ({"files": 48}, {"files": True}, {"verified_bytes": 310737990397},
                       {"hf_repo": "deepseek-ai/DeepSeek-V4.1-Flash-1017"},
                       {"hf_revision": "1bae0e1088993b9b82de08f59de5be5a34e42170"},
                       {"manifest_sha256": "0" * 64}, {"reused_files": 80},
                       {"downloaded_bytes": report["body_byte_budget"] + 1}, {"runtime_ready": True}):
            invalid.append({**report["proof"], **change})
        for proof in invalid:
            with self.subTest(proof=proof), self.assertRaises(ValueError):
                self.stage.validate_proof(proof, report["plan"], report["body_byte_budget"])


if __name__ == "__main__":
    unittest.main()
