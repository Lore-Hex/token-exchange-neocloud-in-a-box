#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fetch committed, hash-bound public build inputs; never install or execute."""
import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site):
    sys.exit("Use python3 -I -S -B scripts/fetch_mkosi_tools.py")

import argparse
import concurrent.futures
import gzip
import io
import json
from pathlib import Path, PurePosixPath
import re
import runpy
import subprocess
import tarfile
import threading
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
# Canonical's dated archive preserves packages removed from the live pool.
# https://snapshot.ubuntu.com/ (Snapshot ID format / reproducible deployments).
# Package size/SHA-256 still come from the existing signed-index locks.
UBUNTU_SNAPSHOT = "https://snapshot.ubuntu.com/ubuntu/20261020T000000Z/"
MAX_BYTES = 160 << 20
NVIDIA_BASE = "https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/"
GUEST_SCOPE = "kernel-driver-build-dependency-coverage"
MAX_METADATA = 64 << 20
# curl's documented transport errors only; HTTP, TLS verification, byte limits
# and content-integrity failures never become retryable.
# https://curl.se/libcurl/c/libcurl-errors.html
TRANSIENT_CODES = frozenset((6, 7, 28, 52, 55, 56))
RETRY_LOG_LOCK = threading.Lock()


class FetchFailure(ValueError):
    """Only public lock identities and fixed failure categories leave the fetcher."""

    def __init__(self, name, size, sha, category, returncode=None):
        super().__init__("pinned input fetch rejected")
        self.report = {"schema": "nib.input-fetch-failure/v1", "result": "fail",
                       "input_kind": "metadata" if name.startswith("metadata/") else "archive",
                       "expected_sha256": sha, "expected_bytes": size,
                       "failure": category, "returncode": returncode,
                       "package_execution": False}


def public_url(value, *, nvidia=False, snapshot=False):
    require(type(value) is str)
    require(not (nvidia and snapshot))
    url = urlsplit(value)
    if snapshot:
        require(value.startswith(UBUNTU_SNAPSHOT + "pool/")
                and not url.query and not url.fragment
                and re.fullmatch(r"[A-Za-z0-9_+.~/-]+\.deb", value[len(UBUNTU_SNAPSHOT):])
                and all(part not in (".", "..", "")
                        for part in value[len(UBUNTU_SNAPSHOT):].split("/")))
        return value
    if nvidia:
        require(value.startswith(NVIDIA_BASE) and not url.query and not url.fragment
                and value[len(NVIDIA_BASE):] not in (".", "..")
                and re.fullmatch(r"[A-Za-z0-9_+.~-]+", value[len(NVIDIA_BASE):]))
        return value
    require(url.scheme == "https" and url.netloc in HOSTS and not url.query and not url.fragment
            and re.fullmatch(r"/ubuntu/[A-Za-z0-9_+.:~/-]+", url.path)
            and all(part not in (".", "..", "") for part in url.path.split("/")[1:]))
    return value


def plan(lock, *, guest=False, guest_scope=GUEST_SCOPE):
    require(guest_scope in (GUEST_SCOPE, tools.TARGETS["guest-runtime"][1])
            and (guest or guest_scope == GUEST_SCOPE))
    require(type(guest) is bool and type(lock) is dict
            and lock["scope"] == (guest_scope if guest else "mkosi-build-tools-dependency-coverage")
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
            require(index["base_url"] in {"https://" + host + "/ubuntu/" for host in HOSTS})
            public_url(index["base_url"] + row["Filename"])
            url = public_url(UBUNTU_SNAPSHOT + row["Filename"], snapshot=True)
        name = "repo/pool/" + base.package_name(row)
        require(name not in items)
        items[name] = (url, int(row["Size"]), row["SHA256"])
    require(sum(size for _, size, _ in items.values()) <= (base.MAX_TOTAL if guest else MAX_BYTES)
            and sum(int(row["Size"]) for row in lock["packages"]) == lock["archive_bytes"])
    for _, size, sha in items.values():
        require(type(size) is int and 0 < size <= (base.MAX_ARCHIVE if guest else 32 << 20)
                and type(sha) is str and re.fullmatch("[0-9a-f]{64}", sha))
    return items


def build_plan(lock, guest, *, guest_scope=GUEST_SCOPE):
    items = plan(lock)
    rows = {row["Package"]: row for row in lock["packages"]}
    require(len(rows) == len(lock["packages"]))
    for row in guest["packages"]:
        require(rows.setdefault(row["Package"], row) == row)
    for name, item in plan(guest, guest=True, guest_scope=guest_scope).items():
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


def validate_metadata(metadata, items):
    expected = {name: item for name, item in items.items() if name.startswith("metadata/")}
    require(type(metadata) is dict and set(metadata) == set(expected))
    for name, (_, size, sha) in expected.items():
        raw = metadata[name]
        require(type(raw) is bytes and len(raw) == size and base.digest(raw) == sha)
    require(sum(map(len, metadata.values())) <= MAX_METADATA)


def archive_metadata(metadata, items):
    validate_metadata(metadata, items)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, raw in sorted(metadata.items()):
            entry = tarfile.TarInfo(name.removeprefix("metadata/"))
            entry.size, entry.mode, entry.mtime = len(raw), 0o444, tools.EPOCH
            archive.addfile(entry, io.BytesIO(raw))
    return gzip.compress(stream.getvalue(), mtime=0)


def unpack_metadata(raw, items):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_METADATA)
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
        expanded = stream.read(MAX_METADATA + 65537)
    require(len(expanded) <= MAX_METADATA + 65536)
    files = {}
    with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
        for entry in archive:
            name = "metadata/" + entry.name
            require(name in items and name.startswith("metadata/") and name not in files
                    and entry.isfile() and not entry.issparse() and not entry.pax_headers
                    and entry.mode == 0o444 and entry.uid == entry.gid == 0
                    and entry.mtime == tools.EPOCH and entry.size == items[name][1])
            with archive.extractfile(entry) as stream:
                files[name] = stream.read(entry.size + 1)
    validate_metadata(files, items)
    return files


