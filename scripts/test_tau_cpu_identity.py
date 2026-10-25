# SPDX-License-Identifier: Apache-2.0
"""Content-free identity diagnostics; fake completions and optional pinned-source audit."""
import ast
import copy
import hashlib
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time
import types
import unittest
from unittest import mock

import tau_cpu_identity as identity
import tau_cpu_probe as probe
import test_tau_cpu_probe as legacy
from test_tau_cpu_controller import control

ROOT = Path(__file__).resolve().parent.parent
FACTS = {"first_name": "Synthetic", "last_name": "Person", "zip": "12345"}
CANARY = "private-identity-canary"


def sources():
    user = {"user_id": "synthetic", "name": {key: FACTS[key] for key in ("first_name", "last_name")},
            "address": {"zip": FACTS["zip"]}, "email": "not-shared@example.test"}
    task = {"id": "2", "initial_state": None, "user_tools": None,
            "user_scenario": {"persona": None, "instructions": {
                "known_info": "Synthetic Person 12345", "unknown_info": "You do not remember your email."}},
            "evaluation_criteria": {"actions": [{"name": "find_user_id_by_name_zip", "arguments": FACTS},
                                               {"name": "get_user_details", "arguments": {"user_id": "synthetic"}}]}}
    return {identity.PREFIX + "tasks.json": [task], identity.PREFIX + "db.json": {"users": {"synthetic": user}}}


def encode_sources(rows):
    raw = {name: probe.tau.encoded(value) for name, value in rows.items()}
    pins = {"files": {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                      for name, data in raw.items()}}
    return raw, pins


def prepared(*, model_17b=False):
    _, docs = legacy.prepared()
    manifest = probe.policy.configuration(**({"identity_followup_17b": True} if model_17b else {"identity_followup": True}))
    plan = {"support_probe": True, "transport": "scripted-parent-pipe",
            "manifest_sha256": probe.tau.digest(probe.tau.encoded(manifest)), "selection": probe.policy.SELECTION}
    return probe.inputs(plan, manifest, ["Synthetic assertion"], fixture=FACTS), docs


def passing():
    row = legacy.passing()
    row.update(schema=identity.REPORT, roles={"agent": 0, "simulator": 3, "evaluator": 0})
    row["observations"] = [{**row["observations"][0], "step": step,
                            "identity": dict.fromkeys(identity.FIELDS, False)} for step in identity.STEPS]
    return row


