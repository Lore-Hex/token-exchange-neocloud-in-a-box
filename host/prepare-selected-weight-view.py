#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Create a separate read-only public checkpoint view; never relax source modes."""
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
import sys
import time
import types
import uuid

CACHE_SHA256 = "25141adae8f894a54f4b8f90c65ba26a9d7b3f493121b640b381c4f2a0cb57f6"
RESERVE = 60
SHARED_MEMORY = Path("/dev/shm")
BOOTSTRAP = """
import sys
scope = {'__name__': 'bound_selected_weight_view', '__file__': sys.argv.pop(1)}
exec(compile(sys.argv.pop(1), '<bound-selected-weight-view>', 'exec'), scope)
scope['worker'](*sys.argv[1:])
"""


class Cancelled(BaseException):
    """Stop the worker through the existing failed-publication boundary."""


def require(value):
    if not value:
        raise ValueError("selected-weight-view-rejected")


def cache_helper():
    path = Path(__file__).absolute().with_name("cache-public-weights.py")
    raw = path.read_bytes()
    require(len(raw) <= 65536 and hashlib.sha256(raw).hexdigest() == CACHE_SHA256)
    module = types.ModuleType("bound_selected_weight_cache")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module


def selection(cache, name="uncensored"):
    require(type(name) is str and name in ("original", "uncensored"))
    original, selected = cache.profiles()
    return (original, 83) if name == "original" else (selected, 79)


def identity_fields(profile, *, worker=False):
    original = profile["name"] == "original"
    return {"schema": "nib.selected-weight-view" + ("-worker" if worker else "")
            + ("/v2" if original else "/v1"), **({"model_profile": profile} if original else {})}


@contextmanager
def private_view_parent(cache, path):
    """Permit one private child of Linux's root-owned sticky RAM disk."""
    if path.parent != SHARED_MEMORY:
        with cache.directory(path, private=True) as fd:
            yield fd
        return
    # Open relative to retained descriptors. Other users may create entries in
    # the sticky directory, but cannot replace our owned, non-symlink child.
    with cache.directory(SHARED_MEMORY.parent) as device:
        info = os.fstat(device)
        require(info.st_uid == 0 and not info.st_mode & 0o022)
        shared = os.open(SHARED_MEMORY.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=device)
        try:
            info = os.fstat(shared)
            require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o1777)
            private = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=shared)
            try:
                info = os.fstat(private)
                require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
                yield private
            finally:
                os.close(private)
        finally:
            os.close(shared)


def view_modes(cache, root, rows, deadline, *, apply=False):
    directories = {"."}
    for _, _, name in rows:
        directories.update(str(path) for path in Path(name).parents)
        with cache.directory((root / name).parent) as parent:
            fd = os.open(Path(name).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                info = os.fstat(fd)
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid())
                if apply:
                    os.fchmod(fd, 0o444)
                    os.fsync(fd)
                require(stat.S_IMODE(os.fstat(fd).st_mode) == 0o444 and time.monotonic() < deadline)
                require(cache.file_identity(os.fstat(fd)) == cache.file_identity(
                    os.stat(Path(name).name, dir_fd=parent, follow_symlinks=False)))
            finally:
                os.close(fd)
    for name in sorted(directories, key=lambda value: (-len(Path(value).parts), value)):
        with cache.directory(root / name) as fd:
            require(os.fstat(fd).st_uid == os.getuid())
            if apply:
                os.fchmod(fd, 0o555)
                os.fsync(fd)
            require(stat.S_IMODE(os.fstat(fd).st_mode) == 0o555 and time.monotonic() < deadline)
    cache.inventory(root, rows)


