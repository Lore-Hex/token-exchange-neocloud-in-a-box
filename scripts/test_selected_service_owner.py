# SPDX-License-Identifier: Apache-2.0
"""Synthetic daemon, kernel and process observations. No sockets or Docker."""

from contextlib import ExitStack, contextmanager, nullcontext, redirect_stdout
import copy
from dataclasses import asdict
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import stat
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import selected_service_owner as owner
from test_selected_service_network import FakeCommands, network
import test_selected_service_smoke as smoke_tests

host = owner.network.source_module("finite_service_host_test", "host/record-selected-service.py")


def endpoint(selected):
    return {"IPAMConfig": {"IPv4Address": selected.policy.address, "IPv6Address": ""},
        "Links": None, "Aliases": None, "DriverOpts": None, "GwPriority": 0,
        "NetworkID": "", "EndpointID": "", "Gateway": "", "IPAddress": "", "MacAddress": "",
        "IPPrefixLen": 0, "IPv6Gateway": "", "GlobalIPv6Address": "", "GlobalIPv6PrefixLen": 0,
        "DNSNames": None}


class Commands(FakeCommands):
    def __init__(self, journal, fixture):
        super().__init__(journal)
        self.fixture, self.container, self.state = fixture, None, None
        self.addresses = [{"ifname": journal.network.policy.ingress, "flags": ["UP"], "addr_info": [
            {"family": "inet", "local": journal.network.policy.published, "prefixlen": 24, "scope": "global"}]}]
        self.routes = [{"dst": "default", "dev": journal.network.policy.ingress}]
        self.fail_container, self.fail_quarantine = None, False

    def run(self, argv, **kwargs):
        if argv == ["ip", "--json", "address", "show"]:
            return types.SimpleNamespace(stdout=owner.policy.encoded(self.addresses))
        if argv == ["ip", "--json", "-4", "route", "show", "table", "all"]:
            return types.SimpleNamespace(stdout=owner.policy.encoded(self.routes))
        return super().run(argv, **kwargs)

    def apply(self, batch):
        rows = owner.policy.decode(batch)["nftables"]
        self.calls.append(batch)
        if "delete" in rows[0]:
            for row in rows:
                table = row["delete"]["table"]
                self.objects = [obj for obj in self.objects if not any(
                    body.get("family") == table["family"] and
                    body.get("table", body.get("name")) == table["name"] for body in obj.values())]
            return
        if self.state is not None:
            if self.fail_quarantine:
                raise subprocess.CalledProcessError(1, ["PRIVATE_CANARY"])
            self.objects += owner.policy.Quarantine(self.journal.network.policy).objects(canonical=True)
            return
        super().apply(batch)

    def call(self, argv, **kwargs):
        if argv[:2] != ["docker", "container"]:
            return super().call(argv, **kwargs)
        self.calls.append(argv)
        action = argv[2]
        if action == "create":
            assert "container-intent.json" in self.state.read()
            if self.fail_container == "before":
                raise KeyboardInterrupt("PRIVATE_CANARY")
            self.container = self.fixture.container(argv)
            selected = self.journal.network
            self.container["Config"]["Labels"] = {**self.fixture.image["Config"].get("Labels", {}),
                                                  owner.smoke.LABEL: selected.policy.token}
            self.container["Config"]["ExposedPorts"] = {"8443/tcp": {}}
            self.container["HostConfig"].update(NetworkMode=self.info["Id"], Dns=["127.0.0.1"],
                PortBindings={"8443/tcp": [{"HostIp": selected.policy.published, "HostPort": str(selected.policy.port)}]})
            self.container["NetworkSettings"] = {"Networks": {selected.name: endpoint(selected)}, "Ports": {}}
            self.container["State"].update(Paused=False, Restarting=False, Dead=False, Pid=0)
            if self.fail_container == "after":
                raise SystemExit("PRIVATE_CANARY")
            return (self.container["Id"] + "\n").encode()
        if action == "ls":
            if self.container is None:
                return b""
            selector = argv[-1] if "--filter" in argv else ""
            if selector.startswith("id=") and selector[3:] != self.container["Id"]:
                return b""
            if selector.startswith("name=") and selector != "name=^" + self.container["Name"] + "$":
                return b""
            return self.container["Id"].encode()
        if action == "inspect":
            return owner.policy.encoded(self.container)
        if action in ("kill", "rm"):
            assert argv[-1] == self.container["Id"]
            if self.fail_container == "remove":
                raise subprocess.CalledProcessError(1, argv)
            self.set_status("exited")
            if action == "rm":
                self.container = None
            return b""
        if action == "start":
            self.set_status("exited")
            return owner.policy.encoded(smoke_tests.lifecycle())
        raise AssertionError(argv)

    def set_status(self, status):
        selected = self.journal.network
        row = self.container
        row["State"].update(Status=status, Running=status == "running", Pid=99 if status == "running" else 0)
        current = endpoint(selected)
        self.info["Containers"] = {}
        row["NetworkSettings"]["Ports"] = {}
        if status == "running":
            current.update(NetworkID=self.info["Id"], EndpointID="e" * 64, Gateway=selected.gateway,
                IPAddress=selected.policy.address, MacAddress="02:42:ac:1d:01:02", IPPrefixLen=24,
                DNSNames=[row["Name"][1:], row["Id"][:12]])
            self.info["Containers"] = {row["Id"]: {"Name": row["Name"][1:], "EndpointID": current["EndpointID"],
                "MacAddress": current["MacAddress"], "IPv4Address": selected.policy.address + "/24", "IPv6Address": ""}}
            row["NetworkSettings"]["Ports"] = copy.deepcopy(row["HostConfig"]["PortBindings"])
        row["NetworkSettings"]["Networks"] = {selected.name: current}


