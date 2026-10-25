#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Preserve public pinned weights on an operator-approved lab cache only."""
import argparse
from contextlib import contextmanager
import datetime
import errno
import fcntl
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
import types
import uuid

MANIFEST_SHA256 = "b74ae635c35372cf6c350e4b33ef60c90ff01fe6426ad0fa7bc99b1982fe5bed"
TOTAL_BYTES = 510311045295
OPTIONS = "nconnect=16,nodev,nosuid,noexec"
# Spheron ES mount-tag protocol and mount(8) filesystem-independent options:
# https://docs.spheron.ai/connecting/volume-mounting/spheron-es (2026-10-23)
# https://man7.org/linux/man-pages/man8/mount.8.html (2026-10-23)
VIRTIOFS_OPTIONS = "nodev,nosuid,noexec"
ADMISSION_CHECKS = ["private-owned-directory", "flock-contention", "hardlink-no-replace", "file-and-directory-fsync"]
PROBE_SHA256 = "dd196679dc4b6d7ee335b3d62900b25484734884bd3da6e74f925260ad7864ff"
RESTORE_RESERVE = 60
WORKER_BOOTSTRAP = """
import sys
scope = {'__name__': 'bound_cache_restore'}
exec(compile(sys.argv.pop(1), '<bound-cache-restore>', 'exec'), scope)
scope['restore_worker'](*sys.argv[1:])
"""
PROFILE_SHA256 = "11475b07991fb510704d64143fcd9a0084327e586699604f14f88436960b754a"
PUBLICATION_SHA256 = "baaf98e34904b080513fdd468e83e1eb818c2e2ce69d87cf6fb6a421e7268666"
SEED_FILES = 48
SEED_BYTES = 310737990397
PROFILE_WORKER_BOOTSTRAP = """
import sys
scope = {'__name__': 'bound_profile_cache', '__file__': sys.argv.pop(1)}
exec(compile(sys.argv.pop(1), '<bound-profile-cache>', 'exec'), scope)
scope['profile_worker'](*sys.argv[1:])
"""


def require(ok):
    if not ok:
        raise ValueError("public-cache-rejected")


@contextmanager
def directory(path, *, create=False, trusted=False, private=False):
    path = Path(path)
    require(path.is_absolute() and str(path) == os.path.normpath(path) and ".." not in path.parts)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        current = Path("/")
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            current /= part
            if trusted or private:
                info = os.fstat(fd)
                require(info.st_uid in (0, os.getuid()))
                sticky_root = (current in (Path("/tmp"), Path("/private/tmp"), Path("/var/tmp"))
                               and info.st_uid == 0 and info.st_mode & stat.S_ISVTX)
                require(not info.st_mode & 0o022 or sticky_root)
        if trusted or private:
            info = os.fstat(fd)
            require(info.st_uid == os.getuid() and not info.st_mode & 0o022)
            if private:
                require(stat.S_IMODE(info.st_mode) == 0o700)
        yield fd
    finally:
        os.close(fd)


def manifest(raw):
    return manifest_rows(raw, MANIFEST_SHA256, TOTAL_BYTES, 83)


def manifest_rows(raw, expected, total, count):
    require(type(raw) is bytes and len(raw) < 65536
            and hashlib.sha256(raw).hexdigest() == expected)
    rows = []
    for line in raw.decode("ascii").splitlines():
        digest, size, name = line.split()
        require(re.fullmatch(r"[0-9a-f]{64}", digest) and size.isdecimal()
                and re.fullmatch(r"[A-Za-z0-9_./-]+", name)
                and all(p not in ("", ".", "..") for p in name.split("/")))
        rows.append((digest, int(size), name))
    require(len(rows) == count and len({r[2] for r in rows}) == count
            and sum(r[1] for r in rows) == total)
    return rows


def command(args):
    return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, timeout=45, check=True,
                          env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}).stdout


def cache_source(export, backend):
    require(backend in {"nfs", "virtiofs"} and type(export) is str)
    pattern = r"[a-z0-9.-]+:/[A-Za-z0-9_-]+" if backend == "nfs" else r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"
    require(re.fullmatch(pattern, export) is not None)


def require_unmounted(target):
    try:
        command(["findmnt", "--json", "--mountpoint", str(target),
                 "--output", "SOURCE,TARGET,FSTYPE,OPTIONS"])
    except subprocess.CalledProcessError as error:
        require(error.returncode == 1 and error.stdout == b"")
    else:
        raise ValueError("public-cache-still-mounted")


