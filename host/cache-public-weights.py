#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Preserve public pinned weights on an operator-approved lab NFS cache only."""
import argparse
from contextlib import contextmanager
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time

MANIFEST_SHA256 = "b74ae635c35372cf6c350e4b33ef60c90ff01fe6426ad0fa7bc99b1982fe5bed"
TOTAL_BYTES = 510311045295
OPTIONS = "nconnect=16,nodev,nosuid,noexec"


def require(ok):
    if not ok:
        raise ValueError("public-cache-rejected")


@contextmanager
def directory(path, *, create=False):
    path = Path(path)
    require(path.is_absolute() and str(path) == os.path.normpath(path) and ".." not in path.parts)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def manifest(raw):
    require(type(raw) is bytes and len(raw) < 65536
            and hashlib.sha256(raw).hexdigest() == MANIFEST_SHA256)
    rows = []
    for line in raw.decode("ascii").splitlines():
        digest, size, name = line.split()
        require(re.fullmatch(r"[0-9a-f]{64}", digest) and size.isdecimal()
                and re.fullmatch(r"[A-Za-z0-9_./-]+", name)
                and all(p not in ("", ".", "..") for p in name.split("/")))
        rows.append((digest, int(size), name))
    require(len(rows) == 83 and len({r[2] for r in rows}) == 83
            and sum(r[1] for r in rows) == TOTAL_BYTES)
    return rows


def command(args):
    return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, timeout=45, check=True,
                          env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}).stdout


def mount_cache(export, target):
    require(re.fullmatch(r"[a-z0-9.-]+:/[A-Za-z0-9_-]+", export) is not None)
    with directory(target, create=True) as fd:
        with os.scandir(fd) as entries:
            require(next(entries, None) is None)
    command(["mount", "-t", "nfs", "-o", OPTIONS, export, str(target)])
    verify_mount(export, target)


def verify_mount(export, target):
    row = json.loads(command(["findmnt", "--json", "--mountpoint", str(target),
                              "--output", "SOURCE,TARGET,FSTYPE,OPTIONS"]))
    require(type(row) is dict and len(row["filesystems"]) == 1)
    fs = row["filesystems"][0]
    require(fs["source"] == export and fs["target"] == str(target) and fs["fstype"] in ("nfs", "nfs4")
            and {"nodev", "nosuid", "noexec"} <= set(fs["options"].split(",")))


def unmount_cache(export, target):
    require(re.fullmatch(r"[a-z0-9.-]+:/[A-Za-z0-9_-]+", export) is not None)
    require(target != Path("/"))
    with directory(target):
        verify_mount(export, target)
    # Close our directory handle first. Never force or lazily detach a busy mount.
    command(["umount", "--", str(target)])
    try:
        command(["findmnt", "--json", "--mountpoint", str(target),
                 "--output", "SOURCE,TARGET,FSTYPE,OPTIONS"])
    except subprocess.CalledProcessError as error:
        require(error.returncode == 1 and error.stdout == b"")
    else:
        raise ValueError("public-cache-still-mounted")


