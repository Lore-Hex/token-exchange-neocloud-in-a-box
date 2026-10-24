# SPDX-License-Identifier: Apache-2.0
"""Original installer receipts, not a benchmark or qualification result."""

import ast
import hashlib
import json
from pathlib import Path
import types
import unittest

from test_evidence_history import git

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evidence/p2/2026-10-24-lab-buildx-install"
PRODUCER = "4041d6059756c2fba5a0a6aea5bbc8a8ae708c64"
SOURCE = "host/install-lab-buildx.py"
SOURCE_SHA = "fcf18b2714f5ecef13f365a8d764f53de9f07ee920ee1e1eb130da1dc7a1c287"
REPORTS = {
    "buildx-install-4041d60.json": (317, "65e24817cb227f7360ce501204aaa0900e3df22b0f7cae680d1cab406d3ea4f9", True),
    "buildx-idempotent-4041d60.json": (318, "85b0348d9b82a24011592f8e026cb230b4eb4de8c0adeb2e80ce4fca25ff5e3e", False),
}


class BuildxEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.source = git(ROOT, "show", PRODUCER + ":" + SOURCE)
        self.assertEqual(hashlib.sha256(self.source).hexdigest(), SOURCE_SHA)
        self.reports = {}
        for name, (size, sha, _) in REPORTS.items():
            raw = (DIRECTORY / name).read_bytes()
            self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), (size, sha))
            self.reports[name] = json.loads(raw)

    def test_exact_producer_and_original_install_idempotence_are_retained(self):
        git(ROOT, "merge-base", "--is-ancestor", PRODUCER, "HEAD")
        self.assertEqual({path.name for path in DIRECTORY.iterdir()}, {*REPORTS, "README.md"})
        for name, report in self.reports.items():
            self.assertEqual(report["script_sha256"], SOURCE_SHA)
            self.assertEqual(report["result"], "pass")
            self.assertIs(report["installed"], REPORTS[name][2])
            self.assertIs(report["hardware_validated"], False)
            self.assertIs(report["confidential_qualified"], False)
            self.assertNotIn("git_sha", report)
            self.assertNotIn("started_utc", report)

    def test_original_source_pins_and_printed_shape_match_without_installing(self):
        tree = ast.parse(self.source)
        pins = {target.id: ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                for target in node.targets if isinstance(target, ast.Name) and target.id in ("VERSION", "SIZE", "SHA256")}
        self.assertEqual(pins, {"VERSION": "v0.38.1", "SIZE": 68366498,
                              "SHA256": "e25d5cd675092bb5d1d03632fdc46cdf3fede7b514dc5f023a105e00d3ce5ad4"})
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        printed = next(node.value.args[0].args[0] for node in main.body if isinstance(node, ast.Expr)
                       and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                       and node.value.func.id == "print")
        # Evaluate only the pinned report dictionary; no CLI, install or Docker call.
        for name, (_, _, installed) in REPORTS.items():
            namespace = {**pins, "installed": installed, "hashlib": hashlib, "__file__": SOURCE,
                         "Path": lambda path: types.SimpleNamespace(read_bytes=lambda: self.source)}
            expected = eval(compile(ast.Expression(printed), SOURCE, "eval"), {"__builtins__": {}}, namespace)
            self.assertEqual(self.reports[name], expected)


if __name__ == "__main__":
    unittest.main()
