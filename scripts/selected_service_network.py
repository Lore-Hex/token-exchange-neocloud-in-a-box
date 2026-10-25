# SPDX-License-Identifier: Apache-2.0
"""Quarantined network reservation for a future finite selected-service owner.

No container start, publication, serving transition or executable CLI exists here.
"""

from dataclasses import asdict, dataclass
import ipaddress
import os
from pathlib import Path
import re
import stat
import tempfile
import types

import selected_network_policy as policy
import tau_runtime

ROOT = Path(__file__).absolute().parents[1]
LABEL = "nib.selected-service-network"
SCHEMA = "nib.selected-service-network-intent/v1"
EVENTS = ("quarantine-intent", "quarantine-applied", "policy-intent", "policy-applied",
          "network-intent", "network-resolved", "network-removed")
require = policy.require


def source_module(name, relative):
    module = types.ModuleType(name)
    path = ROOT / relative
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec", dont_inherit=True), module.__dict__)
    return module


smoke = source_module("selected_network_smoke", "host/record-selected-service-smoke.py")
proof = source_module("selected_network_proof", "scripts/prove-selected-nat.py")


def full_id(value):
    require(type(value) is str and re.fullmatch("[0-9a-f]{64}", value))
    return value


@dataclass(frozen=True)
class Network:
    policy: policy.Policy
    subnet: str
    gateway: str

    def __post_init__(self):
        require(type(self.policy) is policy.Policy and self.policy.nft_version == "1.1.6")
        require(self.policy.bridge == "nib" + self.policy.token[:12])
        require(type(self.subnet) is str and type(self.gateway) is str)
        subnet = ipaddress.IPv4Network(self.subnet)
        require(str(subnet) == self.subnet and 24 <= subnet.prefixlen <= 30
                and any(subnet.subnet_of(ipaddress.IPv4Network(block))
                        for block in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))
        gateway, address = ipaddress.IPv4Address(self.gateway), ipaddress.IPv4Address(self.policy.address)
        require(str(gateway) == self.gateway and gateway != address
                and all(value in subnet and value not in (subnet.network_address, subnet.broadcast_address)
                        for value in (gateway, address))
                and ipaddress.IPv4Address(self.policy.published) not in subnet)

    @property
    def name(self):
        return "nib-selected-net-" + self.policy.token

    @property
    def options(self):
        return {"com.docker.network.bridge.name": self.policy.bridge,
                "com.docker.network.bridge.gateway_mode_ipv4": "nat",
                "com.docker.network.bridge.enable_icc": "false",
                "com.docker.network.bridge.enable_ip_masquerade": "true"}

    def command(self):
        result = ["docker", "network", "create", "--driver", "bridge", "--scope", "local",
                  "--ipv4=true", "--ipv6=false", "--subnet", self.subnet, "--gateway", self.gateway,
                  "--ipam-driver", "default", "--label", LABEL + "=" + self.policy.token]
        for name, value in sorted(self.options.items()):
            result += ["--opt", name + "=" + value]
        return result + [self.name]

    def check(self, row, identifier):
        """Only an empty, exact owned local bridge is admitted in this increment."""
        config = {"Subnet": self.subnet, "Gateway": self.gateway}
        ipam = row["IPAM"]
        # Docker 29's netip.Prefix can serialize an unset range as an empty
        # string. Admit only that representation or omission, never a range.
        require(type(ipam) is dict and set(ipam) == {"Driver", "Options", "Config"}
                and ipam["Driver"] == "default" and ipam["Options"] == {}
                and ipam["Config"] in ([config], [{**config, "IPRange": ""}]))
        require(row["Id"] == full_id(identifier) and row["Name"] == self.name
                and row["Labels"] == {LABEL: self.policy.token}
                and row["Driver"] == "bridge" and row["Scope"] == "local"
                and row["EnableIPv4"] is True and row["EnableIPv6"] is False
                and row["Internal"] is False and row["Attachable"] is False
                and row["Ingress"] is False and row["ConfigOnly"] is False
                and row["ConfigFrom"] == {"Network": ""} and row["Options"] == self.options
                and row["Containers"] == {} and not row.get("Peers") and not row.get("Services"))


JOURNAL_LIMIT = 4096


