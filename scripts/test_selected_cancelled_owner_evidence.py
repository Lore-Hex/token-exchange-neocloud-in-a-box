# SPDX-License-Identifier: Apache-2.0
"""Original cancelled-owner failure and cleanup, not a successful day run."""

import hashlib
import json
from pathlib import Path
import unittest

from test_evidence_history import git
from test_selected_day_startup_evidence import DIRECTORY as STARTUP, HASHES, SOURCE
from test_selected_finite_evidence import FALSE_FIELDS

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evidence/p2/2026-10-25-selected-owner-cancelled"
TERMINAL_SHA = "9114915d81ffbaef55896a1dbd760ac471d186291a22700c1ca8c5dca2355605"
HELPER = "75b66500621793d668959a5e71baf3f26760eed1"
JOINS = ("image_id", "preparation_sha256", "prepared_sha256")
TRUE_FIELDS = ("preflight_pass", "container_created", "tls_publication_requested",
               "watchdog_confirmed", "private_inputs_removed")


class CancelledOwnerEvidenceTests(unittest.TestCase):
    def receipt(self):
        raw = (DIRECTORY / "owner-terminal.json").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), TERMINAL_SHA)
        self.assertLess(len(raw), 4096)
        self.assertTrue(raw.endswith(b"\n"))
        return json.loads(raw)

    def test_exact_original_inventory_closed_fields_and_build_joins(self):
        self.assertEqual({path.name for path in DIRECTORY.iterdir()}, {"README.md", "owner-terminal.json"})
        row = self.receipt()
        self.assertEqual(set(row), {*FALSE_FIELDS, *TRUE_FIELDS, *JOINS,
            "build_sha256", "cleanup", "lifetime", "owner_cleanup", "reason", "result",
            "runtime_started", "schema", "status"})
        raw = (STARTUP / "build.json").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), HASHES["build.json"])
        self.assertEqual(row["build_sha256"], HASHES["build.json"])
        build = json.loads(raw)
        for name in JOINS:
            self.assertEqual(row[name], build[name], name)

    def test_failure_and_unknown_runtime_are_not_promoted_by_cleanup(self):
        row = self.receipt()
        self.assertEqual((row["schema"], row["result"], row["status"], row["reason"], row["cleanup"]),
                         ("nib.selected-service-owner/v2", "fail", "BLOCKED", "lifecycle", "confirmed"))
        self.assertIsNone(row["runtime_started"])
        for names, value in ((FALSE_FIELDS, False), (TRUE_FIELDS, True)):
            for name in names:
                self.assertIs(row[name], value, name)
        cleanup = row["owner_cleanup"]
        self.assertEqual(set(cleanup), {"cleanup_confirmed", "container_absent", "creations_resolved",
                                       "network_absent", "policy_removed", "quarantine_confirmed"})
        for name, value in cleanup.items():
            self.assertIs(value, True, name)
        for name in ("lifecycle", "owner_failure", "fallback_cleanup", "exit_code", "owner_exit_code",
                     "started_at", "ended_at", "finished_at", "signal_sent", "current_identity",
                     "attach_identity", "original_run_pass", "soak_validated"):
            self.assertNotIn(name, row)

    def test_lifetime_is_original_plan_not_actual_completion(self):
        self.assertEqual(self.receipt()["lifetime"], {
            "profile": "ordinary-24h", "admission_stop_at": "2026-10-26T06:40:45Z",
            "hard_stop_at": "2026-10-26T06:47:00Z", "owner_hard_stop_at": "2026-10-26T06:50:00Z"})
        first = json.loads((STARTUP / "failed-owner.json").read_bytes())
        self.assertEqual(hashlib.sha256((STARTUP / "failed-owner.json").read_bytes()).hexdigest(),
                         HASHES["failed-owner.json"])
        self.assertEqual((first["result"], first["cleanup"]), ("fail", "not-started"))

    def test_historical_source_attribution_does_not_invent_embedded_provenance(self):
        for revision, path, expected in (
            (SOURCE, "host/record-selected-service.py",
             "bd12d7b5f09feafcbf0647e68c104188bce6847a3d219b9a28da696121453c84"),
            (HELPER, "host/release-selected-attach.py",
             "c134d5c531a51ec755979a4a2d13434723fbfbffa65b00bc7d1a9b0bf2c59d75"),
        ):
            git(ROOT, "merge-base", "--is-ancestor", revision, "HEAD")
            source = git(ROOT, "show", revision + ":" + path)
            self.assertEqual(hashlib.sha256(source).hexdigest(), expected)
        row = self.receipt()
        for name in ("git_sha", "source_revision", "helper_sources", "address", "hostname", "argv",
                     "state_dir", "pid", "boot_id_sha256", "expectation_sha256"):
            self.assertNotIn(name, row)


if __name__ == "__main__":
    unittest.main()
