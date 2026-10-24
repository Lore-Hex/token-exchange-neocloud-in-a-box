# SPDX-License-Identifier: Apache-2.0
"""Closed synthetic streams; no listeners, Docker, GPU or credential use."""

from contextlib import contextmanager, ExitStack, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

import selected_service_conformance as conformance
import test_selected_service_hook as hook
import test_selected_service_inference as fixtures
import test_selected_service_image as image_tests
import provider_conformance as payload

inference, process, service = conformance.inference, conformance.process, conformance.service
encode = inference.encoded


def receipt(sha=hook.SHA, prior=None):
    prior = prior or hook.receipt(sha)
    return {"schema": conformance.SCHEMA, "result": "pass", "failure": "none",
        "contract_sha256": process.digest(encode(conformance.contract())), "prepared_sha256": sha,
        "inference_sha256": process.digest(encode(prior)), "attempts": dict.fromkeys(conformance.CASES, True),
        "checks": dict.fromkeys(conformance.CASES, True),
        "stream_checks": dict.fromkeys(conformance.STREAM_CHECKS, "pass"),
        "usage": {"prompt_tokens": 12, "completion_tokens": 2}, "usage_unknown": False,
        **dict.fromkeys(inference.CLAIMS, False)}


def owner_receipt(sha=hook.SHA):
    return {**hook.owner_receipt(sha), "schema": service.CONFORMANCE_SCHEMA,
            "mode": service.CONFORMANCE_MODE, "conformance": receipt(sha)}


class JoinTests(unittest.TestCase):
    setup_service = hook.HookTests.setup_service
    owned = hook.HookTests.owned

    def test_conformance_once_only_after_inference_pass_with_original_cutoff(self):
        with self.owned() as (supervisor, owner, clock, _, first):
            original = supervisor.contract
            owner.startup_seconds = 821
            with mock.patch.object(conformance, "run_once", return_value=receipt()) as second:
                result = supervisor.run(conformance_smoke=True)
            self.assertEqual(result, owner_receipt())
            second.assert_called_once_with(supervisor, hook.receipt())
            first.assert_called_once_with(supervisor)
            self.assertIs(supervisor.contract, original)
            self.assertEqual(clock.mono, 100 + 821 + 2)
            self.assertEqual(owner.events[-6:], [("drain", "tls"), ("reap", "tls"),
                ("drain", "guard"), ("reap", "guard"), ("reap", "engine"), ("reap-unregistered", None)])

    def test_failed_inference_prevents_conformance(self):
        with self.owned() as (supervisor, _, _, _, first):
            first.return_value.update(result="fail", failure="completion")
            with mock.patch.object(conformance, "run_once") as second:
                row = supervisor.run(conformance_smoke=True)
            self.assertEqual(row["status"], "failed")
            self.assertIsNone(row["conformance"])
            second.assert_not_called()

    def test_failures_late_cancel_and_cleanup_keep_known_usage(self):
        for fail in ("helper", "deadline", "cancelled", "cleanup", "exception"):
            with self.subTest(fail=fail), self.owned() as (supervisor, owner, clock, shutdown, _):
                def invoke(*_):
                    row = receipt()
                    if fail == "helper":
                        row.update(result="fail", failure="completion")
                    elif fail == "deadline":
                        clock.advance(supervisor.cutoff() - service.CLEANUP_RESERVE - clock.mono)
                    elif fail == "cancelled":
                        shutdown.stopped = True
                    elif fail == "cleanup":
                        owner.drain = mock.Mock(return_value=False)
                    else:
                        raise SystemExit("PRIVATE_CANARY")
                    return row
                with mock.patch.object(conformance, "run_once", side_effect=invoke):
                    row = supervisor.run(conformance_smoke=True)
                self.assertEqual(row["status"], "failed")
                self.assertNotIn("CANARY", json.dumps(row))
                self.assertEqual(row["inference"]["usage"], hook.receipt()["usage"])
                if fail != "exception":
                    self.assertEqual(row["conformance"]["usage"], receipt()["usage"])
                self.assertLessEqual(clock.mono, supervisor.contract.hard_monotonic)


