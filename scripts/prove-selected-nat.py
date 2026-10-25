#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Offline, GPU-free NAT experiment in new mount/network/PID namespaces only."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        sys.exit("Use python3 -I -S -B scripts/prove-selected-nat.py")
    sys.path.insert(0, str(ROOT / "scripts"))

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import os
import platform
import re
import signal
import stat
import subprocess
import tempfile
import time
import uuid

from e2e import snapshot
from fixtures import selected_nat_fixture as fixture
import selected_network_policy as policy

require, encoded, decode = policy.require, policy.encoded, policy.decode
CONFIG = "sha256:64d91f7b885eed272bba87909446b12ff408d4aaa5f1a0e9ca787bbea1a020b9"
MANIFEST = "sha256:6e13e65c55e33adf203d77ee371cf8bf5d81bd4902ef07565721f46bf44917af"
LABEL = "nib.selected-nat-proof"
SOURCES = ("scripts/prove-selected-nat.py", "scripts/selected_network_policy.py",
           "scripts/fixtures/selected_nat_fixture.py", "scripts/e2e/snapshot.py")
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1"}
ENV.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS="1")
SERVICE, INGRESS = "nib-service", "nib-ingress"
S4, P4, C4, H4 = "198.18.0.2", "198.18.0.3", "198.19.0.2", "198.19.0.1"
S6, P6, C6, H6 = "fd00:4e49:1::2", "fd00:4e49:1::3", "fd00:4e49:2::2", "fd00:4e49:2::1"
DNS = "127.0.0.2"
OPERATIONS, CLEANUP, REAP = 480, 90, 25


@contextmanager
def cancellation(notify=None):
    state = {"cancelled": False}
    def cancel(_signum, _frame):
        first = not state["cancelled"]
        state["cancelled"] = True
        if first and notify is not None:
            notify()
    previous = {sig: signal.signal(sig, cancel) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        yield state
    finally:
        state["cancelled"] = True
        for sig, handler in previous.items():
            signal.signal(sig, handler)


class Commands:
    def __init__(self, end, cancelled, work):
        self.end, self.cancelled, self.work = end, cancelled, work
        self.cleaning = False
        self.env = {**ENV, "HOME": str(work), "DOCKER_CONFIG": str(work / "client"),
                    "DOCKER_HOST": "unix://" + str(work / "docker.sock")}

    def run(self, argv, *, data=None, check=True, seconds=10):
        reserve = 3 * REAP if self.cleaning else REAP
        left = self.end + (CLEANUP if self.cleaning else 0) - time.monotonic() - reserve
        require(left > 0 and (self.cleaning or not self.cancelled["cancelled"]))
        with tempfile.TemporaryFile() as stdin:
            if data is not None:
                stdin.write(data)
                stdin.seek(0)
            result = snapshot.execute_process_group(argv, env=self.env, timeout=min(seconds, left),
                max_output_bytes=262144, max_stderr_bytes=65536, stdin=stdin)
        require(not check or result.returncode == 0)
        return result

    def docker(self, *args, **kwargs):
        return self.run(["docker", *args], **kwargs)

    def inspect(self, kind, name):
        row = decode(self.docker(kind, "inspect", name).stdout)
        require(type(row) is list and len(row) == 1 and type(row[0]) is dict)
        return row[0]

    def nft(self, data, report=None):
        if report is not None:
            report["policy_step"] = "check"
        self.run(["nft", "--check", "--json", "--file", "-"], data=data)
        if report is not None:
            report["policy_step"] = "apply"
        self.run(["nft", "--json", "--file", "-"], data=data)

    def rules(self):
        return self.run(["nft", "--json", "list", "ruleset"]).stdout


def namespace_command(work, archive, host_net, host_mount, deadline):
    return ["unshare", "--mount", "--net", "--pid", "--fork", "--mount-proc=/proc",
        "--propagation", "private", "--kill-child=KILL", "--", sys.executable, "-I", "-S", "-B",
        str(work / "scripts/prove-selected-nat.py"), "--inner", str(work), "--archive", str(archive),
        "--host-net", str(host_net), "--host-mount", str(host_mount), "--deadline", str(deadline)]


def check_namespace(host_net, host_mount):
    require(sys.platform == "linux" and os.getuid() == 0 and os.getpid() == 1)
    require(os.stat("/proc/self/ns/net").st_ino != host_net
            and os.stat("/proc/self/ns/mnt").st_ino != host_mount)
    require(Path("/proc/1/stat").read_text().split()[0] == "1")


def daemon_command(work):
    return ["dockerd", "--config-file", str(work / "daemon.json"), "--data-root", str(work / "data"),
        "--exec-root", str(work / "exec"), "--pidfile", str(work / "daemon.pid"),
        "--host", "unix://" + str(work / "docker.sock"), "--bridge", "none", "--storage-driver", "vfs",
        "--exec-opt", "native.cgroupdriver=cgroupfs", "--cgroup-parent", "/" + cgroup_name(work),
        "--cdi-spec-dir", str(work / "cdi"), "--log-level", "error"]


def cgroup_name(work):
    return "nib-nat-" + hashlib.sha256(str(work).encode()).hexdigest()[:24]


def cleanup_cgroup(work):
    """Only remove an empty, uniquely named cgroup; never write cgroup.kill."""
    root = Path("/sys/fs/cgroup") / cgroup_name(work)
    if not root.exists():
        return
    require(not root.is_symlink() and root.stat().st_uid == 0)
    require("populated 0" in (root / "cgroup.events").read_text().splitlines())
    # Normal Docker removal must already have removed all child cgroups.
    require(not any(path.is_dir() for path in root.iterdir()))
    root.rmdir()


def copy_archive(source, target, expected):
    """Seal the bounded public image input before any namespace is created."""
    info = source.lstat()
    require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= 512 << 20)
    with os.fdopen(os.open(source, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream, target.open("xb") as output:
        actual = os.fstat(stream.fileno())
        stamp = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns)
        require(stamp(actual) == stamp(info))
        digest, size, end = hashlib.sha256(), 0, time.monotonic() + 30
        while block := stream.read(1 << 20):
            size += len(block)
            require(size <= info.st_size and time.monotonic() < end)
            digest.update(block)
            output.write(block)
        require(size == info.st_size and digest.hexdigest() == expected
                and stamp(os.fstat(stream.fileno())) == stamp(info))
    target.chmod(0o400)


def spawn(argv, env):
    return subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)


