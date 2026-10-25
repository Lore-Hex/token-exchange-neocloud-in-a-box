# SPDX-License-Identifier: Apache-2.0
"""Pinned assertions over synthetic sockets only; no public or model requests."""

from contextlib import ExitStack, contextmanager, nullcontext, redirect_stdout
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import ssl
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import test_selected_external_smoke as wire
import test_selected_service_conformance as fixtures
import test_selected_service_inference as inference_tests

SPEC = importlib.util.spec_from_file_location("external_checker", Path(__file__).with_name("check-selected-service.py"))
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)
REAL_TRUST_INPUT = check.probe.trust_input
CANARY = "PRIVATE_PROVIDER_CONTENT_CANARY"


def admission():
    wall = datetime.now(timezone.utc)
    stop = (wall + timedelta(seconds=600)).strftime("%Y-%m-%dT%H:%M:%SZ")
    catalog = fixtures.catalog(SimpleNamespace(contract=SimpleNamespace(admission_stop_at=stop)))
    catalog["provider"]["id"] = "private-test-provider"
    return {"schema": check.INPUT, "runner_git_sha": "1" * 40, "service_git_sha": "2" * 40,
        "preparation_sha256": "3" * 64, "checker_payload_sha256": "4" * 64,
        "target": {"address": "8.8.8.8", "hostname": "private-test.example", "port": 8443},
        "catalog": catalog, "deadline": (wall + timedelta(seconds=470)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "admission_stop_at": stop}


def json_response(payload, status=200):
    body = check.encoded(payload)
    return ("HTTP/1.1 " + str(status) + " Synthetic\r\nContent-Type: application/json\r\nContent-Length: "
        + str(len(body)) + "\r\nConnection: close\r\n\r\n").encode() + body


class BoundaryTests(unittest.TestCase):
    def test_checker_payload_digest_precedes_loading_and_binds_complete_inventory(self):
        files = fixtures.PackagingTests().payload()
        expected = check.digest(files["trial.json"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "checker"
            fixtures.payload.publish(root, files)
            root /= "payload"
            self.assertEqual(check.subset.payload_files(root, check.time.monotonic() + 5, expected), set(files))
            with mock.patch.object(check.subset.pinned, "verify") as verify, self.assertRaises(check.inference.Rejected):
                check.subset.payload_files(root, check.time.monotonic() + 5, "0" * 64)
            verify.assert_not_called()
            target = root / "packages/synthetic.py"
            target.chmod(0o600)
            target.write_bytes(b"# unauthenticated package\n")
            with self.assertRaises(ValueError):
                check.subset.payload_files(root, check.time.monotonic() + 5, expected)

    def test_closed_admission_rejects_target_source_deadline_and_profile_drift(self):
        value = admission()
        check.admit(value)
        for change in (lambda v: v.update(extra=True), lambda v: v.update(schema="unknown"),
            lambda v: v.update(runner_git_sha="bad"), lambda v: v.update(service_git_sha="bad"),
            lambda v: v.update(checker_payload_sha256="bad"), lambda v: v.update(preparation_sha256="bad"),
            lambda v: v["target"].update(url="https://unexpected.invalid"),
            lambda v: v["target"].update(address="127.0.0.1"), lambda v: v["target"].update(address="10.0.0.1"),
            lambda v: v["target"].update(hostname="bad\r\nAuthorization: bad"),
            lambda v: v.update(deadline=v["admission_stop_at"]),
            lambda v: v.update(deadline="2000-01-01T00:00:00Z"),
            lambda v: v.update(admission_stop_at="2000-01-01T00:00:00Z")):
            bad = copy.deepcopy(value)
            change(bad)
            with self.subTest(change=change), self.assertRaises((ValueError, check.inference.Rejected)):
                check.admit(bad)

    def test_invalid_cli_and_exception_text_never_escape(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(check.main(["--raw-private-" + CANARY]), 1)
        self.assertNotIn(CANARY, output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["result"], "fail")

    def test_loaded_source_closure_extends_existing_probe_binding(self):
        with mock.patch.object(check.probe, "source_identity", return_value=("1" * 40, {})) as source:
            check.source_identity(check.time.monotonic() + 5)
        names = source.call_args.kwargs["extra_files"]
        self.assertIn("scripts/check-selected-service.py", names)
        self.assertIn("scripts/selected_service_protocol.py", names)
        self.assertIn("scripts/selected_service_conformance.py", names)
        self.assertIn("scripts/provider_conformance_worker.py", names)


@unittest.skipUnless(importlib.util.find_spec("tr_provider_check"), "run with hash-locked checker test environment")
class ExternalCheckerTests(unittest.TestCase):
    @contextmanager
    def fixture(self, *, mutate_input=None, mutate_wire=None, fail_tls=None, fail_send=None, fail_close=None,
                warning=False, stream=None, key_hook=None):
        from tr_provider_check import contract as rules, http as client
        from tr_provider_check.checks import callability, catalog, chat, streaming
        import httpx
        checker = SimpleNamespace(httpx=httpx, rules=rules, client=client, catalog=catalog, chat=chat,
                                  callability=callability, streaming=streaming)
        value = admission()
        served = copy.deepcopy(value["catalog"])
        if mutate_input:
            mutate_input(value)
        raw = check.encoded(value)
        args = SimpleNamespace(run=True, input="/private/input.json", input_sha256=check.digest(raw),
            checker_payload="/private/checker", api_key_file="/private/key")
        wires = {"health": b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n",
            "missing-key": json_response({"error": {"message": "invalid_api_key", "type": "invalid_api_key"}}, 401),
            "wrong-key": json_response({"error": {"message": "invalid_api_key", "type": "invalid_api_key"}}, 401),
            "catalog": json_response(served)}
        for case in check.protocol.JSON_CASES:
            row, status = inference_tests.completion(), 200
            row["choices"][0]["message"]["content"] = "PONG"
            row["usage"].update(prompt_tokens=12, completion_tokens=2, total_tokens=14)
            if warning and case in ("thinking", "reasoning", "reasoning-effort"):
                row, status = {"error": {"message": CANARY, "type": "invalid_request"}}, 400
            wires[case] = json_response(row, status)
        chunks = fixtures.stream() if stream is None else stream
        wires["stream"] = b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n" \
            + b"".join(format(len(chunk), "x").encode() + b"\r\n" + chunk + b"\r\n" for chunk in chunks) + b"0\r\n\r\n"
        if mutate_wire:
            mutate_wire(wires)
        state = SimpleNamespace(args=args, value=value, connections={}, events=[], checker=checker, deadlines=[])
        @contextmanager
        def tls(target, trust, peer, deadline):
            case = check.CASES[len(state.connections)]
            self.assertEqual(vars(target), {**value["target"], "tls_mode": "system-roots",
                                          "certificate": None, "certificate_sha256": None})
            self.assertIsNone(peer)
            state.deadlines.append(deadline)
            if case == fail_tls:
                raise ssl.SSLCertVerificationError(CANARY)
            connection = wire.Socket(wires[case])
            connection.closed = False
            if case == fail_send:
                connection.sendall = mock.Mock(side_effect=OSError(CANARY))
            state.connections[case] = connection
            state.events.append(("tls", case))
            try:
                yield connection
            finally:
                connection.closed = True
                if case == fail_close:
                    raise OSError(CANARY)
        def read(path, deadline):
            if str(path) == args.input:
                return raw
            self.assertEqual(str(path), args.api_key_file)
            case = next(reversed(state.connections))
            self.assertIn(("tls", case), state.events)
            self.assertIn(case, check.GENERATIONS)
            state.events.append(("key", case))
            if key_hook:
                key_hook(state)
            return wire.KEY
        with ExitStack() as stack:
            base, offset = check.time.monotonic(), [0.0]
            clock = SimpleNamespace(monotonic=lambda: base + offset[0], time=check.time.time)
            state.advance = lambda seconds: offset.__setitem__(0, offset[0] + seconds)
            stack.enter_context(mock.patch.object(check, "time", clock))
            stack.enter_context(mock.patch.object(check.subset, "time", clock))
            original_read = check.probe.transport.StreamResponse.read1
            def arrival(response, amount):
                data = original_read(response, amount)
                offset[0] += .01
                return data
            stack.enter_context(mock.patch.object(check.probe.transport.StreamResponse, "read1", arrival))
            state.read = stack.enter_context(mock.patch.object(check.service.identity, "private_read", side_effect=read))
            state.source = stack.enter_context(mock.patch.object(check, "source_identity",
                return_value=("1" * 40, {"scripts/check-selected-service.py": "5" * 64})))
            state.trust = stack.enter_context(mock.patch.object(check.probe, "trust_input", return_value=(object(), None)))
            state.tls = stack.enter_context(mock.patch.object(check.probe, "pinned_tls", side_effect=tls))
            state.loader = stack.enter_context(mock.patch.object(check.subset, "checker_modules"))
            state.loader.return_value.__enter__.return_value = checker
            stack.enter_context(mock.patch.object(check.subset, "quiet_checker", return_value=nullcontext()))
            stack.enter_context(mock.patch.object(httpx, "AsyncClient", side_effect=AssertionError("UNEXPECTED_NETWORK")))
            yield state

    def result(self, state):
        row = check.run(state.args)
        raw = check.encoded(row)
        for secret in (CANARY, "private-test.example", "private-test-provider", "8.8.8.8",
                       "/private/key", wire.KEY.decode(), inference_tests.CANARY):
            self.assertNotIn(secret.encode(), raw)
        self.assertTrue(all(connection.closed for connection in state.connections.values()))
        self.assertFalse(any(row[name] for name in ("ownership_verified", "runtime_identity_verified",
            "continued_readiness", "auth_upstream_calls_measured", "quality_eligible", "deployment_eligible",
            "confidential", "e2ee")))
        self.assertLessEqual(sum(row["attempts"].values()), 13)
        return row

    def test_actual_pinned_seventeen_assertions_use_thirteen_single_exchanges(self):
        with self.fixture() as state:
            row = self.result(state)
            self.assertEqual((row["result"], row["failure"]), ("pass", "none"), row)
            self.assertEqual(row["checks"], dict.fromkeys(check.subset.pinned.MANDATORY, "pass"))
            self.assertEqual(row["declared_v2_upstream_status"], "skip")
            self.assertEqual(list(state.connections), list(check.CASES))
            self.assertEqual(len([item for item in state.events if item[0] == "key"]), 9)
            state.loader.assert_called_once_with(Path(state.args.checker_payload), mock.ANY, "4" * 64)
            for case, connection in state.connections.items():
                self.assertEqual(len(connection.sent), 1)
                self.assertEqual(wire.KEY in connection.sent[0], case in check.GENERATIONS)
            bodies = [json.loads(state.connections[case].sent[0].split(b"\r\n\r\n")[1]) for case in check.protocol.JSON_CASES]
            self.assertEqual(bodies[0]["max_tokens"], 4)
            self.assertEqual(bodies[-1]["max_tokens"], 16)
            self.assertTrue(all("max_tokens" not in body for body in bodies[1:-1]))
            self.assertTrue(all(value == {"usage": {"prompt_tokens": 12, "completion_tokens": 2},
                "usage_unknown": False} for value in row["usage"].values()))

    def test_checker_warn_and_fail_are_never_promoted(self):
        with self.fixture(warning=True) as state:
            row = self.result(state)
            self.assertEqual((row["result"], row["checker_result"]), ("fail", "warn"))
            self.assertEqual(row["checks"]["chat.forwarded-fields"], "warn")
            self.assertTrue(row["usage"]["reasoning"]["usage_unknown"])
            self.assertTrue(row["attempts"]["stream"])
        with self.fixture(mutate_wire=lambda w: w.update(pong=w["pong"].replace(b"PONG", b"NOPE"))) as state:
            row = self.result(state)
            self.assertEqual(row["checker_result"], "fail")
            self.assertEqual(row["checks"]["chat.pong"], "fail")

    def test_tls_identity_and_health_fail_before_credential_read(self):
        for case in ("health", "missing-key", "wrong-key", "catalog", "callability"):
            with self.subTest(case=case), self.fixture(fail_tls=case) as state:
                row = self.result(state)
                self.assertEqual(row["result"], "fail")
                self.assertFalse(any(event[0] == "key" for event in state.events))
                self.assertFalse(row["attempts"]["callability"])
        for bad in (b"HTTP/1.1 302 Found\r\nContent-Length: 0\r\n\r\n",
                    b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\nEXTRA"):
            with self.fixture(mutate_wire=lambda w: w.update(health=bad)) as state:
                self.assertEqual(self.result(state)["result"], "fail")
                self.assertEqual(list(state.connections), ["health"])

    def test_expected_provider_catalog_source_and_hash_mismatch_stop_before_inference(self):
        for change in (lambda v: v["catalog"]["provider"].update(id="another-private-provider"),
                       lambda v: v["catalog"]["data"][0].update(context_length=4096),
                       lambda v: v["catalog"]["data"][0]["lifecycle"].update(retirement_at=None),
                       lambda v: v.update(runner_git_sha="9" * 40)):
            with self.fixture(mutate_input=change) as state:
                row = self.result(state)
                self.assertEqual(row["result"], "fail")
                self.assertFalse(any(event[0] == "key" for event in state.events))
        with self.fixture() as state:
            state.args.input_sha256 = "0" * 64
            self.assertEqual(self.result(state)["result"], "fail")
            state.tls.assert_not_called()

    def test_trust_override_or_nonabsolute_payload_cannot_reach_network_or_key(self):
        for path in ("checker", "/private/../checker"):
            with self.fixture() as state:
                state.args.checker_payload = path
                self.assertEqual(self.result(state)["result"], "fail")
                state.loader.assert_not_called()
                state.tls.assert_not_called()
        for name in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
            with self.fixture() as state, mock.patch.dict(check.probe.os.environ, {name: ""}):
                state.trust.side_effect = REAL_TRUST_INPUT
                self.assertEqual(self.result(state)["result"], "fail")
                state.loader.assert_not_called()
                state.tls.assert_not_called()

    def test_backward_clock_jump_cannot_extend_original_monotonic_budget(self):
        real = check.time.time()
        with self.fixture() as state, mock.patch.object(check.time, "time", return_value=real) as clock:
            original = state.read.side_effect
            def read(path, deadline):
                value = original(path, deadline)
                if str(path) == state.args.api_key_file:
                    clock.return_value = real - 3600
                    state.advance(check.SECONDS + 1)
                return value
            state.read.side_effect = read
            row = self.result(state)
            self.assertEqual(row["failure"], "deadline")
            self.assertFalse(row["attempts"]["callability"])

    def test_unknown_usage_and_late_json_framing_failure_are_preserved(self):
        for kwargs, known in (({"fail_send": "callability"}, False),
                ({"mutate_wire": lambda w: w.update(callability=w["callability"] + b"PRIVATE_TRAILER")}, True),
                ({"fail_close": "callability"}, True)):
            with self.fixture(**kwargs) as state:
                row = self.result(state)
                self.assertEqual(row["result"], "fail")
                self.assertEqual(row["usage"]["callability"]["usage_unknown"], not known)
                self.assertEqual(row["usage"]["callability"]["usage"] is not None, known)
                self.assertEqual(list(state.connections)[-1], "callability")

    def test_stream_usage_done_eof_and_close_are_required(self):
        for chunks, known in ((fixtures.stream(usage=False), False), (fixtures.stream(done=False), True),
                             (fixtures.stream() + [b"data: MALFORMED\n\n"], True)):
            with self.fixture(stream=chunks) as state:
                row = self.result(state)
                self.assertEqual(row["result"], "fail")
                self.assertEqual(row["usage"]["stream"]["usage_unknown"], not known)
                self.assertIsNotNone(row["usage"]["pong"]["usage"])
        with self.fixture(fail_close="stream") as state:
            row = self.result(state)
            self.assertEqual(row["result"], "fail")
            self.assertFalse(row["client_connections_closed"])
            self.assertIsNotNone(row["usage"]["stream"]["usage"])

    def test_redirect_cannot_retry_or_send_to_another_target(self):
        redirect = b"HTTP/1.1 307 Redirect\r\nContent-Length: 0\r\nLocation: https://private.invalid\r\n\r\n"
        with self.fixture(mutate_wire=lambda w: w.update(callability=redirect)) as state:
            row = self.result(state)
            self.assertEqual(row["result"], "fail")
            self.assertEqual(list(state.connections)[-1], "callability")
            self.assertTrue(row["usage"]["callability"]["usage_unknown"])

    def test_forward_jump_and_cancellation_fail_without_more_dispatch(self):
        for error in (KeyboardInterrupt(CANARY), check.service.identity.worker.Abort("timeout")):
            with self.fixture(key_hook=lambda _: (_ for _ in ()).throw(error)) as state:
                row = self.result(state)
                self.assertIn(row["failure"], ("deadline", "cancelled"))
                self.assertFalse(row["attempts"]["callability"])
        real = check.time.time()
        with self.fixture() as state, \
                mock.patch.object(check.time, "time", return_value=real) as clock:
            def advance(_):
                clock.return_value = real + 3600
            # The read completes, then the deadline is rechecked before any send.
            original = state.read.side_effect
            def read(path, deadline):
                value = original(path, deadline)
                if str(path) == state.args.api_key_file:
                    advance(None)
                return value
            state.read.side_effect = read
            row = self.result(state)
            self.assertEqual(row["failure"], "deadline")
            self.assertFalse(row["attempts"]["callability"])

    def test_repeated_case_is_rejected_without_an_additional_connection(self):
        with self.fixture() as state:
            target, _ = check.admit(state.value)
            row = check.empty_result()
            client = check.Client(state.checker, target, object(), state.value, state.args,
                lambda: check.time.monotonic() + 5, row)
            with client.response("health"):
                pass
            with self.assertRaises(check.Boundary), client.response("health"):
                pass
            self.assertEqual(len(state.connections), 1)


if __name__ == "__main__":
    unittest.main()
