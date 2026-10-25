# SPDX-License-Identifier: Apache-2.0
"""Synthetic dialogue/owner pipes only; no model, GPU or endpoint execution."""
import ast
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import tau_customer_resolution as resolution
import tau_pipe_trial as pipe
import test_tau_selected_session as fixtures
import test_tau_selected_launch as launch_fixture
import test_tau_selected_termination as termination
import test_tau_cpu_identity as identity_fixture
import test_tau_selected_driver as driver_fixture
from test_evidence_history import git

policy, worker, tau = resolution.policy, resolution.worker, resolution.policy.tau
ROOT = Path(__file__).resolve().parents[1]
BASE = "e5b44e1"
CANARY = "PRIVATE_CUSTOMER_ARGUMENT_REPLY_CANARY"


class ToolCall(types.SimpleNamespace):
    pass


class ToolMessage(types.SimpleNamespace):
    pass


def observation(outcome="resolved", tool="name_zip"):
    return {"schema": policy.RESOLUTION_REPORT, "tool": tool, "outcome": outcome,
            "intended_customer_match": True if outcome == "resolved" else False if outcome == "wrong_customer" else None}


def manifest():
    row = fixtures.manifest()
    row["input"] = policy.configuration(customer_resolution=True)
    row["settings"] = policy.settings(row["input"])
    row["framing"]["simulator"] = dict(row["framing"]["evaluator"])
    row["framing"]["evaluator"]["served_model"] = "disabled-evaluator"
    row["simulator_binding"] = {"role": "simulator", "owner_role": "evaluator",
        **row["input"]["support"]["simulator"], "runtime_sha256": "c" * 64,
        "process_sha256": "d" * 64, "model_files_sha256": "e" * 64,
        **{key: row["framing"]["simulator"][key] for key in ("tokenizer_sha256", "framing_sha256")}}
    return row


class Orchestrator:
    def __init__(self, *, tool="find_user_id_by_name_zip", result=CANARY, error=False, calls=1, failure=None):
        self.calls = [ToolCall(id="synthetic-call", name=tool, requestor="assistant", arguments={"untouched": CANARY}) for _ in range(calls)]
        self.results = [ToolMessage(error=error, content=result)]
        self.executed = mock.Mock(return_value=self.results)
        self.agent, self.user = mock.Mock(), mock.Mock()
        self.done, self.steps, self.checks, self.failure = False, 0, 0, failure

    def _execute_tool_calls(self, calls):
        return self.executed(calls)

    def initialize(self):
        if self.failure == "initialization":
            raise ValueError(CANARY)

    def step(self):
        self.steps += 1
        if self.failure == "cancelled":
            raise KeyboardInterrupt
        if self.failure == "timeout":
            raise worker.Abort("timeout")
        if self.failure == "no-lookup":
            return
        if self.steps == 3:
            self._execute_tool_calls(self.calls)
        if self.steps > 3:
            raise AssertionError("post-lookup model call")

    def _check_termination(self):
        self.checks += 1


