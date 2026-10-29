#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Hash an official pack read-only, optionally inside a temporary private mount."""
import argparse
from contextlib import contextmanager, ExitStack
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
import types
import uuid

CACHE_SHA256 = "25141adae8f894a54f4b8f90c65ba26a9d7b3f493121b640b381c4f2a0cb57f6"
RESOURCE_SHA256 = "2b740ef5b1ecbc87cb3a078fcbb081017f7db5c9600c9956f97cc52330ad9fdc"
RESERVE = 60
FAILURE_SCHEMA = "nib.public-weight-cache-inspection-worker-failure/v1"
FAILURE_STAGES = frozenset({
    "worker", "worker-preflight", "cache-helper", "manifest", "namespace-check",
    "mount-target", "mount-propagation", "private-mount", "mount-readback", "pack-open",
    "mount-identity", "inventory-before", "file-hash", "inventory-after",
    "final-mount-readback", "deadline", "private-cleanup",
})
FAILURE_ERRORS = frozenset({
    "not-found", "permission-denied", "os-error", "command-failed", "command-timeout",
    "rejected", "unexpected-error",
})
BOOTSTRAP = """
import sys
scope = {'__name__': 'bound_cache_inspection', '__file__': sys.argv.pop(1)}
exec(compile(sys.argv.pop(1), '<bound-cache-inspection>', 'exec'), scope)
scope['worker'](*sys.argv[1:])
"""
RESOURCE_BOOTSTRAP = """
import json, os, pathlib, sys
name, device, inode, expected = sys.argv[1:5]
root = pathlib.Path('/sys/fs/cgroup') / name
fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    info = os.fstat(fd)
    assert (info.st_dev, info.st_ino) == (int(device), int(inode))
    procs = os.open('cgroup.procs', os.O_WRONLY | os.O_NOFOLLOW, dir_fd=fd)
    try:
        raw = str(os.getpid()).encode('ascii')
        assert os.write(procs, raw) == len(raw)
    finally:
        os.close(procs)
    assert pathlib.Path('/proc/self/cgroup').read_text() == '0::/' + name + '\\n'
    for key, value in json.loads(expected).items():
        child = os.open(key, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            assert os.read(child, 4097).decode('ascii').strip() == value
        finally:
            os.close(child)
finally:
    os.close(fd)
os.execv(sys.argv[5], sys.argv[5:])
"""


def require(value):
    if not value:
        raise ValueError("public-cache-inspection-rejected")


def error_code(error):
    # Exception text, filenames, command arguments and output are never copied.
    for kind, code in ((FileNotFoundError, "not-found"), (PermissionError, "permission-denied"),
                       (OSError, "os-error"), (subprocess.CalledProcessError, "command-failed"),
                       (subprocess.TimeoutExpired, "command-timeout"), (ValueError, "rejected")):
        if isinstance(error, kind):
            return code
    return "unexpected-error"


class StageFailure(ValueError):
    def __init__(self, stage, error):
        require(stage in FAILURE_STAGES and error in FAILURE_ERRORS)
        super().__init__("public-cache-inspection-stage-failed")
        self.stage, self.error, self.cleanup_error = stage, error, None


@contextmanager
def failure_stage(stage):
    require(stage in FAILURE_STAGES)
    try:
        yield
    except StageFailure:
        raise
    except Exception as error:
        raise StageFailure(stage, error_code(error)) from None


def worker_failure(value):
    require(type(value) is dict and set(value) == {"schema", "stage", "error", "cleanup_error"}
            and value["schema"] == FAILURE_SCHEMA
            and type(value["stage"]) is str and value["stage"] in FAILURE_STAGES
            and type(value["error"]) is str and value["error"] in FAILURE_ERRORS
            and (value["cleanup_error"] is None or (type(value["cleanup_error"]) is str
                 and value["cleanup_error"] in FAILURE_ERRORS)))
    return {key: value[key] for key in ("stage", "error", "cleanup_error")}


def cache_helper():
    path = Path(__file__).absolute().with_name("cache-public-weights.py")
    with path.open("rb") as stream:
        raw = stream.read(65537)
    require(len(raw) <= 65536 and hashlib.sha256(raw).hexdigest() == CACHE_SHA256)
    module = types.ModuleType("bound_inspection_cache")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module