def fixture_command(work, addresses, ports, end):
    return [sys.executable, "-I", "-S", "-B", str(work / "scripts/fixtures/selected_nat_fixture.py"),
            "serve", *[part for address in addresses for part in ("--address", address)],
            *[part for port in ports for part in ("--port", str(port))], "--until", str(end)]


def container_command(name, token, network, address4, address6, work, end, *, protected=False):
    ports = (8000, 8001, 8443) if name == "candidate" else (53, 8450)
    argv = ["container", "create", "--name", name, "--label", LABEL + "=" + token,
        "--pull", "never", "--platform", "linux/amd64", "--network", network,
        "--ip", address4, "--ip6", address6, "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--security-opt", "apparmor=unconfined",
        "--user", "65532:65532", "--memory", "128m", "--memory-swap", "128m", "--cpus", "1",
        "--pids-limit", "32", "--restart", "no", "--log-driver", "none", "--no-healthcheck",
        "--sysctl", "net.ipv4.ip_unprivileged_port_start=0", "--entrypoint", "/usr/local/bin/python3.12",
        "--mount", "type=bind,src=" + str(work / "fixture") + ",dst=/fixture,readonly"]
    if protected:
        argv += ["--dns", "127.0.0.1"]
    if name == "candidate":
        argv += ["--publish", H4 + ":443:8443/tcp"]
    if name == "client":
        argv += ["--publish", "198.18.0.1:8450:8450/tcp", "--publish", "[fd00:4e49:1::1]:8450:8450/tcp"]
    return argv + [CONFIG, "-I", "-S", "-B", "/fixture/probe.py", "serve",
        "--address", "0.0.0.0", "--address", "::", *[part for port in ports for part in ("--port", str(port))],
        "--until", str(end), "--certificate", "/fixture/cert.pem", "--private-key", "/fixture/key.pem"]


