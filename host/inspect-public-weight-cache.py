#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Hash an already read-only-mounted official pack; never mount, copy or serve it."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import time
import types

CACHE_SHA256 = "25141adae8f894a54f4b8f90c65ba26a9d7b3f493121b640b381c4f2a0cb57f6"
RESERVE = 60
BOOTSTRAP = """
import sys
scope = {'__name__': 'bound_cache_inspection', '__file__': sys.argv.pop(1)}
exec(compile(sys.argv.pop(1), '<bound-cache-inspection>', 'exec'), scope)
scope['worker'](*sys.argv[1:])
"""


def require(value):
    if not value:
        raise ValueError("public-cache-inspection-rejected")


def cache_helper():
    path = Path(__file__).absolute().with_name("cache-public-weights.py")
    with path.open("rb") as stream:
        raw = stream.read(65537)
    require(len(raw) <= 65536 and hashlib.sha256(raw).hexdigest() == CACHE_SHA256)
    module = types.ModuleType("bound_inspection_cache")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module


def inspect_pack(cache, export, mount, rows, deadline):
    cache.verify_mount(export, mount, readonly=True)
    root = mount / "deepseek-v4.1-flash-1017"
    with cache.directory(root) as fd:
        before = cache.file_identity(os.fstat(fd))
        initial = cache.strict_inventory(root, rows)
        for row in rows:
            require(cache.check_file(root, row, deadline, stable=True) == initial[row[2]])
        require(cache.strict_inventory(root, rows) == initial
                and cache.file_identity(os.fstat(fd)) == before
                and cache.file_identity(os.stat(root, follow_symlinks=False)) == before)
    cache.verify_mount(export, mount, readonly=True)
    require(time.monotonic() < deadline)
    return {"files": len(rows), "verified_bytes": sum(row[1] for row in rows)}


def worker(export, mount, seconds):
    require(sys.platform == "linux" and os.getuid() == 0 and sys.flags.isolated
            and sys.flags.no_site and sys.dont_write_bytecode)
    seconds = int(seconds)
    require(0 < seconds <= 7200 - RESERVE)
    cache = cache_helper()
    profile, _ = cache.profiles()
    manifest = Path(__file__).absolute().parents[1] / profile["pack"] / "files.sha256"
    rows = cache.manifest(cache.read_regular(manifest))
    result = inspect_pack(cache, export, Path(mount), rows, time.monotonic() + seconds)
    print(json.dumps({"schema": "nib.public-weight-cache-inspection-worker/v1",
                      "model_profile": profile, **result}, sort_keys=True))


def inspect_report(export, mount, output, producer, seconds):
    require(type(seconds) is int and RESERVE < seconds <= 7200
            and re.fullmatch(r"[0-9a-f]{40}", producer) is not None)
    paths = (mount, output, Path(__file__).absolute())
    require(all(path.is_absolute() and str(path) == os.path.normpath(path) and ".." not in path.parts for path in paths)
            and all(left != right and not left.is_relative_to(right) and not right.is_relative_to(left)
                    for index, left in enumerate(paths) for right in paths[index + 1:]))
    cache = cache_helper()
    cache.cache_source(export, "nfs")
    profile, _ = cache.profiles()
    source = cache.read_regular(Path(__file__).absolute())
    report = {"schema": "nib.public-weight-cache-inspection/v1", "model_profile": profile,
              "producer": producer, "script_sha256": hashlib.sha256(source).hexdigest(),
              "cache_helper_sha256": CACHE_SHA256, "supervisor_sha256": cache.PROBE_SHA256,
              "profile_helper_sha256": cache.PROFILE_SHA256, "timeout_seconds": seconds,
              "result": "fail", "failure": None, "cleanup": "not-started",
              "worker_exit_confirmed": False, "process_group_absence_verified": False,
              "files": 0, "verified_bytes": 0, "pack_verified": False,
              "mount_changed": False, "source_written": False, "source_removed": False,
              "immutable": False, "runtime_ready": False, "confidential": False,
              "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "ended_utc": None}
    with cache.directory(output.parent, private=True) as parent:
        fd = os.open(output.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, "w") as stream:
            def persist():
                stream.seek(0)
                json.dump(report, stream, sort_keys=True)
                stream.write("\n")
                stream.truncate()
                stream.flush()
                os.fsync(stream.fileno())
                os.fsync(parent)

            cancelled = False
            def cancel(*_):
                nonlocal cancelled
                if not cancelled:
                    cancelled = True
                    raise cache.Cancelled("inspection-cancelled")

            handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
            persist()
            try:
                for sig in handlers:
                    signal.signal(sig, cancel)
                capture = cache.load_capture()
                report["cleanup"] = "unproven"
                persist()
                budget = seconds - RESERVE
                deadline = time.monotonic() + budget
                raw = capture([sys.executable, "-I", "-S", "-B", "-c", BOOTSTRAP,
                    str(Path(__file__).absolute()), source.decode("utf-8"), export, str(mount), str(budget)],
                    timeout=budget, limit=4096)
                require(time.monotonic() < deadline and type(raw) is bytes and len(raw) <= 4096)
                result = json.loads(raw, object_pairs_hook=cache.unique)
                require(result == {"schema": "nib.public-weight-cache-inspection-worker/v1",
                        "model_profile": profile, "files": 83, "verified_bytes": profile["total_bytes"]}
                        and type(result["files"]) is int and type(result["verified_bytes"]) is int)
                report.update(result="pass", cleanup="worker-completed", worker_exit_confirmed=True,
                              files=83, verified_bytes=profile["total_bytes"], pack_verified=True)
            except (cache.Cancelled, InterruptedError):
                report["failure"] = "cancelled"
            except Exception as error:
                kinds = {"probe-timeout": "timeout", "probe-output-limit": "output-limit",
                         "probe-command-failed": "worker-failed", "probe-cleanup-timeout": "cleanup-unproven",
                         "probe-cleanup-failed": "cleanup-unproven"}
                report["failure"] = kinds.get(error.args[0] if error.args and type(error.args[0]) is str else "", "rejected")
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


def main(argv=None):
    require(sys.platform == "linux" and os.getuid() == 0 and sys.flags.isolated
            and sys.flags.no_site and sys.dont_write_bytecode)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", required=True)
    parser.add_argument("--mount", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--producer", required=True)
    parser.add_argument("--timeout", required=True, type=int)
    args = parser.parse_args(argv)
    return inspect_report(args.export, args.mount, args.output, args.producer, args.timeout)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit("Public cache inspection rejected.")
