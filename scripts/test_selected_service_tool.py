# SPDX-License-Identifier: Apache-2.0
"""Fixed tool round trips on real HTTP/SSE decoders with owned mock sockets."""

from contextlib import contextmanager, ExitStack, redirect_stdout
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

import selected_service_tool as tool
import test_selected_service_hook as hook
import test_selected_service_inference as fixtures

inference, process, service = tool.inference, tool.process, tool.service
encode = tool.encoded


def rows(case="call", *, output=12, finish=None, args='{"key":"sample"}'):
    deltas = [{"role": "assistant", "content": ""}, {"reasoning": "PRIVATE_REASONING_CANARY"}]
    if case == "call":
        deltas += [{"tool_calls": [{"index": 0, "id": "call_synthetic", "type": "function",
            "function": {"name": "lookup_", "arguments": ""}}]},
            {"tool_calls": [{"index": 0, "function": {"name": "synthetic_value", "arguments": args[:7]}}]},
            {"tool_calls": [{"index": 0, "function": {"arguments": args[7:]}}]}]
    else:
        deltas += [{"content": tool.ANSWER[:4]}, {"content": tool.ANSWER[4:]}]
    base = {"model": service.MODEL, "object": "chat.completion.chunk"}
    result = [{**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]} for delta in deltas]
    result += [{**base, "choices": [{"index": 0, "delta": {},
        "finish_reason": finish or ("tool_calls" if case == "call" else "stop")}]},
        {**base, "choices": [], "usage": {"prompt_tokens": 42, "completion_tokens": output,
                                         "total_tokens": 42 + output}}]
    return result


def chunks(values, *, done=True):
    return [b"data: " + encode(row) + b"\n\n" for row in values] + ([b"data: [DONE]\n\n"] if done else [])


def receipt(sha=hook.SHA):
    return {"schema": tool.SCHEMA, "result": "pass", "failure": "none",
        "contract_sha256": process.digest(encode(tool.contract())), "prepared_sha256": sha,
        "inference_sha256": process.digest(encode(hook.receipt(sha))),
        "attempts": dict.fromkeys(tool.CASES, True), "checks": dict.fromkeys(tool.CASES, True),
        "usage": {case: {"prompt_tokens": 42, "completion_tokens": 12} for case in tool.CASES},
        "usage_unknown": dict.fromkeys(tool.CASES, False),
        "finish": {"call": "tool_calls", "continuation": "stop"},
        "resources_closed": dict.fromkeys(tool.CASES, True), **dict.fromkeys(inference.CLAIMS, False)}


def owner_receipt(sha=hook.SHA):
    return {**hook.owner_receipt(sha), "schema": service.TOOL_SCHEMA, "mode": service.TOOL_MODE,
            "tool_roundtrip": receipt(sha)}