class ResolutionTests(unittest.TestCase):
    def run_dialogue(self, orchestrator, proxy=None, *, expected=CANARY):
        proxy = proxy or types.SimpleNamespace(deadline=time.monotonic() + 5)
        messages = types.ModuleType("tau2.data_model.message")
        messages.ToolCall, messages.ToolMessage = ToolCall, ToolMessage
        with mock.patch.dict(sys.modules, {"tau2.data_model.message": messages}):
            return resolution.dialogue(orchestrator, expected, proxy)

    def test_default_configuration_bytes_and_limits_unchanged(self):
        old = types.ModuleType("old_selected_policy")
        exec(compile(git(ROOT, "show", BASE + ":scripts/tau_selected_policy.py"), "old_policy", "exec"), old.__dict__)
        self.assertEqual(tau.encoded(policy.configuration()), tau.encoded(old.configuration()))
        row = policy.configuration(customer_resolution=True)
        policy.validate(row)
        self.assertEqual(row["limits"], old.LIMITS)
        self.assertEqual(row["contexts"], old.CONTEXTS)
        self.assertEqual(row["generation"], {"temperature": "0", "max_tokens": 256, "num_retries": 0})
        self.assertEqual(row["support"], {"simulator": old.configuration()["support"]["evaluator"]})
        self.assertEqual(row["owner_roles"], {"simulator": "evaluator"})
        self.assertEqual(set(policy.settings(row)["roles"]), {"candidate", "simulator"})
        for key in ("quality_eligible", "confidential", "live_admission"):
            self.assertIs(row[key], False)

    def test_policy_and_binding_mutations_fail_closed(self):
        for mutate in (lambda r: r["input"].update(schema=policy.SCHEMA),
                       lambda r: r["input"]["owner_roles"].update(simulator="simulator"),
                       lambda r: r["input"]["support"].update(evaluator=r["input"]["support"]["simulator"]),
                       lambda r: r["input"]["limits"].update(retries=1),
                       lambda r: r["input"]["limits"].update(output_tokens_per_request=257),
                       lambda r: r["input"].update(quality_eligible=True),
                       lambda r: r["simulator_binding"].update(owner_role="simulator"),
                       lambda r: r["simulator_binding"].update(model="qwen/qwen3-0.6b"),
                       lambda r: r["simulator_binding"].update(tokenizer_sha256="0" * 64),
                       lambda r: r["owners"]["support"]["roles"]["evaluator"].update(model="qwen/qwen3-0.6b"),
                       lambda r: r["framing"]["evaluator"].update(served_model="qwen/qwen3-1.7b")):
            row = manifest()
            mutate(row)
            with self.assertRaises((ValueError, worker.Abort)):
                policy.transport_manifest(row, tau.digest(tau.encoded(row)))

    def test_original_cpu_receipt_mapping_not_relabelled(self):
        f = launch_fixture
        ready, toolkit, _, _, _, _ = f.inputs()
        raw = f.cpu.session.llama.worker.decode(ready["support"])["receipts"]["evaluator-runtime.json"]
        with mock.patch.object(f.cpu.session.identity, "source_receipt"), \
                mock.patch.object(f.launch.runner, "selected_policy", return_value=policy):
            frozen, _, held, _ = f.launch.admit(ready, toolkit, f.cpu.session, f.gpu.gpu, f.gpu.contract(),
                f.gpu.SHA, time.monotonic() + 300, customer_resolution=True)
        runtime = f.cpu.session.llama.worker.decode(raw.encode())
        self.assertEqual(frozen["simulator_binding"], policy.support.runtime_binding(runtime))
        self.assertEqual(frozen["simulator_binding"]["runtime_sha256"], held["receipts"]["evaluator-runtime.json"])
        self.assertEqual(runtime["role"], "evaluator")
        self.assertEqual(frozen["framing"]["simulator"]["served_model"], runtime["model"])

    def test_expected_identity_comes_from_hash_checked_task_not_model_arguments(self):
        sources, pins = identity_fixture.encode_sources(identity_fixture.sources())
        pins_raw = tau.encoded(pins)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bench").mkdir()
            (root / "bench/tau-upstream-pins.json").write_bytes(pins_raw)
            for name, raw in sources.items():
                path = root / "upstream" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            with mock.patch.object(tau, "PINS_SHA", tau.digest(pins_raw)):
                self.assertEqual(resolution.expected_customer(root), "synthetic")
                path.write_bytes(path.read_bytes() + b" ")
                self.assertRaises(ValueError, resolution.expected_customer, root)
            self.assertRaises(ValueError, resolution.expected_customer, root)

    def test_real_dispatch_builds_natural_orchestrator_without_evaluator(self):
        modules = {name: types.ModuleType(name) for name in ("tau2.runner.helpers", "tau2.runner.build",
            "tau2.data_model.simulation", "tau2.utils")}
        task = types.SimpleNamespace(initial_state=None, user_tools=None,
            evaluation_criteria=types.SimpleNamespace(nl_assertions=["assertion"]))
        tasks = modules["tau2.runner.helpers"].get_tasks = mock.Mock(return_value=[task])
        build = modules["tau2.runner.build"].build_text_orchestrator = mock.Mock(return_value=Orchestrator())
        config = modules["tau2.data_model.simulation"].TextRunConfig = mock.Mock(side_effect=lambda **kw: types.SimpleNamespace(**kw))
        logs = modules["tau2.utils"].llm_utils = types.SimpleNamespace(set_llm_log_dir=mock.Mock())
        proxy = types.SimpleNamespace(deadline=time.monotonic() + 5)
        with mock.patch.dict(sys.modules, modules), mock.patch.object(worker, "configure_upstream") as configure, \
                mock.patch.object(resolution, "expected_customer", return_value=CANARY), \
                mock.patch.object(resolution, "dialogue", return_value=observation()) as run:
            self.assertEqual(worker.run_selected_upstream(None, policy.configuration(customer_resolution=True),
                             policy.support.SELECTION, proxy), observation())
        configure.assert_called_once_with(proxy)
        tasks.assert_called_once_with("retail", task_split_name="base", task_ids=["2"], num_tasks=1)
        self.assertEqual(config.call_args.kwargs["max_errors"], 1)
        self.assertEqual(config.call_args.kwargs["max_steps"], 32)
        self.assertEqual(config.call_args.kwargs["timeout"], 900)
        for key in ("max_retries", "hallucination_retries"):
            self.assertEqual(config.call_args.kwargs[key], 0)
        self.assertIsNone(config.call_args.kwargs["save_to"])
        run.assert_called_once_with(build.return_value, CANARY, proxy)
        logs.set_llm_log_dir.assert_called_once_with(None)

    def test_sealed_source_closure_includes_diagnostic_and_fixture_verifier(self):
        runner = launch_fixture.launch.runner
        with mock.patch.object(runner.tau, "committed_identity", return_value={"git_sha": "a" * 40, "files": {}}) as load:
            runner.identity(time.monotonic() + 5, True, selected_operational=True)
        paths = {name for call in load.call_args_list for name in call.kwargs.get("extra_files", ())}
        self.assertTrue({"scripts/tau_customer_resolution.py", "scripts/tau_cpu_identity.py"} <= paths)
        self.assertRaises(ValueError, runner.prepare, None, None, None, None, None, time.monotonic() + 1,
                          customer_resolution=True)

    def test_natural_arguments_once_then_stop_without_reward_or_evaluator(self):
        for tool, category in resolution.TOOLS.items():
            state = Orchestrator(tool=tool)
            row = self.run_dialogue(state)
            self.assertEqual(row, observation(tool=category))
            self.assertEqual((state.steps, state.checks), (3, 3))
            state.executed.assert_called_once_with(state.calls)
            self.assertEqual(state.calls[0].arguments, {"untouched": CANARY})
            self.assertNotIn(CANARY, json.dumps(row))
            self.assertNotIn("_execute_tool_calls", vars(state))
            state.agent.stop.assert_called_once_with(None, None)
            state.user.stop.assert_called_once_with(None, None)

    def test_wrong_customer_errors_and_nonlookup_cannot_pass(self):
        for kwargs, outcome, attempted in (({"result": "another-synthetic-customer"}, "wrong_customer", True),
                ({"error": True}, "lookup_error", True), ({"tool": "unknown-private-tool"}, "unsupported_tool", False),
                ({"tool": "cancel_order"}, "unsupported_tool", False), ({"calls": 2}, "multiple_tools", False),
                ({"calls": 0}, "multiple_tools", False), ({"failure": "no-lookup"}, "not_attempted", False)):
            state, proxy = Orchestrator(**kwargs), types.SimpleNamespace(deadline=time.monotonic() + 5)
            with self.subTest(kwargs=kwargs), self.assertRaises(worker.Abort):
                self.run_dialogue(state, proxy)
            self.assertEqual(proxy.customer_resolution["outcome"], outcome)
            self.assertEqual(state.executed.call_count, int(attempted))
            self.assertLessEqual(state.steps, policy.LIMITS["turns"] + 1)
            self.assertNotIn(CANARY, json.dumps(proxy.customer_resolution))
            state.agent.stop.assert_called_once()
            state.user.stop.assert_called_once()

    def test_plain_result_regresses_original_producer_json_decode(self):
        old = types.ModuleType("old_customer_resolution")
        revision = "975738d7eb26e3d315a41857ece0d70d757db4ab"
        exec(compile(git(ROOT, "show", revision + ":scripts/tau_customer_resolution.py"),
                     "old_resolution", "exec"), old.__dict__)
        for tool, category in resolution.TOOLS.items():
            state, proxy = Orchestrator(tool=tool), types.SimpleNamespace(deadline=time.monotonic() + 5)
            with self.subTest(tool=tool), mock.patch.object(resolution, "dialogue", old.dialogue), \
                    self.assertRaises(ValueError):
                self.run_dialogue(state, proxy)
            self.assertEqual(proxy.customer_resolution, observation("lookup_error", category))
            state.executed.assert_called_once_with(state.calls)
            self.assertNotIn("_execute_tool_calls", vars(state))
            self.assertEqual(self.run_dialogue(Orchestrator(tool=tool)), observation(tool=category))

    def test_result_is_compared_literally_not_json_decoded_or_repaired(self):
        for value in ("null", "true", "123", "[]", '"quoted-id"', "x" * 256):
            with self.subTest(length=len(value)):
                self.assertEqual(self.run_dialogue(Orchestrator(result=value), expected=value), observation())
        for value in (json.dumps(CANARY), CANARY.lower(), " " + CANARY, CANARY + "\n"):
            state, proxy = Orchestrator(result=value), types.SimpleNamespace(deadline=time.monotonic() + 5)
            with self.subTest(value=value), self.assertRaises(worker.Abort):
                self.run_dialogue(state, proxy)
            self.assertEqual(proxy.customer_resolution, observation("wrong_customer"))
            self.assertNotIn(CANARY, json.dumps(proxy.customer_resolution))
            state.executed.assert_called_once()

    def test_invalid_success_result_remains_fail_closed_without_content(self):
        for value in (None, True, 42, b"synthetic", [], {}, "", "x" * 257):
            state, proxy = Orchestrator(result=value), types.SimpleNamespace(deadline=time.monotonic() + 5)
            with self.subTest(kind=type(value).__name__), self.assertRaises(ValueError):
                self.run_dialogue(state, proxy)
            self.assertEqual(proxy.customer_resolution, observation("lookup_error"))
            state.executed.assert_called_once()
            state.agent.stop.assert_called_once()
            state.user.stop.assert_called_once()
        state, proxy = Orchestrator(result=CANARY, error=True), types.SimpleNamespace(deadline=time.monotonic() + 5)
        with self.assertRaises(worker.Abort):
            self.run_dialogue(state, proxy)
        self.assertEqual(proxy.customer_resolution, observation("lookup_error"))
        self.assertNotIn(CANARY, json.dumps(proxy.customer_resolution))

    def test_cancel_expiry_initialization_and_cleanup_fail_closed(self):
        for failure in ("cancelled", "timeout", "initialization", "expired", "cleanup"):
            state = Orchestrator(failure=failure)
            if failure == "cleanup":
                state.agent.stop.side_effect = ValueError(CANARY)
            proxy = types.SimpleNamespace(deadline=time.monotonic() + (-1 if failure == "expired" else 5))
            with self.subTest(failure=failure), self.assertRaises((worker.Abort, KeyboardInterrupt, ValueError)):
                self.run_dialogue(state, proxy)
            self.assertNotIn("_execute_tool_calls", vars(state))
            state.agent.stop.assert_called_once()
            state.user.stop.assert_called_once()

    def test_closed_report_rejects_raw_fields_and_false_match(self):
        for mutate in (lambda r: r.update(content=CANARY), lambda r: r.update(reward=1.0),
                       lambda r: r.update(tool=CANARY), lambda r: r.update(intended_customer_match=1),
                       lambda r: r.update(intended_customer_match=False), lambda r: r.update(outcome="lookup_error")):
            row = observation()
            mutate(row)
            self.assertRaises(ValueError, policy.resolution_result, row, completed=True)

    def test_pipe_finish_has_no_reward_and_preserves_failure_observation(self):
        for failure in (False, True):
            channel = termination.Channel()
            proxy = pipe.transport.WorkerProxy(channel, time.monotonic() + 5,
                selected_operational=True, customer_resolution=True)
            def run(*args):
                proxy.customer_resolution = observation("lookup_error" if failure else "resolved")
                if failure:
                    raise worker.Abort("protocol_error")
                return proxy.customer_resolution
            pipe.execute(None, policy.configuration(customer_resolution=True), policy.support.SELECTION, proxy, run=run)
            packet = channel.sent[-1]
            self.assertIsNone(packet["reward"])
            self.assertIsNone(packet["upstream_termination"])
            self.assertEqual(packet["status"], "protocol_error" if failure else "evaluated")
            self.assertEqual(packet["customer_resolution"], proxy.customer_resolution)


