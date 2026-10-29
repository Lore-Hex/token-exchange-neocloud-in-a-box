#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Observe one private read-only NFS mount; never approve storage admission."""
import argparse
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

INSPECTION_SHA256 = "370db5bdd583122b76809510500a2a0cb5c2a89e3217aae060cea984802a2cdb"
SCHEMA = "nib.storage-metadata-observation/v1"
WORKER_SCHEMA = "nib.storage-metadata-worker/v1"
SECONDS, RESERVE = 180, 60
BOOTSTRAP = """
import sys
scope = {'__name__': 'bound_storage_metadata', '__file__': sys.argv.pop(1)}
exec(compile(sys.argv.pop(1), '<bound-storage-metadata>', 'exec'), scope)
scope['worker'](*sys.argv[1:])
"""


def require(value):
    if not value:
        raise ValueError("storage-metadata-rejected")


def inspection_helper():
    path = Path(__file__).absolute().with_name("inspect-public-weight-cache.py")
    with path.open("rb") as stream:
        raw = stream.read(65537)
    require(len(raw) <= 65536 and hashlib.sha256(raw).hexdigest() == INSPECTION_SHA256)
    module = types.ModuleType("bound_storage_inspection")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module


def natural(value, *, positive=False):
    require(type(value) is int and (1 if positive else 0) <= value < 1 << 63)
    return value


def path_argument(path):
    require(isinstance(path, Path) and path.is_absolute() and str(path) == os.path.normpath(path)
            and ".." not in path.parts and path != Path("/")
            and re.fullmatch(r"/[A-Za-z0-9_./-]{1,510}", str(path)) is not None)


def mount_row(cache, export, mount, identifier):
    natural(identifier, positive=True)
    raw = cache.command(["findmnt", "--json", "--id", str(identifier),
                         "--output", "SOURCE,TARGET,FSTYPE,OPTIONS"])
    require(type(raw) is bytes and len(raw) <= 8192)
    value = json.loads(raw, object_pairs_hook=cache.unique)
    require(type(value) is dict and set(value) == {"filesystems"}
            and type(value["filesystems"]) is list and len(value["filesystems"]) == 1)
    row = value["filesystems"][0]
    require(type(row) is dict and set(row) == {"source", "target", "fstype", "options"}
            and row["source"] == export and row["target"] == str(mount)
            and row["fstype"] in ("nfs", "nfs4") and type(row["options"]) is str
            and len(row["options"]) <= 1024)
    options = row["options"].split(",")
    require(0 < len(options) <= 128 and len(options) == len(set(options))
            and all(re.fullmatch(r"[A-Za-z0-9_.:/=\[\]-]{1,256}", item) is not None for item in options)
            and {"ro", "nodev", "nosuid", "noexec"} <= set(options)
            and not {"rw", "dev", "suid", "exec"} & set(options))
    return {"source": export, "target": str(mount), "fstype": row["fstype"],
            "options": sorted(options), "mount_id": natural(identifier, positive=True)}


def directory_identity(info):
    require(stat.S_ISDIR(info.st_mode))
    return {"device": natural(info.st_dev), "inode": natural(info.st_ino, positive=True),
            "uid": natural(info.st_uid), "gid": natural(info.st_gid),
            "mode": stat.S_IMODE(info.st_mode)}


def observe(inspection, cache, export, mount, binding, deadline):
    with cache.directory(mount) as fd:
        require(inspection.mount_id(fd) == binding["mount_id"])
        before = directory_identity(os.fstat(fd))
        require((before["device"], before["inode"]) == (binding["device"], binding["inode"]))
        mounted = mount_row(cache, export, mount, binding["mount_id"])
        space = os.fstatvfs(fd)
        available = natural(space.f_bavail) * natural(space.f_frsize, positive=True)
        capacity = {"available_bytes": natural(available), "available_inodes": natural(space.f_favail)}
        require(before == directory_identity(os.fstat(fd))
                == directory_identity(os.stat(mount, follow_symlinks=False))
                and inspection.mount_id(fd) == binding["mount_id"]
                and mount_row(cache, export, mount, binding["mount_id"]) == mounted
                and time.monotonic() < deadline)
        with cache.directory(mount) as current:
            require(inspection.mount_id(current) == binding["mount_id"]
                    and directory_identity(os.fstat(current)) == before)
    return {"mount": mounted, "directory": before, "capacity": capacity}


