# SPDX-License-Identifier: Apache-2.0
"""Pinned checker assertions and owned drain on synthetic wires; no listeners."""

from contextlib import contextmanager, ExitStack, redirect_stdout
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

import selected_service_protocol as protocol
import test_selected_service_conformance as subset_tests
import test_selected_service_hook as hook
import test_selected_service_inference as fixtures

subset, inference, process, service = protocol.subset, protocol.inference, protocol.process, protocol.service
encode = inference.encoded


def receipt(sha=hook.SHA, *, warning=False):
    run = protocol.Run(hook.receipt(sha))
    row = run.report
    row.update(prepared_sha256=sha, subset=subset_tests.receipt(sha),
               checks=dict.fromkeys(subset.pinned.MANDATORY, "pass"), all_17_pass=not warning,
               checker_result="warn" if warning else "pass",
               result="fail" if warning else "pass", failure="completion" if warning else "none")
    if warning:
        row["checks"]["chat.forwarded-fields"] = "warn"
    row["attempts"] = dict.fromkeys(protocol.CASES, True)
    row["usage"] = {name: {"prompt_tokens": 12, "completion_tokens": 2} for name in row["usage"]}
    row["drain"] = dict.fromkeys(row["drain"], True)
    return row


def owner_receipt(sha=hook.SHA, *, warning=False):
    row = {**hook.owner_receipt(sha), "schema": service.PROTOCOL_SCHEMA, "mode": service.PROTOCOL_MODE,
           "protocol": receipt(sha, warning=warning)}
    if warning:
        row["status"] = "failed"
    return row


