# SPDX-License-Identifier: Apache-2.0
"""Keep original weight preparation evidence separate from runtime claims."""

import datetime
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p2/2026-10-24-selected-weight-view-pass/preparation.json"


class SelectedWeightEvidenceTests(unittest.TestCase):
    def test_original_receipt_and_producer(self):
        raw = EVIDENCE.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         "9417e4f94c4b62cecd6cfcedf438fa848b2e139ab18cbb6aed1684923b9a39e2")
        row = json.loads(raw)
        self.assertEqual(row["producer"], "9fe2adcf1c096387834ef3c7ad2c6e955fc16f2d")
        self.assertEqual(row["result"], "pass")
        self.assertEqual(row["publication"], "atomic-no-replace")
        self.assertIs(row["source_preserved"], True)
        self.assertIs(row["worker_exit_confirmed"], True)

    def test_inventory_modes_and_limits(self):
        row = json.loads(EVIDENCE.read_bytes())
        self.assertEqual((row["files"], row["verified_bytes"]), (79, 510311056155))
        self.assertEqual((row["file_mode"], row["directory_mode"]), ("0444", "0555"))
        self.assertEqual(row["relative_path"], "model")
        duration = (datetime.datetime.fromisoformat(row["ended_utc"])
                    - datetime.datetime.fromisoformat(row["started_utc"])).total_seconds()
        self.assertGreater(duration, 0)
        self.assertLess(duration, row["timeout_seconds"])
        self.assertEqual(row["timeout_seconds"], 2700)
        self.assertIs(row["runtime_ready"], False)
        self.assertIs(row["confidential"], False)


if __name__ == "__main__":
    unittest.main()
