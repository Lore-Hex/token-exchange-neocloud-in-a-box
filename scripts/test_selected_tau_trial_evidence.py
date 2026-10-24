# SPDX-License-Identifier: Apache-2.0
"""Original selected operational failure; no quality or cleanup promotion."""

import ast
import copy
import hashlib
import json
from pathlib import Path
import types
import unittest

from test_evidence_history import git
from test_selected_tau_failure_evidence import historical_cpu

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evidence/p2/2026-10-24-selected-tau-protocol-failure-1854"
PRODUCER = "7d40f8916c8b76dbb6db93f020795d108d428c55"
HASHES = {
    "gpu/output.log": (10575, "4d40dfbf45c1029ea8d5dba6b8af407266a943d6f5ffd67a1015e3fd716381eb"),
    "gpu/run.json": (1078, "ce4eac07397bca5f7f35c1a8df5f0a5d566ae2471ed2da54fb3c5115a6781424"),
    "cpu/evaluator-runtime.json": (3085, "8ab53cfe85dc252b862b2ed529d8283ad1b924b7774e2b5da014e66fa7ae5f27"),
    "cpu/evaluator-tokenizer.json": (1935, "25c731a878fa4f13f4e158b58c1784d2da90ed8c9115b2f68365fd3901213bd4"),
    "cpu/output.log": (12205, "b024fafd271ab8e4a11f108051d2dbb9b6012f932162ab3d453d3b50c7079dbc"),
    "cpu/run.json": (737, "2085f5084126455c675d27cda856020ac6761a4115ae97e35e7b7ade761d0bec"),
    "cpu/simulator-runtime.json": (3084, "69164a1a2ef26b91d6095acfdd180251f251b0d34b0ed549d8abe298e2019a1c"),
    "cpu/simulator-tokenizer.json": (1935, "ac949d6497e9084feaa0dc6b3ce4e3f6e98aafda700431ad6e12708917fb3f38"),
    "scorer/output.log": (5076, "8f6ceaa78e4e9e85d2edac46c986d4aca538f2db6c2d612ade0d65493d727cde"),
    "scorer/run.json": (668, "0ff41f34396a9db2315805f2bca07f88a20452359c5d96c4d09842d7518abed8"),
    "evidence/output.log": (5652, "e3eb37b8ea0e6c5ce2ba198d06a6bf093eef375fc42eea9eb56cca4708775471"),
    "evidence/run.json": (886, "e7b246d32d5eb251962d9f079320f7da862baa2cab26243e481cdca20d3deeb0"),
}


class SelectedTauTrialEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.documents = {}
        for name, expected in HASHES.items():
            raw = (DIRECTORY / name).read_bytes()
            self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), expected)
            self.documents[name] = json.loads(raw)
        self.gpu, self.cpu, self.scorer, self.controller = (
            self.documents[name + "/output.log"] for name in ("gpu", "cpu", "scorer", "evidence"))

    def source(self, name, sha):
        raw = git(ROOT, "show", PRODUCER + ":scripts/" + name)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), sha)
        return raw

    def module(self, name, sha):
        module = types.ModuleType("original_trial_evidence")
        module.__file__ = str(ROOT / "scripts" / name)
        exec(compile(self.source(name, sha), module.__file__, "exec", dont_inherit=True), module.__dict__)
        return module

    def test_original_inventory_producer_and_failure_envelopes(self):
        git(ROOT, "merge-base", "--is-ancestor", PRODUCER, "HEAD")
        self.assertEqual({str(path.relative_to(DIRECTORY)) for path in DIRECTORY.rglob("*") if path.is_file()},
                         set(HASHES) | {"README.md"})
        for owner in ("gpu", "cpu", "scorer", "evidence"):
            run = self.documents[owner + "/run.json"]
            self.assertEqual((run["git_sha"], run["result"]), (PRODUCER, "fail"))
            self.assertIs(run["hardware_validated"], False)
            self.assertEqual(run["execution"], "hardware" if owner in ("gpu", "evidence") else "software")
        for row in (self.gpu["provenance"], self.gpu["experiment"]["application"], self.cpu,
                    self.scorer["toolkit"], self.controller):
            self.assertEqual(row["git_sha"], PRODUCER)
        self.assertEqual((self.controller["started_utc"], self.controller["ended_utc"]),
                         ("2026-10-24T18:54:24Z", "2026-10-24T19:13:45Z"))
        self.assertEqual(self.gpu["provenance"]["execution_profile"], "tau8k-eager")
        self.assertEqual(self.gpu["provenance"]["pack_revision"], "c19ca5ca4eaeb79d29fdf08aea8327da95b1116a")

    def test_scorer_source_closure_and_bounded_trial(self):
        files = self.scorer["toolkit"]["files"]
        self.assertEqual(len(files), 35)
        for path, sha in files.items():
            self.assertEqual(hashlib.sha256(git(ROOT, "show", PRODUCER + ":" + path)).hexdigest(), sha)
        policy = ast.parse(self.source("tau_selected_policy.py", files["scripts/tau_selected_policy.py"]))
        limits_node = next(node.value for node in policy.body if isinstance(node, ast.Assign)
                           and any(isinstance(target, ast.Name) and target.id == "LIMITS" for target in node.targets))
        # Evaluate only the source-pinned literal arithmetic, not runtime code.
        limits = eval(compile(ast.Expression(limits_node), "original-limits", "eval"), {"__builtins__": {}})
        self.assertEqual(limits, {"concurrency": 1, "retries": 0, "turns": 32, "requests": 32,
                         "request_seconds": 120, "trial_seconds": 900, "run_seconds": 960,
                         "input_tokens_per_request": 8192, "output_tokens_per_request": 256,
                         "total_tokens": 270336})
        diagnostic = self.scorer["diagnostic"]
        trial = diagnostic["trial"]
        self.assertEqual((self.scorer["result"], self.scorer["stage"], trial["status"]),
                         ("fail", "completed", "protocol_error"))
        self.assertEqual(trial["selection"], {"domain": "retail", "role": "candidate", "seed": 42,
                                             "task_id": "2", "trial": 0})
        self.assertEqual((trial["requests"], trial["elapsed_ms"]), (10, 28809))
        self.assertEqual(trial["role_requests"], {"agent": 5, "simulator": 5, "evaluator": 0})
        self.assertEqual(trial["usage"], {"prompt_tokens": 27355, "completion_tokens": 296,
                                         "total_tokens": 27651, "cost_usd": None})
        self.assertEqual(diagnostic["reserved_tokens"], trial["usage"]["total_tokens"])
        self.assertIsNone(trial["reward"])
        self.assertEqual(trial["pricing"], "not-measured")
        self.assertEqual(trial["manifest_sha256"], self.scorer["manifest_sha256"])
        for row in (self.scorer, trial):
            self.assertTrue(all(row[key] is False for key in ("quality_eligible", "live_admission", "confidential")))
        self.assertIs(diagnostic["quality_accepted"], False)

    def test_owner_finish_and_cleanup_do_not_promote_controller(self):
        diagnostic = self.scorer["diagnostic"]
        self.assertEqual((diagnostic["scorer_exit_code"], diagnostic["attach_cleanup"]), (0, "reaped"))
        for name in ("owners_finished", "scorer_protocol_completed", "usage_known"):
            self.assertIs(diagnostic[name], True)
        finish = {"status": "protocol_error", "completed_requests": 5}
        self.assertEqual(diagnostic["owner_finish"], {"candidate": finish, "support": finish})
        self.assertEqual(self.cpu["runtime"]["owner"], finish)
        gpu_owner = self.gpu["experiment"]["tau_owner"]
        self.assertEqual(gpu_owner, {**finish, "ready_sha256": self.controller["ready_sha256"]["candidate"]})
        self.assertEqual(self.cpu["runtime"]["ready_sha256"], self.controller["ready_sha256"]["support"])
        self.assertEqual(self.controller["scorer"], self.scorer)
        self.assertEqual(self.controller["owner_exits"], {"candidate": 1, "support": 1})
        self.assertEqual((self.controller["result"], self.controller["stage"], self.controller["cleanup"]),
                         ("fail", "trial", "unconfirmed"))
        self.assertIsNone(self.controller["owner_reports"])
        self.assertEqual(self.cpu["cleanup"], dict.fromkeys(("container", "image", "build_completion"), "confirmed"))
        for row in (self.gpu, self.cpu["runtime"], self.scorer):
            self.assertEqual(row["cleanup"], "confirmed")
        self.assertIs(self.gpu["experiment"]["engine_stopped"], True)
        for row in (self.gpu, self.gpu["experiment"]):
            self.assertEqual(row["status"], "BLOCKED")
            self.assertTrue(all(row[key] is False for key in ("functional_pass", "deployable", "confidential")))
        self.assertTrue(all(self.controller[key] is False for key in ("quality_eligible", "confidential", "live_admission")))
        self.assertEqual(self.documents["evidence/run.json"]["host"]["driver"], None)
        self.assertEqual(self.documents["evidence/run.json"]["host"]["cuda"], None)

    def test_original_cpu_source_validator_receipt_joins_and_negative_controls(self):
        with historical_cpu(PRODUCER, self.cpu) as (_, cpu, session):
            self.assertEqual(len(self.cpu["sources"]), 31)
            runtime = self.cpu["runtime"]
            self.assertIs(session.validate(runtime), runtime)
            self.assertEqual((runtime["result"], runtime["stage"], runtime["acknowledged"]), ("fail", "released", True))
            self.assertEqual(set(runtime["receipts"]), set(session.FILES))
            for name, sha in runtime["receipts"].items():
                self.assertEqual(sha, HASHES["cpu/" + name][1])
            for role in ("simulator", "evaluator"):
                model, tokenizer = (self.documents["cpu/" + role + "-" + name + ".json"]
                                    for name in ("runtime", "tokenizer"))
                self.assertEqual(model["producer"], tokenizer["producer"])
                self.assertEqual(model["producer"], {"git_sha": PRODUCER, "script": "scripts/record-tau-cpu-parity.py",
                                 "source_sha256": self.cpu["sources"]["scripts/record-tau-cpu-parity.py"]})
                self.assertEqual(tokenizer["runtime_receipt_sha256"], runtime["receipts"][role + "-runtime.json"])
                self.assertEqual(model["logging"], {"access_logging": False, "request_logging": False,
                                                   "stderr": "null", "stdout": "null"})
                self.assertEqual(model["endpoint"]["host"], "127.0.0.1")
                for field in ("framing_sha256", "tokenizer_sha256"):
                    self.assertEqual(tokenizer[field], model["serving"][field])
            for mutate in (lambda row: row.update(result="pass"), lambda row: row.update(acknowledged=False),
                           lambda row: row.update(quality_eligible=True), lambda row: row.update(confidential=True),
                           lambda row: row.update(live_admission=True), lambda row: row.update(response_body="private-canary"),
                           lambda row: row["owner"].update(completed_requests=True)):
                changed = copy.deepcopy(runtime)
                mutate(changed)
                with self.assertRaises((ValueError, cpu.llama.worker.Abort)):
                    session.validate(changed)

    def test_original_gpu_validators_ready_and_parity_without_qualification(self):
        experiment = self.gpu["experiment"]
        probe = self.module("engine_lab_probe.py", "0955775847873fcff89cde788df1a50f4c92e2589121b5aae68f988113e08c17")
        parity = self.module("engine_tokenizer_parity.py", self.cpu["sources"]["scripts/engine_tokenizer_parity.py"])
        correctness = self.module("engine_local_correctness.py", "6f52e50c8c6699650d6a3e05ae55236f10987a7ab26c41c703c209868a79fb1c")
        hardware = probe.validate_nonconfidential_report(experiment["hardware"], family="B300", expected=8)
        self.assertEqual((hardware["driver"], hardware["cuda_driver"]), ("580.178.04", "13.0"))
        self.assertEqual((self.documents["gpu/run.json"]["host"]["driver"],
                          self.documents["gpu/run.json"]["host"]["cuda"]), (hardware["driver"], hardware["cuda_driver"]))
        self.assertEqual((experiment["startup"]["outcome"], experiment["startup"]["wait_ms"]), ("ready", 821161))
        row = parity.validate(experiment["tokenizer_parity"], correctness, max_model_len=8192)
        self.assertIs(row["all_cases_pass"], True)
        self.assertEqual((row["requests_started"], len(row["cases"])), (8, 4))
        self.assertEqual([case["local"]["count"] for case in row["cases"]], [12, 16, 275, 332])
        self.assertEqual([sample["event"] for sample in experiment["runtime_observation"]["samples"]],
                         ["before-requests", "after-requests"])
        for key in ("quality_eligible", "tau_admission"):
            self.assertIs(experiment["runtime_observation"][key], False)
        for mutate in (lambda r: r.update(tau_acceptance=True), lambda r: r.update(quality_accepted=True),
                       lambda r: r.update(confidential=True), lambda r: r.update(requests_started=7),
                       lambda r: r["cases"][0]["usage"].update(prompt_tokens=13)):
            changed = copy.deepcopy(row)
            mutate(changed)
            with self.assertRaises(ValueError):
                parity.validate(changed, correctness, max_model_len=8192)

    def test_original_pass_only_join_and_nonunique_protocol_error_mapping(self):
        raw = self.source("record-tau-selected.py", "7257861541729864f8429e7557f2012fcb672d1a8d91f68724e39e149884a4da")
        join = next(node for node in ast.parse(raw).body if isinstance(node, ast.FunctionDef) and node.name == "final_join")
        guards = [ast.unparse(node) for node in ast.walk(join) if isinstance(node, ast.Compare)]
        self.assertIn("scorer['result'] == 'pass'", guards)
        self.assertIn("runtime['result'] == 'pass'", guards)
        raw = self.source("tau_trial_worker.py", self.scorer["toolkit"]["files"]["scripts/tau_trial_worker.py"])
        worker = ast.parse(raw)
        config = next(node for node in ast.walk(worker) if isinstance(node, ast.Call)
                      and isinstance(node.func, ast.Name) and node.func.id == "TextRunConfig")
        values = {item.arg: ast.literal_eval(item.value) for item in config.keywords if isinstance(item.value, ast.Constant)}
        self.assertEqual({name: values[name] for name in ("max_errors", "max_retries", "hallucination_retries",
                         "auto_review", "auto_resume", "verbose_logs", "save_to")},
                         {"max_errors": 1, "max_retries": 0, "hallucination_retries": 0,
                          "auto_review": False, "auto_resume": False, "verbose_logs": False, "save_to": None})
        mapping = next(ast.literal_eval(node) for node in ast.walk(worker) if isinstance(node, ast.Dict)
                       and any(isinstance(key, ast.Constant) and key.value == "too_many_errors" for key in node.keys))
        self.assertEqual([mapping[key] for key in ("too_many_errors", "agent_error", "user_error")], ["protocol_error"] * 3)
        trial = self.scorer["diagnostic"]["trial"]
        for key in ("termination_reason", "tool_name", "exception", "message", "request_body", "response_body"):
            self.assertNotIn(key, trial)


if __name__ == "__main__":
    unittest.main()
