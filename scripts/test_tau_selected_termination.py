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
def upstream(value, *, failure=None, cleanup=None, environment=None, orchestrator=None):
    names = ("tau2", "tau2.runner", "tau2.runner.batch", "tau2.runner.helpers", "tau2.data_model",
             "tau2.data_model.simulation", "tau2.data_model.message", "tau2.evaluator",
             "tau2.evaluator.evaluator", "tau2.utils", "tau2.environment", "tau2.environment.environment",
             "tau2.orchestrator", "tau2.orchestrator.orchestrator")
    modules = {name: types.ModuleType(name) for name in names}
    modules["tau2.data_model.message"].ToolMessage = ToolMessage
    modules["tau2.data_model.simulation"].TextRunConfig = lambda **kw: types.SimpleNamespace(**kw)
    task = types.SimpleNamespace(evaluation_criteria=types.SimpleNamespace(nl_assertions=[CANARY]))
    modules["tau2.runner.helpers"].get_tasks = lambda *a, **kw: [task]
    modules["tau2.evaluator.evaluator"].EvaluationType = types.SimpleNamespace(ALL_WITH_NL_ASSERTIONS="all-nl")
    modules["tau2.utils"].llm_utils = types.SimpleNamespace(set_llm_log_dir=mock.Mock(side_effect=cleanup))
    run = modules["tau2.runner.batch"].run_single_task = mock.Mock(return_value=value, side_effect=failure)
    modules["tau2.environment.environment"].Environment = environment or type("Environment", (), {})
    modules["tau2.orchestrator.orchestrator"].BaseOrchestrator = orchestrator or type(
        "BaseOrchestrator", (), {"_execute_tool_calls": lambda *args: []})
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


def termination(reason, errors):
    return {"reason": reason, "tool_errors": errors, "tool_error_attribution": worker.ToolErrors().project(errors)}


def tool_types():
    class Environment:
        def make_tool_call(self, tool_name, requestor="assistant", **kwargs):
            return self.operation(tool_name, requestor=requestor, **kwargs)

        def get_response(self, message):
            try:
                result = self.make_tool_call(message.name, requestor=message.requestor, **message.arguments)
                self.sync_tools()
                return ToolMessage(error=False, content=result)
            except Exception as error:
                return ToolMessage(error=True, content=str(error))

    class Orchestrator:
        def _execute_tool_calls(self, calls):
            return [self.environment.get_response(call) for call in calls]

    return Environment, Orchestrator


def tool_state(environment, orchestrator, operation):
    env, state = environment(), orchestrator()
    env.solo_mode, env.user_tools = False, None
    env.tools = types.SimpleNamespace(has_tool=lambda name: name == CANARY)
    env.operation = mock.Mock(side_effect=operation)
    env.sync_tools = lambda: None
    state.environment, state.num_errors = env, 0
    message = types.SimpleNamespace(name=CANARY, requestor="assistant", arguments={"private": CANARY}, id=CANARY)
    return env, state, message