def check_container(info, token, command, *, protected):
    def flag(name):
        return command[command.index(name) + 1]
    host, config = info["HostConfig"], info["Config"]
    require(info["Image"] == CONFIG and config["Image"] == CONFIG
            and config["Labels"].get(LABEL) == token and info["Name"] == "/" + flag("--name")
            and config["User"] == "65532:65532" and config["Entrypoint"] == ["/usr/local/bin/python3.12"]
            and config["Cmd"] == command[command.index(CONFIG) + 1:] and not config.get("Volumes"))
    require(host["NetworkMode"] == flag("--network") and host["ReadonlyRootfs"] is True
            and host["Privileged"] is False and host["CapDrop"] == ["ALL"] and not host.get("CapAdd")
            and not host.get("DeviceRequests") and not host.get("Devices") and not host.get("Binds")
            and host["Memory"] == host["MemorySwap"] == 128 << 20 and host["NanoCpus"] == 1000000000
            and host["PidsLimit"] == 32 and host["RestartPolicy"]["Name"] == "no"
            and host["LogConfig"]["Type"] == "none" and host["Sysctls"] == {"net.ipv4.ip_unprivileged_port_start": "0"}
            and not host.get("PublishAllPorts") and not host.get("PidMode"))
    require(set(host["SecurityOpt"]) in ({"no-new-privileges", "apparmor=unconfined"},
                                         {"no-new-privileges=true", "apparmor=unconfined"}))
    mounts = info["Mounts"]
    require(len(mounts) == 1 and mounts[0]["Type"] == "bind" and mounts[0]["Destination"] == "/fixture"
            and mounts[0]["Source"] == flag("--mount").split(",src=", 1)[1].split(",dst=", 1)[0]
            and mounts[0]["RW"] is False)
    expected = {}
    for index, value in enumerate(command):
        if value == "--publish":
            address, port, target = command[index + 1].rsplit(":", 2)
            expected.setdefault(target, []).append({"HostIp": address.strip("[]"), "HostPort": port})
    require((host.get("PortBindings") or {}) == expected)
    networks = info["NetworkSettings"]["Networks"]
    require(set(networks) == {flag("--network")} and networks[flag("--network")]["IPAMConfig"] == {
        "IPv4Address": flag("--ip"), "IPv6Address": flag("--ip6")})
    if info["State"]["Running"]:
        require(networks[flag("--network")]["IPAddress"] == flag("--ip")
                and networks[flag("--network")]["GlobalIPv6Address"] == flag("--ip6"))
    if protected:
        policy.check_dns(info)
    else:
        require(not host.get("Dns") and not host.get("DnsOptions") and not host.get("DnsSearch"))


def query(commands, name, address, port, kind="tcp"):
    result = commands.docker("container", "exec", name, "/usr/local/bin/python3.12", "-I", "-S", "-B",
        "/fixture/probe.py", "request", "--address", address, "--port", str(port), "--kind", kind,
        "--certificate", "/fixture/cert.pem", seconds=5)
    value = decode(result.stdout)
    require(type(value) is bool or kind == "stats")
    return value


def host_query(work, address, port, kind="tcp"):
    try:
        return fixture.request(address, port, kind, str(work / "fixture/cert.pem"))
    except (OSError, ValueError):
        return False


def observations(commands, work):
    return {
        "tls_host": host_query(work, H4, 443, "tls"),
        "tls_forward": query(commands, "client", H4, 443, "tls"),
        "host_engine": host_query(work, S4, 8000), "host_guard": host_query(work, S4, 8001),
        "host_ipv6_tls": host_query(work, S6, 8443, "tls"),
        "to_host4": query(commands, "candidate", H4, 8450),
        "to_host6": query(commands, "candidate", H6, 8450),
        "to_peer4": query(commands, "candidate", P4, 8450),
        "to_peer6": query(commands, "candidate", P6, 8450),
        "forward4": query(commands, "candidate", "198.18.0.1", 8450),
        "forward6": query(commands, "candidate", "fd00:4e49:1::1", 8450),
        "dns_udp": query(commands, "candidate", "127.0.0.11", 53, "dns-udp"),
        "dns_tcp": query(commands, "candidate", "127.0.0.11", 53, "dns-tcp"),
        "host_dns_udp": query(commands, "candidate", H4, 53, "dns-udp"),
        "host_dns_tcp": query(commands, "candidate", H4, 53, "dns-tcp"),
    }


