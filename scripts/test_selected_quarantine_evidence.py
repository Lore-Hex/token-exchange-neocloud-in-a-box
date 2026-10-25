# SPDX-License-Identifier: Apache-2.0
"""Content-free originals only; raw host baseline captures remain private."""

from datetime import datetime
import hashlib
import unittest

import selected_host_baseline as baseline
from test_evidence_history import git

ROOT, PROOF = baseline.ROOT, baseline.proof
DIRECTORY = ROOT / "evidence/p2/2026-10-25-selected-quarantine-baseline-pass"
PRODUCER = "3f86b9946155995a690cd6125a459868d8b0e467"
HASHES = {
    "summary.json": "54a8aa789592b08d2909c0b3db3de8d822ed63d6b4384804ba1089740f83160d",
    "observation.json": "f28c90ecfde5a1cb471e260299aa09f3f5d1f21402862e5afd62e9d2cfd12df0",
}


class QuarantineEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.rows = {}
        for name, digest in HASHES.items():
            raw = (DIRECTORY / name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            self.rows[name] = baseline.decode(raw)
        self.summary, self.outer = self.rows["summary.json"], self.rows["observation.json"]
        self.inner = self.outer["observation"]

    def test_exact_original_inventory_and_summary_join(self):
        self.assertEqual({str(path.relative_to(DIRECTORY)) for path in DIRECTORY.rglob("*")},
                         {*HASHES, "README.md"})
        baseline.check_summary(self.summary)
        self.assertEqual((self.summary["result"], self.summary["stage"]), ("pass", "complete"))
        self.assertEqual(self.summary["proof_receipt_sha256"], HASHES["observation.json"])
        for key in ("proof_pass", "namespace_reaped", "cleanup_confirmed", "baseline_complete",
                    "firewall_equal", "dns_equal"):
            self.assertIs(self.summary[key], True)
        for row in (self.summary, self.outer, self.inner):
            for key in ("gpu_used", "published", "deployment_eligible"):
                self.assertIs(row[key], False)
        self.assertIs(self.inner["confidential"], False)

    def test_exact_producer_source_venue_and_existing_image_inputs(self):
        self.assertEqual(self.outer["git_sha"], PRODUCER)
        git(ROOT, "merge-base", "--is-ancestor", PRODUCER, "HEAD")
        self.assertEqual(set(self.outer["source_sha256"]), set(PROOF.SOURCES))
        for path, digest in self.outer["source_sha256"].items():
            self.assertEqual(hashlib.sha256(git(ROOT, "show", PRODUCER + ":" + path)).hexdigest(), digest, path)
        self.assertEqual(hashlib.sha256(git(ROOT, "show", PRODUCER + ":" + baseline.SOURCE)).hexdigest(),
                         "21e7c4825dc9b3b33bdbc73c0711f1b478e2c5431a852c5d3081a21e90923e65")
        self.assertEqual((self.outer["venue_profile"], self.outer["host_python"], self.outer["unshare_version"]),
                         ("ubuntu26-ordinary", "3.14.4", "2.41.3"))
        self.assertEqual(self.outer["venue_manifest_sha256"], self.outer["source_sha256"][PROOF.VENUE_FILE])
        prior = baseline.decode((ROOT / "evidence/p2/2026-10-25-selected-nat-ubuntu26-pass/observation.json").read_bytes())
        self.assertEqual(self.outer["archive_sha256"], prior["archive_sha256"])
        for key in ("engine_version", "image_config", "reference_manifest", "venue_profile"):
            self.assertEqual(self.inner[key], prior["observation"][key])

    def test_quarantine_independence_controls_cleanup_and_original_budget(self):
        PROOF.check_report(self.inner)
        self.assertEqual((self.outer["schema"], self.outer["result"], self.outer["stage"]),
                         ("nib.selected-nat-proof-host/v2", "pass", "complete"))
        self.assertEqual((self.inner["schema"], self.inner["result"], self.inner["stage"]),
                         ("nib.selected-nat-proof/v2", "pass", "complete"))
        self.assertEqual(self.outer["started_at"], "2026-10-25T04:56:01.937660+00:00")
        self.assertEqual(self.outer["ended_at"], "2026-10-25T04:57:04.178771+00:00")
        elapsed = (datetime.fromisoformat(self.outer["ended_at"]) -
                   datetime.fromisoformat(self.outer["started_at"])).total_seconds()
        self.assertGreater(elapsed, 0)
        self.assertLess(elapsed, 480 + 90 + 25)
        PROOF.check_observations(self.inner["positive_controls"], False)
        PROOF.check_observations(self.inner["observations"], True)
        self.assertEqual(len(self.inner["positive_controls"]), 15)
        self.assertEqual({name for name, allowed in self.inner["observations"].items() if allowed},
                         {"tls_host", "tls_forward"})
        quarantine = self.inner["quarantine"]
        self.assertEqual(quarantine["tls_controls"], {"tls_host": True, "tls_forward": True})
        PROOF.check_observations(quarantine["observations"], True, quarantined=True)
        for key in ("installed_verified", "serving_withdrawn", "verified", "removed"):
            self.assertIs(quarantine[key], True)
        for key in ("namespace_verified", "dns_relay_unchanged", "tls_verified", "policy_removed", "cleanup_confirmed"):
            self.assertIs(self.inner[key], True)
        self.assertIs(self.outer["namespace_reaped"], True)


if __name__ == "__main__":
    unittest.main()
