# SPDX-License-Identifier: Apache-2.0
"""Bounded original assertion shapes; no model, credentials or service calls."""

import ast
import hashlib
from pathlib import Path
import sys
import time
import types
import unittest
from unittest import mock

import test_tau_external_selection as staged

selection, full = staged.selection, staged.full
tau, policy, worker, external = staged.tau, staged.policy, staged.worker, staged.external


def stage(assertions):
    _, sources, pins = staged.fixtures()
    name = selection.source_names(["retail"])[0]
    rows = tau.json_value(sources[name])
    for row in rows["tasks"]:
        row["evaluation_criteria"] = {"nl_assertions": assertions}
    raw = sources[name] = tau.encoded(rows)
    pins["files"][name] = {"sha256": tau.digest(raw), "bytes": len(raw),
        "git_blob": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()}
    plan = selection.build(sources, pins, "a" * 40, ["retail"], 1, 42, staged.BUDGET)
    selection.verify_sources(plan, sources, pins)
    return {"plan": plan, "index": 0}


class AssertionTests(unittest.TestCase):
    def test_pinned_smoke_shapes_keep_original_selection_budget_and_no_content(self):
        for count in range(4):
            value = stage(["SYNTHETIC_ASSERTION_CANARY"] * count)
            self.assertEqual(selection.assertion_count(value), count)
            self.assertEqual(value["plan"]["unsupported_trials"], 0)
            self.assertEqual(value["plan"]["budget"]["trials_reserved_usd"], "350.224320")
            self.assertEqual(value["plan"]["domain_tasks"]["retail"]["task_ids"], ["0", "1", "10", "11", "2"])
            self.assertNotIn(b"CANARY", tau.encoded(value))
            self.assertFalse(value["plan"]["benchmark_executed"])
        self.assertEqual(selection.assertion_count(stage(None)), 0)

    def test_malformed_oversized_or_more_than_three_assertions_remain_unsupported(self):
        for assertions in (["synthetic"] * 4, [""], [True], ["x" * 16385], {}, "synthetic"):
            with self.subTest(assertions_type=type(assertions).__name__):
                value = stage(assertions)
                self.assertEqual(value["plan"]["unsupported_trials"], 5)
                self.assertEqual(len(value["plan"]["selections"]), 5)
                with self.assertRaises(ValueError):
                    selection.selected(value)

    def test_version_one_plan_is_not_reinterpreted(self):
        value = stage([])
        value["plan"]["schema"] = "nib.tau-external-selection-plan/v1"
        with self.assertRaises(ValueError):
            selection.validate(value["plan"])

    def test_worker_preserves_original_evaluator_for_zero_through_three(self):
        for count in range(4):
            assertions = ["synthetic"] * count
            config = staged.configuration(stage(assertions))
            channel = full.termination.Channel()
            proxy = full.pipe.transport.WorkerProxy(channel, time.monotonic() + 5, selected_operational=True)
            with full.termination.upstream(full.termination.simulation("user_stop", 0)) as run:
                task = types.SimpleNamespace(evaluation_criteria=types.SimpleNamespace(nl_assertions=assertions or None))
                with mock.patch.object(sys.modules["tau2.runner.helpers"], "get_tasks", return_value=[task]):
                    full.pipe.execute(None, config, config["selection"], proxy, run=worker.run_selected_upstream)
                run.assert_called_once()
                self.assertEqual(run.call_args.kwargs["evaluation_type"], "all-nl")
            self.assertEqual(channel.sent[-1]["status"], "evaluated")
            self.assertEqual(channel.sent[-1]["reward"], 0.0)

    def test_legacy_worker_keeps_single_assertion_restriction(self):
        config = full.configuration()
        for assertions in ([], ["synthetic"] * 2):
            channel = full.termination.Channel()
            proxy = full.pipe.transport.WorkerProxy(channel, time.monotonic() + 5, selected_operational=True)
            with full.termination.upstream(full.termination.simulation("user_stop", 0)) as run:
                task = types.SimpleNamespace(evaluation_criteria=types.SimpleNamespace(nl_assertions=assertions))
                with mock.patch.object(sys.modules["tau2.runner.helpers"], "get_tasks", return_value=[task]):
                    full.pipe.execute(None, config, config["selection"], proxy, run=worker.run_selected_upstream)
                run.assert_not_called()
            self.assertNotEqual(channel.sent[-1]["status"], "evaluated")

    def test_parent_binds_count_and_accepts_zero_without_inventing_evaluator_usage(self):
        held, channel = staged.runtime(stage([])), full.lookup.channels.FakeChannel()
        with self.assertRaises(ValueError):
            external.Session(channel, held, assertions=["substitute"])
        session = external.Session(channel, held, assertions=[])
        full.complete_roles(session, channel, ("agent", "simulator"))
        packet = full.terminal(session)
        packet["assertions_sha256"] = worker.digest(worker.encoded([]))
        channel.packets.append(packet)
        self.assertTrue(session.serve_once())
        self.assertEqual(session.result["reward"], "0.0")
        self.assertEqual(session.bridge.counts, {"agent": 1, "simulator": 1, "evaluator": 0})
        self.assertEqual(held.endpoints["evaluator"].observation()["generation_attempts"], 0)
        session.close()

    def test_multiple_assertions_require_complete_ordered_evaluator_results(self):
        assertions = ["first", "second", "third"]
        for mutation in (None, "missing", "reordered", "substitute"):
            held, channel = staged.runtime(stage(assertions)), full.lookup.channels.FakeChannel()
            rows = [{"expectedOutcome": text, "metExpectation": False, "reasoning": "synthetic"} for text in assertions]
            if mutation == "missing": rows.pop()
            if mutation == "reordered": rows.reverse()
            if mutation == "substitute": rows[-1]["expectedOutcome"] = "substitute"
            held.endpoints["evaluator"].http.result["output"][0]["content"][0]["text"] = worker.encoded({"results": rows}).decode()
            session = external.Session(channel, held, assertions=assertions)
            if mutation:
                with self.assertRaises(worker.Abort):
                    full.complete_roles(session, channel)
                self.assertEqual(session.bridge.failed, "protocol_error")
            else:
                full.complete_roles(session, channel)
                packet = full.terminal(session)
                packet["assertions_sha256"] = worker.digest(worker.encoded(assertions))
                channel.packets.append(packet)
                self.assertTrue(session.serve_once())
                self.assertEqual(session.result["reward"], "0.0")
            session.close()

    def test_acceptance_requires_known_usage_and_exact_evaluator_presence(self):
        value = {"schema": "nib.tau-external-staged/v2", "nl_assertion_count": 0,
            "scorer_protocol_completed": True, "attach_cleanup": "reaped", "scorer_exit_code": 0,
            "trial": {"status": "evaluated", "reward": "0.0", "usage": {}, "upstream_termination": {"reason": "user_stop"}},
            "endpoints": {name: {"generation_attempts": 0 if name == "evaluator" else 1, "unknown_responses": 0}
                          for name in ("candidate", "simulator", "evaluator")}}
        self.assertTrue(external.accepted(value))
        for count in (None, True, -1, 1, 2, 3, 4):
            self.assertFalse(external.accepted({**value, "nl_assertion_count": count}))
        value["endpoints"]["evaluator"]["generation_attempts"] = 1
        self.assertFalse(external.accepted(value))
        for count in (1, 2, 3):
            self.assertTrue(external.accepted({**value, "nl_assertion_count": count}))
        value["endpoints"]["evaluator"]["unknown_responses"] = 1
        self.assertFalse(external.accepted({**value, "nl_assertion_count": 1}))