def mount_cache(export, target, *, readonly=False, backend="nfs"):
    cache_source(export, backend)
    if backend == "virtiofs":
        require(target != Path("/"))
    with directory(target, create=True, private=backend == "virtiofs") as fd:
        with os.scandir(fd) as entries:
            require(next(entries, None) is None)
    if backend == "virtiofs":
        require_unmounted(target)
    options = OPTIONS if backend == "nfs" else VIRTIOFS_OPTIONS
    command(["mount", "-t", backend, "-o", ("ro," if readonly else "") + options, export, str(target)])
    verify_mount(export, target, readonly=readonly, **({} if backend == "nfs" else {"backend": backend}))
    if backend == "virtiofs":
        with directory(target, trusted=True):
            pass


def verify_mount(export, target, *, readonly=False, backend="nfs", within=None):
    cache_source(export, backend)
    require(within is None or (backend == "virtiofs" and within.parent == target))
    # findmnt(8) --target resolves a child to its containing mount, unlike
    # --mountpoint. Explicit columns keep the readback independent of defaults.
    # https://man7.org/linux/man-pages/man8/findmnt.8.html (2026-10-23)
    row = json.loads(command(["findmnt", "--json", "--mountpoint" if within is None else "--target", str(target if within is None else within),
                              "--output", "SOURCE,TARGET,FSTYPE,OPTIONS"]),
                     **({} if backend == "nfs" else {"object_pairs_hook": unique}))
    require(type(row) is dict and len(row["filesystems"]) == 1)
    fs = row["filesystems"][0]
    require(fs["source"] == export and fs["target"] == str(target)
            and fs["fstype"] in (("nfs", "nfs4") if backend == "nfs" else ("virtiofs",))
            and {"nodev", "nosuid", "noexec"} <= set(fs["options"].split(",")))
    if backend == "virtiofs":
        require(not {"dev", "suid", "exec"} & set(fs["options"].split(",")))
    if readonly:
        require("ro" in fs["options"].split(",") and "rw" not in fs["options"].split(","))
    elif backend == "virtiofs":
        options = set(fs["options"].split(","))
        require(len(options & {"rw", "ro"}) == 1 and (readonly is None or "rw" in options))


def unmount_cache(export, target, *, backend="nfs"):
    cache_source(export, backend)
    require(target != Path("/"))
    with directory(target, trusted=backend == "virtiofs"):
        verify_mount(export, target, **({} if backend == "nfs" else {"backend": backend, "readonly": None}))
    # Close our directory handle first. Never force or lazily detach a busy mount.
    command(["umount", "--", str(target)])
    require_unmounted(target)


def unmount_report(export, target, output, producer, *, backend="nfs"):
    require(not output.is_relative_to(target))
    report = {"schema": "nib.public-weight-cache-unmount/v1", "result": "fail",
              "producer": producer, "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "unmounted": False, "volume_deleted": False, "files_removed": False}
    if backend != "nfs":
        report["backend"] = backend
    # Reserve the receipt before mutation; an existing path must not lose evidence.
    with directory(output.parent, private=backend == "virtiofs") as parent:
        fd = os.open(output.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    with os.fdopen(fd, "w") as stream:
        try:
            unmount_cache(export, target, **({} if backend == "nfs" else {"backend": backend}))
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


def file_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def check_file(root, row, deadline, *, stable=False):
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
            if stable:
                require(file_identity(info) == file_identity(os.fstat(stream.fileno()))
                        == file_identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)))
                return file_identity(info)


def copy_pack(source, destination, rows, deadline, *, source_rows=None, stable=False):
    require(source != destination and not destination.is_relative_to(source)
            and not source.is_relative_to(destination))
    with directory(source):
        inventory(source, rows if source_rows is None else source_rows)
    # Verify public contents before any bytes are sent to the storage service.
    for row in rows:
        check_file(source, row, deadline, stable=stable)
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
                if stable:
                    require(file_identity(info) == file_identity(os.fstat(incoming))
                            == file_identity(os.stat(src.name, dir_fd=parent, follow_symlinks=False)))
                os.fsync(outgoing)
            finally:
                os.close(incoming)
                if outgoing is not None:
                    os.close(outgoing)
    inventory(destination, rows)
    identities = {}
    for row in rows:
        identities[row[2]] = check_file(destination, row, deadline, stable=stable)
    with directory(destination) as fd:
        os.fsync(fd)
    result = {"files": len(rows), "verified_bytes": sum(row[1] for row in rows)}
    if stable:
        result["identities"] = identities
    return result


