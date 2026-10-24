# SPDX-License-Identifier: Apache-2.0
"""Opt-in owner/host joins with synthetic processes and transports; no ports."""

from contextlib import ExitStack, contextmanager, redirect_stdout
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

import selected_service_inference as inference
import test_selected_service as lifecycle_tests
import test_selected_service_inference as inference_tests
import test_selected_service_process as process_tests
import test_selected_service_smoke as smoke_tests

process, service = inference.process, inference.service
smoke, lifecycle = smoke_tests.smoke, smoke_tests.lifecycle
SHA = process.digest(b"synthetic prepared receipt")


def receipt(sha=SHA):
    return {"schema": inference.SCHEMA, "result": "pass", "failure": "none",
        "contract_sha256": process.digest(inference.encoded(inference.contract())), "prepared_sha256": sha,
        "attempts": dict.fromkeys(inference.CASES, True), "checks": dict.fromkeys(inference.CASES, True),
        "usage": {"prompt_tokens": 12, "completion_tokens": 256}, "usage_unknown": False,
        "auth_upstream_calls_measured": False, **dict.fromkeys(inference.CLAIMS, False)}


def owner_receipt(sha=SHA):
    return {**lifecycle(), "schema": service.INFERENCE_SCHEMA, "mode": service.INFERENCE_MODE,
            "inference": receipt(sha)}


