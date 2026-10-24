# SPDX-License-Identifier: Apache-2.0
"""Immutable selected tool-error evidence, with failed quality and joined cleanup."""

import copy
import hashlib
import json
from pathlib import Path
import types
import unittest

from test_evidence_history import git
from test_selected_tau_failure_evidence import historical_cpu

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evidence/p2/2026-10-24-selected-tau-tool-error-2009"
PRODUCER = "bc8585c183a3ca7721a7a7dfc11f7fa0c715e54b"
HASHES = {
    "cpu/evaluator-runtime.json": (3086, "5706f02682151a96160439471db6e9ca0c490357847fc46e2a38ff189dee0892"),
    "cpu/evaluator-tokenizer.json": (1935, "07ed74d0563fd884342188b317a55e28bfa9a24563b3b705b16868dfd99e68e0"),
    "cpu/output.log": (12207, "ee7d5ac0e18389ac96fb70bb3ea2edb40837136b15f0abe83301c34e05c234a9"),
    "cpu/run.json": (737, "0ff0f817d4f93b55ed4d6e1f08523f962da069698e4061a4f6bc9506dfdb604e"),
    "cpu/simulator-runtime.json": (3085, "44b39eccd499999978173a9c942b7c59139aa1d2bb9d63a765340e4e232f9dca"),
    "cpu/simulator-tokenizer.json": (1935, "db0c5e83d5b746a7f5e6c5f33a0e3d9e1cbb47c46961d8ac0a4a9bcc41437229"),
    "evidence/output.log": (6156, "29e8523811980d3090737af43c07a1392ad01dc5927398fdded76aaeaac67197"),
    "evidence/run.json": (1049, "ef879f639cabda328696821aea7b856b5585d7b4543ae4d6ae44c245835b9cff"),
    "gpu/output.log": (10575, "c01a1232ff7e5226e25f209a1739a7c765dcfb045178f0929636f57773bac855"),
    "gpu/run.json": (1078, "cdcb38ebace6bd2fd3412383cd4b1f093113ab79aa24017ae0f0a0fb31da554e"),
    "scorer/output.log": (5253, "706c908fa309bb61e63737e00b18d13fd04cbe870f4faae506db8901e5a0b419"),
    "scorer/run.json": (668, "c1a453b1e3d87752c116f1524cd8cb1e236f9951ad31d3c3ba2eac879da0f2ab"),
}


class SelectedTauDiagnosticEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.documents = {}
        for name, expected in HASHES.items():
            raw = (DIRECTORY / name).read_bytes()
            self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), expected)
            self.documents[name] = json.loads(raw)
        self.gpu, self.cpu, self.scorer, self.controller = (
            self.documents[name + "/output.log"] for name in ("gpu", "cpu", "scorer", "evidence"))

    def module(self, name, sha):
        raw = git(ROOT, "show", PRODUCER + ":scripts/" + name)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), sha)
        module = types.ModuleType("original_diagnostic_evidence")
        module.__file__ = str(ROOT / "scripts" / name)
        exec(compile(raw, module.__file__, "exec", dont_inherit=True), module.__dict__)
        return module

    def test_original_inventory_producer_and_failure_envelopes(self):
        git(ROOT, "merge-base", "--is-ancestor", PRODUCER, "HEAD")
        self.assertEqual({str(p.relative_to(DIRECTORY)) for p in DIRECTORY.rglob("*") if p.is_file()},
                         set(HASHES) | {"README.md"})
        for owner in ("gpu", "cpu", "scorer", "evidence"):
            run = self.documents[owner + "/run.json"]
            self.assertEqual((run["git_sha"], run["result"], run["hardware_validated"]), (PRODUCER, "fail", False))
            self.assertEqual(run["execution"], "hardware" if owner in ("gpu", "evidence") else "software")
        for row in (self.gpu["provenance"], self.gpu["experiment"]["application"], self.cpu,
                    self.scorer["toolkit"], self.controller):
            self.assertEqual(row["git_sha"], PRODUCER)
        self.assertEqual((self.controller["started_utc"], self.controller["ended_utc"]),
                         ("2026-10-24T20:09:53Z", "2026-10-24T20:29:19Z"))
        self.assertEqual(self.gpu["provenance"]["execution_profile"], "tau8k-eager")
        self.assertEqual(self.gpu["provenance"]["pack_revision"], "c19ca5ca4eaeb79d29fdf08aea8327da95b1116a")

    def test_source_closure_limits_and_usage(self):
        files = self.scorer["toolkit"]["files"]
        self.assertEqual(len(files), 36)
        for path, sha in files.items():
            self.assertEqual(hashlib.sha256(git(ROOT, "show", PRODUCER + ":" + path)).hexdigest(), sha)
        policy = self.module("tau_selected_policy.py", files["scripts/tau_selected_policy.py"])
        self.assertEqual(policy.LIMITS, {"concurrency": 1, "retries": 0, "turns": 32, "requests": 32,
                         "request_seconds": 120, "trial_seconds": 900, "run_seconds": 960,
                         "input_tokens_per_request": 8192, "output_tokens_per_request": 256, "total_tokens": 270336})
        diagnostic = self.scorer["diagnostic"]
        trial = diagnostic["trial"]
        self.assertEqual((self.scorer["result"], self.scorer["stage"], trial["status"]),
                         ("fail", "completed", "protocol_error"))
        self.assertEqual(trial["selection"], {"domain": "retail", "role": "candidate", "seed": 42, "task_id": "2", "trial": 0})
        self.assertEqual((trial["requests"], trial["elapsed_ms"]), (6, 24489))
        self.assertEqual(trial["role_requests"], {"agent": 3, "simulator": 3, "evaluator": 0})
        self.assertEqual(trial["usage"], {"prompt_tokens": 16057, "completion_tokens": 205,
                                         "total_tokens": 16262, "cost_usd": None})
        self.assertEqual(diagnostic["reserved_tokens"], trial["usage"]["total_tokens"])
        self.assertEqual(trial["manifest_sha256"], self.scorer["manifest_sha256"])
        self.assertIsNone(trial["reward"])
        self.assertEqual(trial["pricing"], "not-measured")
        for row in (self.controller, self.scorer, trial):
            self.assertTrue(all(row[key] is False for key in ("quality_eligible", "live_admission", "confidential")))
        self.assertIs(diagnostic["quality_accepted"], False)

    def test_bounded_content_free_diagnostic_rejects_promotion(self):
        worker = self.module("tau_trial_worker.py", self.scorer["toolkit"]["files"]["scripts/tau_trial_worker.py"])
        observed = self.scorer["diagnostic"]["trial"]["upstream_termination"]
        self.assertEqual(observed, {"reason": "too_many_errors", "tool_errors": 1})
        self.assertEqual(worker.validate_upstream_termination(observed), observed)
        self.assertEqual(worker.UPSTREAM_STATUSES[observed["reason"]], "protocol_error")
        for changes in ({"tool_errors": True}, {"tool_errors": 0}, {"tool_errors": 17}, {"tool_errors": -1},
                        {"reason": "private-canary"}, {"tool_name": "private-canary"}, {"message": "private-canary"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                worker.validate_upstream_termination({**observed, **changes})
        with self.assertRaises(ValueError):
            worker.validate_upstream_termination(observed, evaluated=True)

    def test_failed_owner_finish_and_confirmed_cleanup_hash_joins(self):
        diagnostic = self.scorer["diagnostic"]
        self.assertEqual((diagnostic["scorer_exit_code"], diagnostic["attach_cleanup"]), (0, "reaped"))
        for name in ("owners_finished", "scorer_protocol_completed", "usage_known"):
            self.assertIs(diagnostic[name], True)
        finish = {"status": "protocol_error", "completed_requests": 3}
        self.assertEqual(diagnostic["owner_finish"], {"candidate": finish, "support": finish})
        self.assertEqual(self.cpu["runtime"]["owner"], finish)
        self.assertEqual(self.gpu["experiment"]["tau_owner"],
                         {**finish, "ready_sha256": self.controller["ready_sha256"]["candidate"]})
        self.assertEqual(self.cpu["runtime"]["ready_sha256"], self.controller["ready_sha256"]["support"])
        self.assertEqual(self.controller["scorer"], self.scorer)
        self.assertEqual(self.controller["owner_exits"], {"candidate": 1, "support": 1})
        self.assertEqual((self.controller["result"], self.controller["stage"]), ("fail", "trial"))
        self.assertEqual(self.controller["owner_reports"],
                         {owner: {name: HASHES[owner + "/" + name][1] for name in ("run.json", "output.log")}
                          for owner in ("cpu", "gpu")})
        self.assertEqual(self.cpu["cleanup"], dict.fromkeys(("container", "image", "build_completion"), "confirmed"))
        for row in (self.gpu, self.cpu["runtime"], self.scorer, self.controller):
            self.assertEqual(row["cleanup"], "confirmed")
        self.assertIs(self.gpu["experiment"]["engine_stopped"], True)
        for row in (self.gpu, self.gpu["experiment"]):
            self.assertEqual(row["status"], "BLOCKED")
            self.assertTrue(all(row[key] is False for key in ("functional_pass", "deployable", "confidential")))

    def test_original_cpu_source_validator_and_readiness_receipts(self):
        with historical_cpu(PRODUCER, self.cpu) as (_, cpu, session):
            runtime = self.cpu["runtime"]
            self.assertIs(session.validate(runtime), runtime)
            self.assertEqual((runtime["result"], runtime["stage"], runtime["acknowledged"]), ("fail", "released", True))
            self.assertEqual(set(runtime["receipts"]), set(session.FILES))
            for name, sha in runtime["receipts"].items():
                self.assertEqual(sha, HASHES["cpu/" + name][1])
            for role in ("simulator", "evaluator"):
                model, tokenizer = (self.documents["cpu/" + role + "-" + name + ".json"] for name in ("runtime", "tokenizer"))
                self.assertEqual(model["producer"], tokenizer["producer"])
                self.assertEqual(model["producer"], {"git_sha": PRODUCER, "script": "scripts/record-tau-cpu-parity.py",
                                 "source_sha256": self.cpu["sources"]["scripts/record-tau-cpu-parity.py"]})
                self.assertEqual(tokenizer["runtime_receipt_sha256"], runtime["receipts"][role + "-runtime.json"])
                self.assertEqual(model["endpoint"]["host"], "127.0.0.1")
                self.assertEqual(model["logging"], {"access_logging": False, "request_logging": False, "stderr": "null", "stdout": "null"})
                for field in ("framing_sha256", "tokenizer_sha256"):
                    self.assertEqual(tokenizer[field], model["serving"][field])
            for mutation in ({"result": "pass"}, {"acknowledged": False}, {"quality_eligible": True},
                             {"confidential": True}, {"live_admission": True}, {"response_body": "private-canary"}):
                with self.subTest(mutation=mutation), self.assertRaises((ValueError, cpu.llama.worker.Abort)):
                    session.validate({**runtime, **mutation})

    def test_original_gpu_ready_hardware_and_parity_without_qualification(self):
        probe = self.module("engine_lab_probe.py", "0955775847873fcff89cde788df1a50f4c92e2589121b5aae68f988113e08c17")
        parity = self.module("engine_tokenizer_parity.py", self.cpu["sources"]["scripts/engine_tokenizer_parity.py"])
        correctness = self.module("engine_local_correctness.py", "6f52e50c8c6699650d6a3e05ae55236f10987a7ab26c41c703c209868a79fb1c")
        experiment = self.gpu["experiment"]
        hardware = probe.validate_nonconfidential_report(experiment["hardware"], family="B300", expected=8)
        self.assertEqual((hardware["driver"], hardware["cuda_driver"]), ("580.178.04", "13.0"))
        for owner in ("gpu", "evidence"):
            host = self.documents[owner + "/run.json"]["host"]
            self.assertEqual((host["driver"], host["cuda"]), (hardware["driver"], hardware["cuda_driver"]))
        self.assertEqual((experiment["startup"]["outcome"], experiment["startup"]["wait_ms"]), ("ready", 830098))
        row = parity.validate(experiment["tokenizer_parity"], correctness, max_model_len=8192)
        self.assertIs(row["all_cases_pass"], True)
        self.assertEqual((row["requests_started"], len(row["cases"])), (8, 4))
        self.assertEqual([case["local"]["count"] for case in row["cases"]], [12, 16, 275, 332])
        for key in ("quality_eligible", "tau_admission"):
            self.assertIs(experiment["runtime_observation"][key], False)
        for mutation in ({"tau_acceptance": True}, {"quality_accepted": True}, {"confidential": True}, {"requests_started": 7}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                parity.validate({**copy.deepcopy(row), **mutation}, correctness, max_model_len=8192)


if __name__ == "__main__":
    unittest.main()
