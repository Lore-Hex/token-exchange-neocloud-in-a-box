#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare only existing hash-pinned CPU inputs on an ephemeral native runner."""
import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B scripts/prepare-tau-cpu-parity-native.py")

import argparse
import json
import os
from pathlib import Path
import pwd
import subprocess
import tempfile
import time
import types

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = "scripts/prepare-tau-cpu-parity-native.py"
rec = types.ModuleType("native_cpu_recorder")
rec.__file__ = str(ROOT / "scripts/record-tau-cpu-parity.py")
exec(compile(Path(rec.__file__).read_bytes(), rec.__file__, "exec", dont_inherit=True), rec.__dict__)
require = rec.require
WHEEL_BASE = "https://files.pythonhosted.org/packages/59/e8/096984b89133681533650b9078c5ed1c5c9b534e869b5487f22d4de1935c/"
PREP_SECONDS = 900


def plan(pins):
    engine = pins["engine"]
    require(engine["repository"] == "ggml-org/llama.cpp")
    result = {"llama.tar.gz": ("https://codeload.github.com/" + engine["repository"] + "/tar.gz/"
                              + engine["revision"], 38195486, engine["archive_sha256"]),
              rec.WHEEL: (WHEEL_BASE + rec.WHEEL, 27800904, rec.WHEEL_SHA)}
    for index, model in enumerate(("qwen/qwen3-0.6b", "qwen/qwen3-1.7b")):
        row = pins["models"][model]
        result[str(index) + ".gguf"] = ("https://huggingface.co/" + row["repository"] + "/resolve/"
                                       + row["revision"] + "/" + row["filename"], row["bytes"], row["sha256"])
    require(sum(size for _, size, _ in result.values()) < 3 << 30)
    return result


def fetch(fd, name, spec, env, snapshot, deadline):
    url, size, sha = spec
    remaining = min(600, deadline - time.monotonic())
    require(remaining > 0)
    output = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(output, "w+b") as stream:
        # Parent streaming limits also bound chunked/incorrect Content-Length.
        result = snapshot.execute_process_group(["curl", "-q", "--fail", "--silent", "--location",
            "--proto", "=https", "--proto-redir", "=https", "--max-redirs", "5", "--connect-timeout", "20",
            "--max-time", str(remaining), "--max-filesize", str(size), url], env=env,
            timeout=remaining, stdout_file=stream, max_output_bytes=size, max_stderr_bytes=4096,
            stdin=subprocess.DEVNULL)
        require(result.returncode == 0 and not result.stderr)
        stream.flush()
        require(os.fstat(stream.fileno()).st_size == size)
        stream.seek(0)
        digest = rec.hashlib.sha256()
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
        require(digest.hexdigest() == sha and time.monotonic() < deadline)
        os.fsync(stream.fileno())


def prepare(directory, source, env, snapshot, deadline):
    pilot = rec.load(source / "scripts/pilot/export.py", "native_cpu_output")
    parent = pilot.open_parent(directory)
    try:
        os.mkdir(directory.name, 0o700, dir_fd=parent)
        fd = os.open(directory.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        pins = json.loads((source / "scripts/tau_cpu_support_models.json").read_bytes())
        for name, spec in plan(pins).items():
            fetch(fd, name, spec, env, snapshot, deadline)
    finally:
        os.close(fd)
    bases = json.loads((source / "scripts/e2e/pins.json").read_bytes())
    for key in ("go_image", "python_image"):
        remaining = min(180, deadline - time.monotonic())
        require(remaining > 0)
        result = snapshot.execute_process_group(["docker", "pull", "--platform", "linux/amd64", bases[key]],
            env=env, timeout=remaining, capture_output=False, stdin=subprocess.DEVNULL)
        require(result.returncode == 0 and time.monotonic() < deadline)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rec.host("github-actions")
    os.umask(0o077)
    deadline = time.monotonic() + PREP_SECONDS
    raw = (ROOT / rec.SNAPSHOT).read_bytes()
    require(rec.digest(raw) == rec.SNAPSHOT_SHA)
    snapshot = rec.load(ROOT / rec.SNAPSHOT, "native_cpu_snapshot", raw)
    with tempfile.TemporaryDirectory(prefix="nib-cpu-native-prepare-") as temporary:
        work = Path(temporary)
        env = snapshot.controlled_env(work / "control")
        env.update(GIT_ALLOW_PROTOCOL="", GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
        require(not snapshot.git(ROOT, env, "status", "--porcelain", stdout=subprocess.PIPE).stdout)
        source = work / "source"
        _, files, _ = snapshot.snapshot_commit(ROOT, source, work / "control", env)
        require((source / SCRIPT).read_bytes() == Path(__file__).read_bytes()
                and (source / rec.SCRIPT).read_bytes() == rec.BOOTSTRAP
                and rec.digest((source / rec.SNAPSHOT).read_bytes()) == rec.SNAPSHOT_SHA)
        env["DOCKER_HOST"] = snapshot.local_docker_endpoint(env, Path(pwd.getpwuid(os.getuid()).pw_dir))
        prepare(args.output.absolute(), source, env, snapshot, deadline)
        snapshot.validate_snapshot(source, files)
    print("Native CPU inputs verified; no model execution performed.")


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        if isinstance(error, SystemExit) and type(error.code) is int:
            raise
        sys.exit("Native CPU preparation failed; no model execution performed.")
