# SPDX-License-Identifier: Apache-2.0
"""Network ownership and durable intent only; never Docker/nft/socket execution."""

import copy
from dataclasses import replace
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import types
import unittest
from unittest import mock

import selected_service_network as owner
from test_selected_service import Clock


def network():
    return owner.Network(owner.policy.Policy("a" * 32, "nib" + "a" * 12,
        "approved0", "172.29.1.2", "192.0.2.10", 443, "1.1.6"), "172.29.1.0/24", "172.29.1.1")


def inspection(selected, identifier="b" * 64):
    return {"Id": identifier, "Name": selected.name, "Labels": {owner.LABEL: selected.policy.token},
        "Driver": "bridge", "Scope": "local", "EnableIPv4": True, "EnableIPv6": False,
        "Internal": False, "Attachable": False, "Ingress": False, "ConfigOnly": False,
        "ConfigFrom": {"Network": ""}, "Options": selected.options,
        "IPAM": {"Driver": "default", "Options": {},
                 "Config": [{"Subnet": selected.subnet, "IPRange": "", "Gateway": selected.gateway}]}, "Containers": {}}


class FakeCommands(owner.Commands):
    def __init__(self, journal):
        self.journal = journal
        self.wall, self.deadline = journal.contract["hard_wall"], journal.contract["hard_monotonic"]
        self.env = {**owner.proof.ENV, "HOME": "/nonexistent",
                    "DOCKER_CONFIG": "/nonexistent/nib-lab-docker-config",
                    "DOCKER_HOST": "unix:///var/run/docker.sock"}
        self.rows, self.calls, self.objects, self.links = [], [], [], []
        self.failure = None
        self.info = inspection(journal.network)
        self.version = {"Version": "29.1.3", "Os": "linux", "Arch": "amd64"}
        self.nft_version = b"nftables v1.1.6 (Commodore Bullmoose #7)\n"

    def remaining(self):
        return 900

    def rules(self, selected):
        self.calls.append("readback")
        return owner.policy.encoded({"nftables": [{"metainfo": {"version": "1.1.6",
            "release_name": "Commodore Bullmoose #7", "json_schema_version": 1}}, *self.objects]})

    def apply(self, batch):
        decoded = owner.policy.decode(batch)["nftables"]
        first = decoded[0]["create"]["table"]["name"]
        quarantine = first.startswith("nib_quarantine_")
        kind = "quarantine" if quarantine else "policy"
        assert list(self.journal.read())[-1] == kind + "-intent"
        self.calls.append(kind)
        if self.failure == kind:
            raise subprocess.TimeoutExpired(["PRIVATE_CANARY"], 1)
        selected = owner.policy.Quarantine(self.journal.network.policy) if quarantine else self.journal.network.policy
        self.objects += selected.objects(canonical=True)

    def run(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[:2] == ["ip", "--json"]:
            return types.SimpleNamespace(stdout=owner.policy.encoded(self.links))
        if argv == ["nft", "--version"]:
            return types.SimpleNamespace(stdout=self.nft_version)
        if argv[:2] == ["docker", "version"]:
            return types.SimpleNamespace(stdout=owner.policy.encoded(self.version))
        raise AssertionError(argv)

    def call(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[:3] == ["docker", "network", "create"]:
            assert list(self.journal.read())[-1] == "network-intent"
            if self.failure == "create-before":
                raise KeyboardInterrupt("PRIVATE_CANARY")
            self.rows = [{"ID": self.info["Id"], "Name": self.info["Name"]}]
            if self.failure == "create-after":
                raise SystemExit("PRIVATE_CANARY")
            if self.failure == "bad-id":
                return b"PRIVATE_CANARY"
            return (self.info["Id"] + "\n").encode()
        if argv[:3] == ["docker", "network", "ls"]:
            if self.failure == "list":
                raise subprocess.CalledProcessError(1, argv, output=b"PRIVATE_CANARY")
            if "--quiet" in argv:
                return b"\n".join(row["ID"].encode() for row in self.rows if row["ID"] == argv[-1][3:])
            return b"\n".join(owner.policy.encoded(row) for row in self.rows)
        if argv[:3] == ["docker", "network", "inspect"]:
            return owner.policy.encoded(self.info)
        if argv[:3] == ["docker", "network", "rm"]:
            assert argv[3] == self.info["Id"]
            if self.failure == "remove":
                raise subprocess.CalledProcessError(1, argv, output=b"PRIVATE_CANARY")
            self.rows = []
            return b""
        raise AssertionError(argv)


class NetworkTests(unittest.TestCase):
    def test_closed_command_binds_exact_ipv4_network_and_labels(self):
        selected = network()
        command = selected.command()
        self.assertEqual(command[:3], ["docker", "network", "create"])
        self.assertEqual(command[-1], selected.name)
        for flag, value in (("--subnet", selected.subnet), ("--gateway", selected.gateway),
                            ("--driver", "bridge"), ("--scope", "local")):
            self.assertEqual(command[command.index(flag) + 1], value)
        self.assertIn("--ipv4=true", command)
        self.assertIn("--ipv6=false", command)
        self.assertNotIn("--internal", command)
        self.assertNotIn("--publish", command)
        self.assertNotIn("start", command)
        self.assertEqual([command[i + 1] for i, item in enumerate(command) if item == "--opt"],
                         [key + "=" + value for key, value in sorted(selected.options.items())])

    def test_subnet_gateway_address_bridge_and_version_are_closed(self):
        selected = network()
        for changes in ({"subnet": "172.29.1.1/24"}, {"subnet": "0.0.0.0/24"},
                {"subnet": "192.0.2.0/24"}, {"subnet": "172.29.0.0/16"},
                {"subnet": "172.29.1.0/31"}, {"subnet": "::/24"}, {"subnet": 123},
                {"gateway": "172.29.1.2"}, {"gateway": "172.29.1.0"}, {"gateway": "172.29.1.255"},
                {"gateway": "172.29.2.1"}, {"gateway": "172.029.1.1"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(selected, **changes)
        for changes in ({"bridge": "unowned"}, {"nft_version": "1.0.9"},
                        {"address": "172.29.1.0"}, {"published": "172.29.1.10"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(selected, policy=replace(selected.policy, **changes))

    def test_exact_network_inspection_does_not_mutate(self):
        row = inspection(network())
        before = copy.deepcopy(row)
        network().check(row, row["Id"])
        self.assertEqual(row, before)

    def test_unset_ip_range_may_be_omitted_or_empty(self):
        for omitted in (False, True):
            with self.subTest(omitted=omitted):
                row = inspection(network())
                if omitted:
                    del row["IPAM"]["Config"][0]["IPRange"]
                before = copy.deepcopy(row)
                network().check(row, row["Id"])
                self.assertEqual(row, before)

    def test_ipam_remains_exact_except_unset_range_representation(self):
        selected = network()
        for value in (None, False, 0, [], {}, "0.0.0.0/0", selected.subnet,
                      "172.29.1.0/25", "PRIVATE_CANARY"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                row = inspection(selected)
                row["IPAM"]["Config"][0]["IPRange"] = value
                selected.check(row, row["Id"])
        for field, value in (("Subnet", "172.29.2.0/24"), ("Gateway", "172.29.1.2"),
                             ("AuxiliaryAddresses", {}), ("extra", "PRIVATE_CANARY")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                row = inspection(selected)
                row["IPAM"]["Config"][0][field] = value
                selected.check(row, row["Id"])
        for config in (None, {}, [], [None], [inspection(selected)["IPAM"]["Config"][0]] * 2):
            with self.subTest(config=config), self.assertRaises(ValueError):
                row = inspection(selected)
                row["IPAM"]["Config"] = config
                selected.check(row, row["Id"])

    def test_every_network_admission_field_is_required(self):
        for field in inspection(network()):
            row = inspection(network())
            del row[field]
            with self.subTest(missing=field), self.assertRaises((ValueError, KeyError)):
                network().check(row, "b" * 64)

    def test_network_drift_and_unrelated_attachments_are_rejected(self):
        for field, value in (("Id", "c" * 64), ("Name", "other"), ("Labels", {}),
                ("Driver", "overlay"), ("Scope", "swarm"), ("EnableIPv4", 1), ("EnableIPv6", True),
                ("Internal", True), ("Attachable", True), ("Ingress", True), ("ConfigOnly", True),
                ("ConfigFrom", {"Network": "other"}), ("Options", {}), ("IPAM", {}),
                ("Containers", {"c" * 64: {}}), ("Peers", [{}]), ("Services", {"other": {}})):
            with self.subTest(field=field), self.assertRaises(ValueError):
                network().check({**inspection(network()), field: value}, "b" * 64)
        for scope in ("Options", "IPAM"):
            row = inspection(network())
            row[scope]["extra"] = "PRIVATE_CANARY"
            with self.assertRaises(ValueError):
                network().check(row, "b" * 64)


class JournalFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.journal = owner.Journal(self.root, network(), 2000000000, 1000000)
        self.journal.initialize()


class JournalTests(JournalFixture, unittest.TestCase):
    def test_modes_roundtrip_and_original_deadline_binding(self):
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.root / "contract.json").stat().st_mode), 0o600)
        self.assertEqual(self.journal.read(), {})
        self.journal.record("quarantine-intent")
        self.assertEqual(list(self.journal.read()), ["quarantine-intent"])
        with self.assertRaises(ValueError):
            owner.Journal(self.root, network(), 2000000001, 1000000).read()

    def test_no_overwrite_skips_retries_or_arbitrary_event_fields(self):
        with self.assertRaises(ValueError):
            self.journal.initialize()
        for event in ("unknown", "network-resolved", "policy-intent"):
            with self.assertRaises(ValueError):
                self.journal.record(event)
        self.journal.record("quarantine-intent")
        with self.assertRaises(ValueError):
            self.journal.record("quarantine-intent")
        with self.assertRaises(ValueError):
            self.journal.record("quarantine-applied", "b" * 64)

    def test_every_event_fsyncs_file_then_directory(self):
        real = os.fsync
        modes = []
        def sync(fd):
            modes.append(stat.S_IFMT(os.fstat(fd).st_mode))
            real(fd)
        with mock.patch.object(owner.os, "fsync", side_effect=sync):
            self.journal.record("quarantine-intent")
        self.assertEqual(modes, [stat.S_IFREG, stat.S_IFDIR])

    def test_unsafe_files_duplicate_keys_and_partial_writes_fail_closed(self):
        path = self.root / "quarantine-intent.json"
        for raw in (b"", b'{"event":', b'{"event":"quarantine-intent","event":"quarantine-intent"}',
                    b'{"event":"quarantine-intent","extra":"PRIVATE_CANARY"}'):
            path.write_bytes(raw)
            path.chmod(0o600)
            with self.assertRaises((ValueError, OSError)):
                self.journal.read()
        path.unlink()
        path.symlink_to(self.root / "contract.json")
        with self.assertRaises(OSError):
            self.journal.read()
        path.unlink()
        os.link(self.root / "contract.json", path)
        with self.assertRaises(ValueError):
            self.journal.read()

    def test_nonprivate_directory_files_and_unknown_inventory_rejected(self):
        for target, mode in ((self.root, 0o755), (self.root / "contract.json", 0o644)):
            old = stat.S_IMODE(target.stat().st_mode)
            target.chmod(mode)
            with self.assertRaises(ValueError):
                self.journal.read()
            target.chmod(old)
        (self.root / "unexpected").touch()
        with self.assertRaises(ValueError):
            self.journal.read()

    def test_symlinked_parent_and_file_owner_rejected(self):
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            owner.Journal(alias, network(), 2000000000, 1000000).read()
        alias.unlink()
        with mock.patch.object(owner.os, "getuid", return_value=os.getuid() + 1), self.assertRaises(ValueError):
            self.journal.read()


class ReservationTests(JournalFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.commands = FakeCommands(self.journal)
        self.reservation = owner.Reservation(self.commands, self.journal)
        patch = mock.patch.object(owner.Reservation, "admit")
        self.admit = patch.start()
        self.addCleanup(patch.stop)

    def test_reserve_orders_durable_intents_before_each_daemon_call(self):
        identifier = self.reservation.reserve()
        self.assertEqual(identifier, "b" * 64)
        self.assertEqual(list(self.journal.read()), list(owner.EVENTS[:-1]))
        self.assertEqual(self.commands.calls.count("quarantine"), 1)
        self.assertEqual(self.commands.calls.count("policy"), 1)
        self.assertEqual(sum(call[:3] == ["docker", "network", "create"]
                             for call in self.commands.calls if isinstance(call, list)), 1)
        self.assertTrue(all(not isinstance(call, list) or "container" not in call for call in self.commands.calls))
        with self.assertRaises(ValueError):
            self.reservation.reserve()

    def test_cleanup_resolved_network_uses_exact_id_and_retains_both_tables(self):
        self.reservation.reserve()
        before = copy.deepcopy(self.commands.objects)
        result = self.reservation.cleanup()
        self.assertTrue(result["network_cleanup_confirmed"])
        self.assertTrue(result["quarantine_retained"])
        self.assertEqual(self.commands.objects, before)
        self.assertEqual(list(self.journal.read()), list(owner.EVENTS))
        for key in ("runtime_started", "published", "deployable", "hardware_validated", "confidential"):
            self.assertIs(result[key], False)

    def test_interrupt_before_or_after_create_never_resolves_from_absence(self):
        for failure in ("create-before", "create-after", "bad-id"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as root:
                journal = owner.Journal(Path(root).resolve(), network(), 2000000000, 1000000)
                journal.initialize()
                commands = FakeCommands(journal)
                reservation = owner.Reservation(commands, journal)
                commands.failure = failure
                with self.assertRaises((KeyboardInterrupt, SystemExit, ValueError)):
                    reservation.reserve()
                self.assertEqual(list(journal.read())[-1], "network-intent")
                commands.failure = None
                result = reservation.cleanup()
                self.assertFalse(result["create_resolved"])
                self.assertFalse(result["network_cleanup_confirmed"])
                self.assertTrue(result["quarantine_retained"])
                self.assertNotIn("network-resolved", journal.read())

    def test_collision_no_create_or_policy_action(self):
        self.commands.rows = [{"ID": "c" * 64, "Name": network().name}]
        with self.assertRaises(ValueError):
            self.reservation.reserve()
        self.assertEqual(self.journal.read(), {})
        self.assertEqual(self.commands.objects, [])

    def test_existing_bridge_or_owned_table_is_not_adopted(self):
        self.commands.links = [{"ifname": network().policy.bridge}]
        with self.assertRaises(ValueError):
            self.reservation.reserve()
        self.commands.links = []
        self.commands.objects = network().policy.objects(canonical=True)
        with self.assertRaises(ValueError):
            self.reservation.reserve()
        self.assertEqual(self.journal.read(), {})

    def test_ambiguous_duplicate_names_or_unbounded_inventory_rejected(self):
        for rows in ([{"ID": "b" * 64, "Name": network().name}, {"ID": "c" * 64, "Name": network().name}],
                     [{"ID": "b" * 64, "Name": "other"}] * 257):
            self.commands.rows = rows
            with self.assertRaises(ValueError):
                self.reservation.inventory()

    def test_quarantine_or_serving_apply_failure_prevents_network_create(self):
        for failure in ("quarantine", "policy"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as root:
                journal = owner.Journal(Path(root).resolve(), network(), 2000000000, 1000000)
                journal.initialize()
                commands = FakeCommands(journal)
                commands.failure = failure
                with self.assertRaises(subprocess.TimeoutExpired):
                    owner.Reservation(commands, journal).reserve()
                self.assertNotIn("network-intent", journal.read())
                self.assertEqual(commands.rows, [])

    def test_journal_sync_failure_prevents_create(self):
        original = self.journal.record
        def record(event, identifier=None):
            if event == "network-intent":
                raise OSError("PRIVATE_CANARY")
            original(event, identifier)
        with mock.patch.object(self.journal, "record", side_effect=record), self.assertRaises(OSError):
            self.reservation.reserve()
        self.assertEqual(self.commands.rows, [])

    def test_unrelated_or_drifted_network_never_removed(self):
        self.reservation.reserve()
        for changes in ({"Labels": {}}, {"Id": "c" * 64}, {"Containers": {"c" * 64: {}}},
                        {"Options": {"private": "PRIVATE_CANARY"}}):
            self.commands.info = {**inspection(network()), **changes}
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.reservation.cleanup()
        self.assertFalse(any(isinstance(call, list) and call[:3] == ["docker", "network", "rm"]
                             for call in self.commands.calls))

    def test_quarantine_readback_drift_blocks_teardown(self):
        self.reservation.reserve()
        del self.commands.objects[2]
        with self.assertRaises(ValueError):
            self.reservation.cleanup()
        self.assertTrue(self.commands.rows)

    def test_serving_drift_does_not_prevent_independent_quarantine_cleanup(self):
        self.reservation.reserve()
        self.commands.objects = self.reservation.quarantine.objects(canonical=True)
        result = self.reservation.cleanup()
        self.assertTrue(result["network_cleanup_confirmed"])
        self.assertTrue(result["quarantine_retained"])

    def test_failed_inventory_removal_or_renamed_original_does_not_prove_absence(self):
        self.reservation.reserve()
        for failure in ("list", "remove"):
            self.commands.failure = failure
            with self.assertRaises(subprocess.CalledProcessError):
                self.reservation.cleanup()
        self.commands.failure = None
        self.commands.rows[0]["Name"] = "renamed"
        with self.assertRaises(ValueError):
            self.reservation.cleanup()
        self.assertNotIn("network-removed", self.journal.read())


class AdmissionTests(JournalFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.commands = FakeCommands(self.journal)
        self.reservation = owner.Reservation(self.commands, self.journal)

    def test_closed_host_and_daemon_checks_precede_mutation(self):
        with mock.patch.object(owner.os, "getuid", return_value=0), \
                mock.patch.object(owner.proof.sys, "platform", "linux"), \
                mock.patch.object(owner.proof, "check_venue") as venue:
            self.reservation.admit()
            venue.assert_called_once_with(self.commands, "ubuntu26-ordinary")
            for field, value in (("Version", "29.4.0"), ("Os", "other"), ("Arch", "arm64")):
                self.commands.version = {"Version": "29.1.3", "Os": "linux", "Arch": "amd64", field: value}
                with self.assertRaises(ValueError):
                    self.reservation.admit()
            self.commands.version = {"Version": "29.1.3", "Os": "linux", "Arch": "amd64"}
            self.commands.nft_version = b"nftables v1.0.9 (Old Doc Yak #3)"
            with self.assertRaises(ValueError):
                self.reservation.admit()

    def test_no_remote_daemon_ambient_environment_or_extended_deadline(self):
        for env in ({**self.commands.env, "DOCKER_HOST": "tcp://PRIVATE_CANARY:2375"},
                    {**self.commands.env, "LD_PRELOAD": "PRIVATE_CANARY"}):
            self.commands.env = env
            with mock.patch.object(owner.os, "getuid", return_value=0), \
                    mock.patch.object(owner.proof.sys, "platform", "linux"), self.assertRaises(ValueError):
                self.reservation.admit()
        self.commands.wall += 1
        with self.assertRaises(ValueError):
            owner.Reservation(self.commands, self.journal)

    def test_bounded_executor_checks_real_status_and_preserves_original_deadline(self):
        clock = Clock()
        execute = mock.Mock(return_value=subprocess.CompletedProcess([], 0, b"{}", b""))
        recorder = types.SimpleNamespace(snapshot=types.SimpleNamespace(execute_process_group=execute))
        commands = owner.Commands(recorder, {}, clock.wall + 100, clock.monotonic() + 100)
        with mock.patch.object(owner.smoke.time, "time", side_effect=lambda: clock.wall), \
                mock.patch.object(owner.smoke.time, "monotonic", side_effect=clock.monotonic):
            commands.run(["nft", "--json", "list", "ruleset"])
            self.assertEqual(execute.call_args.kwargs["timeout"], 10)
            self.assertEqual(execute.call_args.kwargs["max_output_bytes"], 262144)
            for status in (1, -9, None, False):
                execute.return_value.returncode = status
                with self.assertRaises(ValueError):
                    commands.run(["nft", "--json", "list", "ruleset"])
            clock.wall += 80
            with self.assertRaises(ValueError):
                commands.run(["nft", "--json", "list", "ruleset"])


if __name__ == "__main__":
    unittest.main()