class Fixture:
    @classmethod
    def setUpClass(cls):
        smoke_tests.SmokeTests.setUpClass()

    def setUp(self):
        self.fixture = smoke_tests.SmokeTests("test_build_hash_source_join_and_closed_claims")
        self.fixture.setUp()
        self.fixture.image["Config"]["Labels"] = {"org.opencontainers.image.source": "https://example.invalid/source",
                                                 "maintainer": "synthetic image maintainer"}
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        for name in ("network", "container"):
            (self.root / name).mkdir(mode=0o700)
        self.selected = network()
        self.journal = owner.network.Journal(self.root / "network", self.selected,
                                             time.time() + 3000, time.monotonic() + 3000)
        self.journal.initialize()
        self.commands = Commands(self.journal, self.fixture)
        self.reservation = owner.network.Reservation(self.commands, self.journal)
        with mock.patch.object(owner.network.Reservation, "admit"):
            identifier = self.reservation.reserve()
        self.state = owner.State(self.root / "container")
        self.command = owner.container_command(self.selected, identifier, self.fixture.build, self.fixture.base,
            self.fixture.args.weights, "/private/stage", self.fixture.args, self.fixture.args.hard_stop_at)
        self.runtime = {"command": self.command, "network_id": identifier,
            "image_id": self.fixture.build["image_id"], "entrypoint": self.fixture.image["Config"]["Entrypoint"],
            "image_labels": owner.image_labels(self.fixture.image["Config"]["Labels"]),
            "weights": self.fixture.args.weights, "inputs": "/private/stage"}
        self.contract = {"schema": "nib.selected-service-owner-intent/v1", "network": asdict(self.selected),
            "wall": self.journal.contract["hard_wall"], "monotonic": self.journal.contract["hard_monotonic"]}
        self.state.initialize(self.contract, self.runtime)
        self.commands.state = self.state
        self.owned = owner.Owner(self.commands, self.reservation, self.state)
        patch = mock.patch.object(owner, "host_target")
        self.host_target = patch.start()
        self.addCleanup(patch.stop)


