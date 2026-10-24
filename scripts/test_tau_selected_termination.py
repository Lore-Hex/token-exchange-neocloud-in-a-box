# SPDX-License-Identifier: Apache-2.0
"""Content-free selected diagnostics; optional hash-checked upstream method audit."""

import ast
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys
import tarfile
import time
import types
import unittest
from unittest import mock

import tau_pipe_trial as pipe
import tau_selected_policy as policy

worker, transport = pipe.worker, pipe.transport
ROOT = Path(__file__).resolve().parents[1]
CANARY = "PRIVATE_MODEL_TOOL_EXCEPTION_CANARY"


class ToolMessage(types.SimpleNamespace):
    pass


def simulation(reason="too_many_errors", errors=1):
    return types.SimpleNamespace(termination_reason=types.SimpleNamespace(value=reason),
        reward_info=types.SimpleNamespace(reward=0.0),
        messages=[types.SimpleNamespace(content=CANARY),
                  *[ToolMessage(error=True, content=CANARY, id=CANARY) for _ in range(errors)],
                  ToolMessage(error=False, content=CANARY)])


@contextmanager
def upstream(value, *, failure=None, cleanup=None):
    names = ("tau2", "tau2.runner", "tau2.runner.batch", "tau2.runner.helpers", "tau2.data_model",
             "tau2.data_model.simulation", "tau2.data_model.message", "tau2.evaluator",
             "tau2.evaluator.evaluator", "tau2.utils")
    modules = {name: types.ModuleType(name) for name in names}
    modules["tau2.data_model.message"].ToolMessage = ToolMessage
    modules["tau2.data_model.simulation"].TextRunConfig = lambda **kw: types.SimpleNamespace(**kw)
    task = types.SimpleNamespace(evaluation_criteria=types.SimpleNamespace(nl_assertions=[CANARY]))
    modules["tau2.runner.helpers"].get_tasks = lambda *a, **kw: [task]
    modules["tau2.evaluator.evaluator"].EvaluationType = types.SimpleNamespace(ALL_WITH_NL_ASSERTIONS="all-nl")
    modules["tau2.utils"].llm_utils = types.SimpleNamespace(set_llm_log_dir=mock.Mock(side_effect=cleanup))
    run = modules["tau2.runner.batch"].run_single_task = mock.Mock(return_value=value, side_effect=failure)
    with mock.patch.dict(sys.modules, modules), mock.patch.object(worker, "configure_upstream"):
        yield run


class Channel:
    def __init__(self):
        self.sent, self.closed = [], False

    def send(self, packet, deadline):
        self.sent.append(packet)

    def receive(self, deadline):
        return {"schema": transport.SCHEMA, "kind": "accepted", "sequence": self.sent[-1]["sequence"]}

    def close(self):
        self.closed = True


def execute(value, **kwargs):
    channel = Channel()
    proxy = transport.WorkerProxy(channel, time.monotonic() + 5, selected_operational=True)
    with upstream(value, **kwargs) as run:
        pipe.execute(None, policy.configuration(), dict(policy.support.SELECTION), proxy,
                     run=worker.run_selected_upstream)
    return proxy, channel, run


class TerminationTests(unittest.TestCase):
    def test_all_pinned_reasons_keep_original_status_and_no_content(self):
        for reason, status in worker.UPSTREAM_STATUSES.items():
            with self.subTest(reason=reason):
                errors = 1 if reason == "too_many_errors" else 0
                proxy, channel, run = execute(simulation(reason, errors))
                packet, = channel.sent
                self.assertEqual(packet["upstream_termination"], {"reason": reason, "tool_errors": errors})
                self.assertEqual(packet["status"], status)
                self.assertEqual(packet["reward"], 0.0 if status == "evaluated" else None)
                self.assertTrue(proxy.finished)
                self.assertFalse(channel.closed)
                self.assertNotIn(CANARY, json.dumps(packet))
                config = run.call_args.args[0]
                self.assertEqual((config.max_errors, config.max_steps, config.timeout), (1, 32, 900))
                self.assertEqual((config.max_retries, config.hallucination_retries), (0, 0))

    def test_count_is_failed_tools_not_batches_or_all_messages(self):
        for count in (1, worker.MAX_TOOL_CALLS):
            for reason in ("too_many_errors", "timeout"):
                _, channel, _ = execute(simulation(reason, count))
                self.assertEqual(channel.sent[0]["upstream_termination"]["tool_errors"], count)

    def test_invalid_projection_never_leaks_or_becomes_protocol_success(self):
        mutations = (lambda s: setattr(s.termination_reason, "value", CANARY),
                     lambda s: setattr(s, "messages", None),
                     lambda s: setattr(s, "messages", [ToolMessage(error=True)] * 17),
                     lambda s: setattr(s, "messages", [ToolMessage(error=1)]),
                     lambda s: setattr(s, "messages", [ToolMessage(error=CANARY)]),
                     lambda s: setattr(s, "messages", [ToolMessage(error=False)] * 546),
                     lambda s: setattr(s, "messages", []))
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                value = simulation()
                mutation(value)
                _, channel, _ = execute(value)
                self.assertEqual(channel.sent[0]["status"], "infrastructure_error")
                self.assertIsNone(channel.sent[0]["upstream_termination"])
                self.assertIsNone(channel.sent[0]["reward"])
                self.assertNotIn(CANARY, json.dumps(channel.sent))

    def test_upstream_finalization_failure_has_no_invented_termination(self):
        for error, status in ((ValueError(CANARY), "infrastructure_error"), (KeyboardInterrupt(), "cancelled")):
            _, channel, _ = execute(simulation(), failure=error)
            self.assertEqual(channel.sent[0]["status"], status)
            self.assertIsNone(channel.sent[0]["upstream_termination"])
            self.assertNotIn(CANARY, json.dumps(channel.sent))

    def test_later_finalization_failure_preserves_fact_but_never_reward(self):
        for reason in ("user_stop", "too_many_errors"):
            for error, status in ((ValueError(CANARY), "infrastructure_error"), (KeyboardInterrupt(), "cancelled")):
                count = int(reason == "too_many_errors")
                _, channel, _ = execute(simulation(reason, count), cleanup=error)
                self.assertEqual(channel.sent[0]["status"], status)
                self.assertEqual(channel.sent[0]["upstream_termination"], {"reason": reason, "tool_errors": count})
                self.assertIsNone(channel.sent[0]["reward"])
                self.assertNotIn(CANARY, json.dumps(channel.sent))

    def test_legacy_terminal_frame_is_unchanged(self):
        channel = Channel()
        proxy = transport.WorkerProxy(channel, time.monotonic() + 5)
        proxy.upstream_termination = {"reason": CANARY}
        proxy.finish("protocol_error", None)
        self.assertEqual(set(channel.sent[0]), {"schema", "kind", "sequence", "status", "reward", "assertions_sha256"})
        self.assertNotIn(CANARY, json.dumps(channel.sent))

    def test_selected_proxy_rejects_untrusted_diagnostic_before_sending(self):
        for value in (None, {"reason": "too_many_errors", "tool_errors": 1},
                      {"reason": CANARY, "tool_errors": 0}, {"reason": "user_stop", "tool_errors": False}):
            channel = Channel()
            proxy = transport.WorkerProxy(channel, time.monotonic() + 5, selected_operational=True)
            proxy.upstream_termination = value
            with self.assertRaises(worker.Abort):
                proxy.finish("evaluated", 1.0)
            self.assertEqual(channel.sent, [])
            self.assertTrue(channel.closed)
            self.assertFalse(proxy.finished)


