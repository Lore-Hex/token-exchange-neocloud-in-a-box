# SPDX-License-Identifier: Apache-2.0
"""Offline replay of the finite 1M-context hardware result; no claim promotion."""
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
from test_million_prerequisite_evidence import DIRECTORY as PREREQUISITES
from test_million_prerequisite_evidence import HASHES as PREREQUISITE_HASHES, SOURCE

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evidence/p2/2026-10-26-selected-million-pass"
HASHES = {
    "owner.json": "031c6c3e50e72f9bd53eed6e9274165c7c59a29891130626381eb0473aab6e8d",
    "owner-progress.jsonl": "3d9f95d28f71f7367cd7e1eb69094cbb1953b66bf321af3757c0c6fe605c42cc",
    "input-contract.json": "cae2d5b84803a3b161567fa26951685deb1f56aaf91936e93ff1e297e7f915ae",
}


class MillionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.rows = {}
        for name, expected in HASHES.items():
            raw = (DIRECTORY / name).read_bytes()
            self.assertLess(len(raw), 64 << 10)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), expected, name)
            for forbidden in (b"PRIVATE KEY", b"Bearer ", b"/root/", b"hixtube", b"/dev/shm/"):
                self.assertNotIn(forbidden, raw)
            self.rows[name] = ([json.loads(line) for line in raw.splitlines()]
                               if name.endswith(".jsonl") else json.loads(raw))
        self.owner = self.rows["owner.json"]
        self.inner = self.owner["lifecycle"]
        self.result = self.inner["qualification"]

    def test_original_source_preparation_and_image_bindings(self):
        self.assertEqual({p.name for p in DIRECTORY.iterdir()}, {*HASHES, "README.md"})
        git(ROOT, "merge-base", "--is-ancestor", SOURCE, "HEAD")
        previous = {}
        for name in ("build.json", "preparation.json"):
            raw = (PREREQUISITES / name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), PREREQUISITE_HASHES[name])
            previous[name] = json.loads(raw)
        build, preparation = previous["build.json"], previous["preparation.json"]
        self.assertEqual(self.owner["build_sha256"], PREREQUISITE_HASHES["build.json"])
        self.assertEqual(preparation["git_sha"], SOURCE)
        self.assertEqual(preparation["files"]["context/payload/lab.json"]["sha256"], HASHES["input-contract.json"])
        for field in ("image_id", "preparation_sha256", "prepared_sha256"):
            self.assertEqual(self.owner[field], build[field])
        self.assertEqual(self.owner["preparation_sha256"], PREREQUISITE_HASHES["preparation.json"])
        self.assertEqual(self.result["prepared_sha256"], preparation["prepared_sha256"])
        self.assertEqual(self.owner["image_id"],
                         "sha256:c17040e00711efd40d3aaacd46bfc8846e151d2e37b5d26fabfea6173acdc865")
        self.assertEqual(self.rows["input-contract.json"]["git_sha"], SOURCE)

    def test_actual_tokens_cache_and_no_retry(self):
        result = self.result
        self.assertEqual((result["result"], result["failure"], len(result["samples"])), ("completed", "none", 10))
        for field in ("checks_pass", "context_retrieval_pass", "cache_reuse_observed",
                      "cold_and_negative_zero_observed", "long_output_limit_reached"):
            self.assertIs(result[field], True)
        for level, (prompt, cached) in enumerate(((261888, 261760), (524032, 523904), (1048320, 1048192))):
            for offset, scope in enumerate(("cold", "warm", "different-namespace")):
                row = result["samples"][level * 3 + offset]
                self.assertEqual((row["level"], row["scope"]), (level, scope))
                self.assertTrue(all(row[key] for key in ("accepted", "attempted", "resources_closed", "retrieval_match")))
                measured = row["measurement"]
                self.assertEqual((measured["prompt_tokens"], measured["completion_tokens"], measured["status"]),
                                 (prompt, 4, "completed"))
                self.assertEqual(measured["cached_prompt_tokens"], cached if scope == "warm" else 0)
        last = result["samples"][-1]
        measured = last["measurement"]
        self.assertEqual((last["level"], last["scope"], last["requested_output_tokens"]), (3, "long-output", 393216))
        self.assertTrue(all(last[key] for key in ("accepted", "attempted", "resources_closed")))
        self.assertEqual((measured["prompt_tokens"], measured["completion_tokens"], measured["cached_prompt_tokens"],
                          measured["finish_reason"], measured["status"]), (655360, 393216, 0, "length", "completed"))
        self.assertEqual(measured["prompt_tokens"] + measured["completion_tokens"], 1048576)
        self.assertEqual(measured["output_events"], 393202)
        self.assertAlmostEqual(measured["elapsed_seconds"], 2741.3320599950002)
        self.assertAlmostEqual(measured["first_content_seconds"], 14.067063860000417)
        self.assertAlmostEqual(measured["completion_tokens"] / measured["elapsed_seconds"], 143.43975534314407)
        self.assertAlmostEqual(last["decode_tokens_per_second_estimate"],
                               1 / measured["mean_output_token_interval_estimate_seconds"])
        self.assertEqual((result["known_prompt_tokens"], result["known_output_tokens"],
                          result["usage_unknown_requests"], result["retries"]), (6158080, 393252, 0, 0))
        self.assertAlmostEqual(result["elapsed_seconds"], 2857.318467664001)
        self.assertEqual(result["engine_identity_before_sha256"], result["engine_identity_after_sha256"])

    def test_cleanup_and_original_deadline_are_not_an_active_service(self):
        row, inner = self.owner, self.inner
        self.assertEqual((row["result"], row["status"], row["reason"]), ("pass", "PASS", "completed"))
        self.assertEqual((inner["status"], inner["reason"], inner["mode"]), ("retired", "retired", "long-qualification"))
        self.assertEqual(row["cleanup"], "confirmed")
        self.assertTrue(row["private_inputs_removed"] and row["watchdog_confirmed"] and inner["cleanup_confirmed"])
        self.assertEqual(row["owner_cleanup"], dict.fromkeys(("cleanup_confirmed", "container_absent",
            "creations_resolved", "network_absent", "policy_removed", "quarantine_confirmed"), True))
        self.assertEqual(inner["cleanup"], [{"role": role, "drained": True, "reaped": True}
                                           for role in ("tls", "guard", "engine")])
        self.assertEqual(row["lifetime"]["owner_hard_stop_at"], "2026-10-27T18:00:00Z")
        progress = self.rows["owner-progress.jsonl"]
        self.assertEqual([r["phase"] for r in progress], ["preparation", "inputs", "weights",
            "engine-starting", "guard-starting", "tls-starting", "ready", "draining"])
        self.assertEqual((progress[0]["observed_at"], progress[-2]["observed_at"], progress[-1]["observed_at"]),
                         ("2026-10-26T21:08:24Z", "2026-10-26T21:24:09Z", "2026-10-26T22:11:47Z"))
        for entry in progress:
            self.assertEqual(entry["schema"], "nib.selected-service-progress/v2")
            self.assertEqual(entry["prepared_sha256"], row["prepared_sha256"])
            self.assertEqual(entry["execution"], row["execution"])
            self.assertEqual(entry["lifetime"], inner["lifetime"])

    def test_no_broader_qualification_or_changed_engine_controls(self):
        for field in ("hardware_validated", "published", "deployable", "confidential", "e2ee", "zdr"):
            self.assertIs(self.owner[field], False)
        for field in ("capacity_qualified", "cache_isolation_qualified", "quality_qualified", "confidential", "pricing_measured"):
            self.assertIs(self.result[field], False)
        self.assertEqual((self.result["cache_scope"], self.result["request_rank_identity"]),
                         ("replica-local-routing-unobserved", "unknown"))
        contract = self.rows["input-contract.json"]
        self.assertEqual((contract["execution_profile"], contract["gpu_family"], contract["gpu_security"],
                          contract["data_parallel_size"]), ("long1m-graphs", "B300", "nonconfidential", 2))
        args = contract["arguments"]
        for flag, value in (("--max-model-len", "1048576"), ("--max-num-seqs", "1"),
                            ("--max-num-batched-tokens", "4096"), ("--kv-cache-dtype", "fp8"),
                            ("--engram-config", '{"cpu_offload":false}')):
            self.assertEqual(args.count(flag), 1)
            self.assertEqual(args[args.index(flag) + 1], value)
        for flag in ("--enforce-eager", "--speculative-config", "--ignore-eos"):
            self.assertNotIn(flag, args)
        for flag in ("--enable-chunked-prefill", "--enable-prefix-caching", "--enable-prompt-tokens-details"):
            self.assertEqual(args.count(flag), 1)

    def test_original_validators_reject_inflated_or_incomplete_evidence(self):
        archive = subprocess.run(["git", *GIT_OPTIONS, "-C", str(ROOT), "archive", SOURCE, "scripts", "host"],
            env=GIT_ENV, stdin=subprocess.DEVNULL, capture_output=True, timeout=20, check=True)
        self.assertLess(len(archive.stdout), 16 << 20)
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as bundle:
                for member in bundle.getmembers():
                    self.assertTrue(member.isfile() or member.isdir())
                    self.assertIn(Path(member.name).parts[0], ("scripts", "host"))
                    self.assertNotIn("..", Path(member.name).parts)
                bundle.extractall(target, filter="data")
            program = '''
import copy, json, pathlib, runpy, sys
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / "scripts"))
import selected_long_qualification as q
host = runpy.run_path(str(root / "host/record-selected-service-smoke.py"), run_name="historical_replay")
row = json.loads(pathlib.Path(sys.argv[2]).read_bytes())
inner = row["lifecycle"]
assert host["lifecycle"](q.encoded(inner), lifetime=inner["lifetime"], prepared_sha256=row["prepared_sha256"],
    execution_profile="long1m-graphs", long_qualification=True) == inner
result = inner["qualification"]
assert q.validate_report(q.encoded(result), row["prepared_sha256"], execution_profile="long1m-graphs") == result
for mutate in (lambda r: r.update(known_output_tokens=393253),
               lambda r: r.update(capacity_qualified=True),
               lambda r: r.update(cache_isolation_qualified=True),
               lambda r: r.update(confidential=True),
               lambda r: r.update(retries=1),
               lambda r: r["samples"][2]["measurement"].update(cached_prompt_tokens=1),
               lambda r: r["samples"][-1]["measurement"].update(completion_tokens=393217),
               lambda r: r["samples"][-1]["measurement"].update(finish_reason="stop")):
    changed = copy.deepcopy(result)
    mutate(changed)
    try:
        q.validate_report(q.encoded(changed), row["prepared_sha256"], execution_profile="long1m-graphs")
    except q.Rejected:
        pass
    else:
        raise AssertionError("false evidence accepted")
print("historical million replay passed")
'''
            result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", program,
                str(target), str(DIRECTORY / "owner.json")], stdin=subprocess.DEVNULL,
                capture_output=True, timeout=30, check=True)
            self.assertEqual(result.stdout, b"historical million replay passed\n")
            self.assertEqual(result.stderr, b"")

    def test_scanner_exceptions_are_exact_public_hashes_not_credentials(self):
        rules = tomllib.loads((ROOT / ".gitleaks.toml").read_text())["rules"]
        entries = [entry for rule in rules if rule["id"] == "generic-api-key" for entry in rule["allowlists"]
                   if "2026-10-26-selected-million-pass" in str(entry["paths"])]
        self.assertEqual(len(entries), 2)
        for entry, name in zip(entries, ("input-contract.json", "owner.json")):
            self.assertEqual((entry["condition"], entry["regexTarget"]), ("AND", "match"))
            self.assertEqual(len(entry["paths"]), 1)
            self.assertRegex(str(DIRECTORY / name), entry["paths"][0])
            self.assertIsNone(re.search(entry["paths"][0], str(DIRECTORY / (name + ".other"))))
            self.assertIsNone(re.search(entry["paths"][0], str(ROOT / name)))
            if name == "input-contract.json":
                matches = []
                for key, source in (("tokenizer-adapter.py", "engine_lab_bootstrap.py"),
                                    ("tokenizer-parity.py", "engine_tokenizer_parity.py")):
                    digest = hashlib.sha256(git(ROOT, "show", SOURCE + ":scripts/" + source)).hexdigest()
                    self.assertEqual(self.rows[name]["sources"][key], digest)
                    matches.append(key + '\":\"' + digest + '\"')
            else:
                matches = ['tokens_sha256\": \"' + r["tokens_sha256"] + '\"'
                           for r in self.result["workload"]["cases"]]
            for match in matches:
                self.assertTrue(any(re.fullmatch(regex, match) for regex in entry["regexes"]))
                changed = match[:-2] + ("0" if match[-2] != "0" else "1") + match[-1]
                self.assertFalse(any(re.fullmatch(regex, changed) for regex in entry["regexes"]))


if __name__ == "__main__":
    unittest.main()
