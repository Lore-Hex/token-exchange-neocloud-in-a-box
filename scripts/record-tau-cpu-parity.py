#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Explicit offline two-model CPU parity; no live tau admission or quality proof."""
import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B scripts/record-tau-cpu-parity.py")

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import tempfile
import types
import uuid

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = "scripts/record-tau-cpu-parity.py"
SNAPSHOT = "scripts/e2e/snapshot.py"
SNAPSHOT_SHA = "e73a5a1e3ddb7a6e7b20afce39f7c080a0317d78340f224b2bd95f705bccb59c"
WHEEL = "cmake-3.31.6-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
WHEEL_SHA = "1c8b05df0602365da91ee6a3336fe57525b137706c4ab5675498f662ae1dbcec"
SOURCES = (SCRIPT, SNAPSHOT, "scripts/e2e/exercise.py", "scripts/pilot/run.py",
           "scripts/pilot/pins.json", "scripts/pilot/build-requirements.lock", "scripts/e2e/pins.json",
           "scripts/tau_cpu_parity.py", "scripts/tau_cpu_parity.cpp", "scripts/tau_cpu_parity.cmake",
           "scripts/tau_cpu_parity.Dockerfile", "scripts/tau_cpu_support_models.json",
           "scripts/engine_tokenizer_parity.py", "scripts/tau_live_identity.py",
           "scripts/tau_local_llama.py", "scripts/tau_llama_identity.py", "scripts/tau_local_vllm.py", "scripts/tau_transport.py",
           "scripts/tau_preflight.py", "scripts/tau_trial_worker.py",
           "scripts/prepare-tau-cpu-parity-native.py", ".github/workflows/tau-cpu-parity-native.yml")
BOOTSTRAP = Path(__file__).read_bytes()


def require(value):
    if not value:
        raise ValueError("CPU parity operation rejected")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def load(path, name, raw=None):
    result = types.ModuleType(name)
    result.__file__ = str(path)
    exec(compile(path.read_bytes() if raw is None else raw, str(path), "exec", dont_inherit=True), result.__dict__)
    return result


