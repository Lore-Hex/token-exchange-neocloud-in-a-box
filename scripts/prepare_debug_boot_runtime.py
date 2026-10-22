#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Reconstruct the fixed CPU debug-boot runtime as inert, private archive data."""
import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B scripts/prepare_debug_boot_runtime.py")

import argparse
import json
import lzma
from pathlib import Path
import re
import runpy
import subprocess
import tarfile
import time
import types

ROOT = Path(__file__).absolute().parent.parent
SCRIPT = "scripts/prepare_debug_boot_runtime.py"
LOCK = "image/inputs/debug-boot-runtime-ubuntu2204.json"
TOOLS = "scripts/prepare_guest_tools.py"
tools = types.SimpleNamespace(**runpy.run_path(str(ROOT / TOOLS)))
base, require = tools.base, tools.require
SNAPSHOT = "https://snapshot.ubuntu.com/ubuntu/20261020T000000Z/"
SIGNER = "F6ECB3762474EDA9D21B7022871920D1991BC93C"
ROOTS = ["qemu-system-x86", "ovmf", "util-linux"]
# Ubuntu's signed 20261020 snapshot, not a host-installed QEMU or firmware.
MEMBERS = {
    "usr/bin/qemu-system-x86_64": (15801984, "55862c768c21fa7d3e72ca1b24797d8ab5b406eb5ef4c9ab9c2280f4e0b8f330"),
    "usr/share/OVMF/OVMF_CODE.fd": (1966080, "3b687eaa323bc4f133df19f479d0b2f3c2f10efab4693c89c05139258e391079"),
    "usr/share/OVMF/OVMF_VARS.fd": (131072, "6ed987af3a3c155be71665f510eae3e007eda9b8b94afd59d45e91c4a11565cc"),
}


def metadata_check(lock, directory):
    """Recheck every selected package row against exact, signed-index bytes.

    This extraction-only closure intentionally has no installed Essential set.
    Do not widen guest_package_inputs.check's installed-system scopes for it.
    """
    require(set(lock) == {"schema", "snapshot", "indexes", "roots", "packages", "archive_bytes",
                          "package_execution", "boot_verified", "confidentiality_verified"}
            and lock["schema"] == "nib.debug-boot-runtime-inputs/v1"
            and lock["snapshot"] == SNAPSHOT and lock["roots"] == ROOTS
            and all(lock[k] is False for k in ("package_execution", "boot_verified", "confidentiality_verified"))
            and type(lock["packages"]) is list and 0 < len(lock["packages"]) <= 128
            and type(lock["archive_bytes"]) is int and 0 < lock["archive_bytes"] <= 64 << 20)
    expected = {(r["index"], r["Package"], r["Version"], r["Architecture"]): r for r in lock["packages"]}
    require(len(expected) == len(lock["packages"]))
    index_ids = {suite + "-" + component for suite in ("jammy", "jammy-updates")
                 for component in ("main", "universe")}
    require(len(lock["indexes"]) == 4 and {r["id"] for r in lock["indexes"]} == index_ids)
    observed = {}
    for index in lock["indexes"]:
        suite, component = index["id"].rsplit("-", 1)
        require(index["file"] == index["id"] + ".xz" and index["encoding"] == "xz"
                and index["url"] == SNAPSHOT + "dists/" + suite + "/" + component + "/binary-amd64/Packages.xz"
                and index["inrelease_url"] == SNAPSHOT + "dists/" + suite + "/InRelease"
                and index["signer_fingerprint"] == SIGNER
                and re.fullmatch("[0-9a-f]{64}", index["inrelease_sha256"]))
        raw = base.read(directory / index["file"], 32 << 20)
        require(len(raw) == index["bytes"] and base.digest(raw) == index["sha256"])
        for count, row in enumerate(base.inputs.paragraphs(base.inputs.index_bytes(raw, "xz"))):
            require(count < 200000)
            key = index["id"], row["Package"], row["Version"], row["Architecture"]
            if key in expected:
                require(key not in observed and row == {k: v for k, v in expected[key].items() if k != "index"})
                observed[key] = row
    require(observed.keys() == expected.keys()
            and base.inputs.check_graph(list(observed.values()), ROOTS, []) == lock["archive_bytes"])


def assemble(lock, archives):
    require(0 < len(lock["packages"]) <= 128 and lock["archive_bytes"] <= 64 << 20)
    tree = {}
    deadline = time.monotonic() + 120
    with base.directory(archives) as fd:
        for row in lock["packages"]:
            require(time.monotonic() < deadline and re.fullmatch("[0-9a-f]{64}", row["SHA256"]))
            raw = base.read_at(fd, row["SHA256"] + ".deb", base.MAX_ARCHIVE)
            require(len(raw) == int(row["Size"]) and base.digest(raw) == row["SHA256"])
            base.controls(raw, row)
            tools.merge(tree, tools.payload(raw))
    tools.complete_tree(tree)
    require(not any(name in tree for name in ("etc/ld.so.preload", "nib")))
    for path, (size, digest) in MEMBERS.items():
        row = tree.get(path)
        require(row is not None and row["kind"] == "file" and len(row["data"]) == size
                and base.digest(row["data"]) == digest)
    executables = {name: tools.executable(tree, name)
                   for name in ("/usr/bin/qemu-system-x86_64", "/usr/bin/unshare", "/usr/bin/setpriv")}
    require(time.monotonic() < deadline)
    return tree, executables


def build(metadata, archives, revision, sources):
    lock = json.loads(sources[LOCK])
    metadata_check(lock, metadata)
    tree, executables = assemble(lock, archives)
    inventory, archive = base.encoded(tools.observation(tree)), tools.tar_bytes(tree)
    report = {"schema": "nib.debug-boot-runtime-preparation/v1", "result": "pass",
              "git_sha": revision, "source_hashes": {name: base.digest(raw) for name, raw in sources.items()},
              "lock_sha256": base.digest(sources[LOCK]), "packages": len(lock["packages"]),
              "archive_bytes": lock["archive_bytes"], "executables": executables,
              "members": {name: {"bytes": size, "sha256": digest} for name, (size, digest) in MEMBERS.items()},
              "runtime_tar": {"bytes": len(archive), "sha256": base.digest(archive)},
              "inventory_sha256": base.digest(inventory), "package_binaries_executed": False,
              "maintainer_scripts_executed": False, "runtime_linkage_validated": False,
              "boot_verified": False, "confidentiality_verified": False}
    files = {"runtime.tar": archive, "inventory.json": inventory, "receipt.json": base.encoded(report)}
    require(sum(map(len, files.values())) <= tools.MAX_TREE)
    return files, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("metadata", "archives", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    tools.separate_output(args.output, (args.metadata, args.archives))
    revision, sources = tools.committed((SCRIPT, LOCK))
    require(sources[SCRIPT] == base.read(ROOT / SCRIPT, 1 << 20))
    files, report = build(args.metadata, args.archives, revision, sources)
    base.publish(args.output, files)
    base.verify(args.output, files)
    print(json.dumps({k: report[k] for k in ("result", "git_sha", "packages", "runtime_linkage_validated", "boot_verified")}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, EOFError, UnicodeError,
            lzma.LZMAError, tarfile.TarError, subprocess.SubprocessError):
        sys.exit("Debug runtime preparation rejected; no runtime execution authorized.")
