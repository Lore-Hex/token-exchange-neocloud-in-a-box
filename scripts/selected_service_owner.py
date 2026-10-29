# SPDX-License-Identifier: Apache-2.0
"""Finite ordinary-service ownership. No resource is identified by a PID alone."""

from dataclasses import asdict
from contextlib import nullcontext
import ipaddress
import os
from pathlib import Path
import re
import stat
import time

import selected_service_network as network

policy, smoke = network.policy, network.smoke
require = network.require
EVENTS = ("container-intent", "container-resolved", "start-intent")
FILES = {"contract.json", "runtime.json", *(event + ".json" for event in EVENTS)}
# The pinned CUDA image's required driver constraints alone exceed 2 KiB.
# Only the full runtime intent needs more than the network/event record bound.
RUNTIME_LIMIT = 8192


def image_labels(value):
    """Copy only bounded labels from the already admitted image config."""
    if value is None:
        return {}
    require(type(value) is dict and len(value) <= 64 and smoke.LABEL not in value)
    require(all(type(key) is str and 0 < len(key) <= 256 and "\0" not in key
                and type(item) is str and len(item) <= 1024 and "\0" not in item
                for key, item in value.items()))
    return dict(value)


def daemon_identity(commands, pid=None):
    """Bind the trusted local daemon to our proc/network view, not a guessed PID."""
    deadline = time.monotonic() + min(10, commands.remaining() - smoke.CLIENT_REAP)
    require(time.monotonic() < deadline)
    executable = Path("/usr/bin/dockerd")
    if pid is None:
        candidates = []
        with os.scandir("/proc") as entries:
            for count, entry in enumerate(entries):
                require(count < 16384 and time.monotonic() < deadline)
                if not entry.name.isdecimal():
                    continue
                try:
                    if os.readlink(Path(entry.path) / "exe") == str(executable):
                        candidates.append(int(entry.name))
                except (FileNotFoundError, ProcessLookupError):
                    continue
        require(len(candidates) == 1)
        pid = candidates[0]
    require(type(pid) is int and pid > 1)
    root = Path("/proc") / str(pid)
    raw = smoke.service.observer.read_kernel(root / "stat", 4096, deadline)
    require(raw.startswith(str(pid).encode() + b" ("))
    fields = raw[raw.rindex(b") ") + 2:].split()
    require(len(fields) >= 20 and fields[0] not in (b"Z", b"X", b"x") and root.stat().st_uid == 0)
    start = int(fields[19])
    require(start > 0 and os.readlink(root / "exe") == str(executable))
    image, actual = executable.stat(), (root / "exe").stat()
    require((image.st_dev, image.st_ino) == (actual.st_dev, actual.st_ino))
    spaces = []
    for name in ("net", "pid", "user"):
        current, other = os.stat("/proc/self/ns/" + name), os.stat(root / ("ns/" + name))
        require((current.st_dev, current.st_ino) == (other.st_dev, other.st_ino))
        spaces.append((current.st_dev, current.st_ino))
    socket = Path("/var/run/docker.sock")
    require(socket.resolve() == Path("/run/docker.sock"))
    info = Path("/run/docker.sock").lstat()
    require(stat.S_ISSOCK(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o007)
    require(smoke.service.observer.read_kernel(root / "stat", 4096, deadline).rsplit(b") ", 1)[1].split()[19]
            == fields[19])
    return (pid, start, actual.st_dev, actual.st_ino, tuple(spaces), info.st_dev, info.st_ino, info.st_ctime_ns)


def host_target(commands, selected, *, initial):
    """Reject collisions before inserting a drop on an operator-supplied tuple."""
    rows = policy.decode(commands.run(["ip", "--json", "address", "show"]).stdout)
    require(type(rows) is list and len(rows) <= 1024)
    subnet = ipaddress.IPv4Network(selected.subnet)
    ingress = [row for row in rows if row["ifname"] == selected.policy.ingress]
    require(len(ingress) == 1 and "UP" in ingress[0]["flags"])
    matches = []
    for row in rows:
        require(type(row["addr_info"]) is list and len(row["addr_info"]) <= 256)
        for address in row["addr_info"]:
            if address["family"] != "inet":
                continue
            current = ipaddress.IPv4Interface(str(address["local"]) + "/" + str(address["prefixlen"]))
            if str(current.ip) == selected.policy.published:
                require(row["ifname"] == selected.policy.ingress and address["scope"] == "global"
                        and not address.get("tentative") and not address.get("dadfailed")
                        and not address.get("deprecated"))
                matches.append(address)
            require(not current.network.overlaps(subnet) or
                    (not initial and row["ifname"] == selected.policy.bridge
                     and str(current.ip) == selected.gateway and current.network == subnet))
    require(len(matches) == 1)
    if not initial:
        bridges = [row for row in rows if row["ifname"] == selected.policy.bridge]
        require(len(bridges) == 1 and "UP" in bridges[0]["flags"])
        addresses = [(row["local"], row["prefixlen"]) for row in bridges[0]["addr_info"]
                     if row["family"] == "inet"]
        require(addresses == [(selected.gateway, subnet.prefixlen)])
    routes = policy.decode(commands.run(["ip", "--json", "-4", "route", "show", "table", "all"]).stdout)
    require(type(routes) is list and len(routes) <= 4096)
    for row in routes:
        destination = row.get("dst")
        require(type(destination) is str)
        if destination in ("default", "0.0.0.0/0"):
            continue
        route = ipaddress.IPv4Network(destination if "/" in destination else destination + "/32")
        require(not route.overlaps(subnet) or (not initial and row.get("dev") == selected.policy.bridge
                                               and route.subnet_of(subnet)))
    if initial:
        # This first owner admits an otherwise idle Docker venue. This also
        # excludes another container's requested-but-not-yet-listening binding.
        require(not commands.call(["docker", "container", "ls", "--all", "--quiet", "--no-trunc"],
                                  limit=262144).strip())
        # Both address families: an IPv6 wildcard can also occupy an IPv4 port.
        # These fixed proc files are kernel observations, never operator paths.
        for name in ("tcp", "tcp6"):
            raw = smoke.service.observer.read_kernel("/proc/net/" + name, 262144,
                time.monotonic() + min(1, commands.remaining() - smoke.CLIENT_REAP))
            rows = raw.splitlines()
            require(rows and b"local_address" in rows[0])
            for row in rows[1:]:
                fields = row.split()
                require(len(fields) >= 10)
                if fields[3] == b"0A":
                    require(int(fields[1].rsplit(b":", 1)[1], 16) != selected.policy.port)


class State(network.Journal):
    """A separate private container journal using the reservation's FD/fsync writes."""

    def __init__(self, root):
        self.root = Path(root)

    @staticmethod
    def file_limit(name):
        return RUNTIME_LIMIT if name == "runtime.json" else network.JOURNAL_LIMIT

    def initialize(self, contract, runtime):
        with self._directory() as fd:
            self._check(fd)
            with os.scandir(fd) as entries:
                require(next(entries, None) is None)
            self._write(fd, "contract.json", contract)
            self._write(fd, "runtime.json", runtime)
        self.read()

    def read(self):
        result = {}
        with self._directory() as fd:
            self._check(fd)
            names = set()
            with os.scandir(fd) as entries:
                for entry in entries:
                    require(len(names) < len(FILES))
                    names.add(entry.name)
            require({"contract.json", "runtime.json"} <= names <= FILES)
            for name in sorted(names):
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                        and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1)
                result[name] = policy.decode(network.tau_runtime.read_file(
                    self.root / name, self.file_limit(name), time.monotonic() + 1))
        contract, runtime = result["contract.json"], result["runtime.json"]
        require(type(contract) is dict)
        profile = contract.get("lifetime_profile", smoke.service.SMOKE_PROFILE)
        smoke.service.lifetime_seconds(profile)
        ordinary = smoke.service.ordinary_profile(profile)
        require(set(contract) == {"schema", "network", "wall", "monotonic"}
                | ({"lifetime_profile"} if ordinary else set())
                and contract["schema"] == "nib.selected-service-owner-intent/v" + ("2" if ordinary else "1"))
        selected = network.Network(policy.Policy(**contract["network"]["policy"]),
            contract["network"]["subnet"], contract["network"]["gateway"])
        require(policy.encoded(contract["network"]) == policy.encoded(asdict(selected)))
        require(all(type(contract[key]) in (int, float) and 0 < contract[key] < 1e12
                    for key in ("wall", "monotonic")))
        require(type(runtime) is dict and set(runtime) == {
            "command", "network_id", "image_id", "image_labels", "entrypoint", "weights", "inputs"})
        network.full_id(runtime["network_id"])
        smoke.image_id(runtime["image_id"])
        require(type(runtime["image_labels"]) is dict)
        image_labels(runtime["image_labels"])
        for key in ("command", "entrypoint"):
            require(type(runtime[key]) is list and 0 < len(runtime[key]) <= 256
                    and all(type(item) is str and "\0" not in item for item in runtime[key]))
        for key in ("weights", "inputs"):
            smoke.private_path(runtime[key])
        command = runtime["command"][runtime["command"].index(runtime["image_id"]) + 1:]
        if "--original-view-sha256" in command:
            require(command.count("--original-view-sha256") == 1
                and command.index("--original-view-sha256") + 1 < len(command)
                and re.fullmatch("[0-9a-f]{64}", command[command.index("--original-view-sha256") + 1])
                and command.count("--execution-profile") == 1
                and command[command.index("--execution-profile") + 1] == smoke.service.engine_entry.ORIGINAL_MILLION_PROFILE)
        if ordinary:
            for flag, value in (("--lifetime-profile", profile), ("--hard-stop-at",
                    smoke.service.retirement_at(contract["wall"] - smoke.HOST_RESERVE))):
                require(command.count(flag) == 1 and command[command.index(flag) + 1] == value)
            require(not any(flag in command for flag in
                ("--inference-smoke", "--conformance-smoke", "--protocol-smoke", "--tool-smoke")))
        else:
            require("--lifetime-profile" not in command)
        if "--long-qualification" in command:
            require(ordinary and command.count("--long-qualification") == 1
                    and command.count("--execution-profile") == 1
                    and command.index("--execution-profile") + 1 < len(command)
                    and command[command.index("--execution-profile") + 1] in smoke.service.engine_entry.LONG_CONTEXT_PROFILES)
        prior = True
        for event in EVENTS:
            row = result.get(event + ".json")
            if row is None:
                prior = False
                continue
            require(prior and type(row) is dict)
            if event == "container-resolved":
                require(set(row) == {"event", "id"} and row["event"] == event)
                network.full_id(row["id"])
            else:
                require(row == {"event": event})
        return result

    def record(self, event, identifier=None):
        rows = self.read()
        current = sum(event + ".json" in rows for event in EVENTS)
        require(current < len(EVENTS) and event == EVENTS[current])
        value = {"event": event}
        if event == "container-resolved":
            value["id"] = network.full_id(identifier)
        else:
            require(identifier is None)
        with self._directory() as fd:
            self._check(fd)
            self._write(fd, event + ".json", value)


