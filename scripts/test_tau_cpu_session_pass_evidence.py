# SPDX-License-Identifier: Apache-2.0
"""Historical held-pair metadata replay, never a current live admission."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import time
import unittest
from unittest import mock

import tau_cpu_session as session
from test_evidence_history import git
import test_tau_cpu_session_evidence as previous

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evidence/p2/2026-10-22-cpu-session-native-pass"
PRODUCER = "b04f5e4ec2b0fd0b1ac24710fc259247bad2a039"
HASHES = dict((
    ("run.json", "75298c343ea3abd31478061b08912e6aef54c7ae4d6e5ba9de76e6ba937722db"),
    ("output.log", "3bd81cfa4a5d5bca66e24b9fc5cf040daf4ab2911051d065956b68cf743242a5"),
    ("pair/run.json", "a2d5cc53517b40af18484dc87f80a10126fd3f6220689e54217ae84de06aa711"),
    ("pair/output.log", "00d89030af95a7752264453b99150f16ab53e32fe43747ccfc31ecd0a533877a"),
    ("pair/simulator-runtime.json", "cab470331a9ddd44db922a6a2db7f741f6e3d5741835f551a3b2c29a04acbbee"),
    ("pair/evaluator-runtime.json", "9469cc8a954380237457a47bb4a4cce902f93daef18d86da0a2e32425011f991"),
))
HASHES.update({"pair/" + role + "-tokenizer.json": sha for role, sha in (
    ("simulator", "de96abc426d3e94cfbc45736e27641c1498220325c57b08306d8eaa98728300f"),
    ("evaluator", "132e5b388cb51278d647d7e1b9627754c5bf4d2537450a749b774314423e430d"),
)})


def read(name):
    return json.loads((EVIDENCE / name).read_bytes())


def packet():
    outer, pair = read("output.log"), read("pair/output.log")
    return {"schema": "nib.tau-cpu-pair-ready/v1", "producer": read("pair/simulator-runtime.json")["producer"],
            "image_sha256": pair["image_id"].removeprefix("sha256:"), "expires_unix": outer["ready"]["expires_unix"],
            "receipts": {name: (EVIDENCE / "pair" / name).read_bytes().decode("ascii") for name in session.FILES}}


class CPUSessionPassEvidenceTests(unittest.TestCase):
    def test_all_eight_original_bytes_and_recorded_bounds(self):
        self.assertEqual({p.relative_to(EVIDENCE).as_posix() for p in EVIDENCE.rglob("*") if p.is_file()},
                         set(HASHES) | {"README.md"})
        for name, sha in HASHES.items():
            raw = (EVIDENCE / name).read_bytes()
            self.assertLessEqual(len(raw), 65536)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), sha)
        for name, started in (("run.json", "2026-10-22T23:20:44Z"), ("pair/run.json", "2026-10-22T23:20:45Z")):
            row = read(name)
            self.assertEqual((row["git_sha"], row["result"], row["execution"]), (PRODUCER, "pass", "software"))
            self.assertEqual((row["started_utc"], row["ended_utc"]), (started, "2026-10-22T23:24:51Z"))
            self.assertFalse(row["hardware_validated"])
            self.assertEqual((row["host"]["gpus"], row["host"]["driver"], row["host"]["cuda"]), ("none", None, None))
        self.assertEqual(read("output.log")["runtime_context"]["platform"], "linux/amd64")

    def test_historical_23_and_27_file_closures_not_later_probe_sources(self):
        source = git(ROOT, "show", PRODUCER + ":scripts/record-tau-cpu-parity.py")
        assignments = {target.id: node.value for node in ast.parse(source).body if isinstance(node, ast.Assign)
                       for target in node.targets if isinstance(target, ast.Name)}
        closure = {ast.literal_eval(assignments[node.id] if isinstance(node, ast.Name) else node)
                   for node in assignments["SOURCES"].elts}
        self.assertEqual(len(closure), 23)
        for name, expected in (("pair/output.log", closure), ("output.log", closure | {
                "scripts/record-tau-cpu-session.py", ".github/workflows/tau-cpu-session-native.yml",
                "scripts/pilot/export.py", "scripts/e2e/run.py"})):
            row = read(name)
            self.assertEqual(row["git_sha"], PRODUCER)
            self.assertEqual(set(row["sources"]), expected)
            for path, sha in row["sources"].items():
                self.assertEqual(hashlib.sha256(git(ROOT, "show", PRODUCER + ":" + path)).hexdigest(), sha)
            self.assertEqual(row["source_archive_sha256"],
                             "d09b4d749c0126c2d1f38e9e310a01cd9b7a0a10b4c1ee929acca4159a97e695")

    def test_closed_runtime_ready_and_original_byte_joins(self):
        outer, pair = read("output.log"), read("pair/output.log")
        runtime = session.validate(pair["runtime"])
        self.assertEqual((runtime["schema"], runtime["result"], runtime["stage"]),
                         ("nib.tau-cpu-session/v2", "pass", "released"))
        self.assertTrue(runtime["acknowledged"])
        self.assertIsNone(runtime["failure"])
        self.assertEqual(runtime["cleanup"], "confirmed")
        self.assertEqual(outer["cleanup"], dict.fromkeys(("producer", "process_group"), "confirmed"))
        self.assertEqual(pair["cleanup"], dict.fromkeys(("container", "image", "build_completion"), "confirmed"))
        for value in (outer, pair, runtime, runtime["progress"]["parity"]):
            self.assertTrue(all(value[key] is False for key in ("quality_eligible", "live_admission", "confidential")))
        self.assertEqual(outer["producer_receipts"], {name: HASHES["pair/" + name] for name in ("run.json", "output.log")})
        value = packet()
        raw = session.probes.encoded(value)
        self.assertEqual(session.tau.digest(raw), outer["ready"]["ready_sha256"])
        self.assertEqual(runtime["ready_sha256"], outer["ready"]["ready_sha256"])
        self.assertEqual(runtime["receipts"], outer["ready"]["receipts"])
        # Schema/provenance replay at recorded completion, not live kernel checks.
        with mock.patch.object(session.time, "time", return_value=1792711491):
            originals, held = session.accept(raw, value["producer"], value["image_sha256"], time.monotonic() + 10)
        self.assertEqual(held, outer["ready"])
        for name, original in originals.items():
            self.assertEqual(original, (EVIDENCE / "pair" / name).read_bytes())
            self.assertEqual(session.tau.digest(original), runtime["receipts"][name])
        with mock.patch.object(session.time, "time", return_value=value["expires_unix"] + 1), self.assertRaises(ValueError):
            session.accept(raw, value["producer"], value["image_sha256"], time.monotonic() + 10)

    def test_both_distinct_models_comparisons_and_recipe_hashes(self):
        rows = read("pair/output.log")["runtime"]["progress"]["parity"]["models"]
        self.assertEqual([row["ready_ms"] for row in rows], [585, 920])
        for field in ("pid", "listener_inode"):
            self.assertEqual(len({row["process"][field] for row in rows}), 2)
        self.assertEqual(len({row["weight_sha256"] for row in rows}), 2)
        for role, row in zip(session.ROLES, rows):
            runtime, tokenizer = read("pair/" + role + "-runtime.json"), read("pair/" + role + "-tokenizer.json")
            self.assertEqual(row["process"], runtime["process"])
            self.assertTrue(row["owner_socket_checked"])
            pinned = session.identity.cpu.identity(row["model"], time.monotonic() + 5)
            self.assertEqual(runtime["serving"]["tokenizer_sha256"], pinned["tokenizer_sha256"])
            self.assertEqual(tokenizer["tokenizer_sha256"], pinned["tokenizer_sha256"])
            for case, count in zip(row["cases"], (20, 28, 160, 198)):
                observed = tokenizer["cases"][case["id"]]
                self.assertEqual(case["result"], "pass")
                self.assertEqual(case["local"], case["server"])
                self.assertEqual(case["local"]["count"], count)
                self.assertEqual(case["usage"], {"prompt_tokens": count, "completion_tokens": 1, "total_tokens": count + 1})
                self.assertEqual(observed["independent_tokens_sha256"], case["local"]["tokens_sha256"])
                self.assertEqual(observed["endpoint_tokens_sha256"], case["server"]["tokens_sha256"])

    def test_claim_and_partial_measurement_mutations_fail(self):
        original = read("pair/output.log")["runtime"]
        for change in (lambda row: row.update(acknowledged=False), lambda row: row.update(cleanup="unknown"),
                       lambda row: row.update(live_admission=True), lambda row: row.update(quality_eligible=True),
                       lambda row: row.update(confidential=True), lambda row: row.update(receipts={}),
                       lambda row: row["progress"]["parity"]["models"][1]["cases"].pop(),
                       lambda row: row["progress"]["parity"]["models"][0].update(owner_socket_checked=False),
                       lambda row: row.update(response_body="private-content-canary")):
            value = copy.deepcopy(original)
            change(value)
            with self.assertRaises((ValueError, session.llama.worker.Abort)):
                session.validate(value)

    def test_prior_failure_is_preserved_and_still_unlocalized(self):
        for name, sha in previous.HASHES.items():
            self.assertEqual(hashlib.sha256((previous.EVIDENCE / name).read_bytes()).hexdigest(), sha)
        runtime = json.loads((previous.EVIDENCE / "pair/output.log").read_bytes())["runtime"]
        self.assertEqual((runtime["result"], runtime["stage"], runtime["receipts"]), ("fail", "parity", {}))
        self.assertNotIn("progress", runtime)
        self.assertNotIn("failure", runtime)


if __name__ == "__main__":
    unittest.main()
