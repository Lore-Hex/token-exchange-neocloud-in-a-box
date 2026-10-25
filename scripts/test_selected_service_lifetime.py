# SPDX-License-Identifier: Apache-2.0
"""Closed lifetime profiles with fake clocks/owners, never Docker or sockets."""

from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
import copy
from dataclasses import FrozenInstanceError
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

import selected_service as service
import selected_service_process as process
import test_selected_service as lifecycle_tests
import test_selected_service_network as network_tests
import test_selected_service_owner as owner_tests
import test_selected_service_smoke as smoke_tests

host, owner = owner_tests.host, owner_tests.owner
network, smoke = owner.network, smoke_tests.smoke
DAY = service.ORDINARY_PROFILE


class LifetimeTests(unittest.TestCase):
    def setUp(self):
        self.clock = lifecycle_tests.Clock()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.progress = io.StringIO()
        self.stack.enter_context(redirect_stdout(self.progress))
        for module in (service, service.pilot, host, process):
            self.stack.enter_context(mock.patch.object(module, "time", self.clock))

    def contract(self, seconds, profile=service.SMOKE_PROFILE):
        return service.Contract.begin(self.clock.timestamp(seconds), input_price="1", output_price="2",
                                      lifetime_profile=profile)

    def supervisor(self, profile=DAY):
        contract = self.contract(86400 if profile == DAY else 4800, profile)
        shutdown = lifecycle_tests.Shutdown(self.clock)
        backend = lifecycle_tests.Owner(self.clock, shutdown)
        backend.prepared = types.SimpleNamespace(receipt_sha256="a" * 64)
        inputs = service.Inputs(Path("/unused"), {})
        self.stack.enter_context(mock.patch.object(inputs, "check"))
        self.stack.enter_context(mock.patch.object(service.identity.Endpoint, "check"))
        return service.Supervisor(contract, inputs, backend, shutdown), backend

    def test_closed_limits_default_and_exact_day_boundary(self):
        self.assertEqual(self.contract(4800).lifetime_profile, "smoke")
        self.assertEqual(self.contract(86400, DAY).hard_monotonic, self.clock.mono + 86400)
        for seconds, profile in ((4801, "smoke"), (86401, DAY), (0, DAY), (-1, DAY),
                                 (service.CLEANUP_RESERVE, DAY)):
            with self.subTest(seconds=seconds, profile=profile), self.assertRaises(ValueError):
                self.contract(seconds, profile)
        for profile in (None, True, 86400, [], {}, "24h", "SMOKE", "PRIVATE_CANARY"):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                self.contract(3600, profile)

    def test_day_lifecycle_catalog_and_original_cutoff_join(self):
        supervisor, backend = self.supervisor()
        contract = supervisor.contract
        backend.startup_seconds = 821
        arguments = contract.guard_arguments(supervisor.inputs)
        self.assertEqual(arguments[arguments.index("--retirement-at") + 1], contract.admission_stop_at)
        result = supervisor.run()
        self.assertEqual(result["schema"], service.ORDINARY_SCHEMA)
        self.assertEqual(result["lifetime"], contract.lifetime)
        self.assertEqual((result["status"], result["reason"]), ("retired", "retired"))
        self.assertEqual(result["prepared_sha256"], "a" * 64)
        self.assertEqual([json.loads(line)["phase"] for line in self.progress.getvalue().splitlines()],
                         ["ready", "draining"])
        self.assertEqual([row["role"] for row in result["cleanup"]], ["tls", "guard", "engine"])
        self.assertTrue(all(row["drained"] and row["reaped"] for row in result["cleanup"]))
        self.assertTrue(all(value is contract for value in backend.launches))
        self.assertLessEqual(self.clock.mono, contract.hard_monotonic)
        with self.assertRaises(FrozenInstanceError):
            contract.lifetime_profile = "smoke"
        with self.assertRaises(ValueError):
            supervisor.run()
        self.assertEqual(len(backend.launches), 3)

    def test_forward_jump_is_irreversible_and_expired_restart_rejected(self):
        supervisor, _ = self.supervisor()
        original = supervisor.contract
        self.clock.wall += 80000
        shortened = supervisor.cutoff()
        self.clock.wall -= 40000
        self.assertEqual(supervisor.cutoff(), shortened)
        self.assertEqual(original.lifetime["hard_stop_at"], original.hard_stop_at)
        self.clock.mono = shortened
        with self.assertRaises(ValueError):
            supervisor.checkpoint(original.hard_monotonic)
        self.clock.wall = original.hard_wall
        with self.assertRaises(ValueError):
            service.Contract.begin(original.hard_stop_at, input_price="1", output_price="2",
                                   lifetime_profile=DAY)

    def test_day_smoke_combinations_cannot_start_children(self):
        for mode in ("inference_smoke", "conformance_smoke", "protocol_smoke", "tool_smoke"):
            with self.subTest(mode=mode):
                supervisor, backend = self.supervisor()
                with self.assertRaises(ValueError):
                    supervisor.run(**{mode: True})
                self.assertEqual(backend.launches, [])

    def test_day_cleanup_failure_and_cancel_stay_failures(self):
        for fault in ("reap", "cancel"):
            with self.subTest(fault=fault):
                supervisor, backend = self.supervisor()
                if fault == "reap":
                    backend.reap = lambda *_: False
                else:
                    supervisor.shutdown.stopped = True
                row = supervisor.run()
                with self.assertRaises(ValueError):
                    smoke.lifecycle(smoke_tests.encoded(row), lifetime=supervisor.contract.lifetime,
                                    prepared_sha256="a" * 64)
                self.assertFalse(row["deployable"] or row["hardware_validated"] or row["confidential"])

    def test_broken_progress_output_never_bypasses_drain_or_cleanup(self):
        supervisor, backend = self.supervisor()
        with mock.patch.object(service, "progress", side_effect=BrokenPipeError("PRIVATE_CANARY")):
            row = supervisor.run()
        self.assertEqual((row["status"], row["reason"]), ("failed", "owner-failed"))
        self.assertTrue(row["cleanup_confirmed"])
        self.assertTrue(all(child.poll() is not None for child in backend.children.values()))
        self.assertTrue(all(item["drained"] and item["reaped"] for item in row["cleanup"]))
        self.assertNotIn("PRIVATE_CANARY", json.dumps(row))

    def test_process_rejects_profile_modes_and_expiry_before_prepared_or_spawn(self):
        args = ["--prepared-sha256", "a" * 64, "--provider-config", "/PRIVATE_CANARY/provider",
            "--api-key-file", "/PRIVATE_CANARY/key", "--cert", "/PRIVATE_CANARY/cert",
            "--key", "/PRIVATE_CANARY/tls-key", "--hostname", "service.invalid",
            "--input-price", "1", "--output-price", "2"]
        for extra in (["--lifetime-profile", "PRIVATE_CANARY"],
                      ["--lifetime-profile", DAY, "--tool-smoke"], []):
            output = io.StringIO()
            with self.subTest(extra=extra), redirect_stdout(output), \
                    mock.patch.object(process, "Prepared") as prepared, \
                    mock.patch.object(process.subprocess, "Popen") as spawn:
                self.assertEqual(process.main([*args, "--hard-stop-at", self.clock.timestamp(86400), *extra]), 1)
                prepared.assert_not_called()
                spawn.assert_not_called()
                self.assertNotIn("PRIVATE_CANARY", output.getvalue())

    def test_day_preparation_failure_never_echoes_an_invalid_prepared_digest(self):
        args = ["--prepared-sha256", "PRIVATE_CANARY", "--provider-config", "/unused",
            "--api-key-file", "/unused", "--cert", "/unused", "--key", "/unused",
            "--hostname", "service.invalid", "--input-price", "1", "--output-price", "2",
            "--hard-stop-at", self.clock.timestamp(86400), "--lifetime-profile", DAY]
        output = io.StringIO()
        with redirect_stdout(output), mock.patch.object(process, "Prepared",
                side_effect=ValueError("PRIVATE_CANARY")), mock.patch.object(process.subprocess, "Popen") as spawn:
            self.assertEqual(process.main(args), 1)
            spawn.assert_not_called()
        self.assertNotIn("PRIVATE_CANARY", output.getvalue())
        row = json.loads(output.getvalue())
        self.assertEqual(row["schema"], service.ORDINARY_SCHEMA)
        self.assertIsNone(row["prepared_sha256"])


class AdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        smoke_tests.SmokeTests.setUpClass()

    def setUp(self):
        self.fixture = smoke_tests.SmokeTests("test_build_hash_source_join_and_closed_claims")
        self.fixture.setUp()
        self.args = copy.copy(self.fixture.args)
        self.args.lifetime_profile = DAY
        self.args.hard_stop_at = self.fixture.clock.timestamp(86400)
        self.files = {"context/service/" + name: (smoke.ROOT / name).read_bytes() for name in
                      ("scripts/selected_service.py", "scripts/selected_service_process.py")}

    @contextmanager
    def admission(self):
        with self.fixture.admission() as observations, mock.patch.object(smoke.image_builder,
                "validated_files", return_value=({"prepared_sha256": self.fixture.build["prepared_sha256"]},
                                                  self.files, self.fixture.lab_contract)):
            yield observations

    def test_actual_shared_admission_preserves_same_contract_and_source(self):
        with self.admission():
            with smoke.admitted_runtime(self.args, smoke.report(), lifetime_profile=DAY) as admitted:
                contract = admitted[0]
                self.assertEqual(contract.lifetime_profile, DAY)
                self.assertEqual(contract.hard_monotonic, self.fixture.clock.mono + 86400)
                commands = host.admitted_commands(admitted)
                self.assertEqual((commands.wall, commands.deadline), (contract.hard_wall, contract.hard_monotonic))
                self.fixture.clock.advance(600)
                self.fixture.clock.wall -= 300
                self.assertEqual(commands.remaining(), 85800)
                self.assertEqual(contract.hard_monotonic, 86500)

    def test_old_image_source_and_smoke_entry_cannot_opt_into_day(self):
        for name in tuple(self.files):
            with self.subTest(name=name), self.admission() as (inspect, _):
                original = self.files[name]
                self.files[name] = b"old approved owner without lifetime protocol"
                try:
                    with self.assertRaises(ValueError), smoke.admitted_runtime(
                            self.args, smoke.report(), lifetime_profile=DAY):
                        self.fail("stale source accepted")
                    inspect.assert_not_called()
                finally:
                    self.files[name] = original
        with self.admission() as (inspect, _):
            with self.assertRaises(ValueError), smoke.admitted_runtime(self.args, smoke.report()):
                self.fail("smoke admission promoted")
            inspect.assert_not_called()

    def test_host_inspect_joins_day_profile_and_reserves_without_resources(self):
        selected = network_tests.network()
        args = self.args
        args.run, args.state_dir = False, "/unused-state"
        args.ingress, args.address = selected.policy.ingress, selected.policy.address
        args.published_address, args.published_port = selected.policy.published, selected.policy.port
        args.subnet, args.gateway = selected.subnet, selected.gateway
        seen = []
        def admit(reservation):
            seen.append(reservation.journal.lifetime_profile)
        with self.admission(), ExitStack() as stack:
            stack.enter_context(mock.patch.object(host, "smoke", smoke))
            stack.enter_context(mock.patch.object(network.Reservation, "admit", admit))
            stack.enter_context(mock.patch.object(owner, "daemon_identity"))
            stack.enter_context(mock.patch.object(owner, "host_target"))
            stack.enter_context(mock.patch.object(smoke, "weights_view"))
            spawn = stack.enter_context(mock.patch.object(host.subprocess, "Popen"))
            row = host.preflight(args)
            spawn.assert_not_called()
        self.assertEqual(seen, [DAY])
        self.assertTrue(row["preflight_pass"] and row["private_inputs_removed"])
        self.assertEqual(row["schema"], "nib.selected-service-owner/v2")
        self.assertEqual(row["lifetime"], {**service.lifetime_record(DAY,
            self.fixture.clock.wall + 86400 - smoke.HOST_RESERVE), "owner_hard_stop_at": args.hard_stop_at})
        self.assertFalse(row["runtime_started"] or row["published"] or row["confidential"])

    def test_runtime_plan_rejects_mismatched_profile_before_weights(self):
        with self.admission(), smoke.admitted_runtime(self.args, smoke.report(), lifetime_profile=DAY) as admitted, \
                mock.patch.object(host.smoke, "weights_view") as weights:
            args = copy.copy(self.args)
            args.lifetime_profile = "smoke"
            with self.assertRaises(ValueError):
                host.runtime_plan(args, admitted, network_tests.network(), host.admitted_commands(admitted), host.report())
            weights.assert_not_called()


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "network").mkdir(mode=0o700)
        self.journal = network.Journal(self.root / "network", network_tests.network(), 2000000000, 1000000,
                                       lifetime_profile=DAY)
        self.journal.initialize()

    def test_watchdog_reads_exact_profile_and_rejects_journal_drift(self):
        restored = host.read_reservation(self.root)
        self.assertEqual(restored.contract, self.journal.contract)
        self.assertEqual(restored.lifetime_profile, DAY)
        for field, value in (("schema", network.SCHEMA), ("lifetime_profile", "smoke"),
                             ("lifetime_profile", "PRIVATE_CANARY"), ("extra", True)):
            with self.subTest(field=field, value=value):
                (self.root / "network/contract.json").write_bytes(network.policy.encoded(
                    {**self.journal.contract, field: value}))
                with self.assertRaises(ValueError):
                    host.read_reservation(self.root)

    def test_network_admission_profile_limits_without_changing_venue_gates(self):
        commands = network_tests.FakeCommands(self.journal)
        reservation = network.Reservation(commands, self.journal)
        with mock.patch.object(network.os, "getuid", return_value=0), \
                mock.patch.object(network.proof.sys, "platform", "linux"), \
                mock.patch.object(network.proof, "check_venue") as venue:
            for profile, remaining, accepted in ((DAY, 86400, True), (DAY, 86401, False),
                    (DAY, 0, False), ("smoke", 4800, True), ("smoke", 4801, False)):
                with self.subTest(profile=profile, remaining=remaining), \
                        mock.patch.object(commands, "remaining", return_value=remaining):
                    reservation.journal = network.Journal(self.root, network_tests.network(),
                        commands.wall, commands.deadline, lifetime_profile=profile)
                    venue.reset_mock()
                    if accepted:
                        reservation.admit()
                        venue.assert_called_once()
                    else:
                        with self.assertRaises(ValueError):
                            reservation.admit()
                        venue.assert_not_called()

    def test_guard_commands_keep_original_monotonic_deadline_after_forward_jump(self):
        clock = lifecycle_tests.Clock()
        with mock.patch.object(network.smoke, "time", clock):
            commands = host.GuardCommands(None, host.environment(), clock.wall + 86400, clock.mono + 86400)
            self.assertEqual(commands.remaining(), 86400 - smoke.HOST_RESERVE)
            clock.wall += 1000
            shortened = commands.remaining()
            clock.wall -= 2000
            self.assertEqual(commands.remaining(), shortened)
            clock.mono += shortened
            self.assertEqual(commands.remaining(), 0)