def pinned_audit(archive):
    """Authenticate source, then exercise only original grading with fake results."""
    import tau_runtime as runtime
    deadline = time.monotonic() + 120
    pins = tau.json_value(runtime.read_file(runtime.ROOT / tau.PINS, 1 << 20, deadline))
    _, sources = runtime.source_inputs(runtime.read_file(Path(archive), runtime.ARCHIVE_LIMIT, deadline), pins, deadline)
    tree = ast.parse(sources["src/tau2/evaluator/evaluator_nl_assertions.py"])
    cls, = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "NLAssertionsEvaluator"]
    method, = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "calculate_reward"]
    scope = {"Task": object, "Message": object, "RewardInfo": lambda **kw: types.SimpleNamespace(**kw),
             "RewardType": types.SimpleNamespace(NL_ASSERTION="nl")}
    exec(compile(ast.Module(body=[method], type_ignores=[]), "<pinned-tau-nl-reward>", "exec"), scope)
    check = AssertionTests()
    evaluator = type("FakeEvaluationResults", (), {"evaluate_nl_assertions": mock.Mock()})
    calculate = scope["calculate_reward"].__get__(None, evaluator)
    for count in range(4):
        for met in (False, True):
            assertions = ["synthetic"] * count
            evaluator.evaluate_nl_assertions.reset_mock()
            evaluator.evaluate_nl_assertions.return_value = [types.SimpleNamespace(met=met)] * count
            task = types.SimpleNamespace(evaluation_criteria=types.SimpleNamespace(nl_assertions=assertions))
            result = calculate(task, [])
            check.assertEqual(result.reward, 1.0 if count == 0 or met else 0.0)
            check.assertEqual(evaluator.evaluate_nl_assertions.call_count, int(count > 0))
    plan = selection.build(sources, pins, "a" * 40, list(tau.DOMAINS), 1, 42,
                           {**staged.BUDGET, "gpu_committed_usd": "0"})
    check.assertEqual(plan["unsupported_trials"], 0)
    check.assertEqual(plan["domain_tasks"]["airline"]["assertion_counts"], [1, 1, 1, 3, 2])
    for domain in ("retail", "telecom"):
        check.assertEqual(plan["domain_tasks"][domain]["assertion_counts"], [0] * 5)
    print("Pinned 15-task assertion shapes and original NL reward controls PASS; no inference or spending approval.")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--source-archive":
        pinned_audit(sys.argv[2])
    else:
        unittest.main()
