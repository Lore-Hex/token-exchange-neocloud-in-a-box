# SPDX-License-Identifier: Apache-2.0
"""Pre-container recovery with real private journals and a synthetic daemon."""

from contextlib import ExitStack, redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import types
import unittest
from unittest import mock

import selected_service_network as network_module
from test_selected_service_network import FakeCommands, network

recovery = network_module.source_module("selected_recovery_test", "host/recover-selected-service.py")
policy = network_module.policy


class Commands(FakeCommands):
    def __init__(self, journal):
        super().__init__(journal)
        self.containers = b""
        self.fail_delete = None
        self.deletes = []

    def call(self, argv, **kwargs):
        if argv[:2] == ["docker", "container"]:
            self.calls.append(argv)
            assert argv[2:] == ["ls", "--all", "--quiet", "--no-trunc"]
            return self.containers
        return super().call(argv, **kwargs)

    def apply(self, batch):
        rows = policy.decode(batch)["nftables"]
        if "delete" not in rows[0]:
            return super().apply(batch)
        self.deletes.append(copy.deepcopy(rows))
        if self.fail_delete == len(self.deletes):
            raise subprocess.CalledProcessError(1, ["PRIVATE_CANARY"], output=b"PRIVATE_CANARY")
        for row in rows:
            table = row["delete"]["table"]
            self.objects = [obj for obj in self.objects if not any(
                body.get("family") == table["family"] and
                body.get("table", body.get("name")) == table["name"] for body in obj.values())]


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for name in ("network", "container"):
            (self.root / name).mkdir(mode=0o700)
        self.journal = network_module.Journal(self.root / "network", network(), 2000000000, 1000000)
        self.journal.initialize()
        self.commands = Commands(self.journal)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.admit = self.stack.enter_context(mock.patch.object(network_module.Reservation, "admit"))
        self.processes = self.stack.enter_context(mock.patch.object(recovery, "no_owner_process"))
        self.daemon = self.stack.enter_context(mock.patch.object(recovery.owner, "daemon_identity",
                                                               return_value=(123, "same")))
        network_module.Reservation(self.commands, self.journal).reserve()
        self.commands.calls.clear()

    def run_recovery(self):
        result = recovery.report()
        recovery.recover(self.root, self.journal, self.commands, result)
        return result

    def removed_network(self):
        return any(isinstance(call, list) and call[:3] == ["docker", "network", "rm"]
                   for call in self.commands.calls)

    def test_resolved_empty_network_then_exact_policy_and_quarantine_removal(self):
        unrelated = {"table": {"family": "inet", "name": "foreign"}}
        self.commands.objects.append(unrelated)
        original = (self.journal.root / "contract.json").read_bytes()
        result = self.run_recovery()
        self.assertEqual(result["result"], "pass")
        for key in ("container_absent", "network_absent", "creations_resolved", "quarantine_confirmed",
                    "policy_removed", "quarantine_removed", "cleanup_confirmed"):
            self.assertIs(result[key], True)
        for key in ("runtime_started", "resources_created", "original_run_pass", "deployable",
                    "hardware_validated", "confidential"):
            self.assertIs(result[key], False)
        self.assertEqual(self.commands.objects, [unrelated])
        self.assertEqual((self.journal.root / "contract.json").read_bytes(), original)
        self.assertEqual(list(self.journal.read()), list(network_module.EVENTS))
        self.assertEqual(self.commands.deletes, [
            policy.decode(network().policy.remove_batch(containers_absent=True, creations_resolved=True))["nftables"],
            policy.decode(policy.Quarantine(network().policy).remove_batch(
                containers_absent=True, networks_absent=True, creations_resolved=True))["nftables"]])
        self.assertTrue(self.removed_network())
        self.assertGreaterEqual(self.processes.call_count, 5)
        self.assertTrue(all(not isinstance(call, list) or not any(
            word in call for word in ("create", "start", "kill", "--force")) for call in self.commands.calls))
        self.assertEqual(list((self.root / "container").iterdir()), [])

    def test_every_container_state_file_even_empty_or_unknown_refuses_mutation(self):
        for name in (*recovery.owner.FILES, "unknown", "PRIVATE_CANARY"):
            with self.subTest(name=name):
                path = self.root / "container" / name
                path.touch(mode=0o600)
                with self.assertRaises(ValueError):
                    self.run_recovery()
                path.unlink()
                self.assertFalse(self.removed_network())
                self.assertFalse(self.commands.deletes)

    def test_unresolved_or_previously_removed_network_is_not_adopted(self):
        path = self.journal.root / "network-resolved.json"
        raw = path.read_bytes()
        path.unlink()
        with self.assertRaises(ValueError):
            self.run_recovery()
        path.write_bytes(raw)
        path.chmod(0o600)
        self.journal.record("network-removed")
        with self.assertRaises(ValueError):
            self.run_recovery()
        self.assertFalse(self.removed_network())
        self.assertFalse(self.commands.deletes)

    def test_absent_renamed_replaced_or_ambiguous_network_refuses_removal(self):
        for rows in ([], [{"ID": "b" * 64, "Name": "renamed"}],
                     [{"ID": "c" * 64, "Name": network().name}],
                     [{"ID": "b" * 64, "Name": network().name}, {"ID": "c" * 64, "Name": network().name}]):
            with self.subTest(rows=rows):
                self.commands.rows = rows
                with self.assertRaises(ValueError):
                    self.run_recovery()
                self.assertFalse(self.removed_network())
                self.assertFalse(self.commands.deletes)

    def test_attached_endpoint_or_network_drift_keeps_quarantine(self):
        before = copy.deepcopy(self.commands.info)
        for field, value in (("Containers", {"c" * 64: {}}), ("Labels", {}),
                             ("Options", {}), ("IPAM", {})):
            with self.subTest(field=field):
                self.commands.info = {**before, field: value}
                with self.assertRaises(ValueError):
                    self.run_recovery()
                self.assertFalse(self.removed_network())
                self.assertFalse(self.commands.deletes)

    def test_all_state_container_inventory_blocks_owned_renamed_stopped_and_foreign(self):
        for raw in (b"c" * 64, b"c" * 64 + b"\n" + b"d" * 64, b"PRIVATE_CANARY"):
            with self.subTest(raw=raw):
                self.commands.containers = raw
                with self.assertRaises(ValueError):
                    self.run_recovery()
                self.assertFalse(self.removed_network())
                self.assertFalse(self.commands.deletes)

    def test_cancellation_after_network_removal_never_releases_tables(self):
        original = self.commands.call
        def call(argv, **kwargs):
            raw = original(argv, **kwargs)
            if argv[:3] == ["docker", "network", "rm"]:
                raise KeyboardInterrupt("PRIVATE_CANARY")
            return raw
        self.commands.call = call
        with self.assertRaises(KeyboardInterrupt):
            self.run_recovery()
        self.assertFalse(self.commands.deletes)
        self.assertNotIn("network-removed", self.journal.read())

    def test_failed_quarantine_removal_never_confirms_cleanup(self):
        self.commands.fail_delete = 2
        result = recovery.report()
        with self.assertRaises(subprocess.CalledProcessError):
            recovery.recover(self.root, self.journal, self.commands, result)
        self.assertTrue(result["policy_removed"])
        self.assertFalse(result["quarantine_removed"])
        self.assertFalse(result["cleanup_confirmed"])
        self.assertEqual(result["result"], "fail")

    def test_successful_delete_exit_without_actual_removal_fails_readback(self):
        self.commands.apply = lambda _: None
        with self.assertRaises(ValueError):
            self.run_recovery()
        network_module.Reservation(self.commands, self.journal).quarantined()

    def test_host_failure_active_owner_daemon_drift_or_budget_blocks_mutation(self):
        for target in (self.admit, self.processes, self.daemon):
            with self.subTest(target=target):
                target.side_effect = ValueError("PRIVATE_CANARY")
                with self.assertRaises(ValueError):
                    self.run_recovery()
                target.side_effect = None
                self.assertFalse(self.removed_network())
        self.daemon.side_effect = [(123, "same"), (123, "changed")]
        with self.assertRaises(ValueError):
            self.run_recovery()
        self.daemon.side_effect = None
        with mock.patch.object(self.commands, "remaining", return_value=25), self.assertRaises(ValueError):
            self.run_recovery()
        self.assertFalse(self.removed_network())
        self.assertFalse(self.commands.deletes)

    def test_late_container_state_cannot_release_quarantine(self):
        original = self.commands.call
        def call(argv, **kwargs):
            raw = original(argv, **kwargs)
            if argv[:3] == ["docker", "network", "rm"]:
                (self.root / "container" / "container-intent.json").touch(mode=0o600)
            return raw
        self.commands.call = call
        with self.assertRaises(ValueError):
            self.run_recovery()
        self.assertTrue(self.removed_network())
        self.assertFalse(self.commands.deletes)

    def test_journal_contract_or_resolved_id_drift_cannot_release_tables(self):
        for name in ("contract.json", "network-resolved.json"):
            path = self.journal.root / name
            original = path.read_bytes()
            def changed(_):
                row = policy.decode(original)
                row["hard_wall" if name == "contract.json" else "id"] = (
                    2000000001 if name == "contract.json" else "c" * 64)
                path.write_bytes(policy.encoded(row))
            self.processes.side_effect = changed
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.run_recovery()
            path.write_bytes(original)
            self.assertFalse(self.removed_network())
            self.assertFalse(self.commands.deletes)

    def test_unknown_listing_remove_or_post_remove_absence_retains_tables(self):
        for failure in ("list", "remove"):
            self.commands.failure = failure
            with self.assertRaises(subprocess.CalledProcessError):
                self.run_recovery()
            self.assertFalse(self.commands.deletes)
        self.commands.failure = None
        original = self.commands.call
        def call(argv, **kwargs):
            if argv[:3] == ["docker", "network", "ls"] and "--filter" in argv:
                return b"b" * 64
            return original(argv, **kwargs)
        self.commands.call = call
        with self.assertRaises(ValueError):
            self.run_recovery()
        self.assertFalse(self.commands.deletes)

    def test_policy_drift_or_unknown_removal_never_removes_quarantine(self):
        before = copy.deepcopy(self.commands.objects)
        self.commands.objects.pop()
        with self.assertRaises(ValueError):
            self.run_recovery()
        self.assertFalse(self.removed_network())
        self.commands.objects = before
        self.commands.fail_delete = 1
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_recovery()
        self.assertEqual(len(self.commands.deletes), 1)
        network_module.Reservation(self.commands, self.journal).quarantined()

    def test_bad_private_paths_modes_unknown_root_entries_are_rejected(self):
        for target in (self.root, self.root / "container"):
            target.chmod(0o755)
            with self.assertRaises(ValueError):
                recovery.empty_state(self.root)
            target.chmod(0o700)
        (self.root / "extra").touch()
        with self.assertRaises(ValueError):
            recovery.empty_state(self.root)
        (self.root / "extra").unlink()
        (self.root / "container").rmdir()
        (self.root / "container").symlink_to(self.root / "network", target_is_directory=True)
        with self.assertRaises(OSError):
            recovery.empty_state(self.root)