def check_sinks(commands, server):
    require(server.poll() is None)
    # A dead listener is not evidence of a working drop rule.
    for name in ("peer", "client"):
        for address in ("127.0.0.1", "::1"):
            require(query(commands, name, address, 8450) is True)


def check_observations(observed, protected):
    names = {"tls_host", "tls_forward", "host_engine", "host_guard", "host_ipv6_tls",
             "to_host4", "to_host6", "to_peer4", "to_peer6", "forward4", "forward6",
             "dns_udp", "dns_tcp", "host_dns_udp", "host_dns_tcp"}
    require(type(observed) is dict and set(observed) == names)
    require(all(value is (not protected or name in ("tls_host", "tls_forward"))
                for name, value in observed.items()))


def owned_cleanup(commands, token, network_policy, creation_unknown, installed):
    """Never remove protection on uncertain creates, failed inventory or drift."""
    rows = commands.docker("container", "ls", "--all", "--quiet", "--no-trunc").stdout.splitlines()
    require(len(rows) <= 3)
    for raw in rows:
        identifier = raw.decode()
        require(re.fullmatch("[0-9a-f]{64}", identifier))
        info = commands.inspect("container", identifier)
        require(info["Image"] == CONFIG and info["Config"]["Labels"].get(LABEL) == token
                and info["Name"] in ("/candidate", "/peer", "/client"))
        commands.docker("container", "rm", "--force", identifier)
    require(not commands.docker("container", "ls", "--all", "--quiet").stdout.strip())
    # Retain both tables if absence is not proof of completed creation.
    require(not creation_unknown)
    for name in (SERVICE, INGRESS):
        names = commands.docker("network", "ls", "--format", "{{.Name}}").stdout.decode().splitlines()
        if name in names:
            row = commands.inspect("network", name)
            require(row["Labels"].get(LABEL) == token and not row["Containers"])
            commands.docker("network", "rm", row["Id"])
    remaining = commands.docker("network", "ls", "--format", "{{.Name}}").stdout.decode().splitlines()
    require(SERVICE not in remaining and INGRESS not in remaining)
    if installed:
        network_policy.verify(commands.rules())
        commands.nft(network_policy.remove_batch(containers_absent=True, creations_resolved=True))
        rules = decode(commands.rules())
        require(type(rules) is dict and type(rules.get("nftables")) is list)
        for row in rules["nftables"]:
            for kind, body in row.items():
                require(body.get("name" if kind == "table" else "table") != network_policy.table)
    return True