def private_observation(inspection, cache, export, mount, deadline, parent_namespace, state):
    namespace = os.readlink("/proc/self/ns/mnt")
    require(re.fullmatch(r"mnt:\[[0-9]+\]", parent_namespace) is not None
            and re.fullmatch(r"mnt:\[[0-9]+\]", namespace) is not None and namespace != parent_namespace)
    with cache.directory(mount, private=True) as fd:
        with os.scandir(fd) as entries:
            require(next(entries, None) is None)
    cache.require_unmounted(mount)
    raw = cache.command(["findmnt", "--json", "--target", str(mount), "--output", "PROPAGATION"])
    require(type(raw) is bytes and len(raw) <= 4096
            and json.loads(raw, object_pairs_hook=cache.unique) == {"filesystems": [{"propagation": "private"}]}
            and time.monotonic() < deadline)
    state["private_namespace_verified"] = True
    binding = None
    try:
        state["private_mount_attempted"] = True
        cache.mount_cache(export, mount, readonly=True)
        with cache.directory(mount) as fd:
            identifier = inspection.mount_id(fd)
            mount_row(cache, export, mount, identifier)
            binding = {"mount_id": identifier, **directory_identity(os.fstat(fd))}
        require(time.monotonic() < deadline)
        return observe(inspection, cache, export, mount, binding, deadline)
    finally:
        # A failed mount/readback without a bound identity is never adopted for
        # cleanup. The independent owner must still account for the namespace.
        if binding is not None:
            with cache.directory(mount) as fd:
                require(inspection.mount_id(fd) == binding["mount_id"]
                        and directory_identity(os.fstat(fd)) == {key: binding[key] for key in
                            ("device", "inode", "uid", "gid", "mode")})
                mount_row(cache, export, mount, binding["mount_id"])
            cache.unmount_cache(export, mount)
            cache.require_unmounted(mount)
            state["private_mount_cleanup_confirmed"] = True


def worker(export, mount, parent_namespace):
    inspection = inspection_helper()
    state = {"private_namespace_verified": False, "private_mount_attempted": False,
             "private_mount_cleanup_confirmed": False}
    result = {"schema": WORKER_SCHEMA, "result": "fail", "error": None, "observation": None}
    try:
        require(sys.platform == "linux" and os.getuid() == 0 and sys.flags.isolated
                and sys.flags.no_site and sys.dont_write_bytecode)
        cache = inspection.cache_helper()
        cache.cache_source(export, "nfs")
        require(len(export) <= 256)
        path_argument(Path(mount))
        deadline = time.monotonic() + SECONDS - RESERVE
        result["observation"] = private_observation(
            inspection, cache, export, Path(mount), deadline, parent_namespace, state)
        require(time.monotonic() < deadline)
        result["result"] = "pass"
    except Exception as error:
        result.update(error=inspection.error_code(error), observation=None)
    raw = json.dumps({**result, **state}, sort_keys=True)
    require(len(raw.encode("ascii")) <= 4096)
    print(raw)


def acknowledgement(raw, cache, export, mount):
    require(type(raw) is bytes and len(raw) <= 4096)
    value = json.loads(raw, object_pairs_hook=cache.unique)
    require(type(value) is dict and set(value) == {"schema", "result", "error", "observation",
            "private_namespace_verified", "private_mount_attempted", "private_mount_cleanup_confirmed"}
            and value["schema"] == WORKER_SCHEMA and value["result"] in ("pass", "fail")
            and all(type(value[key]) is bool for key in
                ("private_namespace_verified", "private_mount_attempted", "private_mount_cleanup_confirmed")))
    require((not value["private_mount_attempted"] or value["private_namespace_verified"])
            and (not value["private_mount_cleanup_confirmed"] or value["private_mount_attempted"]))
    if value["result"] == "fail":
        require(value["observation"] is None and value["error"] in {
            "not-found", "permission-denied", "os-error", "command-failed", "command-timeout", "rejected", "unexpected-error"})
        return value
    require(value["error"] is None and all(value[key] is True for key in
            ("private_namespace_verified", "private_mount_attempted", "private_mount_cleanup_confirmed")))
    observation = value["observation"]
    require(type(observation) is dict and set(observation) == {"mount", "directory", "capacity"})
    mounted, directory, capacity = (observation[key] for key in ("mount", "directory", "capacity"))
    require(type(mounted) is dict and set(mounted) == {"source", "target", "fstype", "options", "mount_id"}
            and mounted["source"] == export and mounted["target"] == str(mount)
            and mounted["fstype"] in ("nfs", "nfs4") and type(mounted["options"]) is list
            and 0 < len(mounted["options"]) <= 128
            and all(type(item) is str and re.fullmatch(r"[A-Za-z0-9_.:/=\[\]-]{1,256}", item)
                    for item in mounted["options"])
            and mounted["options"] == sorted(set(mounted["options"]))
            and len(",".join(mounted["options"])) <= 1024
            and {"ro", "nodev", "nosuid", "noexec"} <= set(mounted["options"])
            and not {"rw", "dev", "suid", "exec"} & set(mounted["options"]))
    natural(mounted["mount_id"], positive=True)
    require(type(directory) is dict and set(directory) == {"device", "inode", "uid", "gid", "mode"}
            and type(capacity) is dict and set(capacity) == {"available_bytes", "available_inodes"})
    for key, number in {**directory, **capacity}.items():
        natural(number, positive=key == "inode")
    require(directory["mode"] <= 0o7777)
    return value


