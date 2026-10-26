# SPDX-License-Identifier: Apache-2.0
"""Immutable receipts for the independently admitted long-context graph trial."""

import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest

from test_evidence_history import GIT_ENV, GIT_OPTIONS, git

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evidence/p2/2026-10-26-selected-long-graphs"
SOURCE = "3d496e0cc6964a42dc3c6e3f321f4f7a66499d96"
PROBE_SOURCE = "e03ecbf274c11ac7cde6f220fcb2b641c8f5eb45"
HASHES = {
    "build.json": "c2032dcc8ff028a1b9423e36fa8963bbaad2ff05e9ca083b6cbd3e2259ec6699",
    "preparation.json": "c42e0a6c07a0a7e35e84632b5e4db7be1c3c66baf688bd3969081a2187c2c5eb",
    "input-contract.json": "8637ae20e1b624e2af605a3bc4abd4c050cc55eb83c676a436d8f15db8ac5e51",
    "owner.json": "bd56fa6bf9408bca0b3aa88ab8508271e40c0a97851343e685e67099a5f2b3db",
    "progress.jsonl": "e2ba29c21000ae3679e15e3264e7e74e3c7839012a0c7bb3eba97f3a1f15be1c",
    "serving-progress.jsonl": "de8cbf2c1d074de439ca6e652b5e56c40cde52170fab4599f5ae6bfdb906857f",
    "serving-startup.json": "1799ab5c53d98187e6081f9aef13ce21dcb05417199121dbf20604d66d5a6e40",
    "serving-smoke.json": "934e3d1d48c37aaa01d1adbcf5aaa23d26458ff21b02abd93b449d9f0f17a363",
}


class LongGraphEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.rows = {}
        for name, expected in HASHES.items():
            raw = (DIRECTORY / name).read_bytes()
            self.assertLess(len(raw), 256 << 10)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), expected, name)
            for forbidden in (b"PRIVATE KEY", b"Bearer ", b"/root/nib-owned", b"hixtube"):
                self.assertNotIn(forbidden, raw)
            self.rows[name] = ([json.loads(line) for line in raw.splitlines()]
                               if name.endswith(".jsonl") else json.loads(raw))

    def test_serving_snapshot_is_readiness_not_terminal_cleanup(self):
        progress = self.rows["serving-progress.jsonl"]
        self.assertEqual([item["phase"] for item in progress], ["preparation", "inputs", "weights",
            "engine-starting", "guard-starting", "tls-starting", "ready"])
        self.assertEqual(progress[-1]["observed_at"], "2026-10-26T04:16:55Z")
        for item in progress:
            self.assertEqual(item["schema"], "nib.selected-service-progress/v2")
            self.assertEqual(item["prepared_sha256"], self.rows["build.json"]["prepared_sha256"])
            self.assertEqual(item["execution"], self.rows["owner.json"]["lifecycle"]["execution"])
            self.assertEqual(item["lifetime"], {"admission_stop_at": "2026-10-26T07:24:20Z",
                "hard_stop_at": "2026-10-26T07:57:00Z", "profile": "ordinary-24h"})
        self.assertNotIn("draining", [item["phase"] for item in progress])

    def test_external_observations_bind_the_probe_not_the_runtime_source(self):
        git(ROOT, "merge-base", "--is-ancestor", PROBE_SOURCE, "HEAD")
        for name, count in (("serving-startup.json", 9), ("serving-smoke.json", 8)):
            row = self.rows[name]
            self.assertEqual(row["git_sha"], PROBE_SOURCE)
            self.assertEqual(len(row["source_sha256"]), count)
            for path, expected in row["source_sha256"].items():
                self.assertEqual(hashlib.sha256(git(ROOT, "show", PROBE_SOURCE + ":" + path)).hexdigest(),
                                 expected)
            self.assertEqual((row["result"], row["failure"], row["tls_mode"]),
                             ("pass", "none", "system-roots"))
            for field in ("confidential", "e2ee", "deployment_eligible", "provider_conformance", "quality_eligible"):
                self.assertIs(row[field], False)
        startup = self.rows["serving-startup.json"]
        self.assertEqual(startup["schema"], "nib.selected-external-startup/v2")
        self.assertEqual((startup["attempts"], startup["transport_failures"], startup["health_retries"],
                          startup["health_requests"], startup["tls_checks"]), (42, 41, 41, 1, 1))
        self.assertEqual(startup["ended_at"], "2026-10-26T04:16:57.974753+00:00")
        self.assertTrue(startup["health_ready"])
        self.assertEqual(startup["model_requests"], 0)
        for field in ("credential_accessed", "continued_readiness", "ownership_verified"):
            self.assertIs(startup[field], False)
        smoke = self.rows["serving-smoke.json"]
        self.assertEqual(smoke["schema"], "nib.selected-external-smoke/v2")
        self.assertEqual(smoke["ended_at"], "2026-10-26T04:17:14.790628+00:00")
        for field in ("attempts", "checks", "tls_checks"):
            self.assertEqual(smoke[field], dict.fromkeys(("health", "missing-key", "wrong-key", "stream"), True))
        self.assertTrue(smoke["tls_peer_verified"])
        self.assertFalse(smoke["auth_upstream_calls_measured"])
        self.assertEqual((smoke["max_authenticated_requests"], smoke["retries"], smoke["usage_unknown"]),
                         (1, 0, False))
        self.assertEqual(smoke["usage"], {"prompt_tokens": 36, "completion_tokens": 13})
        measured = smoke["measurement"]
        self.assertEqual((measured["prompt_tokens"], measured["completion_tokens"], measured["cached_prompt_tokens"],
                          measured["output_events"], measured["finish_reason"], measured["status"]),
                         (36, 13, 0, 10, "stop", "completed"))

    def test_secret_scan_exceptions_bind_only_exact_public_hashes(self):
        rules = tomllib.loads((ROOT / ".gitleaks.toml").read_text())["rules"]
        entries = [entry for rule in rules if rule["id"] == "generic-api-key"
                   for entry in rule["allowlists"]
                   if "2026-10-26-selected-long-graphs" in str(entry["paths"])]
        self.assertEqual(len(entries), 2)
        for entry, name in zip(entries, ("input-contract.json", "owner.json")):
            self.assertEqual((entry["condition"], entry["regexTarget"]), ("AND", "match"))
            self.assertEqual(len(entry["paths"]), 1)
            pattern = entry["paths"][0]
            self.assertRegex(str(DIRECTORY / name), pattern)
            self.assertIsNone(re.search(pattern, str(DIRECTORY / (name + ".other"))))
            self.assertIsNone(re.search(pattern, str(ROOT / name)))
            if name == "input-contract.json":
                sources = self.rows[name]["sources"]
                matches = []
                for key, source in (("tokenizer-adapter.py", "engine_lab_bootstrap.py"),
                                    ("tokenizer-parity.py", "engine_tokenizer_parity.py")):
                    raw = git(ROOT, "show", SOURCE + ":scripts/" + source)
                    self.assertEqual(sources[key], hashlib.sha256(raw).hexdigest())
                    matches.append(key + '\":\"' + sources[key] + '\"')
            else:
                cases = self.rows[name]["lifecycle"]["qualification"]["workload"]["cases"]
                matches = ['tokens_sha256\": \"' + row["tokens_sha256"] + '\"' for row in cases]
            for match in matches:
                self.assertTrue(any(re.fullmatch(regex, match) for regex in entry["regexes"]))
                changed = match[:-2] + ("0" if match[-2] != "0" else "1") + match[-1]
                self.assertFalse(any(re.fullmatch(regex, changed) for regex in entry["regexes"]))

    def test_actual_long_output_and_cache_controls(self):
        owner = self.rows["owner.json"]
        result = owner["lifecycle"]["qualification"]
        self.assertEqual((owner["result"], owner["status"], owner["reason"]), ("pass", "PASS", "completed"))
        self.assertEqual((result["result"], result["failure"], len(result["samples"])), ("completed", "none", 10))
        for field in ("checks_pass", "context_retrieval_pass", "cache_reuse_observed",
                      "cold_and_negative_zero_observed", "long_output_limit_reached"):
            self.assertIs(result[field], True)
        for level, (prompt, cached) in enumerate(((32512, 32384), (65280, 65152), (130816, 130688))):
            for index, scope in enumerate(("cold", "warm", "different-namespace")):
                item = result["samples"][3 * level + index]
                self.assertEqual((item["level"], item["scope"]), (level, scope))
                self.assertTrue(item["accepted"] and item["attempted"] and item["resources_closed"]
                                and item["retrieval_match"])
                measured = item["measurement"]
                self.assertEqual((measured["prompt_tokens"], measured["completion_tokens"], measured["status"]),
                                 (prompt, 4, "completed"))
                self.assertEqual(measured["cached_prompt_tokens"], cached if scope == "warm" else 0)
        last = result["samples"][-1]
        self.assertEqual((last["level"], last["scope"], last["requested_output_tokens"]), (3, "long-output", 16384))
        self.assertTrue(last["accepted"] and last["attempted"] and last["resources_closed"])
        measured = last["measurement"]
        self.assertEqual((measured["prompt_tokens"], measured["completion_tokens"], measured["cached_prompt_tokens"],
                          measured["finish_reason"], measured["status"]), (114688, 16384, 0, "length", "completed"))
        self.assertAlmostEqual(measured["elapsed_seconds"], 114.83099303301424)
        self.assertAlmostEqual(measured["ttft_seconds"], 2.4590436939906795)
        self.assertEqual(measured["output_events"], 16381)
        self.assertAlmostEqual(last["decode_tokens_per_second_estimate"],
                               1 / measured["mean_output_token_interval_estimate_seconds"])
        self.assertEqual((result["known_prompt_tokens"], result["known_output_tokens"],
                          result["usage_unknown_requests"], result["retries"]), (800512, 16420, 0, 0))
        self.assertEqual(result["engine_identity_before_sha256"], result["engine_identity_after_sha256"])
        self.assertEqual((result["cache_scope"], result["request_rank_identity"]),
                         ("replica-local-routing-unobserved", "unknown"))
        for field in ("capacity_qualified", "cache_isolation_qualified", "quality_qualified", "confidential", "pricing_measured"):
            self.assertIs(result[field], False)

    def test_final_cleanup_and_progress_bind_to_original_image(self):
        row, build = self.rows["owner.json"], self.rows["build.json"]
        self.assertEqual(row["build_sha256"], HASHES["build.json"])
        for field in ("image_id", "preparation_sha256", "prepared_sha256"):
            self.assertEqual(row[field], build[field])
        self.assertEqual(row["cleanup"], "confirmed")
        self.assertTrue(row["private_inputs_removed"] and row["watchdog_confirmed"])
        self.assertEqual(row["owner_cleanup"], dict.fromkeys(("cleanup_confirmed", "container_absent",
            "creations_resolved", "network_absent", "policy_removed", "quarantine_confirmed"), True))
        inner = row["lifecycle"]
        self.assertTrue(inner["ready"] and inner["cleanup_confirmed"])
        self.assertEqual(inner["cleanup"], [{"role": role, "drained": True, "reaped": True}
                                           for role in ("tls", "guard", "engine")])
        progress = self.rows["progress.jsonl"]
        self.assertEqual([item["phase"] for item in progress], ["preparation", "inputs", "weights",
            "engine-starting", "guard-starting", "tls-starting", "ready", "draining"])
        self.assertEqual((progress[-2]["observed_at"], progress[-1]["observed_at"]),
                         ("2026-10-26T03:47:01Z", "2026-10-26T03:49:36Z"))
        for item in progress:
            self.assertEqual(item["prepared_sha256"], build["prepared_sha256"])
            self.assertEqual(item["execution"], inner["execution"])
            self.assertEqual(item["lifetime"], inner["lifetime"])
        for field in ("confidential", "deployable", "e2ee", "hardware_validated", "published", "zdr"):
            self.assertIs(row[field], False)

    def test_historical_validator_rejects_inflated_usage_and_cache_claims(self):
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
inner = row["lifecycle"]
assert host["lifecycle"](qualification.encoded(inner), lifetime=inner["lifetime"],
    prepared_sha256=row["prepared_sha256"], execution_profile="long128k-graphs", long_qualification=True) == inner