class ContractTests(Fixture, unittest.TestCase):
    def pinned_runtime(self):
        raw = (Path(__file__).parent / "fixtures/selected-base-image-config.json").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
            "bd8a5d7a8774800c92d714c0c226b32c1863f929150f36efd610aebcd12a335d")
        base = {"Config": json.loads(raw)["config"]}
        self.assertEqual(len(base["Config"]["Env"]), 25)
        return {**self.runtime, "image_labels": owner.image_labels(base["Config"]["Labels"]),
            "command": owner.container_command(self.selected,
            self.runtime["network_id"], self.fixture.build, base,
            self.runtime["weights"], self.runtime["inputs"], self.fixture.args,
            self.fixture.args.hard_stop_at)}

    def test_image_labels_are_bounded_copied_and_cannot_claim_ownership(self):
        self.assertEqual(owner.image_labels(None), {})
        labels = {"source": "public", "empty": ""}
        self.assertEqual(owner.image_labels(labels), labels)
        self.assertIsNot(owner.image_labels(labels), labels)
        for value in ([], "text", {owner.smoke.LABEL: "a" * 32}, {"": "value"},
                      {"x" * 257: "value"}, {"key": "x" * 1025}, {"key": None},
                      {"key": "nul\0value"}, {"nul\0key": "value"},
                      {str(i): "value" for i in range(65)}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                owner.image_labels(value)

    def test_runtime_requires_valid_image_labels(self):
        for index, value in enumerate((None, [], {owner.smoke.LABEL: "a" * 32})):
            root = self.root / ("bad-labels-" + str(index))
            root.mkdir(mode=0o700)
            with self.subTest(value=value), self.assertRaises(ValueError):
                owner.State(root).initialize(self.contract, {**self.runtime, "image_labels": value})

    def test_pinned_image_runtime_round_trips_above_old_limit(self):
        runtime = self.pinned_runtime()
        size = len(owner.policy.encoded(runtime)) + 1
        self.assertGreater(size, 4096)
        self.assertLess(size, owner.RUNTIME_LIMIT)
        root = self.root / "actual-runtime"
        root.mkdir(mode=0o700)
        state = owner.State(root)
        state.initialize(self.contract, runtime)
        self.assertEqual(state.read()["runtime.json"], runtime)
        self.assertEqual((root / "runtime.json").stat().st_size, size)

    def test_runtime_exact_bound_and_oversize_reads_and_writes(self):
        for extra in (0, 1):
            runtime = self.pinned_runtime()
            runtime["command"][-1] += "x" * (owner.RUNTIME_LIMIT - len(owner.policy.encoded(runtime)) - 1 + extra)
            root = self.root / ("bound-" + str(extra))
            root.mkdir(mode=0o700)
            state = owner.State(root)
            if extra:
                with self.assertRaises(ValueError):
                    state.initialize(self.contract, runtime)
                self.assertFalse((root / "runtime.json").exists())
            else:
                state.initialize(self.contract, runtime)
                self.assertEqual(state.read()["runtime.json"], runtime)
                with (root / "runtime.json").open("ab") as stream:
                    stream.write(b" ")
                with self.assertRaises(ValueError):
                    state.read()

    def test_other_journals_retain_four_kib_bound(self):
        value = {"data": "x" * 4096}
        for journal, name in ((self.journal, "runtime.json"),
                              (self.state, "contract.json"), (self.state, "container-intent.json")):
            self.assertEqual(journal.file_limit(name), 4096)
            with journal._directory() as fd, self.assertRaises(ValueError):
                journal._write(fd, name, value)

    def test_only_explicit_network_delta_to_shared_smoke_command(self):
        previous = owner.smoke.container_command("nib-selected-" + self.selected.policy.token,
            self.selected.policy.token, self.fixture.build, self.fixture.base, self.fixture.args.weights,
            "/private/stage", self.fixture.args, self.fixture.args.hard_stop_at)
        offset = previous.index("--network")
        self.assertEqual(self.command[:offset], previous[:offset])
        self.assertEqual(self.command[offset + 8:], previous[offset + 2:])
        self.assertEqual(self.command[offset:offset + 8], ["--network", "b" * 64, "--ip", "172.29.1.2",
            "--dns", "127.0.0.1", "--publish", "192.0.2.10:443:8443/tcp"])

    def test_private_journal_order_symlinks_unknown_fields_and_cutoff_binding(self):
        with self.assertRaises(ValueError):
            self.state.record("start-intent")
        self.state.record("container-intent")
        with self.assertRaises(ValueError):
            self.state.record("container-intent")
        self.assertEqual((self.state.root / "container-intent.json").stat().st_mode & 0o777, 0o600)
        extra = self.state.root / "private-extra"
        extra.touch()
        with self.assertRaises(ValueError):
            self.state.read()
        extra.unlink()
        path = self.state.root / "container-intent.json"
        path.unlink()
        path.symlink_to(self.state.root / "contract.json")
        with self.assertRaises(ValueError):
            self.state.read()

    def test_created_running_and_exited_actual_network_joins(self):
        identifier = self.owned.create()
        original = copy.deepcopy(self.commands.container)
        owner.check_container(original, identifier, self.selected, self.runtime)
        self.assertEqual(original, self.commands.container)
        self.owned.release_for_serving()
        for status in ("created", "running", "exited"):
            self.commands.set_status(status)
            self.owned.fresh(serving=True)
        with self.assertRaises(ValueError):
            owner.smoke.check_container(self.commands.container, identifier, self.fixture.name, self.fixture.token,
                self.fixture.build, self.fixture.image, self.command, self.fixture.args.weights, "/private/stage")

    def test_network_dns_publication_runtime_drift_rejected(self):
        identifier = self.owned.create()
        self.commands.set_status("running")
        original = copy.deepcopy(self.commands.container)
        changes = [("HostConfig", "NetworkMode", "host"), ("HostConfig", "Dns", ["127.0.0.11"]),
            ("HostConfig", "DnsSearch", ["private.invalid"]), ("HostConfig", "DnsOptions", ["ndots:0"]),
            ("HostConfig", "ExtraHosts", ["private:host-gateway"]), ("HostConfig", "PortBindings", {}),
            ("HostConfig", "PublishAllPorts", True), ("HostConfig", "MemorySwap", -1),
            ("HostConfig", "Privileged", True), ("Config", "User", "root"), ("Config", "Cmd", []),
            ("Config", "ExposedPorts", {"8000/tcp": {}}), ("NetworkSettings", "Ports", {}),
            ("State", "Paused", True), ("State", "OOMKilled", True), ("State", "Restarting", True)]
        for scope, field, value in changes:
            with self.subTest(scope=scope, field=field), self.assertRaises(ValueError):
                row = copy.deepcopy(original)
                row[scope][field] = value
                owner.check_container(row, identifier, self.selected, self.runtime)
        for field, value in (("NetworkID", "c" * 64), ("IPAddress", "172.29.1.3"),
                ("DriverOpts", {"sysctl": "override"}), ("Aliases", ["private"]), ("Links", ["other"]),
                ("GlobalIPv6Address", "::1"), ("GwPriority", True), ("DNSNames", []), ("Unknown", 1)):
            with self.subTest(endpoint=field), self.assertRaises(ValueError):
                row = copy.deepcopy(original)
                row["NetworkSettings"]["Networks"][self.selected.name][field] = value
                owner.check_container(row, identifier, self.selected, self.runtime)

    def test_inherited_labels_and_owner_label_are_exact(self):
        identifier = self.owned.create()
        original = copy.deepcopy(self.commands.container)
        expected = {**self.runtime["image_labels"], owner.smoke.LABEL: self.selected.policy.token}
        self.assertEqual(original["Config"]["Labels"], expected)
        for labels in ({owner.smoke.LABEL: self.selected.policy.token}, self.runtime["image_labels"],
                       {**expected, "maintainer": "changed"}, {**expected, "extra": "PRIVATE_CANARY"},
                       {**expected, owner.smoke.LABEL: "b" * 32}, None):
            with self.subTest(labels=labels), self.assertRaises((ValueError, AttributeError)):
                row = copy.deepcopy(original)
                row["Config"]["Labels"] = labels
                owner.check_container(row, identifier, self.selected, self.runtime)

    def test_foreign_endpoint_duplicate_network_and_missing_policy_block_serving(self):
        self.owned.create()
        self.commands.info["Containers"] = {"c" * 64: {}}
        with self.assertRaises(ValueError):
            self.owned.release_for_serving()
        self.commands.info["Containers"] = {}
        self.commands.rows.append({"ID": "c" * 64, "Name": self.selected.name})
        with self.assertRaises(ValueError):
            self.owned.release_for_serving()
        self.commands.rows.pop()
        self.commands.objects = []
        with self.assertRaises(ValueError):
            self.owned.release_for_serving()
        self.assertNotIn("start-intent.json", self.state.read())


class CleanupTests(Fixture, unittest.TestCase):
    def test_transition_resampled_but_stable_endpoint_mismatch_rejects(self):
        self.owned.create()
        self.owned.release_for_serving()
        original = self.commands.call
        snapshots = []
        def transition(command, **kwargs):
            raw = original(command, **kwargs)
            if command[:3] == ["docker", "network", "inspect"]:
                snapshots.append(raw)
                if len(snapshots) == 1:
                    self.commands.set_status("running")
            return raw
        with mock.patch.object(self.commands, "call", side_effect=transition):
            self.assertEqual(self.owned.fresh(serving=True)["State"]["Status"], "running")
        self.assertEqual(len(snapshots), 2)
        self.commands.info["Containers"] = {}
        with mock.patch.object(self.commands, "call", wraps=original) as call:
            with self.assertRaises(ValueError):
                self.owned.fresh(serving=True)
        self.assertEqual(sum(args[0][:3] == ["docker", "network", "inspect"]
                             for args, _ in call.call_args_list), 1)

    def test_release_is_once_and_teardown_requires_real_absence(self):
        self.owned.create()
        self.owned.release_for_serving()
        with self.assertRaises(ValueError):
            self.owned.release_for_serving()
        with self.assertRaises(ValueError):
            self.reservation.quarantine.remove_batch(containers_absent=False,
                networks_absent=False, creations_resolved=True)
        self.commands.set_status("running")
        result = self.owned.cleanup()
        self.assertTrue(all(result.values()))
        self.assertIsNone(self.commands.container)
        self.assertEqual(self.commands.rows, [])
        self.assertEqual(self.commands.objects, [])

    def test_interrupted_create_remains_unresolved_even_after_observed_removal(self):
        self.commands.fail_container = "before"
        with self.assertRaises(KeyboardInterrupt):
            self.owned.create()
        result = self.owned.cleanup()
        self.assertTrue(result["container_absent"])
        self.assertFalse(result["creations_resolved"])
        self.assertFalse(result["cleanup_confirmed"])
        self.reservation.quarantined()

    def test_interrupted_create_after_daemon_completion_retains_quarantine(self):
        self.commands.fail_container = "after"
        with self.assertRaises(SystemExit):
            self.owned.create()
        result = self.owned.cleanup()
        self.assertIsNone(self.commands.container)
        self.assertTrue(result["container_absent"])
        self.assertFalse(result["creations_resolved"])
        self.assertFalse(result["cleanup_confirmed"])
        self.reservation.quarantined()

    def test_quarantine_failure_stops_owned_workload_but_never_claims_success(self):
        self.owned.create()
        self.owned.release_for_serving()
        self.commands.set_status("running")
        self.commands.fail_quarantine = True
        result = self.owned.cleanup()
        self.assertTrue(result["container_absent"])
        self.assertFalse(result["quarantine_confirmed"])
        self.assertFalse(result["cleanup_confirmed"])
        self.assertNotEqual(self.commands.rows, [])

    def test_unowned_container_is_never_removed_and_original_deadline_unchanged(self):
        self.owned.create()
        self.commands.container["Config"]["Labels"] = {}
        deadline = self.commands.deadline
        result = self.owned.cleanup()
        self.assertFalse(result["container_absent"])
        self.assertFalse(result["cleanup_confirmed"])
        self.assertEqual(self.commands.deadline, deadline)
        self.assertFalse(any(isinstance(cmd, list) and cmd[:3] in (
            ["docker", "container", "kill"], ["docker", "container", "rm"]) for cmd in self.commands.calls))


class HostTargetTests(unittest.TestCase):
    def check(self, addresses=None, routes=None, proc=None, containers=b""):
        selected = network()
        addresses = addresses if addresses is not None else [{"ifname": "approved0", "flags": ["UP"],
            "addr_info": [{"family": "inet", "local": "192.0.2.10", "prefixlen": 24, "scope": "global"}]}]
        routes = routes if routes is not None else [{"dst": "default", "dev": "approved0"}]
        commands = mock.Mock()
        commands.remaining.return_value = 100
        commands.run.side_effect = [types.SimpleNamespace(stdout=owner.policy.encoded(rows)) for rows in (addresses, routes)]
        commands.call.return_value = containers
        with mock.patch("builtins.open", mock.mock_open(read_data=proc or b"sl local_address\n")):
            owner.host_target(commands, selected, initial=True)

    def test_no_conflict_and_closed_idle_daemon(self):
        self.check()
        with self.assertRaises(ValueError):
            self.check(containers=b"c" * 64)

    def test_wrong_interface_missing_address_and_overlaps_rejected(self):
        for addresses in ([], [{"ifname": "wrong", "flags": ["UP"], "addr_info": [
                {"family": "inet", "local": "192.0.2.10", "prefixlen": 24, "scope": "global"}]}]):
            with self.assertRaises(ValueError):
                self.check(addresses=addresses)
        for dst in ("172.29.1.0/24", "172.29.0.0/16", "172.29.1.2/32"):
            with self.subTest(dst=dst), self.assertRaises(ValueError):
                self.check(routes=[{"dst": dst, "dev": "other"}])

    def test_existing_listener_and_malformed_or_oversize_proc_rejected(self):
        for proc in (b"sl local_address\n0: 00000000:01BB 00000000:0000 0A 0 0 0 0 0 0\n",
                     b"bad", b"x" * 262145):
            with self.assertRaises(ValueError):
                self.check(proc=proc)


class DaemonTests(unittest.TestCase):
    def observation(self, *, namespace=2, executable="/usr/bin/dockerd", socket_mode=0o660, changed=False):
        commands = mock.Mock()
        commands.remaining.return_value = 100
        fields = [b"S", *([b"0"] * 18), b"100"]
        raw = b"44 (dockerd) " + b" ".join(fields)
        changed_raw = raw[:-3] + b"101" if changed else raw
        def file_stat(path):
            return types.SimpleNamespace(st_uid=0, st_dev=1, st_ino=2)
        def space_stat(path):
            return types.SimpleNamespace(st_dev=1, st_ino=2 if "/self/" in str(path) else namespace)
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(owner.smoke.service.observer, "read_kernel",
                                                   side_effect=[raw, changed_raw]))
            stack.enter_context(mock.patch.object(owner.os, "readlink", return_value=executable))
            stack.enter_context(mock.patch.object(Path, "stat", file_stat))
            stack.enter_context(mock.patch.object(owner.os, "stat", side_effect=space_stat))
            stack.enter_context(mock.patch.object(Path, "resolve", return_value=Path("/run/docker.sock")))
            stack.enter_context(mock.patch.object(Path, "lstat", return_value=types.SimpleNamespace(
                st_mode=stat.S_IFSOCK | socket_mode, st_uid=0, st_dev=1, st_ino=3, st_ctime_ns=4)))
            return owner.daemon_identity(commands, 44)

    def test_daemon_process_socket_and_namespace_identity(self):
        self.assertEqual(self.observation(), (44, 100, 1, 2, ((1, 2), (1, 2), (1, 2)), 1, 3, 4))
        for options in ({"namespace": 3}, {"executable": "/private/dockerd"},
                        {"socket_mode": 0o666}, {"changed": True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.observation(**options)

    def test_check_budget_never_extends_original_wall_or_monotonic_cutoff(self):
        with mock.patch.object(host.time, "time", return_value=1000), \
                mock.patch.object(host.time, "monotonic", return_value=100):
            commands = host.GuardCommands(None, host.environment(), 2000, 1100)
            self.assertEqual(commands.remaining(), 1000 - host.MONITOR_RESERVE)
            self.assertEqual(commands.end, 1100 - host.MONITOR_RESERVE)
            self.assertEqual(commands.deadline, 1100)
            commands.reserve = 0
            self.assertEqual(commands.remaining(), 1000)
            self.assertEqual(commands.end, 1100)
            with mock.patch.object(host.time, "time", return_value=500):
                self.assertEqual(commands.remaining(), 1000)
            self.assertEqual((commands.wall, commands.deadline), (2000, 1100))

    def test_real_subprocess_ack_and_bounded_final_pipe_join(self):
        smoke_tests.SmokeTests.setUpClass()
        snapshot = smoke_tests.SmokeTests.recorder.snapshot
        process = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c",
            "import os; os.write(1,b'ready\\n'); os.read(0,1); os.write(1,b'{}\\n')"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        commands = mock.Mock()
        commands.remaining.return_value = 300
        try:
            host.wait_ack(process, b"ready\n", commands)
            process.stdin.close()
            raw, error = snapshot.bounded_output(process, ["synthetic-owner"], 5, None, 4096, 4096)
            self.assertEqual((raw, error, process.returncode), (b"{}\n", b"", 0))
        finally:
            process.stdin.close()
            snapshot.cleanup_process_group(process)


class WatchdogTests(Fixture, unittest.TestCase):
    def run_watchdog(self, messages, *, drift=False):
        output = io.StringIO()
        reader, writer = os.pipe()
        self.addCleanup(os.close, writer)
        uid = os.getuid()
        state_read, journal_read, check = owner.State.read, owner.network.Journal.read, owner.network.Journal._check
        def actual_uid_read(method):
            def read(value):
                with mock.patch.object(owner.os, "getuid", return_value=uid):
                    return method(value)
            return read
        def acknowledge(message):
            if drift and message == b"serving\n":
                next(row["rule"] for row in self.commands.objects if "rule" in row)["expr"] = [{"accept": None}]
        def actual_check(value, fd):
            with mock.patch.object(owner.os, "getuid", return_value=uid):
                check(value, fd)
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(host.os, "getuid", return_value=0))
            stack.enter_context(mock.patch.object(host.sys, "platform", "linux"))
            stack.enter_context(mock.patch.object(host, "read_reservation", return_value=self.journal))
            stack.enter_context(mock.patch.object(host.smoke, "host_recorder", return_value=(None, None)))
            stack.enter_context(mock.patch.object(host, "GuardCommands", return_value=self.commands))
            stack.enter_context(mock.patch.object(host.network, "Reservation", return_value=self.reservation))
            stack.enter_context(mock.patch.object(host.owner, "Owner", return_value=self.owned))
            stack.enter_context(mock.patch.object(self.reservation, "admit"))
            stack.enter_context(mock.patch.object(host.owner, "daemon_identity", return_value=(123, 10)))
            stack.enter_context(mock.patch.object(host, "acknowledge", side_effect=acknowledge))
            stack.enter_context(mock.patch.object(host.select, "select", return_value=([reader], [], [])))
            stack.enter_context(mock.patch.object(host.os, "read", side_effect=messages))
            # Only root venue admission is synthetic. Read real temp files
            # using their real UID, including both ownership/mode checks.
            stack.enter_context(mock.patch.object(owner.State, "read", actual_uid_read(state_read)))
            stack.enter_context(mock.patch.object(owner.network.Journal, "read", actual_uid_read(journal_read)))
            stack.enter_context(mock.patch.object(owner.network.Journal, "_check", actual_check))
            stack.enter_context(redirect_stdout(output))
            code = host.watchdog(str(self.root), reader)
        return code, output.getvalue()

    def test_one_release_parent_eof_and_confirmed_cleanup(self):
        self.owned.create()
        code, raw = self.run_watchdog([b"serve\n", b""])
        self.assertEqual(code, 0, raw)
        self.assertEqual(host.cleanup_record(raw.encode())["result"], "pass")
        self.assertIsNone(self.commands.container)
        self.assertEqual(self.commands.objects, [])

    def test_external_cancellation_keeps_failure_even_with_cleanup(self):
        self.owned.create()
        code, raw = self.run_watchdog([KeyboardInterrupt("PRIVATE_CANARY")])
        self.assertEqual(code, 1)
        row = host.cleanup_record(raw.encode())
        self.assertEqual(row["result"], "fail")
        self.assertTrue(row["cleanup"]["cleanup_confirmed"], raw)
        self.assertNotIn("PRIVATE_CANARY", raw)

    def test_serving_drift_requarantines_before_owned_container_removal(self):
        self.owned.create()
        code, raw = self.run_watchdog([b"serve\n"], drift=True)
        self.assertEqual(code, 1)
        row = host.cleanup_record(raw.encode())
        self.assertEqual(row["result"], "fail")
        self.assertTrue(row["cleanup"]["cleanup_confirmed"], raw)
        calls = self.commands.calls
        quarantine = max(index for index, command in enumerate(calls) if isinstance(command, bytes)
            and b'"create"' in command and b'nib_quarantine_' in command)
        removed = next(index for index, command in enumerate(calls)
                       if isinstance(command, list) and command[:3] == ["docker", "container", "rm"])
        self.assertLess(quarantine, removed)

    def test_original_expiry_and_unknown_control_message_fail_closed(self):
        self.owned.create()
        with mock.patch.object(self.commands, "remaining", return_value=0):
            code, raw = self.run_watchdog([])
        self.assertEqual(code, 1)
        self.assertEqual(host.cleanup_record(raw.encode())["result"], "fail")

    def test_unknown_or_duplicate_control_fails_without_second_release(self):
        for messages in ([b"PRIVATE_CANARY"], [b"serve\n", b"serve\n"]):
            with self.subTest(messages=messages):
                self.setUp()
                self.owned.create()
                code, raw = self.run_watchdog(messages)
                self.assertEqual(code, 1)
                self.assertEqual(host.cleanup_record(raw.encode())["result"], "fail")
                self.assertNotIn("PRIVATE_CANARY", raw)

    def test_closed_cleanup_receipt_rejects_private_canaries_and_false_joins(self):
        good = {"result": "pass", "cleanup": {key: True for key in (
            "container_absent", "network_absent", "creations_resolved", "quarantine_confirmed", "policy_removed", "cleanup_confirmed")}}
        host.cleanup_record(owner.policy.encoded(good))
        for change in ({**good, "private": "PRIVATE_CANARY"}, {**good, "result": "PRIVATE_CANARY"},
                {**good, "cleanup": {**good["cleanup"], "container_absent": False}}):
            with self.assertRaises(ValueError):
                host.cleanup_record(owner.policy.encoded(change))

    def test_cli_rejects_unknown_args_without_raw_paths_or_chains(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(host.main(["--PRIVATE_CANARY=/private/key"]), 1)
        self.assertNotIn("PRIVATE_CANARY", output.getvalue())
        self.assertNotIn("/private/key", output.getvalue())


class CallerTests(Fixture, unittest.TestCase):
    def run_caller(self, *, failure=None, watchdog_result="pass", reap_failure=False, oversized=False):
        result = host.report()
        recorder = types.SimpleNamespace(snapshot=mock.Mock())
        monitor = mock.Mock(returncode=0)
        monitor.poll.return_value = None
        args = copy.copy(self.fixture.args)
        args.state_dir = str(self.root)
        contract = types.SimpleNamespace(hard_wall=self.contract["wall"], hard_monotonic=self.contract["monotonic"],
                                         lifetime_profile=owner.smoke.service.SMOKE_PROFILE)
        admitted = (contract, self.fixture.lab, recorder,
                    self.fixture.lab.controlled_env(args.docker_host), self.fixture.build,
                    self.fixture.base, self.fixture.image, Path("/private/stage"))
        original_call = self.commands.call
        def call(command, **kwargs):
            if command[:3] == ["docker", "container", "start"] and failure is not None:
                raise failure
            return original_call(command, **kwargs)
        def ack(_monitor, message, _commands):
            if message == b"serving\n":
                self.owned.release_for_serving()
        def join(*_args):
            cleaned = self.owned.cleanup()
            monitor.returncode = 0 if watchdog_result == "pass" and cleaned["cleanup_confirmed"] else 1
            return owner.policy.encoded({"result": watchdog_result, "cleanup": cleaned}), b""
        recorder.snapshot.bounded_output.side_effect = join
        if reap_failure:
            recorder.snapshot.cleanup_process_group.side_effect = ValueError("PRIVATE_CANARY")
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(host, "state_directories"))
            stack.enter_context(mock.patch.object(host.network, "Journal", return_value=self.journal))
            stack.enter_context(mock.patch.object(self.journal, "initialize"))
            command_factory = stack.enter_context(mock.patch.object(host.network, "Commands", return_value=self.commands))
            stack.enter_context(mock.patch.object(host.network, "Reservation", return_value=self.reservation))
            stack.enter_context(mock.patch.object(self.reservation, "admit"))
            stack.enter_context(mock.patch.object(host.owner, "daemon_identity", return_value=(123, 10)))
            reserve = stack.enter_context(mock.patch.object(self.reservation, "reserve", return_value=self.runtime["network_id"]))
            stack.enter_context(mock.patch.object(host.owner, "Owner", return_value=self.owned))
            stack.enter_context(mock.patch.object(host.owner.State, "initialize"))
            stack.enter_context(mock.patch.object(host.smoke, "weights_view"))
            launch = stack.enter_context(mock.patch.object(host.subprocess, "Popen", return_value=monitor))
            stack.enter_context(mock.patch.object(host, "wait_ack", side_effect=ack))
            stack.enter_context(mock.patch.object(self.commands, "call", side_effect=call))
            if oversized:
                stack.enter_context(mock.patch.object(owner, "container_command", return_value=["x" * owner.RUNTIME_LIMIT]))
                with self.assertRaises(ValueError):
                    host.run(args, admitted, self.selected, result)
                reserve.assert_not_called()
                launch.assert_not_called()
            else:
                host.run(args, admitted, self.selected, result)
            command_factory.assert_called_once_with(recorder, host.environment(),
                contract.hard_wall, contract.hard_monotonic)
        return result

    def test_oversized_private_intent_rejected_before_any_resource_create(self):
        row = self.run_caller(oversized=True)
        self.assertFalse(row["preflight_pass"])
        self.assertEqual(row["reason"], "runtime-intent")

    def test_private_input_cleanup_failure_cannot_preserve_runtime_pass(self):
        @contextmanager
        def admission(*_):
            yield object()
            raise ValueError("PRIVATE_CANARY")
        args = types.SimpleNamespace(run=True, docker_host=host.environment()["DOCKER_HOST"],
            ingress=self.selected.policy.ingress, address=self.selected.policy.address,
            published_address=self.selected.policy.published, published_port=self.selected.policy.port,
            subnet=self.selected.subnet, gateway=self.selected.gateway)
        def successful_run(_args, _admitted, _selected, result):
            result.update(result="pass", status="PASS", reason="completed", cleanup="confirmed")
        with mock.patch.object(host.smoke, "admitted_runtime", admission), \
                mock.patch.object(host, "run", side_effect=successful_run):
            row = host.preflight(args)
        self.assertEqual((row["result"], row["reason"]), ("fail", "private-input-cleanup"))
        self.assertFalse(row["private_inputs_removed"])
        self.assertNotIn("PRIVATE_CANARY", owner.policy.encoded(row).decode())

    def test_success_joins_lifecycle_ownership_watchdog_and_cleanup(self):
        row = self.run_caller()
        self.assertEqual(row["result"], "pass", row)
        self.assertEqual(row["cleanup"], "confirmed")
        self.assertTrue(row["watchdog_confirmed"])
        self.assertTrue(row["runtime_started"])
        self.assertFalse(row["private_inputs_removed"])
        for key in ("published", "confidential", "e2ee", "deployable", "hardware_validated"):
            self.assertIs(row[key], False)

    def test_caller_interrupt_does_not_discard_confirmed_cleanup(self):
        row = self.run_caller(failure=KeyboardInterrupt("PRIVATE_CANARY"))
        self.assertEqual(row["result"], "fail")
        self.assertEqual(row["cleanup"], "confirmed", row)
        self.assertNotIn("PRIVATE_CANARY", owner.policy.encoded(row).decode())

    def test_watchdog_failure_is_sticky_despite_lifecycle_and_cleanup(self):
        row = self.run_caller(watchdog_result="fail")
        self.assertEqual(row["result"], "fail")
        self.assertEqual(row["cleanup"], "confirmed", row)
        self.assertFalse(row["watchdog_confirmed"])

    def test_unreaped_watchdog_never_races_fallback_quarantine(self):
        row = self.run_caller(reap_failure=True)
        self.assertEqual(row["result"], "fail")
        self.assertEqual(row["cleanup"], "unconfirmed")
        self.assertNotIn("fallback_cleanup", row)


class StateDirectoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.root = self.parent / "state"
        self.root.mkdir(mode=0o700)

    @contextmanager
    def observed_uid(self, uid=0):
        # Keep the real descriptor, mode and no-follow walk; only root UID
        # observation is synthetic so these tests need no privileged chown.
        actual = os.fstat
        def observe(fd):
            fields = list(actual(fd))
            fields[4] = uid
            return os.stat_result(fields)
        with mock.patch.object(host.os, "fstat", side_effect=observe):
            yield

    def inspect_or_run(self, path, *, run=False, accepted=False, uid=0):
        selected = network()
        args = types.SimpleNamespace(run=run, state_dir=str(path),
            docker_host=host.environment()["DOCKER_HOST"], ingress=selected.policy.ingress,
            address=selected.policy.address, published_address=selected.policy.published,
            published_port=selected.policy.port, subnet=selected.subnet, gateway=selected.gateway)
        contract = types.SimpleNamespace(hard_wall=2000000000, hard_monotonic=1000000,
                                         lifetime_profile=owner.smoke.service.SMOKE_PROFILE)
        @contextmanager
        def admitted(_args, result):
            result.update(preflight_pass=True, reason="not-run")
            yield (contract, None, None, None, None, None, None, None)
        with self.observed_uid(uid), ExitStack() as stack:
            stack.enter_context(mock.patch.object(host.smoke, "admitted_runtime", admitted))
            stack.enter_context(mock.patch.object(host, "admitted_commands"))
            journal = stack.enter_context(mock.patch.object(host.network, "Journal"))
            reservation = stack.enter_context(mock.patch.object(host.network, "Reservation"))
            daemon = stack.enter_context(mock.patch.object(host.owner, "daemon_identity"))
            target = stack.enter_context(mock.patch.object(host.owner, "host_target"))
            runtime = stack.enter_context(mock.patch.object(host, "runtime_plan"))
            spawn = stack.enter_context(mock.patch.object(host.subprocess, "Popen"))
            result = host.preflight(args)
            spawn.assert_not_called()
            if accepted:
                journal.assert_called_once()
                journal.return_value.initialize.assert_not_called()
                reservation.return_value.admit.assert_called_once()
                reservation.return_value.reserve.assert_not_called()
                daemon.assert_called_once()
                target.assert_called_once()
                runtime.assert_called_once()
            else:
                for boundary in (journal, reservation, daemon, target, runtime):
                    boundary.assert_not_called()
        self.assertTrue(result["private_inputs_removed"])
        self.assertEqual(result["preflight_pass"], accepted)
        self.assertEqual(result["reason"], "not-run" if accepted else "state-directory")
        self.assertEqual(result["result"], "fail")
        if not accepted:
            self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["cleanup"], "not-started")
        self.assertFalse(result["container_created"] or result["runtime_started"]
                         or result["tls_publication_requested"] or result["watchdog_confirmed"])
        self.assertNotIn(str(path), json.dumps(result))
        return result

    def test_empty_descriptor_is_read_only_and_closed(self):
        before = self.root.stat()
        with self.observed_uid(), host.empty_state_directory(self.root) as fd:
            actual = os.fstat(fd)
            self.assertEqual((actual.st_dev, actual.st_ino), (before.st_dev, before.st_ino))
        with self.assertRaises(OSError):
            os.fstat(fd)
        after = self.root.stat()
        self.assertEqual((before.st_ino, before.st_mode, before.st_mtime_ns, before.st_ctime_ns),
                         (after.st_ino, after.st_mode, after.st_mtime_ns, after.st_ctime_ns))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_run_creates_and_fsyncs_through_the_checked_descriptor(self):
        with self.observed_uid(), mock.patch.object(host.os, "mkdir", wraps=os.mkdir) as mkdir, \
                mock.patch.object(host.os, "fsync", wraps=os.fsync) as fsync:
            host.state_directories(self.root)
        fd = mkdir.call_args_list[0].kwargs["dir_fd"]
        self.assertEqual(mkdir.call_args_list, [mock.call(name, mode=0o700, dir_fd=fd)
                                                for name in ("network", "container")])
        fsync.assert_called_once_with(fd)
        with self.assertRaises(OSError):
            os.fstat(fd)
        self.assertEqual({path.name for path in self.root.iterdir()}, {"network", "container"})
        for path in self.root.iterdir():
            self.assertTrue(path.is_dir())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)

    def test_inspect_preserves_empty_root_and_creates_no_journal(self):
        before = self.root.stat()
        self.inspect_or_run(self.root, accepted=True)
        after = self.root.stat()
        self.assertEqual((before.st_ino, before.st_mode, before.st_mtime_ns, before.st_ctime_ns),
                         (after.st_ino, after.st_mode, after.st_mtime_ns, after.st_ctime_ns))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_invalid_roots_block_inspect_and_run_before_journal_or_resources(self):
        regular = self.parent / "regular"
        regular.write_bytes(b"unchanged")
        link = self.parent / "link"
        link.symlink_to(self.root, target_is_directory=True)
        intermediate = self.parent / "intermediate"
        intermediate.symlink_to(self.parent, target_is_directory=True)
        occupied = self.parent / "occupied"
        occupied.mkdir(mode=0o700)
        (occupied / "original").write_bytes(b"unchanged")
        missing = self.parent / "missing"
        before = set(self.parent.iterdir())
        for path in (missing, regular, link, intermediate / "state", occupied, "relative"):
            for run in (False, True):
                with self.subTest(path=Path(path).name, run=run):
                    self.inspect_or_run(path, run=run)
        self.assertEqual(set(self.parent.iterdir()), before)
        self.assertFalse(missing.exists())
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(regular.read_bytes(), b"unchanged")
        self.assertEqual((occupied / "original").read_bytes(), b"unchanged")

    def test_wrong_mode_or_uid_blocks_both_modes_without_changing_root(self):
        for mode, uid in ((0o750, 0), (0o770, 0), (0o755, 0), (0o1700, 0), (0o700, 1001)):
            self.root.chmod(mode)
            for run in (False, True):
                with self.subTest(mode=mode, uid=uid, run=run):
                    self.inspect_or_run(self.root, run=run, uid=uid)
            self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), mode)
            self.assertEqual(list(self.root.iterdir()), [])


class AdmissionBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        smoke_tests.SmokeTests.setUpClass()

    def setUp(self):
        self.fixture = smoke_tests.SmokeTests("test_build_hash_source_join_and_closed_claims")
        self.fixture.setUp()

    def test_actual_admission_yield_has_explicit_closed_environment_transition(self):
        smoke = smoke_tests.smoke
        with self.fixture.admission(), smoke.admitted_runtime(self.fixture.args, smoke.report()) as admitted:
            original = dict(admitted[3])
            self.assertNotEqual(original, host.environment())
            commands = host.admitted_commands(admitted)
            self.assertEqual(commands.env, host.environment())
            self.assertEqual(admitted[3], original)
            self.assertIs(commands.recorder, admitted[2])
            self.assertEqual((commands.wall, commands.deadline),
                             (admitted[0].hard_wall, admitted[0].hard_monotonic))
            for change in ({**original, "PATH": host.environment()["PATH"]},
                           {**original, "PRIVATE_CANARY": "value"}, host.environment()):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    host.admitted_commands((*admitted[:3], change, *admitted[4:]))

    def test_inspect_uses_actual_admission_and_network_environment(self):
        args = self.fixture.args
        selected = network()
        args.run = False
        args.state_dir = "/private/unused-state"
        args.ingress, args.address = selected.policy.ingress, selected.policy.address
        args.published_address, args.published_port = selected.policy.published, selected.policy.port
        args.subnet, args.gateway = selected.subnet, selected.gateway
        seen = []
        def admit(reservation):
            seen.append(reservation.commands.env)
        with self.fixture.admission(), ExitStack() as stack:
            stack.enter_context(mock.patch.object(host, "smoke", smoke_tests.smoke))
            stack.enter_context(mock.patch.object(host, "empty_state_directory", return_value=nullcontext()))
            stack.enter_context(mock.patch.object(host.network.Reservation, "admit", admit))
            stack.enter_context(mock.patch.object(host.owner, "daemon_identity"))
            stack.enter_context(mock.patch.object(host.owner, "host_target"))
            weights = stack.enter_context(mock.patch.object(host.smoke, "weights_view"))
            result = host.preflight(args)
        self.assertTrue(result["preflight_pass"], result)
        self.assertTrue(result["private_inputs_removed"])
        self.assertEqual(result["reason"], "not-run")
        self.assertEqual(seen, [host.environment()])
        weights.assert_called_once()

    def test_inspect_rejects_oversize_and_confirms_private_cleanup(self):
        args = self.fixture.args
        selected = network()
        args.run = False
        args.state_dir = "/private/unused-state"
        args.ingress, args.address = selected.policy.ingress, selected.policy.address
        args.published_address, args.published_port = selected.policy.published, selected.policy.port
        args.subnet, args.gateway = selected.subnet, selected.gateway
        with self.fixture.admission(), ExitStack() as stack:
            stack.enter_context(mock.patch.object(host, "smoke", smoke_tests.smoke))
            stack.enter_context(mock.patch.object(host, "empty_state_directory", return_value=nullcontext()))
            stack.enter_context(mock.patch.object(host.network.Reservation, "admit"))
            stack.enter_context(mock.patch.object(host.owner, "daemon_identity"))
            stack.enter_context(mock.patch.object(host.owner, "host_target"))
            stack.enter_context(mock.patch.object(host.smoke, "weights_view"))
            stack.enter_context(mock.patch.object(host.owner, "container_command",
                return_value=["x" * owner.RUNTIME_LIMIT]))
            create = stack.enter_context(mock.patch.object(host.subprocess, "Popen"))
            result = host.preflight(args)
            create.assert_not_called()
        self.assertFalse(result["preflight_pass"])
        self.assertTrue(result["private_inputs_removed"])
        self.assertEqual((result["reason"], result["result"]), ("runtime-intent", "fail"))


if __name__ == "__main__":
    unittest.main()
