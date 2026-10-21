#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare an offline startup-probe image as data, without loading or executing it."""
import sys

if __name__ == "__main__" and not sys.flags.isolated:
    sys.exit("Use python3 -I scripts/prepare_mkosi_probe.py")

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import types

ROOT = Path(__file__).absolute().parent.parent
HELPER = "scripts/prepare_mkosi_tools.py"
SCRIPT = "scripts/prepare_mkosi_probe.py"
WORKER = "scripts/mkosi_tools_probe.py"
RUNNER = "host/probe-mkosi-tools.py"
CHECKER = "scripts/mkosi_namespace_check.py"


def bootstrap():
    path = ROOT / HELPER
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size < 1 << 20:
            raise ValueError("probe-bootstrap-rejected")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read((1 << 20) + 1)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_nlink,
                              s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if len(raw) != before.st_size or identity(before) != identity(os.fstat(fd)):
            raise ValueError("probe-bootstrap-rejected")
    finally:
        os.close(fd)
    module = types.ModuleType("mkosi_probe_tools")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module, raw


prep, HELPER_SOURCE = bootstrap()
tools, base, require = prep.tools, prep.base, prep.require


def committed():
    return tools.committed((*prep.mkosi.EXTRA, HELPER, prep.POLICY, SCRIPT, WORKER, RUNNER, CHECKER))


def build(inputs, revision, sources, *, sandbox=False):
    require(type(sandbox) is bool)
    files, receipt = prep.assemble(inputs, revision, sources)
    inventory = json.loads(files["root-inventory.json"])
    require(not any(name == "nib" or name.startswith("nib/") for name in inventory))
    config = {"schema": "nib.mkosi-tools-probe-input/v1", "git_sha": revision,
              "tools_receipt_sha256": base.digest(files["receipt.json"]),
              "snapshot_sha256": base.digest(sources[tools.FILES[4]])}
    payload = {"nib/probe.py": sources[WORKER], "nib/snapshot.py": sources[tools.FILES[4]]}
    if sandbox:
        # Authenticate the entire upstream tree, not just an adjacent checksum.
        pin = json.loads(sources[prep.mkosi.EXTRA[1]])
        _, upstream = prep.mkosi.verify_source(
            base.read(inputs / "mkosi-source.tar.gz", prep.mkosi.MAX_SOURCE), pin)
        payload.update({"nib/mkosi-sandbox.py": upstream["mkosi/sandbox.py"],
                        "nib/namespace-check.py": sources[CHECKER]})
        payload.update({"nib/" + name: upstream[name] for name in pin["license_files"]})
        config.update(schema="nib.mkosi-tools-probe-input/v2", sandbox=True,
                      sandbox_sha256=base.digest(payload["nib/mkosi-sandbox.py"]),
                      checker_sha256=base.digest(sources[CHECKER]))
    payload["nib/input.json"] = base.encoded(config)
    tree = {name: {"kind": "file", "mode": 0o444, "data": raw} for name, raw in payload.items()}
    if sandbox:
        require("tmp" not in inventory or inventory["tmp"]["kind"] == "dir")
        tree["tmp"] = {"kind": "dir", "mode": 0o1777}
    overlay = tools.tar_bytes(tree)
    image, config_hash, tag, layers = tools.docker_archive(
        (files["tools-root.tar"], overlay), revision,
        entrypoint=["/usr/bin/python3.12", "-I", "-S", "-B", "/nib/probe.py"],
        purpose="offline-tools-startup-probe", tag_prefix="nib-tools-startup-probe")
    output = {"image.tar": image, "tools-receipt.json": files["receipt.json"],
              "root-inventory.json": files["root-inventory.json"]}
    report = {
        "schema": "nib.mkosi-probe-preparation/v1", "git_sha": revision, "result": "pass",
        "scope": "startup-probe-image-data-only", "source_hashes": receipt["source_hashes"],
        "tools_receipt_sha256": config["tools_receipt_sha256"],
        "tools_packages": receipt["tools_packages"], "image_tag": tag,
        "config_sha256": config_hash, "layer_sha256s": layers,
        "files": {name: {"bytes": len(raw), "sha256": base.digest(raw)}
                  for name, raw in output.items()},
        "container_executed": False, "package_binaries_executed": False,
        "package_installation_executed": False, "maintainer_scripts_executed": False,
        "mkosi_executed": False, "namespaces_validated": False,
        "abi_validated": False, "bootable": False, "deployable": False,
    }
    if sandbox:
        report.update(schema="nib.mkosi-probe-preparation/v2", sandbox=True,
                      scope="sandbox-probe-image-data-only",
                      sandbox_sha256=config["sandbox_sha256"], checker_sha256=config["checker_sha256"])
    output["receipt.json"] = base.encoded(report)
    require(sum(map(len, output.values())) <= tools.MAX_TREE)
    return output, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True, type=Path)
    parser.add_argument("--sandbox", action="store_true", help="Also test the pinned namespace sandbox")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.output:
        tools.separate_output(args.output, (args.inputs,))
    revision, sources = committed()
    require(sources[HELPER] == HELPER_SOURCE and sources[prep.HELPER] == prep.HELPER_SOURCE
            and sources[tools.FILES[0]] == prep.mkosi.TOOLS_SOURCE)
    files, report = build(args.inputs, revision, sources, sandbox=args.sandbox)
    if args.output:
        base.publish(args.output, files)
    base.verify(args.output or args.verify, files)
    print(json.dumps({key: report[key] for key in
          ("result", "git_sha", "scope", "container_executed", "deployable")}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, EOFError, UnicodeError,
            tarfile.TarError, subprocess.SubprocessError):
        sys.exit("Probe preparation rejected; no container or package code executed.")
