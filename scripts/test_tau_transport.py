# SPDX-License-Identifier: Apache-2.0
"""Local pipes and synthetic inputs only; no Docker, packages or endpoint calls."""

import ast
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import signal
import struct
import threading
import time
import traceback
import unittest
from unittest import mock

import tau_transport as transport
import test_tau_preflight as fixtures

tau, worker = transport.tau, transport.worker
CANARY = "private-content-canary-must-not-be-retained"


def manifest():
    config = fixtures.configuration()
    return {"schema": transport.MANIFEST, "purpose": "nonthinking-transport-prerequisite",
            "quality_eligible": False, "benchmark_manifest_sha256": "a" * 64,
            "upstream_revision": tau.REVISION, "settings": config,
            "framing": {role: {"reasoning_effort": "none", "parallel_tool_calls": True,
                       "served_model": "synthetic-" + role, "framing_sha256": "b" * 64,
                       "tokenizer_sha256": tau.VOCABULARY_DIGEST, "context_tokens": 4096}
                        for role in tau.ROLES}}


def arguments(**kwargs):
    return {"messages": [{"role": "user", "content": CANARY}], "tools": None, "kwargs": kwargs}


def response(content=CANARY):
    return {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


def record(frozen, category="agent", args=None, result=None, selected="candidate"):
    role, body = transport.request_body(frozen, selected, category, args or arguments())
    framing = frozen["framing"][role]
    bounds = {"manifest_sha256": tau.digest(tau.encoded(frozen)), "request_sha256": tau.digest(tau.encoded(body)),
              "role": role, "prompt_tokens": 1,
              **{key: framing[key] for key in ("framing_sha256", "tokenizer_sha256", "context_tokens")}}
    return {"bounds": bounds, "response": response() if result is None else result}


def packet(sequence=1, category="agent", args=None):
    return {"schema": transport.SCHEMA, "kind": "completion", "sequence": sequence,
            "role": category, "arguments": args or arguments()}


class PipeTests(unittest.TestCase):
    def setUp(self):
        parent_read, child_write = os.pipe()
        child_read, parent_write = os.pipe()
        self.parent_channel = transport.Channel(parent_read, parent_write)
        self.child_channel = transport.Channel(child_read, child_write)
        self.addCleanup(self.parent_channel.close)
        self.addCleanup(self.child_channel.close)
        self.deadline = time.monotonic() + 3

    def session(self, records=None, frozen=None, assertions=()):
        frozen = frozen or manifest()
        parent = transport.ParentSession(self.parent_channel, frozen, tau.digest(tau.encoded(frozen)), "candidate",
                                         records if records is not None else [record(frozen)], assertions=assertions)
        child = transport.WorkerProxy(self.child_channel, self.deadline)
        return parent, child

    def exchange(self, parent, child, category="agent", args=None):
        args = args or arguments()
        results = []
        def run():
            try:
                results.append(child.complete(model=transport.ALIASES[category], messages=args["messages"],
                                              tools=args["tools"], **args["kwargs"]))
            except BaseException as exc:
                results.append(exc)
        thread = threading.Thread(target=run)
        thread.start()
        try:
            parent.serve_once()
        finally:
            thread.join(4)
            if thread.is_alive():
                self.parent_channel.close()
                self.child_channel.close()
                thread.join(1)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 1)
        if isinstance(results[0], BaseException):
            raise results[0]
        return results[0]

    def test_explicit_nonblocking_noninheritable_pipe_ownership(self):
        fds = (self.parent_channel.read_fd, self.parent_channel.write_fd,
               self.child_channel.read_fd, self.child_channel.write_fd)
        self.assertEqual(len(set(fds)), 4)
        for fd in fds:
            self.assertFalse(os.get_inheritable(fd))
            self.assertFalse(os.get_blocking(fd))
        self.parent_channel.close()
        self.parent_channel.close()
        for fd in fds[:2]:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_fragmented_header_and_body_roundtrip(self):
        raw = worker.encoded(packet())
        data = len(raw).to_bytes(4, "big") + raw
        def fragments():
            for index in range(0, len(data), 3):
                os.write(self.child_channel.write_fd, data[index:index + 3])
                time.sleep(0.0001)
        thread = threading.Thread(target=fragments)
        thread.start()
        try:
            self.assertEqual(self.parent_channel.receive(self.deadline), packet())
        finally:
            thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_oversize_header_rejected_before_body_read(self):
        os.write(self.child_channel.write_fd, struct.pack(">I", transport.MAX_FRAME + 1))
        with self.assertRaises(worker.Abort) as caught:
            self.parent_channel.receive(self.deadline)
        self.assertEqual(caught.exception.status, "protocol_error")
        self.assertEqual(self.parent_channel.read_fd, -1)

    def test_malformed_duplicate_json_has_no_raw_exception_or_content(self):
        raw = ('{"value":"' + CANARY + '","value":1}').encode()
        os.write(self.child_channel.write_fd, len(raw).to_bytes(4, "big") + raw)
        try:
            self.parent_channel.receive(self.deadline)
            self.fail("expected rejection")
        except worker.Abort as exc:
            self.assertNotIn(CANARY, "".join(traceback.format_exception(exc)))
            self.assertEqual(str(exc), "protocol_error")

    def test_midframe_eof_is_sticky(self):
        os.write(self.child_channel.write_fd, b"\0\0\0\x20{}")
        self.child_channel.close()
        with self.assertRaises(worker.Abort):
            self.parent_channel.receive(self.deadline)
        with self.assertRaises(worker.Abort):
            self.parent_channel.send({}, self.deadline)

    def test_read_deadline_and_cancel_close_without_waiting_for_eof(self):
        with self.assertRaises(worker.Abort) as caught:
            self.parent_channel.receive(time.monotonic() + 0.01)
        self.assertEqual(caught.exception.status, "timeout")
        self.child_channel.cancelled = lambda: True
        with self.assertRaises(worker.Abort) as caught:
            self.child_channel.send({}, self.deadline)
        self.assertEqual(caught.exception.status, "cancelled")

    def test_write_backpressure_obeys_whole_deadline(self):
        with self.assertRaises(worker.Abort) as caught:
            self.child_channel.send({"content": "x" * (transport.MAX_FRAME - 100)}, time.monotonic() + 0.02)
        self.assertEqual(caught.exception.status, "timeout")

    def test_unbounded_caller_deadline_is_rejected_without_io(self):
        with self.assertRaises(worker.Abort) as caught:
            self.parent_channel.receive(time.monotonic() + transport.MAX_WAIT_SECONDS + 10)
        self.assertEqual(caught.exception.status, "protocol_error")
        self.assertEqual(self.parent_channel.read_fd, -1)

    def test_single_parent_ledger_for_all_roles_and_no_content_output(self):
        frozen = manifest()
        records = [record(frozen), record(frozen, "simulator"),
                   record(frozen, "evaluator", result=response('{"results":[]}'))]
        with mock.patch.object(transport.tau, "Budget", wraps=tau.Budget) as budget:
            parent, child = self.session(records, frozen)
            self.assertFalse(hasattr(child, "budget"))
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                for role in transport.ALIASES:
                    value = self.exchange(parent, child, role)
                    self.assertEqual(value["usage"]["total_tokens"], 2)
            self.assertEqual(budget.call_count, 1)
        self.assertEqual(parent.bridge.counts, {"agent": 1, "simulator": 1, "evaluator": 1})
        self.assertEqual(parent.bridge.budget.requests, 3)
        self.assertEqual(parent.bridge.budget.tokens, 6)
        self.assertEqual(parent.bridge.usage()["total_tokens"], 6)
        self.assertEqual(out.getvalue() + err.getvalue(), "")
        self.assertNotIn(CANARY, json.dumps(parent.bridge.usage()))
        self.assertIsNone(parent.current)

    def test_reservation_before_scripted_delivery(self):
        parent, child = self.session()
        dispatch = parent.bridge.transport
        def observed(*args):
            self.assertIsNotNone(parent.bridge.budget.pending)
            self.assertGreater(parent.bridge.budget.tokens, 2)
            return dispatch(*args)
        parent.bridge.transport = observed
        self.exchange(parent, child)

    def test_missing_bound_inputs_fail_before_any_reservation(self):
        frozen = manifest()
        row = record(frozen)
        row["bounds"] = None
        parent, child = self.session([row], frozen)
        with self.assertRaises(worker.Abort):
            self.exchange(parent, child)
        self.assertEqual(parent.bridge.budget.requests, 0)
        self.assertEqual(parent.bridge.budget.tokens, 0)

    def test_unknown_role_and_replay_cannot_reserve_twice(self):
        frozen = manifest()
        parent, child = self.session([record(frozen), record(frozen)], frozen)
        self.exchange(parent, child)
        self.child_channel.send(packet(sequence=1), self.deadline)
        with self.assertRaises(worker.Abort):
            parent.serve_once()
        self.assertEqual(parent.bridge.budget.requests, 1)
        self.assertEqual(parent.bridge.budget.tokens, 2)
        with self.assertRaises(worker.Abort):
            parent.serve_once()
        self.assertEqual(parent.bridge.budget.requests, 1)

    def test_unrecognized_worker_role_and_extra_fields_fail_before_reservation(self):
        parent, child = self.session()
        value = packet(category="unreviewed")
        value["destination"] = CANARY
        self.child_channel.send(value, self.deadline)
        with self.assertRaises(worker.Abort):
            parent.serve_once()
        self.assertEqual(parent.bridge.budget.requests, 0)

    def test_unknown_role_alone_fails_before_reservation(self):
        parent, _ = self.session()
        self.child_channel.send(packet(category="unreviewed"), self.deadline)
        with self.assertRaises(worker.Abort):
            parent.serve_once()
        self.assertEqual(parent.bridge.budget.requests, 0)

    def test_fresh_proxy_rejects_unknown_alias_without_sending(self):
        child = transport.WorkerProxy(self.child_channel, self.deadline)
        with mock.patch.object(self.child_channel, "send") as send:
            with self.assertRaises(worker.Abort):
                child.complete(model="unreviewed", messages=arguments()["messages"])
            send.assert_not_called()

    def test_worker_rejects_unknown_alias_and_wrong_response_sequence(self):
        child = transport.WorkerProxy(self.child_channel, self.deadline)
        self.parent_channel.send({"schema": transport.SCHEMA, "kind": "response", "sequence": 2,
                                  "response": response()}, self.deadline)
        with self.assertRaises(worker.Abort):
            child.complete(model="nib-scripted-agent", messages=arguments()["messages"])
        with self.assertRaises(worker.Abort):
            child.complete(model="unreviewed", messages=arguments()["messages"])

    def test_whole_trial_wait_timeout_restores_timer(self):
        parent, _ = self.session()
        previous = signal.getsignal(signal.SIGALRM)
        parent.bridge.budget.trial_deadline = time.monotonic() + 0.02
        with self.assertRaises(worker.Abort) as caught:
            parent.serve_once()
        self.assertEqual(caught.exception.status, "timeout")
        self.assertEqual(parent.bridge.budget.requests, 0)
        self.assertEqual(signal.getsignal(signal.SIGALRM), previous)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_cancellation_after_reservation_retains_unknown_usage(self):
        parent, child = self.session()
        def cancelled(*args):
            raise KeyboardInterrupt
        parent.bridge.transport = cancelled
        with self.assertRaises(worker.Abort) as caught:
            self.exchange(parent, child)
        self.assertEqual(caught.exception.status, "cancelled")
        self.assertEqual(parent.bridge.budget.requests, 1)
        self.assertGreater(parent.bridge.budget.tokens, 2)
        self.assertIsNone(parent.bridge.usage())

    def test_late_reply_does_not_refund_reserved_unknown_usage(self):
        parent, child = self.session()
        original = parent.bridge.transport
        def late(*args):
            value = original(*args)
            parent.bridge.clock = lambda: parent.bridge.budget.pending[4]
            parent.bridge.budget.clock = parent.bridge.clock
            return value
        parent.bridge.transport = late
        with self.assertRaises(worker.Abort) as caught:
            self.exchange(parent, child)
        self.assertEqual(caught.exception.status, "timeout")
        self.assertIsNone(parent.bridge.usage())
        self.assertGreater(parent.bridge.budget.tokens, 2)

    def test_reply_delivery_failure_keeps_known_usage_but_session_failed(self):
        parent, child = self.session()
        with mock.patch.object(self.parent_channel, "send", side_effect=worker.Abort("timeout")):
            with self.assertRaises(worker.Abort) as caught:
                self.exchange(parent, child)
        self.assertEqual(caught.exception.status, "timeout")
        self.assertEqual(parent.bridge.usage()["total_tokens"], 2)
        self.assertEqual(parent.bridge.failed, "timeout")

    def test_length_response_settles_usage_and_preserves_truncated_status(self):
        frozen = manifest()
        value = response()
        value["choices"][0]["finish_reason"] = "length"
        parent, child = self.session([record(frozen, result=value)], frozen)
        with self.assertRaises(worker.Abort) as caught:
            self.exchange(parent, child)
        self.assertEqual(caught.exception.status, "truncated")
        self.assertEqual(parent.bridge.usage()["total_tokens"], 2)

    def test_wrong_usage_and_raw_provider_error_never_escape(self):
        frozen = manifest()
        value = response()
        value["usage"]["prompt_tokens"] = 2
        parent, child = self.session([record(frozen, result=value)], frozen)
        with self.assertRaises(worker.Abort) as caught:
            self.exchange(parent, child)
        self.assertEqual(str(caught.exception), "protocol_error")
        self.assertIsNone(parent.bridge.usage())
        self.assertIsNone(parent.current)

    def test_upstream_tools_roundtrip_keeps_alias_and_json_strings(self):
        frozen = manifest()
        call = {"id": "call-1", "type": "function", "function": {"name": "lookup", "arguments": '{"q":"x"}'}}
        value = response()
        value["choices"][0].update(message={"role": "assistant", "content": None, "tool_calls": [call]},
                                   finish_reason="tool_calls")
        followup = arguments(seed=123)
        followup["messages"] += [{"role": "assistant", "content": None, "tool_calls": [{**call, "name": "lookup"}]},
                                 {"role": "tool", "tool_call_id": "call-1", "content": CANARY}]
        parent, child = self.session([record(frozen, result=value), record(frozen, args=followup)], frozen)
        self.assertEqual(self.exchange(parent, child)["choices"][0]["message"]["tool_calls"], [call])
        self.exchange(parent, child, args=followup)
        self.assertEqual(parent.bridge.budget.requests, 2)

    def test_evaluator_uses_existing_assertion_validation(self):
        frozen = manifest()
        value = response('{"results":[]}')
        parent, child = self.session([record(frozen, "evaluator", result=value)], frozen, assertions=["must happen"])
        with self.assertRaises(worker.Abort):
            self.exchange(parent, child, "evaluator")
        self.assertIsNone(parent.bridge.usage())
        self.assertEqual(parent.bridge.counts["evaluator"], 1)

    def test_worker_exit_after_request_fails_delivery_without_losing_known_usage(self):
        parent, _ = self.session()
        self.child_channel.send(packet(), self.deadline)
        self.child_channel.close()
        with self.assertRaises(worker.Abort) as caught:
            parent.serve_once()
        self.assertEqual(caught.exception.status, "protocol_error")
        self.assertEqual(parent.bridge.usage()["total_tokens"], 2)
        self.assertEqual(parent.bridge.budget.requests, 1)

    def test_raw_exception_is_suppressed_and_pending_usage_remains_unknown(self):
        parent, child = self.session()
        def invalid(*args):
            raise RuntimeError(CANARY)
        parent.bridge.transport = invalid
        try:
            self.exchange(parent, child)
            self.fail("expected rejection")
        except worker.Abort as exc:
            self.assertNotIn(CANARY, "".join(traceback.format_exception(exc)))
        self.assertIsNone(parent.bridge.usage())
        self.assertIsNone(parent.current)
        parent.close()
        self.assertEqual(parent.records, [])

    def test_worker_and_parent_reentry_is_rejected_without_second_ledger(self):
        parent, child = self.session()
        with child.busy:
            with self.assertRaises(worker.Abort):
                child.complete(model="nib-scripted-agent", messages=arguments()["messages"])
        self.assertEqual(parent.bridge.budget.requests, 0)
        with parent.busy:
            with self.assertRaises(worker.Abort):
                parent.serve_once()
        self.assertEqual(parent.bridge.budget.requests, 0)

    def test_parent_uses_request_not_trial_deadline_for_delivery(self):
        parent, child = self.session()
        original = self.parent_channel.send
        observed = []
        def send(value, deadline):
            observed.append(deadline)
            self.assertLess(deadline, parent.bridge.budget.trial_deadline)
            return original(value, deadline)
        with mock.patch.object(self.parent_channel, "send", side_effect=send):
            self.exchange(parent, child)
        self.assertEqual(len(observed), 1)

    def test_partial_body_timeout_is_fixed_and_closes(self):
        os.write(self.child_channel.write_fd, b"\0\0\0\x20{")
        with self.assertRaises(worker.Abort) as caught:
            self.parent_channel.receive(time.monotonic() + 0.01)
        self.assertEqual(caught.exception.status, "timeout")
        self.assertEqual(self.parent_channel.read_fd, -1)

    def test_oversized_response_keeps_reservation_and_cannot_leak(self):
        frozen = manifest()
        parent, child = self.session([record(frozen, result=response(CANARY * 8000))], frozen)
        with self.assertRaises(worker.Abort) as caught:
            self.exchange(parent, child)
        self.assertEqual(str(caught.exception), "protocol_error")
        self.assertIsNone(parent.bridge.usage())
        self.assertGreater(parent.bridge.budget.tokens, 2)

    def test_malformed_tool_arguments_use_existing_bridge_rejection(self):
        frozen = manifest()
        value = response()
        value["choices"][0].update(finish_reason="tool_calls", message={"role": "assistant", "content": None,
            "tool_calls": [{"id": "one", "type": "function",
                            "function": {"name": "lookup", "arguments": '{"x":1,"x":2}'}}]})
        parent, child = self.session([record(frozen, result=value)], frozen)
        with self.assertRaises(worker.Abort):
            self.exchange(parent, child)
        self.assertIsNone(parent.bridge.usage())
        self.assertGreater(parent.bridge.budget.tokens, 2)