def inner(args):
    check_namespace(args.host_net, args.host_mount)
    work = args.inner
    report = {"schema": "nib.selected-nat-proof/v1", "result": "fail", "stage": "preflight",
              "namespace_verified": True, "cleanup_confirmed": False, "policy_removed": False,
              "gpu_used": False, "deployment_eligible": False, "confidential": False, "published": False}
    daemon = server = None
    installed, unknown = False, False
    token = uuid.uuid4().hex
    net_policy = policy.Policy(token, SERVICE, INGRESS, S4, H4, 443)
    with cancellation() as cancelled:
        commands = Commands(args.deadline, cancelled, work)
        try:
            require(0 < args.deadline - time.monotonic() <= OPERATIONS)
            links = decode(commands.run(["ip", "--json", "link", "show"]).stdout)
            require([row["ifname"] for row in links] == ["lo"])
            require(commands.run(["nft", "--version"]).stdout.decode().strip() == "nftables v1.0.9 (Old Doc Yak #3)")
            require(decode(commands.rules())["nftables"] in ([], [{"metainfo": {
                "version": "1.0.9", "release_name": "Old Doc Yak #3", "json_schema_version": 1}}]))
            # All mounts are private; hide operator runtime sockets before dockerd.
            commands.run(["mount", "-t", "tmpfs", "-o", "mode=0755,nosuid,nodev", "tmpfs", "/run"])
            commands.run(["mount", "--bind", str(work / "resolv.conf"), "/etc/resolv.conf"])
            commands.run(["ip", "link", "set", "lo", "up"])
            require(b"29.1.3" in commands.run(["dockerd", "--version"]).stdout)
            (work / "daemon.json").write_bytes(encoded({"features": {"containerd-snapshotter": False}}))
            (work / "cdi").mkdir()
            daemon = spawn(daemon_command(work), commands.env)
            ready = time.monotonic() + 30
            while True:
                require(daemon.poll() is None and time.monotonic() < ready)
                result = commands.docker("version", "--format", "{{json .Server}}", check=False)
                if result.returncode == 0:
                    version = decode(result.stdout)
                    require(version["Version"] == "29.1.3" and version["Os"] == "linux" and version["Arch"] == "amd64")
                    break
                time.sleep(.1)
            report["engine_version"] = version["Version"]
            report["stage"] = "image"
            commands.docker("image", "load", "--input", str(args.archive), seconds=60)
            image = commands.inspect("image", CONFIG)
            require(image["Id"] == CONFIG and image["Os"] == "linux" and image["Architecture"] == "amd64"
                    and not image["Config"].get("Volumes") and not image["Config"].get("OnBuild"))
            report.update(image_config=CONFIG, reference_manifest=MANIFEST)
            commands.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                "-subj", "/CN=nib.invalid", "-addext", "subjectAltName=DNS:nib.invalid",
                "-keyout", str(work / "fixture/key.pem"), "-out", str(work / "fixture/cert.pem")])
            os.chmod(work / "fixture/key.pem", 0o600)
            os.chown(work / "fixture/key.pem", 65532, 65532)
            os.chmod(work / "fixture/cert.pem", 0o444)
            for name, v4, v6 in ((SERVICE, "198.18.0.0/24", "fd00:4e49:1::/64"),
                                 (INGRESS, "198.19.0.0/24", "fd00:4e49:2::/64")):
                unknown = True
                commands.docker("network", "create", "--driver", "bridge", "--ipv6", "--subnet", v4,
                    "--subnet", v6, "--opt", "com.docker.network.bridge.name=" + name,
                    "--opt", "com.docker.network.bridge.gateway_mode_ipv4=nat",
                    "--opt", "com.docker.network.bridge.gateway_mode_ipv6=nat",
                    "--opt", "com.docker.network.bridge.enable_icc=true", "--label", LABEL + "=" + token, name)
                unknown = False
                row = commands.inspect("network", name)
                require(row["Name"] == name and row["Driver"] == "bridge" and row["Internal"] is False
                        and row["EnableIPv6"] is True and row["Labels"].get(LABEL) == token and not row["Containers"])
                require(row["Options"] == {"com.docker.network.bridge.name": name,
                    "com.docker.network.bridge.gateway_mode_ipv4": "nat",
                    "com.docker.network.bridge.gateway_mode_ipv6": "nat",
                    "com.docker.network.bridge.enable_icc": "true"})
                require({entry["Subnet"] for entry in row["IPAM"]["Config"]} == {v4, v6})
            server = spawn(fixture_command(work, (DNS, H4, H6), (53, 8450), args.deadline), commands.env)
            report["stage"] = "positive-controls"
            def create(name, protected=False):
                nonlocal unknown
                network, v4, v6 = (INGRESS, C4, C6) if name == "client" else (
                    SERVICE, S4 if name == "candidate" else P4, S6 if name == "candidate" else P6)
                command = container_command(name, token, network, v4, v6, work, args.deadline, protected=protected)
                unknown = True
                identifier = commands.docker(*command).stdout.decode().strip()
                require(re.fullmatch("[0-9a-f]{64}", identifier))
                unknown = False
                info = commands.inspect("container", identifier)
                check_container(info, token, command, protected=protected)
                require(info["State"]["Status"] == "created")
                commands.docker("container", "start", identifier)
                end = time.monotonic() + 15
                while True:
                    require(time.monotonic() < end)
                    kind, port = ("tls", 8443) if name == "candidate" else ("tcp", 8450)
                    if query(commands, name, "127.0.0.1", port, kind):
                        break
                    time.sleep(.1)
                info = commands.inspect("container", identifier)
                check_container(info, token, command, protected=protected)
                require(info["State"]["Running"] is True)
                return identifier
            create("peer")
            create("client")
            candidate = create("candidate")
            positive = observations(commands, work)
            check_observations(positive, False)
            check_sinks(commands, server)
            report["positive_controls"] = positive
            commands.docker("container", "rm", "--force", candidate)
            report["stage"] = "policy"
            installed = True  # Uncertain nft commit must never be treated as absent.
            commands.nft(net_policy.batch(), report=report)
            report["policy_step"] = "readback"
            net_policy.verify(commands.rules())
            report["policy_step"] = "dns-baseline"
            dns_before = host_query(work, DNS, 8450, "stats")
            require(type(dns_before) is dict and dns_before["dns_udp"] > 0 and dns_before["dns_tcp"] > 0)
            report["policy_step"] = "protected-create"
            create("candidate", protected=True)
            report["stage"] = "negative-controls"
            observed = observations(commands, work)
            check_observations(observed, True)
            check_sinks(commands, server)
            dns_after = host_query(work, DNS, 8450, "stats")
            require(dns_after == dns_before)
            net_policy.verify(commands.rules())
            report.update(observations=observed, dns_relay_unchanged=True, tls_verified=True,
                          policy_sha256=hashlib.sha256(net_policy.batch()).hexdigest(), stage="complete")
        except BaseException:
            pass
        finally:
            commands.cleaning = True
            try:
                report["cleanup_confirmed"] = owned_cleanup(commands, token, net_policy, unknown, installed)
                report["policy_removed"] = installed
            except BaseException:
                pass
            for child in (server, daemon):
                if child is not None:
                    try:
                        snapshot.cleanup_process_group(child)
                    except BaseException:
                        report["cleanup_confirmed"] = False
            if report["stage"] == "complete" and report["cleanup_confirmed"] and not cancelled["cancelled"]:
                report["result"] = "pass"
    print(encoded(report).decode())
    return 0 if report["result"] == "pass" else 1


