#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare a private checker payload offline; never launch or attach to an engine."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B scripts/provider_conformance.py")

import argparse
import hashlib
import io
import os
from pathlib import Path
import re
import stat
import time
import types
import zipfile

ROOT = Path(__file__).absolute().parent.parent
RUNTIME_SHA = "2a04a7198f9c95a53abed9d03b28abfaf34792f544ead550a94620248b3a90d4"
SUPPORT_SHA = "133e0ebed2afd5ad38498a7ae143c6e93a2c28a007fbc8ad62412e54c3d9361f"
LOCK_SHA = "d5fbea8926b7410c34730593b8ebd305f01b04a160eb30e9867330c45799ade7"
CHECKER_SHA = "591a11295d3f1e0ef20fbc51c788cabfbedf176e79ce56de8378252bd13adecd"
PROFILE_SHA = "11475b07991fb510704d64143fcd9a0084327e586699604f14f88436960b754a"
PROFILE_SOURCE = "scripts/deepseek_model_profile.py"
SOURCES = ("scripts/provider_conformance.py", "scripts/provider_conformance_worker.py",
           "scripts/tau_runtime.py", "scripts/tau_trial_worker.py", "scripts/e2e/requirements.lock", PROFILE_SOURCE)


def require(value):
    if not value:
        raise ValueError("provider payload rejected")


def source(name, expected):
    path = ROOT / name
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= 1 << 20)
        raw = stream.read((1 << 20) + 1)
    require(hashlib.sha256(raw).hexdigest() == expected)
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module


runtime = source("scripts/tau_runtime.py", RUNTIME_SHA)
support = source("scripts/tau_trial_worker.py", SUPPORT_SHA)


def identity(deadline):
    result = None
    for offset in range(0, len(SOURCES), 4):
        current = runtime.tau.committed_identity(deadline, extra_files=SOURCES[offset:offset + 4])
        if result is None:
            result = current
        else:
            require(result["git_sha"] == current["git_sha"])
            result["files"].update(current["files"])
    return result


def requirements(raw):
    require(runtime.digest(raw) == LOCK_SHA)
    result = {}
    for block in re.split(r"(?m)(?=^[a-z0-9-]+==)", raw.decode("ascii")):
        match = re.match(r"([a-z0-9-]+)==([^\s]+)", block)
        if not match:
            require(not block.strip())
            continue
        name, version = match.groups()
        hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})", block)
        require(name not in result and hashes)
        result[name] = (version, frozenset(hashes))
    require(len(result) == 17 and result["trustedrouter-provider-check"][0] == "0.1.8")
    return result


def add_wheel(files, raw, expected, deadline):
    runtime.check_wheel(raw, expected, deadline)
    unpack_wheel(files, raw, deadline)


def unpack_wheel(files, raw, deadline):
    """Only called after target admission and complete archive validation."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for row in archive.infolist():
            require(time.monotonic() < deadline)
            if row.is_dir():
                continue
            parts = runtime.safe_name(row.filename)
            require(not row.filename.endswith((".pyc", ".pyo", ".pth", ".egg-link"))
                    and not any(p.endswith(".data") for p in parts)
                    and not any(p in ("sitecustomize", "usercustomize", "sitecustomize.py", "usercustomize.py")
                                for p in parts))
            name = "packages/" + row.filename
            require(name not in files)
            files[name] = archive.read(row)
    require(len(files) <= 4000 and sum(map(len, files.values())) <= 64 << 20)


def wheel_inputs(wheelhouse, deadline):
    lock = runtime.read_file(ROOT / "scripts/e2e/requirements.lock", 1 << 20, deadline)
    packages = requirements(lock)
    with runtime.directory(wheelhouse) as directory:
        names = os.listdir(directory)
    require(len(names) == len(packages))
    selected = set()
    for name in sorted(names):
        require(name.endswith(".whl"))
        parts = name.split("-")
        require(len(parts) == 5)
        package = parts[0].replace("_", "-").lower()
        require(package in packages and package not in selected and parts[1] == packages[package][0])
        raw = runtime.read_file(wheelhouse / name, 16 << 20, deadline)
        sha = runtime.digest(raw)
        require(sha in packages[package][1])
        if package == "trustedrouter-provider-check":
            require(sha == CHECKER_SHA)
        yield raw, {"filename": name, "version": parts[1], "bytes": len(raw), "sha256": sha}
        selected.add(package)
    require(selected == set(packages))


def prepare(wheelhouse, deadline, *, model_profile="original"):
    require(type(model_profile) is str and model_profile in ("original", "uncensored"))
    files = {}
    for raw, expected in wheel_inputs(wheelhouse, deadline):
        add_wheel(files, raw, expected, deadline)
    for target, name in (("worker.py", "scripts/provider_conformance_worker.py"),
                         ("support.py", "scripts/tau_trial_worker.py")):
        files[target] = runtime.read_file(ROOT / name, 1 << 20, deadline)
    require(runtime.digest(files["support.py"]) == SUPPORT_SHA)
    identity = None
    if model_profile == "uncensored":
        identity = source(PROFILE_SOURCE, PROFILE_SHA).select(model_profile)
        files["model-profile.py"] = runtime.read_file(ROOT / PROFILE_SOURCE, 1 << 20, deadline)
        require(runtime.digest(files["model-profile.py"]) == PROFILE_SHA)
    plan = {"schema": "nib.private-provider-payload/v1", "requirements_sha256": LOCK_SHA,
            "checker_wheel_sha256": CHECKER_SHA,
            "files": {name: {"bytes": len(raw), "sha256": runtime.digest(raw)}
                      for name, raw in sorted(files.items())}}
    if identity is not None:
        plan["model_profile"] = identity
    files["trial.json"] = runtime.encoded(plan)
    return files


def publish(destination, files):
    # Private parent remains 0700; only its read-only subtree enters the container.
    with runtime.directory(destination.parent) as parent:
        os.mkdir(destination.name, 0o700, dir_fd=parent)
    payload = destination / "payload"
    payload.mkdir(mode=0o700)
    for name, raw in files.items():
        runtime.safe_name(name)
        path = payload / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o444)
    for directory, dirs, names in os.walk(payload, topdown=False):
        with runtime.directory(directory) as fd:
            os.fchmod(fd, 0o555)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-profile", choices=("original", "uncensored"), default="original")
    args = parser.parse_args()
    try:
        deadline = time.monotonic() + 60
        producer = identity(deadline)
        files = prepare(args.wheelhouse, deadline, model_profile=args.model_profile)
        require(identity(deadline) == producer
                and runtime.digest(files["worker.py"]) == producer["files"][SOURCES[1]])
        publish(args.output, files)
        print(runtime.digest(files["trial.json"]))
        return 0
    except Exception:
        print("provider payload rejected", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