class ReceiptTests(unittest.TestCase):
    def test_lifecycle_profile_deadline_source_and_failure_joins(self):
        clock = lifecycle_tests.Clock()
        lifetime = service.lifetime_record(DAY, clock.wall + 86400 - smoke.HOST_RESERVE)
        row = {**smoke_tests.lifecycle(), "schema": service.ORDINARY_SCHEMA,
               "lifetime": lifetime, "prepared_sha256": "a" * 64}
        kwargs = {"lifetime": lifetime, "prepared_sha256": "a" * 64}
        self.assertEqual(smoke.lifecycle(smoke_tests.encoded(row), **kwargs), row)
        for change in ({"lifetime": {**lifetime, "profile": "smoke"}},
                       {"lifetime": {**lifetime, "hard_stop_at": clock.timestamp(86400)}},
                       {"lifetime": {**lifetime, "admission_stop_at": clock.timestamp(86400)}},
                       {"prepared_sha256": "b" * 64}, {"schema": "nib.selected-service-lifecycle/v1"},
                       {"extra": "PRIVATE_CANARY"}, {"cleanup_confirmed": False}, {"status": "cancelled"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                smoke.lifecycle(smoke_tests.encoded({**row, **change}), **kwargs)
        with self.assertRaises(ValueError):
            smoke.lifecycle(smoke_tests.encoded(row))
        with self.assertRaises(ValueError):
            smoke.lifecycle(smoke_tests.encoded(smoke_tests.lifecycle()), **kwargs)
        with self.assertRaises(ValueError):
            smoke.lifecycle(smoke_tests.encoded(row), **kwargs, protocol_smoke=True)
        failed = {**row, "status": "failed", "reason": "deadline"}
        self.assertEqual(smoke.lifecycle_failure(smoke_tests.encoded(failed), **kwargs), failed)
        with self.assertRaises(ValueError):
            smoke.lifecycle(smoke_tests.encoded(failed), **kwargs)


class IntentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        owner_tests.Fixture.setUpClass()

    def setUp(self):
        self.fixture = owner_tests.ContractTests("test_image_labels_are_bounded_copied_and_cannot_claim_ownership")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def day(self):
        fixture = self.fixture
        old = fixture.journal.contract
        journal = network.Journal(fixture.root / "network", fixture.selected, old["hard_wall"],
                                  old["hard_monotonic"], lifetime_profile=DAY)
        (journal.root / "contract.json").write_bytes(network.policy.encoded(journal.contract))
        args = copy.copy(fixture.fixture.args)
        args.lifetime_profile = DAY
        runtime = copy.deepcopy(fixture.runtime)
        runtime["command"] = owner.container_command(fixture.selected, runtime["network_id"],
            fixture.fixture.build, fixture.fixture.base, runtime["weights"], runtime["inputs"], args,
            service.retirement_at(old["hard_wall"] - smoke.HOST_RESERVE))
        for name, value in (("contract.json", owner.owner_intent(journal)), ("runtime.json", runtime)):
            (fixture.state.root / name).write_bytes(network.policy.encoded(value))
        fixture.commands.journal = journal
        reservation = network.Reservation(fixture.commands, journal)
        return journal, reservation, runtime

    def test_default_command_and_receipt_bytes_remain_unchanged(self):
        fixture = self.fixture
        args = copy.copy(fixture.fixture.args)
        args.lifetime_profile = "smoke"
        actual = owner.container_command(fixture.selected, fixture.runtime["network_id"],
            fixture.fixture.build, fixture.fixture.base, fixture.runtime["weights"], fixture.runtime["inputs"],
            args, args.hard_stop_at)
        self.assertEqual(actual, fixture.command)
        self.assertNotIn("--lifetime-profile", actual)
        raw = smoke_tests.encoded(smoke_tests.lifecycle())
        self.assertEqual(smoke_tests.encoded(smoke.lifecycle(raw)), raw)
        self.assertEqual(owner.owner_intent(fixture.journal), fixture.contract)

    def test_day_network_container_journals_and_launch_profile_are_one_contract(self):
        journal, reservation, runtime = self.day()
        actual = owner.Owner(self.fixture.commands, reservation, self.fixture.state)
        self.assertEqual(actual.contract, owner.owner_intent(journal))
        self.assertEqual(actual.runtime, runtime)
        self.assertEqual(host.read_reservation(self.fixture.root).contract, journal.contract)
        command = runtime["command"]
        self.assertEqual(command[command.index("--lifetime-profile") + 1], DAY)
        self.assertEqual(command[command.index("--restart") + 1], "no")
        self.assertEqual(command[command.index("--dns") + 1], "127.0.0.1")

    def test_day_runtime_profile_cutoff_mode_and_schema_mismatch_rejected(self):
        journal, reservation, runtime = self.day()
        path = self.fixture.state.root / "runtime.json"
        for flag, value in (("--lifetime-profile", "smoke"), ("--lifetime-profile", "PRIVATE_CANARY"),
                            ("--hard-stop-at", service.retirement_at(journal.contract["hard_wall"]))):
            changed = copy.deepcopy(runtime)
            changed["command"][changed["command"].index(flag) + 1] = value
            with self.subTest(flag=flag, value=value):
                path.write_bytes(network.policy.encoded(changed))
                with self.assertRaises(ValueError):
                    owner.Owner(self.fixture.commands, reservation, self.fixture.state)
        for suffix in (["--lifetime-profile", DAY], ["--protocol-smoke"]):
            path.write_bytes(network.policy.encoded({**runtime, "command": runtime["command"] + suffix}))
            with self.assertRaises(ValueError):
                owner.Owner(self.fixture.commands, reservation, self.fixture.state)
        path.write_bytes(network.policy.encoded(runtime))
        wrong = copy.deepcopy(owner.owner_intent(journal))
        wrong.pop("lifetime_profile")
        wrong["schema"] = "nib.selected-service-owner-intent/v1"
        (self.fixture.state.root / "contract.json").write_bytes(network.policy.encoded(wrong))
        with self.assertRaises(ValueError):
            owner.Owner(self.fixture.commands, reservation, self.fixture.state)

    def test_smoke_network_cannot_adopt_day_container_contract(self):
        journal, _, _ = self.day()
        legacy = network.Journal(journal.root, journal.network, journal.contract["hard_wall"],
                                 journal.contract["hard_monotonic"])
        with self.assertRaises(ValueError):
            owner.Owner(self.fixture.commands, network.Reservation(self.fixture.commands, legacy), self.fixture.state)


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.clock = lifecycle_tests.Clock()
        self.lifetime = service.lifetime_record(DAY, self.clock.wall + 86400 - smoke.HOST_RESERVE)
        self.final = {**smoke_tests.lifecycle(), "schema": service.ORDINARY_SCHEMA,
                      "lifetime": self.lifetime, "prepared_sha256": "a" * 64}
        self.rows = [service.progress_record(phase, self.lifetime, "a" * 64) for phase in service.PROGRESS_PHASES]

    def test_fragmented_closed_phases_relay_live_and_terminal_is_not_relayed(self):
        wire = b"".join(smoke_tests.encoded(row) + b"\n" for row in [*self.rows, self.final])
        for width in (1, 37, len(wire)):
            with self.subTest(width=width):
                sink, output = host.AttachedProgress(self.lifetime, "a" * 64), io.StringIO()
                with redirect_stderr(output), mock.patch.object(host, "time", self.clock):
                    for offset in range(0, len(wire), width):
                        part = wire[offset:offset + width]
                        self.assertEqual(sink.write(part), len(part))
                self.assertEqual(sink.finish(success=True), smoke_tests.encoded(self.final))
                self.assertEqual([json.loads(line) for line in output.getvalue().splitlines()],
                                 [{**row, "observed_at": self.clock.timestamp(0)} for row in self.rows])

    def test_unknown_raw_output_and_mutated_metadata_are_never_forwarded(self):
        for row in ({**self.rows[0], "phase": "PRIVATE_CANARY"},
                    {**self.rows[0], "lifetime": {**self.lifetime, "profile": "smoke"}},
                    {**self.rows[0], "prepared_sha256": "b" * 64},
                    {**self.rows[0], "extra": "PRIVATE_CANARY"}, self.rows[1],
                    {"schema": "PRIVATE_CANARY"}):
            sink, output = host.AttachedProgress(self.lifetime, "a" * 64), io.StringIO()
            with self.subTest(row=row), redirect_stderr(output), self.assertRaises(ValueError):
                sink.write(smoke_tests.encoded(row) + b"\n")
            self.assertEqual(output.getvalue(), "")

    def test_duplicate_trailing_oversized_and_incomplete_progress_fail_closed(self):
        first = smoke_tests.encoded(self.rows[0]) + b"\n"
        for chunks in ((first, first), (b"x" * 16385,),
                       (smoke_tests.encoded(self.final) + b"\nPRIVATE_CANARY",)):
            sink = host.AttachedProgress(self.lifetime, "a" * 64)
            with self.subTest(chunks=len(chunks)), redirect_stderr(io.StringIO()), self.assertRaises(ValueError):
                for chunk in chunks:
                    sink.write(chunk)
        for raw in (b"", first, smoke_tests.encoded(self.final), smoke_tests.encoded(self.final) + b"\n"):
            sink = host.AttachedProgress(self.lifetime, "a" * 64)
            with redirect_stderr(io.StringIO()):
                sink.write(raw)
            with self.assertRaises(ValueError):
                sink.finish(success=True)

    def test_early_failure_preserves_terminal_but_cannot_be_success(self):
        failed = {"schema": service.ORDINARY_SCHEMA, "status": "failed", "reason": "preparation",
                  "deployable": False, "hardware_validated": False,
                  "lifetime": self.lifetime, "prepared_sha256": "a" * 64}
        sink = host.AttachedProgress(self.lifetime, "a" * 64)
        with redirect_stderr(io.StringIO()):
            sink.write(smoke_tests.encoded(self.rows[0]) + b"\n" + smoke_tests.encoded(failed) + b"\n")
        self.assertEqual(sink.finish(success=False), smoke_tests.encoded(failed))
        with self.assertRaises(ValueError):
            sink.finish(success=True)
        self.assertEqual(smoke.lifecycle_failure(sink.finish(success=False), lifetime=self.lifetime,
                                                prepared_sha256="a" * 64), failed)

    def test_day_start_reuses_bounded_process_group_and_original_remaining_budget(self):
        command = ["docker", "container", "start", "--attach", "b" * 64]
        def execute(args, **kwargs):
            self.assertEqual(args, command)
            self.assertEqual(kwargs["timeout"], 1800 - 120)
            self.assertEqual(kwargs["max_output_bytes"], 16384)
            self.assertEqual(kwargs["max_stderr_bytes"], 65536)
            for row in [*self.rows, self.final]:
                kwargs["stdout_file"].write(smoke_tests.encoded(row) + b"\n")
            return types.SimpleNamespace(returncode=0)
        runner = mock.Mock(side_effect=execute)
        commands = types.SimpleNamespace(env=host.environment(), remaining=lambda: 1800,
            recorder=types.SimpleNamespace(snapshot=types.SimpleNamespace(execute_process_group=runner)))
        with redirect_stderr(io.StringIO()):
            self.assertEqual(host.start_day(commands, command, self.lifetime, "a" * 64),
                             smoke_tests.encoded(self.final))
        runner.assert_called_once()
        runner.reset_mock(side_effect=True)
        runner.side_effect = KeyboardInterrupt("PRIVATE_CANARY")
        with self.assertRaises(KeyboardInterrupt):
            host.start_day(commands, command, self.lifetime, "a" * 64)
        runner.assert_called_once()


if __name__ == "__main__":
    unittest.main()