class AttributionTests(unittest.TestCase):
    def test_exact_categories_no_names_messages_or_exception_chains(self):
        class PrivateError(ValueError):
            def __str__(self):
                raise AssertionError("observer must not stringify exceptions")
            __repr__ = __str__
        PrivateError.__name__ = CANARY
        for kind in (*worker.TOOL_ERROR_CATEGORIES, PrivateError):
            error = kind(CANARY)
            error.__cause__ = RuntimeError(CANARY)
            error.__context__ = KeyError(CANARY)
            error.add_note(CANARY)
            observer = worker.ToolErrors()
            observer.record("assistant", True, error)
            row, = observer.project(1)["counts"]
            self.assertEqual(row, {"requestor": "assistant", "known_tool": True,
                "category": worker.TOOL_ERROR_CATEGORIES.get(kind, "other_exception"), "count": 1})
            self.assertNotIn(CANARY, json.dumps(observer.project(1)))

    def test_live_scope_exact_results_restore_and_exclude_replay(self):
        environment, orchestrator = tool_types()
        error = ValueError(CANARY)
        env, state, message = tool_state(environment, orchestrator, error)
        original_batch, original_call = orchestrator._execute_tool_calls, env.make_tool_call
        baseline = state._execute_tool_calls([message])
        with upstream(None, environment=environment, orchestrator=orchestrator), worker.observe_selected_tools() as observer:
            # Initialization and evaluator replay use get_response outside a live batch.
            env.get_response(message)
            result = state._execute_tool_calls([message, message])
            env.get_response(message)
            self.assertEqual(env.make_tool_call, original_call)
            self.assertNotIn("make_tool_call", vars(env))
        self.assertEqual(orchestrator._execute_tool_calls, original_batch)
        self.assertEqual([vars(item) for item in result], [vars(baseline[0])] * 2)
        self.assertEqual(env.operation.call_count, 5)
        self.assertEqual(observer.project(2), {"path": "live_make_tool_call", "unattributed": 0, "counts": [
            {"requestor": "assistant", "known_tool": True, "category": "value_error", "count": 2}]})

    def test_known_tool_requestor_and_unattributed_boundaries(self):
        for mode in ("known", "unknown", "user", "other", "binding", "sync", "metadata"):
            environment, orchestrator = tool_types()
            env, state, message = tool_state(environment, orchestrator, ValueError(CANARY))
            expected = ("assistant", True)
            if mode == "unknown": message.name, expected = "not-installed", ("assistant", False)
            if mode == "user": message.requestor, expected = "user", ("user", False)
            if mode == "other": message.requestor, expected = CANARY, ("other", False)
            if mode == "binding": message.arguments["requestor"] = CANARY
            if mode == "sync":
                env.operation.side_effect, env.operation.return_value = None, CANARY
                env.sync_tools = mock.Mock(side_effect=TypeError(CANARY))
            if mode == "metadata": env.tools.has_tool = mock.Mock(side_effect=ValueError(CANARY))
            baseline = state._execute_tool_calls([message])
            with self.subTest(mode=mode), upstream(None, environment=environment, orchestrator=orchestrator), \
                    worker.observe_selected_tools() as observer:
                result = state._execute_tool_calls([message])
            self.assertEqual([vars(item) for item in result], [vars(item) for item in baseline])
            self.assertTrue(result[0].error)
            projected = observer.project(1)
            if mode in ("binding", "sync", "metadata"):
                self.assertEqual(projected, worker.ToolErrors().project(1))
            else:
                row, = projected["counts"]
                self.assertEqual((row["requestor"], row["known_tool"]), expected)
            self.assertNotIn(CANARY, json.dumps(projected))

    def test_baseexceptions_preserve_identity_and_instance_overrides(self):
        for kind in (KeyboardInterrupt, SystemExit, worker.Abort):
            environment, orchestrator = tool_types()
            error = kind("cancelled")
            env, state, message = tool_state(environment, orchestrator, error)
            saved = env.make_tool_call = mock.Mock(side_effect=error)
            batch = orchestrator._execute_tool_calls
            with upstream(None, environment=environment, orchestrator=orchestrator):
                with self.assertRaises(kind) as caught:
                    with worker.observe_selected_tools() as observer:
                        state._execute_tool_calls([message])
                self.assertIs(caught.exception, error)
                self.assertIs(env.make_tool_call, saved)
                self.assertIs(orchestrator._execute_tool_calls, batch)
                self.assertEqual(observer.project(0), worker.ToolErrors().project(0))

    def test_bounded_counts_and_no_false_join(self):
        observer = worker.ToolErrors()
        for _ in range(worker.MAX_TOOL_CALLS):
            observer.record("assistant", True, ValueError(CANARY))
        self.assertEqual(observer.project(16)["counts"][0]["count"], 16)
        self.assertEqual(observer.project(1), worker.ToolErrors().project(1))
        for _ in range(1000): observer.record("user", False, TypeError(CANARY))
        self.assertEqual(observer.total, 16)
        self.assertEqual(len(observer.counts), 1)
        self.assertEqual(observer.project(16), worker.ToolErrors().project(16))

    def test_closed_wire_contract_negative_canaries_and_legacy(self):
        observer = worker.ToolErrors()
        observer.record("assistant", True, ValueError(CANARY))
        base = {"reason": "too_many_errors", "tool_errors": 1, "tool_error_attribution": observer.project(1)}
        self.assertEqual(worker.validate_upstream_termination(base), base)
        legacy = {"reason": "too_many_errors", "tool_errors": 1}
        self.assertEqual(worker.validate_upstream_termination(legacy), legacy)
        mutations = (
            lambda v: v.update(tool_error_attribution=None),
            lambda v: v["tool_error_attribution"].update(path=CANARY),
            lambda v: v["tool_error_attribution"].update(unattributed=True),
            lambda v: v["tool_error_attribution"].update(unattributed=-1),
            lambda v: v["tool_error_attribution"].update(unattributed=1),
            lambda v: v["tool_error_attribution"].update(raw=CANARY),
            lambda v: v["tool_error_attribution"].update(counts=[]),
            lambda v: v["tool_error_attribution"]["counts"].__imul__(17),
        )
        for mutate in mutations:
            value = json.loads(json.dumps(base))
            mutate(value)
            with self.assertRaises(ValueError): worker.validate_upstream_termination(value)
        for key, values in {"requestor": [CANARY, 0], "known_tool": [None, 1, CANARY],
                            "category": [CANARY, None], "count": [True, 0, -1, 17, CANARY],
                            "raw_tool": [CANARY]}.items():
            for item in values:
                value = json.loads(json.dumps(base))
                value["tool_error_attribution"]["counts"][0][key] = item
                channel = Channel()
                proxy = transport.WorkerProxy(channel, time.monotonic() + 5, selected_operational=True)
                proxy.upstream_termination = value
                with self.assertRaises(worker.Abort): proxy.finish("protocol_error", None)
                self.assertEqual(channel.sent, [])
                self.assertTrue(channel.closed)

    def test_selected_pipe_keeps_outcome_policy_and_only_closed_observations(self):
        environment, orchestrator = tool_types()
        env, state, message = tool_state(environment, orchestrator, KeyError(CANARY))
        def run(*args, **kwargs):
            env.get_response(message)
            value = simulation()
            value.messages = state._execute_tool_calls([message])
            env.get_response(message)
            return value
        _, channel, invoked = execute(None, failure=run, environment=environment, orchestrator=orchestrator)
        packet, = channel.sent
        self.assertEqual((packet["status"], packet["reward"]), ("protocol_error", None))
        row, = packet["upstream_termination"]["tool_error_attribution"]["counts"]
        self.assertEqual(row, {"requestor": "assistant", "known_tool": True, "category": "key_error", "count": 1})
        self.assertNotIn(CANARY, json.dumps(packet))
        config = invoked.call_args.args[0]
        self.assertEqual((config.max_errors, config.max_retries, config.hallucination_retries), (1, 0, 0))


