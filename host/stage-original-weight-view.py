#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""One explicit exclusive NFS copy of the original public model, never a live mount."""
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
import uuid

INSPECTION_SHA256 = "370db5bdd583122b76809510500a2a0cb5c2a89e3217aae060cea984802a2cdb"
VIEW_SHA256 = "e013ff0617785973c38ec330be85477d8dc0c719cd9a7d2cd7cb877ac0068fb5"
MODE = "nfs-exclusive-copy"
SCHEMA = "nib.original-weight-staging/v1"
WORKER_SCHEMA = "nib.original-weight-staging-worker/v1"
FAILURE_SCHEMA = "nib.original-weight-staging-worker-failure/v1"
STAGES = frozenset({"preflight", "namespace", "mount-targets", "source-mount", "destination-mount",
                    "source-verification", "destination-admission", "copy", "modes", "final-verification",
                    "cleanup", "deadline"})
RESERVE = 60
BOOTSTRAP = """
import sys
scope = {'__name__': 'bound_original_staging', '__file__': sys.argv.pop(1)}
exec(compile(sys.argv.pop(1), '<bound-original-staging>', 'exec'), scope)
scope['worker'](*sys.argv[1:])
"""


def require(value):
    if not value:
        raise ValueError("original-weight-staging-rejected")


def helpers():
    path = Path(__file__).absolute().with_name("inspect-public-weight-cache.py")
    with path.open("rb") as stream:
        raw = stream.read(65537)
    require(len(raw) <= 65536 and hashlib.sha256(raw).hexdigest() == INSPECTION_SHA256)
    inspection = types.ModuleType("bound_original_staging_inspection")
    inspection.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), inspection.__dict__)
    cache = inspection.cache_helper()
    view = cache.load_pinned("host/prepare-selected-weight-view.py", VIEW_SHA256)
    return inspection, cache, view


def record_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def staging_intent(profile, attempt, producer, source_sha, exports, mounts):
    return {"schema": "nib.original-weight-staging-intent/v1", "attempt_id": attempt, "producer": producer,
            "script_sha256": source_sha, "model_profile": profile, "mode": MODE,
            "source_export_sha256": hashlib.sha256(exports[0].encode("ascii")).hexdigest(),
            "destination_export_sha256": hashlib.sha256(exports[1].encode("ascii")).hexdigest(),
            "mount_targets_sha256": hashlib.sha256(record_bytes([str(path) for path in mounts])).hexdigest(),
            "source_relative_path": "deepseek-v4.1-flash-1017",
            "destination_wrapper": ".nib-original-view-" + attempt, "model_relative_path": "model"}


def record_filesystem(cache, path):
    # Only explicitly local disk filesystems may hold durable ownership/evidence.
    value = json.loads(cache.command(["findmnt", "--json", "--target", str(path), "--output", "FSTYPE"]),
                       object_pairs_hook=cache.unique)
    require(type(value) is dict and len(value["filesystems"]) == 1
            and value["filesystems"][0]["fstype"] in ("ext4", "xfs", "btrfs"))


def publish_record(cache, publication, directory, name, value):
    require(name in ("staging-intent.json", "destination-created.json", "completion.json"))
    raw = record_bytes(value)
    require(len(raw) <= 8192)
    temporary = ".staging-record-" + uuid.uuid4().hex
    with cache.directory(directory, private=True) as parent:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, "wb") as stream:
            require(stream.write(raw) == len(raw))
            stream.flush()
            os.fsync(stream.fileno())
        # Reuse the pinned no-replace primitive on local disk only, never on NFS.
        # Failed pending records are retained and never adopted.
        publication.rename_exclusive(parent, temporary, name)
        os.fsync(parent)
    return hashlib.sha256(raw).hexdigest()


def mount_binding(inspection, cache, export, target, readonly):
    cache.verify_mount(export, target, readonly=readonly)
    with cache.directory(target) as fd:
        mount = inspection.mount_id(fd)
        # Also bind the open descriptor and require rw explicitly on the destination.
        found = json.loads(cache.command(["findmnt", "--json", "--id", str(mount),
                           "--output", "SOURCE,TARGET,FSTYPE,OPTIONS"]), object_pairs_hook=cache.unique)
        require(type(found) is dict and len(found["filesystems"]) == 1)
        row = found["filesystems"][0]
        options = set(row["options"].split(","))
        require(row["source"] == export and row["target"] == str(target) and row["fstype"] in ("nfs", "nfs4")
                and {"nodev", "nosuid", "noexec", "ro" if readonly else "rw"} <= options
                and not {"dev", "suid", "exec", "rw" if readonly else "ro"} & options)
        return mount, os.fstat(fd).st_dev