def unmount_report(export, target, output, producer):
    require(not output.is_relative_to(target))
    report = {"schema": "nib.public-weight-cache-unmount/v1", "result": "fail",
              "producer": producer, "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "unmounted": False, "volume_deleted": False, "files_removed": False}
    # Reserve the receipt before mutation; an existing path must not lose evidence.
    with directory(output.parent) as parent:
        fd = os.open(output.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    with os.fdopen(fd, "w") as stream:
        try:
            unmount_cache(export, target)
            report.update(result="pass", unmounted=True)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            pass
        finally:
            report["ended_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            json.dump(report, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    return 0 if report["result"] == "pass" else 1


def inventory(root, rows):
    expected = {r[2] for r in rows}
    directories = {"."}
    for name in expected:
        directories.update(str(parent) for parent in Path(name).parents)
    seen, seen_dirs, pending = set(), {"."}, [Path(".")]
    while pending:
        relative = pending.pop()
        with directory(root / relative) as fd, os.scandir(fd) as entries:
            for entry in entries:
                name = str(relative / entry.name)
                if entry.is_dir(follow_symlinks=False):
                    require(name in directories and name not in seen_dirs)
                    seen_dirs.add(name)
                    pending.append(Path(name))
                else:
                    require(entry.is_file(follow_symlinks=False) and name in expected and name not in seen)
                    seen.add(name)
    require(seen == expected)


def check_file(root, row, deadline):
    digest, size, name = row
    path = root / name
    with directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size == size)
            h, count = hashlib.sha256(), 0
            while chunk := stream.read(min(8 << 20, size + 1 - count)):
                require(time.monotonic() < deadline)
                h.update(chunk)
                count += len(chunk)
            require(count == size and h.hexdigest() == digest)


def copy_pack(source, destination, rows, deadline):
    require(source != destination and not destination.is_relative_to(source)
            and not source.is_relative_to(destination))
    with directory(source):
        inventory(source, rows)
    # Verify public contents before any bytes are sent to the storage service.
    for row in rows:
        check_file(source, row, deadline)
    with directory(destination.parent) as parent:
        os.mkdir(destination.name, 0o700, dir_fd=parent)
    for digest, size, name in rows:
        src, dst = source / name, destination / name
        with directory(src.parent) as parent, directory(dst.parent, create=True) as target:
            incoming = os.open(src.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            outgoing = None
            try:
                info = os.fstat(incoming)
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size == size)
                outgoing = os.open(dst.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=target)
                h, count = hashlib.sha256(), 0
                while chunk := os.read(incoming, min(8 << 20, size + 1 - count)):
                    require(time.monotonic() < deadline)
                    h.update(chunk)
                    count += len(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(outgoing, view)
                        require(written > 0)
                        view = view[written:]
                require(count == size and h.hexdigest() == digest)
                os.fsync(outgoing)
            finally:
                os.close(incoming)
                if outgoing is not None:
                    os.close(outgoing)
    inventory(destination, rows)
    for row in rows:
        check_file(destination, row, deadline)
    with directory(destination) as fd:
        os.fsync(fd)
    return {"files": len(rows), "verified_bytes": sum(row[1] for row in rows)}


def main():
    require(sys.flags.isolated and sys.flags.no_site and sys.platform == "linux" and os.getuid() == 0)
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", required=True)
    parser.add_argument("--mount", required=True, type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--unmount-only", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--producer", required=True)
    parser.add_argument("--timeout", type=int)
    args = parser.parse_args()
    require(re.fullmatch(r"[0-9a-f]{40}", args.producer))
    if args.unmount_only:
        require(args.source is None and args.manifest is None and args.timeout is None)
        return unmount_report(args.export, args.mount, args.output, args.producer)
    require(args.source is not None and args.manifest is not None
            and type(args.timeout) is int and 60 < args.timeout <= 7200)
    with args.manifest.open("rb") as stream:
        rows = manifest(stream.read(65537))
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    report = {"schema": "nib.public-weight-cache/v1", "result": "fail", "stage": "mount",
              "producer": args.producer, "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "manifest_sha256": MANIFEST_SHA256, "started_utc": started, "confidential": False,
              "source_removed": False, "files": 0, "verified_bytes": 0}
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("cache-deadline")))
    signal.alarm(args.timeout)
    try:
        mount_cache(args.export, args.mount)
        report["stage"] = "copy-and-verify"
        report.update(copy_pack(args.source, args.mount / "deepseek-v4.1-flash-1017", rows,
                                time.monotonic() + args.timeout - 60))
        report.update(result="pass", stage="complete")
    except Exception:
        pass
    finally:
        signal.alarm(0)
        report["ended_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with args.output.open("x") as stream:
            json.dump(report, stream, sort_keys=True)
            stream.write("\n")
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit("Public cache rejected.")
