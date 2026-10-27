#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare inert, synthetic inputs for a future isolated network proof."""
import sys

if __name__ == "__main__" and not sys.flags.isolated:
    sys.exit("Use python3 -I scripts/prepare_network_proof.py")

import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import types

ROOT = Path(__file__).absolute().parent.parent
SELF = "scripts/prepare_network_proof.py"
STATIC = "scripts/prepare_h100_network.py"
FIXTURE = "guest/nftables/testdata/h100-static-plan.json"


def bootstrap():
    path = ROOT / STATIC
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size < 1 << 20:
            raise ValueError("network-proof-inputs-rejected")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read((1 << 20) + 1)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_nlink,
                              s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if len(raw) != before.st_size or identity(before) != identity(os.fstat(fd)):
            raise ValueError("network-proof-inputs-rejected")
    finally:
        os.close(fd)
    module = types.ModuleType("network_proof_static")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module, raw


static, STATIC_SOURCE = bootstrap()
base, tools, require = static.base, static.tools, static.require
SELF_SOURCE = base.read(ROOT / SELF, 1 << 20)
EXTRA = (*static.EXTRA, SELF, FIXTURE, "scripts/test_network_proof_inputs.py",
         "docs/tasks/p3-t1-network-proof-inputs.md")
LOCK = "image/inputs/guest-runtime-ubuntu2404.json"
SOURCE_ARCHIVE = (14952427, "96e75bd08c57ad401677456fb88ef54a9f05bb1695693013bc6ecce839640fd5")
PRIMARY = {
    "man/systemd.network.xml": static.PRIMARY["systemd.network.xml"],
    "man/systemd.netdev.xml": (120934, "dee5fea0d246514b0e04b2517729dc064ba95bae463c370b369bb91f5a5cd4fb"),
}
PACKAGES = {
    "systemd": ("255.4-1ubuntu8.18", 3474856, "ba37e7dc1e33690d94a1063543d27f9b17c013a20791c3315b2423fbf9e7ae7b"),
    "nftables": ("1.0.9-1ubuntu0.1", 69842, "d648859fccde3c17b8b9b89993427564f91f394a39306db1f67bd5ea3ac42c6e"),
}
EXECUTABLES = {
    "systemd": ("usr/lib/systemd/systemd-networkd", 1669864,
                "e4ab77a36ee958f052b0100b60583716904a393159227e1e895469199e1fd8f9"),
    "nftables": ("usr/sbin/nft", 26856, "3f1c21553e62716ef1abfcf31f51ff94eb73ff4234a6653bac754a42f936d1c7"),
}
SEARCH_DIRECTORIES = tuple(prefix + "/systemd/network" for prefix in
                           ("etc", "run", "usr/lib", "usr/local/lib"))
CASES = (
    "links-down-before-policy", "exclusive-empty-ruleset", "strict-policy-readback",
    "exact-veth-name-and-mac", "exact-address-and-host-route", "allowed-tcp443-reply",
    "deny-other-destination", "deny-other-port", "deny-dns", "deny-dhcp", "deny-ipv6",
    "wrong-name-no-configuration", "wrong-mac-no-configuration", "reject-competing-configuration",
    "reject-generated-configuration", "reject-competing-manager", "no-startup-egress",
    "policy-drift-removes-link", "configuration-drift-removes-link", "owned-cleanup",
)


def committed():
    revision, sources = tools.committed(EXTRA)
    require(sources[SELF] == SELF_SOURCE and sources[STATIC] == STATIC_SOURCE
            and sources[tools.FILES[0]] == static.TOOLS_SOURCE
            and sources[static.EXTRA[1]] == static.EXPORT_SOURCE)
    return revision, sources


def identity(raw):
    return {"bytes": len(raw), "sha256": base.digest(raw)}


def primary_sources(path):
    size, sha = SOURCE_ARCHIVE
    raw = base.read(path, size)
    require(len(raw) == size and base.digest(raw) == sha)
    expanded = base.decompress("source.tar.gz", raw, 96 << 20)
    result = {}
    with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
        for index, member in enumerate(archive):
            require(index < 20000)
            name = member.name.removeprefix("systemd-stable-255.4/")
            if name not in PRIMARY:
                continue
            size, sha = PRIMARY[name]
            require(name not in result and member.isfile() and not member.issparse()
                    and not member.pax_headers and member.size == size)
            with archive.extractfile(member) as stream:
                data = stream.read(size + 1)
            require(len(data) == size and base.digest(data) == sha)
            result[name] = identity(data)
    require(set(result) == set(PRIMARY))
    return {"archive": identity(raw), "files": result}