def resource_helper(cache):
    path = Path(__file__).absolute().with_name("probe-build-resources.py")
    raw = cache.read_regular(path)
    require(hashlib.sha256(raw).hexdigest() == RESOURCE_SHA256)
    resource = types.ModuleType("bound_inspection_resources")
    resource.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), resource.__dict__)
    return resource


def bounded_capture(cache, capture, argv, budget, state, ownership=None):
    resource = resource_helper(cache)
    state["limits"] = dict(resource.LIMITS)
    # Reuse the existing owned cgroup lifecycle, including detached descendants.
    # No parent controllers are enabled and no existing process is moved.
    with resource.cgroup(state) as (fd, name):
        identity = os.fstat(fd)
        if ownership is not None:
            ownership.group(name, identity, resource.LIMITS)
        wrapped = [sys.executable, "-I", "-S", "-B", "-c", RESOURCE_BOOTSTRAP,
                   name, str(identity.st_dev), str(identity.st_ino),
                   json.dumps(resource.LIMITS, sort_keys=True), *argv]
        result = capture(wrapped, timeout=budget, limit=4096)
    require(all(state[key] is True for key in ("limits_read_back", "empty_verified", "group_removed"))
            and state.get("oom_kill") == 0)
    return result


def supervisor_identity():
    pid = os.getpid()
    with open("/proc/self/stat", "rb") as stream:
        raw = stream.read(8193)
    require(len(raw) <= 8192)
    fields = raw.rsplit(b")", 1)[1].split()
    require(int(raw.split(b" ", 1)[0]) == pid and int(fields[19]) > 0)
    with open("/proc/sys/kernel/random/boot_id", "rb") as stream:
        boot = stream.read(38).decode("ascii").strip()
    require(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", boot) is not None)
    return {"pid": pid, "start_ticks": int(fields[19]), "pgid": int(fields[2]), "boot_id": boot}


class Ownership:
    """Immutable, atomic operator records published before any worker exists."""

    def __init__(self, cache, directory, attempt, producer, source_sha):
        self.cache, self.directory = cache, directory
        self.common = {"schema": "nib.cache-inspection-ownership/v1", "attempt_id": attempt,
                       "producer": producer, "script_sha256": source_sha,
                       "supervisor": supervisor_identity()}
        # An existing directory is never adopted, even if it looks empty.
        with cache.directory(directory.parent, private=True) as parent:
            os.mkdir(directory.name, 0o700, dir_fd=parent)
            os.fsync(parent)
        self.publish("supervisor.json", {**self.common, "kind": "supervisor"})

    def publish(self, name, value):
        raw = (json.dumps(value, sort_keys=True) + "\n").encode("ascii")
        require(len(raw) <= 4096 and name in ("supervisor.json", "group.json"))
        # link(2) atomically publishes a complete, fsynced file without replacing
        # another attempt's record. A crash leaves either no record or full JSON.
        # https://man7.org/linux/man-pages/man2/link.2.html (2026-10-29)
        temporary = ".pending-" + uuid.uuid4().hex
        with self.cache.directory(self.directory, private=True) as parent:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                os.fsync(parent)
            finally:
                os.unlink(temporary, dir_fd=parent)
                os.fsync(parent)

    def group(self, name, identity, limits):
        require(re.fullmatch(r"nib-build-[0-9a-f]{32}", name) is not None)
        self.publish("group.json", {**self.common, "kind": "group", "group": {
            "name": name, "device": identity.st_dev, "inode": identity.st_ino,
            "limits": dict(limits)}})


def mount_id(fd):
    # mnt_id identifies the mount of this open file, including bind mounts.
    # https://man7.org/linux/man-pages/man5/proc_pid_fdinfo.5.html (2026-10-28)
    with open(f"/proc/self/fdinfo/{fd}", "rb") as stream:
        raw = stream.read(4097)
    matches = re.findall(rb"^mnt_id:\s*([0-9]+)$", raw, re.MULTILINE)
    require(len(raw) <= 4096 and len(matches) == 1 and int(matches[0]) > 0)
    return int(matches[0])


def hash_file(cache, root, row, deadline, expected_mount):
    digest, size, name = row
    path = root / name
    with cache.directory(path.parent) as parent:
        require(mount_id(parent) == expected_mount)
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(fd)
            require(mount_id(fd) == expected_mount and stat.S_ISREG(info.st_mode)
                    and info.st_nlink == 1 and info.st_size == size and time.monotonic() < deadline)
            # The existing outer supervisor bounds blocking NFS reads too.
            require(hashlib.file_digest(stream, "sha256").hexdigest() == digest
                    and time.monotonic() < deadline
                    and cache.file_identity(info) == cache.file_identity(os.fstat(fd))
                    == cache.file_identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)))
            return cache.file_identity(info)