def read_regular(path, limit=65536):
    with directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= limit)
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
            require(len(raw) == before.st_size and
                    all(getattr(before, key) == getattr(after, key) for key in fields))
            return raw


class Cancelled(BaseException):
    """Stop the supervised worker instead of being swallowed by its selector."""


def load_capture():
    path = Path(__file__).absolute().with_name("probe.py")
    raw = read_regular(path)
    require(hashlib.sha256(raw).hexdigest() == PROBE_SHA256)
    helper = types.ModuleType("bound_cache_capture")
    exec(compile(raw, str(path), "exec", dont_inherit=True), helper.__dict__)
    return helper.capture


def restore_worker(export, target, destination, manifest_path, seconds):
    require(sys.platform == "linux" and sys.flags.isolated and sys.flags.no_site
            and sys.dont_write_bytecode and os.getuid() == 0)
    target, destination, manifest_path = map(Path, (target, destination, manifest_path))
    seconds = int(seconds)
    require(0 < seconds <= 7200 - RESTORE_RESERVE)
    deadline = time.monotonic() + seconds
    rows = manifest(read_regular(manifest_path))
    require(not destination.is_relative_to(target) and not target.is_relative_to(destination))
    # All potentially blocking source/destination work belongs to this child.
    with directory(destination.parent):
        require(not os.path.lexists(destination))
        space = os.statvfs(destination.parent)
        needed = sum(row[1] for row in rows)
        require(space.f_bavail * space.f_frsize >= needed + max(10 << 30, (needed + 9) // 10))
    mount_cache(export, target, readonly=True)
    result = copy_pack(target / "deepseek-v4.1-flash-1017", destination, rows, deadline)
    verify_mount(export, target, readonly=True)
    require(time.monotonic() < deadline)
    print(json.dumps({"schema": "nib.public-weight-cache-restore-worker/v1", **result}, sort_keys=True))


def restore_report(export, target, destination, manifest_path, output, producer, seconds):
    require(type(seconds) is int and RESTORE_RESERVE < seconds <= 7200)
    require(re.fullmatch(r"[0-9a-f]{40}", producer) is not None)
    for path in (target, destination, manifest_path, output):
        require(path.is_absolute() and str(path) == os.path.normpath(path) and ".." not in path.parts)
    require(not destination.is_relative_to(target) and not target.is_relative_to(destination)
            and not output.is_relative_to(target) and not output.is_relative_to(destination)
            and not manifest_path.is_relative_to(target) and not manifest_path.is_relative_to(destination))
    source = read_regular(Path(__file__).absolute())
    report = {"schema": "nib.public-weight-cache-restore/v1", "result": "fail", "stage": "preflight",
              "producer": producer, "script_sha256": hashlib.sha256(source).hexdigest(),
              "supervisor_sha256": PROBE_SHA256, "timeout_seconds": seconds,
              "manifest_sha256": MANIFEST_SHA256,
              "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "ended_utc": None, "failure": None, "cleanup": "not-started", "worker_exit_confirmed": False,
              "process_group_absence_verified": False,
              "confidential": False, "source_removed": False, "files": 0, "verified_bytes": 0}
    with directory(output.parent) as parent:
        fd = os.open(output.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    with os.fdopen(fd, "w") as stream:
        def publish():
            stream.seek(0)
            json.dump(report, stream, sort_keys=True)
            stream.write("\n")
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
        cancelled = False
        def cancel(*_):
            nonlocal cancelled
            if not cancelled:
                cancelled = True
                raise Cancelled("restore-cancelled")
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        publish()
        try:
            for sig in previous:
                signal.signal(sig, cancel)
            capture = load_capture()
            report.update(stage="restore-worker", cleanup="unproven")
            publish()
            budget = seconds - RESTORE_RESERVE
            deadline = time.monotonic() + budget
            raw = capture([sys.executable, "-I", "-S", "-B", "-c", WORKER_BOOTSTRAP, source.decode("utf-8"),
                           export, str(target), str(destination), str(manifest_path), str(budget)],
                          timeout=budget, limit=4096)
            require(time.monotonic() < deadline and type(raw) is bytes and len(raw) <= 4096)
            row = json.loads(raw)
            require(type(row) is dict and set(row) == {"schema", "files", "verified_bytes"}
                    and row["schema"] == "nib.public-weight-cache-restore-worker/v1"
                    and type(row["files"]) is int and row["files"] == 83
                    and type(row["verified_bytes"]) is int and row["verified_bytes"] == TOTAL_BYTES)
            report.update(result="pass", stage="complete", files=row["files"], verified_bytes=row["verified_bytes"],
                          cleanup="worker-completed", worker_exit_confirmed=True)
        except (Cancelled, InterruptedError):
            report.update(result="fail", failure="cancelled")
        except Exception as error:
            kinds = {"probe-timeout": "timeout", "probe-output-limit": "output-limit",
                     "probe-command-failed": "worker-failed", "probe-cleanup-timeout": "cleanup-unproven",
                     "probe-cleanup-failed": "cleanup-unproven"}
            report.update(result="fail", failure=kinds.get(
                error.args[0] if error.args and type(error.args[0]) is str else "", "rejected"))
        finally:
            for sig in previous:
                signal.signal(sig, signal.SIG_IGN)
            try:
                report["ended_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                publish()
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
    return 0 if report["result"] == "pass" else 1


def load_pinned(relative, expected):
    path = Path(__file__).absolute().parent.parent / relative
    raw = read_regular(path)
    require(hashlib.sha256(raw).hexdigest() == expected)
    helper = types.ModuleType("bound_cache_helper")
    helper.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), helper.__dict__)
    return helper


def profiles():
    helper = load_pinned("scripts/deepseek_model_profile.py", PROFILE_SHA256)
    require(helper.NAMES == ("original", "uncensored"))
    original, target = helper.select(), helper.select("uncensored")
    require(original["manifest_sha256"] == MANIFEST_SHA256 and original["total_bytes"] == TOTAL_BYTES)
    return original, target


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def seed_rows(original, target):
    known = {name: (digest, size) for digest, size, name in original}
    return [row for row in target if known.get(row[2]) == row[:2]]


def strict_inventory(root, rows):
    inventory(root, rows)
    result = {}
    for _, size, name in rows:
        path = root / name
        with directory(path.parent) as parent:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                info = os.fstat(fd)
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size == size
                        and file_identity(info) == file_identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)))
                result[name] = file_identity(info)
            finally:
                os.close(fd)
    return result


