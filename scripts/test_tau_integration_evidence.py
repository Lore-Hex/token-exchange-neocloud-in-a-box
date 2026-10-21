# SPDX-License-Identifier: Apache-2.0
"""Preserved actual scripted integration, never a live model quality claim."""
import hashlib
import json
from pathlib import Path
import unittest

import test_tau_trial as fixtures

ROOT = Path(__file__).resolve().parents[1]
REVISION = "7afb53b2b375318ad1ae2e0ef3fdbf918fca5574"
DIRECTORY = ROOT / "evidence/p2/2026-10-21-tau-integrated-7afb53b"


class TauIntegrationEvidenceTests(unittest.TestCase):
    def test_original_bytes_and_source_binding_preserve_synthetic_scope(self):
        documents = []
        for name, expected in (
                ("run.json", "47fa6e0a388b95dd2643fa7708b06777fe41b6b5f7d40ca5a655107dc566bec8"),
                ("output.log", "f6cd7f62195e025465343ca6970875e440b1f4492259c50419c920086612e047")):
            raw = (DIRECTORY / name).read_bytes()
            self.assertLessEqual(len(raw), 65536)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), expected)
            documents.append(json.loads(raw))
        run, report = documents
        self.assertEqual(run["git_sha"], REVISION)
        self.assertEqual(report["toolkit"]["git_sha"], REVISION)
        self.assertEqual((run["started_utc"], run["ended_utc"]),
                         ("2026-10-21T00:04:21Z", "2026-10-21T00:04:30Z"))
        self.assertEqual((run["result"], report["result"], run["execution"]), ("pass", "pass", "software"))
        self.assertIs(run["hardware_validated"], False)
        self.assertEqual(report["cleanup"], "confirmed")
        self.assertIs(report["cleanup_confirmed"], True)
        diagnostic = report["diagnostic"]
        self.assertIs(diagnostic["upstream_executed"], True)
        self.assertIs(diagnostic["completed"], True)
        for field in ("live_transport", "quality_accepted", "confidential"):
            self.assertIs(report[field], False)
            self.assertIs(diagnostic[field], False)
        self.assertEqual(diagnostic["token_usage"], "synthetic-not-tokenized")
        self.assertEqual(diagnostic["accounting"]["role_requests"], {"agent": 1, "simulator": 2, "evaluator": 1})
        trial_report = diagnostic["report"]
        self.assertEqual(trial_report["kind"], "synthetic-fixture")
        self.assertEqual(trial_report["producer"]["git_sha"], REVISION)
        self.assertEqual(len(trial_report["trials"]), 1)
        trial = trial_report["trials"][0]
        self.assertEqual((trial["status"], trial["requests"], trial["reward"]), ("evaluated", 4, "1.0"))
        self.assertEqual(trial["usage"], {"prompt_tokens": 4, "completion_tokens": 4,
                                         "total_tokens": 8, "cost_usd": "0.00001900"})
        self.assertEqual(len(report["toolkit"]["files"]), 14)
        checker = fixtures.module("check-engine-candidate.py")
        for name, expected in report["toolkit"]["files"].items():
            raw = checker.git(ROOT, "show", REVISION + ":" + name)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), expected)
        self.assertEqual(trial_report["producer"]["adapter_sha256"],
                         report["toolkit"]["files"]["scripts/tau_trial_worker.py"])


if __name__ == "__main__":
    unittest.main()
