#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare a private, inert H100 static-network plan; never activate networking."""
import sys

if __name__ == "__main__" and not sys.flags.isolated:
    sys.exit("Use python3 -I scripts/prepare_h100_network.py")

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import types

ROOT = Path(__file__).absolute().parent.parent
SELF = "scripts/prepare_h100_network.py"
EXTRA = (
    SELF, "scripts/mkosi_export.py", "image/inputs/guest-runtime-ubuntu2404.json",
    "guest/nftables/policy.go", "guest/nftables/readback.go", "guest/nftables/nib.nft",
    "guest/network/network.json", "guest/network/networkd.conf",
    "guest/systemd/nib-network.service", "guest/internal/service/network_linux.go",
    "guest/appraisal/collect.go", "guest/appraisal/runtime_linux.go",
    "guest/appraisal/exec_unix.go", "guest/appraisal/process_linux.go",
)
NRAS = "nras.attestation.nvidia.com"
SYSTEMD = "387a14a7b67b8b76adaed4175e14bb7e39b2f738"  # v255.4
SDK = "11b2bfeb42a1158aa08744603ecb239ccb3d2265"
# Public source/package bytes, inspected without executing the SDK or packages.
PRIMARY = {
    "systemd.network.xml": (289068, "4af3e7c576eb7cf34d64d1577417ce7f55f50c5ac596ee588246edada8ff3983"),
    "systemd.resource-control.xml": (96926, "5666a4f337073cc42482775831f44e07a6a0ca31f7f4db5ba98925e943ecde80"),
    "nv_http.cpp": (10771, "4aaed088f49115a4b2db96a11e2035728c134adf702492d1f98b3e6b0cdb68f9"),
    "nv_jwt.cpp": (12891, "f14654c042ad88f478312b4094d5735eed6a876eeed252fed7d4249b8352750e"),
    "nvml_client.cpp": (20765, "672722648a54e6a165a16eb879ec4fbca14e516e43bbb00c2e6460e4b7bcacb2"),
    "systemd.deb": (3474856, "ba37e7dc1e33690d94a1063543d27f9b17c013a20791c3315b2423fbf9e7ae7b"),
}


def bootstrap():
    path = ROOT / "scripts/prepare_guest_tools.py"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size < 1 << 20:
            raise ValueError("network-plan-rejected")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read((1 << 20) + 1)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_nlink,
                              s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if len(raw) != before.st_size or identity(before) != identity(os.fstat(fd)):
            raise ValueError("network-plan-rejected")
    finally:
        os.close(fd)
    module = types.ModuleType("h100_network_tools")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module, raw


tools, TOOLS_SOURCE = bootstrap()
base, require = tools.base, tools.require
export, EXPORT_SOURCE = base.helper("h100_network_json", EXTRA[1])
SELF_SOURCE = base.read(ROOT / SELF, 1 << 20)


def committed():
    revision, sources = tools.committed(EXTRA)
    require(sources[tools.FILES[0]] == TOOLS_SOURCE
            and sources[SELF] == SELF_SOURCE and sources[EXTRA[1]] == EXPORT_SOURCE)
    return revision, sources


def ipv4(value):
    require(type(value) is str)
    ip = ipaddress.IPv4Address(value)
    require(str(ip) == value and not (ip.is_unspecified or ip.is_loopback
            or ip.is_link_local or ip.is_multicast or ip.is_reserved)
            and int(ip) >> 24 != 0)
    return ip


def manifest(raw):
    require(len(raw) <= 4096)
    value = export.parsed(raw)
    require(type(value) is dict and set(value) == {
        "schema", "interface", "mac", "address", "gateway", "gateway_on_link", "nras_ipv4"}
        and value["schema"] == "nib.h100-static-ipv4/v1")
    name, mac, address = value["interface"], value["mac"], value["address"]
    require(type(name) is str and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,14}", name)
            and name != "lo" and type(mac) is str
            and re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", mac)
            and mac != "00:00:00:00:00:00" and not int(mac[:2], 16) & 1
            and type(address) is str and address.count("/") == 1)
    interface = ipaddress.IPv4Interface(address)
    require(str(interface) == address and 1 <= interface.network.prefixlen <= 32)
    local, gateway, remote = ipv4(str(interface.ip)), ipv4(value["gateway"]), ipv4(value["nras_ipv4"])
    require(len({local, gateway, remote}) == 3 and remote not in interface.network
            and type(value["gateway_on_link"]) is bool)
    if interface.network.prefixlen < 31:
        require(local not in (interface.network.network_address, interface.network.broadcast_address))
        require(gateway not in (interface.network.network_address, interface.network.broadcast_address))
    # An off-subnet gateway is an explicit operator assertion, never inferred as
    # reachable. Reject redundant/inconsistent flags as well as guessed defaults.
    require(value["gateway_on_link"] == (gateway not in interface.network))
    return value