def publication_preflight(parent, publication, deadline):
    # Exercise the exact no-replace primitive on this filesystem before a large
    # copy. Failure leaves only our private probe, never a fallback publication.
    name = ".nib-cache-check-" + uuid.uuid4().hex
    os.mkdir(name, 0o700, dir_fd=parent)
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        info = os.fstat(fd)
        os.mkdir("source", 0o700, dir_fd=fd)
        os.mkdir("occupied", 0o700, dir_fd=fd)
        source = os.stat("source", dir_fd=fd, follow_symlinks=False)
        occupied = os.stat("occupied", dir_fd=fd, follow_symlinks=False)
        try:
            publication.rename_exclusive(fd, "source", "occupied")
        except OSError as error:
            require(error.errno == errno.EEXIST)
        else:
            raise ValueError("public-cache-rejected")
        for entry, expected in (("source", source), ("occupied", occupied)):
            require(file_identity(os.stat(entry, dir_fd=fd, follow_symlinks=False)) == file_identity(expected))
        require(time.monotonic() < deadline)
        publication.rename_exclusive(fd, "source", "published")
        current = os.stat("published", dir_fd=fd, follow_symlinks=False)
        require((current.st_dev, current.st_ino) == (source.st_dev, source.st_ino))
        os.rmdir("occupied", dir_fd=fd)
        os.rmdir("published", dir_fd=fd)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        require((current.st_dev, current.st_ino) == (info.st_dev, info.st_ino))
        os.rmdir(name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(fd)


def atomic_copy(source, destination, rows, source_rows, deadline):
    return verified_copy(source, destination, rows, source_rows, deadline, atomic=True)


def upload_copy(source, destination, rows, deadline):
    # Linux v6.8 fs/nfs/dir.c:nfs_rename rejects all rename flags. This fixed
    # upload mode uses exclusive creation and a complete-copy receipt, not an
    # atomic-rename fallback. Failed/partial directories are never overwritten.
    return verified_copy(source, destination, rows, rows, deadline, atomic=False)


def verified_copy(source, destination, rows, source_rows, deadline, *, atomic):
    # The old full-pack helper owns all copying/hashing. Atomic local publication
    # and non-atomic exclusive upload share checks, never a rename fallback.
    publication = load_pinned("scripts/pilot/export.py", PUBLICATION_SHA256) if atomic else None
    require(source != destination and not source.is_relative_to(destination)
            and not destination.is_relative_to(source))
    with directory(source) as source_fd, directory(destination.parent) as parent:
        parent_info, source_info = os.fstat(parent), os.fstat(source_fd)
        require(parent_info.st_uid == os.getuid() and not parent_info.st_mode & 0o022)
        require(not os.path.lexists(destination))
        initial = strict_inventory(source, source_rows)
        if atomic:
            publication_preflight(parent, publication, deadline)
        temporary = destination.parent / (".nib-cache-" + uuid.uuid4().hex) if atomic else destination
        copied = copy_pack(source, temporary, rows, deadline, source_rows=source_rows, stable=True)
        require(strict_inventory(source, source_rows) == initial)
        require(file_identity(source_info) == file_identity(os.stat(source, follow_symlinks=False)))
        require(strict_inventory(temporary, rows) == copied["identities"])
        dirs = {"."}
        for _, _, name in rows:
            dirs.update(str(p) for p in Path(name).parents)
        for name in sorted(dirs, key=lambda value: (-len(Path(value).parts), value)):
            with directory(temporary / name) as fd:
                os.fsync(fd)
        with directory(temporary) as temporary_fd:
            info = os.fstat(temporary_fd)
            require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
            require(file_identity(info) == file_identity(os.stat(temporary.name, dir_fd=parent, follow_symlinks=False)))
            current = os.stat(destination.parent, follow_symlinks=False)
            require((current.st_dev, current.st_ino, current.st_mode, current.st_uid)
                    == (parent_info.st_dev, parent_info.st_ino, parent_info.st_mode, parent_info.st_uid))
            require(time.monotonic() < deadline)
            require(strict_inventory(temporary, rows) == copied["identities"])
            if atomic:
                publication.rename_exclusive(parent, temporary.name, destination.name)
            os.fsync(parent)
            published = os.stat(destination.name, dir_fd=parent, follow_symlinks=False)
            require((published.st_dev, published.st_ino) == (info.st_dev, info.st_ino))
    require(time.monotonic() < deadline)
    return {"files": len(rows), "verified_bytes": sum(row[1] for row in rows)}


def filesystem_probe(parent, deadline):
    # Separate opens contend even in one process: flock(2), DESCRIPTION.
    # https://man7.org/linux/man-pages/man2/flock.2.html (2026-10-23)
    # Only this exclusively created probe is removed, and only on success.
    name = ".nib-fs-check-" + uuid.uuid4().hex
    os.mkdir(name, 0o700, dir_fd=parent)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    first = os.open(name, flags, dir_fd=parent)
    try:
        identity = os.fstat(first)
        require(identity.st_dev == os.fstat(parent).st_dev and stat.S_IMODE(identity.st_mode) == 0o700
                and identity.st_uid == os.getuid())
        second = os.open(name, flags, dir_fd=parent)
        try:
            require(os.path.samestat(identity, os.fstat(second)))
            fcntl.flock(first, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                require(error.errno in (errno.EAGAIN, errno.EWOULDBLOCK))
            else:
                raise ValueError("public-cache-lock-not-exclusive")
            fcntl.flock(first, fcntl.LOCK_UN)
            fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(second)
        before = {}
        for entry, data in (("source", b"source"), ("occupied", b"target")):
            fd = os.open(entry, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=first)
            try:
                require(os.write(fd, data) == len(data))
                os.fsync(fd)
                os.lseek(fd, 0, os.SEEK_SET)
                require(os.read(fd, len(data) + 1) == data)
                before[entry] = file_identity(os.fstat(fd))
            finally:
                os.close(fd)
        try:
            os.link("source", "occupied", src_dir_fd=first, dst_dir_fd=first, follow_symlinks=False)
        except OSError as error:
            require(error.errno == errno.EEXIST)
        else:
            raise ValueError("public-cache-link-replaced")
        for entry, expected in before.items():
            require(file_identity(os.stat(entry, dir_fd=first, follow_symlinks=False)) == expected)
        os.link("source", "published", src_dir_fd=first, dst_dir_fd=first, follow_symlinks=False)
        source = os.stat("source", dir_fd=first, follow_symlinks=False)
        published = os.stat("published", dir_fd=first, follow_symlinks=False)
        require(os.path.samestat(source, published) and source.st_nlink == published.st_nlink == 2)
        os.fsync(first)
        os.unlink("published", dir_fd=first)
        require(os.stat("source", dir_fd=first, follow_symlinks=False).st_nlink == 1)
        require(time.monotonic() < deadline)
        for entry in ("source", "occupied"):
            os.unlink(entry, dir_fd=first)
        os.fsync(first)
        require(os.path.samestat(identity, os.stat(name, dir_fd=parent, follow_symlinks=False)))
        os.rmdir(name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(first)


def admit_filesystem(export, mount, staging_parent, deadline):
    require(staging_parent.parent == mount and time.monotonic() < deadline)
    verify_mount(export, mount, backend="virtiofs")
    with directory(mount, trusted=True) as volume:
        volume_info = os.fstat(volume)
        try:
            os.mkdir(staging_parent.name, 0o700, dir_fd=volume)
        except FileExistsError:
            pass
        with directory(staging_parent, private=True) as parent:
            parent_info = os.fstat(parent)
            require(parent_info.st_dev == volume_info.st_dev)
            verify_mount(export, mount, backend="virtiofs", within=staging_parent)
            filesystem_probe(parent, deadline)
            require(os.path.samestat(parent_info, os.stat(staging_parent.name, dir_fd=volume, follow_symlinks=False)))
        os.fsync(volume)
        require(os.path.samestat(volume_info, os.stat(mount, follow_symlinks=False)))
    with directory(staging_parent, private=True):
        verify_mount(export, mount, backend="virtiofs", within=staging_parent)
    require(time.monotonic() < deadline)


def profile_worker(operation, export, mount, local, manifest_path, seed_manifest, seconds, backend="nfs"):
    require(sys.platform == "linux" and sys.flags.isolated and sys.flags.no_site
            and sys.dont_write_bytecode and os.getuid() == 0)
    require(operation in {"seed", "upload", "restore", "admit"})
    seconds = int(seconds)
    require(0 < seconds <= 7200 - RESTORE_RESERVE)
    deadline = time.monotonic() + seconds
    mount, local, manifest_path = map(Path, (mount, local, manifest_path))
    original, target = profiles()
    rows = manifest_rows(read_regular(manifest_path), target["manifest_sha256"], target["total_bytes"], 79)
    source_rows = rows
    if operation == "seed":
        source_rows = manifest(read_regular(Path(seed_manifest)))
        rows = seed_rows(source_rows, rows)
        require(len(rows) == SEED_FILES and sum(row[1] for row in rows) == SEED_BYTES)
    else:
        require(seed_manifest == "-")
    readonly = operation != "upload"
    mount_options = {} if backend == "nfs" else {"backend": backend}
    if operation == "admit":
        require(backend == "virtiofs" and local.parent == mount and seconds <= 240)
        mount_cache(export, mount, **mount_options)
        admit_filesystem(export, mount, local, deadline)
        print(json.dumps({"schema": "nib.public-weight-profile-worker/v1", "operation": operation,
                          "publication_mode": "none", "files": 0, "verified_bytes": 0,
                          "filesystem_checks": ADMISSION_CHECKS}, sort_keys=True))
        return
    mount_cache(export, mount, readonly=readonly, **mount_options)
    source = local if operation == "upload" else mount / (
        original["served_model_name"] if operation == "seed" else target["served_model_name"])
    destination = mount / target["served_model_name"] if operation == "upload" else local
    with directory(destination.parent) as parent:
        require(not os.path.lexists(destination))
        space = os.fstatvfs(parent)
        # Keep the full target reserve even for a seed. The remaining download
        # still uses the unmodified staging recorder's full-pack capacity gate.
        needed = target["total_bytes"]
        require(space.f_bavail * space.f_frsize >= needed + max(10 << 30, (needed + 9) // 10))
    result = (upload_copy(source, destination, rows, deadline) if operation == "upload"
              else atomic_copy(source, destination, rows, source_rows, deadline))
    verify_mount(export, mount, readonly=readonly, **mount_options)
    require(time.monotonic() < deadline)
    print(json.dumps({"schema": "nib.public-weight-profile-worker/v1", "operation": operation,
                      "publication_mode": publication_mode(operation), **result}, sort_keys=True))


def publication_mode(operation):
    if operation == "admit":
        return "none"
    return "exclusive-directory-copy" if operation == "upload" else "atomic-no-replace-directory"


def profile_report(operation, export, mount, local, manifest_path, seed_manifest, output, producer, seconds, *, backend="nfs"):
    require(operation in {"seed", "upload", "restore", "admit"} and type(seconds) is int and RESTORE_RESERVE < seconds <= 7200)
    require(re.fullmatch(r"[0-9a-f]{40}", producer) is not None)
    cache_source(export, backend)
    admission = operation == "admit"
    require(not admission or (backend == "virtiofs" and seconds <= 300 and local.parent == mount))
    require((seed_manifest is not None) == (operation == "seed"))
    paths = [mount, local, manifest_path, output] + ([] if seed_manifest is None else [seed_manifest])
    for path in paths:
        require(path.is_absolute() and str(path) == os.path.normpath(path) and ".." not in path.parts)
    if admission:
        paths.remove(local)
    require(all(left != right and left not in right.parents and right not in left.parents
                for index, left in enumerate(paths) for right in paths[index + 1:]))
    original, target = profiles()
    source = read_regular(Path(__file__).absolute())
    report = {"schema": "nib.public-weight-profile-cache/v1", "result": "fail", "stage": "preflight",
              "operation": operation, "model_profile": target, "producer": producer,
              "script_sha256": hashlib.sha256(source).hexdigest(), "supervisor_sha256": PROBE_SHA256,
              "profile_helper_sha256": PROFILE_SHA256,
              "publication_helper_sha256": None if operation in {"upload", "admit"} else PUBLICATION_SHA256,
              "source_manifest_sha256": original["manifest_sha256"] if operation == "seed" else target["manifest_sha256"],
              "target_manifest_sha256": target["manifest_sha256"], "timeout_seconds": seconds,
              "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "ended_utc": None,
              "failure": None, "cleanup": "not-started", "worker_exit_confirmed": False,
              "process_group_absence_verified": False, "publication": "not-started",
              "publication_mode": publication_mode(operation),
              "pack_complete": False, "runtime_ready": False, "confidential": False,
              "source_removed": False, "files": 0, "verified_bytes": 0,
              "remaining_files": None, "remaining_bytes": None}
    if backend != "nfs":
        report["backend"] = backend
    if admission:
        report.update(filesystem_admitted=False, filesystem_checks=[], hardware_validated=False,
                      mount_state="unproven", publication="not-applicable", capacity_checked=False,
                      immutable=False)
    with directory(output.parent, private=backend == "virtiofs") as parent:
        info = os.fstat(parent)
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
        fd = os.open(output.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    with os.fdopen(fd, "w") as stream:
        def publish():
            stream.seek(0)
            json.dump(report, stream, sort_keys=True)
            stream.write("\n")
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
        cancelled = False
        def cancel(*_):
            nonlocal cancelled
            if not cancelled:
                cancelled = True
                raise Cancelled("cache-cancelled")
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        publish()
        try:
            for sig in previous:
                signal.signal(sig, cancel)
            capture = load_capture()
            report.update(stage="profile-worker", cleanup="unproven",
                          publication="not-applicable" if admission else "unproven")
            publish()
            budget = seconds - RESTORE_RESERVE
            deadline = time.monotonic() + budget
            raw = capture([sys.executable, "-I", "-S", "-B", "-c", PROFILE_WORKER_BOOTSTRAP,
                           str(Path(__file__).absolute()), source.decode("utf-8"), operation, export,
                           str(mount), str(local), str(manifest_path),
                           "-" if seed_manifest is None else str(seed_manifest), str(budget)]
                          + ([] if backend == "nfs" else [backend]), timeout=budget, limit=4096)
            require(time.monotonic() < deadline and type(raw) is bytes and len(raw) <= 4096)
            row = json.loads(raw, object_pairs_hook=unique)
            count, total = (SEED_FILES, SEED_BYTES) if operation == "seed" else (79, target["total_bytes"])
            if admission:
                count, total = 0, 0
            fields = {"schema", "operation", "publication_mode", "files", "verified_bytes"}
            require(type(row) is dict and set(row) == fields | ({"filesystem_checks"} if admission else set())
                    and row["schema"] == "nib.public-weight-profile-worker/v1" and row["operation"] == operation
                    and row["publication_mode"] == publication_mode(operation)
                    and type(row["files"]) is int and row["files"] == count
                    and type(row["verified_bytes"]) is int and row["verified_bytes"] == total)
            if admission:
                require(row["filesystem_checks"] == ADMISSION_CHECKS)
                report.update(filesystem_admitted=True, filesystem_checks=ADMISSION_CHECKS,
                              mount_state="mounted-read-write")
            report.update(result="pass", stage="filesystem-admitted" if admission else "complete",
                          cleanup="worker-completed", worker_exit_confirmed=True,
                          publication="not-applicable" if admission else "confirmed",
                          pack_complete=operation not in {"seed", "admit"}, files=count, verified_bytes=total,
                          remaining_files=None if admission else 79 - count,
                          remaining_bytes=None if admission else target["total_bytes"] - total)
        except (Cancelled, InterruptedError):
            report.update(result="fail", failure="cancelled", pack_complete=False, files=0, verified_bytes=0,
                          remaining_files=None, remaining_bytes=None)
        except Exception as error:
            kinds = {"probe-timeout": "timeout", "probe-output-limit": "output-limit",
                     "probe-command-failed": "worker-failed", "probe-cleanup-timeout": "cleanup-unproven",
                     "probe-cleanup-failed": "cleanup-unproven"}
            report.update(result="fail", pack_complete=False, files=0, verified_bytes=0,
                          remaining_files=None, remaining_bytes=None,
                          failure=kinds.get(error.args[0] if error.args and type(error.args[0]) is str else "", "rejected"))
        finally:
            for sig in previous:
                signal.signal(sig, signal.SIG_IGN)
            try:
                if admission and report["result"] != "pass":
                    report.update(filesystem_admitted=False, filesystem_checks=[], mount_state="unproven")
                report["ended_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                publish()
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
    return 0 if report["result"] == "pass" else 1


def main():
    require(sys.flags.isolated and sys.flags.no_site and sys.platform == "linux" and os.getuid() == 0)
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", required=True)
    parser.add_argument("--backend", choices=("nfs", "virtiofs"), default="nfs")
    parser.add_argument("--mount", required=True, type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--restore-to", type=Path)
    parser.add_argument("--seed-to", type=Path)
    parser.add_argument("--seed-manifest", type=Path)
    parser.add_argument("--model-profile", choices=("original", "uncensored"), default="original")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--unmount-only", action="store_true")
    parser.add_argument("--mount-only", action="store_true")
    parser.add_argument("--staging-parent", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--producer", required=True)
    parser.add_argument("--timeout", type=int)
    args = parser.parse_args()
    require(re.fullmatch(r"[0-9a-f]{40}", args.producer))
    backend = {} if args.backend == "nfs" else {"backend": args.backend}
    if args.mount_only:
        require(args.backend == "virtiofs" and args.model_profile == "uncensored"
                and args.staging_parent is not None and args.manifest is not None
                and not args.unmount_only and args.source is None and args.restore_to is None
                and args.seed_to is None and args.seed_manifest is None)
        return profile_report("admit", args.export, args.mount, args.staging_parent, args.manifest,
                              None, args.output, args.producer, args.timeout, **backend)
    require(args.staging_parent is None)
    if args.unmount_only:
        require(args.source is None and args.restore_to is None and args.manifest is None and args.timeout is None
                and args.seed_to is None and args.seed_manifest is None and args.model_profile == "original")
        return unmount_report(args.export, args.mount, args.output, args.producer, **backend)
    if args.model_profile == "uncensored":
        choices = {"upload": args.source, "restore": args.restore_to, "seed": args.seed_to}
        require(sum(value is not None for value in choices.values()) == 1 and args.manifest is not None)
        operation, local = next((key, value) for key, value in choices.items() if value is not None)
        return profile_report(operation, args.export, args.mount, local, args.manifest, args.seed_manifest,
                              args.output, args.producer, args.timeout, **backend)
    require(args.backend == "nfs")
    require(args.seed_to is None and args.seed_manifest is None)
    require((args.source is None) != (args.restore_to is None) and args.manifest is not None
            and type(args.timeout) is int and 60 < args.timeout <= 7200)
    if args.restore_to is not None:
        return restore_report(args.export, args.mount, args.restore_to, args.manifest.absolute(),
                              args.output, args.producer, args.timeout)
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