def inspect_pack(cache, export, mount, rows, deadline):
    with failure_stage("mount-readback"):
        cache.verify_mount(export, mount, readonly=True)
    root = mount / "deepseek-v4.1-flash-1017"
    with ExitStack() as opened:
        with failure_stage("pack-open"):
            mounted = opened.enter_context(cache.directory(mount))
            fd = opened.enter_context(cache.directory(root))
        with failure_stage("mount-identity"):
            expected_mount = mount_id(mounted)
            # Resolve the open descriptor's mount, not just a second path lookup.
            # https://man7.org/linux/man-pages/man8/findmnt.8.html (--id, 2026-10-28)
            found = json.loads(cache.command(["findmnt", "--json", "--id", str(expected_mount),
                              "--output", "SOURCE,TARGET,FSTYPE,OPTIONS"]), object_pairs_hook=cache.unique)
            require(type(found) is dict and len(found["filesystems"]) == 1)
            fs = found["filesystems"][0]
            require(fs["source"] == export and fs["target"] == str(mount) and fs["fstype"] in ("nfs", "nfs4")
                    and {"ro", "nodev", "nosuid", "noexec"} <= set(fs["options"].split(","))
                    and not {"rw", "dev", "suid", "exec"} & set(fs["options"].split(",")))
            require(mount_id(fd) == expected_mount)
        with failure_stage("inventory-before"):
            before = cache.file_identity(os.fstat(fd))
            initial = cache.strict_inventory(root, rows)
        for row in rows:
            with failure_stage("file-hash"):
                require(hash_file(cache, root, row, deadline, expected_mount) == initial[row[2]])
        with failure_stage("inventory-after"):
            require(cache.strict_inventory(root, rows) == initial
                    and cache.file_identity(os.fstat(fd)) == before
                    and cache.file_identity(os.stat(root, follow_symlinks=False)) == before)
        with failure_stage("final-mount-readback"):
            with cache.directory(mount) as current:
                require(mount_id(current) == expected_mount)
    with failure_stage("final-mount-readback"):
        cache.verify_mount(export, mount, readonly=True)
    with failure_stage("deadline"):
        require(time.monotonic() < deadline)
    return {"files": len(rows), "verified_bytes": sum(row[1] for row in rows)}


def private_inspection(cache, export, mount, rows, deadline, parent_namespace):
    # unshare(1) --mount --propagation private isolates all mount changes.
    # https://man7.org/linux/man-pages/man1/unshare.1.html (2026-10-28)
    with failure_stage("namespace-check"):
        require(re.fullmatch(r"mnt:\[[0-9]+\]", parent_namespace) is not None
                and os.readlink("/proc/self/ns/mnt") != parent_namespace)
    with failure_stage("mount-target"):
        with cache.directory(mount, private=True) as fd:
            with os.scandir(fd) as entries:
                require(next(entries, None) is None)
        cache.require_unmounted(mount)
    with failure_stage("mount-propagation"):
        found = json.loads(cache.command(["findmnt", "--json", "--target", str(mount),
                           "--output", "PROPAGATION"]), object_pairs_hook=cache.unique)
        require(type(found) is dict and found == {"filesystems": [{"propagation": "private"}]}
                and time.monotonic() < deadline)
    # Even a failed mount/readback may have attached the export in this namespace.
    # Never detach a foreign mount or use forced/lazy cleanup.
    primary = None
    try:
        with failure_stage("private-mount"):
            cache.mount_cache(export, mount, readonly=True)
            require(time.monotonic() < deadline)
        result = inspect_pack(cache, export, mount, rows, deadline)
    except Exception as error:
        primary = error if isinstance(error, StageFailure) else StageFailure("worker", error_code(error))
        raise primary from None
    finally:
        try:
            with failure_stage("private-cleanup"):
                try:
                    cache.require_unmounted(mount)
                except ValueError:
                    cache.verify_mount(export, mount, readonly=True)
                    cache.unmount_cache(export, mount)
        except StageFailure as error:
            if primary is not None:
                primary.cleanup_error = error.error
                raise primary from None
            raise
    with failure_stage("deadline"):
        require(time.monotonic() < deadline)
    return {**result, "private_namespace_verified": True, "private_mount_cleanup_confirmed": True}