def owner_intent(journal):
    ordinary = smoke.service.ordinary_profile(journal.lifetime_profile)
    result = {"schema": "nib.selected-service-owner-intent/v" + ("2" if ordinary else "1"),
        "network": asdict(journal.network), "wall": journal.contract["hard_wall"],
        "monotonic": journal.contract["hard_monotonic"]}
    if ordinary:
        result["lifetime_profile"] = journal.lifetime_profile
    return result


def container_command(selected, identifier, build, base, weights, inputs, args, stop_at):
    token = selected.policy.token
    result = smoke.container_command("nib-selected-" + token, token, build, base,
                                     weights, inputs, args, stop_at)
    offset = result.index("--network")
    require(result[offset + 1] == "none")
    result[offset:offset + 2] = ["--network", network.full_id(identifier),
        "--ip", selected.policy.address, "--dns", "127.0.0.1", "--publish",
        f"{selected.policy.published}:{selected.policy.port}:8443/tcp"]
    return result


def check_container(info, identifier, selected, runtime):
    token, network_id = selected.policy.token, runtime["network_id"]
    smoke.check_container_runtime(info, network.full_id(identifier), "nib-selected-" + token, token,
        {"image_id": runtime["image_id"]}, {"Config": {"Entrypoint": runtime["entrypoint"]}},
        runtime["command"], runtime["weights"], runtime["inputs"])
    host, settings, state = info["HostConfig"], info["NetworkSettings"], info["State"]
    require(info["Config"]["Labels"] == {**runtime["image_labels"], smoke.LABEL: token}
            and info["Config"]["ExposedPorts"] == {"8443/tcp": {}}
            and host["NetworkMode"] == network_id and not host.get("PublishAllPorts")
            and not host.get("Links") and not host.get("AutoRemove"))
    policy.check_dns(info)
    ports = {"8443/tcp": [{"HostIp": selected.policy.published, "HostPort": str(selected.policy.port)}]}
    require(host["PortBindings"] == ports and set(settings["Networks"]) == {selected.name})
    endpoint = settings["Networks"][selected.name]
    require(type(endpoint) is dict and set(endpoint) == {
        "IPAMConfig", "Links", "Aliases", "DriverOpts", "GwPriority", "NetworkID", "EndpointID",
        "Gateway", "IPAddress", "MacAddress", "IPPrefixLen", "IPv6Gateway", "GlobalIPv6Address",
        "GlobalIPv6PrefixLen", "DNSNames"})
    ipam = endpoint["IPAMConfig"]
    require(type(ipam) is dict and {"IPv4Address"} <= set(ipam) <= {"IPv4Address", "IPv6Address", "LinkLocalIPs"}
            and ipam["IPv4Address"] == selected.policy.address and ipam.get("IPv6Address", "") == ""
            and ipam.get("LinkLocalIPs") in (None, [])
            and endpoint["Links"] in (None, []) and endpoint["Aliases"] in (None, [])
            and endpoint["DriverOpts"] in (None, {}) and type(endpoint["GwPriority"]) is int
            and endpoint["GwPriority"] == 0 and endpoint["GlobalIPv6Address"] == ""
            and endpoint["IPv6Gateway"] == "" and type(endpoint["GlobalIPv6PrefixLen"]) is int
            and endpoint["GlobalIPv6PrefixLen"] == 0)
    require(state["Running"] is (state["Status"] == "running")
            and state["Status"] in ("created", "running", "exited")
            and state["Paused"] is False and state["Restarting"] is False
            and state["Dead"] is False and state["OOMKilled"] is False)
    if state["Running"]:
        require(endpoint["NetworkID"] == network_id and endpoint["IPAddress"] == selected.policy.address
                and endpoint["Gateway"] == selected.gateway and type(endpoint["IPPrefixLen"]) is int
                and endpoint["IPPrefixLen"] == ipaddress.IPv4Network(selected.subnet).prefixlen
                and re.fullmatch("(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", endpoint["MacAddress"])
                and settings["Ports"] == ports and type(state["Pid"]) is int and state["Pid"] > 1
                and endpoint["DNSNames"] == ["nib-selected-" + token, identifier[:12]])
        network.full_id(endpoint["EndpointID"])
    else:
        require(not settings["Ports"] and type(state["Pid"]) is int and state["Pid"] == 0
                and endpoint["NetworkID"] in ("", network_id) and endpoint["EndpointID"] == ""
                and endpoint["Gateway"] == endpoint["IPAddress"] == endpoint["MacAddress"] == ""
                and type(endpoint["IPPrefixLen"]) is int and endpoint["IPPrefixLen"] == 0
                and endpoint["DNSNames"] in (None, [], ["nib-selected-" + token, identifier[:12]]))
    return endpoint


