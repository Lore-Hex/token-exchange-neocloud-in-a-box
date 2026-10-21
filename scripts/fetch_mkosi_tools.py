#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fetch only the committed, hash-bound public Ubuntu tools inputs; never install."""
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


def public_url(value):
    require(type(value) is str)
    url = urlsplit(value)
    require(url.scheme == "https" and url.netloc in HOSTS and not url.query and not url.fragment
            and re.fullmatch(r"/ubuntu/[A-Za-z0-9_+.:~/-]+", url.path)
            and all(part not in (".", "..", "") for part in url.path.split("/")[1:]))
    return value


def plan(lock):
    require(type(lock) is dict and lock["scope"] == "mkosi-build-tools-dependency-coverage"
            and 0 < len(lock["indexes"]) <= 6 and 0 < len(lock["packages"]) <= 256)
    items, indexes = {}, {}
    for row in lock["indexes"]:
        require(row["id"] not in indexes and row["encoding"] == "xz"
                and re.fullmatch(r"noble(?:-(?:updates|security))?-(?:main|universe)\.xz", row["file"]))
        indexes[row["id"]] = row
        release = urlsplit(public_url(row["inrelease_url"]))
        path = PurePosixPath(release.path)
        index = PurePosixPath(row["index_path"])
        require(path.name == "InRelease" and index.as_posix() in
                ("main/binary-amd64/Packages.xz", "universe/binary-amd64/Packages.xz"))
        url = urlunsplit((release.scheme, release.netloc,
            str(path.parent / index.parent / "by-hash/SHA256" / row["sha256"]), "", ""))
        name = "metadata/" + row["file"]
        require(name not in items)
        items[name] = (public_url(url), row["bytes"], row["sha256"])
    for row in lock["packages"]:
        index = indexes[row["index"]]
        require(type(row["Filename"]) is str and row["Filename"].startswith("pool/")
                and type(row["Package"]) is str and re.fullmatch(r"[a-z0-9][a-z0-9+.-]{0,127}", row["Package"])
                and type(row["Size"]) is str and re.fullmatch(r"[0-9]{1,9}", row["Size"]))
        name = "repo/pool/" + base.package_name(row)
        require(name not in items)
        items[name] = (public_url(index["base_url"] + row["Filename"]), int(row["Size"]), row["SHA256"])
    require(sum(size for _, size, _ in items.values()) <= MAX_BYTES
            and sum(int(row["Size"]) for row in lock["packages"]) == lock["archive_bytes"])
    for _, size, sha in items.values():
        require(type(size) is int and 0 < size <= 32 << 20
                and type(sha) is str and re.fullmatch("[0-9a-f]{64}", sha))
    return items


def fetch(lock_raw, execute, *, clock=time.monotonic):
    items = plan(json.loads(lock_raw))
    files = {"mkosi-tools-inputs.json": lock_raw}
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
    args = parser.parse_args()
    require(not args.output.exists() and not args.output.is_symlink())
    _, sources = committed()
    files = fetch(sources[prep.LOCK], base.processes.execute_process_group)
    base.publish(args.output.absolute(), files)
    print("Pinned public tools inputs fetched; no package execution.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        sys.exit("Pinned tools fetch rejected; no installation or execution.")
