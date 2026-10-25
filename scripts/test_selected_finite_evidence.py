# SPDX-License-Identifier: Apache-2.0
"""Preserve one finite ordinary owner completion without upgrading its scope."""

import hashlib
import json
from pathlib import Path
import unittest

from test_evidence_history import git
from test_selected_owner_failure_evidence import INPUTS


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p2/2026-10-25-selected-finite-owner-pass/owner.json"
SOURCE = "b586b874fbf86430f19a8f97092901aa02ca9de3"
FALSE_FIELDS = ("confidential", "deployable", "e2ee", "hardware_validated",
                "published", "reasoning", "tools", "zdr")
TRUE_FIELDS = ("container_created", "preflight_pass", "private_inputs_removed",
               "runtime_started", "tls_publication_requested", "watchdog_confirmed")


class FiniteOwnerEvidenceTests(unittest.TestCase):
    def receipt(self):
        raw = EVIDENCE.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         "d35f6873ec1e3db9a7f7ec7c07a51f2ecf0f54f5fb624801b4a774d3907133ae")
        return json.loads(raw)

    def test_original_closed_receipt_and_build_identity(self):
        row = self.receipt()
        self.assertEqual(set(row), {*INPUTS, *FALSE_FIELDS, *TRUE_FIELDS,
                         "cleanup", "lifecycle", "owner_cleanup", "reason", "result", "schema", "status"})
        self.assertEqual({name: row[name] for name in INPUTS}, INPUTS)
        self.assertEqual((row["schema"], row["result"], row["status"], row["reason"], row["cleanup"]),
                         ("nib.selected-service-owner/v1", "pass", "PASS", "completed", "confirmed"))
        for names, expected in ((FALSE_FIELDS, False), (TRUE_FIELDS, True)):
            for name in names:
                self.assertIs(row[name], expected, name)

    def test_ready_natural_retirement_and_complete_cleanup(self):
        row = self.receipt()
        lifecycle = row["lifecycle"]
        privacy = ("confidential", "deployable", "e2ee", "hardware_validated", "reasoning", "tools", "zdr")
        self.assertEqual(set(lifecycle), {*privacy, "cleanup", "cleanup_confirmed", "ready",
                                        "reason", "schema", "status"})
        self.assertEqual((lifecycle["schema"], lifecycle["status"], lifecycle["reason"]),
                         ("nib.selected-service-lifecycle/v1", "retired", "retired"))
        self.assertIs(lifecycle["ready"], True)
        self.assertIs(lifecycle["cleanup_confirmed"], True)
        for name in privacy:
            self.assertIs(lifecycle[name], False, name)
        self.assertEqual([item["role"] for item in lifecycle["cleanup"]], ["tls", "guard", "engine"])
        for item in lifecycle["cleanup"]:
            self.assertEqual(set(item), {"role", "drained", "reaped"})
            self.assertIs(item["drained"], True)
            self.assertIs(item["reaped"], True)
        cleanup = row["owner_cleanup"]
        self.assertEqual(set(cleanup), {"cleanup_confirmed", "container_absent", "creations_resolved",
                                      "network_absent", "policy_removed", "quarantine_confirmed"})
        for name, value in cleanup.items():
            self.assertIs(value, True, name)

    def test_operator_attributed_source_is_preserved_not_embedded(self):
        git(ROOT, "merge-base", "--is-ancestor", SOURCE, "HEAD")
        raw = git(ROOT, "show", SOURCE + ":host/record-selected-service.py")
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         "39dd688dcf91ecc6cd2a31372969c00cb1b5422b2c0ec80c6947e62f4d74737b")
        for name in ("git_sha", "sources", "started_at", "ended_at", "hard_stop_at", "address"):
            self.assertNotIn(name, self.receipt())


if __name__ == "__main__":
    unittest.main()