class BoundaryTests(unittest.TestCase):
    def test_fixed_budget_clipped_to_original_wall_monotonic_and_never_extended(self):
        journal = network_module.Journal("/not-read", network(), 1200, 300)
        with mock.patch.object(recovery.time, "time", return_value=1000) as wall, \
                mock.patch.object(recovery.time, "monotonic", return_value=100) as monotonic:
            commands = recovery.Commands(None, journal, 340)
            self.assertEqual(commands.remaining(), 200)
            wall.return_value = 1190
            self.assertEqual(commands.remaining(), 10)
            wall.return_value = 1000
            self.assertEqual(commands.remaining(), 10)
            monotonic.return_value = 111
            self.assertLess(commands.remaining(), 0)
        journal = network_module.Journal("/not-read", network(), 2000, 1100)
        with mock.patch.object(recovery.time, "time", return_value=1000), \
                mock.patch.object(recovery.time, "monotonic", return_value=100):
            commands = recovery.Commands(None, journal, 340)
            self.assertEqual(commands.remaining(), 240)
            self.assertEqual(commands.end, 340)

    def test_subprocess_reserve_fits_budget_and_rejects_unknown_exit(self):
        execute = mock.Mock(return_value=subprocess.CompletedProcess([], 0, b"{}", b""))
        recorder = types.SimpleNamespace(snapshot=types.SimpleNamespace(execute_process_group=execute))
        journal = network_module.Journal("/not-read", network(), 2000, 1100)
        with mock.patch.object(recovery.time, "time", return_value=1000), \
                mock.patch.object(recovery.time, "monotonic", return_value=100):
            commands = recovery.Commands(recorder, journal, 130)
            commands.run(["nft", "--json", "list", "ruleset"])
            self.assertEqual(execute.call_args.kwargs["timeout"], 5)
            execute.return_value.returncode = None
            with self.assertRaises(ValueError):
                commands.run(["nft", "--json", "list", "ruleset"])
            commands.recovery_end = 125
            execute.reset_mock()
            with self.assertRaises(ValueError):
                commands.run(["nft", "--json", "list", "ruleset"])
            execute.assert_not_called()

    def test_proc_inventory_is_bounded_and_rejects_live_owner_or_watchdog(self):
        commands = types.SimpleNamespace(end=time.monotonic() + 10)
        entries = [types.SimpleNamespace(name="123", path="/proc/123")]
        cases = (b"python3\0-I\0/committed/host/record-selected-service.py\0--run\0",
                 b"python3\0-I\0/committed/host/record-selected-service.py\0--watchdog\0")
        for raw in (*cases, b"x" * 65537):
            with mock.patch.object(recovery.os, "scandir") as scan, \
                    mock.patch("builtins.open", return_value=io.BytesIO(raw)), self.assertRaises(ValueError):
                scan.return_value.__enter__.return_value = iter(entries)
                recovery.no_owner_process(commands)
        for raw in (b"", b"python3\0/other.py\0"):
            with mock.patch.object(recovery.os, "scandir") as scan, \
                    mock.patch("builtins.open", return_value=io.BytesIO(raw)):
                scan.return_value.__enter__.return_value = iter(entries)
                recovery.no_owner_process(commands)
        with mock.patch.object(recovery.os, "scandir") as scan, \
                mock.patch("builtins.open", side_effect=PermissionError("PRIVATE_CANARY")), \
                self.assertRaises(PermissionError):
            scan.return_value.__enter__.return_value = iter(entries)
            recovery.no_owner_process(commands)

    def test_cli_never_exposes_exception_chain_or_private_inputs_and_restores_handlers(self):
        before = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        argv = ["--cleanup-only", "--state-dir", "/PRIVATE_CANARY", "--hard-stop-at", "2026-10-25T06:15:00Z"]
        for error in (ValueError("PRIVATE_CANARY"), KeyboardInterrupt("PRIVATE_CANARY"), SystemExit("PRIVATE_CANARY")):
            output = io.StringIO()
            with mock.patch.object(recovery, "execute", side_effect=error), redirect_stdout(output):
                self.assertEqual(recovery.main(argv), 1)
            self.assertNotIn("PRIVATE_CANARY", output.getvalue())
            self.assertFalse(json.loads(output.getvalue())["cleanup_confirmed"])
            self.assertEqual({sig: signal.getsignal(sig) for sig in before}, before)
        for extra in (["--run"], ["--budget", "241"], ["--docker-host", "tcp://PRIVATE_CANARY"]):
            output = io.StringIO()
            with mock.patch.object(recovery, "execute") as execute, redirect_stdout(output):
                self.assertEqual(recovery.main(argv + extra), 1)
                execute.assert_not_called()
            self.assertNotIn("PRIVATE_CANARY", output.getvalue())

    def test_execute_rejects_nonroot_nonlinux_and_extended_original_cutoff(self):
        args = types.SimpleNamespace(state_dir="/unused", hard_stop_at="2026-10-25T06:15:00Z")
        for uid, platform in ((501, "linux"), (0, "darwin")):
            with mock.patch.object(recovery.os, "getuid", return_value=uid), \
                    mock.patch.object(recovery.sys, "platform", platform), self.assertRaises(ValueError):
                recovery.execute(args, recovery.report(), time.monotonic() + 240)
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            args.state_dir = str(Path(directory).resolve())
            stack.enter_context(mock.patch.object(recovery.os, "getuid", return_value=0))
            stack.enter_context(mock.patch.object(recovery.sys, "platform", "linux"))
            stack.enter_context(mock.patch.object(recovery, "empty_state"))
            stack.enter_context(mock.patch.object(recovery.host, "read_reservation",
                return_value=types.SimpleNamespace(contract={"hard_wall": 1})))
            recorder = stack.enter_context(mock.patch.object(recovery.smoke, "host_recorder"))
            with self.assertRaises(ValueError):
                recovery.execute(args, recovery.report(), time.monotonic() + 240)
            recorder.assert_not_called()

    def test_concurrent_recovery_lock_blocks_before_read_or_commands(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory).resolve()
            args = types.SimpleNamespace(state_dir=str(root), hard_stop_at="2026-10-25T06:15:00Z")
            stack.enter_context(mock.patch.object(recovery.os, "getuid", return_value=0))
            stack.enter_context(mock.patch.object(recovery.sys, "platform", "linux"))
            read = stack.enter_context(mock.patch.object(recovery.host, "read_reservation"))
            recorder = stack.enter_context(mock.patch.object(recovery.smoke, "host_recorder"))
            with network_module.tau_runtime.directory(root) as locked:
                recovery.fcntl.flock(locked, recovery.fcntl.LOCK_EX | recovery.fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):
                    recovery.execute(args, recovery.report(), time.monotonic() + 240)
            read.assert_not_called()
            recorder.assert_not_called()

    def test_real_closed_venue_source_admission_failure_precedes_mutations(self):
        journal = network_module.Journal("/not-read", network(), 2000000000, 1000000)
        commands = Commands(journal)
        with mock.patch.object(network_module.os, "getuid", return_value=0), \
                mock.patch.object(network_module.proof.sys, "platform", "linux"), \
                mock.patch.object(network_module.proof, "check_venue", side_effect=ValueError("PRIVATE_CANARY")) as check, \
                self.assertRaises(ValueError):
            recovery.recover(Path("/not-read"), journal, commands, recovery.report())
        check.assert_called_once_with(commands, network_module.proof.ORDINARY_VENUE)
        self.assertEqual(commands.calls, [])


if __name__ == "__main__":
    unittest.main()
