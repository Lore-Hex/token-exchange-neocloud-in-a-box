#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare exact mkosi tools payload data; never execute packages or upstream code."""
import sys

if __name__ == "__main__" and not sys.flags.isolated:
    sys.exit("Use python3 -I scripts/prepare_mkosi_tools.py")

import argparse
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import tarfile
import time
import types

ROOT = Path(__file__).absolute().parent.parent
SCRIPT = "scripts/prepare_mkosi_tools.py"
HELPER = "scripts/prepare_mkosi_inputs.py"
POLICY = "image/inputs/mkosi-tools-members.json"
POLICY_SHA = "d1d383d27ed3fec38ddfd9492945caeb5b37ff0d1341be91e2f4492166ced1da"
LOCK = "image/inputs/mkosi-tools-ubuntu2404.json"
HOST_PROGRAMS = ("/usr/bin/python3.12", "/usr/bin/apt-get", "/usr/bin/dpkg",
                 "/usr/bin/bwrap", "/usr/bin/bsdtar", "/usr/bin/systemd-repart",
                 "/usr/bin/awk")


def bootstrap():
    path = ROOT / HELPER
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size < 1 << 20:
            raise ValueError("tools-bootstrap-rejected")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read((1 << 20) + 1)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_nlink,
                              s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if len(raw) != before.st_size or identity(before) != identity(os.fstat(fd)):
            raise ValueError("tools-bootstrap-rejected")
    finally:
        os.close(fd)
    module = types.ModuleType("mkosi_tools_inputs")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module, raw


mkosi, HELPER_SOURCE = bootstrap()
tools, base, require = mkosi.tools, mkosi.base, mkosi.require


def policy(raw, lock_raw):
    require(len(raw) <= 128 << 10 and base.digest(raw) == POLICY_SHA)
    value, lock = json.loads(raw), json.loads(lock_raw)
    require(value["schema"] == "nib.mkosi-tools-members/v1"
            and value["tools_lock_sha256"] == base.digest(lock_raw)
            and set(value["packages"]) == {"ca-certificates", "gawk", "grub-pc-bin"})
    rows = {r["Package"]: r for r in lock["packages"]}
    indexes = {r["id"]: r for r in lock["indexes"]}
    for name, pin in value["packages"].items():
        row = rows[name]
        require(pin["archive_sha256"] == row["SHA256"]
                and pin["archive_bytes"] == int(row["Size"])
                and pin["version"] == row["Version"]
                and pin["source_url"] == indexes[row["index"]]["base_url"] + row["Filename"])
    return value


def member(tree, name, pin):
    # Package ownership is checked before merge; aliasing cannot relocate a pin.
    require(tools.resolve(tree, name) == name)
    row = tree.get(name)
    require(row is not None and row["kind"] == "file" and row["mode"] == pin["mode"]
            and len(row["data"]) == pin["bytes"] and base.digest(row["data"]) == pin["sha256"])
    return row["data"]


def add_awk(tree, pin):
    member(tree, "usr/bin/gawk", pin["members"]["usr/bin/gawk"])
    # These two links alone do not simulate update-alternatives or package setup.
    links = {"usr/bin/awk": "/etc/alternatives/awk", "etc/alternatives/awk": "/usr/bin/gawk"}
    for name in links:
        require(name not in tree and tools.resolve(tree, name, ancestors_only=True) == name)
    tree.update({name: {"kind": "link", "target": target, "mode": 0o777}
                 for name, target in links.items()})
    require(tools.resolve(tree, "usr/bin/awk") == "usr/bin/gawk")
    return links


def add_resolver_mountpoint(tree):
    # mkosi ecf7f570 __init__.py:5017-5029 requires this placeholder even
    # offline. Never import the host's resolver, nameservers or search domains.
    name = "etc/resolv.conf"
    require(name not in tree and tools.resolve(tree, name, ancestors_only=True) == name)
    tree[name] = {"kind": "file", "data": b"", "mode": 0o644}
    return {name: {"bytes": 0, "sha256": base.digest(b""), "mode": 0o644}}


