#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare a source-bound CLI bundle or record an explicitly approved weight stage."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site):
    sys.exit("Use python3 -I -S host/record-stage.py")

import argparse
from contextlib import contextmanager
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import time
import types

ROOT = Path(__file__).absolute().parent.parent
SCRIPT = "host/record-stage.py"
BUILDER = "scripts/record-engine-image.py"
BUILDER_REVISION = "45e7a09039a312429a73b605021b8d18828af4b3"
BUILDER_SHA256 = "af5b2a1d9f880f1677650c25ba146cbfe673b06b216a0e47440d6f2b5ca71af2"
BUILDER_FILES = {
    BUILDER: BUILDER_SHA256,
    "scripts/prepare-engine-overlay.py": "84ad218c21117432175f3aefa54b1d3820bb269bb7ad0b7c46765b7d6e3deec3",
    "scripts/check-engine-candidate.py": "a295d3ec5ccda0794e3d830570d1d78c1b562e3cdb49ceb42aef37e6b25fd266",
    "scripts/engine_overlay.py": "077980a3e82a7cb0b7a44bd8df506b3dea8802c2a37ad3344fe56414a0801d01",
    "scripts/e2e/snapshot.py": "b7f5753ecacde3cda2bf5fdc673b6f2af239875f2a9468e34a5219a797b78b3a",
    "scripts/engine_image_diagnostic.py": "e97285260e589d4d7cb6405529275a49a88be72778977fb34f03394dea2b2a48",
}
PACK = "models/deepseek-v4.1-flash-1017/1"
SMALL = 2 << 20
MAX_BINARY = 128 << 20
MAX_SECONDS = 3 * 3600
GRACE = 30
INTERVAL = 30
SHA = r"[0-9a-f]{64}"
ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C"}
GIT_ENV = {**ENV, "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_NO_REPLACE_OBJECTS": "1",
           "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": "", "GIT_TERMINAL_PROMPT": "0"}
CANCELLED = False


def require(condition):
    if not condition:
        raise ValueError("staging contract rejected")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("ascii")


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def parse(data):
    require(len(data) <= SMALL)
    return json.loads(data.decode("ascii"), object_pairs_hook=unique)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def directory(path, *, private=False):
    path = Path(path)
    require(path.is_absolute() and str(path) == os.path.normpath(path) and ".." not in path.parts)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            require(not info.st_mode & 0o022 or info.st_mode & stat.S_ISVTX)
        if private:
            info = os.fstat(fd)
            require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
        yield fd
    finally:
        os.close(fd)


def read(path, limit=SMALL):
    path = Path(path)
    with directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit)
            data = stream.read(limit + 1)
            require(len(data) == before.st_size <= limit and identity(before) == identity(os.fstat(stream.fileno()))
                    == identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)))
            return data


try:
    BOOTSTRAP = read(Path(__file__).absolute())
except Exception:
    if __name__ == "__main__":
        sys.exit("Weight staging bootstrap rejected.")
    raise


def write(fd, name, data, mode=0o600):
    require("/" not in name and name not in {"", ".", ".."})
    output = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=fd)
    with os.fdopen(output, "wb") as stream:
        require(stream.write(data) == len(data))
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(fd)


@contextmanager
def new_directory(path):
    path = Path(path)
    with directory(path.parent) as parent:
        os.mkdir(path.name, 0o700, dir_fd=parent)
        os.fsync(parent)
        with directory(path, private=True) as fd:
            yield fd


