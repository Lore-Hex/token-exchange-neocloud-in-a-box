# SPDX-License-Identifier: Apache-2.0
"""Concrete CLI/admission/lifecycle joins with fake owners; no listening ports."""

from contextlib import ExitStack, contextmanager, redirect_stdout, redirect_stderr
import copy
from dataclasses import replace
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import selected_long_qualification as q
import test_selected_long_qualification as fixtures
import test_selected_service as lifecycle_tests
import test_selected_service_lifetime as lifetime_tests
import test_selected_service_smoke as smoke_tests

service, process = q.service, q.process
host, owner, smoke = lifetime_tests.host, lifetime_tests.owner, smoke_tests.smoke
LONG, DAY, SHA = service.engine_entry.LONG_PROFILE, service.ORDINARY_PROFILE, "a" * 64


def successful():
    def collect(*args):
        observed = fixtures.collector(*args)
        if args[1] == 3:
            observed = replace(observed, sample=replace(observed.sample,
                completion_tokens=q.LONG_OUTPUT, finish_reason="length"))
        return observed
    return fixtures.run_sequence(collect, execution_profile=LONG)


class HookTests(unittest.TestCase):
    @contextmanager
    def supervisor(self):
        with lifecycle_tests.LifecycleTests().setup_service() as (supervisor, backend, clock, shutdown):
            supervisor.contract = service.Contract.begin(clock.timestamp(8000), input_price="1", output_price="2",
                lifetime_profile=DAY, execution_profile=LONG)
            supervisor.hard_deadline = supervisor.contract.hard_monotonic
            backend.prepared = SimpleNamespace(receipt_sha256=SHA)
            backend.observation = service.EngineReady(lifecycle_tests.endpoint(1000),
                json.dumps({"data": [{"id": service.MODEL, "max_model_len": q.CONTEXT}]}).encode(),
                q.CONTEXT, service.MEMORY_BYTES, 0, LONG)
            with redirect_stdout(io.StringIO()):
                yield supervisor, backend, clock, shutdown

    def join(self, result, contract, *, failed=False):
        return (smoke.lifecycle_failure if failed else smoke.lifecycle)(q.encoded(result),
            lifetime=contract.lifetime, prepared_sha256=SHA, execution_profile=LONG, long_qualification=True)

    def test_ready_hook_runs_once_then_retires_without_waiting_for_day_expiry(self):
        qualified = successful()
        self.assertTrue(qualified["checks_pass"])
        with self.supervisor() as (supervisor, backend, clock, _):
            initial = clock.mono
            def run(actual):
                self.assertIs(actual, supervisor)
                self.assertEqual(actual.phase, "ready")
                self.assertEqual(list(actual.children), list(service.ROLES))
                self.assertEqual((actual.contract.request_seconds, actual.contract.cleanup_reserve), (900, 1960))
                clock.advance(3600)
                return qualified
            with mock.patch.object(q, "run_owned", side_effect=run) as hook:
                result = supervisor.run(long_qualification=True)
                with self.assertRaises(ValueError):
                    supervisor.run(long_qualification=True)
            hook.assert_called_once()
            self.assertEqual(clock.mono, initial + 3602)
            self.assertEqual((result["schema"], result["lifecycle_schema"]),
                             (service.EXECUTION_SCHEMA, service.LONG_SCHEMA))
            self.assertEqual(result["status"], "retired", result)
            self.assertTrue(result["cleanup_confirmed"])
            self.assertIs(result["qualification"], qualified)
            self.assertEqual(self.join(result, supervisor.contract), result)
            self.assertEqual([event for event in backend.events if event[0] in ("drain", "reap")],
                [("drain", "tls"), ("reap", "tls"), ("drain", "guard"), ("reap", "guard"), ("reap", "engine")])
            self.assertLess(clock.mono, supervisor.contract.hard_monotonic)

    def test_cache_miss_short_output_cancel_and_unconfirmed_close_cannot_pass(self):
        def miss(*args):
            observed = fixtures.collector(*args)
            return replace(observed, sample=replace(observed.sample, cached_prompt_tokens=0))
        def lost(*args):
            args[-1]["attempted"] = True
            q.record_sample(args[-1], replace(fixtures.sample(), status="cancelled"))
            raise KeyboardInterrupt()
        for report in (fixtures.run_sequence(execution_profile=LONG),
                       fixtures.run_sequence(miss, execution_profile=LONG),
                       fixtures.run_sequence(lost, execution_profile=LONG)):
            with self.subTest(failure=report["failure"]), self.supervisor() as (supervisor, backend, _, _), \
                    mock.patch.object(q, "run_owned", return_value=report):
                result = supervisor.run(long_qualification=True)
                self.assertEqual((result["status"], result["reason"]), ("failed", "owner-failed"))
                self.assertEqual(result["qualification"], report)
                self.assertEqual(result["cleanup_confirmed"], all(row["resources_closed"] for row in report["samples"]))
                self.assertTrue(all(child.poll() is not None for child in backend.children.values()))
                self.assertEqual(self.join(result, supervisor.contract, failed=True), result)
                with self.assertRaises(ValueError):
                    self.join(result, supervisor.contract)

    def test_unexpected_hook_exception_and_cleanup_failure_stay_failed(self):
        for fault in ("exception", "reap", "cancel", "deadline"):
            with self.subTest(fault=fault), self.supervisor() as (supervisor, backend, clock, shutdown):
                def run(_):
                    if fault == "exception":
                        raise RuntimeError("PRIVATE_CANARY")
                    if fault == "reap":
                        backend.reap = lambda *_: False
                    if fault == "cancel":
                        shutdown.stopped = True
                    if fault == "deadline":
                        clock.advance(8000 - supervisor.contract.cleanup_reserve)
                    return successful()
                with mock.patch.object(q, "run_owned", side_effect=run):
                    result = supervisor.run(long_qualification=True)
                self.assertEqual(result["status"], "failed")
                self.assertNotIn("PRIVATE_CANARY", q.encoded(result).decode())
                self.assertEqual(self.join(result, supervisor.contract, failed=True), result)

    def test_mode_combinations_rejected_before_start_and_defaults_do_not_call_hook(self):
        for profile, lifetime, other in ((service.engine_entry.EAGER_PROFILE, DAY, False),
                (LONG, "smoke", False), (LONG, DAY, True)):
            with self.supervisor() as (supervisor, backend, _, _), mock.patch.object(q, "run_owned") as hook:
                supervisor.contract = replace(supervisor.contract, execution_profile=profile, lifetime_profile=lifetime)
                with self.assertRaises(ValueError):
                    supervisor.run(long_qualification=True, tool_smoke=other)
                self.assertEqual(backend.children, {})
                hook.assert_not_called()
        with lifecycle_tests.LifecycleTests().setup_service() as (supervisor, _, _, _), \
                mock.patch.object(q, "run_owned") as hook:
            self.assertEqual(supervisor.run()["status"], "retired")
            hook.assert_not_called()

    def test_report_joins_cannot_be_replayed_as_ordinary_or_promoted(self):
        with self.supervisor() as (supervisor, _, _, _), mock.patch.object(q, "run_owned", return_value=successful()):
            result = supervisor.run(long_qualification=True)
            for mutate in (lambda r: r["qualification"].update(prepared_sha256="b" * 64),
                    lambda r: r["qualification"].update(checks_pass=False),
                    lambda r: r.update(mode="tool-smoke"), lambda r: r.update(unknown="PRIVATE_CANARY"),
                    lambda r: r["qualification"].update(request_rank_identity="0")):
                row = copy.deepcopy(result)
                mutate(row)
                with self.assertRaises(ValueError):
                    self.join(row, supervisor.contract)
            with self.assertRaises(ValueError):
                smoke.lifecycle(q.encoded(result), lifetime=supervisor.contract.lifetime,
                                execution_profile=LONG, prepared_sha256=SHA)
            with self.assertRaises(ValueError):
                self.join({**result, "status": "failed"}, supervisor.contract)

    def test_terminal_and_all_progress_fit_existing_attached_output_bound(self):
        with self.supervisor() as (supervisor, _, _, _), mock.patch.object(q, "run_owned", return_value=successful()):
            result = supervisor.run(long_qualification=True)
            rows = [service.progress_record(phase, supervisor.contract.lifetime, SHA, LONG)
                    for phase in service.PROGRESS_PHASES]
            # Actual process publisher uses default JSON separators, unlike the
            # compact private report encoder. Preserve the real 16 KiB bound.
            wire = b"".join(json.dumps(row, sort_keys=True).encode() + b"\n" for row in [*rows, result])
            self.assertLessEqual(len(wire), 16384)
            sink = host.AttachedProgress(supervisor.contract.lifetime, SHA, execution_profile=LONG)
            with redirect_stderr(io.StringIO()):
                for start in range(0, len(wire), 23):
                    sink.write(wire[start:start + 23])
            self.assertEqual(json.loads(sink.finish(success=True)), result)


class AdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        smoke_tests.SmokeTests.setUpClass()

    def test_actual_admission_binds_all_helpers_profile_and_original_budget(self):
        fixture = smoke_tests.SmokeTests()
        fixture.setUp()
        args = copy.copy(fixture.args)
        args.long_qualification, args.lifetime_profile, args.execution_profile = True, DAY, LONG
        args.hard_stop_at = fixture.clock.timestamp(8000)
        fixture.lab_contract["execution_profile"] = LONG
        names = ("scripts/selected_service.py", "scripts/selected_service_process.py", "scripts/selected_service_engine.py",
                 q.SELF, q.CHILD, "scripts/selected_service_inference.py", "scripts/stream_transport.py", "scripts/stream_measurement.py")
        files = {"context/service/" + name: (smoke.ROOT / name).read_bytes() for name in names}
        for mutation in (None, "budget", *files):
            selected = dict(files)
            args.hard_stop_at = fixture.clock.timestamp(7540 if mutation == "budget" else 8000)
            if mutation in files:
                selected[mutation] = b"old approved source without long qualification"
            with self.subTest(mutation=mutation), fixture.admission() as (inspect, _), \
                    mock.patch.object(smoke.image_builder, "validated_files", return_value=(
                        {"prepared_sha256": fixture.build["prepared_sha256"]}, selected, fixture.lab_contract)):
                if mutation is not None:
                    with self.assertRaises(ValueError), smoke.admitted_runtime(args, smoke.report(), lifetime_profile=DAY):
                        self.fail("stale source or insufficient reserve admitted")
                    inspect.assert_not_called()
                else:
                    with smoke.admitted_runtime(args, smoke.report(), lifetime_profile=DAY) as admitted:
                        self.assertEqual((admitted[0].request_seconds, admitted[0].cleanup_reserve), (900, 1960))

    def test_container_command_has_one_explicit_hook_and_smoke_default_is_identical(self):
        fixture = smoke_tests.SmokeTests()
        fixture.setUp()
        args = copy.copy(fixture.args)
        def command():
            return smoke.container_command("nib-selected-" + "1" * 32, "2" * 32,
                fixture.build, fixture.base, args.weights, "/private/stage", args, args.hard_stop_at)
        original = command()
        args.long_qualification = False
        self.assertEqual(command(), original)
        args.long_qualification, args.lifetime_profile, args.execution_profile = True, DAY, LONG
        self.assertEqual(command().count("--long-qualification"), 1)
        self.assertEqual(command()[-1], "--long-qualification")
        for field, value in (("lifetime_profile", "smoke"), ("execution_profile", "tau8k-eager"), ("tool_smoke", True)):
            old = getattr(args, field, False)
            setattr(args, field, value)
            with self.assertRaises(ValueError):
                command()
            setattr(args, field, old)

    def test_process_cli_checks_remaining_budget_before_prepared_inputs_or_processes(self):
        clock = lifecycle_tests.Clock()
        args = ["--prepared-sha256", SHA, "--provider-config", "/PRIVATE_CANARY", "--api-key-file", "/PRIVATE_CANARY",
            "--cert", "/PRIVATE_CANARY", "--key", "/PRIVATE_CANARY", "--hostname", "private.invalid",
            "--input-price", "1", "--output-price", "2", "--hard-stop-at", clock.timestamp(7360),
            "--execution-profile", LONG, "--lifetime-profile", DAY, "--long-qualification"]
        output = io.StringIO()
        with ExitStack() as stack:
            for module in (service, service.pilot, process):
                stack.enter_context(mock.patch.object(module, "time", clock))
            prepared = stack.enter_context(mock.patch.object(process, "Prepared"))
            inputs = stack.enter_context(mock.patch.object(service, "snapshot_inputs"))
            spawn = stack.enter_context(mock.patch.object(process.subprocess, "Popen"))
            stack.enter_context(redirect_stdout(output))
            self.assertEqual(process.main(args), 1)
        for call in (prepared, inputs, spawn):
            call.assert_not_called()
        row = json.loads(output.getvalue())
        self.assertEqual((row["status"], row["reason"], row["qualification"]), ("failed", "preparation", None))
        self.assertNotIn("PRIVATE_CANARY", output.getvalue())
        smoke.lifecycle_failure(output.getvalue().encode(), lifetime=row["lifetime"],
                                prepared_sha256=SHA, execution_profile=LONG, long_qualification=True)

    def test_process_cli_redacts_invalid_preparation_hash_on_every_early_exit(self):
        clock = lifecycle_tests.Clock()
        for budget in (7360, 8000):
            for digest in ("PRIVATE_CANARY", "a" * 64 + "PRIVATE_CANARY", "A" * 64):
                with self.subTest(budget=budget, digest=digest), ExitStack() as stack:
                    args = ["--prepared-sha256", digest, "--provider-config", "/PRIVATE_CANARY",
                        "--api-key-file", "/PRIVATE_CANARY", "--cert", "/PRIVATE_CANARY",
                        "--key", "/PRIVATE_CANARY", "--hostname", "private.invalid",
                        "--input-price", "1", "--output-price", "2",
                        "--hard-stop-at", clock.timestamp(budget), "--execution-profile", LONG,
                        "--lifetime-profile", DAY, "--long-qualification"]
                    for module in (service, service.pilot, process):
                        stack.enter_context(mock.patch.object(module, "time", clock))
                    prepared = stack.enter_context(mock.patch.object(process, "Prepared"))
                    inputs = stack.enter_context(mock.patch.object(service, "snapshot_inputs"))
                    spawn = stack.enter_context(mock.patch.object(process.subprocess, "Popen"))
                    output = io.StringIO()
                    stack.enter_context(redirect_stdout(output))
                    self.assertEqual(process.main(args), 1)
                    row = json.loads(output.getvalue())
                    self.assertEqual((row["status"], row["reason"]), ("failed", "preparation"))
                    self.assertIsNone(row["prepared_sha256"])
                    self.assertIsNone(row["execution_prepared_sha256"])
                    self.assertNotIn("PRIVATE_CANARY", output.getvalue())
                    self.assertNotIn(digest, output.getvalue())
                    for call in (prepared, inputs, spawn):
                        call.assert_not_called()

    def test_process_cli_passes_only_opt_in_hook_and_preserves_cleanup_exception(self):
        clock = lifecycle_tests.Clock()
        args = ["--prepared-sha256", SHA, "--provider-config", "/private/provider", "--api-key-file", "/private/key",
            "--cert", "/private/cert", "--key", "/private/key", "--hostname", "private.invalid",
            "--input-price", "1", "--output-price", "2", "--hard-stop-at", clock.timestamp(8000),
            "--execution-profile", LONG, "--lifetime-profile", DAY, "--long-qualification"]
        contract = service.Contract  # Real contract construction remains tested.
        for cleanup_failure in (False, True):
            @contextmanager
            def snapshots(**_):
                yield service.Inputs(Path("/private/synthetic-inputs"), {})
                if cleanup_failure:
                    raise ValueError("PRIVATE_CANARY")
            output = io.StringIO()
            with ExitStack() as stack:
                for module in (service, service.pilot, process):
                    stack.enter_context(mock.patch.object(module, "time", clock))
                actual = contract.begin(clock.timestamp(8000), input_price="1", output_price="2",
                    execution_profile=LONG, lifetime_profile=DAY)
                result = {**smoke_tests.lifecycle(), "schema": service.LONG_SCHEMA, "mode": service.LONG_MODE,
                    "lifetime": actual.lifetime, "prepared_sha256": SHA, "qualification": successful()}
                result = service.execution_result(result, LONG, SHA)
                stack.enter_context(mock.patch.object(process, "Prepared", return_value=SimpleNamespace(lab={"execution_profile": LONG})))
                stack.enter_context(mock.patch.object(service, "snapshot_inputs", snapshots))
                stack.enter_context(mock.patch.object(process, "ProcessOwner"))
                run = stack.enter_context(mock.patch.object(service.Supervisor, "run", return_value=result))
                stack.enter_context(redirect_stdout(output))
                self.assertEqual(process.main(args), int(cleanup_failure), output.getvalue())
            self.assertTrue(run.call_args.kwargs["long_qualification"])
            row = json.loads(output.getvalue().splitlines()[-1])
            self.assertEqual(row["qualification"], result["qualification"])
            self.assertEqual(row["status"], "failed" if cleanup_failure else "retired")
            self.assertNotIn("PRIVATE_CANARY", output.getvalue())
            (smoke.lifecycle_failure if cleanup_failure else smoke.lifecycle)(q.encoded(row),
                prepared_sha256=SHA, lifetime=actual.lifetime, execution_profile=LONG, long_qualification=True)

    def test_owner_journal_rejects_duplicate_wrong_profile_and_smoke_hook(self):
        lifetime_tests.IntentTests.setUpClass()
        check = lifetime_tests.IntentTests()
        check.setUp()
        self.addCleanup(check.doCleanups)
        _, _, runtime = check.day()
        runtime["command"] += ["--execution-profile", LONG, "--long-qualification"]
        path = check.fixture.state.root / "runtime.json"
        def write(row):
            path.write_bytes(owner.policy.encoded(row))
            return check.fixture.state.read()
        self.assertEqual(write(runtime)["runtime.json"], runtime)
        for command in (runtime["command"] + ["--long-qualification"],
                        ["tau8k-eager" if item == LONG else item for item in runtime["command"]],
                        runtime["command"] + ["--tool-smoke"]):
            with self.assertRaises(ValueError):
                write({**runtime, "command": command})

    def test_actual_host_cli_forwards_hook_only_when_explicit_and_rejects_combinations(self):
        values = {name: "/private/synthetic-input" for name in (
            "build-report", "build-sha256", "preparation", "preparation-sha256", "docker-host",
            "provider-config", "api-key-file", "cert", "key", "hostname", "weights", "input-price",
            "output-price", "hard-stop-at", "state-dir", "ingress", "address", "published-address", "subnet", "gateway")}
        args = ["--inspect", "--published-port", "8443", "--lifetime-profile", DAY, "--execution-profile", LONG]
        args += [item for name, value in values.items() for item in ("--" + name, value)]
        for suffix in ([], ["--long-qualification"], ["--long-qualification", "--tool-smoke"]):
            output = io.StringIO()
            with mock.patch.object(host, "preflight", return_value=host.report()) as preflight, \
                    mock.patch.object(host.subprocess, "Popen") as spawn, redirect_stdout(output):
                self.assertEqual(host.main(args + suffix), 1)
            spawn.assert_not_called()
            if "--tool-smoke" in suffix:
                preflight.assert_not_called()
            else:
                observed = preflight.call_args.args[0]
                self.assertEqual((observed.long_qualification, observed.execution_profile, observed.lifetime_profile),
                                 (bool(suffix), LONG, DAY))
            self.assertNotIn("/private/synthetic-input", output.getvalue())


if __name__ == "__main__":
    unittest.main()