class OwnerSessionTests(unittest.TestCase):
    def session(self):
        frozen = manifest()
        channels = {name: fixtures.OwnerReplies(binding) for name, binding in frozen["owners"].items()}
        coordinators = {name: fixtures.owners.Coordinator(channel, frozen["owners"][name], time.monotonic() + 30)
                        for name, channel in channels.items()}
        channel = fixtures.fixtures.FakeChannel()
        session = fixtures.selected.Session(channel, frozen, tau.digest(tau.encoded(frozen)),
            coordinators["candidate"], coordinators["support"], assertions=["assertion"])
        self.addCleanup(session.close)
        return session, channel, channels

    def request(self, session, channel, role):
        channel.packets.append(fixtures.fixtures.fixtures.packet(session.sequence + 1, role,
                                                                fixtures.fixtures.fixtures.arguments()))
        return session.serve_once()

    def test_simulator_uses_original_17b_owner_and_no_evaluator_endpoint(self):
        session, channel, owners = self.session()
        self.assertEqual(set(session.endpoints), {"candidate", "simulator"})
        self.request(session, channel, "simulator")
        self.request(session, channel, "agent")
        self.assertEqual([row["role"] for row in owners["support"].sent], ["evaluator", "evaluator"])
        channel.packets.append({"schema": pipe.transport.SCHEMA, "kind": "finished", "sequence": 3,
            "status": "evaluated", "reward": None, "assertions_sha256": tau.digest(tau.encoded(["assertion"])),
            "upstream_termination": None, "customer_resolution": observation()})
        self.assertTrue(session.serve_once())
        self.assertIsNone(session.result["reward"])
        self.assertEqual(session.result["role_requests"], {"agent": 1, "simulator": 1, "evaluator": 0})
        self.assertEqual(session.result["usage"]["total_tokens"], 16)

    def test_evaluator_call_is_rejected_before_owner_dispatch(self):
        session, channel, owners = self.session()
        with self.assertRaises(worker.Abort):
            self.request(session, channel, "evaluator")
        self.assertEqual(owners["support"].sent, [])

    def test_cap_failure_retains_usage_without_terminal_success(self):
        session, channel, owners = self.session()
        owners["candidate"].response = fixtures.capped_tool_response()
        with self.assertRaises(worker.Abort):
            self.request(session, channel, "agent")
        self.assertTrue(session.bridge.usage_known)
        self.assertEqual(session.bridge.usage()["total_tokens"], 263)
        self.assertIsNone(session.result)

    def test_invalid_usage_stays_unknown_and_reserved(self):
        session, channel, owners = self.session()
        owners["candidate"].response = fixtures.capped_tool_response(255)
        owners["candidate"].response["usage"]["total_tokens"] = 0
        with self.assertRaises(worker.Abort):
            self.request(session, channel, "agent")
        self.assertFalse(session.bridge.usage_known)
        self.assertIsNone(session.bridge.usage())

    def test_reward_or_missing_resolution_and_role_count_tampering_rejected(self):
        for fault in ("reward", "missing", "wrong_customer", "evaluator_count"):
            session, channel, _ = self.session()
            self.request(session, channel, "simulator")
            self.request(session, channel, "agent")
            packet = {"schema": pipe.transport.SCHEMA, "kind": "finished", "sequence": 3,
                "status": "evaluated", "reward": None, "assertions_sha256": tau.digest(tau.encoded(["assertion"])),
                "upstream_termination": None, "customer_resolution": observation()}
            if fault == "reward": packet["reward"] = 0.0
            elif fault == "missing": packet.pop("customer_resolution")
            elif fault == "wrong_customer": packet["customer_resolution"] = observation("wrong_customer")
            else: session.bridge.counts["evaluator"] = 1
            channel.packets.append(packet)
            with self.subTest(fault=fault), self.assertRaises(worker.Abort):
                session.serve_once()