class IdentityTests(unittest.TestCase):
    def test_worker_selects_identity_only_for_exact_profile_and_finalizes_logging(self):
        names = ("tau2", "tau2.runner", "tau2.runner.helpers", "tau2.runner.build", "tau2.data_model",
                 "tau2.data_model.simulation", "tau2.evaluator", "tau2.evaluator.evaluator_nl_assertions", "tau2.utils")
        modules = {name: types.ModuleType(name) for name in names}
        task = types.SimpleNamespace(initial_state=None, user_tools=None,
                                     evaluation_criteria=types.SimpleNamespace(nl_assertions=["Synthetic assertion"]))
        modules["tau2.runner.helpers"].get_tasks = mock.Mock(return_value=[task])
        modules["tau2.data_model.simulation"].TextRunConfig = lambda **kw: types.SimpleNamespace(**kw)
        owner = mock.Mock()
        builder = modules["tau2.runner.build"].build_text_orchestrator = mock.Mock(return_value=owner)
        evaluator = modules["tau2.evaluator.evaluator_nl_assertions"].NLAssertionsEvaluator = mock.Mock()
        logging = modules["tau2.utils"].llm_utils = types.SimpleNamespace(set_llm_log_dir=mock.Mock())
        for error, model_17b in itertools.product((None, ValueError(CANARY), KeyboardInterrupt()), (False, True)):
            logging.set_llm_log_dir.reset_mock()
            manifest = probe.policy.configuration(**({"identity_followup_17b": True} if model_17b else {"identity_followup": True}))
            with mock.patch.dict(sys.modules, modules), mock.patch.object(probe.worker, "configure_upstream"), \
                    mock.patch.object(identity, "run", return_value=0.0, side_effect=error) as execute:
                if error is None:
                    self.assertEqual(probe.worker.run_support_probe(None, manifest,
                                     probe.policy.SELECTION, mock.Mock()), 0.0)
                else:
                    with self.assertRaises(type(error)) as caught:
                        probe.worker.run_support_probe(None, manifest,
                                                       probe.policy.SELECTION, mock.Mock())
                    self.assertIs(caught.exception, error)
            execute.assert_called_once_with(owner)
            evaluator.calculate_reward.assert_not_called()
            owner.step.assert_not_called()
            logging.set_llm_log_dir.assert_called_once_with(None)
            config = builder.call_args.args[0]
            self.assertEqual((config.max_errors, config.max_retries, config.hallucination_retries), (1, 0, 0))
            self.assertEqual(config.llm_args_user, {"temperature": 0, "num_retries": 0})
            self.assertFalse(config.auto_review or config.auto_resume or config.verbose_logs)
            self.assertIsNone(config.save_to)

    def test_local_entry_remains_closed_to_identity_and_checks_resources(self):
        for fault in (None, "platform", "machine", "memory", "available", "cpus", "disk"):
            with self.subTest(fault=fault), mock.patch.dict(os.environ, {}, clear=True), \
                    mock.patch.object(sys, "platform", "darwin" if fault == "platform" else "linux"), \
                    mock.patch.object(os, "uname", return_value=types.SimpleNamespace(machine="arm64" if fault == "machine" else "x86_64")), \
                    mock.patch.object(os, "cpu_count", return_value=3 if fault == "cpus" else 4), \
                    mock.patch.object(control.shutil, "disk_usage", return_value=types.SimpleNamespace(free=0 if fault == "disk" else 12 << 30)), \
                    mock.patch.object(Path, "read_bytes", return_value=(
                        f"MemTotal: {1 if fault == 'memory' else 10 << 20} kB\n"
                        f"MemAvailable: {1 if fault == 'available' else 8 << 20} kB\n").encode()):
                with self.assertRaises(ValueError):
                    control.preflight()  # No ambient GitHub assertion on a local venue.
                if fault is None:
                    self.assertEqual(control.preflight(local_identity=True)["platform"], "linux/amd64")
                else:
                    self.assertRaises(ValueError, control.preflight, local_identity=True)
        runner = mock.Mock()
        runner.validate_plan.return_value = ({}, probe.policy.configuration())
        report = {"git_sha": "a" * 40, "sources": {control.rec.SCRIPT: "b" * 64}}
        supervise = mock.Mock()
        with mock.patch.object(control.rec, "toolkit"), mock.patch.object(control.rec, "load", return_value=runner), \
                mock.patch.dict(control.execute.__globals__, {"supervise": supervise}), self.assertRaises(ValueError):
            control.execute(ROOT, {}, mock.Mock(), [Path("/private/input")] * 4, Path("/private/output"),
                            report, time.monotonic() + 10, probe={"payload": Path("/private/payload"), "sha": "c" * 64},
                            local_identity=True)
        supervise.assert_not_called()
        row = control.evidence_record("a" * 40, "b" * 64, local_identity=True)
        self.assertTrue(row["command"].endswith(" --local-identity-followup"))
        self.assertFalse(row["hardware_validated"])
        self.assertRaises(ValueError, control.evidence_record, "a" * 40, local_identity=True)

    def test_profile_is_explicit_and_keeps_all_resource_limits(self):
        old = probe.policy.configuration()
        new = probe.policy.configuration(identity_followup=True)
        self.assertEqual({**new, "schema": old["schema"]}, old)
        self.assertEqual(probe.policy.validate(new), new)
        for field, value in (("requests", 4), ("run_seconds", 301), ("output_tokens_per_request", 257)):
            changed = copy.deepcopy(new)
            changed["limits"][field] = value
            with self.assertRaises(ValueError):
                probe.policy.validate(changed)
        selected, _ = prepared()
        probe.prepared_input(selected)
        for mutate in (lambda r: r.pop("identity_fixture"),
                       lambda r: r["identity_fixture"].update(email="secret@example.test"),
                       lambda r: r["identity_fixture"].update(zip=True),
                       lambda r: r["manifest"].update(schema=probe.policy.SCHEMA)):
            changed = copy.deepcopy(selected)
            mutate(changed)
            with self.assertRaises((ValueError, probe.worker.Abort)):
                probe.prepared_input(changed)

    def test_fixture_checks_hashes_unknown_email_and_unique_database_join(self):
        raw, pins = encode_sources(sources())
        self.assertEqual(identity.fixture(raw, pins), FACTS)
        with self.assertRaises(ValueError):
            identity.fixture({**raw, identity.PREFIX + "db.json": raw[identity.PREFIX + "db.json"] + b" "}, pins)
        for mode in ("email", "known-email", "wrong-tool", "wrong-id", "duplicate", "missing", "missing-name"):
            rows = sources()
            task = rows[identity.PREFIX + "tasks.json"][0]
            users = rows[identity.PREFIX + "db.json"]["users"]
            if mode == "email":
                task["user_scenario"]["instructions"]["unknown_info"] += " injected@example.test"
            elif mode == "known-email":
                task["user_scenario"]["instructions"]["unknown_info"] = "Your email is known."
            elif mode == "wrong-tool":
                task["evaluation_criteria"]["actions"][0]["name"] = "find_user_id_by_email"
            elif mode == "wrong-id":
                task["evaluation_criteria"]["actions"][1]["arguments"]["user_id"] = "other"
            elif mode == "duplicate":
                users["other"] = copy.deepcopy(users["synthetic"])
            elif mode == "missing":
                users.clear()
            else:
                task["user_scenario"]["instructions"]["known_info"] = "12345"
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                identity.fixture(*encode_sources(rows))

    def test_projection_is_literal_bounded_and_cannot_leak_or_claim_semantics(self):
        for content, email, name in (("I do not know my email.", False, False),
                ("SYNTHETIC Person 12345", False, True),
                ("Do not use wrong@example.test", True, False),
                ("Syntheticperson Person 123456", False, False)):
            row = identity.project(content + " " + CANARY, FACTS)
            self.assertEqual(row["email_like_literal"], email)
            self.assertEqual(row["first_name_literal"] and row["last_name_literal"] and row["zip_literal"], name)
            self.assertEqual(set(row), set(identity.FIELDS))
            self.assertNotIn(CANARY, json.dumps(row))
        self.assertFalse(identity.project("x" * (1 << 20), FACTS)["email_like_literal"])
        self.assertRaises(ValueError, identity.project, "x" * ((1 << 20) + 1), FACTS)

    def test_fixed_original_user_path_preserves_history_and_stops_on_failure(self):
        message = lambda **values: types.SimpleNamespace(**values)
        for stop_at in (None, 0, 1, 2):
            state = object()
            user = mock.Mock()
            reply = mock.Mock()
            reply.is_tool_call.return_value = False
            user.is_stop.side_effect = [index == stop_at for index in range(3)]
            user.generate_next_message.return_value = (reply, state)
            initial = message(content="original-initial")
            owner = types.SimpleNamespace(user=user, user_state=state, message=initial, initialize=mock.Mock())
            with mock.patch.dict(sys.modules, {"tau2.data_model.message": types.SimpleNamespace(AssistantMessage=message)}):
                if stop_at is None:
                    self.assertEqual(identity.run(owner), 0.0)
                else:
                    self.assertRaises(ValueError, identity.run, owner)
            owner.initialize.assert_called_once()
            calls = user.generate_next_message.call_args_list
            self.assertEqual(len(calls), 3 if stop_at is None else stop_at + 1)
            self.assertIs(calls[0].args[0], initial)
            self.assertTrue(all(call.args[1] is state for call in calls))
            for index, call in enumerate(calls[1:]):
                self.assertEqual(call.args[0].content, identity.QUESTIONS[index])

    def test_report_mutations_and_role_substitution_are_rejected(self):
        original = passing()
        self.assertEqual(probe.validate(original), original)
        for mutate in (lambda r: r.update(schema="nib.tau-cpu-probe/v1"),
                lambda r: r["roles"].update(evaluator=1), lambda r: r["roles"].update(simulator=True),
                lambda r: r["selection"].update(seed=43), lambda r: r["observations"][0].update(step="email"),
                lambda r: r["observations"][0].update(identity=None),
                lambda r: r["observations"][0]["identity"].update(email_like_literal=1),
                lambda r: r["observations"][0]["identity"].update(content=CANARY),
                lambda r: r.update(reward=1.0), lambda r: r.update(quality_eligible=True)):
            row = copy.deepcopy(original)
            mutate(row)
            with self.assertRaises((ValueError, probe.worker.Abort)):
                probe.validate(row)

    def test_private_pipes_three_real_accounted_replies_and_failure_controls(self):
        child_code = legacy.CHILD.replace("('simulator','agent','evaluator')", "('simulator','simulator','simulator')")
        for mode, model_17b in itertools.product(("pass", "email", "truncated", "bad-usage", "extra-request", "cancelled", "timeout",
                     "legacy-profile", "wrong-role", "early-success"), (False, True)):
            selected, docs = prepared(model_17b=model_17b)
            if mode == "legacy-profile":
                selected, docs = legacy.prepared()
            runtimes = {role: docs[role + "-runtime.json"] for role in ("simulator", "evaluator")}
            endpoints = {role: mock.Mock(port=row["endpoint"]["port"], expected=row["process"])
                         for role, row in runtimes.items()}
            content = CANARY + (" wrong@example.test" if mode == "email" else " Synthetic Person 12345")
            response = {"choices": [{"message": {"role": "assistant", "content": content},
                                    "finish_reason": "length" if mode == "truncated" else "stop"}],
                        "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                                  "total_tokens": 999 if mode == "bad-usage" else 110}}
            client = mock.Mock(ids=None)
            client.count.return_value = 100
            client.complete.return_value = response
            if mode == "cancelled":
                client.complete.side_effect = KeyboardInterrupt()
            if mode == "timeout":
                client.complete.side_effect = probe.worker.Abort("timeout")
            code = child_code.replace("('simulator','simulator','simulator')", "('simulator',)*4") if mode == "extra-request" else child_code
            if mode == "wrong-role":
                code = child_code.replace("('simulator','simulator','simulator')", "('simulator','evaluator','simulator')")
            if mode == "early-success":
                code = child_code.replace("('simulator','simulator','simulator')", "('simulator',)")
            child = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", code, str(ROOT / "scripts")],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            try:
                with self.subTest(mode=mode, model_17b=model_17b), mock.patch.object(probe, "ProbeClient", return_value=client) as factory:
                    row = probe.execute(selected, endpoints, runtimes, [], os.dup(child.stdout.fileno()),
                                        os.dup(child.stdin.fileno()), time.monotonic() + 3)
                self.assertEqual(row["result"], "pass" if mode in ("pass", "email") else "fail", row)
                self.assertNotIn(CANARY, json.dumps(row))
                self.assertNotIn("example.test", json.dumps(row))
                if model_17b and mode != "legacy-profile":
                    runtime, endpoint = runtimes["evaluator"], endpoints["evaluator"]
                    factory.assert_called_once_with(endpoint.port, probe.policy.MODELS[1], endpoint)
                    self.assertEqual(row["schema"], probe.policy.IDENTITY_17_REPORT)
                    self.assertEqual(row["simulator_binding"], probe.policy.runtime_binding(runtime))
                self.assertEqual(row["roles"]["agent"], 0)
                self.assertEqual(row["roles"]["evaluator"], 0)
                if mode in ("pass", "email", "extra-request"):
                    self.assertEqual(row["roles"]["simulator"], 3)
                    self.assertEqual(sum(item["completion_tokens"] for item in row["observations"]), 30)
                    self.assertEqual([item["step"] for item in row["observations"]], list(identity.STEPS))
                if mode in ("pass", "email"):
                    self.assertEqual([item["identity"]["email_like_literal"] for item in row["observations"]], [mode == "email"] * 3)
                if mode == "truncated":
                    self.assertEqual(row["status"], "truncated")
                    self.assertEqual(row["observations"][0]["completion_tokens"], 10)
                for call in client.complete.call_args_list:
                    self.assertEqual(call.args[0]["model"], selected["manifest"]["support"]["simulator"]["model"])
                    self.assertEqual(call.args[0]["max_completion_tokens"], 256)
            finally:
                child.stdin.close()
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=2)
                child.stdout.close()


