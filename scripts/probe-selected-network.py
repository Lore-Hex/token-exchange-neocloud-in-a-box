#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bounded local TCP port-publication experiment, never a serving profile."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import signal
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "docker.io/library/python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2"
LABEL = "nib.selected-network-probe"


def require(condition):
    if not condition:
        raise ValueError("network proof rejected")


def publication(info, port):
    requested = info["HostConfig"]["PortBindings"]
    require(requested == {"8443/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(port)}]})
    ports = info["NetworkSettings"]["Ports"]
    require(type(ports) is dict and set(ports) <= {"8443/tcp"})
    actual = ports.get("8443/tcp")
    require(actual is None or actual == requested["8443/tcp"])
    return actual is not None


def probe(port):
    try:
        with socket.create_connection(("127.0.0.1", port), 1) as connection:
            connection.sendall(b"NIB-PROBE\n")
            expected, data, deadline = b"NIB-PROBE-OK\n", bytearray(), time.monotonic() + 1
            while len(data) < len(expected):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                connection.settimeout(remaining)
                block = connection.recv(len(expected) + 1 - len(data))
                if not block:
                    return False
                data.extend(block)
            return data == expected
    except OSError:
        return False


@contextmanager
def cancellation():
    state = {"cancelled": False}
    def cancel(_number, _frame):
        state["cancelled"] = True
    old = {number: signal.signal(number, cancel) for number in (signal.SIGTERM, signal.SIGINT)}
    try:
        yield state
    finally:
        for number, handler in old.items():
            signal.signal(number, handler)


class Docker:
    def __init__(self, endpoint, config, cancelled=None):
        require(endpoint.startswith("unix:///") and "\n" not in endpoint and "\0" not in endpoint)
        self.env = {"PATH": os.defpath + ":/usr/local/bin:/opt/homebrew/bin", "HOME": str(config),
                    "DOCKER_CONFIG": str(config), "DOCKER_HOST": endpoint}
        self.deadline = time.monotonic() + 240
        self.cleaning = False
        self.cancelled = cancelled if cancelled is not None else {"cancelled": False}

    def call(self, *args, check=True):
        left = self.deadline + (60 if self.cleaning else 0) - time.monotonic()
        require(left > 0 and (self.cleaning or not self.cancelled["cancelled"]))
        result = subprocess.run(["docker", *args], env=self.env, capture_output=True,
                                timeout=min(15, left), check=False)
        require(len(result.stdout) <= 131072 and len(result.stderr) <= 131072)
        if check:
            require(result.returncode == 0)
        return result

    def inspect(self, kind, name):
        result = self.call(kind, "inspect", name, check=False)
        if result.returncode:
            # A failed inspect alone is not absence. A successful inventory
            # must also omit the exact name before cleanup can be confirmed.
            names = self.call(kind, "ls", *(('-a',) if kind == 'container' else ()),
                              "--format", "{{.Names}}" if kind == "container" else "{{.Name}}")
            require(name not in names.stdout.decode().splitlines())
            return None
        rows = json.loads(result.stdout)
        require(type(rows) is list and len(rows) == 1 and type(rows[0]) is dict)
        return rows[0]


def case(docker, isolated, cases):
    token = uuid.uuid4().hex
    network, container = "nib-net-" + token, "nib-probe-" + token
    result = {"isolated_internal": isolated, "listener_ready": False,
              "operational_port_mapping": None, "host_reachable": None,
              "cleanup_confirmed": False, "creation_outcome_unknown": False}
    cases.append(result)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    try:
        options = ("--internal", "--opt", "com.docker.network.bridge.gateway_mode_ipv4=isolated") if isolated else ()
        result["creation_outcome_unknown"] = True
        docker.call("network", "create", "--driver", "bridge", "--label", LABEL + "=" + token,
                    "--ipv6=false", *options, network)
        result["creation_outcome_unknown"] = False
        net = docker.inspect("network", network)
        require(net is not None and net["Name"] == network and net["Driver"] == "bridge"
                and net["Internal"] is isolated and net["EnableIPv6"] is False
                and net["Labels"].get(LABEL) == token and not net.get("Containers"))
        result["creation_outcome_unknown"] = True
        docker.call("container", "create", "--name", container, "--label", LABEL + "=" + token,
            "--pull", "never", "--platform", "linux/amd64", "--network", network,
            "--publish", "127.0.0.1:" + str(port) + ":8443/tcp", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--log-driver", "none",
            "--user", "65532:65532", "--cpus", "1", "--memory", "128m", "--memory-swap", "128m",
            "--pids-limit", "32", "--entrypoint", "/usr/local/bin/python3.12",
            "--mount", "type=bind,source=" + str(ROOT / "scripts/fixtures/selected_network_listener.py")
                + ",target=/listener.py,readonly", IMAGE, "-I", "-S", "-B", "/listener.py")
        result["creation_outcome_unknown"] = False
        docker.call("container", "start", container)
        until = time.monotonic() + 20
        while time.monotonic() < until:
            if docker.call("container", "exec", container, "/usr/local/bin/python3.12",
                           "-I", "-S", "-B", "/listener.py", "--probe", check=False).returncode == 0:
                result["listener_ready"] = True
                break
            time.sleep(.1)
        require(result["listener_ready"])
        info = docker.inspect("container", container)
        require(info is not None and info["Name"] == "/" + container and info["State"]["Running"] is True
                and info["Config"]["Image"] == IMAGE and info["Config"]["Labels"].get(LABEL) == token
                and set(info["NetworkSettings"]["Networks"]) == {network})
        result["operational_port_mapping"] = publication(info, port)
        result["host_reachable"] = probe(port)
    finally:
        docker.cleaning = True
        # A timed-out create may still finish in the daemon. Recover a late
        # owned object, but never claim absence proves an unknown create ended.
        if result["creation_outcome_unknown"]:
            for _ in range(3):
                docker.inspect("container", container)
                docker.inspect("network", network)
                time.sleep(.5)
        info = docker.inspect("container", container)
        if info is not None:
            require(info["Name"] == "/" + container and info["Config"]["Labels"].get(LABEL) == token
                    and info["Config"]["Image"] == IMAGE)
            docker.call("container", "rm", "--force", info["Id"])
        require(docker.inspect("container", container) is None)
        net = docker.inspect("network", network)
        if net is not None:
            require(net["Name"] == network and net["Labels"].get(LABEL) == token and not net.get("Containers"))
            docker.call("network", "rm", net["Id"])
        result["cleanup_confirmed"] = (docker.inspect("network", network) is None
                                       and not result["creation_outcome_unknown"])
        docker.cleaning = False
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker-host", required=True)
    parser.add_argument("--engine-version", required=True, choices=("29.1.3", "29.4.0"))
    args = parser.parse_args()
    report = {"schema": "nib.selected-network-probe/v1", "result": "fail", "stage": "preflight",
              "cases": [], "gpu_used": False, "tls_verified": False, "egress_verified": False,
              "deployment_eligible": False, "confidential": False}
    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                  check=True, timeout=5).stdout.decode().strip()
        require(len(revision) == 40 and all(char in "0123456789abcdef" for char in revision))
        source_hashes = {}
        for name in ("scripts/probe-selected-network.py", "scripts/fixtures/selected_network_listener.py"):
            committed = subprocess.run(["git", "show", revision + ":" + name], cwd=ROOT,
                                       capture_output=True, check=True, timeout=5).stdout
            require(committed == (ROOT / name).read_bytes())
            source_hashes[name] = hashlib.sha256(committed).hexdigest()
        report.update(git_sha=revision, source_sha256=source_hashes)
        with cancellation() as cancelled, tempfile.TemporaryDirectory(prefix="nib-network-proof-") as directory:
            docker = Docker(args.docker_host, Path(directory), cancelled)
            version = json.loads(docker.call("version", "--format", "{{json .Server}}").stdout)
            require(version["Version"] == args.engine_version and version["Os"] == "linux")
            report["engine_version"] = version["Version"]
            report["daemon_arch"] = version["Arch"]
            report["image_reference"] = IMAGE
            image = docker.inspect("image", IMAGE)
            require(image is not None and image["Os"] == "linux" and image["Architecture"] == "amd64")
            report["image_id"] = image["Id"]
            for isolated in (False, True):
                report["stage"] = "isolated" if isolated else "positive-control"
                observed = case(docker, isolated, report["cases"])
                if not isolated:
                    require(observed["operational_port_mapping"] and observed["host_reachable"])
            require(not cancelled["cancelled"])
            report.update(result="recorded", stage="complete")
    except Exception:
        pass
    print(json.dumps(report, sort_keys=True))
    return 0 if report["result"] == "recorded" else 1


if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        raise SystemExit("Use python3 -I -S -B scripts/probe-selected-network.py")
    raise SystemExit(main())