def pinned_upstream_audit(archive):
    """Execute original error/termination methods, not upstream imports or models."""
    lock = json.loads((ROOT / "bench/tau-runtime-lock.json").read_text())
    path = Path(archive)
    worker.require(hashlib.sha256(path.read_bytes()).hexdigest() == lock["archive_sha256"])
    with tarfile.open(path) as source:
        def read(name):
            members = [m for m in source.getmembers() if m.name.endswith("/" + name)]
            worker.require(len(members) == 1)
            raw = source.extractfile(members[0]).read()
            worker.require(hashlib.sha256(raw).hexdigest() == lock["source_files"][name]["sha256"])
            return ast.parse(raw)
        orchestrator = read("src/tau2/orchestrator/orchestrator.py")
        environment = read("src/tau2/environment/environment.py")
        models = read("src/tau2/data_model/simulation.py")
    enum = next(n for n in models.body if isinstance(n, ast.ClassDef) and n.name == "TerminationReason")
    reasons = {ast.literal_eval(n.value) for n in enum.body if isinstance(n, ast.Assign)}
    worker.require(reasons == set(worker.UPSTREAM_STATUSES))
    selected = {("BaseOrchestrator", "_execute_tool_calls"), ("Orchestrator", "_check_termination"),
                ("Environment", "get_response")}
    methods = [n for tree in (orchestrator, environment) for cls in tree.body if isinstance(cls, ast.ClassDef)
               for n in cls.body if isinstance(n, ast.FunctionDef) and (cls.name, n.name) in selected]
    worker.require(len(methods) == 3)
    scope = {"Role": types.SimpleNamespace(ENV="env"), "ToolMessage": ToolMessage,
             "TerminationReason": types.SimpleNamespace(MAX_STEPS="max_steps", TOO_MANY_ERRORS="too_many_errors"),
             "logger": types.SimpleNamespace(debug=lambda *args: None)}
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
                             *methods], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "<pinned-upstream-error-methods>", "exec"), scope)
    for failed in (False, True):
        for count in (1, worker.MAX_TOOL_CALLS):
            def call(*args, **kwargs):
                if failed:
                    raise ValueError(CANARY)
                return CANARY
            env = types.SimpleNamespace(make_tool_call=call, sync_tools=lambda: None, to_json_str=lambda x: x)
            env.get_response = types.MethodType(scope["get_response"], env)
            state = types.SimpleNamespace(environment=env, num_errors=0, max_errors=1, max_steps=32,
                step_count=11, to_role="agent", done=False, termination_reason=None, _check_timeout=lambda: None)
            tool = types.SimpleNamespace(name=CANARY, requestor="assistant", arguments={}, id=CANARY)
            messages = scope["_execute_tool_calls"](state, [tool] * count)
            scope["_check_termination"](state)
            worker.require(state.num_errors == (count if failed else 0) and state.done is failed)
            worker.require(state.termination_reason == ("too_many_errors" if failed else None))
            if failed:
                value = simulation()
                value.messages = messages
                _, channel, _ = execute(value)
                packet, = channel.sent
                worker.require(packet["status"] == "protocol_error" and packet["reward"] is None
                               and packet["upstream_termination"] == {"reason": "too_many_errors", "tool_errors": count}
                               and CANARY not in json.dumps(packet))
    print("Pinned upstream error/termination methods and selected pipe projection PASS (four controls); no model execution.")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--upstream-source":
        pinned_upstream_audit(sys.argv[2])
    else:
        unittest.main()
