# SPDX-License-Identifier: Apache-2.0
"""Original known-tool rejection evidence, never promoted to a quality pass."""

import hashlib
import json
from pathlib import Path
import types
import unittest

from test_evidence_history import git
from test_selected_tau_failure_evidence import historical_cpu

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evidence/p2/2026-10-25-selected-tau-tool-rejection-0245"
PRODUCER = "76013ab9d7da6a98d35d7bae0a7dc5eead494737"
HASHES = {
    "cpu/evaluator-runtime.json": (3088, "c366c5b79d003e3cd96e0ae710abc75a39508a094f615f704d23e4c3e7933db4"),
    "cpu/evaluator-tokenizer.json": (1935, "b18f504ad5436175a17b5a72258e698a9ad85a06c4d83bd32e20e47decb7e19f"),
    "cpu/output.log": (12211, "d8bb724275fa904013a8e336e8c2beef099120dbbc2062886215b4b6a54fd59a"),
    "cpu/run.json": (737, "35f20448b9e0d3f653189f5445905016e89319d97e955c18bd5b1fb1ffbbba39"),
    "cpu/simulator-runtime.json": (3087, "cf7865a6fad44eb6d99bbbc0ec3077e3d4a0b499de28b75cf72d57d8af07e4c4"),
    "cpu/simulator-tokenizer.json": (1935, "7366286c4deaa828cebdbabbb25ae69e2a08751c87e76dcd9c7bfb5be8eb5d6e"),
    "evidence/output.log": (6319, "16af522738edc8c22f0582adfcbab87b09641f1a093835184b4d43b4f95b309d"),
    "evidence/run.json": (1049, "1f4f2180d1a11d2327d84118cec431b63e39523f8b54e4fb5fba85c6ec66165b"),
    "gpu/output.log": (10575, "d24185cb98a8cfa8f16002fd1392ddd925befe7c681afe5a597ae39e15d47590"),
    "gpu/run.json": (1078, "bf1ffcca5994edb599a93c4fc917ce374fc3e7baec2abcbb6493324c36ca243a"),
    "scorer/output.log": (5416, "6bf3abb83a5c56ff17b3e2502daf5c7222a5f463af2d22d1956d8eaaa78692f3"),
    "scorer/run.json": (668, "57f1a05eefb4fef5a3de8c8e003bb92b139b7e17696bfcf43f67ba9eb87d246f"),
}


class AttributionAssertions:
    directory = DIRECTORY
    producer = PRODUCER
    hashes = HASHES
    source_count = 36
    elapsed_ms = 24821
    prompt_tokens = 16093
    completion_tokens = 226
    completed_requests = 3
    started = "2026-10-25T02:45:33Z"
    ended = "2026-10-25T03:04:53Z"
    origin = {}

    def setUp(self):
        self.rows = {}
        for name, expected in self.hashes.items():
            raw = (self.directory / name).read_bytes()
            self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), expected)
            self.rows[name] = json.loads(raw)
        self.gpu, self.cpu, self.scorer, self.controller = (
            self.rows[name + "/output.log"] for name in ("gpu", "cpu", "scorer", "evidence"))

    def assert_original_inventory_and_sources(self):
        git(ROOT, "merge-base", "--is-ancestor", self.producer, "HEAD")
        self.assertEqual({str(p.relative_to(self.directory)) for p in self.directory.rglob("*") if p.is_file()},
                         set(self.hashes) | {"README.md"})
        for owner in ("gpu", "cpu", "scorer", "evidence"):
            row = self.rows[owner + "/run.json"]
            self.assertEqual((row["git_sha"], row["result"], row["hardware_validated"]), (self.producer, "fail", False))
        files = self.scorer["toolkit"]["files"]
        self.assertEqual(len(files), self.source_count)
        for path, digest in files.items():
            self.assertEqual(hashlib.sha256(git(ROOT, "show", self.producer + ":" + path)).hexdigest(), digest)

    def test_exact_inventory_sources_and_failed_trial(self):
        self.assert_original_inventory_and_sources()
        worker = types.ModuleType("original_attribution_worker")
        worker.__file__ = str(ROOT / "scripts/tau_trial_worker.py")
        exec(compile(git(ROOT, "show", self.producer + ":scripts/tau_trial_worker.py"), worker.__file__, "exec"), worker.__dict__)
        trial = self.scorer["diagnostic"]["trial"]
        observed = trial["upstream_termination"]
        self.assertEqual(observed, {"reason": "too_many_errors", "tool_errors": 1,
            "tool_error_attribution": {"path": "live_make_tool_call", "unattributed": 0,
                "counts": [{"requestor": "assistant", "known_tool": True, "category": "value_error", "count": 1,
                            **self.origin}]}})
        self.assertEqual(worker.validate_upstream_termination(observed), observed)
        with self.assertRaises(ValueError):
            worker.validate_upstream_termination(observed, evaluated=True)
        self.assertEqual((trial["status"], trial["reward"], trial["requests"], trial["elapsed_ms"]),
                         ("protocol_error", None, 6, self.elapsed_ms))
        self.assertEqual(trial["role_requests"], {"agent": 3, "simulator": 3, "evaluator": 0})
        self.assertEqual(trial["usage"], {"prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
                                         "total_tokens": self.prompt_tokens + self.completion_tokens, "cost_usd": None})
        for row in (trial, self.scorer, self.controller):
            self.assertTrue(all(row[key] is False for key in ("quality_eligible", "live_admission", "confidential")))

    def test_original_owner_cleanup_and_receipt_joins(self):
        self.assertEqual((self.controller["started_utc"], self.controller["ended_utc"]),
                         (self.started, self.ended))
        self.assertEqual((self.controller["result"], self.controller["stage"]), ("fail", "trial"))
        self.assertEqual(self.controller["scorer"], self.scorer)
        self.assertEqual(self.controller["owner_exits"], {"candidate": 1, "support": 1})
        self.assertEqual(self.controller["owner_reports"],
            {owner: {name: self.hashes[owner + "/" + name][1] for name in ("run.json", "output.log")}
             for owner in ("cpu", "gpu")})
        finish = {"status": "protocol_error", "completed_requests": self.completed_requests}
        self.assertEqual(self.scorer["diagnostic"]["owner_finish"], {"candidate": finish, "support": finish})
        self.assertEqual(self.cpu["runtime"]["owner"], finish)
        self.assertEqual(self.gpu["experiment"]["tau_owner"],
                         {**finish, "ready_sha256": self.controller["ready_sha256"]["candidate"]})
        self.assertEqual(self.cpu["runtime"]["ready_sha256"], self.controller["ready_sha256"]["support"])
        for row in (self.gpu, self.cpu["runtime"], self.scorer, self.controller):
            self.assertEqual(row["cleanup"], "confirmed")
        self.assertIs(self.gpu["experiment"]["engine_stopped"], True)
        with historical_cpu(self.producer, self.cpu) as (_, _, session):
            self.assertIs(session.validate(self.cpu["runtime"]), self.cpu["runtime"])
            for name, digest in self.cpu["runtime"]["receipts"].items():
                self.assertEqual(digest, self.hashes["cpu/" + name][1])
            for role in ("simulator", "evaluator"):
                receipt = self.rows["cpu/" + role + "-tokenizer.json"]
                self.assertEqual(receipt["runtime_receipt_sha256"], self.hashes["cpu/" + role + "-runtime.json"][1])


class AttributionEvidenceTests(AttributionAssertions, unittest.TestCase):
    pass


if __name__ == "__main__":
    unittest.main()