class HookTests(unittest.TestCase):
    setup_service = lifecycle_tests.LifecycleTests.setup_service

    @contextmanager
    def owned(self):
        with self.setup_service() as state:
            state[1].prepared = SimpleNamespace(receipt_sha256=SHA)
            with mock.patch.object(inference, "run_once", return_value=receipt()) as call:
                yield (*state, call)

    def test_default_receipt_and_retirement_unchanged(self):
        with self.owned() as (supervisor, _, clock, _, call):
            result = supervisor.run()
            self.assertEqual(result, lifecycle())
            call.assert_not_called()
            self.assertGreaterEqual(clock.mono, supervisor.contract.hard_monotonic - service.CLEANUP_RESERVE)

    def test_once_after_all_ready_then_immediate_front_first_cleanup(self):
        with self.owned() as (supervisor, owner, clock, _, call):
            owner.startup_seconds = 821
            original = supervisor.contract
            def invoke(actual):
                self.assertIs(actual, supervisor)
                self.assertEqual(set(actual.children), set(service.ROLES))
                self.assertIsNotNone(actual.engine_identity)
                self.assertFalse(any(event[0] == "drain" for event in owner.events))
                return receipt()
            call.side_effect = invoke
            result = supervisor.run(inference_smoke=True)
            self.assertEqual(result, owner_receipt())
            call.assert_called_once_with(supervisor)
            self.assertIs(supervisor.contract, original)
            self.assertEqual(clock.mono, 100 + 821 + 2)
            self.assertEqual(owner.events[-6:], [("drain", "tls"), ("reap", "tls"),
                ("drain", "guard"), ("reap", "guard"), ("reap", "engine"), ("reap-unregistered", None)])
            with self.assertRaises(service.Rejected):
                supervisor.run(inference_smoke=True)
            self.assertEqual(call.call_count, 1)

    def test_readiness_failure_never_calls_helper(self):
        with self.owned() as (supervisor, owner, _, _, call):
            owner.startup_seconds = 1800
            result = supervisor.run(inference_smoke=True)
            self.assertEqual(result["status"], "failed")
            self.assertIsNone(result["inference"])
            self.assertTrue(result["cleanup_confirmed"])
            call.assert_not_called()

    def test_helper_failures_are_sticky_and_cleanup_keeps_valid_usage(self):
        failures = [ValueError("PRIVATE_CANARY"), KeyboardInterrupt("PRIVATE_CANARY"),
            SystemExit("PRIVATE_CANARY"), service.identity.worker.Abort("timeout")]
        failed = {**receipt(), "result": "fail", "failure": "completion"}
        for value in [failed, *failures, {**receipt(), "extra": "PRIVATE_CANARY"},
                      {**receipt(), "prepared_sha256": process.digest(b"wrong")}]:
            with self.subTest(value=type(value).__name__), self.owned() as (supervisor, owner, _, _, call):
                if isinstance(value, BaseException):
                    call.side_effect = value
                else:
                    call.return_value = value
                result = supervisor.run(inference_smoke=True)
                self.assertEqual(result["status"], "failed")
                self.assertTrue(result["cleanup_confirmed"])
                self.assertEqual(owner.events[-1], ("reap-unregistered", None))
                self.assertNotIn("PRIVATE_CANARY", json.dumps(result))
                self.assertEqual(result["inference"], failed if value is failed else None)

    def test_deadline_and_shutdown_after_helper_cannot_pass(self):
        for stop in ("deadline", "cancelled", "child"):
            with self.subTest(stop=stop), self.owned() as (supervisor, owner, clock, shutdown, call):
                def invoke(_):
                    if stop == "deadline":
                        clock.advance(supervisor.cutoff() - service.CLEANUP_RESERVE - clock.mono)
                    elif stop == "cancelled":
                        shutdown.stopped = True
                    else:
                        owner.children["engine"].returncode = 1
                    return receipt()
                call.side_effect = invoke
                result = supervisor.run(inference_smoke=True)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["inference"]["usage"], receipt()["usage"])
                self.assertLessEqual(clock.mono, supervisor.contract.hard_monotonic)

    def test_drain_reap_and_late_cancel_failure_keep_usage(self):
        for fail in ("drain", "reap", "reap_unregistered", "cancel"):
            with self.subTest(fail=fail), self.owned() as (supervisor, owner, _, shutdown, _):
                if fail == "cancel":
                    def reap(_):
                        shutdown.stopped = True
                        return True
                    owner.reap_unregistered = reap
                else:
                    setattr(owner, fail, mock.Mock(return_value=False))
                result = supervisor.run(inference_smoke=True)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["inference"]["usage"], receipt()["usage"])

    @contextmanager
    def real_helper(self):
        with inference_tests.InferenceTests().fixture() as state, ExitStack() as stack:
            supervisor, owner = state.supervisor, state.owner
            supervisor.children, supervisor.used = {}, False
            owner.prepared.receipt_sha256 = state.sha
            owner.start = mock.Mock(side_effect=lambda role, contract, inputs, register, deadline:
                                    register(owner.processes[role]))
            owner.probe = mock.Mock(side_effect=lambda role, child, deadline:
                service.EngineReady(lifecycle_tests.endpoint(child.pid), inference.encoded({"data": [
                    {"id": service.MODEL, "max_model_len": service.CONTEXT}]}),
                    service.CONTEXT, service.MEMORY_BYTES, 0) if role == "engine" else True)
            def retire(role, child, deadline):
                child.returncode = 0
                return True
            owner.drain = mock.Mock(side_effect=retire)
            owner.reap = mock.Mock(side_effect=retire)
            owner.reap_unregistered = mock.Mock(return_value=True)
            stack.enter_context(mock.patch.object(service.identity.Endpoint, "check"))
            yield state

    def test_real_helper_joins_actual_contract_with_mocked_transport(self):
        with self.real_helper() as state:
            row = state.supervisor.run(inference_smoke=True)
            self.assertEqual(row, owner_receipt(state.sha))
            self.assertEqual(state.connect.call_count, 3)
            self.assertEqual(state.clock.mono, 100)

    def test_prepared_retains_only_the_validated_receipt_hash(self):
        with process_tests.PackagingTests().prepared_fixture() as (_, _, sha):
            prepared = process.Prepared(sha, process.time.monotonic() + 10)
            self.assertEqual(prepared.receipt_sha256, sha)
            with self.assertRaises(service.Rejected):
                process.Prepared(SHA, process.time.monotonic() + 10)

    def test_preparation_diagnostics_closed_stages_categories_and_legacy_bytes(self):
        for stage in process.PREPARATION_STAGES:
            for rejected in (False, True):
                for enabled in (False, True):
                    with self.subTest(stage=stage, rejected=rejected, enabled=enabled), ExitStack() as stack:
                        targets = {"contract": (service.Contract, "begin"), "prepared": (process, "Prepared"),
                            "inputs": (service, "snapshot_inputs"), "owner": (process, "ProcessOwner")}
                        failure = service.Rejected("identity") if rejected else OSError("PRIVATE_CANARY")
                        failure.__cause__ = ValueError("PRIVATE_CHAIN_CANARY")
                        for name, (target, attribute) in targets.items():
                            kwargs = {"side_effect": failure} if name == stage else {}
                            stack.enter_context(mock.patch.object(target, attribute, **kwargs))
                        launch = stack.enter_context(mock.patch.object(service.Supervisor, "run"))
                        output = io.StringIO()
                        stack.enter_context(redirect_stdout(output))
                        args = ["--prepared-sha256", SHA, "--provider-config", "/PRIVATE_CANARY",
                            "--api-key-file", "/PRIVATE_CANARY", "--cert", "/PRIVATE_CANARY",
                            "--key", "/PRIVATE_CANARY", "--hostname", "private.invalid",
                            "--input-price", "1", "--output-price", "2", "--hard-stop-at", "unused"]
                        self.assertEqual(process.main(args + (["--inference-smoke"] if enabled else [])), 1)
                        launch.assert_not_called()
                        raw = output.getvalue()
                        self.assertNotIn("CANARY", raw)
                        self.assertNotIn("OSError", raw)
                        if enabled:
                            row = json.loads(raw)
                            self.assertEqual(row["preparation_stage"], stage)
                            self.assertEqual(row["preparation_category"], "identity" if rejected else "internal")
                            self.assertEqual(smoke.lifecycle_failure(raw.encode(), inference_smoke=True,
                                                                    prepared_sha256=SHA), row)
                        else:
                            self.assertEqual(raw, '{"schema":"nib.selected-service-lifecycle/v1",'
                                '"status":"failed","reason":"preparation","deployable":false,'
                                '"hardware_validated":false}\n')

    def cli_fixture(self, *, cleanup_error=False):
        # Used in an isolated subprocess so the real __main__ trampoline runs.
        with self.real_helper() as state, ExitStack() as stack:
            def prepared_init(target, expected, deadline):
                self.assertEqual(expected, state.sha)
                target.__dict__.update(state.owner.prepared.__dict__)
            def owner_init(target, prepared, inputs, hostname, shutdown):
                target.__dict__.update(state.owner.__dict__)
                target.prepared = prepared
            @contextmanager
            def inputs(**_):
                yield state.supervisor.inputs
                if cleanup_error:
                    raise OSError("PRIVATE_CANARY")
            stack.enter_context(mock.patch.object(process.Prepared, "__init__", prepared_init))
            stack.enter_context(mock.patch.object(process.ProcessOwner, "__init__", owner_init))
            stack.enter_context(mock.patch.object(service, "snapshot_inputs", inputs))
            stack.enter_context(mock.patch.object(process.threading, "Event", return_value=state.supervisor.shutdown))
            stack.enter_context(mock.patch.object(sys, "argv", [process.__file__, "--prepared-sha256", state.sha,
                "--provider-config", "/unused", "--api-key-file", "/unused", "--cert", "/unused", "--key", "/unused",
                "--hostname", "service.invalid", "--input-price", "0.10", "--output-price", "0.20",
                "--hard-stop-at", state.clock.timestamp(3600), "--inference-smoke"]))
            output = io.StringIO()
            with redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
                runpy.run_path(process.__file__, run_name="__main__")
            self.assertEqual(stopped.exception.code, int(cleanup_error))
            row = json.loads(output.getvalue())
            self.assertEqual(row["status"], "failed" if cleanup_error else "retired")
            self.assertEqual(row["inference"], receipt(state.sha))
            self.assertEqual(state.connect.call_count, 3)
            self.assertNotIn("PRIVATE_CANARY", output.getvalue())

    def test_actual_isolated_cli_canonical_identity_and_snapshot_cleanup_failure(self):
        code = """
import sys
sys.path.insert(0, sys.argv[1])
from test_selected_service_hook import HookTests
for failure in (False, True):
    HookTests().cli_fixture(cleanup_error=failure)
print('canonical-hook-ok')
"""
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code,
            str(Path(__file__).absolute().parent)], capture_output=True, timeout=20, check=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stdout, b"canonical-hook-ok\n")


class HostHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        smoke_tests.SmokeTests.setUpClass()

    def setUp(self):
        self.fixture = smoke_tests.SmokeTests()
        self.fixture.setUp()
        self.fixture.args.inference_smoke = True
        self.sha = self.fixture.build["prepared_sha256"]

    def test_closed_mode_hash_claims_and_failures(self):
        row = owner_receipt(self.sha)
        join = dict(inference_smoke=True, prepared_sha256=self.sha)
        self.assertEqual(smoke.lifecycle(inference.encoded(row), **join), row)
        for bad in (lifecycle(), {**row, "mode": "other"}, {**row, "extra": "PRIVATE_CANARY"},
                    {**row, "inference": None}, {**row, "inference": receipt(SHA)},
                    {**row, "inference": {**receipt(self.sha), "tools": True}},
                    {**row, "inference": {**receipt(self.sha), "result": "fail", "failure": "cancelled"}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                smoke.lifecycle(inference.encoded(bad), **join)
        with self.assertRaises(ValueError):
            smoke.lifecycle(inference.encoded(row))
        for helper in (None, receipt(self.sha), {**receipt(self.sha), "result": "fail", "failure": "completion"}):
            failed = {**row, "status": "failed", "inference": helper}
            self.assertEqual(smoke.lifecycle_failure(inference.encoded(failed), **join), failed)
        minimal = {"schema": service.INFERENCE_SCHEMA, "mode": service.INFERENCE_MODE, "inference": None,
            "status": "failed", "reason": "preparation", "deployable": False, "hardware_validated": False,
            "preparation_stage": "prepared", "preparation_category": "identity"}
        self.assertEqual(smoke.lifecycle_failure(inference.encoded(minimal), **join), minimal)
        for bad in (lifecycle(), {**minimal, "inference": receipt(self.sha)},
                    {**minimal, "extra": "PRIVATE_CANARY"}):
            with self.assertRaises(ValueError):
                smoke.lifecycle_failure(inference.encoded(bad), **join)
        for key in ("preparation_stage", "preparation_category"):
            for value in ("PRIVATE_CANARY", "OSError", None, 1, [], {}):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    smoke.lifecycle_failure(inference.encoded({**minimal, key: value}), **join)
            with self.assertRaises(ValueError):
                smoke.lifecycle_failure(inference.encoded({k: v for k, v in minimal.items() if k != key}), **join)
            with self.assertRaises(ValueError):
                smoke.lifecycle_failure(inference.encoded({**row, "status": "failed", key: minimal[key]}), **join)

    def test_host_pass_and_cleanup_failures_keep_accounting_without_retry(self):
        fixture = self.fixture
        row = owner_receipt(self.sha)
        for failure, code in ((None, 0), ("cleanup", 0), (None, 1), ("inspect", 0)):
            with self.subTest(failure=failure, code=code), fixture.running(
                    failure=failure, monitor_code=code, output=inference.encoded(row)) as (contract, result):
                original = fixture.recorder.call.side_effect
                def call(command, env, **kwargs):
                    if failure == "inspect" and command[2] == "inspect" and fixture.info is not None \
                            and fixture.info["State"]["Status"] == "exited":
                        raise OSError("PRIVATE_CANARY")
                    return original(command, env, **kwargs)
                fixture.recorder.call.side_effect = call
                fixture.run_owner(contract, result)
                self.assertEqual(result["result"], "pass" if failure is None and code == 0 else "fail")
                self.assertEqual(result["lifecycle"]["inference"]["usage"], receipt()["usage"])
                self.assertNotIn("PRIVATE_CANARY", json.dumps(result))
            create = next(cmd for cmd in fixture.commands if cmd[2] == "create")
            self.assertEqual(create[-1], "--inference-smoke")
            self.assertEqual(create[create.index("--network") + 1], "none")
            self.assertNotIn("--publish", create)
            self.assertEqual(sum(cmd[2] == "start" for cmd in fixture.commands), 1)

    def test_nonzero_helper_failure_preserves_usage_through_diagnostic_path(self):
        fixture = self.fixture
        row = owner_receipt(self.sha)
        row.update(status="failed", reason="owner-failed")
        row["inference"].update(result="fail", failure="completion")
        with fixture.running() as (contract, result):
            original = fixture.recorder.call.side_effect
            def call(command, env, **kwargs):
                if command[2] == "start":
                    raise subprocess.CalledProcessError(1, command, output=inference.encoded(row),
                                                        stderr=b"PRIVATE_CANARY")
                return original(command, env, **kwargs)
            fixture.recorder.call.side_effect = call
            fixture.run_owner(contract, result)
        self.assertEqual(result["owner_failure"], row)
        self.assertEqual(result["start_client_exit_code"], 1)
        self.assertEqual(result["cleanup"], "confirmed")
        self.assertEqual(result["result"], "fail")
        self.assertNotIn("PRIVATE_CANARY", json.dumps(result))

    def test_inspect_mode_records_opt_in_but_never_runs(self):
        self.fixture.args.run = False
        with self.fixture.admission() as (_, run):
            row = smoke.preflight(self.fixture.args)
            self.assertEqual(row["schema"], "nib.selected-service-smoke/v2")
            self.assertEqual(row["mode"], service.INFERENCE_MODE)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