class ReceiptTests(unittest.TestCase):
    def test_closed_shape_prior_hash_outcomes_usage_and_no_claim_upgrades(self):
        good = receipt()
        self.assertEqual(conformance.validate_report(encode(good), hook.SHA, hook.receipt()), good)
        changes = [(name, "PRIVATE_CANARY") for name in ("schema", "failure", "contract_sha256",
            "prepared_sha256", "inference_sha256")]
        changes += [(name, True) for name in inference.CLAIMS]
        changes += [("extra", "PRIVATE_CANARY"), ("checks", dict.fromkeys(conformance.CASES, False)),
            ("usage_unknown", True), ("usage", None), ("stream_checks", None),
            ("stream_checks", dict.fromkeys(conformance.STREAM_CHECKS, "warn"))]
        for name, value in changes:
            with self.subTest(name=name), self.assertRaises(ValueError):
                conformance.validate_report(encode({**good, name: value}), hook.SHA, hook.receipt())
        for key in good["usage"]:
            for value in (True, None, -1, 0, 100000, 1.5):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    conformance.validate_report(encode({**good, "usage": {**good["usage"], key: value}}),
                                                hook.SHA, hook.receipt())
        prior = hook.receipt()
        prior["usage"]["completion_tokens"] -= 1
        with self.assertRaises(ValueError):
            conformance.validate_report(encode(good), hook.SHA, prior)

    def test_quiet_checker_restores_both_fds_on_base_exception(self):
        code = """
import os, sys
sys.path.insert(0, sys.argv[1])
from selected_service_conformance import quiet_checker
try:
    with quiet_checker():
        print('PRIVATE_PYTHON_CANARY')
        os.write(1, b'PRIVATE_STDOUT_CANARY')
        os.write(2, b'PRIVATE_STDERR_CANARY')
        raise SystemExit('PRIVATE_EXCEPTION_CANARY')
except SystemExit:
    pass
os.write(1, b'restored-out')
os.write(2, b'restored-err')
"""
        row = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code,
            str(Path(__file__).absolute().parent)], capture_output=True, timeout=15, check=True)
        self.assertEqual((row.stdout, row.stderr), (b"restored-out", b"restored-err"))

    def test_missing_checker_fails_prepared_stage_before_owner_or_inputs(self):
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(service.Contract, "begin"))
            stack.enter_context(mock.patch.object(process, "Prepared", return_value=SimpleNamespace(files={})))
            inputs = stack.enter_context(mock.patch.object(service, "snapshot_inputs"))
            owner = stack.enter_context(mock.patch.object(process, "ProcessOwner"))
            output = io.StringIO()
            stack.enter_context(redirect_stdout(output))
            result = process.main(["--prepared-sha256", hook.SHA, "--provider-config", "/PRIVATE_CANARY",
                "--api-key-file", "/PRIVATE_CANARY", "--cert", "/PRIVATE_CANARY", "--key", "/PRIVATE_CANARY",
                "--hostname", "private.invalid", "--input-price", "1", "--output-price", "2",
                "--hard-stop-at", "unused", "--conformance-smoke"])
        self.assertEqual(result, 1)
        inputs.assert_not_called()
        owner.assert_not_called()
        row = hook.smoke.lifecycle_failure(output.getvalue().encode(), conformance_smoke=True,
                                           prepared_sha256=hook.SHA)
        self.assertEqual(row["preparation_stage"], "prepared")
        self.assertEqual(row["preparation_category"], "contract")
        self.assertNotIn("CANARY", output.getvalue())

    def test_host_mode_is_closed_and_nonzero_failure_retains_both_accountings(self):
        row = owner_receipt()
        join = {"conformance_smoke": True, "prepared_sha256": hook.SHA}
        self.assertEqual(hook.smoke.lifecycle(encode(row), **join), row)
        for mode in ({}, {"inference_smoke": True, "prepared_sha256": hook.SHA}):
            with self.assertRaises(ValueError):
                hook.smoke.lifecycle(encode(row), **mode)
        for value in (hook.owner_receipt(), {**row, "conformance": None}, {**row, "extra": "PRIVATE_CANARY"},
                      {**row, "conformance": receipt(process.digest(b"other"))}):
            with self.assertRaises(ValueError):
                hook.smoke.lifecycle(encode(value), **join)
        row.update(status="failed", reason="owner-failed")
        row["conformance"].update(result="fail", failure="completion")
        self.assertEqual(hook.smoke.lifecycle_failure(encode(row), **join), row)

    def test_real_host_failure_join_and_inspect_mode_are_metadata_only(self):
        hook.smoke_tests.SmokeTests.setUpClass()
        fixture = hook.smoke_tests.SmokeTests()
        fixture.setUp()
        fixture.args.conformance_smoke = True
        row = owner_receipt(fixture.build["prepared_sha256"])
        for failure in (None, "cleanup"):
            with fixture.running(failure=failure, output=encode(row)) as (contract, result):
                fixture.run_owner(contract, result)
                self.assertEqual(result["result"], "pass" if failure is None else "fail")
                self.assertEqual(result["lifecycle"]["conformance"]["usage"], receipt()["usage"])
            command = next(cmd for cmd in fixture.commands if cmd[2] == "create")
            self.assertEqual(command[-1], "--conformance-smoke")
            self.assertNotIn("--publish", command)
            self.assertEqual(command[command.index("--network") + 1], "none")
        fixture.args.run = False
        with fixture.admission() as (_, run):
            row = hook.smoke.preflight(fixture.args)
            self.assertEqual(row["mode"], service.CONFORMANCE_MODE)
            run.assert_not_called()


class PackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        image_tests.ImageTests.setUpClass()

    def payload(self):
        root = Path(__file__).resolve().parents[1]
        files = {target: (root / source).read_bytes() for target, source in (
            ("support.py", "scripts/tau_trial_worker.py"), ("worker.py", "scripts/provider_conformance_worker.py"),
            ("model-profile.py", "scripts/deepseek_model_profile.py"))}
        files["packages/synthetic.py"] = b"# synthetic non-executed dependency\n"
        plan = {"schema": "nib.private-provider-payload/v1", "requirements_sha256": payload.LOCK_SHA,
            "checker_wheel_sha256": payload.CHECKER_SHA, "model_profile": service.models.select("uncensored"),
            "files": {name: {"bytes": len(raw), "sha256": process.digest(raw)} for name, raw in files.items()}}
        files["trial.json"] = encode(plan)
        return files

    def test_optional_preparation_uses_existing_payload_builder_and_binds_every_file(self):
        image = image_tests.image
        with image_tests.ImageTests().preparation() as (root, bundle, trcs, original, _, _):
            files = self.payload()
            with mock.patch.object(payload, "prepare", return_value=files) as prepare:
                destination = root / "prepared"
                result = image.prepare(bundle, hook.SHA, trcs, destination, checker_wheelhouse=root / "wheels")
            self.assertEqual(prepare.call_args.args[0], root / "wheels")
            self.assertEqual(prepare.call_args.kwargs, {"model_profile": "uncensored"})
            image.validated_files(destination, result["receipt_sha256"])
            service_root = destination / "context/service"
            prepared = json.loads((service_root / "prepared.json").read_bytes())
            for name, raw in files.items():
                self.assertEqual((service_root / "checker" / name).read_bytes(), raw)
                self.assertEqual(prepared["files"]["checker/" + name], process.digest(raw))
            for name, raw in original.items():
                self.assertEqual((bundle / "payload" / name).read_bytes(), raw)
            target = service_root / "checker/packages/synthetic.py"
            target.write_bytes(b"# changed\n")
            with self.assertRaises(ValueError):
                process.source_files(service_root, conformance.time.monotonic() + 5)

    def test_optional_payload_inventory_rejects_links_extras_and_wrong_model(self):
        image = image_tests.image
        for change in ("link", "extra", "model"):
            with self.subTest(change=change), image_tests.ImageTests().preparation() as state:
                root, _, _, _, _, _ = state
                files = self.payload()
                if change == "model":
                    plan = json.loads(files["trial.json"])
                    plan["model_profile"]["served_model_name"] = "other"
                    files["trial.json"] = encode(plan)
                payload.publish(root / "checker", files)
                directory = root / "checker/payload"
                if change != "model":
                    directory.chmod(0o700)
                    path = directory / "extra.py"
                    if change == "extra":
                        path.write_bytes(b"# unexpected\n")
                    else:
                        path.symlink_to(directory / "worker.py")
                with self.assertRaises(ValueError):
                    conformance.payload_files(directory, conformance.time.monotonic() + 5)


def catalog(supervisor):
    # Reuse the committed Qwen wire fixture, then apply only the selected identity/limits.
    source = (Path(__file__).resolve().parents[1] / "internal/pilotapi/compatibility_test.go").read_text()
    row = json.loads(source.split("const qwenCatalog = `", 1)[1].split("`", 1)[0])
    model = row["data"][0]
    model.update(id=service.MODEL, owned_by="dealignai", context_length=service.CONTEXT,
                 name="Selected synthetic test")
    model["lifecycle"]["retirement_at"] = supervisor.contract.admission_stop_at
    return row