class Owner:
    def __init__(self, commands, reservation, state):
        require(type(reservation) is network.Reservation and reservation.commands is commands
                and type(state) is State)
        self.commands, self.reservation, self.state = commands, reservation, state
        self.selected = reservation.network
        self.initial = state.read()
        self.contract, self.runtime = self.initial["contract.json"], self.initial["runtime.json"]
        require(self.contract == owner_intent(reservation.journal))

    def rows(self):
        rows = self.state.read()
        require(rows["contract.json"] == self.contract and rows["runtime.json"] == self.runtime)
        return rows

    def owned(self, identifier=None):
        return self.commands.owned("nib-selected-" + self.selected.policy.token,
            self.selected.policy.token, self.runtime["image_id"], identifier)

    def fresh(self, *, serving, verifying=False):
        require(not verifying or (not serving and "--original-view-sha256" in self.runtime["command"]))
        rows = self.rows()
        identifier = rows["container-resolved.json"]["id"]
        # Start/exit can occur between Docker's two separate inspect endpoints.
        # Resample only a changed lifecycle snapshot, never a mismatched stable
        # observation. There is no second create/start or deadline extension.
        for _ in range(3):
            info = self.owned(identifier)
            require(info is not None)
            endpoint = check_container(info, identifier, self.selected, self.runtime)
            if not serving and not verifying:
                require(info["State"]["Status"] == "created")
            found = self.reservation.inventory()
            require(len(found) == 1 and found[0]["ID"] == self.runtime["network_id"])
            raw = self.commands.call(["docker", "network", "inspect", "--format", "{{json .}}",
                                      self.runtime["network_id"]], limit=262144)
            observed = policy.decode(raw)
            after = self.owned(identifier)
            require(after is not None)
            check_container(after, identifier, self.selected, self.runtime)
            if info["State"] == after["State"] and info["NetworkSettings"] == after["NetworkSettings"]:
                break
        else:
            require(False)
        attachments = observed["Containers"]
        # Validate the network definition separately, without fabricating a
        # network-none container inspection or accepting foreign endpoints.
        expected = {} if not info["State"]["Running"] else {identifier: {
            "Name": "nib-selected-" + self.selected.policy.token,
            "EndpointID": endpoint["EndpointID"], "MacAddress": endpoint["MacAddress"],
            "IPv4Address": self.selected.policy.address + "/" + self.selected.subnet.split("/")[1],
            "IPv6Address": ""}}
        require(attachments == expected)
        self.selected.check({**observed, "Containers": {}}, self.runtime["network_id"])
        raw = self.commands.rules(self.selected)
        self.selected.policy.verify(raw)
        if serving:
            require(all(body.get("table") != self.reservation.quarantine.table
                        and body.get("name") != self.reservation.quarantine.table
                        for row in policy.decode(raw)["nftables"] for body in row.values()))
        elif verifying:
            self.reservation.protected()
        host_target(self.commands, self.selected, initial=False)
        return info

    def create(self):
        require("container-intent.json" not in self.rows() and self.owned() is None)
        self.reservation.protected()
        self.reservation.inspect(self.runtime["network_id"])
        registration = nullcontext()
        if "--original-view-sha256" in self.runtime["command"]:
            import original_view_mount
            registration = original_view_mount.registration(self)
        with registration:
            self.state.record("container-intent")
            identifier = network.full_id(self.commands.call(self.runtime["command"], limit=128).decode().strip())
            self.state.record("container-resolved", identifier)
            self.fresh(serving=False)
            self.reservation.protected()
            return identifier

    def release_for_serving(self):
        """Watchdog-only transition; deliberately NOT the teardown removal API."""
        original = "--original-view-sha256" in self.runtime["command"]
        if original:
            import original_view_mount
            require("start-intent.json" in self.rows())
            original_view_mount.verify_release(self)
        else:
            require("start-intent.json" not in self.rows())
            self.fresh(serving=False)
        self.reservation.protected()
        if not original:
            self.state.record("start-intent")
        # Live resources are admitted above, not falsely declared absent. The
        # serving table remains installed; no other table is flushed/replaced.
        batch = policy.encoded({"nftables": [{"delete": {"table": {
            "family": family, "name": self.reservation.quarantine.table}}} for family in ("bridge", "inet")]})
        self.commands.apply(batch)
        self.fresh(serving=True)

    def begin_original_verification(self):
        require("--original-view-sha256" in self.runtime["command"]
                and "start-intent.json" not in self.rows())
        self.fresh(serving=False)
        self.reservation.protected()
        self.state.record("start-intent")

    def quarantine(self):
        try:
            self.reservation.quarantined()
        except (ValueError, KeyError):
            self.commands.apply(self.reservation.quarantine.batch())
            self.reservation.quarantined()

    def cleanup(self):
        result = {"container_absent": False, "network_absent": False, "creations_resolved": False,
                  "quarantine_confirmed": False, "policy_removed": False, "cleanup_confirmed": False}
        try:
            self.quarantine()
            result["quarantine_confirmed"] = True
        except BaseException:
            pass
        try:
            rows = self.rows()
            identifier = rows.get("container-resolved.json", {}).get("id")
            if "container-intent.json" in rows:
                # Even if quarantine installation failed, stop only the owned
                # workload. Never report successful confinement in that case.
                self.commands.cleanup("nib-selected-" + self.selected.policy.token,
                    self.selected.policy.token, self.runtime["image_id"], identifier)
            require(self.owned(identifier) is None and self.owned() is None)
            result["container_absent"] = True
            cleaned = self.reservation.cleanup()
            result["network_absent"] = cleaned["network_absent_observed"]
            result["creations_resolved"] = identifier is not None and cleaned["create_resolved"]
            if not result["creations_resolved"] or not result["quarantine_confirmed"]:
                return result
            self.commands.apply(self.selected.policy.remove_batch(containers_absent=True, creations_resolved=True))
            self.commands.apply(self.reservation.quarantine.remove_batch(
                containers_absent=True, networks_absent=True, creations_resolved=True))
            rows = policy.decode(self.commands.rules(self.selected))["nftables"]
            owned = (self.selected.policy.table, self.reservation.quarantine.table)
            require(all(body.get("table") not in owned and body.get("name") not in owned
                        for row in rows for body in row.values()))
            result.update(policy_removed=True, cleanup_confirmed=True)
        except BaseException:
            pass
        return result
