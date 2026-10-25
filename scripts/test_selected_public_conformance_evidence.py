# SPDX-License-Identifier: Apache-2.0
"""Replay original public-TLS observations without private endpoint inputs."""

from datetime import datetime
import hashlib
from pathlib import Path
import unittest

from selected_network_policy import decode
from test_evidence_history import git

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evidence/p2/2026-10-25-selected-public-conformance"
PRODUCER = "66f93d4749d79d00a9b4e702c4e6011ba8b540b3"
ORIGINALS = {
    "startup.json": "b47a93fbb364f15122c6f2471790d03e4c9a87cb43494dfd49a9aca4ecd476ed",
    "conformance.json": "8c845851b3798f26d7b02d9b9d28261c91e6be010d6cd12eb4c7e029cb21573f",
}


class PublicConformanceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.rows = {}
        for name, digest in ORIGINALS.items():
            raw = (DIRECTORY / name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            self.assertLess(len(raw), 16384)
            self.assertNotRegex(raw, rb"(?<![A-Za-z0-9])[0-9]{1,3}(?:\.[0-9]{1,3}){3}(?![A-Za-z0-9])")
            for private in (b"Bearer ", b"PRIVATE KEY", b"https://", b"/Users/", b"/root/"):
                self.assertNotIn(private, raw)
            self.rows[name] = decode(raw)

    def test_original_inventory_and_committed_source_closure(self):
        self.assertEqual({p.name for p in DIRECTORY.iterdir()}, {*ORIGINALS, "README.md"})
        git(ROOT, "merge-base", "--is-ancestor", PRODUCER, "HEAD")
        for name, count in (("startup.json", 9), ("conformance.json", 30)):
            row = self.rows[name]
            self.assertEqual(row["git_sha"], PRODUCER)
            self.assertEqual(len(row["source_sha256"]), count)
            for path, digest in row["source_sha256"].items():
                self.assertEqual(hashlib.sha256(git(ROOT, "show", PRODUCER + ":" + path)).hexdigest(), digest)
            self.assertEqual((row["result"], row["failure"], row["tls_mode"]), ("pass", "none", "system-roots"))
            start, end, deadline = (datetime.fromisoformat(row[key]) for key in ("started_at", "ended_at", "deadline"))
            self.assertGreater(end, start)
            self.assertLess(end, deadline)
            self.assertLess((end - start).total_seconds(), row["max_seconds"])
            for field in ("continued_readiness", "ownership_verified", "quality_eligible", "deployment_eligible", "confidential", "e2ee"):
                self.assertIs(row[field], False)

    def test_startup_retries_are_health_only(self):
        row = self.rows["startup.json"]
        self.assertEqual(row["schema"], "nib.selected-external-startup/v2")
        self.assertEqual((row["attempts"], row["health_retries"], row["transport_failures"]), (88, 87, 87))
        self.assertEqual((row["tls_checks"], row["health_requests"], row["attempt_timeouts"], row["health_rejections"]), (1, 1, 0, 0))
        self.assertEqual((row["model_requests"], row["credential_accessed"], row["provider_conformance"]), (0, False, False))
        self.assertIs(row["health_ready"], True)
        self.assertEqual((row["max_seconds"], row["max_attempts"], row["attempt_seconds"]), (1200, 128, 5))
        self.assertEqual(row["backoff_seconds"], [1, 2, 4, 8, 10])

    def test_exact_checker_limits_and_usage_not_quality_or_attestation(self):
        row = self.rows["conformance.json"]
        self.assertEqual(row["schema"], "nib.selected-external-conformance/v1")
        self.assertEqual(row["expected_service_git_sha"], "75b66500621793d668959a5e71baf3f26760eed1")
        self.assertEqual(row["expected_preparation_sha256"], "def1003aa4b1d942760f8f7ec98b35fdd5c87bbb48d80e1db39bb482d342aa07")
        self.assertEqual(row["checker_payload_sha256"], "f32dd82b212b7920aab172754b291a944e270d7fd53ae16ed6f3edf31b33b12e")
        self.assertEqual((row["max_requests"], row["max_authenticated_requests"], row["max_output_tokens"], row["max_seconds"]), (13, 9, 256, 480))
        self.assertEqual((row["retries"], row["redirects"]), (0, 0))
        self.assertEqual(row["checker_result"], "pass")
        self.assertIs(row["all_17_pass"], True)
        self.assertEqual(len(row["checks"]), 17)
        self.assertEqual(set(row["checks"].values()), {"pass"})
        self.assertEqual(row["declared_v2_upstream_status"], "skip")
        cases = {"health", "missing-key", "wrong-key", "catalog", *row["usage"]}
        self.assertEqual(len(cases), 13)
        for name in ("attempts", "tls_checks"):
            self.assertEqual(row[name], dict.fromkeys(cases, True))
        self.assertEqual(row["prechecks"], dict.fromkeys(("health", "missing-key", "wrong-key", "catalog"), True))
        totals = {"prompt_tokens": 0, "completion_tokens": 0}
        for measured in row["usage"].values():
            self.assertIs(measured["usage_unknown"], False)
            for name, value in measured["usage"].items():
                self.assertIs(type(value), int)
                self.assertGreaterEqual(value, 0)
                totals[name] += value
        self.assertEqual(totals, {"prompt_tokens": 280, "completion_tokens": 246})
        self.assertIs(row["client_connections_closed"], True)
        self.assertIs(row["auth_upstream_calls_measured"], False)
        self.assertIs(row["runtime_identity_verified"], False)
        self.assertEqual(row["admission_stop_at"], "2026-10-26T07:50:45Z")
        self.assertGreater(datetime.fromisoformat(row["started_at"]), datetime.fromisoformat(self.rows["startup.json"]["ended_at"]))


if __name__ == "__main__":
    unittest.main()