def worker(export, mount, seconds, parent_namespace=None):
    try:
        with failure_stage("worker-preflight"):
            require(sys.platform == "linux" and os.getuid() == 0 and sys.flags.isolated
                    and sys.flags.no_site and sys.dont_write_bytecode)
            seconds = int(seconds)
            require(0 < seconds <= 7200 - RESERVE)
        with failure_stage("cache-helper"):
            cache = cache_helper()
        with failure_stage("manifest"):
            profile, _ = cache.profiles()
            manifest = Path(__file__).absolute().parents[1] / profile["pack"] / "files.sha256"
            rows = cache.manifest(cache.read_regular(manifest))
        deadline = time.monotonic() + seconds
        result = (inspect_pack(cache, export, Path(mount), rows, deadline) if parent_namespace is None
                  else private_inspection(cache, export, Path(mount), rows, deadline, parent_namespace))
        result = {"schema": "nib.public-weight-cache-inspection-worker/v1", "model_profile": profile, **result}
    except Exception as error:
        if not isinstance(error, StageFailure):
            error = StageFailure("worker", error_code(error))
        result = {"schema": FAILURE_SCHEMA, "stage": error.stage, "error": error.error,
                  "cleanup_error": error.cleanup_error}
        worker_failure(result)
    # A diagnostic acknowledgment exits zero so the unchanged pinned capture
    # returns it. Only the supervisor's exact success acknowledgment can pass.
    print(json.dumps(result, sort_keys=True))


