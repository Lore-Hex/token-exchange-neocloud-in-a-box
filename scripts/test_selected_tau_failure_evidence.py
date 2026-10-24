# SPDX-License-Identifier: Apache-2.0
"""Original controller readiness failure, not inference or confirmed cleanup."""

import ast
import hashlib
import json
from pathlib import Path
import types
import unittest

from test_evidence_history import git

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p2/2026-10-24-selected-tau-readiness-failure-1736"
PRODUCER = "025b7be189b18900d6ebd860e344a80096069aec"
SOURCE = "scripts/record-tau-selected.py"
SOURCE_SHA = "baf1fc9c2e84be9a5301f571209620c4ddd0917f1c6dc6e2c237a511cf3e7272"
STAMP = "2026-10-24T17:37:00Z"
HASHES = {
    "run.json": (886, "e21582427b3e1ee8e20b7099c69705df0d086951bd5257bace298d6319e589cf"),
    "output.log": (430, "8279ea3f7244dd493b67d907296e69e7f3c63d6a949696ace60d552c077f6257"),
}


class SelectedTauFailureEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.documents = {}
        for name, (size, sha) in HASHES.items():
            raw = (EVIDENCE / name).read_bytes()
            self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), (size, sha))
            self.documents[name] = json.loads(raw)
        self.run, self.report = self.documents["run.json"], self.documents["output.log"]

    def test_original_bytes_and_exact_producer_are_retained(self):
        git(ROOT, "merge-base", "--is-ancestor", PRODUCER, "HEAD")
        for row in (self.run, self.report):
            self.assertEqual(row["git_sha"], PRODUCER)
            self.assertEqual((row["started_utc"], row["ended_utc"]), (STAMP, STAMP))
            self.assertEqual(row["result"], "fail")
        self.assertEqual(self.run["execution"], "hardware")
        self.assertIs(self.run["hardware_validated"], False)

    def test_original_source_defines_the_record_shape(self):
        raw = git(ROOT, "show", PRODUCER + ":" + SOURCE)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), SOURCE_SHA)
        function = next(node for node in ast.parse(raw).body
                        if isinstance(node, ast.FunctionDef) and node.name == "run")
        assignments = {target.id: node.value for node in ast.walk(function)
                       if isinstance(node, ast.Assign) for target in node.targets
                       if isinstance(target, ast.Name) and target.id in ("report", "envelope")}
        self.assertEqual(set(assignments), {"report", "envelope"})
        namespace = {"toolkit": {"git_sha": PRODUCER}, "runner": types.SimpleNamespace(now=lambda: STAMP)}
        # Evaluate only the two hash-pinned dictionary constructors, never the
        # launcher body, imports, filesystem operations or subprocess paths.
        expected = eval(compile(ast.Expression(assignments["report"]), SOURCE, "eval"),
                        {"__builtins__": {}}, namespace)
        updates = [(ast.literal_eval(target.slice), node.value.value)
                   for node in ast.walk(function) if isinstance(node, ast.Assign)
                   and isinstance(node.value, ast.Constant) for target in node.targets
                   if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                   and target.value.id == "report"]
        self.assertIn(("stage", "readiness"), updates)
        self.assertIn(("failure", "selected-operational-run-failed"), updates)
        expected.update(stage="readiness", owner_exits={"candidate": 1, "support": 1},
                        failure="selected-operational-run-failed")
        self.assertEqual(self.report, expected)
        envelope = eval(compile(ast.Expression(assignments["envelope"]), SOURCE, "eval"),
                        {"__builtins__": {}}, {**namespace, "report": expected})
        self.assertEqual(self.run, envelope)

    def test_failure_is_not_upgraded_to_readiness_cleanup_or_quality(self):
        self.assertEqual(self.report["schema"], "nib.tau-selected-run/v1")
        self.assertEqual(self.report["cleanup"], "unconfirmed")
        self.assertEqual(self.report["ready_sha256"], {})
        self.assertIsNone(self.report["owner_reports"])
        self.assertIsNone(self.report["scorer"])
        self.assertEqual(self.report["owner_exits"], {"candidate": 1, "support": 1})
        for name in ("quality_eligible", "confidential", "live_admission"):
            self.assertIs(self.report[name], False)
        self.assertNotIn("cause", self.report)
        self.assertNotIn("message", self.report)


if __name__ == "__main__":
    unittest.main()
