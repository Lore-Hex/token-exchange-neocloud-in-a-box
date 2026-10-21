#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Recheck a fixed source review of guest maintainer hooks; never execute hooks."""
import argparse
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tarfile
import time
import types

ROOT = Path(__file__).absolute().parent.parent
SCRIPT = "scripts/review_guest_hooks.py"
POLICY = "image/inputs/r595-ubuntu2404-hook-review.json"
LOCK = "image/inputs/r595-ubuntu2404.json"
tools = types.SimpleNamespace(**runpy.run_path(str(ROOT / "scripts/prepare_guest_tools.py")))
base, require = tools.base, tools.require
CONTROL_NAMES = frozenset(("preinst", "postinst", "prerm", "postrm", "config", "triggers"))
DATA_PREFIXES = ("etc/kernel/", "usr/lib/kernel/install.d/", "usr/lib/dkms/",
                 "usr/share/initramfs-tools/hooks/", "etc/initramfs/")
MAX_EXPANDED = 1 << 30


def data_hooks(raw):
    _, (name, packed) = base.archive_members(raw)
    expanded = base.decompress(name, packed, MAX_EXPANDED)
    result = {}
    with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
        for count, member in enumerate(archive):
            require(count < 100000 and not member.pax_headers and not member.issparse())
            name = member.name.removeprefix("./").rstrip("/")
            if name in ("", ".") and member.isdir():
                continue
            require(0 < len(name) <= 2048 and not name.startswith("/")
                    and all(part not in ("", ".", "..") for part in name.split("/")))
            if not name.startswith(DATA_PREFIXES) or member.isdir():
                continue
            require(tools.path_name(name) == name and name not in result
                    and member.isfile() and 0 < member.size <= 256 << 10
                    and 0 <= member.mode <= 0o777)
            with archive.extractfile(member) as stream:
                raw = stream.read((256 << 10) + 1)
            require(len(raw) == member.size)
            result[name] = {"bytes": len(raw), "sha256": base.digest(raw), "mode": member.mode}
    return result


def inventory(lock, archives):
    rows = lock["packages"]
    require(type(rows) is list and 0 < len(rows) <= 256
            and len({r["Package"] for r in rows}) == len(rows))
    result = []
    deadline = time.monotonic() + 600
    total = 0
    with base.directory(archives) as parent:
        for row in sorted(rows, key=lambda r: r["Package"]):
            require(time.monotonic() < deadline)
            raw = base.read_at(parent, row["SHA256"] + ".deb", base.MAX_ARCHIVE)
            total += len(raw)
            require(total <= base.MAX_TOTAL and len(raw) == int(row["Size"])
                    and base.digest(raw) == row["SHA256"])
            _, members = base.controls(raw, row)
            result.append({"package": row["Package"], "version": row["Version"],
                           "archive_sha256": row["SHA256"],
                           "control_hooks": {k: v for k, v in members.items() if k in CONTROL_NAMES},
                           "data_hooks": data_hooks(raw)})
    require(total == lock["archive_bytes"] and time.monotonic() < deadline)
    return result


def validate(policy, lock_raw, rows):
    require(type(policy) is dict and policy.get("schema") == "nib.guest-hook-review-policy/v1"
            and policy.get("scope") == "fresh-directory-installation-source-review"
            and policy.get("guest_lock_sha256") == base.digest(lock_raw)
            and policy.get("inventory_sha256") == base.digest(base.encoded(rows))
            and policy.get("package_count") == len(rows)
            and policy.get("control_members") == sum(len(r["control_hooks"]) for r in rows)
            and policy.get("data_hooks") == sum(len(r["data_hooks"]) for r in rows)
            and policy.get("installation_authorized") is False)
    require(all(type(policy.get(k)) is int for k in ("package_count", "control_members", "data_hooks")))
    return {"schema": "nib.guest-hook-review/v1", "result": "pass",
            "scope": policy["scope"], "policy_sha256": base.digest(base.encoded(policy)),
            "guest_lock_sha256": base.digest(lock_raw),
            "inventory_sha256": base.digest(base.encoded(rows)),
            "package_count": len(rows), "control_members": policy["control_members"],
            "data_hooks": policy["data_hooks"], "source_review_matches": True,
            "installation_authorized": False, "packages_executed": False,
            "image_built": False, "hardware_validated": False, "deployable": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archives", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    tools.separate_output(args.output, (args.archives, ROOT))
    revision, sources = tools.committed((SCRIPT, POLICY))
    lock_raw, policy = sources[LOCK], json.loads(sources[POLICY])
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rows = inventory(json.loads(lock_raw), args.archives)
    report = validate(policy, lock_raw, rows)
    report["git_sha"] = revision
    ended = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    run = {"schema": "nib.evidence/v1", "phase": "P3", "task": "T1", "git_sha": revision,
           "command": "python -I -B scripts/review_guest_hooks.py --archives PRIVATE --output PRIVATE",
           "started_utc": started, "ended_utc": ended, "result": "pass", "execution": "software",
           "hardware_validated": False,
           "host": {"provider": "local", "machine": "offline-package-review", "gpus": "none",
                    "driver": None, "cuda": None},
           "notes": "Read-only hash recheck of reviewed public package hooks. No extraction or hook execution."}
    base.publish(args.output, {"run.json": base.encoded(run), "result.json": base.encoded(report),
                               "inventory.json": base.encoded(rows)})
    print("Guest hook source review matches; installation remains unauthorized.")


if __name__ == "__main__":
    try:
        require(sys.flags.isolated and sys.flags.dont_write_bytecode)
        main()
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, subprocess.SubprocessError):
        sys.exit("Guest hook source review rejected; no installation performed.")