def packages(archives, sources):
    lock = json.loads(sources[LOCK], object_pairs_hook=static.export.unique)
    result = {}
    for name, (version, size, sha) in PACKAGES.items():
        rows = [row for row in lock["packages"] if row["Package"] == name]
        require(len(rows) == 1 and rows[0]["Version"] == version and rows[0]["Architecture"] == "amd64"
                and (int(rows[0]["Size"]), rows[0]["SHA256"]) == (size, sha))
        raw = base.read(archives / (name + "_" + version + "_amd64.deb"), size)
        require(len(raw) == size and base.digest(raw) == sha)
        tree = tools.payload(raw)
        executable, expected_size, expected_sha = EXECUTABLES[name]
        row = tree.get(executable)
        require(row is not None and row["kind"] == "file" and row["mode"] == 0o755
                and len(row["data"]) == expected_size and base.digest(row["data"]) == expected_sha
                and row["data"][:6] == b"\x7fELF\x02\x01" and row["data"][18:20] == b"\x3e\x00")
        configurations = {}
        for path, entry in tree.items():
            if any(path.startswith(directory + "/") for directory in SEARCH_DIRECTORIES):
                require(entry["kind"] == "file" and entry["mode"] == 0o644)
                configurations[path] = identity(entry["data"])
        result[name] = {"version": version, "archive": identity(raw),
                        "executable": {"path": executable, **identity(row["data"])},
                        "network_configuration_inventory": configurations}
    return result


def prepare(revision, sources, package_inputs, primary):
    fixture = static.export.parsed(sources[FIXTURE])
    manifest = static.manifest(base.encoded(fixture["manifest"]))
    # Reuse the reviewed static renderer and Go-shared synthetic fixture exactly.
    files = static.render(manifest)
    require(files == {
        "candidate/etc/nib/network.json": fixture["network_config"].encode(),
        "candidate/etc/systemd/network/10-nib-nras.network": fixture["networkd"].encode(),
        "candidate/etc/hosts": fixture["hosts"].encode(),
    })
    plan = {
        "schema": "nib.isolated-network-proof-inputs/v1", "scope": "offline-synthetic-inputs-only",
        "guest": {"outer_nic": False, "acceleration": "tcg", "root": "readonly-erofs-and-verity",
                  "policy": "unchanged-nib-network-and-strict-nft-readback"},
        "topology": {"kind": "veth-only-inside-debug-guest", "subject": manifest,
                     "peer_interface": "nibpeer0", "peer_namespace": "separate-inside-debug-guest",
                     "peer_gateway": manifest["gateway"], "peer_destination": manifest["nras_ipv4"],
                     "blocked_destination": "203.0.113.10", "allowed_transport": "tcp443-not-tls-proof"},
        "configuration_search_directories": list(SEARCH_DIRECTORIES),
        "require_absent": ["unlisted-network-files", "all-network-drop-ins", "generated-network-files",
                           "alternate-netdevs", "alternate-link-rules", "competing-network-managers"],
        "required_cases": [{"id": case, "status": "not-run"} for case in CASES],
        "execution_authorized": False, "runtime_loader_closure_verified": False,
        "guest_kernel_veth_verified": False, "network_activated": False, "proof_completed": False,
        "tls_verified": False, "hardware_validated": False, "confidential": False, "deployable": False,
    }
    files["proof-plan.json"] = base.encoded(plan)
    receipt = {
        "schema": "nib.network-proof-preparation/v1", "result": "pass", "scope": plan["scope"],
        "git_sha": revision, "source_hashes": {p: base.digest(b) for p, b in sorted(sources.items())},
        "packages": package_inputs, "primary_sources": primary,
        "files": {p: identity(b) for p, b in sorted(files.items())},
        "package_binaries_executed": False, "image_modified": False, "network_activated": False,
        "proof_completed": False, "hardware_validated": False, "confidential": False, "deployable": False,
    }
    files["receipt.json"] = base.encoded(receipt)
    require(sum(map(len, files.values())) <= 65536)
    return files, receipt


def main(argv=None):
    parser = static.Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--archives", type=Path, required=True)
    parser.add_argument("--systemd-source", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)
    destination = args.output or args.verify
    static.separate_output(destination, args.systemd_source, args.archives)
    revision, sources = committed()
    package_inputs = packages(args.archives, sources)
    primary = primary_sources(args.systemd_source)
    files, report = prepare(revision, sources, package_inputs, primary)
    if args.output:
        base.publish(destination, files)
    base.verify(destination, files)
    print(json.dumps({"result": report["result"], "scope": report["scope"],
                      "receipt_sha256": base.digest(files["receipt.json"]),
                      "proof_completed": False, "network_activated": False}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, EOFError, RecursionError,
            tarfile.TarError, subprocess.SubprocessError, KeyboardInterrupt):
        sys.exit("Network proof preparation rejected; no activation attempted.")
