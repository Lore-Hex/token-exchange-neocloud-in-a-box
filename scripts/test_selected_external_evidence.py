# SPDX-License-Identifier: Apache-2.0
"""Replay immutable external observations without host addresses or credentials."""

import ast
from datetime import datetime
import hashlib
from pathlib import Path
import re
import unittest

from selected_network_policy import decode
from test_evidence_history import git

ROOT = Path(__file__).resolve().parent.parent


class _ExternalEvidenceChecks:
    schema_version = 1
    source_revisions = {}

    def setUp(self):
        self.rows = {}
        for name, (revision, digest) in self.originals.items():
            raw = (self.directory / name).read_bytes()
            self.assertLess(len(raw), 16384)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            self.assertNotRegex(raw, rb"(?<![A-Za-z0-9])[0-9]{1,3}(?:\.[0-9]{1,3}){3}(?![A-Za-z0-9])")
            self.assertNotIn(b"Bearer ", raw)
            self.assertNotIn(b"PRIVATE KEY", raw)
            row = decode(raw)
            self.assertEqual(row["git_sha"], revision)
            self.rows[name] = row

    def test_exact_original_inventory_and_historical_named_source_closures(self):
        self.assertEqual({p.relative_to(self.directory).as_posix() for p in self.directory.rglob("*")},
                         {*self.originals, "README.md"})
        for name, row in self.rows.items():
            # Squash merges can retain exact producer files without retaining
            # the producer commit in main's ancestry. Original receipts stay intact.
            revision = self.source_revisions.get(row["git_sha"], row["git_sha"])
            git(ROOT, "merge-base", "--is-ancestor", revision, "HEAD")
            source = ast.parse(git(ROOT, "show", revision + ":scripts/probe-selected-service.py"))
            assignment = next(node for node in source.body if isinstance(node, ast.Assign)
                              and any(isinstance(target, ast.Name) and target.id == "SOURCES"
                                      for target in node.targets))
            paths = set(ast.literal_eval(assignment.value))
            if name == "startup.json":
                paths.add("scripts/wait-selected-service.py")
            self.assertEqual(set(row["source_sha256"]), paths)
            for path, digest in row["source_sha256"].items():
                self.assertEqual(hashlib.sha256(git(ROOT, "show", revision + ":" + path)).hexdigest(), digest)

    def test_bounded_credential_free_startup_then_one_healthy_peer(self):
        row = self.rows["startup.json"]
        self.assertEqual((row["schema"], row["result"], row["failure"]),
                         (f"nib.selected-external-startup/v{self.schema_version}", "pass", "none"))
        if self.schema_version == 2:
            self.assertEqual(row["tls_mode"], "system-roots")
        self.assertEqual((row["attempts"], row["health_retries"], row["transport_failures"]),
                         (self.startup_attempts, self.startup_attempts - 1, self.startup_attempts - 1))
        self.assertEqual((row["tls_checks"], row["health_requests"]), (1, 1))
        self.assertEqual((row["attempt_timeouts"], row["health_rejections"], row["model_requests"]), (0, 0, 0))
        self.assertIs(row["health_ready"], True)
        self.assertIs(row["credential_accessed"], False)
        self.assertEqual((row["max_seconds"], row["max_attempts"], row["attempt_seconds"]), (1200, 128, 5))
        self.assertEqual(row["backoff_seconds"], [1, 2, 4, 8, 10])
        self.assertLess(row["attempts"], row["max_attempts"])
        start, end = (datetime.fromisoformat(row[key]) for key in ("started_at", "ended_at"))
        self.assertGreater((end - start).total_seconds(), 0)
        self.assertLess((end - start).total_seconds(), row["max_seconds"])
        self.assertLess(end, datetime.fromisoformat(row["deadline"]))
        self.assertRegex(row["target_sha256"], re.compile(r"^[0-9a-f]{64}$"))

    def test_all_checks_one_authenticated_stream_actual_usage_and_no_quality_claim(self):
        row = self.rows["probe.json"]
        self.assertEqual((row["schema"], row["result"], row["failure"]),
                         (f"nib.selected-external-smoke/v{self.schema_version}", "pass", "none"))
        if self.schema_version == 2:
            self.assertEqual(row["tls_mode"], "system-roots")
        expected = dict.fromkeys(("health", "missing-key", "wrong-key", "stream"), True)
        for field in ("attempts", "checks", "tls_checks"):
            self.assertEqual(row[field], expected)
        self.assertIs(row["tls_peer_verified"], True)
        self.assertEqual((row["max_authenticated_requests"], row["retries"]), (1, 0))
        self.assertEqual(row["usage"], {"prompt_tokens": 36, "completion_tokens": 13})
        self.assertIs(row["usage_unknown"], False)
        meter = row["measurement"]
        self.assertEqual((meter["status"], meter["finish_reason"], meter["output_events"]), ("completed", "stop", 10))
        self.assertEqual(meter["prompt_tokens"], row["usage"]["prompt_tokens"])
        self.assertEqual(meter["completion_tokens"], row["usage"]["completion_tokens"])
        self.assertEqual(meter["cached_prompt_tokens"], self.cached_prompt_tokens)
        self.assertEqual(meter["ttft_seconds"], meter["first_content_seconds"])
        self.assertAlmostEqual(meter["ttft_seconds"], self.ttft_seconds)
        self.assertAlmostEqual(meter["elapsed_seconds"], self.elapsed_seconds)
        start, end = (datetime.fromisoformat(row[key]) for key in ("started_at", "ended_at"))
        self.assertGreater(start, datetime.fromisoformat(self.rows["startup.json"]["ended_at"]))
        self.assertGreater((end - start).total_seconds(), meter["elapsed_seconds"])
        self.assertLess((end - start).total_seconds(), 150)
        self.assertIs(row["auth_upstream_calls_measured"], False)
        for observed in self.rows.values():
            for field in ("provider_conformance", "quality_eligible", "deployment_eligible", "confidential", "e2ee"):
                self.assertIs(observed[field], False)
        self.assertIs(self.rows["startup.json"]["ownership_verified"], False)
        self.assertIs(self.rows["startup.json"]["continued_readiness"], False)


