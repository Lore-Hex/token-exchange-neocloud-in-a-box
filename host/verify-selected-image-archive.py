#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify one source-bound original image archive offline; never load or run it."""

import argparse
from contextlib import contextmanager
import datetime
import gzip
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

ROOT = Path(__file__).absolute().parents[1]
SELF = "host/verify-selected-image-archive.py"
EXPORT = "scripts/pilot/export.py"
EXPORT_SHA = "baaf98e34904b080513fdd468e83e1eb818c2e2ce69d87cf6fb6a421e7268666"
IMAGE = "scripts/selected_service_image.py"
IMAGE_SHA = "875074e48e88929c8926cc8b5dce5fa819d9ee3f42372f01a19c395d6602771e"
RUNNER = "scripts/e2e/snapshot.py"
RUNNER_SHA = "e73a5a1e3ddb7a6e7b20afce39f7c080a0317d78340f224b2bd95f705bccb59c"
SCHEMA = "nib.selected-image-archive-verification/v1"
PROFILE = "original-long1m-graphs"
MAX_ARCHIVE, MAX_EXPANDED = 10 << 30, 64 << 30
SECONDS, MEMORY, MAX_REPORT = 1800, 2 << 30, 65536
HELPERS = {EXPORT: EXPORT_SHA, IMAGE: IMAGE_SHA, RUNNER: RUNNER_SHA}
LIMITS = {"archive_bytes": MAX_ARCHIVE, "expanded_bytes": MAX_EXPANDED,
          "wall_seconds": SECONDS, "cpu_seconds": SECONDS,
          "address_space_bytes": MEMORY, "open_files": 128, "file_bytes": 1 << 20,
          "json_bytes": 1 << 20, "members": 4096, "layers": 128}


def require(value):
    if not value:
        raise ValueError("selected-image-archive-rejected")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"


def strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result
    def invalid(_):
        raise ValueError("selected-image-archive-rejected")
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def directory(path, *, private=False):
    path = Path(path)
    require(path.is_absolute() and str(path) == os.path.normpath(path) and ".." not in path.parts)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        if private:
            info = os.fstat(fd)
            require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def regular(path, *, private=False):
    path = Path(path)
    parent = directory(path.parent, private=private)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb", buffering=0) as stream:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1)
            if private:
                require(before.st_uid == os.getuid() and stat.S_IMODE(before.st_mode) == 0o600)
            yield stream, before
            require(identity(before) == identity(os.fstat(fd))
                    == identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)))
    finally:
        os.close(parent)


def read(path, limit, *, private=False):
    with regular(path, private=private) as (stream, info):
        require(0 < info.st_size <= limit)
        raw = stream.read(limit + 1)
        require(len(raw) == info.st_size)
        return raw


def load_pinned(relative, expected):
    raw = read(ROOT / relative, 1 << 20)
    require(sha(raw) == expected)
    module = types.ModuleType("selected_archive_" + Path(relative).stem)
    module.__file__ = str(ROOT / relative)
    # Never install this instance in sys.modules or modify another caller's helper.
    exec(compile(raw, module.__file__, "exec", dont_inherit=True), module.__dict__)
    return module


def source_inputs(args):
    image = load_pinned(IMAGE, IMAGE_SHA)
    fd = directory(args.preparation, private=True)
    os.close(fd)
    preparation, files, contract = image.validated_files(args.preparation, args.preparation_sha256)
    require(contract["execution_profile"] == PROFILE)
    models = image.owner.service.models
    model = models.validate_contract(contract)
    require(model == models.select("original"))
    models.validate_manifest("original", files["context/payload/pack/files.sha256"])
    build = image.validate_build_record(read(args.build_report, 16384, private=True),
        args.build_sha256, preparation, args.preparation_sha256, contract)
    return build, {"preparation_sha256": args.preparation_sha256,
        "build_sha256": args.build_sha256, "prepared_sha256": build["prepared_sha256"],
        "build_producer": preparation["git_sha"], "lab_receipt_sha256": preparation["lab_receipt_sha256"],
        "execution_profile": PROFILE, "model_profile": model}


def verify_archive(stream, expected_sha, expected_bytes, build, deadline):
    helper = load_pinned(EXPORT, EXPORT_SHA)
    helper.MAX_ARCHIVE, helper.MAX_EXPANDED, helper.TIMEOUT = MAX_ARCHIVE, MAX_EXPANDED, SECONDS
    original_digest = helper.digest
    layers, configs = [], []
    first = True

    def digest(data, limit, until):
        nonlocal first
        value = original_digest(data, limit, min(until, deadline))
        require(time.monotonic() < deadline)
        if first:
            # The existing parser hashes the whole archive before reading its graph.
            require(data is stream and value == {"sha256": expected_sha, "bytes": expected_bytes})
            first = False
        if isinstance(data, gzip.GzipFile):
            layers.append(value)
        return value

    def json_load(data):
        value = strict_json(data.read((1 << 20) + 1))
        if type(value) is dict and value.get("os") == "linux" and value.get("architecture") == "amd64":
            configs.append(value)
        return value

    # Observe the pinned parser's hashing results, not a second OCI/layer parser.
    # This closed profile accepts gzip layers only; Qwen still supports its old formats.
    helper.digest = digest
    helper.json = types.SimpleNamespace(load=json_load)
    result = helper.validate_archive(stream, build["image_id"])
    require(not first and len(configs) == 1 and len(layers) == result["layers"]
            and result["image_id"] != result["config_id"])
    config = configs[0]["config"]
    require(config.get("User") == "65532:65532" and config.get("WorkingDir") == "/"
            and config.get("Entrypoint") == ["/usr/bin/python3.12", "-I", "-S", "-B",
                "/opt/nib-service/scripts/selected_service_process.py", "--prepared-sha256", build["prepared_sha256"]]
            and not any(config.get(key) for key in ("Cmd", "Volumes", "ExposedPorts", "OnBuild", "Shell"))
            and config.get("Healthcheck") == {"Test": ["NONE"]})
    diff_ids = ["sha256:" + row["sha256"] for row in layers]
    require(diff_ids == configs[0]["rootfs"]["diff_ids"] and time.monotonic() < deadline)
    return {**result, "expanded_bytes": sum(row["bytes"] for row in layers), "diff_ids": diff_ids}