class Journal:
    """Private append-only, fsynced intent. Missing completion never means failure."""

    def __init__(self, root, network, wall, monotonic):
        require(type(network) is Network and all(type(v) in (int, float) and 0 < v < 1e12
                                               for v in (wall, monotonic)))
        self.root, self.network = Path(root), network
        self.contract = {"schema": SCHEMA, "network": asdict(network),
                         "hard_wall": wall, "hard_monotonic": monotonic}

    def _directory(self):
        return tau_runtime.directory(self.root)

    def _check(self, fd):
        info = os.fstat(fd)
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)

    @staticmethod
    def file_limit(name):
        return JOURNAL_LIMIT

    def _write(self, fd, name, value):
        raw = policy.encoded(value) + b"\n"
        require(len(raw) <= self.file_limit(name))
        target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(target, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(fd)

    def initialize(self):
        with self._directory() as fd:
            self._check(fd)
            with os.scandir(fd) as entries:
                require(next(entries, None) is None)
            self._write(fd, "contract.json", self.contract)

    def read(self):
        with self._directory() as fd:
            self._check(fd)
            names = set()
            with os.scandir(fd) as entries:
                for entry in entries:
                    require(len(names) < len(EVENTS) + 1)
                    names.add(entry.name)
            require("contract.json" in names and names <= {"contract.json", *(e + ".json" for e in EVENTS)})
            values = {}
            for name in sorted(names):
                source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(source, "rb") as stream:
                    before = os.fstat(stream.fileno())
                    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                            and before.st_uid == os.getuid() and stat.S_IMODE(before.st_mode) == 0o600
                            and 0 < before.st_size <= 4096)
                    raw = stream.read(4097)
                    require(tau_runtime.identity(before) == tau_runtime.identity(os.fstat(stream.fileno())))
                values[name] = policy.decode(raw)
        require(policy.encoded(values.pop("contract.json")) == policy.encoded(self.contract))
        result = {}
        for event in EVENTS:
            name = event + ".json"
            if name not in values:
                continue
            require(len(result) == EVENTS.index(event))
            row = values[name]
            require(type(row) is dict and row.get("event") == event)
            if event == "network-resolved":
                require(set(row) == {"event", "id"})
                full_id(row["id"])
            else:
                require(row == {"event": event})
            result[event] = row
        return result

    def record(self, event, identifier=None):
        current = self.read()
        require(len(current) < len(EVENTS) and event == EVENTS[len(current)])
        row = {"event": event}
        if event == "network-resolved":
            row["id"] = full_id(identifier)
        else:
            require(identifier is None)
        with self._directory() as fd:
            self._check(fd)
            self._write(fd, event + ".json", row)


class Commands(smoke.Docker):
    """Same deadline and process-group cleanup as smoke; stdin for nft batches."""

    @property
    def end(self):
        self.remaining()
        return self.deadline

    def run(self, argv, *, data=None):
        left = self.remaining() - smoke.CLIENT_REAP
        require(left > 0)
        with tempfile.TemporaryFile() as stream:
            if data is not None:
                require(type(data) is bytes and len(data) <= 262144)
                stream.write(data)
                stream.seek(0)
            result = self.recorder.snapshot.execute_process_group(argv, env=self.env,
                timeout=min(10, left), max_output_bytes=262144, max_stderr_bytes=65536, stdin=stream)
        require(type(result.returncode) is int and result.returncode == 0)
        return result

    def rules(self, network):
        return self.run(network.policy.readback_args()).stdout

    def apply(self, batch):
        self.run(["nft", "--check", "--json", "--file", "-"], data=batch)
        self.run(["nft", "--json", "--file", "-"], data=batch)


