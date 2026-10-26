# SPDX-License-Identifier: Apache-2.0
"""Original long-profile artifacts; configuration is not a measured capacity."""

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

from test_evidence_history import GIT_ENV, GIT_OPTIONS, git

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evidence/p2/2026-10-26-selected-long-qualification"
SOURCE = "e0be185f06c6ebd484cb8906ef6219b44d53f4cc"
PREVIOUS = "75b66500621793d668959a5e71baf3f26760eed1"
HASHES = {
    "build.json": "05a30134269a5848a43e0176e5eaeeedaafba0dd793201ccdabdcedcac0f9ae7",
    "preparation.json": "4e6ee9c4d82b9971bfeeef11aa053ff6836a2a215fc8af8e6cfc698dfdf10ca6",
    "input-contract.json": "10a1a3e91d02577481418d2cdb10d0fb78504df8a1b43d258cba4a4ee1e3ea14",
    "previous-build.json": "03a5a5afcfe002c56b8008af7d7ba0d85c724403708248acf59f72ef8de2d3da",
    "previous-owner.json": "a61052048a17db69d9663a72073b2ff1cf4bee980b943e0ed4f14f1224253aa9",
    "owner.json": "91915a7e1aeebe6e1de12197fef241dd45d1dce041f797758690aa4067ce2e2c",
    "progress.jsonl": "b9a1b62e92a18d6d7aca2c71bd3d19153155f01434078f529235d8c23a83e7c7",
}


class LongEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.rows = {}
        for name, expected in HASHES.items():
            raw = (DIRECTORY / name).read_bytes()
            self.assertLess(len(raw), 256 << 10)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), expected, name)
            self.assertEqual(raw.endswith(b"\n"), name in ("previous-owner.json", "owner.json", "progress.jsonl"))
            for forbidden in (b"PRIVATE KEY", b"Bearer ", b"/root/nib-owned", b"hixtube"):
                self.assertNotIn(forbidden, raw)
            self.rows[name] = ([json.loads(line) for line in raw.splitlines()]
                               if name == "progress.jsonl" else json.loads(raw))

    def test_actual_usage_cache_controls_and_failed_output_are_not_promoted(self):
        owner = self.rows["owner.json"]
        result = owner["owner_failure"]["qualification"]
        self.assertEqual((owner["result"], owner["status"], owner["reason"]), ("fail", "BLOCKED", "lifecycle"))
        self.assertEqual((result["result"], result["failure"], len(result["samples"])), ("fail", "deadline", 10))
        self.assertTrue(result["context_retrieval_pass"] and result["cache_reuse_observed"]
                        and result["cold_and_negative_zero_observed"])
        self.assertFalse(result["checks_pass"] or result["long_output_limit_reached"])
        samples = result["samples"]
        for level, (prompt, cached) in enumerate(((32512, 32384), (65280, 65152), (130816, 130688))):
            for index, scope in enumerate(("cold", "warm", "different-namespace")):
                item = samples[3 * level + index]
                self.assertEqual((item["level"], item["scope"]), (level, scope))
                self.assertTrue(item["accepted"] and item["attempted"] and item["resources_closed"]
                                and item["retrieval_match"])
                measured = item["measurement"]
                self.assertEqual((measured["prompt_tokens"], measured["completion_tokens"], measured["status"]),
                                 (prompt, 4, "completed"))
                self.assertEqual(measured["cached_prompt_tokens"], cached if scope == "warm" else 0)
        last = samples[-1]
        self.assertEqual((last["level"], last["scope"], last["requested_output_tokens"]), (3, "long-output", 16384))
        self.assertTrue(last["attempted"])
        self.assertFalse(last["accepted"] or last["resources_closed"])
        self.assertEqual(last["measurement"]["status"], "timeout")
        self.assertGreaterEqual(last["measurement"]["elapsed_seconds"], 900)
        self.assertLess(last["measurement"]["elapsed_seconds"], 901)
        self.assertEqual(last["measurement"]["output_events"], 12634)
        for field in ("prompt_tokens", "completion_tokens", "cached_prompt_tokens", "finish_reason", "ttft_seconds"):
            self.assertIsNone(last["measurement"][field])
        self.assertEqual((result["known_prompt_tokens"], result["known_output_tokens"], result["usage_unknown_requests"]),
                         (685824, 36, 1))
        self.assertAlmostEqual(result["completed_output_tokens_per_second"], 36 / result["elapsed_seconds"])
        self.assertIsNotNone(result["engine_identity_before_sha256"])
        self.assertIsNone(result["engine_identity_after_sha256"])
        for field in ("capacity_qualified", "cache_isolation_qualified", "quality_qualified", "confidential", "pricing_measured"):
            self.assertIs(result[field], False)

    def test_final_cleanup_and_progress_keep_the_inner_failure(self):
        row, build = self.rows["owner.json"], self.rows["build.json"]
        self.assertEqual(row["build_sha256"], HASHES["build.json"])
        for field in ("image_id", "preparation_sha256", "prepared_sha256"):
            self.assertEqual(row[field], build[field])
        self.assertEqual(row["cleanup"], "confirmed")
        self.assertIsNone(row["runtime_started"])
        self.assertTrue(row["private_inputs_removed"] and row["watchdog_confirmed"])
        self.assertEqual(row["owner_cleanup"], self.rows["previous-owner.json"]["owner_cleanup"])
        inner = row["owner_failure"]
        self.assertTrue(inner["ready"])
        self.assertFalse(inner["cleanup_confirmed"])
        self.assertEqual(inner["cleanup"], [{"role": role, "drained": True, "reaped": True}
                                           for role in ("tls", "guard", "engine")])
        progress = self.rows["progress.jsonl"]
        self.assertEqual([item["phase"] for item in progress], ["preparation", "inputs", "weights",
            "engine-starting", "guard-starting", "tls-starting", "ready", "draining"])
        self.assertEqual((progress[-2]["observed_at"], progress[-1]["observed_at"]),
                         ("2026-10-26T01:34:58Z", "2026-10-26T01:50:48Z"))
        for item in progress:
            self.assertEqual(item["prepared_sha256"], build["prepared_sha256"])
            self.assertEqual(item["execution"], inner["execution"])
            self.assertEqual(item["lifetime"], inner["lifetime"])

    def test_historical_validator_accepts_failure_and_rejects_false_cache_and_usage_claims(self):
        archived = subprocess.run(["git", *GIT_OPTIONS, "-C", str(ROOT), "archive", SOURCE, "scripts", "host"],
            env=GIT_ENV, stdin=subprocess.DEVNULL, capture_output=True, timeout=20, check=True)
        self.assertLess(len(archived.stdout), 16 << 20)
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            with tarfile.open(fileobj=io.BytesIO(archived.stdout)) as archive:
                for item in archive.getmembers():
                    self.assertTrue(item.isfile() or item.isdir())
                    self.assertIn(Path(item.name).parts[0], ("scripts", "host"))
                    self.assertNotIn("..", Path(item.name).parts)
                archive.extractall(target, filter="data")
            program = '''
import copy, json, pathlib, runpy, sys
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / "scripts"))
import selected_long_qualification as qualification
host = runpy.run_path(str(root / "host/record-selected-service-smoke.py"), run_name="historical_replay")
row = json.loads(pathlib.Path(sys.argv[2]).read_bytes())
inner = row["owner_failure"]
assert host["lifecycle_failure"](qualification.encoded(inner), lifetime=inner["lifetime"],
    prepared_sha256=row["prepared_sha256"], execution_profile="long128k-eager", long_qualification=True) == inner
result = inner["qualification"]
assert qualification.validate_report(qualification.encoded(result), row["prepared_sha256"]) == result
for mutate in (lambda r: r.update(checks_pass=True), lambda r: r.update(usage_unknown_requests=0),
               lambda r: r.update(known_output_tokens=16384),
               lambda r: r["samples"][2]["measurement"].update(cached_prompt_tokens=1)):
    changed = copy.deepcopy(result)
    mutate(changed)
    try:
        qualification.validate_report(qualification.encoded(changed), row["prepared_sha256"])
    except qualification.Rejected:
        pass
    else:
        raise AssertionError("false evidence accepted")
print("historical failure replay passed")
'''
            result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", program,
                str(target), str(DIRECTORY / "owner.json")], stdin=subprocess.DEVNULL,
                capture_output=True, timeout=30, check=True)
            self.assertEqual(result.stdout, b"historical failure replay passed\n")
            self.assertEqual(result.stderr, b"")

    def test_source_and_preparation_binding(self):
        preparation, build = self.rows["preparation.json"], self.rows["build.json"]
        self.assertEqual(preparation["git_sha"], SOURCE)
        self.assertEqual(build["preparation_sha256"], HASHES["preparation.json"])
        self.assertEqual(build["prepared_sha256"], preparation["prepared_sha256"])
        self.assertEqual(preparation["files"]["context/payload/lab.json"]["sha256"],
                         HASHES["input-contract.json"])
        for name in ("scripts/selected_long_qualification.py", "scripts/selected_long_tokenizer.py",
                     "scripts/selected_service.py", "scripts/selected_service_process.py"):
            raw = git(ROOT, "show", SOURCE + ":" + name)
            self.assertEqual(preparation["files"]["context/service/" + name],
                             {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})

    def test_build_does_not_claim_runtime_or_attestation(self):
        for name in ("build.json", "previous-build.json"):
            row = self.rows[name]
            self.assertEqual((row["schema"], row["result"]),
                             ("nib.selected-service-image-build/v1", "pass"))
            for field in ("image_built", "build_completed"):
                self.assertIs(row[field], True)
            for field in ("runtime_started", "published", "deployable", "hardware_validated"):
                self.assertIs(row[field], False)
            self.assertIs(row["base_binding"]["daemon_os_interpreter_trusted"], True)
            self.assertIs(row["base_binding"]["local_config_bytes_rehashed"], False)
            self.assertIs(row["base_binding"]["local_layers_rehashed"], False)

    def test_selected_weights_and_c1_nonconfidential_profile(self):
        row = self.rows["input-contract.json"]
        self.assertEqual(row["git_sha"], SOURCE)
        self.assertEqual((row["execution_profile"], row["gpu_family"], row["gpu_security"],
                          row["data_parallel_size"]), ("long128k-eager", "B300", "nonconfidential", 2))
        self.assertEqual(row["model_profile"]["hf_revision"], "c19ca5ca4eaeb79d29fdf08aea8327da95b1116a")
        self.assertEqual(row["model_profile"]["total_bytes"], 510311056155)
        self.assertIs(row["confidential"], False)
        arguments = row["arguments"]
        for flag, value in (("--max-model-len", "131072"), ("--max-num-seqs", "1"),
                            ("--max-num-batched-tokens", "4096"), ("--kv-cache-dtype", "fp8"),
                            ("--engram-config", '{"cpu_offload":false}')):
            self.assertEqual(arguments.count(flag), 1)
            self.assertEqual(arguments[arguments.index(flag) + 1], value)
        for flag in ("--enable-chunked-prefill", "--enable-prefix-caching",
                     "--enable-prompt-tokens-details", "--enforce-eager"):
            self.assertEqual(arguments.count(flag), 1)

    def test_previous_owner_cancellation_preserves_failure(self):
        row, build = self.rows["previous-owner.json"], self.rows["previous-build.json"]
        self.assertEqual((row["result"], row["status"], row["reason"], row["cleanup"]),
                         ("fail", "BLOCKED", "lifecycle", "confirmed"))
        self.assertIsNone(row["runtime_started"])
        self.assertEqual(row["build_sha256"], HASHES["previous-build.json"])
        for field in ("image_id", "preparation_sha256", "prepared_sha256"):
            self.assertEqual(row[field], build[field])
        self.assertEqual(row["owner_cleanup"], dict.fromkeys(("cleanup_confirmed", "container_absent",
            "creations_resolved", "network_absent", "policy_removed", "quarantine_confirmed"), True))
        for field in ("private_inputs_removed", "watchdog_confirmed"):
            self.assertIs(row[field], True)
        for field in ("confidential", "deployable", "e2ee", "hardware_validated", "published", "zdr"):
            self.assertIs(row[field], False)
        self.assertNotIn("lifecycle", row)

    def test_historical_producers_are_not_invented_embedded_provenance(self):
        for revision, name, expected in (
            (SOURCE, "host/record-selected-service.py", "49e2f36cf3d85c270077b43abf2ceaac555b949a92152f16397db6169f89128b"),
            (SOURCE, "scripts/selected_service_image.py", "7a421cddfc360c1329ee0c602332d127a070fc1a70841e141913a220e7795788"),
            (PREVIOUS, "host/record-selected-service.py", "bcbfe247980194c79388337e01d5a33499c1c39271a99dabfb25ce850c772e45"),
        ):
            git(ROOT, "merge-base", "--is-ancestor", revision, "HEAD")
            self.assertEqual(hashlib.sha256(git(ROOT, "show", revision + ":" + name)).hexdigest(), expected)
        for name in ("build.json", "previous-build.json", "previous-owner.json"):
            for field in ("git_sha", "source_revision", "started_at", "ended_at", "hostname", "address", "pid"):
                self.assertNotIn(field, self.rows[name])


if __name__ == "__main__":
    unittest.main()