def verify(args):
    deadline = time.monotonic() + SECONDS
    build, source = source_inputs(args)
    with regular(args.archive, private=True) as (stream, info):
        require(info.st_size == args.archive_bytes and 0 < info.st_size <= MAX_ARCHIVE)
        archive = verify_archive(stream, args.archive_sha256, args.archive_bytes, build, deadline)
    require(time.monotonic() < deadline)
    return {"source": source, "archive": archive}


class Cancelled(BaseException):
    pass


def interrupted(*_):
    raise Cancelled()


def worker(args):
    try:
        import resource
        require(sys.platform == "linux")
        for name, limit in (("RLIMIT_AS", MEMORY), ("RLIMIT_CPU", SECONDS), ("RLIMIT_CORE", 0),
                            ("RLIMIT_NOFILE", 128), ("RLIMIT_FSIZE", 1 << 20)):
            key = getattr(resource, name)
            resource.setrlimit(key, (limit, limit))
            require(resource.getrlimit(key) == (limit, limit))
        for sig in (signal.SIGALRM, signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, interrupted)
        signal.setitimer(signal.ITIMER_REAL, SECONDS)
        result = {"result": "pass", **verify(args)}
        raw = encoded(result)
        require(len(raw) <= MAX_REPORT)
        sys.stdout.buffer.write(raw)
        return 0
    except BaseException:
        # Neither exception text nor operator paths enter diagnostics or receipts.
        print('{"result":"fail"}')
        return 1


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def publish(parent, name, value):
    raw = encoded(value)
    require(len(raw) <= MAX_REPORT)
    temporary = ".archive-receipt-" + uuid.uuid4().hex
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            require(stream.write(raw) == len(raw))
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
    finally:
        os.unlink(temporary, dir_fd=parent)
    os.fsync(parent)


def run(args):
    require(sys.platform == "linux" and args.output != args.archive and args.output != args.build_report
            and not args.output.is_relative_to(args.preparation))
    parent = directory(args.output.parent, private=True)
    try:
        # A durable exclusive claim also prevents automatic replay after a crash.
        require(not os.path.lexists(args.output))
        claim = os.open(args.output.name + ".claim", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600, dir_fd=parent)
        os.close(claim)
        os.fsync(parent)
        report = {"schema": SCHEMA, "result": "fail", "producer": args.producer,
            "script_sha256": sha(read(ROOT / SELF, 1 << 20)), "helpers": HELPERS,
            "profile": PROFILE, "limits": LIMITS, "started_utc": utc(),
            "expected": {"archive_sha256": args.archive_sha256, "archive_bytes": args.archive_bytes,
                         "preparation_sha256": args.preparation_sha256, "build_sha256": args.build_sha256},
            "archive_verified": False, "worker_exit_confirmed": False,
            "image_loaded": False, "runtime_started": False, "deployable": False,
            "hardware_validated": False, "published": False, "confidential": False}
        handlers = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            runner = load_pinned(RUNNER, RUNNER_SHA)
            command = [sys.executable, "-I", "-S", "-B", str(ROOT / SELF), "--worker",
                "--archive", str(args.archive), "--archive-sha256", args.archive_sha256,
                "--archive-bytes", str(args.archive_bytes), "--preparation", str(args.preparation),
                "--preparation-sha256", args.preparation_sha256, "--build-report", str(args.build_report),
                "--build-sha256", args.build_sha256, "--producer", args.producer]
            result = runner.execute_process_group(command,
                env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C"},
                timeout=SECONDS + 5, max_output_bytes=MAX_REPORT, max_stderr_bytes=4096)
            report["worker_exit_confirmed"] = True
            value = strict_json(result.stdout)
            require(result.returncode == 0 and type(value) is dict
                    and set(value) == {"result", "source", "archive"} and value["result"] == "pass")
            report.update(result="pass", archive_verified=True, source=value["source"], archive=value["archive"])
        except BaseException:
            report["failure"] = "verification-failed-or-incomplete"
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
        report["ended_utc"] = utc()
        publish(parent, args.output.name, report)
        print(json.dumps({"result": report["result"], "archive_verified": report["archive_verified"]}))
        return 0 if report["result"] == "pass" else 1
    finally:
        os.close(parent)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--archive-bytes", type=int, required=True)
    parser.add_argument("--preparation", type=Path, required=True)
    parser.add_argument("--preparation-sha256", required=True)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--build-sha256", required=True)
    parser.add_argument("--producer", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode)
        require(re.fullmatch(r"[0-9a-f]{40}", args.producer)
                and all(re.fullmatch(r"[0-9a-f]{64}", value) for value in
                        (args.archive_sha256, args.preparation_sha256, args.build_sha256))
                and 0 < args.archive_bytes <= MAX_ARCHIVE and (args.worker or args.output is not None))
        if args.worker:
            return worker(args)
        return run(args)
    except BaseException:
        print('{"result":"fail","archive_verified":false}')
        return 1


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "scripts"))
    raise SystemExit(main())