class Reservation:
    """One network create, no container calls, no retry and no policy removal."""

    def __init__(self, commands, journal):
        require(isinstance(commands, Commands) and type(journal) is Journal)
        require(commands.wall == journal.contract["hard_wall"]
                and commands.deadline == journal.contract["hard_monotonic"])
        self.commands, self.journal, self.network = commands, journal, journal.network
        self.quarantine = policy.Quarantine(self.network.policy)

    def admit(self):
        require(os.getuid() == 0 and proof.sys.platform == "linux"
                and 0 < self.commands.remaining() <= 4800)
        require(self.commands.env == {**proof.ENV, "HOME": "/nonexistent",
            "DOCKER_CONFIG": "/nonexistent/nib-lab-docker-config",
            "DOCKER_HOST": "unix:///var/run/docker.sock"})
        proof.check_venue(self.commands, proof.ORDINARY_VENUE)
        require(self.commands.run(["nft", "--version"]).stdout.strip()
                == b"nftables v1.1.6 (Commodore Bullmoose #7)")
        row = policy.decode(self.commands.run(["docker", "version", "--format", "{{json .Server}}"]).stdout)
        require(row["Version"] == "29.1.3" and row["Os"] == "linux" and row["Arch"] == "amd64")

    def inventory(self):
        raw = self.commands.call(["docker", "network", "ls", "--no-trunc", "--format", "{{json .}}"], limit=262144)
        lines = raw.splitlines()
        require(len(lines) <= 256)
        rows = [policy.decode(line) for line in lines]
        require(len({full_id(row["ID"]) for row in rows}) == len(rows))
        # Docker network names are not unique. Never select by label/name alone.
        selected = [row for row in rows if row["Name"] == self.network.name]
        require(len(selected) <= 1)
        return selected

    def inspect(self, identifier):
        row = policy.decode(self.commands.call(["docker", "network", "inspect", "--format", "{{json .}}",
                                               full_id(identifier)], limit=262144))
        self.network.check(row, identifier)
        return row

    def protected(self):
        raw = self.commands.rules(self.network)
        # Commands rejects every nonzero/unknown exit before returning bytes.
        self.quarantine.verify(raw, install_exit_code=0, readback_exit_code=0)
        self.network.policy.verify(raw)

    def quarantined(self):
        self.quarantine.verify(self.commands.rules(self.network),
                               install_exit_code=0, readback_exit_code=0)

    def reserve(self):
        self.admit()
        require(not self.journal.read() and not self.inventory())
        links = policy.decode(self.commands.run(["ip", "--json", "link", "show"]).stdout)
        require(type(links) is list and len(links) <= 1024
                and all(row["ifname"] != self.network.policy.bridge for row in links))
        raw = policy.decode(self.commands.rules(self.network))
        require(type(raw) is dict and set(raw) == {"nftables"} and type(raw["nftables"]) is list)
        for row in raw["nftables"]:
            require(type(row) is dict and len(row) == 1)
            body = next(iter(row.values()))
            require(type(body) is dict and body.get("name") not in
                    (self.quarantine.table, self.network.policy.table) and body.get("table") not in
                    (self.quarantine.table, self.network.policy.table))
        for kind, selected in (("quarantine", self.quarantine), ("policy", self.network.policy)):
            self.journal.record(kind + "-intent")
            self.commands.apply(selected.batch())
            raw = self.commands.rules(self.network)
            if kind == "quarantine":
                selected.verify(raw, install_exit_code=0, readback_exit_code=0)
            else:
                selected.verify(raw)
            self.journal.record(kind + "-applied")
        self.protected()
        self.journal.record("network-intent")
        identifier = full_id(self.commands.call(self.network.command(), limit=128).decode().strip())
        # Durably bind the returned ID before any subsequent inspection may fail.
        self.journal.record("network-resolved", identifier)
        self.inspect(identifier)
        rows = self.inventory()
        require(len(rows) == 1 and rows[0]["ID"] == identifier)
        self.protected()
        return identifier

    def cleanup(self):
        """Never treat a missing object as resolution of an interrupted create."""
        events = self.journal.read()
        require("network-intent" in events and "network-removed" not in events)
        self.quarantined()
        rows = self.inventory()
        expected = events.get("network-resolved", {}).get("id")
        if rows:
            identifier = full_id(rows[0]["ID"])
            require(expected is None or identifier == expected)
            self.inspect(identifier)
            self.quarantined()
            self.commands.call(["docker", "network", "rm", identifier], limit=4096)
        require(not self.inventory())
        if expected is not None:
            # Also query the exact ID: a renamed original is not absence.
            raw = self.commands.call(["docker", "network", "ls", "--no-trunc", "--quiet",
                                      "--filter", "id=" + expected], limit=4096)
            require(not raw.strip())
            self.journal.record("network-removed")
        self.quarantined()
        return {"network_absent_observed": True, "create_resolved": expected is not None,
                "network_cleanup_confirmed": expected is not None, "quarantine_retained": True,
                "runtime_started": False, "published": False, "deployable": False,
                "hardware_validated": False, "confidential": False}
