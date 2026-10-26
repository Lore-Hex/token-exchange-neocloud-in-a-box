# SPDX-License-Identifier: Apache-2.0
"""Offline external endpoints, original parent budget and sealed-scorer boundary."""

import copy
import json
from decimal import Decimal
from contextlib import ExitStack
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import tau_external as external
import tau_trial as runner
import test_tau_trial as fixtures
import test_tau_selected_scorer as scorer
import test_tau_owner_channel as channels
import test_tau_selected_session as selected_fixtures

policy, worker = external.policy, external.worker


def body(role="candidate"):
    return {"model": policy.PUBLIC_MODEL if role == "candidate" else policy.EXTERNAL_MODEL,
            "messages": [{"role": "system", "content": "SYNTHETIC_SYSTEM_CANARY"},
                         {"role": "user", "content": "SYNTHETIC_PROMPT_CANARY"}],
            "stream": False, "n": 1, "temperature": 0, "max_completion_tokens": 256,
            "reasoning_effort": "none", "parallel_tool_calls": False}


def tool():
    return {"type": "function", "function": {"name": "lookup", "description": "Synthetic lookup.",
            "parameters": {"type": "object", "properties": {"key": {"type": "string"}}}}}


def response(role="candidate", *, prompt=20, output=3):
    if role == "candidate":
        return {"model": policy.PUBLIC_MODEL, "usage": {"prompt_tokens": prompt, "completion_tokens": output,
            "total_tokens": prompt + output}, "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": "SYNTHETIC_COMPLETION_CANARY", "tool_calls": None}}]}
    return {"model": policy.EXTERNAL_MODEL, "usage": {"input_tokens": prompt, "output_tokens": output,
            "total_tokens": prompt + output, "output_tokens_details": {"reasoning_tokens": 0}},
            "status": "completed", "service_tier": "default", "store": False, "error": None,
            "incomplete_details": None, "output": [{"type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": "SYNTHETIC_COMPLETION_CANARY", "annotations": []}]}]}


class FakeHTTP:
    def __init__(self, role):
        self.role, self.calls = role, []
        self.result = response(role)
        self.count = 20

    def post(self, path, value, key, deadline, *, before_send=lambda: None):
        before_send()
        self.calls.append((path, copy.deepcopy(value)))
        if path == external.COUNT:
            return {"object": "response.input_tokens", "input_tokens": self.count}
        if isinstance(self.result, BaseException):
            raise self.result
        return copy.deepcopy(self.result)


def endpoint(role):
    http = FakeHTTP(role)
    counter = types.SimpleNamespace(count=mock.Mock(return_value=20))
    return external.Endpoint(role, http, b"SYNTHETIC_KEY_CANARY", counter)


def runtime():
    config = policy.configuration(customer_resolution=True, external_simulator=True)
    manifest = {"schema": policy.EXTERNAL_TRANSPORT, "input": config, "settings": policy.settings(config),
        "binding_sha256": "a" * 64, "framing": {role: {"reasoning_effort": "none", "parallel_tool_calls": False,
          "context_tokens": 8192, "served_model": policy.PUBLIC_MODEL if role == "candidate" else policy.EXTERNAL_MODEL,
          "framing_sha256": "b" * 64, "tokenizer_sha256": external.tau.VOCABULARY_DIGEST if role == "candidate" else "c" * 64}
          for role in ("candidate", "simulator")}}
    return types.SimpleNamespace(manifest=manifest, endpoints={role: endpoint(role) for role in ("candidate", "simulator")},
                                 close=mock.Mock())