class TerminationTests(unittest.TestCase):
    def test_all_pinned_reasons_keep_original_status_and_no_content(self):
        for reason, status in worker.UPSTREAM_STATUSES.items():
            with self.subTest(reason=reason):
                errors = 1 if reason == "too_many_errors" else 0
                proxy, channel, run = execute(simulation(reason, errors))
                packet, = channel.sent
                self.assertEqual(packet["upstream_termination"], termination(reason, errors))
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
                self.assertEqual(channel.sent[0]["upstream_termination"], termination(reason, count))
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
        toolkit = read("src/tau2/environment/toolkit.py")
        retail = read("src/tau2/domains/retail/environment.py")
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
                               and packet["upstream_termination"] == termination("too_many_errors", count)
                               and CANARY not in json.dumps(packet))
    # Exercise the actual dispatch/catch/toolkit methods with synthetic inputs,
    # including replay exclusion and failures outside make_tool_call.
    selected_classes = {"Environment": {"make_tool_call", "use_tool", "use_user_tool", "get_response"},
                        "ToolKitBase": {"use_tool", "has_tool"}, "BaseOrchestrator": {"_execute_tool_calls"}}
    classes = []
    for tree in (environment, toolkit, orchestrator):
        for cls in tree.body:
            if isinstance(cls, ast.ClassDef) and cls.name in selected_classes:
                methods = [node for node in cls.body if isinstance(node, ast.FunctionDef)
                           and node.name in selected_classes[cls.name]]
                worker.require({node.name for node in methods} == selected_classes[cls.name])
                classes.append(ast.ClassDef(name=cls.name, bases=[], keywords=[], body=methods, decorator_list=[]))
    worker.require(len(classes) == 3)
    retail_calls = [node for node in ast.walk(retail) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name) and node.func.id == "Environment"]
    worker.require(len(retail_calls) == 1 and not any(k.arg == "user_tools" for k in retail_calls[0].keywords))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module="__future__",
        names=[ast.alias(name="annotations")], level=0), *classes], type_ignores=[])),
        "<pinned-upstream-tool-dispatch>", "exec"), scope)
    Environment, Orchestrator = scope["Environment"], scope["BaseOrchestrator"]
    for mode in ("success", "known", "unknown", "user", "binding", "sync"):
        env, state, message = tool_state(Environment, Orchestrator, None)
        env.tools = scope["ToolKitBase"]()
        env.tools.tools = {CANARY: mock.Mock(return_value=CANARY)}
        env.to_json_str = lambda value: value
        if mode == "known": env.tools.tools[CANARY].side_effect = KeyError(CANARY)
        if mode == "unknown": message.name = "absent"
        if mode == "user": message.requestor = "user"
        if mode == "binding": message.arguments["requestor"] = CANARY
        if mode == "sync": env.sync_tools = mock.Mock(side_effect=ValueError(CANARY))
        before = [vars(item) for item in state._execute_tool_calls([message])]
        with upstream(None, environment=Environment, orchestrator=Orchestrator), worker.observe_selected_tools() as observer:
            env.get_response(message)
            actual = state._execute_tool_calls([message])
            env.get_response(message)
        worker.require([vars(item) for item in actual] == before and "make_tool_call" not in vars(env))
        errors = int(mode != "success")
        projected = observer.project(errors)
        worker.require(CANARY not in json.dumps(projected))
        if mode in ("known", "unknown", "user"):
            row, = projected["counts"]
            worker.require(row == {"requestor": "user" if mode == "user" else "assistant",
                "known_tool": mode == "known", "category": "key_error" if mode == "known" else "value_error", "count": 1})
        else:
            worker.require(projected == worker.ToolErrors().project(errors))
    print("Pinned upstream error/termination and live attribution PASS (ten controls); no model execution.")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--upstream-source":
        pinned_upstream_audit(sys.argv[2])
    else:
        unittest.main()