def fetch_attempt(name, item, execute, deadline, clock):
    url, size, sha = item
    seconds = min(60, deadline - clock())
    def failed(category, returncode=None):
        raise FetchFailure(name, size, sha, category, returncode) from None
    if seconds <= 0:
        failed("deadline")
    # No curlrc, proxy, redirect, credentials, implicit curl retry or raw logs.
    try:
        result = execute(["/usr/bin/curl", "--disable", "--noproxy", "*", "--fail", "--silent",
            "--show-error", "--proto", "=https", "--tlsv1.2", "--connect-timeout", "10",
            "--max-time", str(seconds), "--max-filesize", str(size), url],
            env=dict(ENV), timeout=seconds, max_output_bytes=size + 65536,
            stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        failed("timeout")
    except base.processes.OutputLimitExceeded:
        failed("output-limit")
    except OSError:
        failed("io")
    except (ValueError, subprocess.SubprocessError):
        failed("process-rejected")
    if type(result.returncode) is not int or not -255 <= result.returncode <= 255:
        failed("process-rejected")
    if result.returncode:
        failed("process-exit", result.returncode)
    if clock() >= deadline:
        failed("deadline")
    if result.stderr != b"":
        failed("stderr")
    if type(result.stdout) is not bytes or len(result.stdout) != size:
        failed("size")
    if base.digest(result.stdout) != sha:
        failed("digest")
    return result.stdout


def fetch_item(name, item, execute, deadline, clock, sleep, *, cancelled=lambda: False):
    for attempt in range(1, 4):
        if cancelled():
            raise FetchFailure(name, item[1], item[2], "cancelled")
        try:
            return fetch_attempt(name, item, execute, deadline, clock)
        except FetchFailure as error:
            row = error.report
            retryable = row["failure"] == "timeout" or (
                row["failure"] == "process-exit" and row["returncode"] in TRANSIENT_CODES)
            if not retryable or attempt == 3:
                raise
            delay = 2 ** (attempt - 1)
            if deadline - clock() <= delay:
                raise FetchFailure(name, item[1], item[2], "deadline") from None
            # Record every retry without retaining response bytes, stderr,
            # exception strings, URLs, paths or request-derived identifiers.
            with RETRY_LOG_LOCK:
                print(json.dumps({**row, "schema": "nib.input-fetch-retry/v1", "result": "retry",
                                  "failed_attempt": attempt, "delay_seconds": delay}, sort_keys=True), flush=True)
            sleep(delay)
    raise AssertionError("unreachable fetch attempt")


def fetch_parallel(items, execute, deadline, clock, sleep):
    """Four in-flight public inputs; join bounded children before returning errors."""
    remaining = iter(sorted(items.items()))
    pending, files, stop = {}, {}, threading.Event()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        try:
            def replenish():
                while len(pending) < 4:
                    row = next(remaining, None)
                    if row is None:
                        break
                    name, item = row
                    future = pool.submit(fetch_item, name, item, execute, deadline, clock, sleep,
                                         cancelled=stop.is_set)
                    pending[future] = row
            replenish()
            while pending:
                seconds = deadline - clock()
                if seconds <= 0:
                    name, item = next(iter(pending.values()))
                    raise FetchFailure(name, item[1], item[2], "deadline")
                done, _ = concurrent.futures.wait(pending, timeout=seconds,
                                                 return_when=concurrent.futures.FIRST_COMPLETED)
                if not done:
                    name, item = next(iter(pending.values()))
                    raise FetchFailure(name, item[1], item[2], "deadline")
                # Observe every completed failure before admitting more work.
                for future in sorted(done, key=lambda f: pending[f][0]):
                    name, _ = pending.pop(future)
                    files[name] = future.result()
                replenish()
        except BaseException:
            stop.set()
            for future in pending:
                future.cancel()
            raise
    return files


def fetch(lock_raw, execute, *, clock=time.monotonic, sleep=time.sleep, guest_raw=None, metadata=None,
          workers=1, guest_scope=GUEST_SCOPE):
    require(type(workers) is int and workers in (1, 4))
    require(guest_raw is not None or guest_scope == GUEST_SCOPE)
    lock = json.loads(lock_raw)
    items = plan(lock) if guest_raw is None else build_plan(lock, json.loads(guest_raw), guest_scope=guest_scope)
    files = {"mkosi-tools-inputs.json": lock_raw}
    if guest_raw is not None:
        files["guest-inputs.json"] = guest_raw
    if metadata is not None:
        validate_metadata(metadata, items)
        files.update(metadata)
    deadline = clock() + 600
    if workers == 4:
        files.update(fetch_parallel({name: item for name, item in items.items() if name not in files},
                                    execute, deadline, clock, sleep))
        return files
    for name, item in sorted(items.items()):
        if name in files:
            continue
        files[name] = fetch_item(name, item, execute, deadline, clock, sleep)
    return files


def committed(extra=()):
    revision, sources = tools.committed((*prep.mkosi.EXTRA, PREPARER, prep.POLICY, SCRIPT, *extra))
    require(sources[prep.HELPER] == prep.HELPER_SOURCE
            and sources[tools.FILES[0]] == prep.mkosi.TOOLS_SOURCE)
    return revision, sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    guest = parser.add_mutually_exclusive_group()
    guest.add_argument("--include-guest", action="store_true",
                        help="fetch the fixed guest/tools union into archives/ for prepare_mkosi_inputs")
    guest.add_argument("--guest-runtime", action="store_true",
                       help="fetch the separate runtime candidate for offline APT rehearsal, not installation")
    archived = parser.add_mutually_exclusive_group()
    archived.add_argument("--metadata", type=Path,
                        help="reuse exact archived indexes; every size and hash is reverified")
    archived.add_argument("--metadata-archive", type=Path,
                          help="reuse the bounded public input-index archive after hash verification")
    parser.add_argument("--export-metadata", action="store_true",
                        help="export only authenticated metadata; requires --include-guest --metadata")
    args = parser.parse_args()
    require(not args.output.exists() and not args.output.is_symlink())
    guest_path, guest_scope = tools.target_spec("guest-runtime" if args.guest_runtime else "guest")
    _, sources = committed((guest_path,) if args.guest_runtime else ())
    guest_raw = sources[guest_path] if args.include_guest or args.guest_runtime else None
    items = (build_plan(json.loads(sources[prep.LOCK]), json.loads(guest_raw), guest_scope=guest_scope)
             if guest_raw else plan(json.loads(sources[prep.LOCK])))
    require(not args.export_metadata or (args.include_guest and args.metadata))
    metadata = None
    if args.metadata:
        with base.directory(args.metadata) as fd:
            metadata = {name: base.read_at(fd, name.removeprefix("metadata/"), 32 << 20)
                        for name in items if name.startswith("metadata/")}
    if args.metadata_archive:
        union = build_plan(json.loads(sources[prep.LOCK]), json.loads(sources[tools.FILES[-1]]))
        all_metadata = unpack_metadata(base.read(args.metadata_archive, MAX_METADATA), union)
        metadata = {name: raw for name, raw in all_metadata.items() if name in items}
    if args.export_metadata:
        raw = archive_metadata(metadata, items)
        base.publish(args.output.absolute(), {"metadata.tar.gz": raw,
            "metadata.sha256": (base.digest(raw) + "  metadata.tar.gz\n").encode("ascii")})
        print("Authenticated input-index archive exported; no network or package execution.")
        return
    files = fetch(sources[prep.LOCK], base.processes.execute_process_group,
                  guest_raw=guest_raw, metadata=metadata, workers=4, guest_scope=guest_scope)
    base.publish(args.output.absolute(), files)
    print("Pinned public build inputs fetched; no package execution.")


if __name__ == "__main__":
    try:
        main()
    except FetchFailure as error:
        print(json.dumps(error.report, sort_keys=True))
        sys.exit("Pinned input fetch failed; see content-free diagnostic.")
    except (OSError, ValueError, KeyError, TypeError, EOFError, tarfile.TarError, subprocess.SubprocessError):
        sys.exit("Pinned tools fetch rejected; no installation or execution.")
