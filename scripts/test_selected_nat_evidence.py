# SPDX-License-Identifier: Apache-2.0
"""Immutable failed network observation, never promoted by current diagnostics."""

import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p2/2026-10-25-selected-nat-policy-failure"
SPEC = importlib.util.spec_from_file_location("nat_evidence_producer", ROOT / "scripts/prove-selected-nat.py")
PROOF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROOF)


class SelectedNatEvidenceTests(unittest.TestCase):
    def test_original_bytes_and_explicit_failure_are_preserved(self):
        hashes = {
            "observation.json": "3d5677a9d92b086cc7ecdca7e136382893d0c3db4153e384677dc0e0f7f8488e",
            "bootstrap-check.json": "2e22df142f51f70e8a4b35e802b1ce2f80136ce812e3e44f4aab685b41760cfa",
            "bootstrap-install.json": "814103496d33e9f10cb39ba90618b32d3184a1d0efd0367e0392611623150110",
        }
        rows = {}
        for name, digest in hashes.items():
            raw = (EVIDENCE / name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            rows[name] = json.loads(raw)
        outer = rows["observation.json"]
        self.assertEqual(outer["git_sha"], "742c6a1e4f6920a3001415aa75ee5fd8b2a4a299")
        self.assertEqual(outer["result"], "fail")
        self.assertIs(outer["namespace_reaped"], True)
        inner = outer["observation"]
        PROOF.check_report(inner)
        self.assertEqual((inner["result"], inner["stage"]), ("fail", "policy"))
        self.assertNotIn("policy_step", inner)
        self.assertNotIn("observations", inner)
        self.assertEqual(len(inner["positive_controls"]), 15)
        self.assertTrue(all(inner["positive_controls"].values()))
        for key in ("cleanup_confirmed", "policy_removed", "gpu_used", "deployment_eligible", "confidential", "published"):
            self.assertIs(inner[key], False)
        for name, installed in (("bootstrap-check.json", False), ("bootstrap-install.json", True)):
            row = rows[name]
            self.assertEqual(row["result"], "pass")
            self.assertEqual(row["revision"], outer["git_sha"])
            self.assertIs(row["installed"], installed)
            for key in ("daemon_started", "namespace_proof_executed", "venue_qualified"):
                self.assertIs(row[key], False)


if __name__ == "__main__":
    unittest.main()