class ToolTests(unittest.TestCase):
    @contextmanager
    def fixture(self, *, first=None, second=None, tail=b"", status=200):
        with fixtures.InferenceTests().fixture() as state, ExitStack() as stack:
            state.supervisor._selected_inference_used = True
            state.owner.prepared.receipt_sha256 = state.sha
            source = Path(tool.__file__).read_bytes()
            state.owner.prepared.files["scripts/selected_service_tool.py"] = process.digest(source)
            original = state.read.side_effect
            state.read.side_effect = lambda name: source if name == tool.__file__ else original(name)
            for module in (tool, tool.wire):
                stack.enter_context(mock.patch.object(module, "time", state.clock))
            state.sockets, state.connections = [], []
            for index, parts in enumerate((first if first is not None else chunks(rows()),
                                            second if second is not None else chunks(rows("continuation")))):
                connection = fixtures.resource()
                data = b"".join(format(len(part), "x").encode() + b"\r\n" + part + b"\r\n" for part in parts)
                header = (f"HTTP/1.1 {status} Synthetic\r\nContent-Type: text/event-stream\r\n"
                          "Transfer-Encoding: chunked\r\n\r\n").encode()
                connection.makefile.return_value = io.BytesIO(header + data + b"0\r\n\r\n" + tail)
                state.sockets.append(fixtures.resource())
                state.connections.append(connection)
            state.connect.side_effect = state.sockets
            state.owner.trust.wrap_socket.side_effect = state.connections
            state.prior = hook.receipt(state.sha)
            yield state

    def run_helper(self, state):
        row = tool.run_once(state.supervisor, state.prior)
        self.assertEqual(tool.validate_report(encode(row), state.sha, state.prior), row)
        for private in (fixtures.CANARY.encode(), fixtures.KEY, b"PRIVATE_REASONING_CANARY", b"call_synthetic",
                        tool.NAME.encode(), b'"key":"sample"', tool.ANSWER.encode(), b"service.invalid"):
            self.assertNotIn(private, encode(row))
        return row

    def test_exact_owned_roundtrip_fragmented_wire_same_identity_fixed_result_and_no_claims(self):
        wire = b"".join(chunks(rows()))
        parts = [wire[i:i + 7] for i in range(0, len(wire), 7)]
        with self.fixture(first=parts) as state:
            row = self.run_helper(state)
            self.assertEqual(row, receipt(state.sha))
            self.assertEqual(state.connect.call_count, 2)
            self.assertEqual(state.owner.peer.call_count, 2)
            requests = []
            for index, connection in enumerate(state.connections):
                header, body = connection.sendall.call_args.args[0].split(b"\r\n\r\n", 1)
                self.assertIn(b"Authorization: Bearer " + fixtures.KEY, header)
                self.assertTrue(header.startswith(b"POST /v1/chat/completions HTTP/1.1\r\n"))
                self.assertEqual(state.connect.call_args_list[index].args, (("127.0.0.1", 8443),))
                connection.__exit__.assert_called_once()
                state.sockets[index].__exit__.assert_called_once()
                requests.append(json.loads(body))
            self.assertEqual(requests[0], json.loads(tool.request_bytes()))
            assistant = requests[1]["messages"][1]
            self.assertEqual(assistant, {"role": "assistant", "content": None,
                "reasoning": "PRIVATE_REASONING_CANARY", "tool_calls": [{"id": "call_synthetic",
                "type": "function", "function": {"name": tool.NAME, "arguments": '{"key":"sample"}'}}]})
            self.assertEqual(requests[1], json.loads(tool.request_bytes(assistant)))
            self.assertEqual(requests[1]["messages"][2], {"role": "tool", "tool_call_id": "call_synthetic",
                                                       "content": tool.RESULT})
            self.assertEqual(requests[1]["tool_choice"], "none")
            self.assertTrue(all(r["reasoning_effort"] == "none" and r["max_tokens"] == 256 for r in requests))

    def test_every_byte_boundary_crlf_and_utf8_is_handled_by_shared_framer(self):
        values = rows()
        values[1]["choices"][0]["delta"] = {"reasoning": "\u03bb"}
        raw = b"".join(chunks(values)).replace(b"\\u03bb", "\u03bb".encode()).replace(b"\n", b"\r\n")
        for split in range(len(raw) + 1):
            meter = tool.ToolStream()
            meter.feed(raw[:split], .1)
            meter.feed(raw[split:], .2)
            self.assertEqual(meter.close(.3).status, "completed")
            self.assertEqual(meter.qualify("call")["reasoning"], "\u03bb")

    def test_malformed_or_wrong_arguments_never_repaired_and_original_usage_retained(self):
        for args in ('{"key":', '{"key":"wrong"}', '{"key":"sample","key":"sample"}',
                     '"{\\"key\\":\\"sample\\"}"', '[]', 'null', '{"key":NaN}', '{"key":1e999}'):
            with self.subTest(args=args), self.fixture(first=chunks(rows(args=args))) as state:
                row = self.run_helper(state)
                self.assertEqual(row["result"], "fail")
                self.assertEqual(row["usage"]["call"], {"prompt_tokens": 42, "completion_tokens": 12})
                self.assertEqual(row["finish"]["call"], "tool_calls")
                self.assertTrue(row["resources_closed"]["call"])
                self.assertFalse(row["attempts"]["continuation"])
                self.assertEqual(state.connect.call_count, 1)

    def test_cap_saturation_and_native_length_fail_both_legs_without_relabelling(self):
        for case in tool.CASES:
            for finish, output in (("length", 255), ("tool_calls" if case == "call" else "stop", 256)):
                parts = chunks(rows(case, finish=finish, output=output))
                with self.subTest(case=case, finish=finish), self.fixture(
                        **{"first" if case == "call" else "second": parts}) as state:
                    row = self.run_helper(state)
                    self.assertEqual(row["result"], "fail")
                    self.assertEqual(row["finish"][case], finish)
                    self.assertEqual(row["usage"][case]["completion_tokens"], output)
                    self.assertFalse(row["usage_unknown"][case])
                    if case == "call":
                        self.assertFalse(row["attempts"]["continuation"])

    def test_reject_bad_indices_ids_names_parallel_calls_and_reasoning_alias_mix(self):
        def delta(values, index=2):
            return values[index]["choices"][0]["delta"]
        changes = [lambda v: delta(v)["tool_calls"][0].update(index=1),
            lambda v: delta(v)["tool_calls"][0].update(index=True),
            lambda v: delta(v)["tool_calls"][0].update(id="bad id"),
            lambda v: delta(v)["tool_calls"][0].update(id="x" * 129),
            lambda v: delta(v, 3)["tool_calls"][0].update(id="call_synthetic"),
            lambda v: delta(v)["tool_calls"].append(copy.deepcopy(delta(v)["tool_calls"][0])),
            lambda v: delta(v)["tool_calls"][0]["function"].update(name="wrong"),
            lambda v: delta(v)["tool_calls"][0]["function"].update(name="x" * 65),
            lambda v: delta(v)["tool_calls"][0]["function"].update(arguments="x" * 32769),
            lambda v: delta(v, 3).update(reasoning_content="PRIVATE_CANARY"),
            lambda v: delta(v).update(extra="PRIVATE_CANARY")]
        for change in changes:
            values = rows()
            change(values)
            with self.subTest(change=changes.index(change)), self.fixture(first=chunks(values)) as state:
                row = self.run_helper(state)
                self.assertEqual(row["result"], "fail")
                self.assertFalse(row["attempts"]["continuation"])

    def test_missing_finish_usage_done_bad_totals_and_eof_cannot_pass(self):
        good = chunks(rows())
        mutations = [good[:-1], good[:-2] + good[-1:], good[:-3] + good[-2:],
            good + [b"data: PRIVATE_CANARY\n\n"], good + [b"data: "],
            good[:-1] + good[-2:], [part.replace(b'"total_tokens":54', b'"total_tokens":55') for part in good]]
        for parts in mutations:
            with self.subTest(parts=len(parts)), self.fixture(first=parts) as state:
                row = self.run_helper(state)
                self.assertEqual(row["result"], "fail")
                self.assertFalse(row["attempts"]["continuation"])
        for kwargs in ({"tail": b"PRIVATE_CANARY"}, {"status": 401}):
            with self.subTest(kwargs=kwargs), self.fixture(**kwargs) as state:
                row = self.run_helper(state)
                self.assertEqual(row["result"], "fail")
                self.assertEqual(row["usage_unknown"]["call"], "status" in kwargs)

    def test_tool_local_usage_validation_keeps_strict_legacy_integer_bounds(self):
        good = rows()[-1]["usage"]
        changes = [{key: value} for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                   for value in (True, -1, 1.5, "12", 100000)]
        changes += [{"completion_tokens": 0, "total_tokens": 42},
                    {"prompt_tokens_details": []}, {"completion_tokens_details": []},
                    {"prompt_tokens_details": {"cached_tokens": True}},
                    {"prompt_tokens_details": {"cached_tokens": 43}},
                    {"completion_tokens_details": {"reasoning_tokens": True}},
                    {"completion_tokens_details": {"reasoning_tokens": 13}}]
        for change in changes:
            values = rows()
            values[-1]["usage"] = {**good, **change}
            with self.subTest(change=change), self.fixture(first=chunks(values)) as state:
                row = self.run_helper(state)
                self.assertEqual(row["result"], "fail")
                self.assertIsNone(row["usage"]["call"])
                self.assertTrue(row["usage_unknown"]["call"])
                self.assertFalse(row["attempts"]["continuation"])
        values = rows()
        values[-1]["usage"].update(prompt_tokens_details={"cached_tokens": 42},
                                  completion_tokens_details={"reasoning_tokens": 12})
        with self.fixture(first=chunks(values)) as state:
            self.assertEqual(self.run_helper(state), receipt(state.sha))

    def test_continuation_rejects_another_tool_wrong_answer_or_reasoning_only(self):
        for kind in ("tool", "wrong", "reasoning"):
            values = rows() if kind == "tool" else rows("continuation")
            if kind == "wrong":
                values[2]["choices"][0]["delta"]["content"] = "WRONG"
            elif kind == "reasoning":
                for item in values:
                    for choice in item["choices"]:
                        delta = choice["delta"]
                        if "content" in delta:
                            delta["reasoning"] = delta.pop("content")
            with self.subTest(kind=kind), self.fixture(second=chunks(values)) as state:
                row = self.run_helper(state)
                self.assertEqual(row["result"], "fail")
                self.assertTrue(row["checks"]["call"])
                self.assertFalse(any(row["usage_unknown"].values()))

    def test_cancellation_deadline_close_failure_and_identity_change_preserve_usage(self):
        for failure in ("cancel", "deadline", "close", "identity", "interrupt", "exit"):
            with self.subTest(failure=failure), self.fixture() as state:
                original = tool.ToolStream.qualify
                def qualify(meter, case):
                    result = original(meter, case)
                    if case == "continuation":
                        if failure == "cancel":
                            state.handlers[inference.signal.SIGTERM](0, None)
                        elif failure == "deadline":
                            state.clock.advance(tool.SECONDS + 1)
                        elif failure == "identity":
                            state.owner.identities["tls"]["pid"] += 1
                        elif failure == "interrupt":
                            raise KeyboardInterrupt(fixtures.CANARY)
                        elif failure == "exit":
                            raise SystemExit(fixtures.CANARY)
                    return result
                if failure == "close":
                    state.connections[-1].__exit__.side_effect = OSError(fixtures.CANARY)
                with mock.patch.object(tool.ToolStream, "qualify", qualify):
                    row = self.run_helper(state)
                self.assertEqual(row["result"], "fail")
                self.assertFalse(any(row["usage_unknown"].values()))
                self.assertEqual(row["resources_closed"]["continuation"], failure != "close")
                for connection in state.connections:
                    connection.__exit__.assert_called_once()

    def test_peer_and_source_failure_send_no_credentials_and_double_use_cannot_retry(self):
        for failure in ("peer", "source", "double", "prior"):
            with self.subTest(failure=failure), self.fixture() as state:
                if failure == "peer":
                    state.owner.peer.side_effect = OSError(fixtures.CANARY)
                elif failure == "source":
                    state.owner.prepared.files["scripts/selected_service_tool.py"] = "0" * 64
                elif failure == "double":
                    self.assertEqual(self.run_helper(state)["result"], "pass")
                else:
                    state.supervisor._selected_inference_used = False
                row = self.run_helper(state)
                self.assertEqual(row["result"], "fail")
                self.assertFalse(any(row["attempts"].values()))
                if failure != "double":
                    for connection in state.connections:
                        connection.sendall.assert_not_called()
                else:
                    self.assertEqual(state.connect.call_count, 2)

    def test_midstream_deadline_cancellation_and_transport_failure_stop_without_retry(self):
        for failure in ("deadline", "cancel", "transport", "size"):
            with self.subTest(failure=failure), self.fixture() as state:
                original = tool.transport.StreamResponse.read1
                reads = 0
                def read(response, amount):
                    nonlocal reads
                    reads += 1
                    if reads == 2:
                        if failure == "deadline":
                            state.clock.advance(inference.REQUEST_SECONDS + 1)
                        elif failure == "cancel":
                            state.handlers[inference.signal.SIGINT](0, None)
                        elif failure == "transport":
                            raise OSError(fixtures.CANARY)
                        else:
                            return b"x" * (tool.LIMIT + 1)
                    return original(response, amount)
                with mock.patch.object(tool.transport.StreamResponse, "read1", read):
                    row = self.run_helper(state)
                self.assertEqual(row["result"], "fail")
                self.assertTrue(row["usage_unknown"]["call"])
                self.assertFalse(row["attempts"]["continuation"])
                self.assertEqual(state.connect.call_count, 1)
                state.connections[0].__exit__.assert_called_once()

    def test_actual_supervisor_calls_real_helper_then_retires_all_owned_roles(self):
        for fail in (False, True):
            with self.subTest(fail=fail), self.fixture(
                    second=chunks(rows("continuation", output=256 if fail else 12))) as state, ExitStack() as stack:
                supervisor, owner = state.supervisor, state.owner
                supervisor.used, supervisor.children = False, {}
                owner.start = mock.Mock(side_effect=lambda role, contract, inputs, register, deadline:
                    register(owner.processes[role]))
                owner.probe = mock.Mock(side_effect=lambda role, child, deadline:
                    service.EngineReady(hook.lifecycle_tests.endpoint(child.pid), encode({"data": [
                        {"id": service.MODEL, "max_model_len": service.CONTEXT}]}),
                        service.CONTEXT, service.MEMORY_BYTES, 0) if role == "engine" else True)
                events = []
                def retire(role, child, deadline):
                    events.append(role)
                    self.assertLess(state.clock.mono, deadline)
                    for connection in state.connections:
                        connection.__exit__.assert_called_once()
                    child.returncode = 0
                    return True
                owner.drain = retire
                owner.reap = lambda role, child, deadline: retire(role, child, deadline)
                owner.reap_unregistered = lambda deadline: True
                stack.enter_context(mock.patch.object(service.identity.Endpoint, "check"))
                stack.enter_context(mock.patch.object(inference, "run_once", return_value=state.prior))
                row = supervisor.run(tool_smoke=True)
                self.assertEqual(row["status"], "failed" if fail else "retired")
                self.assertEqual(events, ["tls", "tls", "guard", "guard", "engine"])
                self.assertTrue(row["cleanup_confirmed"])
                join = hook.smoke.lifecycle_failure if fail else hook.smoke.lifecycle
                self.assertEqual(join(encode(row), tool_smoke=True, prepared_sha256=state.sha), row)