def render(value):
    # No default route, DHCP, DNS, IPv6 discovery, user hostname or policy renderer.
    # The existing guest Config/Render/strict readback consumes network.json.
    network = ("[Match]\nName={interface}\nMACAddress={mac}\n\n"
               "[Network]\nAddress={address}\nDHCP=no\nLinkLocalAddressing=no\n"
               "IPv6AcceptRA=no\nLLMNR=no\nMulticastDNS=no\nDNSDefaultRoute=no\n"
               "KeepConfiguration=no\n\n[Route]\nDestination={nras_ipv4}/32\n"
               "Gateway={gateway}\nGatewayOnLink={onlink}\n").format(
                   **value, onlink="yes" if value["gateway_on_link"] else "no")
    return {
        "candidate/etc/nib/network.json": base.encoded({"schema": "nib.guest-network/v1",
                                                       "egress": [value["nras_ipv4"]]}),
        "candidate/etc/systemd/network/10-nib-nras.network": network.encode("ascii"),
        "candidate/etc/hosts": (value["nras_ipv4"] + " " + NRAS + "\n").encode("ascii"),
    }


def primary_sources(path, sources):
    lock = json.loads(sources[EXTRA[2]], object_pairs_hook=export.unique)
    rows = [r for r in lock["packages"] if r["Package"] == "systemd"]
    require(len(rows) == 1 and rows[0]["Version"] == "255.4-1ubuntu8.18"
            and rows[0]["Architecture"] == "amd64"
            and (int(rows[0]["Size"]), rows[0]["SHA256"]) == PRIMARY["systemd.deb"])
    result = {}
    for name, (size, sha) in PRIMARY.items():
        raw = base.read(path / name, size)
        require(len(raw) == size and base.digest(raw) == sha)
        result[name] = {"bytes": size, "sha256": sha}
    return result


def private_manifest(path):
    with base.directory(path.parent) as parent:
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        require(before.st_uid == os.getuid() and stat.S_IMODE(before.st_mode) == 0o600)
        raw = base.read_at(parent, path.name, 4096)
        after = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        require(base.identity(before) == base.identity(after) and before.st_uid == after.st_uid)
        return raw


def prepare(raw, revision, sources, primary):
    value = manifest(raw)
    files = {"operator-manifest.json": raw, **render(value)}
    report = {
        "schema": "nib.h100-network-plan/v1", "result": "pass", "scope": "offline-private-plan-only",
        "git_sha": revision, "source_hashes": {p: base.digest(b) for p, b in sorted(sources.items())},
        "manifest_sha256": base.digest(raw), "primary_inputs": primary,
        "systemd_source": SYSTEMD, "sdk_source": SDK, "nras_hostname": NRAS,
        "network_activated": False, "tls_verified": False, "device_access_verified": False,
        "memory_limit_verified": False, "image_built": False, "boot_validated": False,
        "hardware_validated": False, "confidential": False, "deployable": False,
        "files": {p: {"bytes": len(b), "sha256": base.digest(b)} for p, b in sorted(files.items())},
    }
    files["receipt.json"] = base.encoded(report)
    require(sum(map(len, files.values())) <= 65536)
    return files, report


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("network-plan-arguments-rejected")


def separate_output(destination, manifest_path, primary_path):
    tools.separate_output(destination, (ROOT, manifest_path, primary_path))

    def lineage(path):
        require(len(path.parts) <= 64)
        result = []
        for ancestor in (path, *path.parents):
            with base.directory(ancestor) as fd:
                result.append(os.fstat(fd))
        return result

    # Textual paths alone miss case-insensitive aliases. Compare nofollow-opened
    # directory identities in both directions before reading or publishing.
    protected = [lineage(ROOT), lineage(primary_path), lineage(manifest_path.parent)]
    parents = lineage(destination.parent)
    try:
        with base.directory(destination) as fd:
            existing = os.fstat(fd)
    except FileNotFoundError:
        existing = None
    ancestors = parents if existing is None else [existing, *parents]
    require(not any(os.path.samestat(group[0], ancestor)
                    for group in protected[:2] for ancestor in ancestors))
    if existing is not None:
        require(not any(os.path.samestat(existing, ancestor)
                        for group in protected for ancestor in group))


def main(argv=None):
    parser = Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--primary-sources", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)
    destination = args.output or args.verify
    separate_output(destination, args.manifest, args.primary_sources)
    revision, sources = committed()
    primary = primary_sources(args.primary_sources, sources)
    files, report = prepare(private_manifest(args.manifest), revision, sources, primary)
    if args.output:
        base.publish(destination, files)
    base.verify(destination, files)
    # Paths, addresses, manifest contents and exception strings remain private.
    print(json.dumps({"result": report["result"], "scope": report["scope"],
                      "receipt_sha256": base.digest(files["receipt.json"]),
                      "network_activated": False, "deployable": False}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError,
            subprocess.SubprocessError, KeyboardInterrupt):
        sys.exit("H100 network plan rejected; no activation attempted.")
