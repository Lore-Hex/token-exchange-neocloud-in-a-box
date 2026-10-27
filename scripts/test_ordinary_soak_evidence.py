# SPDX-License-Identifier: Apache-2.0
"""Replay the original short observer receipts without contacting any endpoint."""

import ast
from datetime import datetime
import hashlib
from pathlib import Path
import unittest

from selected_network_policy import decode
from test_evidence_history import git

ROOT = Path(__file__).resolve().parent.parent
DIRECTORY = ROOT / "evidence/p2/2026-10-27-ordinary-short-observer"
REVISION = "fd380d29456816f329dbffca0c46a67c4c6530f8"
ORIGINALS = {
    "run.json": "92c6d36597f24f3a39831ba5d8271dcd85bfab1f488824e1446c610cf4c35c74",
    "0000-intent.json": "8e93b7a65eae279f561ebc3a54ef0ffb3a4c0e5fdff689d7b218506b70fa7970",
    "0000-sample.json": "23fb7364e9fb63d1e004e54771daa96bb3a32a04cdb011656deb558c32013516",
    "terminal.json": "b166eeeb4525988a178a4a4b5c1df14d8095420c57ac200e0fba670917651166",
}
CLAIMS = ("continued_readiness", "provider_conformance", "quality_eligible",
          "deployment_eligible", "confidential", "e2ee", "peak_load_soak",
          "phase_complete", "server_ownership_verified")


class ShortObserverEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.rows = {}
        previous = None
        for ordinal, (name, digest) in enumerate(ORIGINALS.items()):
            raw = (DIRECTORY / name).read_bytes()
            self.assertLess(len(raw), 32768)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            self.assertNotRegex(raw, rb"(?<![A-Za-z0-9])[0-9]{1,3}(?:\.[0-9]{1,3}){3}(?![A-Za-z0-9])")
            for marker in (b"Bearer ", b"PRIVATE KEY", b"https://", b"/Users/"):
                self.assertNotIn(marker, raw)
            row = decode(raw)
            self.assertEqual(row["schema"], "nib.ordinary-soak-receipt/v1")
            self.assertEqual(row["ordinal"], ordinal)
            self.assertEqual(row["previous_sha256"], previous)
            self.rows[name] = row["payload"]
            previous = digest

    def test_original_inventory_and_historical_source_closure(self):
        self.assertEqual({p.name for p in DIRECTORY.iterdir()}, {*ORIGINALS, "README.md"})
        git(ROOT, "merge-base", "--is-ancestor", REVISION, "HEAD")
        source = ast.parse(git(ROOT, "show", REVISION + ":scripts/probe-selected-service.py"))
        assignment = next(node for node in source.body if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == "SOURCES"
                                  for target in node.targets))
        paths = set(ast.literal_eval(assignment.value))
        run, probe = self.rows["run.json"], self.rows["0000-sample.json"]["probe"]
        for row, expected in ((run, paths | {"scripts/soak-selected-service.py"}), (probe, paths)):
            self.assertEqual(row["git_sha"], REVISION)
            self.assertEqual(set(row["source_sha256"]), expected)
            for path, digest in row["source_sha256"].items():
                self.assertEqual(hashlib.sha256(git(ROOT, "show", REVISION + ":" + path)).hexdigest(), digest)

    def test_one_sample_and_full_short_window_without_extended_claims(self):
        run, intent, sample, terminal = (self.rows[name] for name in ORIGINALS)
        self.assertEqual(run["schema"], "nib.ordinary-soak-contract/v1")
        self.assertEqual(terminal["schema"], "nib.ordinary-serving-soak/v1")
        self.assertEqual((run["duration_seconds"], run["interval_seconds"], run["max_samples"]), (300, 300, 1))
        self.assertEqual((run["max_http_requests"], run["max_model_posts"], run["max_authenticated_requests"]), (4, 3, 1))
        self.assertEqual((run["concurrency"], run["max_output_tokens_per_request"], run["retries"]), (1, 256, 0))
        for row in (intent, sample, terminal):
            self.assertEqual(row["run_sha256"], ORIGINALS["run.json"])
        self.assertEqual((intent["sample"], sample["sample"]), (0, 0))
        self.assertEqual((intent["max_http_requests"], intent["max_authenticated_requests"]), (4, 1))
        start = datetime.fromisoformat(run["started_at"])
        cutoff = datetime.fromisoformat(run["original_cutoff_utc"])
        ended = datetime.fromisoformat(terminal["ended_at"])
        self.assertEqual((cutoff - start).total_seconds(), 300)
        self.assertGreaterEqual(ended, cutoff)
        self.assertLess(ended, datetime.fromisoformat(run["authorized_not_after"]))
        self.assertGreaterEqual(terminal["elapsed_seconds"], 300)
        self.assertLess(terminal["elapsed_seconds"], 305)
        self.assertEqual((terminal["result"], terminal["failure"]), ("pass", "none"))
        self.assertEqual((terminal["samples_started"], terminal["samples_completed"], terminal["samples_passed"]), (1, 1, 1))
        self.assertEqual((terminal["samples_failed"], terminal["unknown_usage_samples"], terminal["retries"]), (0, 0, 0))
        self.assertEqual((terminal["http_requests_attempted"], terminal["model_posts_attempted"],
                          terminal["authenticated_requests_attempted"]), (4, 3, 1))
        self.assertEqual((terminal["known_prompt_tokens"], terminal["known_completion_tokens"]), (36, 13))
        self.assertEqual((terminal["potential_unrecorded_http_requests"],
                          terminal["potential_unrecorded_authenticated_requests"]), (0, 0))
        for field in ("window_completed", "receipts_complete", "client_operations_returned"):
            self.assertIs(terminal[field], True)
        for row in (run, terminal):
            for field in CLAIMS:
                self.assertIs(row[field], False)
        for field in ("remote_cancellation_verified", "server_resources_modified", "server_teardown_performed"):
            self.assertIs(terminal[field], False)
        # A receipt cannot contain its own final digest; the envelope chain is
        # checked above against the independently retained original file hashes.
        self.assertIsNone(terminal["terminal_sha256"])

    def test_verified_tls_rejected_keys_and_known_stream_usage(self):
        probe = self.rows["0000-sample.json"]["probe"]
        self.assertEqual((probe["schema"], probe["result"], probe["failure"]),
                         ("nib.selected-external-smoke/v2", "pass", "none"))
        self.assertEqual(probe["tls_mode"], "system-roots")
        self.assertIs(probe["tls_peer_verified"], True)
        expected = dict.fromkeys(("health", "missing-key", "wrong-key", "stream"), True)
        for field in ("checks", "attempts", "tls_checks"):
            self.assertEqual(probe[field], expected)
        self.assertEqual(probe["usage"], {"prompt_tokens": 36, "completion_tokens": 13})
        self.assertIs(probe["usage_unknown"], False)
        self.assertIs(probe["auth_upstream_calls_measured"], False)
        self.assertEqual((probe["max_authenticated_requests"], probe["retries"]), (1, 0))
        meter = probe["measurement"]
        self.assertEqual((meter["status"], meter["finish_reason"], meter["cached_prompt_tokens"]), ("completed", "stop", 0))
        self.assertEqual((meter["prompt_tokens"], meter["completion_tokens"]), (36, 13))
        self.assertAlmostEqual(meter["ttft_seconds"], 0.6269382499158382)
        self.assertAlmostEqual(meter["elapsed_seconds"], 0.7871843343600631)
        self.assertEqual(meter["ttft_seconds"], meter["first_content_seconds"])
        for field in ("provider_conformance", "quality_eligible", "deployment_eligible", "confidential", "e2ee"):
            self.assertIs(probe[field], False)


if __name__ == "__main__":
    unittest.main()
