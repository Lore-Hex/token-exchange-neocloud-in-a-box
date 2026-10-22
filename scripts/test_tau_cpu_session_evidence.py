# SPDX-License-Identifier: Apache-2.0
"""Original failed lifecycle evidence, not a parity or admission result."""
import ast
import hashlib
import json
from pathlib import Path
import unittest

from test_evidence_history import git

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evidence/p2/2026-10-22-cpu-session-parity-failure"
PRODUCER = "ea1de780008a7f81b1e54d2df3012c437f109830"
HASHES = {
    "run.json": "ac2587d04ced83db71f05535c41e68868a3f9b3a5dd7fe709def9d56597db4cb",
    "output.log": "49a69f155630fc3cffef5a807d90db959a2b0919f3b99bd9672b8b9ac9ad07f6",
    "pair/run.json": "4d5502ab87123f4ef82895c74449c514db7d1ca10cfc72f76b77a1a66ca6ecfd",
    "pair/output.log": "37e4176ada74c59a4a7eb9d661720cc9527248352a7524f506fa8284101093e6",
}


class CPUSessionEvidenceTests(unittest.TestCase):
    def test_four_originals_unchanged_and_no_missing_receipts_invented(self):
        self.assertEqual({p.relative_to(EVIDENCE).as_posix() for p in EVIDENCE.rglob("*") if p.is_file()},
                         set(HASHES))
        for name, sha in HASHES.items():
            raw = (EVIDENCE / name).read_bytes()
            self.assertLessEqual(len(raw), 65536)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), sha)
            row = json.loads(raw)
            self.assertEqual((row["git_sha"], row["result"]), (PRODUCER, "fail"))
        outer, pair = (json.loads((EVIDENCE / name).read_bytes()) for name in ("run.json", "pair/run.json"))
        self.assertEqual((outer["started_utc"], pair["started_utc"], outer["ended_utc"], pair["ended_utc"]),
                         ("2026-10-22T22:36:07Z", "2026-10-22T22:36:08Z",
                          "2026-10-22T22:41:29Z", "2026-10-22T22:41:29Z"))
        for record in (outer, pair):
            self.assertEqual(record["execution"], "software")
            self.assertFalse(record["hardware_validated"])
            self.assertEqual(record["host"]["gpus"], "none")

    def test_exact_producer_closures_and_unchanged_runtime_guards(self):
        source = git(ROOT, "show", PRODUCER + ":scripts/record-tau-cpu-parity.py")
        assignments = {target.id: node.value for node in ast.parse(source).body if isinstance(node, ast.Assign)
                       for target in node.targets if isinstance(target, ast.Name)}
        closure = {ast.literal_eval(assignments[node.id] if isinstance(node, ast.Name) else node)
                   for node in assignments["SOURCES"].elts}
        self.assertEqual(len(closure), 23)
        for name, expected in (("pair/output.log", closure), ("output.log", closure | {
                "scripts/record-tau-cpu-session.py", ".github/workflows/tau-cpu-session-native.yml",
                "scripts/pilot/export.py", "scripts/e2e/run.py"})):
            row = json.loads((EVIDENCE / name).read_bytes())
            self.assertEqual(set(row["sources"]), expected)
            for path, sha in row["sources"].items():
                self.assertEqual(hashlib.sha256(git(ROOT, "show", PRODUCER + ":" + path)).hexdigest(), sha)
            self.assertEqual(row["source_archive_sha256"],
                             "f5e50c21f54a97afdffa59a20d98cc3907bcf84209876512acb24dde743959ac")
        for path in ("scripts/tau_cpu_parity.py", "scripts/pilot/run.py", "scripts/tau_live_identity.py",
                     "scripts/tau_cpu_parity.Dockerfile", "scripts/record-tau-cpu-parity.py",
                     "scripts/engine_tokenizer_parity.py", "scripts/record-tau-cpu-session.py",
                     ".github/workflows/tau-cpu-session-native.yml"):
            self.assertEqual((ROOT / path).read_bytes(), git(ROOT, "show", PRODUCER + ":" + path))

    def test_failure_does_not_identify_model_comparison_or_exception(self):
        outer, pair = (json.loads((EVIDENCE / name).read_bytes()) for name in ("output.log", "pair/output.log"))
        self.assertEqual(outer["stage"], "producer")
        self.assertIsNone(outer["ready"])
        self.assertEqual(outer["producer_receipts"], {})
        self.assertEqual(outer["cleanup"], {"process_group": "confirmed", "producer": "unknown"})
        self.assertEqual(pair["stage"], "runtime")
        self.assertEqual(pair["cleanup"], dict.fromkeys(("container", "image", "build_completion"), "confirmed"))
        self.assertEqual(pair["runtime"], {"schema": "nib.tau-cpu-session/v1", "result": "fail", "stage": "parity",
            "receipts": {}, "ready_sha256": None, "acknowledged": False, "cleanup": "confirmed",
            "quality_eligible": False, "confidential": False, "live_admission": False})
        self.assertTrue(all(row[key] is False for row in (outer, pair)
                            for key in ("quality_eligible", "confidential", "live_admission")))


if __name__ == "__main__":
    unittest.main()
