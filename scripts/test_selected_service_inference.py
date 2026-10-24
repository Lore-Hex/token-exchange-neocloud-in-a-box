# SPDX-License-Identifier: Apache-2.0
"""Mock transports only; no listening ports, inference, containers or hardware."""

from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
import copy
import io
import json
from pathlib import Path
import signal
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

import selected_service_inference as inference
from test_selected_service import Child, Clock, endpoint

process, service = inference.process, inference.service
CANARY = "PRIVATE_RESPONSE_CANARY"
KEY = b"SYNTHETIC_TEST_KEY_" * 4
DENIED = inference.encoded({"error": {"message": "invalid_api_key", "type": "invalid_api_key"}})


def completion():
    return {"object": "chat.completion", "model": service.MODEL,
        "choices": [{"index": 0, "finish_reason": "stop", "message": {
            "role": "assistant", "content": CANARY}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 256, "total_tokens": 268}}


def resource():
    value = mock.MagicMock()
    value.__enter__.return_value = value
    value.__exit__.return_value = False
    return value


class InferenceTests(unittest.TestCase):
    @contextmanager
    def fixture(self, *, lifetime=3600):
        clock, shutdown = Clock(), threading.Event()
        owner = process.ProcessOwner.__new__(process.ProcessOwner)
        owner.hostname, owner.prepared = "service.invalid", process.Prepared.__new__(process.Prepared)
        source = Path(inference.__file__).read_bytes()
        owner.prepared.files = {"scripts/selected_service_inference.py": process.digest(source)}
        owner.prepared.lab_sha = process.digest(b"synthetic prepared lab")
        prepared = inference.encoded({"files": owner.prepared.files, "lab_sha256": owner.prepared.lab_sha})
        owner.processes = {role: Child(100 + index) for index, role in enumerate(service.ROLES)}
        owner.identities = {role: endpoint(child.pid).expected for role, child in owner.processes.items()}
        owner.identities["tls"]["listener_ipv6"] = True
        events, deadlines = [], []
        owner.bound = mock.Mock(side_effect=lambda role, *_: owner.identities[role])
        owner.peer = mock.Mock(side_effect=lambda *_: events.append("peer"))
        owner.check_mounts = mock.Mock(return_value=True)
        sockets, connections, responses = [], [], []
        for index in range(3):
            sockets.append(resource())
            connection = resource()
            connection.sendall.side_effect = lambda _, index=index: events.append(("send", index))
            connections.append(connection)
            response = resource()
            response.status = 200 if index == 2 else 401
            response.read.return_value = inference.encoded(completion()) if index == 2 else DENIED
            responses.append(response)
        owner.trust = mock.Mock()
        def wrap(*_, **__):
            events.append("tls")
            return connections[owner.trust.wrap_socket.call_count - 1]
        owner.trust.wrap_socket.side_effect = wrap
        handlers = {number: object() for number in (signal.SIGINT, signal.SIGTERM)}
        original_handlers = dict(handlers)
        def install(number, handler):
            previous, handlers[number] = handlers[number], handler
            return previous
        @contextmanager
        def alarm(deadline):
            deadlines.append(deadline)
            if clock.mono >= deadline:
                raise service.identity.worker.Abort("timeout")
            yield
            if clock.mono >= deadline:
                raise service.identity.worker.Abort("timeout")
        with ExitStack() as stack:
            output, errors = io.StringIO(), io.StringIO()
            stack.enter_context(redirect_stdout(output))
            stack.enter_context(redirect_stderr(errors))
            for module in (inference, service, service.pilot):
                stack.enter_context(mock.patch.object(module, "time", clock))
            contract = service.Contract.begin(clock.timestamp(lifetime), input_price="0.10", output_price="0.20")
            inputs = service.Inputs(Path("/unused-private-snapshots"), {})
            stack.enter_context(mock.patch.object(inputs, "check"))
            supervisor = service.Supervisor(contract, inputs, owner, shutdown)
            supervisor.children, supervisor.used = dict(owner.processes), True
            supervisor.engine_identity = dict(owner.identities["engine"])
            read = stack.enter_context(mock.patch.object(process.bootstrap, "read_source",
                side_effect=lambda name: source if name == inference.__file__ else prepared))
            key = stack.enter_context(mock.patch.object(service.identity, "private_read", return_value=KEY + b"\n"))
            connect = stack.enter_context(mock.patch.object(inference.socket, "create_connection", side_effect=sockets))
            stack.enter_context(mock.patch.object(inference.http.client, "HTTPResponse", side_effect=responses))
            stack.enter_context(mock.patch.object(service.identity.worker, "alarm", side_effect=alarm))
            stack.enter_context(mock.patch.object(inference.signal, "getsignal", side_effect=handlers.__getitem__))
            stack.enter_context(mock.patch.object(inference.signal, "signal", side_effect=install))
            state = SimpleNamespace(supervisor=supervisor, owner=owner, clock=clock, responses=responses,
                sockets=sockets, connections=connections, events=events, connect=connect, read=read, key=key,
                deadlines=deadlines, handlers=handlers, prepared=prepared, sha=process.digest(prepared))
            yield state
            self.assertEqual(handlers, original_handlers)
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(errors.getvalue(), "")

    def result(self, state, *, failure=None):
        result = inference.run_once(state.supervisor)
        self.assertEqual(result["result"], "pass" if failure is None else "fail")
        self.assertEqual(result["failure"], "none" if failure is None else failure)
        raw = inference.encoded(result)
        for private in (CANARY.encode(), KEY, b"short greeting", b"service.invalid", b"Authorization"):
            self.assertNotIn(private, raw)
        if result["prepared_sha256"] is not None:
            self.assertEqual(inference.validate_report(raw, state.sha), result)
        return result

    def test_exact_three_requests_fixed_route_owned_peer_before_key_and_256_cap(self):
        with self.fixture() as state:
            result = self.result(state)
            self.assertEqual(result["usage"], {"prompt_tokens": 12, "completion_tokens": 256})
            self.assertFalse(result["usage_unknown"])
            self.assertFalse(result["auth_upstream_calls_measured"])
            self.assertTrue(all(result["checks"].values()))
            self.assertEqual(state.events, [item for i in range(3) for item in ("peer", "tls", ("send", i))])
            self.assertEqual(state.connect.call_count, 3)
            for i, (raw, tls, response) in enumerate(zip(state.sockets, state.connections, state.responses)):
                self.assertEqual(state.connect.call_args_list[i].args, (("127.0.0.1", 8443),))
                state.owner.peer.assert_any_call("tls", state.owner.identities["tls"], raw, state.deadlines[i + 1])
                header, body = tls.sendall.call_args.args[0].split(b"\r\n\r\n")
                self.assertTrue(header.startswith(b"POST /v1/chat/completions HTTP/1.1\r\n"))
                self.assertEqual(body, inference.request_bytes())
                self.assertEqual(json.loads(body)["max_tokens"], 256)
                self.assertNotIn("reasoning", json.loads(body))
                if i == 0:
                    self.assertNotIn(b"Authorization", header)
                else:
                    self.assertIn(b"Bearer " + (KEY if i == 2 else b"invalid-synthetic-key"), header)
                raw.__exit__.assert_called_once()
                tls.__exit__.assert_called_once()
                response.__exit__.assert_called_once()
            state.key.assert_called_once()

    def test_negative_requires_exact_401_guard_error_and_stops_before_inference(self):
        for index in (0, 1):
            for status, body in ((200, DENIED), (403, DENIED), (401, b'{}'),
                                 (401, inference.encoded({"error": CANARY}))):
                with self.subTest(index=index, status=status, body=body), self.fixture() as state:
                    state.responses[index].status = status
                    state.responses[index].read.return_value = body
                    result = self.result(state, failure="authentication")
                    self.assertFalse(result["attempts"]["authenticated"])
                    self.assertEqual(state.connect.call_count, index + 1)

    def test_no_retries_even_after_failed_attempt(self):
        for fail in (False, True):
            with self.subTest(fail=fail), self.fixture() as state:
                if fail:
                    state.responses[0].status = 403
                self.result(state, failure="authentication" if fail else None)
                before = state.connect.call_count
                result = self.result(state, failure="already-used")
                self.assertFalse(any(result["attempts"].values()))
                self.assertEqual(state.connect.call_count, before)

    def test_wrong_model_malformed_or_duplicate_json_and_invalid_usage_fail_closed(self):
        invalid = [b'not json', b'{"object":1,"object":2}', b'{"usage":NaN}']
        wrong = completion()
        wrong["model"] = "wrong-model"
        invalid.append(inference.encoded(wrong))
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            for value in (None, True, -1, 0, 1.5, 99999):
                row = completion()
                row["usage"][field] = value
                invalid.append(inference.encoded(row))
        row = completion()
        del row["usage"]
        invalid.append(inference.encoded(row))
        for raw in invalid:
            with self.subTest(raw=raw), self.fixture() as state:
                state.responses[2].read.return_value = raw
                result = self.result(state, failure="protocol")
                self.assertTrue(result["usage_unknown"])
                self.assertIsNone(result["usage"])

    def test_nonempty_visible_content_required_but_known_usage_survives(self):
        for content, calls, finish in ((None, None, "stop"), ("  ", None, "length"),
                (CANARY, [{"function": CANARY}], "tool_calls"), (CANARY, {}, "stop")):
            with self.subTest(content=content, calls=calls, finish=finish), self.fixture() as state:
                row = completion()
                row["choices"][0].update(finish_reason=finish, message={"role": "assistant",
                    "content": content, "tool_calls": calls, "reasoning": CANARY})
                state.responses[2].read.return_value = inference.encoded(row)
                result = self.result(state, failure="completion")
                self.assertEqual(result["usage"], {"prompt_tokens": 12, "completion_tokens": 256})
                self.assertFalse(result["usage_unknown"])

    def test_visible_content_at_cap_with_length_is_only_functional_not_qualification(self):
        with self.fixture() as state:
            row = completion()
            row["choices"][0]["finish_reason"] = "length"
            state.responses[2].read.return_value = inference.encoded(row)
            result = self.result(state)
            self.assertTrue(all(result[name] is False for name in inference.CLAIMS))

    def test_lost_response_or_partial_send_has_unknown_usage_and_closes_resources(self):
        for method in ("send", "headers", "body"):
            with self.subTest(method=method), self.fixture() as state:
                operation = {"send": state.connections[2].sendall,
                    "headers": state.responses[2].begin, "body": state.responses[2].read}[method]
                operation.side_effect = OSError(CANARY)
                result = self.result(state, failure="transport")
                self.assertTrue(result["usage_unknown"])
                self.assertEqual(state.connect.call_count, 3)
                state.sockets[2].__exit__.assert_called_once()
                state.connections[2].__exit__.assert_called_once()

    def test_response_size_bounds_and_error_response_never_leak(self):
        for index, limit in ((0, 4096), (2, 1 << 20)):
            with self.subTest(index=index), self.fixture() as state:
                state.responses[index].read.return_value = b"x" * (limit + 1)
                result = self.result(state, failure="protocol")
                state.responses[index].read.assert_called_once_with(limit + 1)
                self.assertEqual(result["usage_unknown"], index == 2)
        with self.fixture() as state:
            state.responses[2].status = 500
            state.responses[2].read.return_value = CANARY.encode()
            self.result(state, failure="completion")

    def test_identity_peer_inputs_mounts_or_dead_child_fail_before_credentials(self):
        for change in ("peer", "identity", "engine", "inputs", "mounts", "child", "registered"):
            with self.subTest(change=change), self.fixture() as state:
                if change == "peer":
                    state.owner.peer.side_effect = service.Rejected("identity")
                elif change == "identity":
                    state.owner.bound.side_effect = lambda *_: None
                elif change == "engine":
                    state.supervisor.engine_identity = {}
                elif change == "inputs":
                    state.supervisor.inputs.check.side_effect = service.Rejected("inputs-changed")
                elif change == "mounts":
                    state.owner.check_mounts.return_value = False
                elif change == "child":
                    state.owner.processes["engine"].returncode = 1
                else:
                    state.supervisor.children["engine"] = Child(100)
                self.result(state, failure="not-ready" if change == "registered" else "identity")
                for connection in state.connections:
                    connection.sendall.assert_not_called()

    def test_prepared_helper_binding_and_lab_receipt_tampering_fail(self):
        for change in ("source", "files", "lab"):
            with self.subTest(change=change), self.fixture() as state:
                if change == "source":
                    state.read.side_effect = lambda name: b"changed source" if name == inference.__file__ else state.prepared
                else:
                    row = json.loads(state.prepared)
                    row["files" if change == "files" else "lab_sha256"] = {}
                    state.read.side_effect = None
                    state.read.return_value = inference.encoded(row)
                self.result(state, failure="identity")
                state.connect.assert_not_called()

    def test_invalid_key_fails_before_network_and_no_nonmain_execution(self):
        with self.fixture() as state:
            state.key.return_value = b"invalid\r\n" + CANARY.encode()
            self.result(state, failure="internal")
            state.connect.assert_not_called()
        with self.fixture() as state, mock.patch.object(inference.threading, "current_thread", return_value=object()):
            self.result(state, failure="contract")
            state.connect.assert_not_called()

    def test_absolute_cutoff_and_reserve_are_never_extended(self):
        with self.fixture(lifetime=service.CLEANUP_RESERVE + 20) as state:
            def received(*_):
                state.clock.advance(4)
                return DENIED
            state.responses[0].read.side_effect = received
            state.responses[1].read.side_effect = received
            self.result(state)
            self.assertEqual(state.deadlines, [120, 105, 109, 120])
            self.assertEqual(state.supervisor.contract.hard_monotonic, 120 + service.CLEANUP_RESERVE)
        with self.fixture() as state:
            state.clock.advance(3600 - service.CLEANUP_RESERVE)
            self.result(state, failure="deadline")
            state.connect.assert_not_called()

    def test_post_response_identity_deadline_signal_and_finalization_preserve_usage(self):
        for change in ("identity", "deadline", "signal", "close", "outer-alarm"):
            with self.subTest(change=change), self.fixture() as state:
                def finishing(*_):
                    if change == "identity":
                        state.owner.identities["engine"]["start_ticks"] += 1
                    elif change == "deadline":
                        state.clock.advance(120)
                    elif change == "signal":
                        state.handlers[signal.SIGTERM]()
                    elif change == "close":
                        raise OSError(CANARY)
                    else:
                        # The outer alarm's exit happens after the last checkpoint.
                        original = state.supervisor.checkpoint
                        calls = 0
                        def checkpoint(deadline):
                            nonlocal calls
                            original(deadline)
                            calls += 1
                            if calls == 2:
                                state.clock.advance(inference.SECONDS)
                        state.supervisor.checkpoint = checkpoint
                    return False
                state.responses[2].__exit__.side_effect = finishing
                failure = {"identity": "identity", "deadline": "deadline", "signal": "cancelled",
                           "close": "transport", "outer-alarm": "deadline"}[change]
                result = self.result(state, failure=failure)
                self.assertEqual(result["usage"], {"prompt_tokens": 12, "completion_tokens": 256})
                self.assertFalse(result["usage_unknown"])

    def test_signal_during_body_sets_shutdown_and_marks_unknown_without_retry(self):
        for number in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(number=number), self.fixture() as state:
                state.responses[2].read.side_effect = lambda *_: state.handlers[number]()
                result = self.result(state, failure="cancelled")
                self.assertTrue(state.supervisor.shutdown.is_set())
                self.assertTrue(result["usage_unknown"])
                state.responses[2].__exit__.assert_called_once()

    def test_report_tampering_bounds_order_types_claims_and_provenance(self):
        with self.fixture() as state:
            result = self.result(state)
        changes = [{"extra": CANARY}, {"failure": []}, {"failure": "internal"},
            {"result": "pass", "checks": dict.fromkeys(inference.CASES, False)},
            {"contract_sha256": process.digest(b"wrong contract")}, {"prepared_sha256": None},
            {"auth_upstream_calls_measured": True}, {"hardware_validated": 0}, {"usage_unknown": True},
            {"checks": {**result["checks"], "missing-key": False}},
            {"attempts": {**result["attempts"], "missing-key": 1}},
            {"usage": {"prompt_tokens": True, "completion_tokens": 256}},
            {"usage": {"prompt_tokens": 12, "completion_tokens": 257}}, {"usage": None}]
        changes.extend({name: True} for name in inference.CLAIMS)
        for change in changes:
            with self.subTest(change=change), self.assertRaises(inference.Rejected):
                inference.validate_report(inference.encoded({**copy.deepcopy(result), **change}), state.sha)
        for raw in (b" " * 4097, b'{"schema":1,"schema":2}'):
            with self.assertRaises(ValueError):
                inference.validate_report(raw, state.sha)
        with self.assertRaises(inference.Rejected):
            inference.validate_report(inference.encoded(result), process.digest(b"wrong expected receipt"))

    def test_real_alarm_bounds_trickle_without_socket_or_fixed_port(self):
        # Exercise the actual shared alarm and HTTP parser in an isolated child;
        # only its bytes source is fake, so slow chunks cannot renew the bound.
        code = r'''
import io, signal, sys, time
from types import SimpleNamespace
from unittest import mock
sys.path.insert(0, sys.argv[1])
import selected_service_inference as inference
class Trickle(io.RawIOBase):
    def __init__(self):
        self.header = b'HTTP/1.1 200 OK\r\nContent-Length: 1048576\r\n\r\n'
        self.chunks = 0
    def readable(self):
        return True
    def readinto(self, target):
        if self.header:
            data, self.header = self.header[:len(target)], self.header[len(target):]
            target[:len(data)] = data
            return len(data)
        time.sleep(.005)
        self.chunks += 1
        target[0] = 120
        return 1
body = Trickle()
sock = mock.MagicMock()
sock.__enter__.return_value = sock
sock.makefile.return_value = io.BufferedReader(body)
owner = SimpleNamespace(peer=mock.Mock(), hostname='service.invalid', trust=mock.Mock())
owner.trust.wrap_socket.return_value = sock
supervisor = SimpleNamespace(owner=owner, cutoff=lambda: time.monotonic() + 1000)
report = {'attempts': {}, 'usage_unknown': False}
handler, timer = signal.getsignal(signal.SIGALRM), signal.getitimer(signal.ITIMER_REAL)
assert timer == (0.0, 0.0)
with mock.patch.object(inference, 'checkpoint'), \
     mock.patch.object(inference.socket, 'create_connection', return_value=sock):
    try:
        inference.exchange(supervisor, {}, 'authenticated', b'synthetic', time.monotonic() + .1, report)
    except inference.service.identity.worker.Abort as error:
        assert error.status == 'timeout'
    else:
        raise AssertionError('deadline not enforced')
assert report['usage_unknown'] and body.closed and body.chunks < 1048576
assert signal.getsignal(signal.SIGALRM) == handler
assert signal.getitimer(signal.ITIMER_REAL) == timer
assert sock.__exit__.call_count == 2
print('bounded-response-ok')
'''
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(Path(__file__).parent)],
            capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"bounded-response-ok\n")


if __name__ == "__main__":
    unittest.main()
