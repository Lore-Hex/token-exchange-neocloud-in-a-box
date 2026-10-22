#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare only the fixed original tau CPU prompt probe, without model execution."""
import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B scripts/prepare-tau-cpu-probe-native.py")

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import types

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = "scripts/prepare-tau-cpu-probe-native.py"
native = types.ModuleType("native_cpu_inputs")
native.__file__ = str(ROOT / "scripts/prepare-tau-cpu-parity-native.py")
exec(compile(Path(native.__file__).read_bytes(), native.__file__, "exec", dont_inherit=True), native.__dict__)
rec, require = native.rec, native.require
ARCHIVE_BYTES = 92081935  # Exact pinned fc0055dc codeload archive, SHA checked below.


def plan(runtime):
    raw = (runtime.ROOT / runtime.CATALOG).read_bytes()
    require(rec.digest(raw) == runtime.CATALOG_SHA)
    catalog = json.loads(raw)
    archive = ("https://codeload.github.com/sierra-research/tau2-bench/tar.gz/" + runtime.REVISION,
               ARCHIVE_BYTES, runtime.ARCHIVE_SHA)
    wheels = {}
    for row in catalog["wheels"].values():
        require(type(row["filename"]) is str and rec.re.fullmatch(r"[A-Za-z0-9_.-]+\.whl", row["filename"])
                and row["url"].startswith("https://files.pythonhosted.org/packages/")
                and row["url"].endswith("/" + row["filename"])
                and type(row["bytes"]) is int and 0 < row["bytes"] <= runtime.WHEEL_LIMIT
                and rec.re.fullmatch("[0-9a-f]{64}", row["sha256"]) and row["filename"] not in wheels)
        wheels[row["filename"]] = (row["url"], row["bytes"], row["sha256"])
    require(0 < len(wheels) <= 128 and sum(row[1] for row in wheels.values()) <= runtime.WHEELS_LIMIT)
    return archive, wheels


def prepare(directory, source, work, env, snapshot, deadline):
    runner = rec.load(source / "scripts/tau_trial.py", "cpu_probe_setup")
    archive, wheels = plan(runner.runtime)
    cache = work / "inputs"
    cache.mkdir(mode=0o700)
    fd = os.open(cache, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        native.fetch(fd, "tau.tar.gz", archive, env, snapshot, deadline)
        for name, spec in wheels.items():
            native.fetch(fd, name, spec, env, snapshot, deadline)
    finally:
        os.close(fd)
    files, contract = runner.prepare(cache / "tau.tar.gz", cache, None, None, None,
        min(deadline, time.monotonic() + runner.runtime.SECONDS), parent_pipe=True, support_probe=True)
    runner.publish(directory, files)
    receipt = {"schema": "nib.tau-cpu-probe-setup/v1", "git_sha": contract["producer"]["git_sha"],
        "plan_sha256": rec.digest(files["trial.json"]), "source_archive_sha256": archive[2],
        "runtime_lock_sha256": runner.runtime.CATALOG_SHA, "wheels": len(wheels),
        "target": "scripted", "pricing": "not-measured", "upstream_executed": False,
        "quality_eligible": False, "confidential": False, "live_admission": False}
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        rec.write(fd, "setup.json", receipt)
        os.fsync(fd)
    finally:
        os.close(fd)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rec.host("github-actions")
    require(os.environ.get("GITHUB_REF") == "refs/heads/main")
    os.umask(0o077)
    deadline = time.monotonic() + native.PREP_SECONDS
    raw = (ROOT / rec.SNAPSHOT).read_bytes()
    require(rec.digest(raw) == rec.SNAPSHOT_SHA)
    snapshot = rec.load(ROOT / rec.SNAPSHOT, "cpu_probe_snapshot", raw)
    with tempfile.TemporaryDirectory(prefix="nib-cpu-probe-prepare-") as temporary:
        work = Path(temporary)
        env = snapshot.controlled_env(work / "control")
        env.update(GIT_ALLOW_PROTOCOL="", GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
        require(not snapshot.git(ROOT, env, "status", "--porcelain", stdout=subprocess.PIPE).stdout)
        source = work / "source"
        _, files, _ = snapshot.snapshot_commit(ROOT, source, work / "control", env)
        require((source / SCRIPT).read_bytes() == Path(__file__).read_bytes()
                and (source / rec.SCRIPT).read_bytes() == rec.BOOTSTRAP
                and (source / native.SCRIPT).read_bytes() == Path(native.__file__).read_bytes())
        prepare(args.output.absolute(), source, work, env, snapshot, deadline)
        snapshot.validate_snapshot(source, files)
    print("Pinned CPU prompt probe inputs verified; no model execution performed.")


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        if isinstance(error, SystemExit) and type(error.code) is int:
            raise
        sys.exit("CPU probe preparation failed; no model execution performed.")
