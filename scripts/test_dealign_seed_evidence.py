# SPDX-License-Identifier: Apache-2.0
"""Replay public seed metadata, not NFS I/O or complete-pack acceptance."""

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
DIRECTORY = ROOT / "evidence/p2/2026-10-23-dealign-partial-seed"
REVISION = "4578a14bf90574d88515076a625acec813d6712b"
RECEIPT_SHA = "f0d7eb7839fbbc2e3290219c78baf3f66380df053168fc6a88c55b1e370f0145"
SOURCES = {
    "host/cache-public-weights.py": "script_sha256",
    "host/probe.py": "supervisor_sha256",
    "scripts/deepseek_model_profile.py": "profile_helper_sha256",
    "scripts/pilot/export.py": "publication_helper_sha256",
    "models/deepseek-v4.1-flash-1017/1/files.sha256": "source_manifest_sha256",
    "models/deepseek-v4.1-flash-1017-uncensored-fp8/1/files.sha256": "target_manifest_sha256",
}


class DealignSeedEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (DIRECTORY / "receipt.json").read_bytes()
        if hashlib.sha256(cls.raw).hexdigest() != RECEIPT_SHA:
            raise AssertionError("original seed receipt changed")
        cls.row = json.loads(cls.raw)
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.package = Path(temporary.name).resolve()
        for name, field in SOURCES.items():
            source = git(ROOT, "show", REVISION + ":" + name)
            if hashlib.sha256(source).hexdigest() != cls.row[field]:
                raise AssertionError("historical seed source binding changed")
            path = cls.package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(source)
        cls.cache = types.ModuleType("original_dealign_seed")
        cls.cache.__file__ = str(cls.package / "host/cache-public-weights.py")
        exec(compile(Path(cls.cache.__file__).read_bytes(), cls.cache.__file__, "exec"), cls.cache.__dict__)
        cls.row = json.loads(cls.raw, object_pairs_hook=cls.cache.unique)

    def test_original_receipt_identity_timing_and_partial_only_claims(self):
        self.assertEqual({path.name for path in DIRECTORY.iterdir()}, {"receipt.json", "README.md"})
        self.assertEqual(len(self.raw), 1642)
        row = self.row
        self.assertEqual((row["schema"], row["producer"], row["operation"]),
                         ("nib.public-weight-profile-cache/v1", REVISION, "seed"))
        self.assertEqual((row["result"], row["stage"], row["failure"]), ("pass", "complete", None))
        self.assertEqual((row["cleanup"], row["publication"], row["publication_mode"]),
                         ("worker-completed", "confirmed", "atomic-no-replace-directory"))
        self.assertIs(row["worker_exit_confirmed"], True)
        for field in ("pack_complete", "runtime_ready", "confidential", "source_removed",
                      "process_group_absence_verified"):
            self.assertIs(row[field], False)
        self.assertEqual((row["files"], row["verified_bytes"], row["remaining_files"], row["remaining_bytes"]),
                         (48, 310737990397, 31, 199573065758))
        for field in ("files", "verified_bytes", "remaining_files", "remaining_bytes", "timeout_seconds"):
            self.assertIs(type(row[field]), int)
        self.assertEqual((row["started_utc"], row["ended_utc"]),
                         ("2026-10-23T08:43:23.196273+00:00", "2026-10-23T09:23:43.984062+00:00"))
        elapsed = datetime.datetime.fromisoformat(row["ended_utc"]) - datetime.datetime.fromisoformat(row["started_utc"])
        self.assertEqual(elapsed, datetime.timedelta(seconds=2420, microseconds=787789))
        self.assertEqual(row["timeout_seconds"], 7200)
        self.assertLess(elapsed.total_seconds(), row["timeout_seconds"])

    def test_reachable_original_six_file_source_and_manifest_closure(self):
        git(ROOT, "merge-base", "--is-ancestor", REVISION, "HEAD")
        cache = self.cache
        original, target = cache.profiles()
        self.assertEqual(self.row["model_profile"], target)
        self.assertEqual(target["hf_revision"], "c19ca5ca4eaeb79d29fdf08aea8327da95b1116a")
        self.assertEqual(target["hf_repo"], "dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8")
        self.assertTrue(callable(cache.load_capture()))
        self.assertTrue(callable(cache.load_pinned("scripts/pilot/export.py", cache.PUBLICATION_SHA256).rename_exclusive))
        old = cache.manifest((self.package / original["pack"] / "files.sha256").read_bytes())
        new = cache.manifest_rows((self.package / target["pack"] / "files.sha256").read_bytes(),
                                  target["manifest_sha256"], target["total_bytes"], 79)
        selected = cache.seed_rows(old, new)
        self.assertEqual((len(old), len(new), len(selected)), (83, 79, self.row["files"]))
        self.assertEqual(set(selected), set(old) & set(new))
        self.assertEqual(sum(row[1] for row in selected), self.row["verified_bytes"])
        shards = [row for row in selected if row[2].endswith(".safetensors")]
        self.assertEqual((len(shards), sum(row[1] for row in shards)), (21, 310723700832))
        self.assertEqual((len(new) - len(selected), target["total_bytes"] - self.row["verified_bytes"]),
                         (self.row["remaining_files"], self.row["remaining_bytes"]))
        self.assertEqual((cache.SEED_FILES, cache.SEED_BYTES), (self.row["files"], self.row["verified_bytes"]))

    def replay_metadata(self, worker):
        # Feed only the original worker's public metadata shape into the exact
        # historical parent checks. No worker, mount or model-byte copy runs.
        cache = self.cache
        original, target = cache.profiles()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            output = root / "receipt.json"
            capture = mock.Mock(return_value=json.dumps(worker).encode())
            with mock.patch.object(cache, "load_capture", return_value=capture):
                status = cache.profile_report(
                    "seed", "cache.example.test:/public", root / "mount", root / "local",
                    self.package / target["pack"] / "files.sha256",
                    self.package / original["pack"] / "files.sha256", output, REVISION, 7200)
            capture.assert_called_once()
            self.assertEqual(capture.call_args.kwargs, {"timeout": 7140, "limit": 4096})
            self.assertEqual(hashlib.sha256(capture.call_args.args[0][7].encode()).hexdigest(),
                             self.row["script_sha256"])
            return status, json.loads(output.read_bytes(), object_pairs_hook=cache.unique)

    def worker_metadata(self):
        return {"schema": "nib.public-weight-profile-worker/v1", **{
            field: self.row[field] for field in ("operation", "publication_mode", "files", "verified_bytes")}}

    def test_original_parent_metadata_checks_replay_without_claiming_new_io(self):
        status, replayed = self.replay_metadata(self.worker_metadata())
        self.assertEqual(status, 0)
        without_clock = lambda row: {key: value for key, value in row.items() if key not in ("started_utc", "ended_utc")}
        self.assertEqual(without_clock(replayed), without_clock(self.row))

    def test_original_parent_rejects_complete_pack_or_wrong_publication_metadata(self):
        for change in ({"files": 79}, {"verified_bytes": 510311056155}, {"files": True},
                       {"operation": "restore"}, {"publication_mode": "exclusive-directory-copy"},
                       {"pack_complete": True}, {"schema": "nib.public-weight-profile-cache/v1"}):
            with self.subTest(change=change):
                status, failed = self.replay_metadata({**self.worker_metadata(), **change})
                self.assertEqual((status, failed["result"], failed["failure"]), (1, "fail", "rejected"))
                self.assertEqual((failed["cleanup"], failed["publication"]), ("unproven", "unproven"))
                for field in ("pack_complete", "runtime_ready", "confidential", "worker_exit_confirmed",
                              "source_removed", "process_group_absence_verified"):
                    self.assertIs(failed[field], False)
                self.assertEqual((failed["files"], failed["verified_bytes"]), (0, 0))
                self.assertIsNone(failed["remaining_files"])
                self.assertIsNone(failed["remaining_bytes"])


if __name__ == "__main__":
    unittest.main()
