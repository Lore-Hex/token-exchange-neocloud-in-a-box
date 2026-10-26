# SPDX-License-Identifier: Apache-2.0
"""Full original task wiring with synthetic endpoints; no inference or keys."""

import ast
import copy
from contextlib import redirect_stdout
from enum import Enum
import io
import json
from decimal import Decimal
from pathlib import Path
import subprocess
import stat
import sys
import tempfile
import time
import types
from typing import Optional
import unittest
from unittest import mock

import tau_external as external
import tau_pipe_trial as pipe
import test_tau_external as lookup
import test_tau_selected_termination as termination

policy, worker, runner = external.policy, external.worker, lookup.runner


def configuration():
    return policy.configuration(external_simulator=True, full_task=True)


def body(role="candidate"):
    return {**lookup.body(role), "max_completion_tokens": 2048}


def runtime():
    held = lookup.runtime()
    config = configuration()
    held.manifest.update(schema=policy.EXTERNAL_TRIAL_TRANSPORT, input=config, settings=policy.settings(config))
    held.manifest["framing"]["evaluator"] = copy.deepcopy(held.manifest["framing"]["simulator"])
    budget = external.CountBudget()
    held.endpoints = {}
    for role, row in held.manifest["framing"].items():
        row["context_tokens"] = 32768
        client = external.Endpoint(role, lookup.FakeHTTP(role), b"SYNTHETIC_KEY_CANARY",
            types.SimpleNamespace(count=mock.Mock(return_value=20)), full_task=True, count_budget=budget)
        if role == "evaluator":
            client.http.result["output"][0]["content"][0]["text"] = worker.encoded({"results": [{
                "expectedOutcome": "synthetic", "metExpectation": False, "reasoning": "SYNTHETIC_REASON_CANARY"}]}).decode()
        held.endpoints[role] = client
    return held


def terminal(session, reward=0.0):
    return {"schema": external.local.transport.SCHEMA, "kind": "finished", "sequence": session.sequence + 1,
            "status": "evaluated", "reward": reward, "upstream_termination": {"reason": "user_stop", "tool_errors": 0},
            "assertions_sha256": worker.digest(worker.encoded(["synthetic"]))}


def complete_roles(session, channel, roles=("agent", "simulator", "evaluator")):
    for category in roles:
        channel.packets.append(lookup.channels.fixtures.packet(session.sequence + 1, category))
        if session.serve_once():
            raise AssertionError("early terminal")