class DriverTests(unittest.TestCase):
    def test_actual_child_pipe_null_reward_and_cleanup_failure_joins(self):
        code = driver_fixture.CHILD.replace("import tau_pipe_trial as trial", "import tau_pipe_trial as trial\nimport tau_selected_policy as policy")
        code = code.replace("selected_operational=True)", "selected_operational=True, customer_resolution=True)")
        code = code.replace("('simulator', 'agent', 'evaluator')", "('simulator', 'agent')")
        code = code.replace("bridge.upstream_termination = {'reason': 'user_stop', 'tool_errors': 0}",
                            "bridge.customer_resolution = " + repr(observation()))
        code = code.replace("return 1.0", "return bridge.customer_resolution")
        code = code.replace("trial.execute(None, {}, {}, proxy", "trial.execute(None, policy.configuration(customer_resolution=True), {}, proxy")
        for fault in (None, "owner-finish", "unknown-usage"):
            helper = driver_fixture.SelectedDriverTests()
            helper.setUp()
            try:
                helper.frozen = manifest()
                helper.plan["manifest_sha256"] = tau.digest(tau.encoded(helper.frozen["input"]))
                if fault == "unknown-usage":
                    helper.channels["candidate"].error = "infrastructure_error"
                with mock.patch.object(helper.owners["candidate"], "finish", wraps=helper.owners["candidate"].finish,
                        side_effect=OSError(CANARY) if fault == "owner-finish" else None):
                    result = helper.run_child(code)
                self.assertEqual(driver_fixture.driver.accepted(result), fault is None)
                self.assertEqual(result["attach_cleanup"], "reaped")
                self.assertIsNone(result["trial"]["reward"])
                self.assertEqual(result["trial"]["role_requests"]["evaluator"], 0)
                self.assertEqual(result["trial"]["simulator_binding"], helper.frozen["simulator_binding"])
                self.assertNotIn("CANARY", repr(result))
                if fault != "unknown-usage":
                    self.assertEqual(result["trial"]["usage"]["total_tokens"], 16)
                    self.assertEqual(result["trial"]["customer_resolution"], observation())
                else:
                    self.assertIsNone(result["trial"]["usage"])
                    self.assertEqual(result["reserved_tokens"], 8 + 8448)
            finally:
                helper.doCleanups()