result = inner["qualification"]
assert qualification.validate_report(qualification.encoded(result), row["prepared_sha256"]) == result
for mutate in (lambda r: r.update(known_output_tokens=16421),
               lambda r: r.update(capacity_qualified=True),
               lambda r: r.update(cache_isolation_qualified=True),
               lambda r: r["samples"][2]["measurement"].update(cached_prompt_tokens=1),
               lambda r: r["samples"][-1]["measurement"].update(completion_tokens=16385)):
    changed = copy.deepcopy(result)
    mutate(changed)
    try:
        qualification.validate_report(qualification.encoded(changed), row["prepared_sha256"])
    except qualification.Rejected:
        pass
    else:
        raise AssertionError("false evidence accepted")
print("historical graph replay passed")
'''
            result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", program,
                str(target), str(DIRECTORY / "owner.json")], stdin=subprocess.DEVNULL,
                capture_output=True, timeout=30, check=True)
            self.assertEqual(result.stdout, b"historical graph replay passed\n")
            self.assertEqual(result.stderr, b"")

    def test_original_source_and_preparation_are_bound(self):
        git(ROOT, "merge-base", "--is-ancestor", SOURCE, "HEAD")
        preparation, build = self.rows["preparation.json"], self.rows["build.json"]
        self.assertEqual(preparation["git_sha"], SOURCE)
        self.assertEqual(build["preparation_sha256"], HASHES["preparation.json"])
        self.assertEqual(build["prepared_sha256"], preparation["prepared_sha256"])
        self.assertEqual(len(preparation["files"]), 489)
        self.assertEqual(preparation["files"]["context/payload/lab.json"]["sha256"],
                         HASHES["input-contract.json"])
        checked = 0
        for name, record in preparation["files"].items():
            if name.startswith("context/service/scripts/"):
                source = name.removeprefix("context/service/")
                raw = git(ROOT, "show", SOURCE + ":" + source)
                self.assertEqual(record, {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
                checked += 1
        self.assertGreater(checked, 30)

    def test_exact_selected_profile_does_not_enable_speculation_or_cpu_offload(self):
        row = self.rows["input-contract.json"]
        self.assertEqual(row["git_sha"], SOURCE)
        self.assertEqual((row["execution_profile"], row["gpu_family"], row["gpu_security"],
                          row["data_parallel_size"]), ("long128k-graphs", "B300", "nonconfidential", 2))
        self.assertEqual(row["model_profile"]["hf_revision"], "c19ca5ca4eaeb79d29fdf08aea8327da95b1116a")
        self.assertEqual(row["model_profile"]["total_bytes"], 510311056155)
        self.assertIs(row["confidential"], False)
        arguments = row["arguments"]
        for flag, value in (("--max-model-len", "131072"), ("--max-num-seqs", "1"),
                            ("--max-num-batched-tokens", "4096"), ("--kv-cache-dtype", "fp8"),
                            ("--engram-config", '{"cpu_offload":false}')):
            self.assertEqual(arguments.count(flag), 1)
            self.assertEqual(arguments[arguments.index(flag) + 1], value)
        compilation = json.loads(arguments[arguments.index("--compilation-config") + 1])
        self.assertEqual(compilation, {"pass_config": {"fuse_allreduce_rms": False},
                                      "cudagraph_mode": "FULL_DECODE_ONLY", "cudagraph_capture_sizes": [1]})
        self.assertNotIn("--enforce-eager", arguments)
        self.assertNotIn("--speculative-config", arguments)
        for flag in ("--enable-chunked-prefill", "--enable-prefix-caching", "--enable-prompt-tokens-details"):
            self.assertEqual(arguments.count(flag), 1)

    def test_build_only_receipt_is_not_rewritten_as_runtime_validation(self):
        build = self.rows["build.json"]
        self.assertEqual((build["schema"], build["result"]), ("nib.selected-service-image-build/v1", "pass"))
        self.assertEqual(build["image_id"], "sha256:3346a8d5d741d4bfef1a1f363a9befc78f089d819ad7a26ea48efe16282a70c8")
        for field in ("build_completed", "image_built"):
            self.assertIs(build[field], True)
        for field in ("runtime_started", "hardware_validated", "deployable", "published"):
            self.assertIs(build[field], False)
        self.assertIs(build["base_binding"]["daemon_os_interpreter_trusted"], True)
        self.assertIs(build["base_binding"]["local_config_bytes_rehashed"], False)
        self.assertIs(build["base_binding"]["local_layers_rehashed"], False)


if __name__ == "__main__":
    unittest.main()