class ExternalTrialTests(unittest.TestCase):
    def test_committed_lookup_and_selected_policy_bytes_unchanged(self):
        original = subprocess.run(["git", "show", "03b8b3744709b01abf0c1377eb8e8640a69e0587:scripts/tau_selected_policy.py"],
            cwd=runner.ROOT, capture_output=True, check=True, timeout=10).stdout
        before = types.ModuleType("reviewed_lookup_policy")
        exec(compile(original, "<reviewed-403-policy>", "exec"), before.__dict__)
        for options in ({}, {"customer_resolution": True}, {"customer_resolution": True, "external_simulator": True}):
            self.assertEqual(worker.encoded(before.configuration(**options)), worker.encoded(policy.configuration(**options)))
            self.assertEqual(worker.encoded(before.settings(before.configuration(**options))),
                             worker.encoded(policy.settings(policy.configuration(**options))))

    def test_closed_profile_is_not_lookup_and_preserves_defaults(self):
        value = configuration()
        self.assertTrue(policy.is_external(value) and policy.is_external_trial(value))
        self.assertFalse(policy.is_resolution(value))
        self.assertEqual(value["selection"], policy.support.SELECTION)
        self.assertEqual(value["contexts"], dict.fromkeys(("candidate", "simulator", "evaluator"), 32768))
        self.assertEqual(value["generation"]["max_tokens"], 2048)
        self.assertEqual(value["limits"]["requests"], 32)
        self.assertEqual(value["limits"]["total_tokens"], 1114112)
        self.assertEqual(value["limits"]["cost_usd"], "3.00")
        self.assertEqual(value["evaluation"], {"type": "all_with_nl_assertions", "auto_review": False,
            "reward_basis": "original-task", "first_lookup_stop": False})
        self.assertFalse(value["quality_eligible"])
        self.assertEqual(policy.configuration()["generation"]["max_tokens"], 256)
        self.assertTrue(policy.is_resolution(lookup.runtime().manifest["input"]))
        for kwargs in ({"full_task": True}, {"full_task": True, "external_simulator": True, "customer_resolution": True}):
            with self.assertRaises(ValueError):
                policy.configuration(**kwargs)
        for mutate in (lambda v: v["evaluation"].update(type="all_ignore_basis"),
                       lambda v: v["evaluation"].update(auto_review=True),
                       lambda v: v["support"]["evaluator"].update(model="other"),
                       lambda v: v["contexts"].update(candidate=8192),
                       lambda v: v["limits"].update(retries=1), lambda v: v.update(quality_eligible=True)):
            altered = copy.deepcopy(value)
            mutate(altered)
            with self.assertRaises(ValueError):
                policy.validate(altered)

    def test_transport_all_three_roles_context_and_output_bound(self):
        manifest = runtime().manifest
        policy.transport_manifest(manifest, worker.digest(worker.encoded(manifest)))
        for mutate in (lambda m: m.update(schema=policy.EXTERNAL_TRANSPORT),
                       lambda m: m["framing"].pop("evaluator"),
                       lambda m: m["framing"]["candidate"].update(context_tokens=8192),
                       lambda m: m["settings"]["roles"]["evaluator"]["generation"].update(max_tokens=256)):
            altered = copy.deepcopy(manifest)
            mutate(altered)
            with self.assertRaises(ValueError):
                policy.transport_manifest(altered, worker.digest(worker.encoded(altered)))

    def test_runtime_requires_new_contract_before_keys_or_http(self):
        config = configuration()
        contract = {"schema": "nib.tau-external-trial-endpoint/v1", "hostname": "candidate.example.test",
            "address": "8.8.8.8", "port": 443, "model": policy.PUBLIC_MODEL, "context_tokens": 32768,
            "output_tokens": 2048, "prices": config["prices"], "simulator": policy.EXTERNAL_MODEL,
            "evaluator": policy.EXTERNAL_MODEL, "candidate_profile": "quality32k-eager",
            "retire_at": int(time.time()) + 1000, "conformance_sha256": "a" * 64}
        mutations = (None, lambda c: c.update(schema="nib.tau-external-endpoint/v1"),
                     lambda c: c.update(context_tokens=8192), lambda c: c.update(output_tokens=256),
                     lambda c: c.update(context_tokens=32768.0),
                     lambda c: c.update(candidate_profile="tau8k-eager"), lambda c: c.update(evaluator="other"))
        for mutate in mutations:
            value = copy.deepcopy(contract)
            if mutate: mutate(value)
            raw = worker.encoded(value)
            counter = types.SimpleNamespace(binding="b" * 64, close=mock.Mock())
            with mock.patch.object(external.private, "private_read", side_effect=[raw, b"SYNTHETIC_KEY_CANARY", b"SYNTHETIC_SIMULATOR_KEY"]) as read, \
                    mock.patch.object(external.tau_external_tokenizer, "Counter", return_value=counter) as build, \
                    mock.patch.object(external.bounded_https, "HTTPS") as http:
                args = (None, worker.digest(raw), None, None, None, None, None, time.monotonic() + 90)
                if mutate:
                    with self.assertRaises(ValueError):
                        external.Runtime(*args, full_task=True)
                    self.assertEqual(read.call_count, 1)
                    build.assert_not_called()
                    http.assert_not_called()
                else:
                    held = external.Runtime(*args, full_task=True)
                    self.assertEqual(set(held.endpoints), {"candidate", "simulator", "evaluator"})
                    self.assertIs(held.endpoints["simulator"].count_budget, held.endpoints["evaluator"].count_budget)
                    build.assert_called_once_with(None, None, None, context_tokens=32768)
                    self.assertEqual(http.call_count, 3)
                    self.assertEqual(http.call_args_list[-1].args, ("api.openai.com", (external.COUNT, external.RESPONSES)))
                    held.close()
                    counter.close.assert_called_once()
                    self.assertTrue(all(c.key == b"" for c in held.endpoints.values()))

    def test_prepared_full_task_cannot_run_under_default_lookup_opt_in(self):
        for manifest, opt_in in ((configuration(), {}), (lookup.runtime().manifest["input"], {"full_task": True})):
            with mock.patch.object(runner, "identity", return_value={}), \
                    mock.patch.object(runner, "validate_plan", return_value=({}, manifest)), \
                    mock.patch.object(runner, "parent_driver", return_value=external), \
                    mock.patch.object(runner.probe, "capture") as command, self.assertRaises(ValueError):
                runner.run(None, None, None, None, parent_pipe=True,
                    external_inputs={"deadline": time.monotonic() + 90, "arguments": (None,) * 7, **opt_in})
            command.assert_not_called()

    def test_new_source_closure_cannot_import_ambient_helpers(self):
        names = ("tau_preflight", "tau_trial_worker", "tau_aggregate", "tau_transport",
                 *runner.SELECTED_PARENT_MODULES, "tau_pipe_driver", "tau_selected_driver", *runner.EXTERNAL_PARENT_MODULES)
        toolkit = {"files": {"scripts/" + name + ".py": worker.digest(
            (runner.ROOT / ("scripts/" + name + ".py")).read_bytes()) for name in names}}
        with mock.patch.dict(sys.modules, {"tau_external": object(), "tau_selected_policy": object()}):
            driver = runner.parent_driver(toolkit, selected_operational=True, external_simulator=True)
            self.assertEqual(driver.policy.configuration(external_simulator=True, full_task=True), configuration())
        toolkit["files"]["scripts/tau_selected_policy.py"] = "0" * 64
        with self.assertRaises(ValueError):
            runner.parent_driver(toolkit, selected_operational=True, external_simulator=True)

    def test_cli_explicit_full_prepare_and_run_without_new_inputs(self):
        entry = runner.load("scripts/record-tau-external.py")
        with mock.patch.object(entry.trial, "prepare", return_value=({}, {"producer": {"git_sha": "a" * 40}})) as prepare, \
                mock.patch.object(entry.trial, "publish"), redirect_stdout(io.StringIO()), \
                mock.patch.object(sys, "argv", ["record-tau-external.py", "prepare", "--full-task", "--source-archive", "/unused",
                                               "--wheelhouse", "/unused", "--output", "/unused"]):
            self.assertEqual(entry.main(), 0)
            self.assertEqual(prepare.call_args.kwargs, {"parent_pipe": True, "selected_operational": True,
                "customer_resolution": False, "external_simulator": True, "full_task": True})
        with tempfile.TemporaryDirectory() as temporary:
            arguments = ["record-tau-external.py", "run", "--full-task"]
            for name in ("payload", "docker-socket", "contract", "candidate-key", "simulator-key", "vllm-source",
                         "tokenizer", "tokenizer-wheel"):
                arguments.extend(["--" + name, "/unused"])
            cutoff = entry.datetime.datetime.fromtimestamp(time.time() + 90, entry.datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            arguments.extend(["--output", temporary + "/result", "--plan-sha256", "a" * 64,
                "--contract-sha256", "b" * 64, "--deadline-utc", cutoff])
            with mock.patch.object(sys, "argv", arguments), redirect_stdout(io.StringIO()), \
                    mock.patch.object(entry.trial, "run", return_value={"result": "pass", "cleanup": "confirmed"}) as run:
                self.assertEqual(entry.main(), 0)
                inputs = run.call_args.kwargs["external_inputs"]
                self.assertIs(inputs["full_task"], True)
                self.assertEqual(len(inputs["arguments"]), 7)
                self.assertLessEqual(inputs["deadline"] - time.monotonic(), 90)

    def test_full_lifecycle_retains_scored_zero_on_late_failure(self):
        for failure in (None, "terminal", "cleanup"):
            held, plan = runtime(), lookup.scorer.fixture()[3]
            for client in held.endpoints.values():
                client.attempts = 1
            observed = {"schema": "nib.tau-external-full-task/v1", "scorer_protocol_completed": True,
                "attach_cleanup": "reaped", "scorer_exit_code": 0, "trial": {"status": "evaluated", "reward": "0.0",
                    "usage": {"total_tokens": 69}, "upstream_termination": {"reason": "user_stop", "tool_errors": 0}},
                "endpoints": {role: c.observation() for role, c in held.endpoints.items()}}
            module = types.SimpleNamespace(policy=policy, Runtime=mock.Mock(return_value=held),
                run_process=mock.Mock(return_value=observed), accepted=external.accepted)
            state, published = {"exists": False, "inspections": 0}, {}
            def capture(argv, **kwargs):
                args = argv[5:]
                if args[:2] == ["image", "inspect"]:
                    return worker.encoded([lookup.fixtures.image_inspection()])
                if args[0] == "create":
                    self.assertEqual(args[args.index("--network") + 1], "none")
                    self.assertNotIn("--gpus", args)
                    state.update(exists=True, info=lookup.fixtures.LifecycleTests().info(args))
                    state["info"]["Config"]["OpenStdin"] = True
                    return b"d" * 64
                if args[0] == "inspect":
                    state["inspections"] += 1
                    row = copy.deepcopy(state["info"])
                    if failure == "terminal" and state["inspections"] == 2:
                        row["State"]["ExitCode"] = 1
                    return worker.encoded([row])
                if args[0] == "ps": return b"d" * 64 if state["exists"] else b""
                if args[0] == "rm":
                    if failure == "cleanup": raise OSError("SYNTHETIC_PRIVATE_CANARY")
                    state["exists"] = False
                    return b""
                self.fail("unexpected Docker command")
            socket = mock.Mock()
            socket.is_absolute.return_value = True
            socket.stat.return_value = types.SimpleNamespace(st_mode=stat.S_IFSOCK)
            with tempfile.TemporaryDirectory() as temporary, \
                    mock.patch.object(runner, "identity", return_value={"git_sha": "a" * 40}), \
                    mock.patch.object(runner, "validate_plan", return_value=(plan, configuration())), \
                    mock.patch.object(runner, "parent_driver", return_value=module), \
                    mock.patch.object(runner.shutil, "which", return_value="/fixed/docker"), \
                    mock.patch.object(runner.probe, "capture", side_effect=capture), \
                    mock.patch.object(runner, "publish_evidence", side_effect=lambda p, f: published.update(f)):
                record = runner.run(Path("/unused"), "a" * 64, Path(temporary) / "result", socket, parent_pipe=True,
                    external_inputs={"deadline": time.monotonic() + 90, "arguments": (None,) * 7, "full_task": True})
            self.assertEqual(record["result"], "pass" if failure is None else "fail")
            self.assertEqual(record["schema"], "nib.tau-external-full-task-run/v1")
            self.assertEqual(record["trial"]["trial"]["reward"], "0.0")
            self.assertNotIn("diagnostic", record)
            self.assertFalse(record["full_benchmark"] or record["quality_eligible"] or record["public_owner_mutated"])
            self.assertEqual(module.Runtime.call_args.kwargs, {"full_task": True})
            self.assertNotIn(b"CANARY", b"".join(published.values()))

    def test_full_history_tools_and_evaluator_input_unchanged(self):
        original = body()
        original.update(tools=[lookup.tool()], tool_choice="auto")
        original["messages"] += [{"role": "assistant", "content": None, "tool_calls": [{"id": "call_1",
            "type": "function", "name": "lookup", "function": {"name": "lookup", "arguments": '{ "key": "original" }'}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": '"original tool result"'},
            {"role": "user", "content": "SYNTHETIC_LATER_TASK_CANARY"}]
        frozen = copy.deepcopy(original)
        wire = external.candidate_request(original, full_task=True)
        self.assertEqual(original, frozen)
        self.assertEqual(wire["tools"], original["tools"])
        self.assertEqual(wire["messages"][-2:], original["messages"][-2:])
        self.assertEqual(wire["messages"][-3]["tool_calls"][0]["function"],
                         original["messages"][-3]["tool_calls"][0]["function"])
        client, request = runtime().endpoints["evaluator"], body("evaluator")
        request["messages"][-1]["content"] = "SYNTHETIC_ORIGINAL_FULL_TRAJECTORY_AND_ASSERTIONS"
        count = client.count(request, 32768, time.monotonic() + 5)
        client.complete(request, count, time.monotonic() + 5)
        counted, generated = [row[1] for row in client.http.calls]
        self.assertEqual(counted["input"], request["messages"])
        self.assertEqual(generated["input"], counted["input"])
        self.assertEqual(generated["max_output_tokens"], 2048)
        self.assertEqual(generated["truncation"], "disabled")

    def test_context_wire_and_output_caps_fail_before_inference(self):
        for role in ("candidate", "simulator", "evaluator"):
            for count in (30720, 30721):
                client = runtime().endpoints[role]
                client.http.count = count
                client.counter = types.SimpleNamespace(count=lambda body: count)
                if count == 30720:
                    self.assertEqual(client.count(body(role), 32768, time.monotonic() + 5), count)
                else:
                    with self.assertRaises(ValueError):
                        client.count(body(role), 32768, time.monotonic() + 5)
                self.assertEqual(client.attempts, 0)
                self.assertFalse(any(path in (external.RESPONSES, external.CHAT) for path, _ in client.http.calls))
        for mutate in (lambda b: b.update(max_completion_tokens=256),
                       lambda b: b["messages"].extend([{"role": "user", "content": "x"}] * 128),
                       lambda b: b["messages"][0].update(content="x" * 65536),
                       lambda b: b.update(tools=[lookup.tool()] * 17)):
            request, client = body(), runtime().endpoints["candidate"]
            mutate(request)
            with self.assertRaises(ValueError):
                client.count(request, 32768, time.monotonic() + 5)
            self.assertEqual(client.http.calls, [])

    def test_count_budget_shared_lost_response_charged_no_retry(self):
        held = runtime()
        for index in range(32):
            role = "simulator" if index % 2 else "evaluator"
            client = held.endpoints[role]
            client.count(body(role), 32768, time.monotonic() + 5)
            client.ready = None
        for role in ("simulator", "evaluator"):
            with self.assertRaises(ValueError):
                held.endpoints[role].count(body(role), 32768, time.monotonic() + 5)
        self.assertEqual(sum(c.count_attempts for c in held.endpoints.values()), 32)
        self.assertEqual(sum(Decimal(c.observation()["count_cost_reserved_usd"]) for c in held.endpoints.values()),
                         Decimal("67.044864"))
        client = runtime().endpoints["evaluator"]
        client.http.post = mock.Mock(side_effect=OSError("SYNTHETIC_PRIVATE_CANARY"))
        with self.assertRaises(OSError):
            client.count(body("evaluator"), 32768, time.monotonic() + 5)
        self.assertEqual(client.count_attempts, 1)
        self.assertEqual(client.count_budget.attempts, 1)
        self.assertEqual(client.http.post.call_count, 1)

    def test_evaluated_zero_and_one_retained_with_all_role_usage(self):
        for reward in (0.0, 1.0):
            held, channel = runtime(), lookup.channels.FakeChannel()
            session = external.Session(channel, held, assertions=["synthetic"])
            complete_roles(session, channel)
            channel.packets.append(terminal(session, reward))
            self.assertTrue(session.serve_once())
            self.assertEqual(session.result["reward"], str(reward))
            self.assertEqual(session.result["usage"]["total_tokens"], 69)
            self.assertEqual(session.result["role_requests"], {"agent": 1, "simulator": 1, "evaluator": 1})
            self.assertNotIn("customer_resolution", session.result)
            observed = {"schema": "nib.tau-external-full-task/v1", "trial": session.result,
                "scorer_protocol_completed": True, "attach_cleanup": "reaped", "scorer_exit_code": 0,
                "endpoints": {role: c.observation() for role, c in held.endpoints.items()}}
            self.assertTrue(external.accepted(observed))
            self.assertNotIn("CANARY", str(observed))
            session.close()

    def test_no_lookup_early_terminal_missing_evaluator_or_tampered_finish(self):
        for failure in ("evaluator", "termination", "reward", "assertions", "lookup", "ack"):
            held, channel = runtime(), lookup.channels.FakeChannel()
            session = external.Session(channel, held, assertions=["synthetic"])
            roles = ("agent", "simulator") if failure == "evaluator" else ("agent", "simulator", "evaluator")
            complete_roles(session, channel, roles)
            packet = terminal(session)
            if failure == "termination": packet["upstream_termination"] = None
            if failure == "reward": packet["reward"] = None
            if failure == "assertions": packet["assertions_sha256"] = "a" * 64
            if failure == "lookup": packet["customer_resolution"] = {"outcome": "resolved"}
            if failure == "ack": channel.send = mock.Mock(side_effect=OSError("SYNTHETIC_PRIVATE_CANARY"))
            channel.packets.append(packet)
            with self.subTest(failure=failure), self.assertRaises(worker.Abort):
                session.serve_once()
            self.assertIsNone(session.result)
            self.assertEqual(session.bridge.usage()["total_tokens"], 23 * len(roles))
            session.close()

    def test_evaluator_bad_json_and_timeout_preserve_measured_endpoint_usage(self):
        for failure in ("json", "length", "lost"):
            held, channel = runtime(), lookup.channels.FakeChannel()
            client = held.endpoints["evaluator"]
            if failure == "json": client.http.result["output"][0]["content"][0]["text"] = "SYNTHETIC_BAD_CANARY"
            if failure == "length": client.http.result.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
            if failure == "lost": client.http.result = TimeoutError("SYNTHETIC_PRIVATE_CANARY")
            session = external.Session(channel, held, assertions=["synthetic"])
            with self.subTest(failure=failure), self.assertRaises(worker.Abort):
                complete_roles(session, channel, ("evaluator",))
            self.assertEqual(client.unknown, int(failure == "lost"))
            self.assertEqual(client.observation()["known_usage"]["total_tokens"], 0 if failure == "lost" else 23)
            self.assertNotIn("CANARY", str(client.observation()))
            session.close()

    def test_native_candidate_cap_uses_full_profile_without_weakening_guard(self):
        for tokens, finish, status in ((2048, "tool_calls", "protocol_error"), (2047, "tool_calls", None),
                                      (2048, "length", "truncated")):
            held, channel = runtime(), lookup.channels.FakeChannel([lookup.channels.fixtures.packet()])
            reply = lookup.selected_fixtures.capped_tool_response(tokens, finish)
            reply["usage"].update(prompt_tokens=20, total_tokens=20 + tokens)
            held.endpoints["candidate"].http.result = {"model": policy.PUBLIC_MODEL, **reply}
            session = external.Session(channel, held, assertions=["synthetic"])
            if status is None:
                self.assertFalse(session.serve_once())
            else:
                with self.assertRaises(worker.Abort) as caught:
                    session.serve_once()
                self.assertEqual(caught.exception.status, status)
            self.assertEqual(session.bridge.usage()["completion_tokens"], tokens)
            session.close()

    def test_original_runner_no_early_lookup_and_no_optional_judge(self):
        for reason, reward in (("user_stop", 0.0), ("agent_stop", 1.0), ("too_many_errors", None)):
            value = termination.simulation(reason, int(reason == "too_many_errors"))
            if reward is not None: value.reward_info.reward = reward
            channel = termination.Channel()
            proxy = pipe.transport.WorkerProxy(channel, time.monotonic() + 5, selected_operational=True)
            with termination.upstream(value) as run, mock.patch.object(worker, "text_config", wraps=worker.text_config) as config:
                pipe.execute(None, configuration(), dict(policy.support.SELECTION), proxy, run=worker.run_selected_upstream)
                run.assert_called_once()
                self.assertEqual(run.call_args.kwargs["evaluation_type"], "all-nl")
                self.assertFalse(run.call_args.kwargs["auto_review"])
                self.assertEqual(config.call_args.args[1]["roles"]["evaluator"]["generation"]["max_tokens"], 2048)
            self.assertEqual(channel.sent[-1]["reward"], reward)
            self.assertNotIn("customer_resolution", channel.sent[-1])
            self.assertEqual(channel.sent[-1]["upstream_termination"]["reason"], reason)
            self.assertNotIn("CANARY", str(channel.sent[-1]))

    def test_failure_during_finalization_does_not_invent_reward(self):
        channel = termination.Channel()
        proxy = pipe.transport.WorkerProxy(channel, time.monotonic() + 5, selected_operational=True)
        with termination.upstream(termination.simulation("user_stop", 0), cleanup=RuntimeError("SYNTHETIC_PRIVATE_CANARY")):
            pipe.execute(None, configuration(), dict(policy.support.SELECTION), proxy, run=worker.run_selected_upstream)
        self.assertNotEqual(channel.sent[-1]["status"], "evaluated")
        self.assertIsNone(channel.sent[-1]["reward"])
        self.assertNotIn("CANARY", str(channel.sent[-1]))

    def test_real_pipe_whole_task_ack_and_late_attach_failure(self):
        program = '''import sys,os,time
sys.path.insert(0,sys.argv[1])
import tau_transport as t
c=t.Channel(os.dup(0),os.dup(1))
w=t.WorkerProxy(c,time.monotonic()+10,selected_operational=True)
w.assertions=["synthetic"]
for role in ("simulator","agent","simulator","agent","evaluator"):
 w.complete(model=t.ALIASES[role],messages=[{"role":"user","content":"SYNTHETIC_HISTORY_CANARY"}])
w.upstream_termination={"reason":"user_stop","tool_errors":0}
w.finish("evaluated",0.0)
c.close()
if sys.argv[2]=="trailing": os.write(1,b"SYNTHETIC_TRAILING_CANARY")
sys.exit(1 if sys.argv[2]=="exit" else 0)
'''
        for failure in ("none", "trailing", "exit"):
            with mock.patch.object(external.pipe, "selected_assertions", return_value=["synthetic"]):
                report = external.run_process([sys.executable, "-I", "-S", "-B", "-c", program,
                    str(runner.ROOT / "scripts"), failure], {}, Path("/unused"), {"selection": {}}, runtime(),
                    time.monotonic() + 65)
            self.assertEqual(external.accepted(report), failure == "none")
            self.assertEqual(report["attach_cleanup"], "reaped")
            self.assertEqual(report["trial"]["reward"], "0.0")
            self.assertEqual(report["trial"]["role_requests"], {"agent": 2, "simulator": 2, "evaluator": 1})
            self.assertEqual(report["trial"]["usage"]["total_tokens"], 115)
            self.assertEqual(report["cost_ceiling_usd"], "70.044864")
            self.assertFalse(report["quality_eligible"] or report["full_benchmark"])
            self.assertNotIn("CANARY", str(report))

    def test_full_request_and_generation_budget_shared_across_roles(self):
        budget = policy.ResourceBudget(configuration(), clock=lambda: 1)
        budget.begin_trial()
        for index in range(32):
            role = ("candidate", "simulator", "evaluator")[index % 3]
            self.assertEqual(budget.reserve(role)["max_tokens"], 2048)
            budget.settle({"prompt_tokens": 30720, "completion_tokens": 2048, "total_tokens": 32768})
        self.assertLessEqual(budget.spent, Decimal("3.00"))
        with self.assertRaises(ValueError):
            budget.reserve("evaluator")
        self.assertEqual(budget.requests, 32)


def pinned_upstream_audit(root):
    """Hash-check actual pinned scorer/model/task sources; never import SDKs."""
    root = Path(root)
    pins = json.loads((runner.ROOT / "bench/tau-runtime-lock.json").read_bytes())["source_files"]
    def source(name):
        raw = (root / "upstream" / name).read_bytes()
        require = worker.require
        require(len(raw) == pins[name]["bytes"] and worker.digest(raw) == pins[name]["sha256"])
        return raw
    check = ExternalTrialTests()
    constants = {node.targets[0].id: ast.literal_eval(node.value)
        for node in ast.parse(source("src/tau2/config.py")).body
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in ("DEFAULT_LLM_USER", "DEFAULT_LLM_NL_ASSERTIONS", "DEFAULT_LLM_EVAL_USER_SIMULATOR")}
    check.assertEqual(constants["DEFAULT_LLM_USER"], policy.EXTERNAL_MODEL)
    check.assertEqual(constants["DEFAULT_LLM_NL_ASSERTIONS"], policy.EXTERNAL_MODEL)
    check.assertEqual(constants["DEFAULT_LLM_EVAL_USER_SIMULATOR"], "claude-opus-4-5")
    tasks = json.loads(source("data/tau2/domains/retail/tasks.json"))
    task, = [item for item in tasks if item["id"] == "2"]
    check.assertEqual(task["evaluation_criteria"]["reward_basis"], ["DB", "NL_ASSERTION"])
    check.assertEqual(len(task["evaluation_criteria"]["nl_assertions"]), 1)
    task_tree = ast.parse(source("src/tau2/data_model/tasks.py"))
    reward_type, = [node for node in task_tree.body if isinstance(node, ast.ClassDef) and node.name == "RewardType"]
    tree = ast.parse(source("src/tau2/evaluator/evaluator.py"))
    selected = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))
                and node.name in ("EvaluationType", "evaluate_simulation")]
    check.assertEqual(len(selected), 2)
    scope = {"Enum": Enum, "Optional": Optional, "SimulationRun": object, "Task": object,
        "RewardInfo": lambda **kwargs: types.SimpleNamespace(**kwargs),
        "TerminationReason": types.SimpleNamespace(AGENT_STOP="agent_stop", USER_STOP="user_stop"),
        "CommunicationMode": types.SimpleNamespace(HALF_DUPLEX="half", FULL_DUPLEX="full"),
        "registry": types.SimpleNamespace(get_env_constructor=lambda domain: lambda **kw: types.SimpleNamespace(tools=None, user_tools=None)),
        "ToolType": object}
    exec(compile(ast.Module(body=[reward_type, *selected], type_ignores=[]), "<pinned-tau-evaluation>", "exec"), scope)
    bases = scope["RewardType"]
    # Actual task 2 uses DB/NL_ASSERTION, not EvaluationCriteria's generic
    # DB/COMMUNICATE default. Other diagnostics must not replace that basis.
    for env, comm, action, nl, expected in ((1.0, 0.0, 0.0, 1.0, 1.0), (0.0, 1.0, 1.0, 1.0, 0.0),
                                           (1.0, 1.0, 1.0, 0.0, 0.0), (1.0, 1.0, 1.0, 1.0, 1.0)):
        functions = []
        for name, reward, basis in (("EnvironmentEvaluator", env, bases.DB), ("CommunicateEvaluator", comm, bases.COMMUNICATE),
                                     ("ActionEvaluator", action, bases.ACTION), ("NLAssertionsEvaluator", nl, bases.NL_ASSERTION)):
            row = types.SimpleNamespace(reward=reward, reward_breakdown={basis: reward}, info={}, db_check=None,
                env_assertions=None, action_checks=None, nl_assertions=None, communicate_checks=None)
            calculate = mock.Mock(return_value=row)
            functions.append(calculate)
            scope[name] = types.SimpleNamespace(calculate_reward=calculate)
        simulated = types.SimpleNamespace(termination_reason="user_stop", messages=[])
        original = types.SimpleNamespace(evaluation_criteria=types.SimpleNamespace(
            reward_basis=[bases(value) for value in task["evaluation_criteria"]["reward_basis"]]))
        result = scope["evaluate_simulation"](simulated, original, scope["EvaluationType"].ALL_WITH_NL_ASSERTIONS,
                                             solo_mode=False, domain="retail")
        check.assertEqual(result.reward, expected)
        for calculate in functions:
            calculate.assert_called_once()
    nl_tree = ast.parse(source("src/tau2/evaluator/evaluator_nl_assertions.py"))
    judge, = [node for node in nl_tree.body if isinstance(node, ast.ClassDef) and node.name == "NLAssertionsEvaluator"]
    method, = [node for node in judge.body if isinstance(node, ast.FunctionDef) and node.name == "evaluate_nl_assertions"]
    scope = {"Message": object, "NLAssertionCheck": lambda **kw: types.SimpleNamespace(**kw),
        "json": json, "SystemMessage": lambda **kw: types.SimpleNamespace(**kw),
        "UserMessage": lambda **kw: types.SimpleNamespace(**kw), "DEFAULT_LLM_NL_ASSERTIONS": policy.EXTERNAL_MODEL,
        "DEFAULT_LLM_NL_ASSERTIONS_ARGS": {"temperature": 0},
        "generate": mock.Mock(return_value=types.SimpleNamespace(content=json.dumps({"results": [
            {"expectedOutcome": "synthetic", "metExpectation": False, "reasoning": "synthetic"}]})))}
    exec(compile(ast.Module(body=[method], type_ignores=[]), "<pinned-tau-nl-evaluator>", "exec"), scope)
    result = scope["evaluate_nl_assertions"].__get__(None, object)(
        [types.SimpleNamespace(role="user", content="SYNTHETIC_FULL_HISTORY_CANARY")], ["synthetic"])
    check.assertFalse(result[0].met)
    call = scope["generate"].call_args.kwargs
    check.assertEqual(call["model"], policy.EXTERNAL_MODEL)
    check.assertIn("SYNTHETIC_FULL_HISTORY_CANARY", call["messages"][1].content)
    print("Pinned task/model/NL evaluator and four original reward-basis controls PASS; no inference.")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--upstream-root":
        pinned_upstream_audit(sys.argv[2])
    else:
        unittest.main()