def pinned_upstream_audit(root):
    """Optional offline execution of pinned lookup/serialization/batch bodies."""
    root = Path(root)
    pins = json.loads((ROOT / "bench/tau-runtime-lock.json").read_bytes())["source_files"]
    modules = {}
    wanted = {"domains/retail/tools": ("find_user_id_by_email", "find_user_id_by_name_zip"),
              "environment/environment": ("to_json_str", "get_response"),
              "orchestrator/orchestrator": ("_execute_tool_calls",)}
    for name, methods in wanted.items():
        path = "src/tau2/" + name + ".py"
        raw, pin = (root / "upstream" / path).read_bytes(), pins[path]
        worker.require(len(raw) == pin["bytes"] and tau.digest(raw) == pin["sha256"])
        functions = [node for cls in ast.parse(raw).body if isinstance(cls, ast.ClassDef)
                     for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in methods]
        worker.require(len(functions) == len(methods))
        for function in functions:
            if name.startswith("domains/"):
                worker.require(len(function.decorator_list) == 1 and ast.dump(function.decorator_list[0]) ==
                               ast.dump(ast.parse("is_tool(ToolType.READ)", mode="eval").body))
                function.decorator_list = []
            elif function.name == "to_json_str":
                worker.require(len(function.decorator_list) == 1
                               and ast.dump(function.decorator_list[0]) == ast.dump(ast.parse("classmethod", mode="eval").body))
            else:
                worker.require(not function.decorator_list)
        scope = {"ToolCall": ToolCall, "ToolMessage": ToolMessage, "logger": mock.Mock(), "Any": object}
        exec(compile(ast.Module(body=functions, type_ignores=[]), path, "exec"), scope)
        modules[name] = scope
    # No real task content is emitted. Actual pinned task/database validation is
    # performed separately from execution with this deliberately synthetic DB.
    worker.require(type(resolution.expected_customer(root)) is str)
    tools = types.SimpleNamespace(db=types.SimpleNamespace(users={CANARY: types.SimpleNamespace(
        email="synthetic@example.test", name=types.SimpleNamespace(first_name="Synthetic", last_name="Person"),
        address=types.SimpleNamespace(zip="12345"))}))
    check = ResolutionTests()
    serialize = modules["environment/environment"]["to_json_str"].__get__(None, types.SimpleNamespace)
    check.assertIs(serialize(CANARY), CANARY)
    with check.assertRaises(ValueError):
        worker.decode(serialize(CANARY).encode())  # The original producer rejected a successful string ID.
    for tool, arguments, error in (("find_user_id_by_name_zip", dict(identity_fixture.FACTS), False),
            ("find_user_id_by_email", {"email": "synthetic@example.test"}, False),
            ("find_user_id_by_email", {"email": "absent@example.test"}, True)):
        state = Orchestrator(tool=tool)
        state.calls[0].arguments = arguments
        state.num_errors = 0
        env = types.SimpleNamespace(sync_tools=lambda: None, to_json_str=serialize)
        env.make_tool_call = lambda name, requestor, **kw: modules["domains/retail/tools"][name](tools, **kw)
        env.get_response = types.MethodType(modules["environment/environment"]["get_response"], env)
        state.environment = env
        state._execute_tool_calls = types.MethodType(modules["orchestrator/orchestrator"]["_execute_tool_calls"], state)
        if error:
            with check.assertRaises(worker.Abort):
                check.run_dialogue(state)
        else:
            check.assertEqual(check.run_dialogue(state), observation(tool=resolution.TOOLS[tool]))
        check.assertEqual(state.num_errors, int(error))
        check.assertEqual(state.calls[0].arguments, arguments)
    print("Pinned lookup/serializer/batch controls PASS; two successes and one error, no model execution.")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--upstream-root":
        pinned_upstream_audit(sys.argv[2])
    else:
        unittest.main()
