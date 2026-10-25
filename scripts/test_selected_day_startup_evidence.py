# SPDX-License-Identifier: Apache-2.0
"""Keep day-profile build/startup evidence distinct from full-run success."""

from datetime import datetime
import hashlib
import json
from pathlib import Path
import unittest

from test_evidence_history import git
from test_selected_owner_failure_evidence import FALSE_FIELDS

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evidence/p2/2026-10-25-selected-day-startup"
SOURCE = "4e0bfd7052d9a1e74d9fa83dda54272efd425351"
HASHES = {
    "build.json": "01adccd8995ec65b7bea04d941c6cf9bec1adb78ba5c2fbbc85610cfa87e379c",
    "failed-owner.json": "2eed893ac1787c3ea4ba773d8cefc58365bd4a12de22ae41d5d1de8c18f9094e",
    "cancelled-startup.json": "3cb4986f69ab1fdfc68778c2070feda9f4396351ce972d13ce2e8b564471d554",
    "r2-startup.json": "22afb9042fd7a0f8dfa70b5c0c011100fbd6742452b58401b87d1cd11f28a73f",
    "r2-probe.json": "59a29341dffc3bf7112895fc6db5fb3dd7e6f1173aba7a2d1ffc1c1939a79407",
}


class DayStartupEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.rows = {}
        for name, digest in HASHES.items():
            raw = (DIRECTORY / name).read_bytes()
            self.assertLess(len(raw), 16384)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            self.assertNotIn(b"PRIVATE KEY", raw)
            self.assertNotIn(b"Bearer ", raw)
            self.rows[name] = json.loads(raw)

    def test_original_inventory_and_completed_build_without_runtime(self):
        self.assertEqual({p.name for p in DIRECTORY.iterdir()}, {*HASHES, "README.md"})
        row = self.rows["build.json"]
        self.assertEqual((row["schema"], row["result"]), ("nib.selected-service-image-build/v1", "pass"))
        for field in ("image_built", "build_completed"):
            self.assertIs(row[field], True)
        for field in ("runtime_started", "published", "deployable", "hardware_validated"):
            self.assertIs(row[field], False)
        self.assertIs(row["base_binding"]["daemon_os_interpreter_trusted"], True)
        self.assertIs(row["base_binding"]["local_layers_rehashed"], False)
        self.assertIs(row["base_binding"]["local_config_bytes_rehashed"], False)

    def test_failed_owner_retains_original_diagnostics_and_no_runtime_claim(self):
        row, build = self.rows["failed-owner.json"], self.rows["build.json"]
        joins = ("image_id", "preparation_sha256", "prepared_sha256")
        self.assertEqual(set(row), {*FALSE_FIELDS, *joins, "build_sha256", "cleanup", "preflight_pass",
                                   "private_inputs_removed", "reason", "result", "schema", "status"})
        self.assertEqual(row["build_sha256"], HASHES["build.json"])
        for field in joins:
            self.assertEqual(row[field], build[field])
        self.assertEqual((row["schema"], row["result"], row["status"], row["reason"], row["cleanup"]),
                         ("nib.selected-service-owner/v1", "fail", "BLOCKED", "not-run", "not-started"))
        self.assertIs(row["preflight_pass"], True)
        self.assertIs(row["private_inputs_removed"], True)
        for field in FALSE_FIELDS:
            self.assertIs(row[field], False)
        git(ROOT, "merge-base", "--is-ancestor", SOURCE, "HEAD")
        source = git(ROOT, "show", SOURCE + ":host/record-selected-service.py")
        self.assertEqual(hashlib.sha256(source).hexdigest(),
                         "bd12d7b5f09feafcbf0647e68c104188bce6847a3d219b9a28da696121453c84")
        for receipt in (row, build):
            for field in ("git_sha", "source_sha256", "started_at", "ended_at", "hard_stop_at", "address"):
                self.assertNotIn(field, receipt)

    def test_cancelled_observer_has_no_tls_credential_or_inference_claim(self):
        row = self.rows["cancelled-startup.json"]
        self.assertEqual((row["schema"], row["result"], row["failure"]),
                         ("nib.selected-external-startup/v1", "fail", "cancelled"))
        self.assertEqual((row["attempts"], row["transport_failures"], row["health_retries"]), (6, 6, 5))
        for field in ("tls_checks", "health_requests", "health_rejections", "attempt_timeouts", "model_requests"):
            self.assertEqual(row[field], 0)
        for field in ("health_ready", "credential_accessed", "ownership_verified", "continued_readiness",
                      "provider_conformance", "quality_eligible", "deployment_eligible", "confidential", "e2ee"):
            self.assertIs(row[field], False)
        self.assertEqual((row["max_seconds"], row["max_attempts"], row["attempt_seconds"]), (1200, 128, 5))
        self.assertEqual(row["backoff_seconds"], [1, 2, 4, 8, 10])
        start, end, deadline = (datetime.fromisoformat(row[field]) for field in ("started_at", "ended_at", "deadline"))
        self.assertLess(start, end)
        self.assertLess(end, deadline)
        self.assertLess((deadline - start).total_seconds(), row["max_seconds"])
        self.assertEqual(row["git_sha"], "0f14583de4e91f2162c14d0fc067872de1668996")

    def test_client_source_closures_and_no_privacy_or_host_ownership_claims(self):
        for name in ("cancelled-startup.json", "r2-startup.json", "r2-probe.json"):
            row = self.rows[name]
            self.assertEqual(len(row["source_sha256"]), 8 if name == "r2-probe.json" else 9)
            for path, digest in row["source_sha256"].items():
                self.assertEqual(hashlib.sha256(git(ROOT, "show", row["git_sha"] + ":" + path)).hexdigest(), digest)
            for field in ("provider_conformance", "quality_eligible", "deployment_eligible", "confidential", "e2ee"):
                self.assertIs(row[field], False)
            for field in ("owner_cleanup", "lifecycle", "image_id", "build_sha256"):
                self.assertNotIn(field, row)

    def test_r2_readiness_is_bounded_credential_free_and_not_continued_readiness(self):
        row = self.rows["r2-startup.json"]
        self.assertEqual((row["schema"], row["result"], row["failure"]),
                         ("nib.selected-external-startup/v1", "pass", "none"))
        self.assertEqual(row["git_sha"], self.rows["cancelled-startup.json"]["git_sha"])
        self.assertEqual(row["target_sha256"], self.rows["cancelled-startup.json"]["target_sha256"])
        self.assertEqual((row["attempts"], row["transport_failures"], row["health_retries"]), (69, 68, 68))
        self.assertEqual((row["tls_checks"], row["health_requests"]), (1, 1))
        for field in ("health_rejections", "attempt_timeouts", "model_requests"):
            self.assertEqual(row[field], 0)
        self.assertIs(row["health_ready"], True)
        for field in ("credential_accessed", "ownership_verified", "continued_readiness"):
            self.assertIs(row[field], False)
        self.assertEqual((row["max_seconds"], row["max_attempts"], row["attempt_seconds"]), (1200, 128, 5))
        self.assertEqual(row["backoff_seconds"], [1, 2, 4, 8, 10])
        start, end, deadline = (datetime.fromisoformat(row[field]) for field in ("started_at", "ended_at", "deadline"))
        self.assertLess(start, end)
        self.assertLess(end, deadline)
        self.assertLess((deadline - start).total_seconds(), row["max_seconds"])

    def test_r2_one_verified_authenticated_stream_with_actual_usage(self):
        row = self.rows["r2-probe.json"]
        self.assertEqual((row["schema"], row["result"], row["failure"]),
                         ("nib.selected-external-smoke/v1", "pass", "none"))
        self.assertEqual(row["git_sha"], "cebbaa21ce38316ea853ffd87d3ac9a8a25d7dce")
        expected = dict.fromkeys(("health", "missing-key", "wrong-key", "stream"), True)
        for field in ("attempts", "checks", "tls_checks"):
            self.assertEqual(row[field], expected)
        self.assertEqual((row["max_authenticated_requests"], row["retries"]), (1, 0))
        self.assertIs(row["tls_peer_verified"], True)
        self.assertIs(row["usage_unknown"], False)
        self.assertIs(row["auth_upstream_calls_measured"], False)
        self.assertEqual(row["usage"], {"prompt_tokens": 36, "completion_tokens": 13})
        meter = row["measurement"]
        self.assertEqual((meter["status"], meter["finish_reason"], meter["output_events"]), ("completed", "stop", 10))
        for field in ("prompt_tokens", "completion_tokens"):
            self.assertEqual(meter[field], row["usage"][field])
        self.assertIsNone(meter["cached_prompt_tokens"])
        self.assertEqual(meter["ttft_seconds"], meter["first_content_seconds"])
        self.assertAlmostEqual(meter["ttft_seconds"], 2.630387583747506)
        self.assertAlmostEqual(meter["elapsed_seconds"], 3.370043291710317)
        start, end = (datetime.fromisoformat(row[field]) for field in ("started_at", "ended_at"))
        self.assertGreater(start, datetime.fromisoformat(self.rows["r2-startup.json"]["ended_at"]))
        self.assertGreater((end - start).total_seconds(), meter["elapsed_seconds"])
        self.assertLess((end - start).total_seconds(), 150)


if __name__ == "__main__":
    unittest.main()
