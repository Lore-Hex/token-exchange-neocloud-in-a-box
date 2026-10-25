# SPDX-License-Identifier: Apache-2.0
"""Original finite-owner failures are not repaired into successful evidence."""

import hashlib
import json
from pathlib import Path
import unittest

from test_evidence_history import git


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p2/2026-10-25-selected-owner-startup-failures"
CASES = (
    ("owner-8fcfe413.json", "3f08aaf1daa271c191329f72158591081166fea572fb2908abc090f160db8274",
     "8fcfe41338d9ca755462044dcc6f165357516518",
     "3a37fcac04103d2653950daacc68fcb6d32a4a3eedb04b406f6a23dcfaa2dec6"),
    ("owner-be7b2fc.json", "28c22be33ba828636cc1af6c592984953e56a6b838fb4427928f02858c072992",
     "be7b2fcc6082b3c0afa1b7b8d536c33ce05ca6d3",
     "9f7c9531d5bb93bb80c27c0c36540556ceb1c5c859f659521ff3f846bd355648"),
)
INPUTS = {
    "build_sha256": "8b61bb413cd3e72fafeb762fcfbc88e3bdf5eebddd480e06226e02b4b5c38753",
    "image_id": "sha256:c9a732c68e6ef799d1879c130ebf77d89bcdd62613090e859042a911800bb538",
    "preparation_sha256": "9566cc6a60bc653849ce43be8e6fdd46d5e34f42529e9342ae36a36407cfcad5",
    "prepared_sha256": "377c6a910f9aac817725532ac16f16e825e928501d0aa4bf66c7ba88fd58abff",
}
FALSE_FIELDS = ("confidential", "container_created", "deployable", "e2ee", "hardware_validated",
                "published", "reasoning", "runtime_started", "tls_publication_requested", "tools",
                "watchdog_confirmed", "zdr")
RECOVERY = ("recovery-dcb8563.json",
            "ca26771c582f823973dfa40c4e126315d9070305cbe0a8f84ab555eeee444063",
            "dcb8563ab8a5404add41b277d9c928da0405d92a",
            "3b8f40db396dffbfef5a65769b56a5143ac20908220c93743b20007f736ffce2")


class OwnerFailureEvidenceTests(unittest.TestCase):
    def receipt(self, case):
        name, expected, _, _ = case
        raw = (EVIDENCE / name).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), expected)
        return json.loads(raw)

    def rows(self):
        return [self.receipt(case) for case in CASES]

    def test_original_bytes_closed_failures_and_input_joins(self):
        common = {*INPUTS, *FALSE_FIELDS, "cleanup", "preflight_pass", "private_inputs_removed",
                  "reason", "result", "schema", "status"}
        for index, row in enumerate(self.rows()):
            self.assertEqual(set(row), common | ({"owner_cleanup"} if index else set()))
            self.assertEqual({name: row[name] for name in INPUTS}, INPUTS)
            self.assertEqual((row["schema"], row["result"], row["status"]),
                             ("nib.selected-service-owner/v1", "fail", "BLOCKED"))
            for name in FALSE_FIELDS:
                self.assertIs(row[name], False, name)

    def test_distinct_original_cleanup_and_reason_are_not_promoted(self):
        recovery = self.receipt(RECOVERY)
        self.assertIs(recovery["cleanup_confirmed"], True)
        self.assertIs(recovery["original_run_pass"], False)
        first, second = self.rows()
        self.assertEqual((first["result"], second["result"]), ("fail", "fail"))
        self.assertEqual((first["reason"], first["cleanup"]), ("weights", "not-started"))
        self.assertIs(first["preflight_pass"], False)
        self.assertIs(first["private_inputs_removed"], False)
        self.assertNotIn("owner_cleanup", first)
        self.assertEqual((second["reason"], second["cleanup"]), ("network", "unconfirmed"))
        self.assertIs(second["preflight_pass"], True)
        self.assertIs(second["private_inputs_removed"], True)
        self.assertIsNone(second["owner_cleanup"])

    def test_recovery_is_only_bounded_cleanup_without_original_run_promotion(self):
        row = self.receipt(RECOVERY)
        true_fields = ("cleanup_confirmed", "container_absent", "network_absent",
                       "creations_resolved", "quarantine_confirmed", "policy_removed",
                       "quarantine_removed")
        false_fields = ("confidential", "deployable", "hardware_validated",
                        "original_run_pass", "resources_created", "runtime_started")
        self.assertEqual(set(row), {*true_fields, *false_fields, "budget_seconds",
                         "contract_sha256", "hard_stop_at", "reason", "result", "schema"})
        self.assertEqual((row["schema"], row["result"], row["reason"]),
                         ("nib.selected-precontainer-recovery/v1", "pass", "completed"))
        self.assertIs(type(row["budget_seconds"]), int)
        self.assertEqual(row["budget_seconds"], 240)
        self.assertEqual(row["hard_stop_at"], "2026-10-25T06:15:00Z")
        self.assertEqual(row["contract_sha256"],
                         "68e7ee54f2a96ac1b20bb7ff4efbc4e6057c154a6f4984148d7759efe6f567d8")
        for names, expected in ((true_fields, True), (false_fields, False)):
            for name in names:
                self.assertIs(row[name], expected, name)
        for original in self.rows():
            self.assertNotIn("contract_sha256", original)

    def test_operator_attributed_producer_sources_remain_reachable(self):
        sources = [(case, "host/record-selected-service.py") for case in CASES]
        sources.append((RECOVERY, "host/recover-selected-service.py"))
        for (_, _, revision, expected), path in sources:
            git(ROOT, "merge-base", "--is-ancestor", revision, "HEAD")
            raw = git(ROOT, "show", revision + ":" + path)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()