class ContractTests(unittest.TestCase):
    def test_frozen_benchmark_manifest_is_not_silently_reinterpreted(self):
        frozen = manifest()
        for edit in (lambda row: row.pop("framing"), lambda row: row.update(quality_eligible=True),
                     lambda row: row.update(schema="nib.tau-manifest/v1"),
                     lambda row: row["framing"]["candidate"].update(reasoning_effort="high"),
                     lambda row: row["framing"]["candidate"].pop("reasoning_effort")):
            row = copy.deepcopy(frozen)
            edit(row)
            with self.assertRaises((worker.Abort, ValueError)):
                transport.smoke_manifest(row, tau.digest(tau.encoded(row)))
        with self.assertRaises(worker.Abort):
            transport.smoke_manifest(frozen, "c" * 64)

    def test_explicit_generation_tool_choice_seed_mapping(self):
        frozen = manifest()
        args = arguments(temperature=0, max_tokens=50, num_retries=0, stream=False, n=1,
                         reasoning_effort="none", parallel_tool_calls=True, seed=42, tool_choice="auto")
        args["tools"] = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]
        role, body = transport.request_body(frozen, "reference", "agent", args)
        self.assertEqual(role, "reference")
        self.assertEqual(body["model"], "synthetic-reference")
        self.assertEqual(body["tools"], args["tools"])
        self.assertEqual(body["tool_choice"], "auto")
        self.assertEqual(body["seed"], 42)
        self.assertEqual(body["max_completion_tokens"], 50)
        self.assertNotIn("max_tokens", body)
        self.assertNotIn("num_retries", body)
        for choice in ("none", "required", {"type": "function", "function": {"name": "lookup"}}):
            args["kwargs"]["tool_choice"] = choice
            self.assertEqual(transport.request_body(frozen, "candidate", "agent", args)[1]["tool_choice"], choice)

    def test_unknown_and_conflicting_settings_rejected_not_dropped(self):
        cases = ({"api_base": CANARY}, {"headers": {}}, {"response_format": {}}, {"stream": True}, {"n": 2},
                 {"num_retries": 1}, {"reasoning_effort": "high"}, {"parallel_tool_calls": False},
                 {"max_tokens": 1}, {"temperature": 1}, {"seed": True}, {"stream": 0},
                 {"max_tokens": 50, "max_completion_tokens": 50}, {"tool_choice": "auto"})
        for kwargs in cases:
            with self.subTest(kwargs=tuple(kwargs)), self.assertRaises((worker.Abort, ValueError)):
                transport.request_body(manifest(), "candidate", "agent", arguments(**kwargs))

    def test_reasoning_history_and_multimodal_are_rejected_not_stripped(self):
        for message in ({"role": "assistant", "content": "x", "reasoning_content": "x"},
                        {"role": "assistant", "content": "x", "reasoning": "x"},
                        {"role": "user", "content": [{"type": "image_url", "image_url": CANARY}]}):
            args = arguments()
            args["messages"] = [message]
            with self.assertRaises(worker.Abort):
                transport.request_body(manifest(), "candidate", "agent", args)

    def test_bounds_bind_exact_role_request_manifest_framing_and_context(self):
        frozen = manifest()
        sha = tau.digest(tau.encoded(frozen))
        role, body = transport.request_body(frozen, "candidate", "agent", arguments())
        valid = record(frozen)["bounds"]
        transport.check_bounds(valid, frozen, sha, role, body)
        for key, changed in (("role", "reference"), ("request_sha256", "c" * 64),
                             ("manifest_sha256", "c" * 64), ("framing_sha256", "c" * 64),
                             ("tokenizer_sha256", "c" * 64), ("context_tokens", 8192),
                             ("context_tokens", True), ("prompt_tokens", 101), ("prompt_tokens", True)):
            row = {**valid, key: changed}
            with self.subTest(field=key), self.assertRaises((worker.Abort, ValueError)):
                transport.check_bounds(row, frozen, sha, role, body)
        frozen["framing"]["candidate"]["context_tokens"] = 50
        valid["context_tokens"] = 50
        with self.assertRaises(worker.Abort):
            transport.check_bounds(valid, frozen, sha, role, body)

    def test_count_inputs_cannot_arrive_as_a_worker_packet_field(self):
        self.assertEqual(set(packet()), {"schema", "kind", "sequence", "role", "arguments"})
        self.assertNotIn("bounds", packet()["arguments"])

    def test_imports_and_dispatch_have_no_endpoint_or_upstream_execution(self):
        tree = ast.parse(Path(transport.__file__).read_text())
        imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        imports.update(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
        self.assertEqual(imports, {"decimal", "fcntl", "math", "os", "re", "select", "stat", "threading", "time",
                                   "tau_preflight", "tau_trial_worker", "tau_support_probe"})
        self.assertNotIn("Budget", {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)})
        self.assertFalse(hasattr(transport, "run_upstream"))
        self.assertFalse(hasattr(transport, "live_dispatch"))
        self.assertEqual(transport.ALIASES, {"agent": "nib-scripted-agent", "simulator": "nib-scripted-simulator",
                                            "evaluator": "nib-scripted-evaluator"})


if __name__ == "__main__":
    unittest.main()
