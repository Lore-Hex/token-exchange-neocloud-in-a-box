#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify one source-bound original image archive offline; never load or run it."""

import argparse
import builtins
from contextlib import contextmanager
import datetime
import gzip
import hashlib
import importlib
from importlib.machinery import ModuleSpec
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
IMAGE_SHA = "73dfa32d1a13a54c19cb4484d63d7247c7c05b34cf746297e49745bbab2ce6d1"
RUNNER = "scripts/e2e/snapshot.py"
RUNNER_SHA = "e73a5a1e3ddb7a6e7b20afce39f7c080a0317d78340f224b2bd95f705bccb59c"
SCHEMA = "nib.selected-image-archive-verification/v1"
PROFILE = "original-long1m-graphs"
MAX_ARCHIVE, MAX_EXPANDED = 10 << 30, 64 << 30
SECONDS, MEMORY, MAX_REPORT = 1800, 2 << 30, 65536
PREPARATION_MODULES = {
    "deepseek_model_profile": "aba5b55574e6b1c959b4e2c017973b6ec0025d3a0081a0427a00bdc5a9f37c5f",
    "e2e.exercise": "f3af954884134fbd34a94bc001bfb9d7c9c5151c923e9b81571b678251efae6c",
    "e2e.snapshot": RUNNER_SHA,
    "engine_lab": "0befaeac8c981dd1432f541fda30aa5a20a7d62fb4a1cacd817d57b1eb72fea2",
    "engine_local_correctness": "6f52e50c8c6699650d6a3e05ae55236f10987a7ab26c41c703c209868a79fb1c",
    "engine_python_bootstrap": "4287c42d3d34f42a2e89901600c11c2fe20669f3bb6b5b120f2671b992a5df92",
    "engine_runtime_observation": "e83b13ebbc1979d77a34e2078e0bb4e2aefd01cb4299c98c1aeabe127aee428f",
    "engine_tokenizer_parity": "978814f3341b83fed33282f23ee76b173bc4235235ccf374149d0363252f3ce8",
    "pilot.serve": "9d921944e5aa7a608cc7e4cb7adea57f50ba78b04641ceca283a701a94a7e561",
    "pilot.run": "7a0d23e8b95e998a09ec7a0b066d067441e1fb1cd6121bd5172d9373c10d6cf8",
    "selected_service": "eb86228a08b017f6d660cd59d06d8de5ef8a779a5d073eab7eabd1f4ca099d4c",
    "selected_service_engine": "08569ee0c6741d87028b739f05f234452e1992eacdf9348f94b0dede520dc87d",
    "selected_service_image": IMAGE_SHA,
    "selected_service_process": "cd9b6f83286eb65edeec50d6a8985e168f42ab391f4b67375efe8512ce1dfdc9",
    "tau_live_identity": "a51e8c271dd0dd1fef22ef41babc76ab0991de11ddcc567630309395072cf2c7",
    "tau_llama_identity": "f292626bc218c21e682e61d7358517432c47dd6dac7b5c0fc91434508feb35b3",
    "tau_local_llama": "02d8023a72010821eb84f8b9f482569a0d8419d8a2baa70ffd78f4aabdb6f911",
    "tau_local_vllm": "88efe065276e1b4888e2cf5471fd14e6fcb51c527ef370a87e74eca6032486e1",
    "tau_preflight": "c38f9876dbdeb78663f9ffab5d39a28c6d87c7b298215d6179ff079072822704",
    "tau_transport": "f417f88eea5ac1aae370d66b028d475b7501a487e1aa0d4707eb482acf778be1",
    "tau_trial_worker": "133e0ebed2afd5ad38498a7ae143c6e93a2c28a007fbc8ad62412e54c3d9361f",
}
HELPERS = {EXPORT: EXPORT_SHA, RUNNER: RUNNER_SHA,
           **{"scripts/" + name.replace(".", "/") + ".py": value
              for name, value in PREPARATION_MODULES.items()}}
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


@contextmanager
def preparation_helper():
    paths = {name: ROOT / "scripts" / (name.replace(".", "/") + ".py")
             for name in PREPARATION_MODULES}
    sources = {name: read(path, 1 << 20) for name, path in paths.items()}
    require(all(sha(sources[name]) == expected for name, expected in PREPARATION_MODULES.items()))
    buffered = {str(paths[name]): raw for name, raw in sources.items()}
    direct_loaders = {str(paths[name]) for name in ("pilot.serve", "pilot.run")}

    class BufferedPath(type(Path())):
        def read_bytes(self):
            require(str(self) in buffered)
            return buffered[str(self)]

    def source_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "pathlib":
            require(fromlist == ("Path",) and level == 0)
            return types.SimpleNamespace(Path=BufferedPath)
        return builtins.__import__(name, globals, locals, fromlist, level)

    def execute_source(filename, namespace):
        require(filename in buffered)
        if filename in direct_loaders:
            namespace["__builtins__"] = {**vars(builtins), "__import__": source_import, "exec": source_exec}
        exec(compile(buffered[filename], filename, "exec", dont_inherit=True), namespace)

    def source_exec(code, namespace):
        require(type(code) is types.CodeType)
        execute_source(code.co_filename, namespace)

    class ClosedImports:
        def find_spec(self, name, path=None, target=None):
            if name in sources or name in ("pilot", "e2e"):
                return ModuleSpec(name, self, is_package=name in ("pilot", "e2e"))
            require(not name.startswith(("pilot.", "e2e.")))
            return None

        def create_module(self, spec):
            return None

        def exec_module(self, module):
            name = module.__name__
            if name in sources:
                module.__file__ = str(paths[name])
                execute_source(module.__file__, module.__dict__)

    # Authenticate the complete closed graph before executing any of it. Compile
    # those exact bytes, not a second filesystem read or an existing module cache.
    # The two legacy direct-source loaders get private Path/exec bindings as well;
    # their nested loads cannot bypass authentication or reopen dependency files.
    names = set(sources) | {"pilot", "e2e"}
    saved = {name: sys.modules[name] for name in names if name in sys.modules}
    search = list(sys.path)
    loader = ClosedImports()
    try:
        for name in names:
            sys.modules.pop(name, None)
        sys.path[:] = [path for path in search if Path(path).absolute() != ROOT / "scripts"]
        sys.meta_path.insert(0, loader)
        yield importlib.import_module("selected_service_image")
    finally:
        sys.meta_path.remove(loader)
        sys.path[:] = search
        for name in names:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def source_inputs(args):
    with preparation_helper() as image:
        return authenticated_inputs(args, image)


def authenticated_inputs(args, image):
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
        "validator_sources_sha256": sha(encoded(PREPARATION_MODULES)),
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
    raise SystemExit(main())
