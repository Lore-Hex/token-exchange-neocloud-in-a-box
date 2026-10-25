# SPDX-License-Identifier: Apache-2.0
"""The startup diagnostic preserves a failure, not inferred readiness."""

import hashlib
import json
from pathlib import Path
import unittest

from test_selected_service_smoke import smoke


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p2/2026-10-24-selected-startup-diagnostic/smoke.json"


class StartupDiagnosticEvidenceTests(unittest.TestCase):
    def test_original_receipt_and_closed_failure(self):
        raw = EVIDENCE.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         "46c2ed87b05ee55fdfa77a77d4170b4845fdafee15241e4f29a39face89a4e12")
        row = json.loads(raw)
        owner = row["owner_failure"]
        self.assertEqual(smoke.lifecycle_failure(json.dumps(owner).encode()), owner)
        self.assertEqual(owner["reason"], "preparation")
        self.assertEqual(row["start_client_exit_code"], 1)
        self.assertEqual(row["result"], "fail")
        self.assertEqual(row["status"], "BLOCKED")
        self.assertEqual(row["cleanup"], "confirmed")
        self.assertIsNone(row["runtime_started"])
        for claim in ("deployable", "hardware_validated", "published", "tools", "reasoning",
                      "zdr", "confidential", "e2ee"):
            self.assertIs(row[claim], False)

    def test_same_original_image_and_preparation(self):
        prior = json.loads((ROOT / "evidence/p2/2026-10-24-selected-service-smoke-failure/smoke.json")
                           .read_bytes())
        row = json.loads(EVIDENCE.read_bytes())
        for key in ("image_id", "build_sha256", "preparation_sha256", "prepared_sha256"):
            self.assertEqual(row[key], prior[key])


if __name__ == "__main__":
    unittest.main()
