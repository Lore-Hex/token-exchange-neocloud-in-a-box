#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fetch committed, hash-bound public build inputs; never install or execute."""
import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site):
    sys.exit("Use python3 -I -S -B scripts/fetch_mkosi_tools.py")

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import runpy
import subprocess
import time
import types
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).absolute().parent.parent
SCRIPT = "scripts/fetch_mkosi_tools.py"
PREPARER = "scripts/prepare_mkosi_tools.py"
prep = types.SimpleNamespace(**runpy.run_path(str(ROOT / PREPARER), run_name="tools_preparer"))
base, tools, require = prep.base, prep.tools, prep.require
ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C"}
HOSTS = {"archive.ubuntu.com", "security.ubuntu.com"}
MAX_BYTES = 160 << 20
NVIDIA_BASE = "https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/"
GUEST_SCOPE = "kernel-driver-build-dependency-coverage"


def public_url(value, *, nvidia=False):
    require(type(value) is str)
    url = urlsplit(value)
    if nvidia:
        require(value.startswith(NVIDIA_BASE) and not url.query and not url.fragment
                and value[len(NVIDIA_BASE):] not in (".", "..")
                and re.fullmatch(r"[A-Za-z0-9_+.~-]+", value[len(NVIDIA_BASE):]))
        return value
    require(url.scheme == "https" and url.netloc in HOSTS and not url.query and not url.fragment
            and re.fullmatch(r"/ubuntu/[A-Za-z0-9_+.:~/-]+", url.path)
            and all(part not in (".", "..", "") for part in url.path.split("/")[1:]))
    return value


def plan(lock, *, guest=False):
    require(type(guest) is bool and type(lock) is dict
            and lock["scope"] == (GUEST_SCOPE if guest else "mkosi-build-tools-dependency-coverage")
            and 0 < len(lock["indexes"]) <= (7 if guest else 6) and 0 < len(lock["packages"]) <= 256)
    items, indexes = {}, {}
    for row in lock["indexes"]:
        require(row["id"] not in indexes)
        indexes[row["id"]] = row
        if row["id"] == "nvidia":
            require(guest and row["encoding"] == "plain" and row["file"] == "nvidia.packages"
                    and row["index_path"] == "Packages" and row["base_url"] == NVIDIA_BASE
                    and row["inrelease_url"] == NVIDIA_BASE + "InRelease")
            url = public_url(NVIDIA_BASE + "Packages", nvidia=True)
        else:
            require(row["encoding"] == "xz"
                    and re.fullmatch(r"noble(?:-(?:updates|security))?-(?:main|universe)\.xz", row["file"]))
            release = urlsplit(public_url(row["inrelease_url"]))
            path = PurePosixPath(release.path)
            index = PurePosixPath(row["index_path"])
            require(path.name == "InRelease" and index.as_posix() in
                    ("main/binary-amd64/Packages.xz", "universe/binary-amd64/Packages.xz"))
            url = public_url(urlunsplit((release.scheme, release.netloc,
                str(path.parent / index.parent / "by-hash/SHA256" / row["sha256"]), "", "")))
        name = "metadata/" + row["file"]
        require(name not in items and type(row["bytes"]) is int and 0 < row["bytes"] <= 32 << 20)
        items[name] = (url, row["bytes"], row["sha256"])
    for row in lock["packages"]:
        index = indexes[row["index"]]
        require(type(row["Filename"]) is str and type(row["Package"]) is str
                and re.fullmatch(r"[a-z0-9][a-z0-9+.-]{0,127}", row["Package"])
                and type(row["Size"]) is str and re.fullmatch(r"[0-9]{1,9}", row["Size"]))
        if row["index"] == "nvidia":
            require(guest and re.fullmatch(r"\./[A-Za-z0-9_+.~-]+\.deb", row["Filename"]))
            url = public_url(NVIDIA_BASE + row["Filename"][2:], nvidia=True)
        else:
            require(row["Filename"].startswith("pool/"))
            url = public_url(index["base_url"] + row["Filename"])
        name = "repo/pool/" + base.package_name(row)
        require(name not in items)
        items[name] = (url, int(row["Size"]), row["SHA256"])
    require(sum(size for _, size, _ in items.values()) <= (base.MAX_TOTAL if guest else MAX_BYTES)
            and sum(int(row["Size"]) for row in lock["packages"]) == lock["archive_bytes"])
    for _, size, sha in items.values():
        require(type(size) is int and 0 < size <= (base.MAX_ARCHIVE if guest else 32 << 20)
                and type(sha) is str and re.fullmatch("[0-9a-f]{64}", sha))
    return items


def build_plan(lock, guest):
    items = plan(lock)
    rows = {row["Package"]: row for row in lock["packages"]}
    require(len(rows) == len(lock["packages"]))
    for row in guest["packages"]:
        require(rows.setdefault(row["Package"], row) == row)
    for name, item in plan(guest, guest=True).items():
        require(items.setdefault(name, item) == item)
    require(len(rows) <= prep.mkosi.MAX_PACKAGES
            and sum(size for _, size, _ in items.values()) <= prep.mkosi.MAX_BYTES)
    result = {}
    for name, item in items.items():
        if name.startswith("repo/pool/"):
            name = "archives/" + item[2] + ".deb"
        require(name not in result)
        result[name] = item
    return result


def fetch(lock_raw, execute, *, clock=time.monotonic, guest_raw=None):
    lock = json.loads(lock_raw)
    items = plan(lock) if guest_raw is None else build_plan(lock, json.loads(guest_raw))
    files = {"mkosi-tools-inputs.json": lock_raw}
    if guest_raw is not None:
        files["guest-inputs.json"] = guest_raw
    deadline = clock() + 600
    for name, (url, size, sha) in sorted(items.items()):
        seconds = min(60, deadline - clock())
        require(seconds > 0)
        # No curlrc, proxy, redirect, credentials, retry or unbounded diagnostics.
        result = execute(["/usr/bin/curl", "--disable", "--noproxy", "*", "--fail", "--silent",
            "--show-error", "--proto", "=https", "--tlsv1.2", "--connect-timeout", "10",
            "--max-time", str(seconds), "--max-filesize", str(size), url],
            env=dict(ENV), timeout=seconds, max_output_bytes=size + 65536,
            stdin=subprocess.DEVNULL)
        result.check_returncode()
        require(clock() < deadline and type(result.stdout) is bytes and result.stderr == b""
                and len(result.stdout) == size and base.digest(result.stdout) == sha)
        files[name] = result.stdout
    return files


def committed(extra=()):
    revision, sources = tools.committed((*prep.mkosi.EXTRA, PREPARER, prep.POLICY, SCRIPT, *extra))
    require(sources[prep.HELPER] == prep.HELPER_SOURCE
            and sources[tools.FILES[0]] == prep.mkosi.TOOLS_SOURCE)
    return revision, sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--include-guest", action="store_true",
                        help="fetch the fixed guest/tools union into archives/ for prepare_mkosi_inputs")
    args = parser.parse_args()
    require(not args.output.exists() and not args.output.is_symlink())
    _, sources = committed()
    files = fetch(sources[prep.LOCK], base.processes.execute_process_group,
                  guest_raw=sources[tools.FILES[-1]] if args.include_guest else None)
    base.publish(args.output.absolute(), files)
    print("Pinned public build inputs fetched; no package execution.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        sys.exit("Pinned tools fetch rejected; no installation or execution.")