def load_builder():
    # Pin the reviewed helper closure, not today's unrelated image descriptor.
    # All six blobs are verified before any compilation; no lazy fetch/import.
    with tempfile.TemporaryDirectory(prefix="nib-stage-helper-") as temporary:
        root = Path(temporary).resolve()
        def git(*args):
            code, data = execute(["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(ROOT), *args],
                                 30, env={**GIT_ENV, "TMPDIR": str(root)}, allow_stderr=True)
            require(code == 0)
            return data
        files = {}
        for name, expected in BUILDER_FILES.items():
            row = git("ls-tree", BUILDER_REVISION, "--", name).decode("ascii").split()
            require(len(row) == 4 and row[:2] == ["100644", "blob"] and row[3] == name
                    and re.fullmatch(r"[0-9a-f]{40}", row[2]))
            require(0 < int(git("cat-file", "-s", row[2])) <= 60000)
            data = git("cat-file", "blob", row[2])
            require(digest(data) == expected)
            files[name] = data
        for name, data in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with directory(path.parent) as fd:
                write(fd, path.name, data)
        module = types.ModuleType("committed_stage_builder")
        module.__file__ = str(root / BUILDER)
        exec(compile(files[BUILDER], module.__file__, "exec"), module.__dict__)
    # These loaded functions take an explicit source root; helper temp paths
    # are never used for source selection or later imports.
    return module


def committed(builder, revision, name):
    row = builder.checker.git(ROOT, "ls-tree", revision, "--", name).decode("ascii").split()
    require(len(row) == 4 and row[0] in {"100644", "100755"} and row[1] == "blob" and row[3] == name)
    require(0 < int(builder.checker.git(ROOT, "cat-file", "-s", row[2])) <= SMALL)
    return builder.checker.git(ROOT, "cat-file", "blob", row[2])


def validate_plan(plan):
    require(type(plan) is dict and set(plan) == {"hf_repo", "hf_revision", "manifest_sha256", "files", "total_bytes", "minimum_budget_bytes"})
    require(type(plan["hf_repo"]) is str and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", plan["hf_repo"])
            and re.fullmatch(r"[0-9a-f]{40}", plan["hf_revision"]) and re.fullmatch(SHA, plan["manifest_sha256"]))
    require(type(plan["files"]) is int and 0 < plan["files"] <= 100000
            and type(plan["total_bytes"]) is int and 0 < plan["total_bytes"] < 1 << 50
            and type(plan["minimum_budget_bytes"]) is int
            and plan["minimum_budget_bytes"] == plan["total_bytes"] + plan["files"])


def prepare(destination):
    require(not CANCELLED)
    builder = load_builder()
    revision = builder.checker.git(ROOT, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    require(re.fullmatch(r"[0-9a-f]{40}", revision))
    require(committed(builder, revision, SCRIPT) == BOOTSTRAP)
    files = {"record-stage.py": BOOTSTRAP, "pack.yaml": committed(builder, revision, PACK + "/pack.yaml"),
             "files.sha256": committed(builder, revision, PACK + "/files.sha256")}
    with tempfile.TemporaryDirectory(prefix="nib-stage-build-") as temporary:
        work = Path(temporary).resolve()
        env = builder.offline_environment(work / "control")
        native, source_hash = builder.build_validator(ROOT, revision, work, env)
        inputs = work / "pack"
        inputs.mkdir(mode=0o700)
        for name in ("pack.yaml", "files.sha256"):
            (inputs / name).write_bytes(files[name])
        plan = parse(builder.call([str(native), "model", "stage", str(inputs)], env, timeout=30))
        validate_plan(plan)
        target = work / "target"
        target.mkdir()
        binary, target_hash = builder.build_validator(ROOT, revision, target, {**env, "GOOS": "linux", "GOARCH": "amd64"})
        require(target_hash == source_hash and plan["manifest_sha256"] == digest(files["files.sha256"]))
        files["nib"] = read(binary, MAX_BINARY)
        receipt = {"schema": "nib.stage-bundle/v1", "git_sha": revision, "build_source_sha256": source_hash,
                   "builder_sha256": BUILDER_SHA256, "builder_git_sha": BUILDER_REVISION,
                   "toolchain": builder.GO_TOOLCHAIN, "platform": "linux/amd64",
                   "plan": plan, "files": {name: {"sha256": digest(data), "bytes": len(data)} for name, data in files.items()},
                   "scope": "trusted-offline-builder-receipt-not-reproducibility-or-attestation"}
        require(not CANCELLED)
        with new_directory(destination) as fd:
            for name, data in files.items():
                write(fd, name, data, 0o500 if name == "nib" else 0o600)
            write(fd, "receipt.json", json_bytes(receipt))
    print(json_bytes({"source_git_sha": revision, "receipt_sha256": digest(json_bytes(receipt))}).decode(), end="")


def bundle(root, expected):
    require(re.fullmatch(SHA, expected))
    with directory(root, private=True) as fd:
        names = set()
        with os.scandir(fd) as entries:
            for entry in entries:
                require(len(names) < 5)
                names.add(entry.name)
        require(names == {"receipt.json", "record-stage.py", "nib", "pack.yaml", "files.sha256"})
    data = read(root / "receipt.json")
    require(digest(data) == expected)
    receipt = parse(data)
    require(set(receipt) == {"schema", "git_sha", "build_source_sha256", "builder_sha256", "builder_git_sha", "toolchain", "platform", "plan", "files", "scope"}
            and receipt["schema"] == "nib.stage-bundle/v1" and receipt["platform"] == "linux/amd64"
            and receipt["toolchain"] == "go1.27.2" and receipt["builder_sha256"] == BUILDER_SHA256
            and receipt["builder_git_sha"] == BUILDER_REVISION
            and receipt["scope"] == "trusted-offline-builder-receipt-not-reproducibility-or-attestation"
            and re.fullmatch(r"[0-9a-f]{40}", receipt["git_sha"]) and re.fullmatch(SHA, receipt["build_source_sha256"]))
    validate_plan(receipt["plan"])
    require(set(receipt["files"]) == {"record-stage.py", "nib", "pack.yaml", "files.sha256"})
    files = {}
    for name, expected_file in receipt["files"].items():
        data = read(root / name, MAX_BINARY if name == "nib" else SMALL)
        require(expected_file == {"sha256": digest(data), "bytes": len(data)})
        files[name] = data
    require(files["record-stage.py"] == BOOTSTRAP and digest(files["files.sha256"]) == receipt["plan"]["manifest_sha256"])
    return receipt, files


def capacity(destination, plan):
    # Conservative full-pack allowance even on resume. Not a reservation or
    # provision for a second immutable image; concurrent users/quotas can differ.
    reserve = max(10 << 30, (plan["total_bytes"] + 9) // 10)
    with directory(destination.parent, private=True) as parent:
        try:
            fd = os.open(destination.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            info = os.fstatvfs(parent)
        else:
            try:
                target = os.fstat(fd)
                require(target.st_uid == os.getuid() and stat.S_IMODE(target.st_mode) == 0o700)
                info = os.fstatvfs(fd)
            finally:
                os.close(fd)
    available = info.f_bavail * info.f_frsize
    required = plan["total_bytes"] + reserve
    require(type(available) is int and 0 <= available < 1 << 63)
    return {"available_bytes": available, "required_bytes": required, "overhead_bytes": reserve, "sufficient": available >= required}


def observation(destination, plan):
    counts = {"observed_files": 0, "observed_bytes": 0, "partial_files": 0, "partial_bytes": 0}
    deadline, visited, seen = time.monotonic() + 2, 0, set()
    try:
        with directory(destination.parent, private=True) as parent:
            try:
                fd = os.open(destination.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            except FileNotFoundError:
                return counts
            def walk(fd, depth):
                nonlocal visited
                require(depth <= 32)
                with os.scandir(fd) as entries:
                    for entry in entries:
                        visited += 1
                        require(visited <= 2 * plan["files"] + 256 and time.monotonic() < deadline)
                        info = entry.stat(follow_symlinks=False)
                        if stat.S_ISDIR(info.st_mode):
                            child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                            try:
                                require(identity(info) == identity(os.fstat(child)))
                                walk(child, depth + 1)
                            finally:
                                os.close(child)
                        else:
                            require(stat.S_ISREG(info.st_mode) and 0 <= info.st_size <= plan["total_bytes"] + plan["files"])
                            key = (info.st_dev, info.st_ino)
                            if key in seen:
                                continue
                            seen.add(key)
                            prefix = "partial" if entry.name.startswith(".nib-part-") else "observed"
                            counts[prefix + "_files"] += 1
                            counts[prefix + "_bytes"] += info.st_size
            try:
                info = os.fstat(fd)
                require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
                walk(fd, 0)
            finally:
                os.close(fd)
    except (OSError, ValueError):
        return None
    return counts


# Keep the group leader alive until the monitoring parent closes its liveness
# pipe. Inherited pipes are bounded; raw child output is never logged. SIGTERM
# gets a grace window for the Go downloader to remove its own partial file.
SUPERVISOR = """
import os,select,signal,subprocess,sys,time
status,life,grace=int(sys.argv[1]),int(sys.argv[2]),float(sys.argv[3])
child=None
try:
    child=subprocess.Popen(sys.argv[4:],stdin=subprocess.DEVNULL)
    signal.signal(signal.SIGTERM,lambda *_: None)
    while child.poll() is None:
        if select.select([life],[],[],0.1)[0]:
            child.terminate()
            try: child.wait(timeout=grace)
            except subprocess.TimeoutExpired: pass
            raise SystemExit(1)
    os.write(status,str(child.returncode).encode('ascii'))
    os.close(status);os.close(1);os.close(2)
    select.select([life],[],[])
finally:
    os.killpg(os.getpid(),signal.SIGKILL)
"""


def execute(argv, timeout, progress=None, state=None, *, env=None, allow_stderr=False):
    require(0 < timeout <= MAX_SECONDS and not CANCELLED)
    reader, writer = os.pipe()
    try:
        life_reader, life_writer = os.pipe()
    except BaseException:
        os.close(reader)
        os.close(writer)
        raise
    try:
        require(not CANCELLED)
        child = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", SUPERVISOR, str(writer), str(life_reader), str(GRACE), *argv],
            env=ENV if env is None else env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            pass_fds=(writer, life_reader), start_new_session=True)
    except BaseException:
        os.close(reader)
        os.close(life_writer)
        raise
    finally:
        os.close(writer)
        os.close(life_reader)
    if state is not None:
        state["cleanup"] = "unproven"
    buffers = {"stdout": bytearray(), "status": bytearray()}
    started, count, ending, reason = time.monotonic(), 0, None, None
    stderr_seen = False
    tick = started
    try:
        with selectors.DefaultSelector() as selector:
            for stream, name in ((child.stdout, "stdout"), (child.stderr, "stderr"), (reader, "status")):
                selector.register(stream, selectors.EVENT_READ, name)
            while selector.get_map():
                now = time.monotonic()
                if ending is None and (CANCELLED or now - started >= timeout):
                    reason = "cancelled" if CANCELLED else "deadline"
                    ending = now + GRACE + 1
                    os.close(life_writer)
                    life_writer = None
                if ending is not None and now >= ending:
                    break
                if progress is not None and now >= tick:
                    progress(int(now - started))
                    tick = now + INTERVAL
                for key, _ in selector.select(0.25):
                    data = os.read(key.fd, 4096)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    count += len(data)
                    require(count <= 65536)
                    if key.data in buffers:
                        buffers[key.data].extend(data)
                    else:
                        stderr_seen = True
        code = bytes(buffers["status"])
        if reason is not None or CANCELLED:
            raise ValueError("staging interrupted")
        require(code in (b"0", b"1", b"2"))
        # Only bounded read-only Git bootstrap operations may discard platform
        # launcher warnings; their returned source still requires exact hashes.
        require(code != b"0" or not stderr_seen or allow_stderr)
        return int(code), bytes(buffers["stdout"])
    finally:
        if life_writer is not None:
            os.close(life_writer)
        try:
            child.wait(timeout=GRACE + 2)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)
        child.stdout.close()
        child.stderr.close()
        os.close(reader)
        deadline = time.monotonic() + 5
        while True:
            try:
                os.killpg(child.pid, 0)
            except ProcessLookupError:
                if state is not None:
                    state["cleanup"] = "confirmed"
                break
            require(time.monotonic() < deadline)
            time.sleep(0.05)


def validate_proof(proof, plan, budget):
    require(type(proof) is dict and set(proof) == {"hf_repo", "hf_revision", "manifest_sha256", "files", "verified_bytes", "downloaded_bytes", "reused_files"})
    require(all(type(proof[key]) is int for key in ("files", "verified_bytes", "downloaded_bytes", "reused_files")))
    require(all(proof[key] == plan[key] for key in ("hf_repo", "hf_revision", "manifest_sha256", "files"))
            and proof["verified_bytes"] == plan["total_bytes"]
            and type(proof["downloaded_bytes"]) is int and 0 <= proof["downloaded_bytes"] <= budget
            and type(proof["reused_files"]) is int and 0 <= proof["reused_files"] <= plan["files"])


def utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(root, expected, destination, evidence, seconds, budget, download, workers=1):
    require(sys.platform == "linux" and os.uname().machine == "x86_64")
    require(type(seconds) is int and 0 < seconds <= MAX_SECONDS and type(budget) is int and budget > 0)
    require(type(workers) is int and 1 <= workers <= 4)
    require(destination.is_absolute() and destination.name not in ("", ".", "..") and ".." not in destination.parts)
    paths = (root, destination, evidence)
    require(all(path.is_absolute() and str(path) == os.path.normpath(path) and ".." not in path.parts for path in paths))
    require(all(left != right and left not in right.parents and right not in left.parents
                for index, left in enumerate(paths) for right in paths[index + 1:]))
    receipt, files = bundle(root, expected)
    plan = receipt["plan"]
    require(budget == plan["minimum_budget_bytes"])
    if not download:
        disk = capacity(destination, plan)
        print(json_bytes({"plan": plan, "disk": disk, "download": False}).decode(), end="")
        return 0 if disk["sufficient"] else 1
    started = utc()
    report = {"schema": "nib.weight-stage-recording/v1", "result": "fail", "receipt_sha256": expected,
              "source_git_sha": receipt["git_sha"], "build_source_sha256": receipt["build_source_sha256"],
              "nib_sha256": receipt["files"]["nib"]["sha256"], "plan": plan, "disk": None, "proof": None,
              "last_observation": None, "deadline_seconds": seconds, "body_byte_budget": budget,
              "cleanup": "not-started", "cleanup_scope": "local-process-group-only", "stage": "disk-preflight", "hardware_validated": False,
              "runtime_ready": False, "confidential_qualified": False}
    if workers != 1:
        report["download_workers"] = workers
    with new_directory(evidence) as output:
        try:
            require(not CANCELLED)
            disk = capacity(destination, plan)
            report["disk"] = disk
            require(disk["sufficient"])
            with tempfile.TemporaryDirectory(prefix="nib-stage-exec-") as temporary:
                private = Path(temporary).resolve()
                with directory(private, private=True) as fd:
                    for name in ("nib", "pack.yaml", "files.sha256"):
                        write(fd, name, files[name], 0o500 if name == "nib" else 0o600)
                report["stage"] = "plan-verification"
                code, data = execute([str(private / "nib"), "model", "stage", str(private)], 30, state=report)
                require(code == 0 and parse(data) == plan)
                def progress(elapsed):
                    counts = observation(destination, plan)
                    report["last_observation"] = {"elapsed_seconds": elapsed, "counts": counts, "verified": False}
                    print(json_bytes({"stage": "staging", **report["last_observation"]}).decode(), end="", flush=True)
                report.update(stage="staging", cleanup="unproven")
                worker_args = [] if workers == 1 else ["--workers", str(workers)]
                code, data = execute([str(private / "nib"), "model", "stage", "--download", "--max-bytes", str(budget),
                                     "--timeout", str(seconds) + "s", *worker_args, str(private), str(destination)], seconds, progress, report)
                require(code == 0)
                proof = parse(data)
                validate_proof(proof, plan, budget)
                report.update(proof=proof, result="pass", stage="complete")
        except (Exception, KeyboardInterrupt):
            report.update(result="fail", proof=None, failure_code="staging-preflight-execution-or-proof-failed")
        ended = utc()
        if ended < started:
            ended = started
            report.update(result="fail", proof=None, failure_code="clock-regressed")
        record = {"schema": "nib.evidence/v1", "phase": "P0", "task": "T4", "git_sha": receipt["git_sha"],
                  "command": "python3 -I -S record-stage.py run --download --seconds " + str(seconds)
                      + " --max-bytes " + str(budget) + " --receipt-sha256 " + expected
                      + ("" if workers == 1 else " --workers " + str(workers))
                      + " --destination <private> --evidence <private>",
                  "started_utc": started, "ended_utc": ended, "result": report["result"], "execution": "software",
                  "hardware_validated": False, "host": {"provider": "operator-approved-test-host", "machine": "linux-amd64",
                      "gpus": "not-used", "driver": None, "cuda": None},
                  "notes": "Pinned public byte staging only; no engine launch, immutable volume, attestation or runtime readiness. "
                           "Trusted offline builder receipt SHA-256: " + expected + ". Progress is unverified metadata, not transfer or integrity proof. "
                           "Process bounds do not delete a rented machine or stop billing. Complete model files are retained; no wildcard cleanup."}
        write(output, "output.log", json_bytes(report))
        write(output, "run.json", json_bytes(record))
    return 0 if report["result"] == "pass" else 1


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid staging arguments")


def main():
    parser = Parser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--output", type=Path, required=True)
    launch = sub.add_parser("run")
    launch.add_argument("--receipt-sha256", required=True)
    launch.add_argument("--destination", type=Path, required=True)
    launch.add_argument("--evidence", type=Path, required=True)
    launch.add_argument("--seconds", type=int, required=True)
    launch.add_argument("--max-bytes", type=int, required=True)
    launch.add_argument("--download", action="store_true")
    launch.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.output)
        return 0
    return run(Path(__file__).absolute().parent, args.receipt_sha256, args.destination, args.evidence,
               args.seconds, args.max_bytes, args.download, args.workers)


def cancel(signum, frame):
    global CANCELLED
    CANCELLED = True


if __name__ == "__main__":
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, cancel)
    try:
        sys.exit(main())
    except Exception:
        sys.exit("Weight staging failed; no runtime or hardware acceptance may be inferred.")