def original_user_methods(raw):
    """Execute three exact pinned method bodies with synthetic messages/LLM only."""
    scope = {"logger": mock.Mock()}
    class Message:
        def __init__(self, **values):
            self.__dict__.update(is_audio=False, tool_calls=None, cost=None, usage=None, raw_data=None)
            self.__dict__.update(values)
        def has_content(self):
            return bool(self.content)
        def is_tool_call(self):
            return bool(self.tool_calls)
        def validate(self):
            identity.require(type(self.content) is str)
    for name in ("AssistantMessage", "UserMessage", "ToolMessage", "MultiToolMessage"):
        scope[name] = type(name, (Message,), {})
    methods = {}
    for path, cls_name, selected in (("src/tau2/user/user_simulator.py", "UserSimulator", ("generate_next_message", "_generate_next_message")),
                                     ("src/tau2/user/user_simulator_base.py", "UserState", ("flip_roles",))):
        nodes = [node for cls in ast.parse(raw[path]).body if isinstance(cls, ast.ClassDef) and cls.name == cls_name
                 for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in selected]
        identity.require(len(nodes) == len(selected) and {node.name for node in nodes} == set(selected)
                         and all(not node.decorator_list for node in nodes))
        tree = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes], type_ignores=[])
        exec(compile(ast.fix_missing_locations(tree), path, "exec"), scope)
        methods.update({name: scope[name] for name in selected})
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        return scope["AssistantMessage"](role="assistant", content=CANARY)
    scope["generate"] = generate
    user_type = type("PinnedUser", (), {key: methods[key] for key in ("generate_next_message", "_generate_next_message")})
    user = user_type()
    user.llm, user.llm_args, user.tools = "nib-scripted-simulator", {"temperature": 0, "num_retries": 0}, None
    user.is_stop = lambda message: False
    state_type = type("PinnedState", (), {"flip_roles": methods["flip_roles"]})
    state = state_type()
    state.system_messages, state.messages = [object()], []
    owner = types.SimpleNamespace(user=user, user_state=state, initialize=lambda: None,
                                 message=scope["AssistantMessage"](role="assistant", content="synthetic-initial"))
    with mock.patch.dict(sys.modules, {"tau2.data_model.message": types.SimpleNamespace(AssistantMessage=scope["AssistantMessage"])}):
        identity.require(identity.run(owner) == 0.0)
    identity.require(len(calls) == 3 and len(state.messages) == 6)
    for index, call in enumerate(calls):
        identity.require(call["model"] == user.llm and call["tools"] is None and call["num_retries"] == 0
                         and call["temperature"] == 0 and call["call_name"] == "user_simulator_response")
        identity.require(call["messages"][0] is state.system_messages[0]
                         and [msg.role for msg in call["messages"][1:]] == ["user", "assistant"] * index + ["user"])
    identity.require([call["messages"][-1].content for call in calls[1:]] == list(identity.QUESTIONS))