def inspect_report(export, mount, output, ownership_dir, attempt_id, producer):
    require(type(export) is str and len(export) <= 256 and type(attempt_id) is str
            and re.fullmatch(r"[0-9a-f]{32}", attempt_id) is not None
            and type(producer) is str and re.fullmatch(r"[0-9a-f]{40}", producer) is not None)
    paths = (mount, output, ownership_dir, Path(__file__).absolute())
    for path in paths:
        path_argument(path)
    require(all(left != right and not left.is_relative_to(right) and not right.is_relative_to(left)
                for index, left in enumerate(paths) for right in paths[index + 1:]))
    inspection = inspection_helper()
    cache = inspection.cache_helper()
    cache.cache_source(export, "nfs")
    source = cache.read_regular(Path(__file__).absolute())
    state = {"limits_read_back": False, "empty_verified": False, "group_removed": False,
             "oom_kill": None, "memory_peak_bytes": None, "populated_before_cleanup": None}
    report = {"schema": SCHEMA, "producer": producer, "attempt_id": attempt_id,
              "script_sha256": hashlib.sha256(source).hexdigest(), "inspection_helper_sha256": INSPECTION_SHA256,
              "cache_helper_sha256": inspection.CACHE_SHA256, "resource_helper_sha256": inspection.RESOURCE_SHA256,
              "supervisor_sha256": cache.PROBE_SHA256, "timeout_seconds": SECONDS,
              "worker_timeout_seconds": SECONDS - RESERVE, "result": "fail", "failure": None,
              "observation": None, "cleanup": "not-started", "worker_exit_confirmed": False,
              "private_namespace_verified": False, "private_mount_attempted": False,
              "private_mount_cleanup_confirmed": False, "resources": state,
              "process_group_absence_verified": False, "quota": "unknown", "backing": "unknown",
              "all_client_aliases": "unknown", "write_capability": "unverified",
              "admission_authorized": False, "pack_verified": False, "runtime_ready": False,
              "confidential": False, "host_mount_mutation_requested": False,
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
                    raise cache.Cancelled("storage-metadata-cancelled")

            handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
            persist()
            try:
                for sig in handlers:
                    signal.signal(sig, cancel)
                ownership = inspection.Ownership(cache, ownership_dir, attempt_id, producer, report["script_sha256"])
                namespace = os.readlink("/proc/self/ns/mnt")
                require(re.fullmatch(r"mnt:\[[0-9]+\]", namespace) is not None)
                report["cleanup"] = "unproven"
                persist()
                argv = ["/usr/bin/unshare", "--mount", "--propagation", "private", "--",
                        sys.executable, "-I", "-S", "-B", "-c", BOOTSTRAP,
                        str(Path(__file__).absolute()), source.decode("utf-8"), export, str(mount), namespace]
                deadline = time.monotonic() + SECONDS - RESERVE
                raw = inspection.bounded_capture(cache, cache.load_capture(), argv, SECONDS - RESERVE, state, ownership)
                value = acknowledgement(raw, cache, export, mount)
                require(time.monotonic() < deadline)
                report.update({key: value[key] for key in ("observation", "private_namespace_verified",
                              "private_mount_attempted", "private_mount_cleanup_confirmed")})
                report.update(result=value["result"], failure=value["error"], worker_exit_confirmed=True,
                              cleanup="confirmed" if value["private_mount_cleanup_confirmed"] else "unproven")
            except (cache.Cancelled, InterruptedError):
                report["failure"] = "cancelled"
            except Exception as error:
                kinds = {"probe-timeout": "timeout", "probe-output-limit": "output-limit",
                         "probe-command-failed": "worker-failed", "probe-cleanup-timeout": "cleanup-unproven",
                         "probe-cleanup-failed": "cleanup-unproven"}
                key = error.args[0] if error.args and type(error.args[0]) is str else ""
                report["failure"] = kinds.get(key, inspection.error_code(error))
            finally:
                for sig in handlers:
                    signal.signal(sig, signal.SIG_IGN)
                try:
                    if cancelled:
                        report.update(result="fail", failure="cancelled", observation=None,
                                      worker_exit_confirmed=False, cleanup="unproven",
                                      private_mount_cleanup_confirmed=False)
                    report["ended_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    persist()
                finally:
                    for sig, handler in handlers.items():
                        signal.signal(sig, handler)
    return 0 if report["result"] == "pass" else 1


def main(argv=None):
    require(sys.platform == "linux" and os.getuid() == 0 and sys.flags.isolated
            and sys.flags.no_site and sys.dont_write_bytecode)
    class QuietParser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError("storage-metadata-arguments-rejected")
    parser = QuietParser(description=__doc__, allow_abbrev=False)
    for name in ("export", "producer", "attempt-id"):
        parser.add_argument("--" + name, required=True)
    for name in ("mount", "output", "ownership-dir"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args(argv)
    return inspect_report(args.export, args.mount, args.output, args.ownership_dir, args.attempt_id, args.producer)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit("Storage metadata inspection rejected.")