@unittest.skipUnless(importlib.util.find_spec("tr_provider_check"), "run with hash-locked checker test environment")
class PinnedProtocolTests(unittest.TestCase):
    @contextmanager
    def fixture(self, *, warning=False, json_change=None, chunks=None):
        from tr_provider_check import contract as rules, http as client
        from tr_provider_check.checks import callability, catalog, chat, streaming
        import httpx
        with subset_tests.PinnedCheckerTests().fixture() as state, ExitStack() as stack:
            loader = stack.enter_context(mock.patch.object(subset, "checker_modules"))
            loader.return_value.__enter__.return_value = SimpleNamespace(httpx=httpx, rules=rules, client=client,
                catalog=catalog, chat=chat, callability=callability, streaming=streaming)
            stack.enter_context(mock.patch.object(protocol, "time", state.clock))
            source = Path(protocol.__file__).read_bytes()
            state.owner.prepared.files["scripts/selected_service_protocol.py"] = process.digest(source)
            original = state.read.side_effect
            state.read.side_effect = lambda name: source if name == protocol.__file__ else original(name)
            state.active_alarms = []
            @contextmanager
            def alarm(deadline):
                state.active_alarms.append(deadline)
                try:
                    yield
                finally:
                    self.assertEqual(state.active_alarms.pop(), deadline)
            stack.enter_context(mock.patch.object(service.identity.worker, "alarm", side_effect=alarm))
            bodies = [(200, encode(subset_tests.catalog(state.supervisor)))]
            for case in protocol.JSON_CASES:
                row = fixtures.completion()
                row["choices"][0]["message"]["content"] = "PONG"
                row["usage"].update(completion_tokens=2, total_tokens=14)
                status = 200
                if warning and case in ("thinking", "reasoning", "reasoning-effort"):
                    status, row = 400, {"error": {"message": fixtures.CANARY, "type": "invalid_request"}}
                if json_change:
                    json_change(case, row)
                bodies.append((status, encode(row)))
            chunks = subset_tests.stream() if chunks is None else chunks
            bodies.append((200, b"".join(format(len(part), "x").encode() + b"\r\n" + part + b"\r\n"
                for part in chunks) + b"0\r\n\r\n"))
            for index, (status, body) in enumerate(bodies):
                state.sockets.append(fixtures.resource())
                connection = fixtures.resource()
                header = ("HTTP/1.1 " + str(status) + " Synthetic\r\n").encode()
                if index == len(bodies) - 1:
                    header += b"Content-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n"
                else:
                    header += ("Content-Type: application/json\r\nContent-Length: " + str(len(body)) + "\r\n").encode()
                connection.makefile.return_value = io.BytesIO(header + b"\r\n" + body)
                state.connections.append(connection)
            state.connect.side_effect = state.sockets
            state.owner.trust.wrap_socket.side_effect = state.connections
            yield state

    def finish(self, state, run, *, signal=True):
        if signal and run.report["drain"]["retirement_requested"]:
            run.drain.retiring()
        row = run.finish(state.supervisor)
        self.assertEqual(protocol.validate_report(encode(row), state.sha, state.prior), row)
        self.assertEqual(state.active_alarms, [])
        for private in (fixtures.CANARY.encode(), fixtures.KEY, b"service.invalid", b"Authorization", b"private.invalid"):
            self.assertNotIn(private, encode(row))
        self.assertTrue(run.drain is None or not run.drain.thread or not run.drain.thread.is_alive())
        return row

    def test_full_unchanged_checker_sequence_then_first_content_gated_drain(self):
        with self.fixture() as state:
            run = protocol.start(state.supervisor, state.prior)
            self.assertEqual(state.active_alarms, [])
            self.assertTrue(run.report["all_17_pass"], run.report)
            self.assertTrue(run.report["drain"]["reader_started"])
            self.assertFalse(run.drain.completed)
            self.assertEqual(run.drain.count, len(subset_tests.stream()[0]))
            self.assertEqual(state.connect.call_count, 14)
            self.assertEqual(state.owner.peer.call_count, 14)
            # A retired TLS identity must not be fabricated or rechecked by the reader.
            state.owner.bound.side_effect = AssertionError("retired TLS cannot be reobserved")
            row = self.finish(state, run)
            self.assertEqual(row, receipt(state.sha))
            for connection in state.connections:
                connection.__exit__.assert_called_once()
            requests = [json.loads(c.sendall.call_args.args[0].split(b"\r\n\r\n", 1)[1])
                        for c in state.connections[5:-1]]
            self.assertEqual(requests[0]["max_tokens"], 4)
            self.assertEqual(requests[-1]["max_tokens"], 16)
            self.assertTrue(all("max_tokens" not in row for row in requests[1:-1]))
            self.assertEqual(requests[2]["temperature"], 0)
            self.assertTrue(all(row["stream"] is False for row in requests))
            drain = json.loads(state.connections[-1].sendall.call_args.args[0].split(b"\r\n\r\n", 1)[1])
            self.assertEqual(drain, {"model": protocol.service.MODEL, "stream": True,
                "stream_options": {"include_usage": True}, "max_tokens": 256,
                "temperature": 0, "reasoning_effort": "none",
                "messages": [{"role": "user", "content": protocol.DRAIN_PROMPT}]})
            self.assertEqual(protocol.contract()["drain_reasoning_effort"], "none")
            self.assertEqual(protocol.contract()["schema"], "nib.selected-service-protocol-contract/v2")

    def test_reasoning_only_stream_cannot_trigger_retirement(self):
        chunks = subset_tests.stream()
        chunks[0] = chunks[0].replace(b'"content":', b'"reasoning_content":')
        with self.fixture(chunks=chunks) as state:
            row = self.finish(state, protocol.start(state.supervisor, state.prior))
            self.assertEqual(row["result"], "fail")
            self.assertTrue(row["all_17_pass"])
            self.assertFalse(row["drain"]["first_content"])
            self.assertFalse(row["drain"]["retirement_requested"])
            self.assertFalse(row["drain"]["reader_started"])
            self.assertFalse(row["drain"]["signal_sent"])
            self.assertTrue(row["drain"]["resources_closed"])
            self.assertEqual(row["usage"]["drain"], {"prompt_tokens": 12, "completion_tokens": 2})
            self.assertFalse(row["usage_unknown"]["drain"])

    def test_expected_forwarded_field_warn_is_retained_and_drain_still_completes(self):
        with self.fixture(warning=True) as state:
            row = self.finish(state, protocol.start(state.supervisor, state.prior))
            self.assertEqual((row["result"], row["checker_result"]), ("fail", "warn"))
            self.assertEqual(row["checks"]["chat.forwarded-fields"], "warn")
            self.assertEqual(len(row["checks"]), 17)
            self.assertTrue(all(row["drain"].values()))
            self.assertFalse(row["all_17_pass"])
            for case in ("thinking", "reasoning", "reasoning-effort"):
                self.assertIsNone(row["usage"][case])
                self.assertTrue(row["usage_unknown"][case])

    def test_failed_json_usage_stays_unknown_without_erasing_other_work(self):
        def change(case, row):
            if case == "pong":
                del row["usage"]
        with self.fixture(json_change=change) as state:
            row = self.finish(state, protocol.start(state.supervisor, state.prior))
            self.assertEqual(row["checker_result"], "fail")
            self.assertEqual(row["checks"]["chat.usage"], "fail")
            self.assertTrue(row["usage_unknown"]["pong"])
            self.assertIsNone(row["usage"]["pong"])
            self.assertTrue(row["drain"]["completed"])
            self.assertEqual(row["usage"]["drain"], {"prompt_tokens": 12, "completion_tokens": 2})

    def test_drain_requires_usage_done_eof_and_nonbuffered_first_content(self):
        cases = [(subset_tests.stream(usage=False), False), (subset_tests.stream(done=False), True),
                 (subset_tests.stream() + [b"data: PRIVATE_CANARY\n\n"], True),
                 ([b"".join(subset_tests.stream())], True),
                 ([part for index, part in enumerate(subset_tests.stream()) if index != 1], False)]
        for chunks, known in cases:
            with self.subTest(chunks=len(chunks), known=known), self.fixture(chunks=chunks) as state:
                row = self.finish(state, protocol.start(state.supervisor, state.prior))
                self.assertEqual(row["result"], "fail")
                self.assertFalse(row["drain"]["completed"])
                self.assertEqual(row["usage_unknown"]["drain"], not known)
                self.assertTrue(row["drain"]["resources_closed"])

    def test_external_cancellation_remains_sticky_before_and_after_retirement(self):
        for when in ("first-content", "retirement", "finished"):
            with self.subTest(when=when), self.fixture() as state:
                if when == "first-content":
                    state.connections[-1].sendall.side_effect = lambda _: state.handlers[inference.signal.SIGTERM](0, None)
                run = protocol.start(state.supervisor, state.prior)
                if when != "first-content":
                    if when == "finished":
                        run.drain.retiring()
                        self.assertTrue(run.drain.join())
                    state.supervisor.shutdown.set()
                row = self.finish(state, run, signal=when == "retirement")
                self.assertEqual(row["result"], "fail")
                self.assertEqual(row["failure"], "cancelled")
                self.assertTrue(state.supervisor.shutdown.is_set())
                self.assertTrue(row["drain"]["resources_closed"])
                if when == "first-content":
                    self.assertTrue(row["usage_unknown"]["drain"])
                else:
                    self.assertTrue(row["drain"]["completed"])

    def test_missing_retirement_signal_deadline_and_reader_start_failure_close_resources(self):
        for failure in ("no-signal", "deadline", "start"):
            with self.subTest(failure=failure), self.fixture() as state, ExitStack() as stack:
                if failure == "start":
                    stack.enter_context(mock.patch.object(protocol.threading.Thread, "start",
                                                         side_effect=RuntimeError(fixtures.CANARY)))
                run = protocol.start(state.supervisor, state.prior)
                if failure == "deadline":
                    state.clock.advance(inference.REQUEST_SECONDS + 1)
                row = self.finish(state, run, signal=failure == "deadline")
                self.assertEqual(row["result"], "fail")
                self.assertTrue(row["drain"]["resources_closed"])
                self.assertTrue(row["usage_unknown"]["drain"])
                self.assertEqual(state.connect.call_count, 14)

    def test_stream_socket_close_failure_never_confirms_reader_resources(self):
        for early in (False, True):
            chunks = [b"".join(subset_tests.stream())] if early else None
            with self.subTest(early=early), self.fixture(chunks=chunks) as state:
                state.connections[-1].__exit__.side_effect = OSError(fixtures.CANARY)
                row = self.finish(state, protocol.start(state.supervisor, state.prior))
                self.assertEqual(row["result"], "fail")
                self.assertFalse(row["drain"]["resources_closed"])
                self.assertEqual(row["drain"]["reader_joined"], not early)
                self.assertFalse(row["usage_unknown"]["drain"])
                state.connections[-1].__exit__.assert_called_once()

    def test_unchecked_case_usage_unknown_cannot_be_promoted_by_seventeen_passes(self):
        def change(case, row):
            if case == "max-token":
                del row["usage"]
        with self.fixture(json_change=change) as state:
            row = self.finish(state, protocol.start(state.supervisor, state.prior))
            self.assertTrue(row["all_17_pass"])
            self.assertEqual(row["checker_result"], "pass")
            self.assertEqual(row["result"], "fail")
            self.assertTrue(row["usage_unknown"]["max-token"])
            self.assertTrue(row["drain"]["completed"])

    def test_bounded_reader_join_shuts_down_only_its_owned_socket_and_freezes_receipt(self):
        with self.fixture() as state:
            run = protocol.start(state.supervisor, state.prior)
            run.drain.retiring()
            self.assertTrue(run.drain.join())
            thread = mock.Mock(ident=123)
            thread.is_alive.return_value = True
            run.drain.thread = thread
            run.drain.closed = False
            run.report["drain"]["resources_closed"] = False
            run.drain.completed = False
            row = run.finish(state.supervisor)
            frozen = encode(row)
            self.assertEqual(row["result"], "fail")
            self.assertFalse(row["drain"]["reader_joined"])
            self.assertFalse(row["drain"]["resources_closed"])
            self.assertEqual(thread.join.call_count, 2)
            self.assertTrue(all(0 <= call.args[0] <= 5 for call in thread.join.call_args_list))
            state.connections[-1].shutdown.assert_called_once_with(protocol.socket.SHUT_RDWR)
            for connection in state.connections[:-1]:
                connection.shutdown.assert_not_called()
            run.drain.usage, run.drain.completed = (999, 999), True
            self.assertEqual(encode(row), frozen)
            protocol.validate_report(frozen, state.sha, state.prior)

    def test_actual_supervisor_and_reader_keep_order_cancel_usage_and_cleanup(self):
        for failure in (None, "warn", "cancel", "close", "reap", "wait"):
            with self.subTest(failure=failure), self.fixture(warning=failure == "warn") as state, ExitStack() as stack:
                supervisor, owner = state.supervisor, state.owner
                supervisor.used, supervisor.children = False, {}
                owner.start = mock.Mock(side_effect=lambda role, contract, inputs, register, deadline:
                    register(owner.processes[role]))
                owner.probe = mock.Mock(side_effect=lambda role, child, deadline:
                    service.EngineReady(hook.lifecycle_tests.endpoint(child.pid), encode({"data": [
                        {"id": service.MODEL, "max_model_len": service.CONTEXT}]}),
                        service.CONTEXT, service.MEMORY_BYTES, 0) if role == "engine" else True)
                events = []
                def begin(role, child, deadline):
                    events.append(("term", role))
                    self.assertIsNone(owner.processes["engine"].poll())
                    self.assertEqual(supervisor.phase, "draining")
                    if failure == "cancel":
                        supervisor.shutdown.set()
                    return True
                def wait(role, child, deadline):
                    events.append(("wait", role))
                    self.assertIsNone(owner.processes["engine"].poll())
                    if failure == "wait" and role == "tls":
                        raise service.identity.worker.Abort("timeout")
                    child.returncode = 0
                    return True
                def reap(role, child, deadline):
                    events.append(("reap", role))
                    if role == "guard":
                        # The reader's resource is already closed before downstream retirement.
                        state.connections[-1].__exit__.assert_called_once()
                    child.returncode = 0
                    return failure != "reap"
                owner.begin_drain, owner.wait_drain = mock.Mock(side_effect=begin), mock.Mock(side_effect=wait)
                owner.drain, owner.reap = mock.Mock(side_effect=wait), mock.Mock(side_effect=reap)
                owner.reap_unregistered = mock.Mock(return_value=True)
                stack.enter_context(mock.patch.object(service.identity.Endpoint, "check"))
                stack.enter_context(mock.patch.object(inference, "run_once", return_value=state.prior))
                if failure == "close":
                    state.connections[-1].__exit__.side_effect = OSError(fixtures.CANARY)
                row = supervisor.run(protocol_smoke=True)
                self.assertEqual(row["status"], "retired" if failure is None else "failed", row)
                self.assertEqual(row["cleanup_confirmed"], failure not in ("close", "reap"))
                if failure == "wait":
                    state.connections[-1].shutdown.assert_called_once_with(protocol.socket.SHUT_RDWR)
                    self.assertFalse(row["cleanup"][0]["drained"])
                else:
                    self.assertTrue(row["protocol"]["drain"]["completed"])
                    self.assertFalse(row["protocol"]["usage_unknown"]["drain"])
                self.assertEqual(events, [("term", "tls"), ("wait", "tls"), ("reap", "tls"),
                    ("wait", "guard"), ("reap", "guard"), ("reap", "engine")])
                check = hook.smoke.lifecycle if failure is None else hook.smoke.lifecycle_failure
                self.assertEqual(check(encode(row), protocol_smoke=True, prepared_sha256=state.sha), row)
                self.assertEqual(state.active_alarms, [])
                self.assertLess(state.clock.mono, supervisor.cutoff())

    def test_deadline_before_http_eof_cannot_promote_complete_sse(self):
        with self.fixture() as state:
            run = protocol.start(state.supervisor, state.prior)
            original = run.drain.read
            def read():
                block = original()
                if not block:
                    state.clock.advance(inference.REQUEST_SECONDS + 1)
                return block
            run.drain.read = read
            row = self.finish(state, run)
            self.assertEqual(row["result"], "fail")
            self.assertFalse(row["drain"]["completed"])
            self.assertFalse(row["usage_unknown"]["drain"])

    def test_drain_peer_failure_preserves_checks_without_sending_request(self):
        with self.fixture() as state:
            original = state.owner.peer.side_effect
            def peer(*args):
                if state.owner.peer.call_count == 14:
                    raise service.Rejected("identity")
                return original(*args)
            state.owner.peer.side_effect = peer
            row = self.finish(state, protocol.start(state.supervisor, state.prior))
            self.assertEqual(row["result"], "fail")
            self.assertTrue(row["all_17_pass"])
            self.assertFalse(row["attempts"]["drain"])
            self.assertTrue(row["drain"]["resources_closed"])
            state.connections[-1].sendall.assert_not_called()

    def test_source_tampering_and_second_use_cannot_open_more_connections(self):
        for change in ("source", "double"):
            with self.subTest(change=change), self.fixture() as state:
                if change == "source":
                    state.owner.prepared.files["scripts/selected_service_protocol.py"] = "0" * 64
                    row = self.finish(state, protocol.start(state.supervisor, state.prior))
                    self.assertEqual(row["failure"], "identity")
                    self.assertEqual(state.connect.call_count, 4)
                else:
                    self.finish(state, protocol.start(state.supervisor, state.prior))
                    row = protocol.start(state.supervisor, state.prior).finish(state.supervisor)
                    self.assertEqual(row["failure"], "already-used")
                    self.assertEqual(state.connect.call_count, 14)

    def test_image_import_probe_requires_nonroot_isolation_inventory_and_native_modules(self):
        import builtins
        from tr_provider_check.checks import callability, catalog, chat, streaming
        image = subset_tests.image_tests.image
        prepared = {"lab_sha256": hook.SHA, "files": {"checker/trial.json": hook.SHA}}
        raw = encode(prepared)
        for failure in (None, "uid", "isolation", "hash", "inventory", "native"):
            with self.subTest(failure=failure), ExitStack() as stack:
                stack.enter_context(mock.patch.object(image.sys, "flags", mock.Mock(
                    wraps=sys.flags, isolated=failure != "isolation", no_site=True)))
                stack.enter_context(mock.patch.object(image.sys, "dont_write_bytecode", True))
                stack.enter_context(mock.patch.object(image.os, "getuid", return_value=0 if failure == "uid" else 65532))
                stack.enter_context(mock.patch.object(image.os, "getgid", return_value=65532))
                stack.enter_context(mock.patch.object(image, "read", return_value=raw))
                stack.enter_context(mock.patch.object(image.owner, "preparation_receipt",
                    return_value={} if failure == "inventory" else prepared))
                loader = stack.enter_context(mock.patch.object(subset, "checker_modules"))
                loader.return_value.__enter__.return_value = SimpleNamespace(catalog=catalog, chat=chat,
                    callability=callability, streaming=streaming)
                original = builtins.__import__
                def importing(name, *args, **kwargs):
                    if failure == "native" and name == "brotli":
                        raise ImportError(fixtures.CANARY)
                    return original(name, *args, **kwargs)
                stack.enter_context(mock.patch.object(builtins, "__import__", side_effect=importing))
                expected = "0" * 64 if failure == "hash" else process.digest(raw)
                if failure is None:
                    image.checker_imports(expected)
                    loader.assert_called_once()
                else:
                    with self.assertRaisesRegex(ValueError, "^selected checker imports rejected$"):
                        image.checker_imports(expected)


