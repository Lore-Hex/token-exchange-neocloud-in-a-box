# SPDX-License-Identifier: Apache-2.0
"""Immutable native CPU observations; no model execution or live admission."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import unittest

import tau_cpu_parity as cpu
from test_evidence_history import git

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evidence/p2/2026-10-22-cpu-parity-native-pass"
PRODUCER = "0a89d7f5193fab48f1b1e3cfa8e88fd772436f4f"


class CPUParityEvidenceTests(unittest.TestCase):
    def report(self):
        return json.loads((EVIDENCE / "output.log").read_bytes())

    def test_original_receipts_and_terminal_claims_are_unchanged(self):
        for name, sha in (("run.json", "25b871a3653381c2ccb2195ff82ba6c329e7242723b2f7956beae8de6033f94e"),
                          ("output.log", "392863195c43d19c5f871b76ba47363e6442cb5b0ac2ea32c3c3593014be137f")):
            raw = (EVIDENCE / name).read_bytes()
            self.assertLessEqual(len(raw), 65536)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), sha)
            row = json.loads(raw)
            self.assertEqual((row["git_sha"], row["result"]), (PRODUCER, "pass"))
        record = json.loads((EVIDENCE / "run.json").read_bytes())
        self.assertEqual((record["started_utc"], record["ended_utc"]),
                         ("2026-10-22T21:33:14Z", "2026-10-22T21:38:39Z"))
        self.assertEqual(record["execution"], "software")
        self.assertFalse(record["hardware_validated"])
        self.assertEqual(record["host"]["provider"], "github-actions")
        self.assertEqual(record["host"]["gpus"], "none")
        self.assertEqual(row["stage"], "complete")
        self.assertEqual(row["cleanup"], dict.fromkeys(("container", "image", "build_completion"), "confirmed"))
        self.assertTrue(all(row[key] is False for key in ("quality_eligible", "live_admission", "confidential")))

    def test_original_21_file_closure_not_later_producer_is_bound(self):
        row = self.report()
        source = git(ROOT, "show", PRODUCER + ":scripts/record-tau-cpu-parity.py")
        assignments = {target.id: node.value for node in ast.parse(source).body if isinstance(node, ast.Assign)
                       for target in node.targets if isinstance(target, ast.Name)}
        closure = {ast.literal_eval(assignments[node.id] if isinstance(node, ast.Name) else node)
                   for node in assignments["SOURCES"].elts}
        self.assertEqual(len(closure), 21)
        self.assertEqual(set(row["sources"]), closure)
        for path, sha in row["sources"].items():
            self.assertEqual(hashlib.sha256(git(ROOT, "show", PRODUCER + ":" + path)).hexdigest(), sha)
        pins = json.loads(git(ROOT, "show", PRODUCER + ":scripts/e2e/pins.json"))
        self.assertEqual(row["base_images"], {key: pins[key] for key in ("go_image", "python_image")})
        models = json.loads(git(ROOT, "show", PRODUCER + ":scripts/tau_cpu_support_models.json"))
        self.assertEqual(row["runtime"]["engine_revision"], models["engine"]["revision"])
        for item in row["runtime"]["models"]:
            self.assertEqual(item["weight_sha256"], models["models"][item["model"]]["sha256"])

    def test_both_distinct_models_pass_four_unchanged_probes_and_strict_validator(self):
        report = self.report()["runtime"]
        self.assertIs(cpu.validate(report), report)
        self.assertIsNone(report["failure"])
        self.assertEqual([model["model"] for model in report["models"]],
                         ["qwen/qwen3-0.6b", "qwen/qwen3-1.7b"])
        self.assertEqual(len({model["weight_sha256"] for model in report["models"]}), 2)
        self.assertEqual([model["ready_ms"] for model in report["models"]], [812, 1610])
        for model in report["models"]:
            self.assertTrue(model["owner_socket_checked"])
            self.assertEqual(model["process"]["exe_sha256"], report["binaries"]["server"])
            self.assertEqual([case["id"] for case in model["cases"]], list(cpu.probes.IDS))
            for case, count in zip(model["cases"], (20, 28, 160, 198)):
                self.assertEqual(case["result"], "pass")
                self.assertEqual(case["local"], case["server"])
                self.assertEqual(case["local"]["count"], count)
                self.assertEqual(case["usage"], {"prompt_tokens": count, "completion_tokens": 1,
                                                 "total_tokens": count + 1})

    def test_strict_validator_still_rejects_changed_actual_result(self):
        original = self.report()["runtime"]
        changes = (lambda row: row.update(live_admission=True),
                   lambda row: row.update(quality_eligible=True),
                   lambda row: row.update(confidential=True),
                   lambda row: row.update(models=row["models"][:1]),
                   lambda row: row["models"][0].update(owner_socket_checked=False),
                   lambda row: row["models"][0]["process"].update(exe_sha256="0" * 64),
                   lambda row: row["models"][0].update(weight_sha256="0" * 64),
                   lambda row: row["models"][0]["cases"][0]["server"].update(tokens_sha256="0" * 64),
                   lambda row: row["models"][0]["cases"][0]["usage"].update(prompt_tokens=21),
                   lambda row: row.update(response_body="synthetic-private-canary"))
        for change in changes:
            altered = copy.deepcopy(original)
            change(altered)
            with self.assertRaises((ValueError, cpu.llama.worker.Abort)):
                cpu.validate(altered)


if __name__ == "__main__":
    unittest.main()