def stream(*, usage=True, done=True):
    prefix = {"model": service.MODEL, "object": "chat.completion.chunk"}
    rows = [{**prefix, "choices": [{"index": 0, "delta": {"content": fixtures.CANARY}, "finish_reason": None}]},
            {**prefix, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
    if usage:
        rows.append({**prefix, "choices": [], "usage": {
            "prompt_tokens": 12, "completion_tokens": 2, "total_tokens": 14}})
    return [b"data: " + encode(row) + b"\n\n" for row in rows] + ([b"data: [DONE]\n\n"] if done else [])


@unittest.skipUnless(importlib.util.find_spec("tr_provider_check"), "run with hash-locked checker test environment")
class PinnedCheckerTests(unittest.TestCase):
    @contextmanager
    def fixture(self, *, chunks=None, catalog_change=None):
        from tr_provider_check import contract as rules, http as client
        from tr_provider_check.checks import catalog as checker_catalog, streaming
        import httpx
        checker = SimpleNamespace(httpx=httpx, rules=rules, client=client, catalog=checker_catalog, streaming=streaming)
        with fixtures.InferenceTests().fixture() as state, ExitStack() as stack:
            state.supervisor._selected_inference_used = True
            state.owner.prepared.receipt_sha256 = state.sha
            state.owner.prepared.files["checker/trial.json"] = process.digest(b"synthetic pinned payload")
            state.owner.prepared.files["scripts/selected_service_conformance.py"] = process.digest(
                Path(conformance.__file__).read_bytes())
            original = state.read.side_effect
            state.read.side_effect = lambda name: Path(name).read_bytes() if name == conformance.__file__ else original(name)
            row = catalog(state.supervisor)
            if catalog_change:
                catalog_change(row)
            provider = {key: value for key, value in row["provider"].items()
                        if key not in ("error_contract", "request_id_header")}
            state.key.side_effect = lambda path, _: encode(provider) if path.name == "provider.json" else fixtures.KEY
            stack.enter_context(mock.patch.object(conformance, "time", state.clock))
            loader = stack.enter_context(mock.patch.object(conformance, "checker_modules"))
            loader.return_value.__enter__.return_value = checker
            # Any accidental use of the checker's separate HTTP transport is a failure.
            stack.enter_context(mock.patch.object(httpx, "AsyncClient", side_effect=AssertionError("NETWORK_CANARY")))
            chunks = stream() if chunks is None else chunks
            bodies = [fixtures.DENIED, fixtures.DENIED, encode(row),
                      b"".join(format(len(part), "x").encode() + b"\r\n" + part + b"\r\n" for part in chunks)
                      + b"0\r\n\r\n"]
            sockets, connections = [], []
            for index, body in enumerate(bodies):
                sockets.append(fixtures.resource())
                connection = fixtures.resource()
                if index == 3:
                    headers = b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n"
                else:
                    headers = ("HTTP/1.1 " + ("401 Unauthorized" if index < 2 else "200 OK")
                        + "\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)) + "\r\n").encode()
                wire = io.BytesIO(headers + b"\r\n" + body)
                connection.makefile.return_value = wire
                connections.append(connection)
            state.connect.side_effect = sockets
            state.owner.trust.wrap_socket.side_effect = connections
            # Deterministic monotonic arrival times for the unchanged incremental check.
            original_read = conformance.transport.StreamResponse.read1
            def read(response, amount):
                value = original_read(response, amount)
                state.clock.advance(.01)
                return value
            stack.enter_context(mock.patch.object(conformance.transport.StreamResponse, "read1", read))
            state.connections, state.sockets = connections, sockets
            state.prior = hook.receipt(state.sha)
            yield state

    def run_helper(self, state, *, failure=None):
        row = conformance.run_once(state.supervisor, state.prior)
        self.assertEqual(row["result"], "pass" if failure is None else "fail", row)
        if failure is not None:
            self.assertEqual(row["failure"], failure)
        self.assertEqual(conformance.validate_report(encode(row), state.sha, state.prior), row)
        for private in (fixtures.CANARY.encode(), fixtures.KEY, b"service.invalid", b"Authorization", b"private.invalid"):
            self.assertNotIn(private, encode(row))
        return row

    def test_actual_pinned_catalog_and_seven_stream_checks_using_owned_mock_wire(self):
        with self.fixture() as state:
            row = self.run_helper(state)
            self.assertEqual(row, receipt(state.sha, state.prior))
            self.assertEqual(state.connect.call_count, 4)
            self.assertEqual(state.owner.peer.call_count, 4)
            bodies = [connection.sendall.call_args.args[0] for connection in state.connections]
            self.assertNotIn(b"Authorization", bodies[0])
            self.assertNotIn(b"Authorization", bodies[2])
            self.assertTrue(bodies[2].startswith(b"GET /v1/models HTTP/1.1\r\n"))
            self.assertIn(b"Bearer " + fixtures.KEY, bodies[3])
            body = json.loads(bodies[3].split(b"\r\n\r\n", 1)[1])
            self.assertEqual(body["model"], service.MODEL)
            self.assertEqual(body["max_tokens"], service.OUTPUT)
            self.assertEqual(body["stream_options"], {"include_usage": True})
            self.assertFalse(any(row[key] for key in inference.CLAIMS))

    def test_missing_usage_done_and_late_malformed_data_never_pass(self):
        for chunks, known in ((stream(usage=False), False), (stream(done=False), True),
                              (stream() + [b"data: PRIVATE_CANARY\n\n"], True)):
            with self.subTest(known=known), self.fixture(chunks=chunks) as state:
                row = self.run_helper(state, failure="protocol")
                self.assertEqual(row["usage_unknown"], not known)
                self.assertEqual(row["usage"], receipt()["usage"] if known else None)

    def test_catalog_schema_canonical_identity_limits_and_privacy_fail_closed(self):
        changes = [lambda row: row.update(extra="PRIVATE_CANARY"),
            lambda row: row["data"][0].update(id="dealignai/other"),
            lambda row: row["data"][0].update(context_length=4096),
            lambda row: row["data"][0]["capabilities"].update(tools=True),
            lambda row: row["data"][0]["lifecycle"].update(retirement_at=None),
            lambda row: row["data"][0]["pricing"].update(input="999")]
        for change in changes:
            with self.subTest(change=changes.index(change)), self.fixture(catalog_change=change) as state:
                row = self.run_helper(state, failure="protocol")
                self.assertFalse(row["attempts"]["stream"])

    def test_peer_failure_sends_no_credentials_and_unknown_usage_remains_unknown(self):
        with self.fixture() as state:
            state.owner.peer.side_effect = service.Rejected("identity")
            row = self.run_helper(state, failure="identity")
            self.assertFalse(any(row["attempts"].values()))
            for connection in state.connections:
                connection.sendall.assert_not_called()

    def test_cancellation_deadline_size_and_double_use_are_fail_closed(self):
        for case in ("cancelled", "deadline", "size", "double"):
            with self.subTest(case=case), self.fixture() as state:
                if case == "cancelled":
                    state.connections[3].sendall.side_effect = lambda _: state.handlers[inference.signal.SIGTERM](0, None)
                    row = self.run_helper(state, failure="cancelled")
                    self.assertTrue(state.supervisor.shutdown.is_set())
                    self.assertTrue(row["usage_unknown"])
                elif case == "deadline":
                    def elapsed(_):
                        state.clock.advance(conformance.SECONDS)
                    state.connections[3].sendall.side_effect = elapsed
                    row = self.run_helper(state, failure="deadline")
                elif case == "size":
                    with mock.patch.object(conformance, "LIMIT", 1):
                        row = self.run_helper(state, failure="protocol")
                else:
                    self.run_helper(state)
                    count = state.connect.call_count
                    row = conformance.run_once(state.supervisor, state.prior)
                    self.assertEqual(row["failure"], "already-used")
                    self.assertEqual(count, state.connect.call_count)
                self.assertLessEqual(state.connect.call_count, 4)

    def test_known_usage_survives_late_identity_and_socket_close_failure(self):
        for case in ("identity", "close"):
            with self.subTest(case=case), self.fixture() as state:
                if case == "close":
                    state.connections[3].__exit__.side_effect = OSError("PRIVATE_CANARY")
                    expected = "transport"
                else:
                    original = conformance.transport.StreamResponse.read1
                    def read(response, amount):
                        raw = original(response, amount)
                        if b'"usage"' in raw:
                            state.owner.bound.side_effect = service.Rejected("identity")
                        return raw
                    expected = "identity"
                    state.read_patch = mock.patch.object(conformance.transport.StreamResponse, "read1", read)
                with ExitStack() as stack:
                    if case == "identity":
                        stack.enter_context(state.read_patch)
                    row = self.run_helper(state, failure=expected)
                self.assertEqual(row["usage"], receipt()["usage"])
                self.assertFalse(row["usage_unknown"])


if __name__ == "__main__":
    unittest.main()