class JoinTests(unittest.TestCase):
    setup_service = hook.HookTests.setup_service
    owned = hook.HookTests.owned

    def test_owner_retires_front_first_then_joins_reader_before_stopping_engine(self):
        for warning in (False, True):
            with self.subTest(warning=warning), self.owned() as (supervisor, owner, clock, _, _):
                run = protocol.Run(hook.receipt())
                run.report = receipt(warning=warning)
                run.drain = SimpleNamespace(closed=True, thread=None,
                    deadline=supervisor.cutoff() - service.CLEANUP_RESERVE, stop=lambda: None,
                    retiring=lambda: owner.events.append(("reader-released", "tls")),
                    join=lambda: owner.events.append(("reader-joined", "tls")) or True)
                owner.begin_drain = lambda *args: owner.events.append(("term", "tls")) or True
                owner.wait_drain = owner.drain
                with mock.patch.object(protocol, "start", return_value=run) as start, \
                        mock.patch.object(service.identity.worker, "alarm", return_value=ExitStack()):
                    row = supervisor.run(protocol_smoke=True)
                start.assert_called_once_with(supervisor, hook.receipt())
                self.assertEqual(row, owner_receipt(warning=warning))
                self.assertEqual(supervisor.phase, "draining")
                self.assertEqual(owner.events[-9:], [("term", "tls"), ("reader-released", "tls"),
                    ("drain", "tls"), ("reap", "tls"), ("reader-joined", "tls"),
                    ("drain", "guard"), ("reap", "guard"), ("reap", "engine"), ("reap-unregistered", None)])
                self.assertLess(clock.mono, supervisor.cutoff() - service.CLEANUP_RESERVE)

    def test_host_closed_mode_and_failure_join_retain_warn_and_drain(self):
        join = {"protocol_smoke": True, "prepared_sha256": hook.SHA}
        for warning in (False, True):
            row = owner_receipt(warning=warning)
            check = hook.smoke.lifecycle_failure if warning else hook.smoke.lifecycle
            self.assertEqual(check(encode(row), **join), row)
            for mode in ({}, {"inference_smoke": True}, {"conformance_smoke": True}):
                with self.assertRaises(ValueError):
                    check(encode(row), prepared_sha256=hook.SHA, **mode)
        good = owner_receipt()
        changes = [("all_17_pass", False), ("checker_result", "warn"), ("extra", fixtures.CANARY)]
        changes += [(name, True) for name in inference.CLAIMS]
        for name, value in changes:
            changed = copy.deepcopy(good)
            changed["protocol"][name] = value
            with self.subTest(name=name), self.assertRaises(ValueError):
                hook.smoke.lifecycle(encode(changed), **join)
        for name in ("reader_joined", "resources_closed"):
            changed = owner_receipt(warning=True)
            changed["protocol"]["drain"][name] = False
            with self.subTest(name=name), self.assertRaises(ValueError):
                hook.smoke.lifecycle_failure(encode(changed), **join)

    def test_unjoined_reader_forces_unconfirmed_cleanup_and_preserves_usage(self):
        with self.owned() as (supervisor, owner, _, _, _):
            run = protocol.Run(hook.receipt())
            run.report = receipt()
            run.report["drain"].update(reader_joined=False, resources_closed=False, completed=False)
            run.drain = SimpleNamespace(closed=False, thread=None, retiring=lambda: None, join=lambda: False,
                deadline=supervisor.cutoff() - service.CLEANUP_RESERVE, stop=lambda: None)
            owner.begin_drain = lambda *args: True
            owner.wait_drain = owner.drain
            with mock.patch.object(protocol, "start", return_value=run), \
                    mock.patch.object(service.identity.worker, "alarm", return_value=ExitStack()):
                row = supervisor.run(protocol_smoke=True)
            self.assertFalse(row["cleanup_confirmed"])
            self.assertEqual(row["status"], "failed")
            self.assertEqual(row["protocol"]["usage"], receipt()["usage"])
            self.assertEqual(hook.smoke.lifecycle_failure(encode(row), protocol_smoke=True,
                prepared_sha256=hook.SHA), row)

    def test_real_host_command_network_none_and_nonzero_join(self):
        hook.smoke_tests.SmokeTests.setUpClass()
        fixture = hook.smoke_tests.SmokeTests()
        fixture.setUp()
        fixture.args.protocol_smoke = True
        row = owner_receipt(fixture.build["prepared_sha256"])
        with fixture.running(output=encode(row)) as (contract, result):
            fixture.run_owner(contract, result)
            self.assertEqual(result["result"], "pass")
            self.assertEqual(result["lifecycle"]["protocol"], row["protocol"])
        command = next(cmd for cmd in fixture.commands if cmd[2] == "create")
        self.assertEqual(command[-1], "--protocol-smoke")
        self.assertNotIn("--publish", command)
        self.assertEqual(command[command.index("--network") + 1], "none")
        failed = owner_receipt(fixture.build["prepared_sha256"], warning=True)
        with fixture.running() as (contract, result):
            original = fixture.recorder.call.side_effect
            def call(command, env, **kwargs):
                if command[2] == "start":
                    raise subprocess.CalledProcessError(1, command, output=encode(failed), stderr=b"PRIVATE_CANARY")
                return original(command, env, **kwargs)
            fixture.recorder.call.side_effect = call
            fixture.run_owner(contract, result)
            self.assertEqual(result["result"], "fail")
            self.assertEqual(result["owner_failure"], failed)
            self.assertEqual(result["cleanup"], "confirmed")
            self.assertNotIn("CANARY", json.dumps(result))
        fixture.args.run = False
        with fixture.admission() as (_, run):
            observed = hook.smoke.preflight(fixture.args)
            self.assertEqual(observed["mode"], service.PROTOCOL_MODE)
            run.assert_not_called()

    def test_missing_checker_is_rejected_before_private_inputs_and_owner(self):
        with ExitStack() as stack:
            begin = stack.enter_context(mock.patch.object(service.Contract, "begin"))
            begin.return_value.execution_profile = "tau8k-eager"
            stack.enter_context(mock.patch.object(process, "Prepared", return_value=SimpleNamespace(
                files={}, lab={"execution_profile": "tau8k-eager"})))
            inputs = stack.enter_context(mock.patch.object(service, "snapshot_inputs"))
            owner = stack.enter_context(mock.patch.object(process, "ProcessOwner"))
            output = io.StringIO()
            stack.enter_context(redirect_stdout(output))
            result = process.main(["--prepared-sha256", hook.SHA, "--provider-config", "/PRIVATE_CANARY",
                "--api-key-file", "/PRIVATE_CANARY", "--cert", "/PRIVATE_CANARY", "--key", "/PRIVATE_CANARY",
                "--hostname", "private.invalid", "--input-price", "1", "--output-price", "2",
                "--hard-stop-at", "unused", "--protocol-smoke"])
        self.assertEqual(result, 1)
        inputs.assert_not_called()
        owner.assert_not_called()
        row = hook.smoke.lifecycle_failure(output.getvalue().encode(), protocol_smoke=True,
                                           prepared_sha256=hook.SHA)
        self.assertEqual(row["preparation_stage"], "prepared")
        self.assertEqual(row["preparation_category"], "contract")
        self.assertIsNone(row["protocol"])
        self.assertNotIn("CANARY", output.getvalue())


if __name__ == "__main__":
    unittest.main()