def classify(tree, pins):
    expected = pins["packages"]["grub-pc-bin"]["members"]
    foreign = {}
    for name, pin in expected.items():
        raw = member(tree, name, pin)
        require(len(raw) >= 52 and raw[:7] == b"\x7fELF\x01\x01\x01"
                and struct.unpack_from("<HHI", raw, 16) == (pin["elf_type"], 3, 1)
                and struct.unpack_from("<H", raw, 40)[0] == 52)
        foreign[name] = {"role": "non-host-grub-input", **pin}
    counts = {"elf64-relocatable": 0, "elf64-executable": 0, "elf64-shared": 0}
    for name, row in tree.items():
        raw = row.get("data", b"")
        if not raw.startswith(b"\x7fELF") or name in foreign:
            continue
        require(len(raw) >= 64 and raw[:7] == b"\x7fELF\x02\x01\x01"
                and struct.unpack_from("<HI", raw, 18) == (62, 1)
                and struct.unpack_from("<H", raw, 52)[0] == 64)
        kind = struct.unpack_from("<H", raw, 16)[0]
        require(kind in (1, 2, 3))
        counts[{1: "elf64-relocatable", 2: "elf64-executable", 3: "elf64-shared"}[kind]] += 1
    return {"non_host_grub_inputs": foreign, "other_elf_counts": counts,
            "runtime_linkage_validated": False, "abi_validated": False}


def assemble(inputs, revision, sources):
    lock_raw = sources[LOCK]
    require(base.read(inputs / "mkosi-tools-inputs.json", 2 << 20) == lock_raw)
    lock, pins = json.loads(lock_raw), policy(sources[POLICY], lock_raw)
    mkosi.validate_tools_lock(lock, inputs / "metadata")
    require(0 < len(lock["packages"]) <= 256 and lock["archive_bytes"] <= 128 << 20)
    tree = {p: {"kind": "link", "target": "usr/" + p, "mode": 0o777}
            for p in ("bin", "sbin", "lib", "lib64")}
    deadline = time.monotonic() + 600
    with base.directory(inputs / "repo/pool") as directory:
        for row in lock["packages"]:
            require(time.monotonic() < deadline)
            raw = base.read_at(directory, base.package_name(row), base.MAX_ARCHIVE)
            require(len(raw) == int(row["Size"]) and base.digest(raw) == row["SHA256"])
            _, control = base.controls(raw, row)
            pin = pins["packages"].get(row["Package"])
            exact = ({"archive_sha256": pin["archive_sha256"], "members": pin["members"]}
                     if row["Package"] == "ca-certificates" else None)
            incoming = tools.payload(raw, exact_data=exact)
            if pin:
                for name, expected in pin["members"].items():
                    member(incoming, name, expected)
                if row["Package"] == "gawk":
                    require(control.get("postinst") == pin["postinst"])
            tools.merge(tree, incoming)
    aliases = add_awk(tree, pins["packages"]["gawk"])
    prepared_files = add_resolver_mountpoint(tree)
    classification = classify(tree, pins)
    executables = {name: tools.executable(tree, name) for name in HOST_PROGRAMS}
    archive = tools.tar_bytes(tree)
    inventory = base.encoded(tools.observation(tree))
    require(time.monotonic() < deadline)
    files = {"tools-root.tar": archive, "root-inventory.json": inventory,
             "member-policy.json": sources[POLICY]}
    report = {
        "schema": "nib.mkosi-tools-preparation/v1", "git_sha": revision, "result": "pass",
        "scope": "data-only-exact-tools-tree", "tools_packages": len(lock["packages"]),
        "source_hashes": {p: base.digest(data) for p, data in sorted(sources.items())},
        "tools_lock_sha256": base.digest(lock_raw), "prepared_aliases": aliases,
        "prepared_files": prepared_files,
        "package_configuration_simulated": False, "classification": classification,
        "host_executables": executables, "upstream_executed": False,
        "package_scripts_executed": False, "package_binaries_executed": False,
        "installable_verified": False, "runtime_linkage_validated": False,
        "abi_validated": False, "bootable": False, "deployable": False,
        "files": {n: {"bytes": len(data), "sha256": base.digest(data)} for n, data in files.items()},
    }
    files["receipt.json"] = base.encoded(report)
    require(sum(map(len, files.values())) <= tools.MAX_TREE)
    return files, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.output:
        tools.separate_output(args.output, (args.inputs,))
    revision, sources = tools.committed((*mkosi.EXTRA, SCRIPT, POLICY))
    require(sources[HELPER] == HELPER_SOURCE and sources[tools.FILES[0]] == mkosi.TOOLS_SOURCE)
    files, report = assemble(args.inputs, revision, sources)
    if args.output:
        base.publish(args.output, files)
    base.verify(args.output or args.verify, files)
    print(json.dumps({k: report[k] for k in ("result", "git_sha", "tools_packages",
          "scope", "package_binaries_executed", "abi_validated", "deployable")}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, EOFError, UnicodeError,
            struct.error, tarfile.TarError, subprocess.SubprocessError):
        sys.exit("Mkosi tools preparation rejected; no package or upstream code executed.")