class ExternalTests(unittest.TestCase):
    def test_explicit_profile_default_unchanged_and_no_evaluator(self):
        self.assertNotIn("prices", policy.configuration())
        self.assertEqual(policy.configuration(customer_resolution=True)["owner_roles"], {"simulator": "evaluator"})
        config = runtime().manifest["input"]
        self.assertEqual(set(policy.settings(config)["roles"]), {"candidate", "simulator"})
        self.assertNotIn("owner_roles", config)
        with self.assertRaises(ValueError):
            policy.configuration(external_simulator=True)
        for mutation in (lambda v: v["support"]["simulator"].update(model="unreviewed"),
                         lambda v: v["limits"].update(retries=1), lambda v: v.update(quality_eligible=True)):
            altered = copy.deepcopy(config)
            mutation(altered)
            with self.assertRaises(ValueError):
                policy.validate(altered)

    def test_projection_preserves_arguments_and_only_exact_redundancy(self):
        original = body()
        original.update(tools=[tool()], tool_choice="auto")
        original["messages"] += [{"role": "assistant", "content": "earlier", "tool_calls": None},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function",
                "name": "lookup", "function": {"name": "lookup", "arguments": '{ "key" : "value" }'}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": '"original result"'}]
        frozen = copy.deepcopy(original)
        projected = external.candidate_request(original)
        self.assertEqual(original, frozen)
        self.assertEqual(projected["tools"], original["tools"])
        call = projected["messages"][-2]["tool_calls"][0]
        self.assertNotIn("name", call)
        self.assertEqual(call["function"], original["messages"][-2]["tool_calls"][0]["function"])
        self.assertNotIn("tool_calls", projected["messages"][-3])
        original["messages"][-2]["tool_calls"][0]["name"] = "other"
        with self.assertRaises(ValueError):
            external.candidate_request(original)

    def test_request_limits_reject_before_any_generation(self):
        for mutation in (lambda b: b.update(tools=[tool()] * 17, tool_choice="auto"),
                         lambda b: b["messages"].extend([{"role": "user", "content": "x"}] * 128),
                         lambda b: b["messages"][0].update(content="x" * 65537),
                         lambda b: b.update(model="other"), lambda b: b.update(max_completion_tokens=257),
                         lambda b: b.update(tools=[{**tool(), "function": {**tool()["function"], "strict": False}}], tool_choice="auto")):
            with self.subTest(mutation=mutation):
                item, client = body(), endpoint("candidate")
                mutation(item)
                with self.assertRaises(ValueError):
                    client.count(item, 8192, time.monotonic() + 2)
                self.assertEqual(client.http.calls, [])
        client = endpoint("candidate")
        client.counter.count.return_value = 7937
        with self.assertRaises(ValueError):
            client.count(body(), 8192, time.monotonic() + 2)
        self.assertIsNone(client.ready)

    def test_responses_count_and_generation_share_exact_input(self):
        client, original = endpoint("simulator"), body("simulator")
        original["messages"].append({"role": "assistant", "content": "earlier", "tool_calls": None})
        prompt = client.count(original, 8192, time.monotonic() + 2)
        result = client.complete(original, prompt, time.monotonic() + 2)
        self.assertEqual([row[0] for row in client.http.calls], [external.COUNT, external.RESPONSES])
        count, generate = [row[1] for row in client.http.calls]
        self.assertEqual(count, {key: generate[key] for key in count})
        self.assertEqual(generate["input"][-1], {"role": "assistant", "content": "earlier"})
        self.assertFalse(generate["store"] or generate["stream"] or generate["background"])
        self.assertEqual(generate["max_output_tokens"], 256)
        self.assertEqual(result["usage"]["total_tokens"], 23)
        self.assertEqual(client.unknown, 0)
        self.assertNotIn("CANARY", str(client.observation()))
        with self.assertRaises(ValueError):
            client.complete(original, prompt, time.monotonic() + 2)
        self.assertEqual(len(client.http.calls), 2)

    def test_usage_preserved_on_bad_content_count_and_truncation(self):
        for mutate in (lambda r: r["choices"][0]["message"].update(reasoning="private"),
                       lambda r: r["usage"].update(prompt_tokens=21, total_tokens=24)):
            client = endpoint("candidate")
            mutate(client.http.result)
            count = client.count(body(), 8192, time.monotonic() + 2)
            with self.assertRaises(ValueError):
                client.complete(body(), count, time.monotonic() + 2)
            self.assertEqual(client.unknown, 0)
            self.assertEqual(client.observation()["known_usage"]["completion_tokens"], 3)
        client = endpoint("simulator")
        client.http.result.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
        count = client.count(body("simulator"), 8192, time.monotonic() + 2)
        result = client.complete(body("simulator"), count, time.monotonic() + 2)
        self.assertEqual(result["choices"][0]["finish_reason"], "length")

    def test_lost_response_is_unknown_and_never_retried(self):
        client = endpoint("candidate")
        client.http.result = OSError("SYNTHETIC_PRIVATE_CANARY")
        count = client.count(body(), 8192, time.monotonic() + 2)
        with self.assertRaises(OSError):
            client.complete(body(), count, time.monotonic() + 2)
        self.assertEqual((client.attempts, client.unknown), (1, 1))
        self.assertNotIn("CANARY", str(client.observation()))

    def test_terminal_requires_usage_and_original_resolution_ack(self):
        for failure in (None, "reward", "resolution", "ack"):
            with self.subTest(failure=failure):
                held, channel = runtime(), channels.FakeChannel()
                session = external.Session(channel, held, assertions=["synthetic"])
                for category in ("agent", "simulator"):
                    channel.packets.append(channels.fixtures.packet(session.sequence + 1, category))
                    self.assertFalse(session.serve_once())
                resolution = {"schema": policy.RESOLUTION_REPORT, "tool": "email", "outcome": "resolved",
                              "intended_customer_match": True}
                packet = {"schema": external.local.transport.SCHEMA, "kind": "finished", "sequence": 3,
                          "status": "evaluated", "reward": None, "upstream_termination": None,
                          "customer_resolution": resolution, "assertions_sha256": worker.digest(worker.encoded(["synthetic"]))}
                if failure == "reward": packet["reward"] = 1
                if failure == "resolution": resolution.update(outcome="lookup_error", intended_customer_match=None)
                channel.packets.append(packet)
                if failure == "ack": channel.send = mock.Mock(side_effect=OSError("SYNTHETIC_PRIVATE_CANARY"))
                if failure is None:
                    self.assertTrue(session.serve_once())
                    self.assertEqual(session.result["role_requests"], {"agent": 1, "simulator": 1, "evaluator": 0})
                    self.assertEqual(session.result["usage"]["total_tokens"], 46)
                    self.assertEqual(channel.sent[-1]["kind"], "accepted")
                    self.assertNotIn("CANARY", str(session.result))
                else:
                    with self.assertRaises(worker.Abort):
                        session.serve_once()
                    self.assertIsNone(session.result)
                    self.assertEqual(session.bridge.usage()["total_tokens"], 46)
                session.close()

    def test_candidate_tool_cap_and_native_length_retain_actual_usage(self):
        for tokens, finish, status in ((256, "tool_calls", "protocol_error"), (255, "tool_calls", None),
                                        (256, "length", "truncated")):
            held, channel = runtime(), channels.FakeChannel([channels.fixtures.packet()])
            reply = selected_fixtures.capped_tool_response(tokens, finish)
            reply["usage"].update(prompt_tokens=20, total_tokens=20 + tokens)
            held.endpoints["candidate"].http.result = {"model": policy.PUBLIC_MODEL, **reply}
            session = external.Session(channel, held, assertions=["synthetic"])
            if status:
                with self.assertRaises(worker.Abort) as caught:
                    session.serve_once()
                self.assertEqual(caught.exception.status, status)
            else:
                self.assertFalse(session.serve_once())
            self.assertEqual(session.bridge.usage()["completion_tokens"], tokens)
            self.assertEqual(held.endpoints["candidate"].observation()["known_usage"]["completion_tokens"], tokens)
            session.close()

    def test_real_budget_caps_cost_and_keeps_unknown_reservation(self):
        budget = policy.ResourceBudget(runtime().manifest["input"])
        budget.begin_trial()
        budget.reserve("simulator")
        self.assertEqual(budget.spent, Decimal("0.018432"))
        budget.abandon()
        self.assertTrue(budget.failed)
        with self.assertRaises(ValueError):
            budget.reserve("candidate")
        budget = policy.ResourceBudget(runtime().manifest["input"])
        budget.begin_trial()
        for _ in range(32):
            budget.reserve("simulator")
            budget.settle({"prompt_tokens": 20, "completion_tokens": 3, "total_tokens": 23})
        with self.assertRaises(ValueError):
            budget.reserve("simulator")

    def test_manifest_tampering_and_legacy_owner_ambiguity_rejected(self):
        manifest = runtime().manifest
        policy.transport_manifest(manifest, worker.digest(worker.encoded(manifest)))
        for mutate in (lambda m: m.update(owners={}), lambda m: m["framing"]["candidate"].update(context_tokens=16384),
                       lambda m: m["framing"]["simulator"].update(served_model="alias"),
                       lambda m: m["settings"]["limits"].update(cost_usd="1000")):
            item = copy.deepcopy(manifest)
            mutate(item)
            with self.assertRaises(ValueError):
                policy.transport_manifest(item, worker.digest(worker.encoded(item)))
        with mock.patch.object(runner, "identity") as identify, self.assertRaises(ValueError):
            runner.run(None, None, None, None, parent_pipe=True, selected_owners={}, external_inputs={})
        identify.assert_not_called()

    def test_real_attach_child_reaped_on_early_protocol_failure(self):
        held = runtime()
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(external.pipe, "selected_assertions", return_value=["synthetic"]):
                result = external.run_process([sys.executable, "-I", "-S", "-c", "import time;print('bad',flush=True);time.sleep(30)"],
                    {"PATH": "/usr/bin:/bin"}, Path(temporary), {"selection": {}}, held, time.monotonic() + 65)
        self.assertEqual(result["attach_cleanup"], "reaped")
        self.assertFalse(external.accepted(result))
        self.assertEqual(result["endpoints"]["candidate"]["generation_attempts"], 0)

    def test_runtime_rejects_contract_before_key_reads_or_tokenizer(self):
        with mock.patch.object(external.private, "private_read", return_value=b"{}") as read, \
                mock.patch.object(external.tau_external_tokenizer, "Counter") as counter, self.assertRaises(ValueError):
            external.Runtime(Path("/private/contract"), worker.digest(b"{}"), None, None, None, None, None, time.monotonic() + 60)
        read.assert_called_once()
        counter.assert_not_called()

    def test_verified_parent_closure_loads_not_ambient_modules(self):
        names = ("tau_preflight", "tau_trial_worker", "tau_aggregate", "tau_transport", *runner.SELECTED_PARENT_MODULES,
                 "tau_pipe_driver", "tau_selected_driver", *runner.EXTERNAL_PARENT_MODULES)
        toolkit = {"files": {"scripts/" + name + ".py": worker.digest((runner.ROOT / "scripts" / (name + ".py")).read_bytes())
                             for name in names}}
        driver = runner.parent_driver(toolkit, selected_operational=True, external_simulator=True)
        self.assertIsNot(driver, external)
        self.assertIsNot(driver.worker, worker)
        self.assertEqual(driver.policy.EXTERNAL_MODEL, policy.EXTERNAL_MODEL)
        toolkit["files"]["scripts/bounded_https.py"] = "0" * 64
        with self.assertRaises(ValueError):
            runner.parent_driver(toolkit, selected_operational=True, external_simulator=True)

    def test_external_lifecycle_reuses_strict_container_and_failure_cleanup(self):
        for failure in (None, "image", "runtime", "create", "isolation", "driver", "terminal", "cleanup", "close"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                held, plan = runtime(), scorer.fixture()[3]
                for client in held.endpoints.values():
                    client.attempts = 1
                observed = {"scorer_protocol_completed": True, "attach_cleanup": "reaped", "scorer_exit_code": 0,
                    "trial": {"status": "evaluated", "reward": None, "usage": {}, "customer_resolution": {"outcome": "resolved"}},
                    "endpoints": {name: client.observation() for name, client in held.endpoints.items()}}
                module = types.SimpleNamespace(policy=policy, Runtime=mock.Mock(return_value=held),
                    run_process=mock.Mock(return_value=observed), accepted=external.accepted)
                if failure == "runtime": module.Runtime.side_effect = ValueError("SYNTHETIC_PRIVATE_CANARY")
                if failure == "driver":
                    held.endpoints["candidate"].measured.append({"prompt_tokens": 20, "completion_tokens": 3, "total_tokens": 23})
                    module.run_process.side_effect = KeyboardInterrupt
                if failure == "close": held.close.side_effect = OSError("SYNTHETIC_PRIVATE_CANARY")
                state, calls, published = {"exists": False, "inspections": 0}, [], {}
                def capture(argv, **kwargs):
                    args = argv[5:]
                    calls.append(args)
                    if args[:2] == ["image", "inspect"]:
                        image = fixtures.image_inspection()
                        if failure == "image": image["Architecture"] = "arm64"
                        return worker.encoded([image])
                    if args[0] == "create":
                        self.assertEqual(args[args.index("--network") + 1], "none")
                        self.assertNotIn("--gpus", args)
                        state.update(exists=True, info=fixtures.LifecycleTests().info(args))
                        state["info"]["Config"]["OpenStdin"] = True
                        if failure == "create": raise OSError("SYNTHETIC_PRIVATE_CANARY")
                        return b"d" * 64
                    if args[0] == "inspect":
                        state["inspections"] += 1
                        item = copy.deepcopy(state["info"])
                        if failure == "isolation": item["HostConfig"]["NetworkMode"] = "host"
                        if failure == "terminal" and state["inspections"] == 2: item["State"]["ExitCode"] = 1
                        return worker.encoded([item])
                    if args[0] == "ps": return b"d" * 64 if state["exists"] else b""
                    if args[0] == "rm":
                        if failure == "cleanup": raise OSError("SYNTHETIC_PRIVATE_CANARY")
                        state["exists"] = False
                        return b""
                    self.fail("unexpected Docker command")
                for target, name, kwargs in (
                    (runner, "identity", {"return_value": {"git_sha": "a" * 40}}),
                    (runner, "validate_plan", {"return_value": (plan, held.manifest["input"])}),
                    (runner, "parent_driver", {"return_value": module}),
                    (runner.shutil, "which", {"return_value": "/fixed/docker"}),
                    (runner.probe, "capture", {"side_effect": capture})):
                    stack.enter_context(mock.patch.object(target, name, **kwargs))
                sock = mock.Mock()
                sock.is_absolute.return_value = True
                sock.stat.return_value = types.SimpleNamespace(st_mode=stat.S_IFSOCK)
                deadline = time.monotonic() + 90
                report = runner.run(Path("/private/payload"), "a" * 64, Path(temporary) / "output", sock,
                    parent_pipe=True, external_inputs={"arguments": (None,) * 7, "deadline": deadline})
                published = {p.name: p.read_bytes() for p in (Path(temporary) / "output").iterdir()}
                self.assertEqual(set(published), {"run.json", "output.log"})
                self.assertEqual(json.loads(published["output.log"]), report)
                self.assertEqual(report["result"], "pass" if failure is None else "fail")
                self.assertNotIn(b"CANARY", b"".join(published.values()))
                if failure == "driver":
                    self.assertEqual(report["endpoint_usage"]["candidate"]["known_usage"]["total_tokens"], 23)
                if failure in ("image", "runtime"):
                    self.assertFalse(any(args[0] == "create" for args in calls))
                if failure not in ("image", "runtime"):
                    held.close.assert_called_once()
                if failure in (None, "terminal", "cleanup"):
                    self.assertEqual(report["diagnostic"], observed)
                    self.assertEqual(module.run_process.call_args.args[-1], deadline)
                if failure in ("create", "isolation"):
                    self.assertEqual(report["cleanup"], "create-completion-unknown")

    def test_close_failure_does_not_skip_reaping(self):
        with mock.patch.object(external.pipe, "selected_assertions", return_value=["synthetic"]), \
                mock.patch.object(external.Session, "close", side_effect=OSError("SYNTHETIC_PRIVATE_CANARY")):
            result = external.run_process([sys.executable, "-I", "-S", "-c", "print('invalid',flush=True)"],
                {}, Path("/unused"), {"selection": {}}, runtime(), time.monotonic() + 65)
        self.assertEqual(result["attach_cleanup"], "reaped")
        self.assertFalse(external.accepted(result))

    def test_real_pipe_success_and_post_terminal_failure_preserve_result(self):
        program = '''import sys,os,time
sys.path.insert(0,sys.argv[1])
import tau_transport as t
import tau_selected_policy as p
c=t.Channel(os.dup(0),os.dup(1))
w=t.WorkerProxy(c,time.monotonic()+10,selected_operational=True,customer_resolution=True)
w.assertions=["synthetic"]
for role in ("simulator","agent"):
 w.complete(model=t.ALIASES[role],messages=[{"role":"user","content":"SYNTHETIC_PROMPT_CANARY"}])
w.customer_resolution={"schema":p.RESOLUTION_REPORT,"tool":"email","outcome":"resolved","intended_customer_match":True}
w.finish("evaluated",None)
c.close()
if sys.argv[2]=="trailing": os.write(1,b"SYNTHETIC_TRAILING_CANARY")
sys.exit(1 if sys.argv[2]=="exit" else 0)
'''
        for failure in ("none", "trailing", "exit"):
            with self.subTest(failure=failure), mock.patch.object(external.pipe, "selected_assertions", return_value=["synthetic"]):
                result = external.run_process([sys.executable, "-I", "-S", "-B", "-c", program,
                    str(runner.ROOT / "scripts"), failure], {}, Path("/unused"), {"selection": {}}, runtime(),
                    time.monotonic() + 65)
            self.assertEqual(external.accepted(result), failure == "none")
            self.assertEqual(result["attach_cleanup"], "reaped")
            self.assertEqual(result["trial"]["usage"]["total_tokens"], 46)
            self.assertEqual(result["trial"]["customer_resolution"]["outcome"], "resolved")
            self.assertIsNone(result["trial"]["reward"])
            self.assertNotIn("CANARY", str(result))

    def test_cli_closed_arguments_and_failure_output(self):
        script = runner.ROOT / "scripts/record-tau-external.py"
        commands = (["--help"], ["run", "--unreviewed", "SYNTHETIC_PRIVATE_CANARY"])
        for arguments in commands:
            result = subprocess.run([sys.executable, "-I", "-S", "-B", str(script), *arguments],
                                    capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0 if arguments == ["--help"] else 2)
            self.assertNotIn(b"CANARY", result.stdout + result.stderr)
            self.assertNotIn(b"Traceback", result.stderr)

    def test_actual_signal_interrupts_blocked_pipe_and_reaps_only_attach(self):
        program = '''import sys,signal,threading,os,time,json
from pathlib import Path
from unittest import mock
sys.path.insert(0,sys.argv[1])
import tau_external as e
from test_tau_external import runtime
def cancel(s,f): raise KeyboardInterrupt
signal.signal(signal.SIGTERM,cancel)
timer=threading.Timer(0.1,lambda:os.kill(os.getpid(),signal.SIGTERM));timer.start()
with mock.patch.object(e.pipe,"selected_assertions",return_value=["synthetic"]):
 r=e.run_process([sys.executable,"-I","-S","-c","import time;time.sleep(30)"],{},Path("/unused"),{"selection":{}},runtime(),time.monotonic()+65)
timer.join()
assert r["attach_cleanup"]=="reaped" and not e.accepted(r),r
assert r["trial"]["status"]=="cancelled",r
assert signal.getsignal(signal.SIGTERM) is cancel
print("signal-and-reap PASS")
'''
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", program, str(runner.ROOT / "scripts")],
                                capture_output=True, timeout=10, check=True)
        self.assertEqual(result.stdout, b"signal-and-reap PASS\n")
        self.assertEqual(result.stderr, b"")

    def test_actual_signal_during_restore_waits_until_both_handlers_restored(self):
        program = '''import sys,signal,os,time
from pathlib import Path
from unittest import mock
sys.path.insert(0,sys.argv[1])
import tau_external as e
from test_tau_external import runtime
def cancel(s,f): raise KeyboardInterrupt
signal.signal(signal.SIGINT,cancel);signal.signal(signal.SIGTERM,cancel)
original=signal.signal
mask=signal.pthread_sigmask(signal.SIG_BLOCK,set())
injected=False
def restore(number,handler):
 global injected
 old=original(number,handler)
 if number==signal.SIGINT and handler is cancel and not injected:
  injected=True
  assert signal.SIGTERM in signal.pthread_sigmask(signal.SIG_BLOCK,set())
  os.kill(os.getpid(),signal.SIGTERM)
 return old
with mock.patch.object(e.pipe,"selected_assertions",return_value=["synthetic"]),mock.patch.object(signal,"signal",side_effect=restore):
 r=e.run_process([sys.executable,"-I","-S","-c","print('invalid',flush=True)"],{},Path("/unused"),{"selection":{}},runtime(),time.monotonic()+65)
assert injected and r["attach_cleanup"]=="reaped" and not e.accepted(r)
assert all(signal.getsignal(s) is cancel for s in (signal.SIGINT,signal.SIGTERM))
assert signal.pthread_sigmask(signal.SIG_BLOCK,set())==mask
print("restore PASS")
'''
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", program, str(runner.ROOT / "scripts")],
                                capture_output=True, timeout=10, check=True)
        self.assertEqual(result.stdout, b"restore PASS\n")
        self.assertEqual(result.stderr, b"")


if __name__ == "__main__":
    unittest.main()