def inspect_report(export, mount, output, producer, seconds, private_mount=False, bounded_resources=False,
                   ownership_dir=None, attempt_id=None):
    require(type(seconds) is int and RESERVE < seconds <= 7200
            and re.fullmatch(r"[0-9a-f]{40}", producer) is not None
            and type(private_mount) is bool and type(bounded_resources) is bool)
    require((ownership_dir is None and attempt_id is None)
            or (isinstance(ownership_dir, Path) and type(attempt_id) is str
                and re.fullmatch(r"[0-9a-f]{32}", attempt_id) is not None and bounded_resources))
    paths = (mount, output, Path(__file__).absolute()) + (() if ownership_dir is None else (ownership_dir,))
    require(all(path.is_absolute() and str(path) == os.path.normpath(path) and ".." not in path.parts for path in paths)
            and all(left != right and not left.is_relative_to(right) and not right.is_relative_to(left)
                    for index, left in enumerate(paths) for right in paths[index + 1:]))
    cache = cache_helper()
    cache.cache_source(export, "nfs")
    profile, _ = cache.profiles()
    source = cache.read_regular(Path(__file__).absolute())
    ownership = (None if ownership_dir is None else Ownership(
        cache, ownership_dir, attempt_id, producer, hashlib.sha256(source).hexdigest()))
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
    if private_mount:
        report.update(schema="nib.public-weight-cache-inspection/v2", private_mount_requested=True,
                      private_namespace_verified=False, private_mount_cleanup_confirmed=False)
    if bounded_resources:
        report.update(schema="nib.public-weight-cache-inspection/v3", bounded_resources_requested=True,
                      resource_helper_sha256=RESOURCE_SHA256,
                      resources={"limits_read_back": False, "empty_verified": False, "group_removed": False,
                                 "oom_kill": None, "memory_peak_bytes": None, "populated_before_cleanup": None})
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
                    if report["result"] != "pass":
                        raise cache.Cancelled("inspection-cancelled")

            handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
            persist()
            try:
                for sig in handlers:
                    signal.signal(sig, cancel)
                capture = cache.load_capture()
                def capture_worker(argv, **kwargs):
                    raw = capture(argv, **kwargs)
                    require(type(raw) is bytes and len(raw) <= 4096)
                    value = json.loads(raw, object_pairs_hook=cache.unique)
                    if type(value) is dict and value.get("schema") == FAILURE_SCHEMA:
                        # Preserve known worker failure even if cgroup cleanup
                        # subsequently fails. This never acknowledges cleanup.
                        report["worker_failure"] = worker_failure(value)
                    return raw

                report["cleanup"] = "unproven"
                persist()
                budget = seconds - RESERVE
                deadline = time.monotonic() + budget
                argv = [sys.executable, "-I", "-S", "-B", "-c", BOOTSTRAP,
                        str(Path(__file__).absolute()), source.decode("utf-8"), export, str(mount), str(budget)]
                private_ack = {}
                if private_mount:
                    namespace = os.readlink("/proc/self/ns/mnt")
                    require(re.fullmatch(r"mnt:\[[0-9]+\]", namespace) is not None)
                    argv = ["/usr/bin/unshare", "--mount", "--propagation", "private", "--", *argv, namespace]
                    private_ack = {"private_namespace_verified": True, "private_mount_cleanup_confirmed": True}
                raw = (bounded_capture(cache, capture_worker, argv, budget, report["resources"], ownership) if bounded_resources
                       else capture_worker(argv, timeout=budget, limit=4096))
                if "worker_failure" in report:
                    raise ValueError("probe-command-failed")
                require(time.monotonic() < deadline and type(raw) is bytes and len(raw) <= 4096)
                result = json.loads(raw, object_pairs_hook=cache.unique)
                require(result == {"schema": "nib.public-weight-cache-inspection-worker/v1",
                        "model_profile": profile, "files": 83, "verified_bytes": profile["total_bytes"], **private_ack}
                        and type(result["files"]) is int and type(result["verified_bytes"]) is int)
                require(all(result[key] is True for key in private_ack))
                report.update(result="pass", cleanup="worker-completed", worker_exit_confirmed=True,
                              files=83, verified_bytes=profile["total_bytes"], pack_verified=True, **private_ack)
            except (cache.Cancelled, InterruptedError):
                report.update(result="fail", failure="cancelled", pack_verified=False,
                              files=0, verified_bytes=0, worker_exit_confirmed=False, cleanup="unproven")
            except Exception as error:
                kinds = {"probe-timeout": "timeout", "probe-output-limit": "output-limit",
                         "probe-command-failed": "worker-failed", "probe-cleanup-timeout": "cleanup-unproven",
                         "probe-cleanup-failed": "cleanup-unproven"}
                report.update(result="fail", pack_verified=False, files=0, verified_bytes=0,
                              worker_exit_confirmed=False, cleanup="unproven", failure=kinds.get(
                                  error.args[0] if error.args and type(error.args[0]) is str else "", "rejected"))
            finally:
                for sig in handlers:
                    signal.signal(sig, signal.SIG_IGN)
                try:
                    if cancelled:
                        report.update(result="fail", failure="cancelled", pack_verified=False,
                                      files=0, verified_bytes=0, worker_exit_confirmed=False, cleanup="unproven")
                        if private_mount:
                            report.update(private_namespace_verified=False, private_mount_cleanup_confirmed=False)
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
            raise ValueError("public-cache-inspection-arguments-rejected")
    parser = QuietParser(description=__doc__)
    parser.add_argument("--export", required=True)
    parser.add_argument("--mount", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--producer", required=True)
    parser.add_argument("--timeout", required=True, type=int)
    parser.add_argument("--private-mount", action="store_true")
    parser.add_argument("--bounded-resources", action="store_true")
    parser.add_argument("--ownership-dir", type=Path)
    parser.add_argument("--attempt-id")
    args = parser.parse_args(argv)
    return inspect_report(args.export, args.mount, args.output, args.producer, args.timeout,
                          args.private_mount, args.bounded_resources, args.ownership_dir, args.attempt_id)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit("Public cache inspection rejected.")