def mode_probe(cache, view, wrapper, deadline):
    probe = wrapper / ".mode-check"
    with cache.directory(wrapper, private=True) as parent:
        os.mkdir(probe.name, 0o700, dir_fd=parent)
    with cache.directory(probe, private=True) as fd:
        child = os.open("empty", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        os.close(child)
        rows = [(hashlib.sha256(b"").hexdigest(), 0, "empty")]
        view.view_modes(cache, probe, rows, deadline, apply=True)
        # Only the successful, newly owned capability probe is removed, never a partial model.
        os.fchmod(fd, 0o700)
        os.unlink("empty", dir_fd=fd)
        os.fsync(fd)
    with cache.directory(wrapper, private=True) as parent:
        os.rmdir(probe.name, dir_fd=parent)
        os.fsync(parent)


def copy_view(inspection, cache, view, source_mount, destination_mount, rows, attempt, deadline, stage, record_created):
    source = source_mount / "deepseek-v4.1-flash-1017"
    wrapper = destination_mount / (".nib-original-view-" + attempt)
    model = wrapper / "model"
    stage[0] = "source-verification"
    with cache.directory(source_mount) as source_volume, cache.directory(source) as original, \
            cache.directory(destination_mount, trusted=True) as volume:
        source_info, volume_info = os.fstat(original), os.fstat(volume)
        source_id, destination_id = inspection.mount_id(original), inspection.mount_id(volume)
        require(source_id == inspection.mount_id(source_volume) and source_id != destination_id)
        initial = cache.strict_inventory(source, rows)
        stage[0] = "destination-admission"
        needed = sum(row[1] for row in rows)
        space = os.fstatvfs(volume)
        require(space.f_bavail * space.f_frsize >= needed + max(10 << 30, (needed + 9) // 10)
                and not os.path.lexists(wrapper) and time.monotonic() < deadline)
        cache.filesystem_probe(volume, deadline)
        os.mkdir(wrapper.name, 0o700, dir_fd=volume)
        os.fsync(volume)
        with cache.directory(wrapper, private=True) as owned:
            identity = os.fstat(owned)
            require(inspection.mount_id(owned) == destination_id)
            created_sha = record_created({"wrapper_device": identity.st_dev, "wrapper_inode": identity.st_ino})
            require(type(created_sha) is str and re.fullmatch(r"[0-9a-f]{64}", created_sha) is not None)
            mode_probe(cache, view, wrapper, deadline)
            stage[0] = "copy"
            copied = cache.upload_copy(source, model, rows, deadline)
            require(copied == {"files": len(rows), "verified_bytes": needed})
            stage[0] = "modes"
            view.view_modes(cache, model, rows, deadline, apply=True)
            stage[0] = "final-verification"
            verified = {row[2]: inspection.hash_file(cache, model, row, deadline, destination_id) for row in rows}
            view.view_modes(cache, model, rows, deadline)
            require(cache.strict_inventory(source, rows) == initial
                    and cache.file_identity(os.stat(source, follow_symlinks=False)) == cache.file_identity(source_info)
                    and inspection.mount_id(original) == source_id
                    and os.path.samestat(identity, os.stat(wrapper.name, dir_fd=volume, follow_symlinks=False))
                    and stat.S_IMODE(os.fstat(owned).st_mode) == 0o700)
            os.fsync(owned)
            with cache.directory(model) as complete:
                model_info = os.fstat(complete)
                require(cache.strict_inventory(model, rows) == verified)
                destination = {"wrapper_device": identity.st_dev, "wrapper_inode": identity.st_ino,
                               "model_device": model_info.st_dev, "model_inode": model_info.st_ino,
                               "created_record_sha256": created_sha,
                               "inventory_sha256": hashlib.sha256(record_bytes(verified)).hexdigest()}
        os.fsync(volume)
        require(os.path.samestat(volume_info, os.stat(destination_mount, follow_symlinks=False)))
    return {**copied, "destination": destination}


def acknowledgment(profile, attempt, intent_sha, destination):
    return {"schema": WORKER_SCHEMA, "model_profile": profile, "attempt_id": attempt, "mode": MODE,
            "files": 83, "verified_bytes": profile["total_bytes"], "file_mode": "0444", "directory_mode": "0555",
            "wrapper_mode": "0700", "relative_path": "model", "source_preserved": True,
            "private_namespace_verified": True, "private_mount_cleanup_confirmed": True,
            "staging_intent_sha256": intent_sha, "destination": destination}


def destination_identity(value):
    require(type(value) is dict and set(value) == {"wrapper_device", "wrapper_inode", "model_device",
            "model_inode", "inventory_sha256", "created_record_sha256"} and all(type(value[k]) is int and value[k] >= 0
            for k in ("wrapper_device", "model_device")) and all(type(value[k]) is int and value[k] > 0
            for k in ("wrapper_inode", "model_inode")) and value["wrapper_device"] == value["model_device"]
            and value["wrapper_inode"] != value["model_inode"] and all(type(value[k]) is str
            and re.fullmatch(r"[0-9a-f]{64}", value[k]) is not None
            for k in ("inventory_sha256", "created_record_sha256")))
    return value


def created_record(intent_sha, wrapper):
    return {"schema": "nib.original-weight-staging-created/v1", "staging_intent_sha256": intent_sha, **wrapper}


def private_stage(inspection, cache, view, exports, mounts, rows, profile, attempt, deadline, namespace, intent_sha,
                  record_created):
    stage, attempted, bindings = ["namespace"], [], {}
    failure, cleanup_error = None, None
    try:
        require(re.fullmatch(r"mnt:\[[0-9]+\]", namespace) is not None
                and os.readlink("/proc/self/ns/mnt") != namespace)
        stage[0] = "mount-targets"
        for mount in mounts:
            with cache.directory(mount, private=True) as fd, os.scandir(fd) as entries:
                require(next(entries, None) is None)
            cache.require_unmounted(mount)
            propagation = json.loads(cache.command(["findmnt", "--json", "--target", str(mount),
                                     "--output", "PROPAGATION"]), object_pairs_hook=cache.unique)
            require(propagation == {"filesystems": [{"propagation": "private"}]})
        for export, mount, readonly in zip(exports, mounts, (True, False)):
            stage[0] = "source-mount" if readonly else "destination-mount"
            attempted.append((export, mount, readonly))
            cache.mount_cache(export, mount, readonly=readonly)
            bindings[mount] = mount_binding(inspection, cache, export, mount, readonly)
            require(time.monotonic() < deadline)
        require(bindings[mounts[0]][0] != bindings[mounts[1]][0]
                and bindings[mounts[0]][1] != bindings[mounts[1]][1])
        copied = copy_view(inspection, cache, view, *mounts, rows, attempt, deadline, stage, record_created)
        destination_identity(copied["destination"])
        for export, mount, readonly in attempted:
            require(mount_binding(inspection, cache, export, mount, readonly) == bindings[mount])
    except Exception as error:
        failure = {"stage": stage[0], "error": inspection.error_code(error)}
    finally:
        # Attempt both owned cleanups even if one is busy or unknown. No force/lazy unmount.
        for export, mount, readonly in reversed(attempted):
            try:
                try:
                    cache.require_unmounted(mount)
                except ValueError:
                    current = mount_binding(inspection, cache, export, mount, readonly)
                    require(mount not in bindings or current == bindings[mount])
                    cache.unmount_cache(export, mount)
            except Exception as error:
                cleanup_error = inspection.error_code(error)
    if failure is None and cleanup_error is not None:
        failure = {"stage": "cleanup", "error": cleanup_error}
    if failure is None and time.monotonic() >= deadline:
        failure = {"stage": "deadline", "error": "rejected"}
    return ({"schema": FAILURE_SCHEMA, **failure, "cleanup_error": cleanup_error} if failure
            else acknowledgment(profile, attempt, intent_sha, copied["destination"]))


def worker(source_export, destination_export, source_mount, destination_mount, attempt, seconds, namespace, intent_sha,
           ownership_dir):
    inspection, cache, view = helpers()
    try:
        require(sys.platform == "linux" and os.getuid() == 0 and sys.flags.isolated
                and sys.flags.no_site and sys.dont_write_bytecode)
        seconds = int(seconds)
        require(0 < seconds <= 7200 - RESERVE and re.fullmatch(r"[0-9a-f]{32}", attempt) is not None
                and re.fullmatch(r"[0-9a-f]{64}", intent_sha) is not None)
        profile, count = view.selection(cache, "original")
        ownership_dir = Path(ownership_dir)
        record_filesystem(cache, ownership_dir)
        with cache.directory(ownership_dir, private=True):
            raw = cache.read_regular(ownership_dir / "staging-intent.json", 8192)
        require(hashlib.sha256(raw).hexdigest() == intent_sha)
        intent = json.loads(raw, object_pairs_hook=cache.unique)
        require(re.fullmatch(r"[0-9a-f]{40}", intent["producer"]) is not None
                and raw == record_bytes(staging_intent(profile, attempt, intent["producer"],
                    hashlib.sha256(cache.read_regular(Path(__file__).absolute())).hexdigest(),
                    (source_export, destination_export), (Path(source_mount), Path(destination_mount)))))
        publication = cache.load_pinned("scripts/pilot/export.py", cache.PUBLICATION_SHA256)
        def record_created(wrapper):
            return publish_record(cache, publication, ownership_dir, "destination-created.json",
                                  created_record(intent_sha, wrapper))
        manifest = Path(__file__).absolute().parents[1] / profile["pack"] / "files.sha256"
        rows = cache.manifest_rows(cache.read_regular(manifest), profile["manifest_sha256"], profile["total_bytes"], count)
        result = private_stage(inspection, cache, view, (source_export, destination_export),
                               (Path(source_mount), Path(destination_mount)), rows, profile, attempt,
                               time.monotonic() + seconds, namespace, intent_sha, record_created)
    except Exception as error:
        result = {"schema": FAILURE_SCHEMA, "stage": "preflight", "error": inspection.error_code(error),
                  "cleanup_error": None}
    print(json.dumps(result, sort_keys=True))


def stage_report(source_export, destination_export, source_mount, destination_mount, output, ownership_dir,
                 attempt, producer, seconds, mode):
    require(mode == MODE and type(seconds) is int and RESERVE < seconds <= 7200
            and re.fullmatch(r"[0-9a-f]{32}", attempt) is not None
            and re.fullmatch(r"[0-9a-f]{40}", producer) is not None and source_export != destination_export)
    paths = (source_mount, destination_mount, output, ownership_dir, Path(__file__).absolute())
    require(all(path.is_absolute() and str(path) == os.path.normpath(path) and ".." not in path.parts for path in paths)
            and all(left != right and not left.is_relative_to(right) and not right.is_relative_to(left)
                    for index, left in enumerate(paths) for right in paths[index + 1:]))
    inspection, cache, view = helpers()
    for export in (source_export, destination_export):
        cache.cache_source(export, "nfs")
    profile, _ = view.selection(cache, "original")
    source = cache.read_regular(Path(__file__).absolute())
    intent = staging_intent(profile, attempt, producer, hashlib.sha256(source).hexdigest(),
                            (source_export, destination_export), (source_mount, destination_mount))
    intent_sha = hashlib.sha256(record_bytes(intent)).hexdigest()
    report = {"schema": SCHEMA, "mode": MODE, "model_profile": profile, "attempt_id": attempt,
              "producer": producer, "script_sha256": hashlib.sha256(source).hexdigest(),
              "inspection_helper_sha256": INSPECTION_SHA256, "view_helper_sha256": VIEW_SHA256,
              "cache_helper_sha256": inspection.CACHE_SHA256, "resource_helper_sha256": inspection.RESOURCE_SHA256,
              "supervisor_sha256": cache.PROBE_SHA256, "profile_helper_sha256": cache.PROFILE_SHA256,
              "publication_helper_sha256": cache.PUBLICATION_SHA256, "staging_intent_sha256": intent_sha,
              "completion_sha256": None,
              "timeout_seconds": seconds, "result": "fail", "failure": None, "publication": "unproven",
              "cleanup": "not-started", "worker_exit_confirmed": False, "files": 0, "verified_bytes": 0,
              "source_preserved": None, "source_written": False, "source_removed": False,
              "partial_removed": False, "host_visible_mount_created": False, "immutable": False,
              "runtime_ready": False, "confidential": False, "private_namespace_verified": False,
              "private_mount_cleanup_confirmed": False, "resources": {"limits_read_back": False,
                  "empty_verified": False, "group_removed": False, "oom_kill": None,
                  "memory_peak_bytes": None, "populated_before_cleanup": None},
              "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "ended_utc": None}
    for path in {output.parent, ownership_dir.parent}:
        record_filesystem(cache, path)
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
                        raise cache.Cancelled("staging-cancelled")
            handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
            persist()
            try:
                for sig in handlers:
                    signal.signal(sig, cancel)
                ownership = inspection.Ownership(cache, ownership_dir, attempt, producer, report["script_sha256"])
                publication = cache.load_pinned("scripts/pilot/export.py", cache.PUBLICATION_SHA256)
                require(publish_record(cache, publication, ownership_dir, "staging-intent.json", intent) == intent_sha)
                capture = cache.load_capture()
                def capture_worker(argv, **kwargs):
                    raw = capture(argv, **kwargs)
                    require(type(raw) is bytes and len(raw) <= 4096)
                    value = json.loads(raw, object_pairs_hook=cache.unique)
                    if type(value) is dict and value.get("schema") == FAILURE_SCHEMA:
                        require(set(value) == {"schema", "stage", "error", "cleanup_error"}
                                and type(value["stage"]) is str and value["stage"] in STAGES
                                and type(value["error"]) is str and value["error"] in inspection.FAILURE_ERRORS
                                and (value["cleanup_error"] is None or (type(value["cleanup_error"]) is str
                                     and value["cleanup_error"] in inspection.FAILURE_ERRORS)))
                        report["worker_failure"] = {k: value[k] for k in ("stage", "error", "cleanup_error")}
                    return raw
                budget = seconds - RESERVE
                deadline = time.monotonic() + budget
                namespace = os.readlink("/proc/self/ns/mnt")
                require(re.fullmatch(r"mnt:\[[0-9]+\]", namespace) is not None)
                argv = ["/usr/bin/unshare", "--mount", "--propagation", "private", "--",
                        sys.executable, "-I", "-S", "-B", "-c", BOOTSTRAP, str(Path(__file__).absolute()),
                        source.decode("utf-8"), source_export, destination_export, str(source_mount),
                        str(destination_mount), attempt, str(budget), namespace, intent_sha, str(ownership_dir)]
                report["cleanup"] = "unproven"
                persist()
                raw = inspection.bounded_capture(cache, capture_worker, argv, budget, report["resources"], ownership)
                require("worker_failure" not in report and time.monotonic() < deadline)
                result = json.loads(raw, object_pairs_hook=cache.unique)
                destination = destination_identity(result["destination"])
                require(record_bytes(result) == record_bytes(acknowledgment(profile, attempt, intent_sha, destination)))
                created = cache.read_regular(ownership_dir / "destination-created.json", 8192)
                require(hashlib.sha256(created).hexdigest() == destination["created_record_sha256"]
                        and created == record_bytes(created_record(intent_sha,
                            {k: destination[k] for k in ("wrapper_device", "wrapper_inode")})))
                completion = {"schema": "nib.original-weight-staging-completion/v1", "staging_intent_sha256": intent_sha,
                              "worker_acknowledgment": result, "resources": report["resources"]}
                report["completion_sha256"] = publish_record(cache, publication, ownership_dir, "completion.json", completion)
                report.update(result="pass", publication="exclusive-directory-copy", cleanup="worker-completed",
                              worker_exit_confirmed=True, files=83, verified_bytes=profile["total_bytes"],
                              source_preserved=True, private_namespace_verified=True, private_mount_cleanup_confirmed=True)
            except (cache.Cancelled, InterruptedError):
                report["failure"] = "cancelled"
            except Exception as error:
                kinds = {"probe-timeout": "timeout", "probe-output-limit": "output-limit",
                         "probe-cleanup-timeout": "cleanup-unproven", "probe-cleanup-failed": "cleanup-unproven"}
                report["failure"] = "worker-failed" if "worker_failure" in report else kinds.get(
                    error.args[0] if error.args and type(error.args[0]) is str else "", "rejected")
            finally:
                for sig in handlers:
                    signal.signal(sig, signal.SIG_IGN)
                try:
                    if cancelled:
                        report["failure"] = "cancelled"
                    if report["failure"] is not None:
                        report.update(result="fail", publication="unproven", cleanup="unproven", files=0,
                                      verified_bytes=0, source_preserved=None, worker_exit_confirmed=False,
                                      private_namespace_verified=False, private_mount_cleanup_confirmed=False)
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
            raise ValueError("original-weight-staging-arguments-rejected")
    parser = QuietParser(description=__doc__)
    parser.add_argument("--mode", choices=(MODE,), required=True)
    for name in ("source-export", "destination-export", "attempt", "producer"):
        parser.add_argument("--" + name, required=True)
    for name in ("source-mount", "destination-mount", "output", "ownership-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--timeout", type=int, required=True)
    args = vars(parser.parse_args(argv))
    args["seconds"] = args.pop("timeout")
    return stage_report(**args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit("Original weight staging rejected.")