def pinned_audit(archive):
    pins_raw = (ROOT / probe.tau.PINS).read_bytes()
    identity.require(hashlib.sha256(pins_raw).hexdigest() == probe.tau.PINS_SHA)
    pins = json.loads(pins_raw)
    lock = json.loads((ROOT / "bench/tau-runtime-lock.json").read_bytes())
    identity.require(hashlib.sha256(Path(archive).read_bytes()).hexdigest() == lock["archive_sha256"])
    with tarfile.open(archive) as source:
        raw = {}
        for name in ("tasks.json", "db.json"):
            path = identity.PREFIX + name
            members = [item for item in source.getmembers() if item.name.endswith("/" + path)]
            identity.require(len(members) == 1 and members[0].isfile() and members[0].size <= 16 << 20)
            raw[path] = source.extractfile(members[0]).read((16 << 20) + 1)
        identity.fixture(raw, pins)
        for path in ("src/tau2/user/user_simulator.py", "src/tau2/user/user_simulator_base.py"):
            expected = lock["source_files"][path]
            members = [item for item in source.getmembers() if item.name.endswith("/" + path)]
            identity.require(len(members) == 1 and members[0].isfile() and members[0].size == expected["bytes"])
            raw[path] = source.extractfile(members[0]).read(expected["bytes"] + 1)
            identity.require(len(raw[path]) == expected["bytes"] and hashlib.sha256(raw[path]).hexdigest() == expected["sha256"])
        original_user_methods(raw)
    print("Pinned task/database and three original simulator/history method controls PASS; no task values or model execution.")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--upstream-source":
        pinned_audit(sys.argv[2])
    else:
        unittest.main()