def prepare_sources(work, commands):
    revision = commands.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"]).stdout.decode().strip()
    require(re.fullmatch("[0-9a-f]{40}", revision))
    hashes = {}
    for name in SOURCES:
        raw = commands.run(["git", "-C", str(ROOT), "show", revision + ":" + name]).stdout
        require(raw == (ROOT / name).read_bytes())
        target = work / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    (work / "fixture").mkdir(mode=0o755)
    (work / "fixture/probe.py").write_bytes((work / SOURCES[2]).read_bytes())
    (work / "fixture").chmod(0o755)
    (work / "fixture/probe.py").chmod(0o444)
    (work / "resolv.conf").write_text("nameserver " + DNS + "\n")
    return revision, hashes


def check_report(report):
    require(type(report) is dict and report.get("schema") == "nib.selected-nat-proof/v1")
    required = {"schema", "result", "stage", "namespace_verified", "cleanup_confirmed", "policy_removed",
                "gpu_used", "deployment_eligible", "confidential", "published"}
    optional = {"engine_version", "image_config", "reference_manifest", "positive_controls", "observations",
                "dns_relay_unchanged", "tls_verified", "policy_sha256", "policy_step"}
    require(required <= set(report) <= required | optional)
    require(report["namespace_verified"] is True)
    for key in ("cleanup_confirmed", "policy_removed", "dns_relay_unchanged", "tls_verified"):
        require(key not in report or type(report[key]) is bool)
    for key, expected in (("engine_version", "29.1.3"), ("image_config", CONFIG), ("reference_manifest", MANIFEST)):
        require(key not in report or report[key] == expected)
    if "policy_sha256" in report:
        require(type(report["policy_sha256"]) is str and re.fullmatch("[0-9a-f]{64}", report["policy_sha256"]))
    if "policy_step" in report:
        require(report["policy_step"] in ("check", "apply", "readback", "dns-baseline", "protected-create"))
    if "positive_controls" in report:
        check_observations(report["positive_controls"], False)
    if "observations" in report:
        check_observations(report["observations"], True)
    require(report.get("result") in ("pass", "fail") and report.get("stage") in (
        "preflight", "image", "positive-controls", "policy", "negative-controls", "complete"))
    for key in ("gpu_used", "deployment_eligible", "confidential", "published"):
        require(report.get(key) is False)
    if report["result"] == "pass":
        for key in ("namespace_verified", "cleanup_confirmed", "policy_removed", "dns_relay_unchanged", "tls_verified"):
            require(report.get(key) is True)
        require(report["stage"] == "complete" and report.get("engine_version") == "29.1.3"
                and report.get("image_config") == CONFIG and report.get("reference_manifest") == MANIFEST)
        check_observations(report.get("positive_controls"), False)
        check_observations(report.get("observations"), True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-sha256")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--inner", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--host-net", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--host-mount", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--deadline", type=float, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.inner is not None:
        return inner(args)
    require(args.run and sys.platform == "linux" and os.getuid() == 0 and platform.machine() == "x86_64"
            and sys.version_info[:3] == (3, 12, 13) and args.output is not None
            and re.fullmatch("[0-9a-f]{64}", args.archive_sha256 or ""))
    require(args.archive.is_absolute() and args.output.is_absolute() and not args.output.exists())
    require(args.output.resolve() == args.output and not (Path("/sys/fs/cgroup") / cgroup_name(args.output)).exists())
    require(Path("/sys/fs/cgroup/cgroup.controllers").is_file())
    require(not Path("/etc/resolv.conf").is_symlink() and Path("/etc/resolv.conf").is_file())
    args.output.mkdir(mode=0o700)
    end = time.monotonic() + OPERATIONS
    child = None
    def notify():
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    with cancellation(notify) as cancelled:
        commands = Commands(end, cancelled, args.output)
        require(commands.run(["unshare", "--version"]).stdout.decode().strip() == "unshare from util-linux 2.39.3")
        copy_archive(args.archive, args.output / "image.tar", args.archive_sha256)
        revision, hashes = prepare_sources(args.output, commands)
        argv = namespace_command(args.output, args.output / "image.tar", os.stat("/proc/self/ns/net").st_ino,
                                 os.stat("/proc/self/ns/mnt").st_ino, end)
        report = {"schema": "nib.selected-nat-proof-host/v1", "git_sha": revision, "source_sha256": hashes,
                  "archive_sha256": args.archive_sha256, "result": "fail", "namespace_reaped": False,
                  "gpu_used": False, "published": False, "deployment_eligible": False,
                  "stage": "namespace",
                  "started_at": datetime.now(timezone.utc).isoformat(), "host_python": platform.python_version(),
                  "unshare_version": "2.39.3"}
        try:
            require(not cancelled["cancelled"])
            child = subprocess.Popen(argv, env=commands.env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            try:
                if cancelled["cancelled"]:
                    notify()
                stdout, _stderr = snapshot.bounded_output(child, argv,
                    max(1, end - time.monotonic() + CLEANUP), None, 65536, 65536)
            finally:
                snapshot.cleanup_process_group(child)
                report["namespace_reaped"] = True
            report["stage"] = "cgroup"
            cleanup_cgroup(args.output)
            report["stage"] = "report"
            observation = decode(stdout)
            check_report(observation)
            report["observation"] = observation
            if child.returncode == 0 and report["observation"].get("result") == "pass" and not cancelled["cancelled"]:
                report["result"] = "pass"
                report["stage"] = "complete"
        except BaseException:
            pass
        report["ended_at"] = datetime.now(timezone.utc).isoformat()
        (args.output / "observation.json").write_bytes(encoded(report) + b"\n")
        print(encoded(report).decode())
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