def toolkit(root):
    # This path is private and freshly extracted, with no ignored bytecode.
    # Order the explicit source closure instead of importing ambient modules.
    for name in ("engine_tokenizer_parity", "tau_preflight", "tau_trial_worker", "tau_transport",
                 "tau_local_vllm", "tau_local_llama", "tau_llama_identity", "tau_live_identity", "tau_cpu_parity"):
        sys.modules[name] = load(root / "scripts" / (name + ".py"), name)
    return sys.modules["tau_cpu_parity"]


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def host(kind):
    require(kind in ("local", "github-actions"))
    if kind == "github-actions":
        require(sys.platform == "linux" and os.uname().machine == "x86_64"
                and os.environ.get("GITHUB_ACTIONS") == "true"
                and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted")
    return {"provider": kind, "machine": "owned-linux-amd64-container", "gpus": "none",
            "driver": None, "cuda": None}


def runtime_command(image, name):
    return ["docker", "run", "--name", name, "--rm", "--init", "--pull", "never",
            "--platform", "linux/amd64", "--network", "none", "--cgroupns", "private", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--user", "65532:65532", "--log-driver", "none",
            "--pids-limit", "128", "--memory", "8g", "--memory-swap", "8g", "--cpus", "4",
            "--ulimit", "core=0:0", "--ulimit", "nofile=1024:1024",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=128m,mode=1777",
            "--tmpfs", "/work:rw,exec,nosuid,nodev,size=4g,mode=0700,uid=65532,gid=65532", image]


def context(source, target, inputs, pilot):
    require(len(inputs) == 4)
    pins = json.loads((source / "scripts/tau_cpu_support_models.json").read_bytes())
    files = {}
    for relative in SOURCES:
        raw = (source / relative).read_bytes()
        require(len(raw) <= 1 << 20)
        destination = target / "repo" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        files[relative] = digest(raw)
    (target / "repo/source.json").write_text(json.dumps(files, sort_keys=True))
    shutil.copyfile(source / "scripts/tau_cpu_parity.Dockerfile", target / "Dockerfile")
    (target / "inputs").mkdir()
    (target / "cmake").mkdir()
    for original, name, sha, size in (
        (inputs[0], "inputs/llama.tar.gz", pins["engine"]["archive_sha256"], 64 << 20),
        (inputs[1], "inputs/0.gguf", pins["models"]["qwen/qwen3-0.6b"]["sha256"], 639446688),
        (inputs[2], "inputs/1.gguf", pins["models"]["qwen/qwen3-1.7b"]["sha256"], 1834426016),
        (inputs[3], "cmake/" + WHEEL, WHEEL_SHA, 32 << 20),
    ):
        pilot.checked_copy(original, target / name, sha, size)
        require(pilot.file_digest(target / name) == sha)
        if name.endswith(".gguf"):
            require((target / name).stat().st_size == size)
    hashes = {path.relative_to(target).as_posix(): pilot.file_digest(path)
              for path in sorted(target.rglob("*")) if path.is_file()}
    return digest(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()), files


def execute(source, work, env, snapshot, inputs, report, home):
    cpu = toolkit(source)
    pilot, _ = cpu.tools()
    cleanup = load(source / "scripts/e2e/run.py", "cpu_cleanup").cleanup_resources
    name = "nib-cpu-parity-" + uuid.uuid4().hex
    image = "nib-cpu-parity:" + name.removeprefix("nib-cpu-parity-")
    build_started, build_done = False, False
    try:
        env = {**env, "DOCKER_HOST": snapshot.local_docker_endpoint(env, home)}
        snapshot.require_buildkit(env)
        report["stage"] = "inputs"
        target = work / "context"
        target.mkdir()
        report["context_sha256"], report["sources"] = context(source, target, inputs, pilot)
        bases = json.loads((source / "scripts/e2e/pins.json").read_bytes())
        report["base_images"] = {key: bases[key] for key in ("go_image", "python_image")}
        # Docker 28.0.4 inspect has no --platform flag; verify returned platform.
        # Fail before building if either immutable base is not already local.
        report["stage"] = "base-images"
        for base in report["base_images"].values():
            result = snapshot.execute_process_group(["docker", "image", "inspect", "--format",
                "{{.Id}} {{.Os}}/{{.Architecture}}", base], env=env, timeout=15,
                max_output_bytes=1024, max_stderr_bytes=1024)
            require(result.returncode == 0 and not result.stderr
                    and re.fullmatch(rb"sha256:[0-9a-f]{64} linux/amd64\s*", result.stdout))
        report["stage"] = "toolchain"
        build_started = True
        iid = work / "image-id"
        result = snapshot.execute_process_group(["docker", "build", "--network", "none", "--pull=false",
            "--platform", "linux/amd64", "--tag", image, "--iidfile", str(iid),
            "--build-arg", "GO_IMAGE=" + bases["go_image"], "--build-arg", "PYTHON_IMAGE=" + bases["python_image"],
            str(target)], env=env, timeout=240, capture_output=False)
        require(result.returncode == 0)
        build_done = True
        image_id = iid.read_text().strip()
        require(re.fullmatch("sha256:[0-9a-f]{64}", image_id))
        report["image_id"] = image_id
        report["stage"] = "runtime"
        result = snapshot.execute_process_group(runtime_command(image_id, name), env=env,
            timeout=cpu.TOTAL_SECONDS, max_output_bytes=65536, max_stderr_bytes=65536,
            stdin=subprocess.DEVNULL)
        require(result.returncode in (0, 1) and not result.stderr)
        runtime = cpu.validate(cpu.llama.worker.decode(result.stdout))
        require((runtime["result"] == "pass") == (result.returncode == 0))
        report["runtime"] = runtime
        report["result"] = runtime["result"]
        report["stage"] = "complete" if runtime["result"] == "pass" else "runtime"
    except BaseException:
        report["result"] = "fail"
    finally:
        # Removing Docker's client group does not prove its daemon-side children
        # stopped. Always remove the owned container and query actual absence.
        cleaned = cleanup(image, name, env=env)
        report["cleanup"] = {key: cleaned[key]["absence"] for key in ("container", "image")}
        report["cleanup"]["build_completion"] = "unknown" if build_started and not build_done else "confirmed"
        if cleaned["result"] != "pass" or report["cleanup"]["build_completion"] != "confirmed":
            report["result"] = "fail"


def worker():
    # No ambient imports or bytecode. Every dependency comes from the parent's
    # exact source snapshot embedded in the read-only, identity-bound image.
    expected = json.loads((ROOT / "source.json").read_bytes())
    require(set(expected) == set(SOURCES))
    for path, sha in expected.items():
        require(digest((ROOT / path).read_bytes()) == sha)
    cpu = toolkit(ROOT)
    result = cpu.validate(cpu.run(Path("/inputs/llama.tar.gz"),
        [Path("/inputs/0.gguf"), Path("/inputs/1.gguf")], Path("/work")))
    raw = cpu.probes.encoded(result)
    require(len(raw) <= 65536)
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()
    return 0 if result["result"] == "pass" else 1


def write(fd, name, value):
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    require(len(raw) <= 65536)
    target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(target, "wb") as stream:
        require(stream.write(raw) == len(raw))
        stream.flush()
        os.fsync(stream.fileno())


def main():
    if sys.argv[1:] == ["--worker"]:
        return worker()
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError("CPU parity arguments rejected")
    parser = Parser(description=__doc__)
    for name in ("archive", "model-06", "model-17", "cmake-wheel", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--host-kind", choices=("local", "github-actions"), default="local")
    args = parser.parse_args()
    facts = host(args.host_kind)
    os.umask(0o077)
    raw = (ROOT / SNAPSHOT).read_bytes()
    require(digest(raw) == SNAPSHOT_SHA)
    snapshot = load(ROOT / SNAPSHOT, "cpu_snapshot", raw)
    with tempfile.TemporaryDirectory(prefix="nib-cpu-parity-") as temporary:
        work = Path(temporary)
        env = snapshot.controlled_env(work / "control")
        env.update(GIT_ALLOW_PROTOCOL="", GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0", GOPROXY="off", GOSUMDB="off")
        require(not snapshot.git(ROOT, env, "status", "--porcelain", stdout=subprocess.PIPE).stdout)
        source = work / "source"
        revision, files, archive_sha = snapshot.snapshot_commit(ROOT, source, work / "control", env)
        require((source / SCRIPT).read_bytes() == BOOTSTRAP
                and digest((source / SNAPSHOT).read_bytes()) == SNAPSHOT_SHA)
        exporter = load(source / "scripts/pilot/export.py", "cpu_export")
        parent = exporter.open_parent(args.output)
        try:
            os.mkdir(args.output.name, 0o700, dir_fd=parent)
            fd = os.open(args.output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        finally:
            os.close(parent)
        try:
            report = {"schema": "nib.tau-cpu-parity-run/v1", "result": "fail", "stage": "inputs",
                      "git_sha": revision, "source_archive_sha256": archive_sha, "runtime": None,
                      "context_sha256": None, "sources": {}, "base_images": {}, "image_id": None,
                      "cleanup": {"container": "unknown", "image": "unknown", "build_completion": "unknown"},
                      "quality_eligible": False, "confidential": False, "live_admission": False}
            record = {"schema": "nib.evidence/v1", "phase": "P2", "task": "T3", "git_sha": revision,
                      "command": "python3 -I -S -B scripts/record-tau-cpu-parity.py --archive PRIVATE --model-06 PRIVATE --model-17 PRIVATE --cmake-wheel PRIVATE --output PRIVATE",
                      "started_utc": now(), "ended_utc": now(), "result": "fail", "execution": "software",
                      "hardware_validated": False,
                      "host": facts,
                      "notes": "Offline CPU tokenizer prerequisite only. No tau trial, quality, live admission, public service or confidential claim. Pending until output.log and final receipt are present."}
            if args.host_kind != "local":
                record["command"] += " --host-kind " + args.host_kind
            write(fd, "run.json", record)
            execute(source, work, env, snapshot, [args.archive, args.model_06, args.model_17, args.cmake_wheel],
                    report, Path(pwd.getpwuid(os.getuid()).pw_dir))
            try:
                snapshot.validate_snapshot(source, files)
            except BaseException:
                report.update(result="fail", stage="source-changed")
            write(fd, "output.log", report)
            record.update(result=report["result"], ended_utc=now())
            record["notes"] = record["notes"].split(" Pending")[0]
            write(fd, "run.json.pending", record)
            os.replace("run.json.pending", "run.json", src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
            print("CPU parity: " + report["result"] + "; no qualification claim.")
            return 0 if report["result"] == "pass" else 1
        finally:
            os.close(fd)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BaseException as exc:
        if isinstance(exc, SystemExit) and type(exc.code) is int:
            raise
        sys.exit("CPU parity rejected; no successful result may be inferred.")