class JoinTests(unittest.TestCase):
    setup_service = hook.HookTests.setup_service
    owned = hook.HookTests.owned

    def test_owner_runs_once_after_inference_with_original_deadline_front_first_cleanup(self):
        with self.owned() as (supervisor, owner, clock, _, first):
            owner.startup_seconds = 821
            original = supervisor.contract
            with mock.patch.object(tool, "run_once", return_value=receipt()) as run:
                row = supervisor.run(tool_smoke=True)
            self.assertEqual(row, owner_receipt())
            self.assertIs(supervisor.contract, original)
            self.assertEqual(clock.mono, 100 + 821 + 2)
            run.assert_called_once_with(supervisor, hook.receipt())
            first.assert_called_once_with(supervisor)
            self.assertEqual(owner.events[-6:], [("drain", "tls"), ("reap", "tls"),
                ("drain", "guard"), ("reap", "guard"), ("reap", "engine"), ("reap-unregistered", None)])

    def test_failed_inference_prevents_tool_and_modes_are_mutually_exclusive(self):
        with self.owned() as (supervisor, _, _, _, first), mock.patch.object(tool, "run_once") as run:
            first.return_value.update(result="fail", failure="completion")
            self.assertIsNone(supervisor.run(tool_smoke=True)["tool_roundtrip"])
            run.assert_not_called()
        for other in ("inference_smoke", "conformance_smoke", "protocol_smoke"):
            with self.owned() as (supervisor, *_), self.assertRaises(ValueError):
                supervisor.run(tool_smoke=True, **{other: True})

    def test_late_failure_and_cleanup_never_erase_tool_accounting_or_promote_pass(self):
        for fail in ("helper", "deadline", "cancel", "drain", "reap", "resource"):
            with self.subTest(fail=fail), self.owned() as (supervisor, owner, clock, shutdown, _):
                observed = receipt()
                def run(*_):
                    if fail in ("helper", "resource"):
                        observed.update(result="fail", failure="completion")
                    if fail == "resource":
                        observed["resources_closed"]["continuation"] = False
                        observed["checks"]["continuation"] = False
                    elif fail == "deadline":
                        clock.advance(supervisor.cutoff() - service.CLEANUP_RESERVE - clock.mono)
                    elif fail == "cancel":
                        shutdown.stopped = True
                    elif fail in ("drain", "reap"):
                        setattr(owner, fail, mock.Mock(return_value=False))
                    return observed
                with mock.patch.object(tool, "run_once", side_effect=run):
                    row = supervisor.run(tool_smoke=True)
                self.assertEqual(row["status"], "failed")
                self.assertEqual(row["tool_roundtrip"]["usage"], receipt()["usage"])
                self.assertEqual(row["cleanup_confirmed"], fail not in ("reap", "resource"))
                self.assertEqual(hook.smoke.lifecycle_failure(encode(row), tool_smoke=True,
                                                             prepared_sha256=hook.SHA), row)

    def test_unreturned_or_invalid_helper_cannot_claim_socket_cleanup(self):
        for value in (SystemExit(fixtures.CANARY), {**receipt(), "extra": fixtures.CANARY}):
            with self.owned() as (supervisor, owner, _, _, _):
                with mock.patch.object(tool, "run_once", **(
                        {"side_effect": value} if isinstance(value, BaseException) else {"return_value": value})):
                    row = supervisor.run(tool_smoke=True)
                self.assertEqual(row["status"], "failed")
                self.assertFalse(row["cleanup_confirmed"])
                self.assertIsNone(row["tool_roundtrip"])
                self.assertEqual(owner.events[-1], ("reap-unregistered", None))
                self.assertNotIn("CANARY", encode(row).decode())

    def test_closed_report_and_host_join_reject_invention_or_claims(self):
        good = receipt()
        changes = [(key, True) for key in inference.CLAIMS]
        changes += [("extra", fixtures.CANARY), ("prepared_sha256", "0" * 64), ("contract_sha256", "0" * 64),
            ("inference_sha256", "0" * 64), ("finish", dict.fromkeys(tool.CASES, "length")),
            ("usage_unknown", dict.fromkeys(tool.CASES, True)), ("usage", dict.fromkeys(tool.CASES)),
            ("resources_closed", dict.fromkeys(tool.CASES, False))]
        for key, value in changes:
            with self.subTest(key=key), self.assertRaises(ValueError):
                tool.validate_report(encode({**good, key: value}), hook.SHA, hook.receipt())
        for value in (True, -1, 0, 257, 1.5):
            row = copy.deepcopy(good)
            row["usage"]["call"]["completion_tokens"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                tool.validate_report(encode(row), hook.SHA, hook.receipt())
        row = owner_receipt()
        self.assertEqual(hook.smoke.lifecycle(encode(row), tool_smoke=True, prepared_sha256=hook.SHA), row)
        for mode in ({}, {"inference_smoke": True}, {"protocol_smoke": True},
                     {"tool_smoke": True, "protocol_smoke": True}):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                hook.smoke.lifecycle(encode(row), prepared_sha256=hook.SHA, **mode)

    def test_host_network_none_closed_mode_nonzero_failure_and_inspect_no_runtime(self):
        hook.smoke_tests.SmokeTests.setUpClass()
        fixture = hook.smoke_tests.SmokeTests()
        fixture.setUp()
        fixture.args.tool_smoke = True
        row = owner_receipt(fixture.build["prepared_sha256"])
        with fixture.running(output=encode(row)) as (contract, result):
            fixture.run_owner(contract, result)
            self.assertEqual(result["result"], "pass")
            self.assertEqual(result["lifecycle"]["tool_roundtrip"]["usage"], receipt()["usage"])
        command = next(cmd for cmd in fixture.commands if cmd[2] == "create")
        self.assertEqual(command[-1], "--tool-smoke")
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertNotIn("--publish", command)
        row.update(status="failed", reason="owner-failed")
        row["tool_roundtrip"].update(result="fail", failure="completion")
        self.assertEqual(hook.smoke.lifecycle_failure(encode(row), tool_smoke=True,
            prepared_sha256=fixture.build["prepared_sha256"]), row)
        with fixture.running() as (contract, result):
            original = fixture.recorder.call.side_effect
            def call(command, env, **kwargs):
                if command[2] == "start":
                    raise subprocess.CalledProcessError(1, command, output=encode(row), stderr=b"PRIVATE_CANARY")
                return original(command, env, **kwargs)
            fixture.recorder.call.side_effect = call
            fixture.run_owner(contract, result)
            self.assertEqual(result["owner_failure"], row)
            self.assertEqual(result["result"], "fail")
            self.assertEqual(result["cleanup"], "confirmed")
            self.assertNotIn(b"PRIVATE_CANARY", encode(result))
        fixture.args.run = False
        with fixture.admission() as (_, run):
            observed = hook.smoke.preflight(fixture.args)
            self.assertEqual(observed["mode"], service.TOOL_MODE)
            run.assert_not_called()

    def test_preparation_failure_closed_mode_and_no_private_text(self):
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(service.Contract, "begin", side_effect=ValueError(fixtures.CANARY)))
            output = io.StringIO()
            stack.enter_context(redirect_stdout(output))
            code = process.main(["--prepared-sha256", hook.SHA, "--provider-config", "/PRIVATE_CANARY",
                "--api-key-file", "/PRIVATE_CANARY", "--cert", "/PRIVATE_CANARY", "--key", "/PRIVATE_CANARY",
                "--hostname", "private.invalid", "--input-price", "1", "--output-price", "2",
                "--hard-stop-at", "unused", "--tool-smoke"])
        self.assertEqual(code, 1)
        row = hook.smoke.lifecycle_failure(output.getvalue().encode(), tool_smoke=True, prepared_sha256=hook.SHA)
        self.assertEqual((row["preparation_stage"], row["preparation_category"]), ("contract", "internal"))
        self.assertIsNone(row["tool_roundtrip"])
        self.assertNotIn("PRIVATE_CANARY", output.getvalue())

    def test_helper_imports_without_site_packages_or_checker_payload(self):
        root = str(Path(tool.__file__).resolve().parent)
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); import selected_service_tool as t; "
            "assert t.process.service is t.service; assert 'tr_provider_check' not in sys.modules; "
            "assert t.contract()['authenticated_requests'] == 2", root],
            capture_output=True, timeout=15, check=True)
        self.assertEqual((result.stdout, result.stderr), (b"", b""))


if __name__ == "__main__":
    unittest.main()