def prepare_view(cache, source, destination, rows, deadline):
    require(source != destination and not source.is_relative_to(destination)
            and not destination.is_relative_to(source) and time.monotonic() < deadline)
    publication = cache.load_pinned("scripts/pilot/export.py", cache.PUBLICATION_SHA256)
    # Parent stays private. Only the new child will ever be bind-mounted into
    # the service; no source, cache, key or parent permissions are changed.
    with cache.directory(source) as source_fd, private_view_parent(cache, destination.parent) as parent:
        original_root, parent_info = os.fstat(source_fd), os.fstat(parent)
        original = cache.strict_inventory(source, rows)
        require(not os.path.lexists(destination))
        needed = sum(row[1] for row in rows)
        space = os.fstatvfs(parent)
        require(space.f_bavail * space.f_frsize >= needed + max(10 << 30, (needed + 9) // 10))
        cache.publication_preflight(parent, publication, deadline)
        temporary = destination.parent / (".nib-selected-view-" + uuid.uuid4().hex)
        os.mkdir(temporary.name, 0o700, dir_fd=parent)
        model = temporary / "model"
        copied = cache.copy_pack(source, model, rows, deadline, stable=True)
        require(cache.strict_inventory(source, rows) == original
                and cache.file_identity(os.stat(source, follow_symlinks=False)) == cache.file_identity(original_root)
                and cache.strict_inventory(model, rows) == copied["identities"])
        view_modes(cache, model, rows, deadline, apply=True)
        # Rehash after mode preparation, before publishing. Ordinary host root
        # can still mutate files: runtime must mount the view read-only and
        # independently run the full manifest validator before serving.
        for row in rows:
            cache.check_file(model, row, deadline, stable=True)
        view_modes(cache, model, rows, deadline)
        with cache.directory(temporary) as prepared:
            identity = os.fstat(prepared)
            os.fsync(prepared)
            require(cache.strict_inventory(source, rows) == original)
            require(cache.file_identity(os.stat(source, follow_symlinks=False)) == cache.file_identity(original_root))
            require(cache.file_identity(os.stat(destination.parent, follow_symlinks=False)) ==
                    cache.file_identity(os.fstat(parent)))
            require(os.path.samestat(parent_info, os.fstat(parent))
                    and os.path.samestat(identity, os.stat(temporary.name, dir_fd=parent, follow_symlinks=False))
                    and time.monotonic() < deadline)
            publication.rename_exclusive(parent, temporary.name, destination.name)
            os.fsync(parent)
            require(os.path.samestat(identity, os.stat(destination.name, dir_fd=parent, follow_symlinks=False)))
        view_modes(cache, destination / "model", rows, deadline)
    return {"files": len(rows), "verified_bytes": needed, "file_mode": "0444", "directory_mode": "0555",
            "relative_path": "model"}


def worker(source, destination, seconds, model_profile="uncensored"):
    require(sys.platform == "linux" and os.getuid() == 0 and sys.flags.isolated
            and sys.flags.no_site and sys.dont_write_bytecode)
    seconds = int(seconds)
    require(0 < seconds <= 7200 - RESERVE)
    cache = cache_helper()
    profile, count = selection(cache, model_profile)
    manifest = Path(__file__).absolute().parents[1] / profile["pack"] / "files.sha256"
    rows = cache.manifest_rows(cache.read_regular(manifest), profile["manifest_sha256"],
                               profile["total_bytes"], count)
    result = prepare_view(cache, Path(source), Path(destination), rows, time.monotonic() + seconds)
    print(json.dumps({**identity_fields(profile, worker=True), **result}, sort_keys=True))


def main(argv=None):
    require(sys.platform == "linux" and os.getuid() == 0 and sys.flags.isolated
            and sys.flags.no_site and sys.dont_write_bytecode)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--producer", required=True)
    parser.add_argument("--timeout", required=True, type=int)
    parser.add_argument("--model-profile", choices=("original", "uncensored"), default="uncensored")
    args = parser.parse_args(argv)
    require(re.fullmatch("[0-9a-f]{40}", args.producer) and RESERVE < args.timeout <= 7200)
    paths = (args.source, args.destination, args.output)
    require(all(path.is_absolute() and str(path) == os.path.normpath(path) and ".." not in path.parts for path in paths)
            and all(left != right and not left.is_relative_to(right) and not right.is_relative_to(left)
                    for index, left in enumerate(paths) for right in paths[index + 1:]))
    cache = cache_helper()
    profile, count = selection(cache, args.model_profile)
    source = cache.read_regular(Path(__file__).absolute())
    report = {**identity_fields(profile), "producer": args.producer,
              "script_sha256": hashlib.sha256(source).hexdigest(), "cache_helper_sha256": CACHE_SHA256,
              "manifest_sha256": profile["manifest_sha256"], "result": "fail", "publication": "unproven",
              "source_preserved": None, "confidential": False, "runtime_ready": False,
              "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "timeout_seconds": args.timeout, "worker_exit_confirmed": False}
    with cache.directory(args.output.parent, private=True) as parent:
        fd = os.open(args.output.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    with os.fdopen(fd, "w") as output:
        def persist():
            output.seek(0)
            json.dump(report, output, sort_keys=True)
            output.write("\n")
            output.truncate()
            output.flush()
            os.fsync(output.fileno())
        cancelled = False
        def cancel(*_):
            nonlocal cancelled
            if not cancelled:
                cancelled = True
                raise Cancelled("selected-weight-view-cancelled")
        handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        persist()
        try:
            for sig in handlers:
                signal.signal(sig, cancel)
            raw = cache.load_capture()([sys.executable, "-I", "-S", "-B", "-c", BOOTSTRAP,
                str(Path(__file__).absolute()), source.decode("utf-8"), str(args.source), str(args.destination),
                str(args.timeout - RESERVE)] + (["original"] if args.model_profile == "original" else []),
                timeout=args.timeout - RESERVE, limit=4096)
            expected = {**identity_fields(profile, worker=True), "files": count,
                        "verified_bytes": profile["total_bytes"], "file_mode": "0444", "directory_mode": "0555",
                        "relative_path": "model"}
            result = json.loads(raw, object_pairs_hook=cache.unique)
            require(result == expected and type(result["files"]) is int and type(result["verified_bytes"]) is int)
            report.update(result="pass", publication="atomic-no-replace", worker_exit_confirmed=True, source_preserved=True,
                          **{key: value for key, value in expected.items() if key != "schema"})
        except BaseException:
            # A killed worker may have published before acknowledgment. Retain
            # its directory for inspection; never retry or replace automatically.
            pass
        finally:
            for sig in handlers:
                signal.signal(sig, signal.SIG_IGN)
            try:
                report["ended_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                persist()
            finally:
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit("Selected weight view rejected.")
