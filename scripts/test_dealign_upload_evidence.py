# SPDX-License-Identifier: Apache-2.0
"""Preserve actual upload metadata; replay checks without NFS or model I/O."""

import datetime
import hashlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

from test_evidence_history import git

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evidence/p2/2026-10-23-dealign-cache-upload"
REVISION = "4578a14bf90574d88515076a625acec813d6712b"
RECEIPT_SHA = "70aa251d3158d4302fcb934532031bcab269f3b572f7aa2cd9a3c0775e0c121f"
SOURCES = {
    "host/cache-public-weights.py": "script_sha256",
    "host/probe.py": "supervisor_sha256",
    "scripts/deepseek_model_profile.py": "profile_helper_sha256",
    "models/deepseek-v4.1-flash-1017-uncensored-fp8/1/files.sha256": "target_manifest_sha256",
}


class DealignUploadEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (DIRECTORY / "receipt.json").read_bytes()
        if len(cls.raw) != 1566 or hashlib.sha256(cls.raw).hexdigest() != RECEIPT_SHA:
            raise AssertionError("original upload receipt changed")
        cls.row = json.loads(cls.raw)
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.package = Path(temporary.name).resolve()
        for name, field in SOURCES.items():
            source = git(ROOT, "show", REVISION + ":" + name)
            if hashlib.sha256(source).hexdigest() != cls.row[field]:
                raise AssertionError("historical upload source binding changed")
            path = cls.package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(source)
        cls.cache = types.ModuleType("original_dealign_upload")
        cls.cache.__file__ = str(cls.package / "host/cache-public-weights.py")
        exec(compile(Path(cls.cache.__file__).read_bytes(), cls.cache.__file__, "exec"), cls.cache.__dict__)
        cls.row = json.loads(cls.raw, object_pairs_hook=cls.cache.unique)

    def test_original_identity_timing_completion_and_limits(self):
        self.assertEqual({path.name for path in DIRECTORY.iterdir()}, {"receipt.json", "README.md"})
        row = self.row
        self.assertEqual((row["schema"], row["producer"], row["operation"]),
                         ("nib.public-weight-profile-cache/v1", REVISION, "upload"))
        self.assertEqual((row["result"], row["stage"], row["failure"]), ("pass", "complete", None))
        self.assertEqual((row["cleanup"], row["publication"], row["publication_mode"]),
                         ("worker-completed", "confirmed", "exclusive-directory-copy"))
        self.assertIsNone(row["publication_helper_sha256"])
        for field in ("pack_complete", "worker_exit_confirmed"):
            self.assertIs(row[field], True)
        for field in ("runtime_ready", "confidential", "source_removed", "process_group_absence_verified"):
            self.assertIs(row[field], False)
        self.assertEqual((row["files"], row["verified_bytes"], row["remaining_files"], row["remaining_bytes"]),
                         (79, 510311056155, 0, 0))
        for field in ("files", "verified_bytes", "remaining_files", "remaining_bytes", "timeout_seconds"):
            self.assertIs(type(row[field]), int)
        self.assertEqual((row["started_utc"], row["ended_utc"]),
                         ("2026-10-23T11:04:25.312425+00:00", "2026-10-23T12:17:27.349410+00:00"))
        elapsed = datetime.datetime.fromisoformat(row["ended_utc"]) - datetime.datetime.fromisoformat(row["started_utc"])
        self.assertEqual(elapsed, datetime.timedelta(seconds=4382, microseconds=36985))
        self.assertEqual(row["timeout_seconds"], 7200)
        self.assertLess(elapsed.total_seconds(), row["timeout_seconds"] - self.cache.RESTORE_RESERVE)

    def test_reachable_original_source_closure_manifest_and_prior_stage_identity(self):
        git(ROOT, "merge-base", "--is-ancestor", REVISION, "HEAD")
        original, target = self.cache.profiles()
        self.assertEqual(self.row["model_profile"], target)
        self.assertNotEqual(target["hf_revision"], original["hf_revision"])
        self.assertEqual(self.row["source_manifest_sha256"], self.row["target_manifest_sha256"])
        rows = self.cache.manifest_rows((self.package / target["pack"] / "files.sha256").read_bytes(),
                                        target["manifest_sha256"], target["total_bytes"], 79)
        self.assertEqual((len(rows), sum(row[1] for row in rows)), (self.row["files"], self.row["verified_bytes"]))
        self.assertTrue(callable(self.cache.load_capture()))
        self.assertFalse((self.package / "scripts/pilot/export.py").exists())
        stage_raw = (ROOT / "evidence/p0/2026-10-23-dealign-stage-complete/output.log").read_bytes()
        self.assertEqual(hashlib.sha256(stage_raw).hexdigest(), "ab2b4c8af5544ff0cc76c34c0ce35a47759d219a4c1711e1b639971fe9f82e7a")
        stage = json.loads(stage_raw, object_pairs_hook=self.cache.unique)
        for field in ("hf_repo", "hf_revision", "manifest_sha256"):
            self.assertEqual(stage["proof"][field], target[field])
        for field in ("files", "verified_bytes"):
            self.assertEqual(stage["proof"][field], self.row[field])

    def worker_metadata(self):
        return {"schema": "nib.public-weight-profile-worker/v1", **{
            field: self.row[field] for field in ("operation", "publication_mode", "files", "verified_bytes")}}

    def replay_metadata(self, worker=None, error=None):
        # Only replay the parent's checks on a synthetic worker message. The
        # pinned capture source is verified separately; no child or NFS runs.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            output = root / "receipt.json"
            capture = mock.Mock(return_value=json.dumps(worker).encode(), side_effect=error)
            with mock.patch.object(self.cache, "load_capture", return_value=capture):
                status = self.cache.profile_report(
                    "upload", "cache.example.test:/public", root / "mount", root / "local",
                    self.package / self.row["model_profile"]["pack"] / "files.sha256",
                    None, output, REVISION, 7200)
            capture.assert_called_once()
            self.assertEqual(capture.call_args.kwargs, {"timeout": 7140, "limit": 4096})
            self.assertEqual(hashlib.sha256(capture.call_args.args[0][7].encode()).hexdigest(), self.row["script_sha256"])
            return status, json.loads(output.read_bytes(), object_pairs_hook=self.cache.unique)

    def test_original_parent_metadata_replays_without_claiming_new_copy(self):
        status, replayed = self.replay_metadata(self.worker_metadata())
        self.assertEqual(status, 0)
        without_clock = lambda row: {key: value for key, value in row.items() if key not in ("started_utc", "ended_utc")}
        self.assertEqual(without_clock(replayed), without_clock(self.row))

    def assert_failed(self, status, failed, reason):
        self.assertEqual((status, failed["result"], failed["failure"]), (1, "fail", reason))
        self.assertEqual((failed["cleanup"], failed["publication"]), ("unproven", "unproven"))
        for field in ("pack_complete", "runtime_ready", "confidential", "worker_exit_confirmed",
                      "source_removed", "process_group_absence_verified"):
            self.assertIs(failed[field], False)
        self.assertEqual((failed["files"], failed["verified_bytes"]), (0, 0))
        self.assertIsNone(failed["remaining_files"])
        self.assertIsNone(failed["remaining_bytes"])

    def test_partial_wrong_mode_or_extra_claim_cannot_replay_as_complete_upload(self):
        for change in ({"files": 48}, {"verified_bytes": 310737990397}, {"files": True},
                       {"operation": "restore"}, {"publication_mode": "atomic-no-replace-directory"},
                       {"pack_complete": True}, {"runtime_ready": True}):
            with self.subTest(change=change):
                self.assert_failed(*self.replay_metadata({**self.worker_metadata(), **change}), "rejected")

    def test_supervisor_failure_cannot_claim_completion_or_cleanup(self):
        for message, reason in (("probe-timeout", "timeout"), ("probe-command-failed", "worker-failed"),
                                ("probe-cleanup-timeout", "cleanup-unproven")):
            with self.subTest(message=message):
                self.assert_failed(*self.replay_metadata(error=ValueError(message)), reason)


if __name__ == "__main__":
    unittest.main()