class ExternalEvidenceTests(_ExternalEvidenceChecks, unittest.TestCase):
    directory = ROOT / "evidence/p2/2026-10-25-selected-external-smoke"
    originals = {
        "startup.json": ("0f14583de4e91f2162c14d0fc067872de1668996",
                         "60ad327e3a7c9df028f335c83a2005f66b67343a54dbb17f921570862ea06fc4"),
        "probe.json": ("cebbaa21ce38316ea853ffd87d3ac9a8a25d7dce",
                       "c216833c7571a71edf37e0e855d3419b34f6fa2adad0cfd049e404cacc0197d7"),
    }
    startup_attempts = 89
    cached_prompt_tokens = None
    ttft_seconds = 2.5979687087237835
    elapsed_seconds = 3.348629292100668


class MillionExternalEvidenceTests(_ExternalEvidenceChecks, unittest.TestCase):
    directory = ROOT / "evidence/p2/2026-10-26-selected-million-external"
    originals = {
        "startup.json": ("317c3f3b3dd4734900148c4d30bbf88a10827627",
                         "2ccf30d6c6541ebffb0ddba6c7f9714353ddc0980c075b0e509def6fdcf7509f"),
        "probe.json": ("317c3f3b3dd4734900148c4d30bbf88a10827627",
                       "d5f8e0c3f2df02d2cb1d40008749af9759283ba44c8bf83b9a31928aa0fc7aa6"),
    }
    startup_attempts = 95
    cached_prompt_tokens = 0
    ttft_seconds = 0.666639500297606
    elapsed_seconds = 0.7810464175418019


class PublicWeeklyExternalEvidenceTests(_ExternalEvidenceChecks, unittest.TestCase):
    directory = ROOT / "evidence/p2/2026-10-27-selected-public-weekly"
    originals = {
        "startup.json": ("0ac1642de8a6c84bbb23ae24b212b54fbd88ac1d",
                         "0e935eaccf19f883a209fbc1f232804d8d69243539ac2cd6174e1abbb582733c"),
        "probe.json": ("0ac1642de8a6c84bbb23ae24b212b54fbd88ac1d",
                       "05537fcd64b60d749fc979b11d2400f3d6168eb37a9cf6e6a1f8e740b740cef2"),
    }
    source_revisions = {
        "0ac1642de8a6c84bbb23ae24b212b54fbd88ac1d":
            "95de501a21a2977e7c7aa6a70695512dd33a41f9",
    }
    schema_version = 2
    startup_attempts = 96
    cached_prompt_tokens = 0
    ttft_seconds = 0.6706833336502314
    elapsed_seconds = 0.7925418755039573


if __name__ == "__main__":
    unittest.main()
