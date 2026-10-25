#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Offline macOS observer payload; never a Linux serving-image checker payload."""

import sys
from pathlib import Path

if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        sys.exit("Use python3 -I -S -B scripts/external_checker_payload.py")
    sys.path.insert(0, str(Path(__file__).absolute().parent))

import argparse
from contextlib import contextmanager
import os
import platform
import re
import stat
import sysconfig
import time

import provider_conformance as prep

ROOT, runtime = prep.ROOT, prep.runtime
require, digest, encoded = prep.require, runtime.digest, runtime.encoded
PROFILE = "macos-arm64-cpython-3.12.13"
SCHEMA = "nib.external-checker-payload/v1"
TARGET = {"profile": PROFILE, "implementation": "cpython", "python_full_version": "3.12.13",
          "abi": "cp312", "sys_platform": "darwin", "platform_machine": "arm64",
          "minimum_macos": "11.0", "soabi": "cpython-312-darwin", "purpose": "external-observer-only"}
SOURCES = ("scripts/external_checker_payload.py", "scripts/check-selected-service.py",
           "scripts/selected_service_conformance.py", "scripts/selected_service_protocol.py")


def identity(deadline):
    result = prep.identity(deadline)
    current = runtime.tau.committed_identity(deadline, extra_files=SOURCES)
    require(current["git_sha"] == result["git_sha"])
    result["files"].update(current["files"])
    return result


def wheel_target(filename):
    # PyPA platform tags; deliberately only the two native tags in this lock.
    require(type(filename) is str and re.fullmatch(r"[A-Za-z0-9_.]+(?:-[A-Za-z0-9_.]+){4}\.whl", filename))
    tags = runtime.expand_tags(["-".join(filename[:-4].split("-")[-3:])])
    pure = {("py2", "none", "any"), ("py3", "none", "any"), ("py312", "none", "any")}
    native = {("cp312", "cp312", "macosx_10_13_universal2"), ("cp312", "cp312", "macosx_11_0_arm64")}
    require((tags <= pure and any(py in ("py3", "py312") for py, _, _ in tags)) or tags <= native)


def check_runtime():
    require(sys.platform == "darwin" and sys.implementation.name == "cpython"
            and sys.version_info[:3] == (3, 12, 13) and platform.machine() == "arm64"
            and sysconfig.get_config_var("SOABI") == TARGET["soabi"])
    version = platform.mac_ver()[0]
    require(re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", version) and int(version.split(".")[0]) >= 11)


def wheel_records(wheels, deadline):
    packages = prep.requirements(runtime.read_file(ROOT / "scripts/e2e/requirements.lock",
                                                   1 << 20, deadline))
    require(type(wheels) is dict and set(wheels) == set(packages))
    for name, row in wheels.items():
        require(type(row) is dict and set(row) == {"filename", "version", "bytes", "sha256"})
        wheel_target(row["filename"])
        parts = row["filename"].split("-")
        require(parts[0].replace("_", "-").lower() == name and parts[1] == row["version"] == packages[name][0]
                and type(row["bytes"]) is int and 0 < row["bytes"] <= 16 << 20
                and type(row["sha256"]) is str and row["sha256"] in packages[name][1])
        if name == "trustedrouter-provider-check":
            require(row["sha256"] == prep.CHECKER_SHA)


def add_wheel(files, raw, expected, deadline):
    wheel_target(expected["filename"])
    runtime.check_wheel_archive(raw, expected, deadline)
    prep.unpack_wheel(files, raw, deadline)


def prepare(wheelhouse, deadline, producer):
    files, wheels = {}, {}
    for raw, expected in prep.wheel_inputs(wheelhouse, deadline):
        add_wheel(files, raw, expected, deadline)
        wheels[expected["filename"].split("-")[0].replace("_", "-").lower()] = expected
    wheel_records(wheels, deadline)
    plan = {"schema": SCHEMA, "target": TARGET, "producer": producer,
        "requirements_sha256": prep.LOCK_SHA, "checker_wheel_sha256": prep.CHECKER_SHA,
        "model_profile": prep.source(prep.PROFILE_SOURCE, prep.PROFILE_SHA).select("uncensored"),
        "wheels": wheels, "files": {name: {"bytes": len(raw), "sha256": digest(raw)}
                                     for name, raw in sorted(files.items())}}
    files["trial.json"] = encoded(plan)
    return files


def verify(root, expected, deadline):
    check_runtime()
    require(root.is_absolute() and ".." not in root.parts and type(expected) is str
            and re.fullmatch("[0-9a-f]{64}", expected))
    with runtime.directory(root):
        raw = runtime.read_file(root / "trial.json", 1 << 20, deadline)
        require(digest(raw) == expected)
        plan = prep.support.decode(raw)
        require(type(plan) is dict and set(plan) == {"schema", "target", "producer", "requirements_sha256",
            "checker_wheel_sha256", "model_profile", "wheels", "files"}
            and plan["schema"] == SCHEMA and plan["target"] == TARGET
            and plan["requirements_sha256"] == prep.LOCK_SHA and plan["checker_wheel_sha256"] == prep.CHECKER_SHA
            and plan["producer"] == identity(deadline)
            and plan["model_profile"] == prep.source(prep.PROFILE_SOURCE, prep.PROFILE_SHA).select("uncensored")
            and type(plan["files"]) is dict and 0 < len(plan["files"]) <= 4000
            and all(name.startswith("packages/") for name in plan["files"]))
        wheel_records(plan["wheels"], deadline)
        prep.support.verify_tree(root, plan["files"], deadline)
        for directory, dirs, names in os.walk(root):
            require(time.monotonic() < deadline and stat.S_IMODE(os.lstat(directory).st_mode) == 0o555)
            for name in names:
                require(stat.S_IMODE(os.lstat(Path(directory) / name).st_mode) == 0o444)
        require(sum(row["bytes"] for row in plan["files"].values()) <= 64 << 20)
        return plan


@contextmanager
def checker_modules(root, deadline, expected_sha256):
    import selected_service_conformance as subset
    if sys.platform == "darwin":
        with subset.service.identity.worker.alarm(deadline):
            verify(root, expected_sha256, deadline)
        with subset.imported_checker(root) as checker:
            yield checker
    else:
        # The original verifier still rejects this distinct macOS schema.
        with subset.checker_modules(root, deadline, expected_sha256) as checker:
            yield checker


def main(argv=None):
    try:
        class QuietParser(argparse.ArgumentParser):
            def error(self, message):
                raise ValueError("external checker payload rejected")
        parser = QuietParser(description=__doc__, allow_abbrev=False)
        parser.add_argument("--profile", required=True, choices=(PROFILE,))
        parser.add_argument("--wheelhouse", required=True, type=Path)
        parser.add_argument("--output", required=True, type=Path)
        args = parser.parse_args(argv)
        require(args.profile == PROFILE)
        check_runtime()
        deadline = time.monotonic() + 60
        with prep.support.alarm(deadline):
            producer = identity(deadline)
            files = prepare(args.wheelhouse, deadline, producer)
            require(identity(deadline) == producer)
            prep.publish(args.output, files)
            expected = digest(files["trial.json"])
            verify(args.output / "payload", expected, deadline)
        print(expected)
        return 0
    except BaseException:
        print("external checker payload rejected", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
