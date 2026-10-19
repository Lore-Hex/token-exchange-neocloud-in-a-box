#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run the local Qwen pilot from committed source and retain sanitized evidence."""

import sys

if __name__ == "__main__" and not sys.flags.isolated:
    sys.exit("Run this recorder with python -I to exclude ambient Python imports.")

import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import tempfile
import types

ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP_SOURCE = Path(__file__).read_bytes()


def load_source(name, path):
    # Normal imports may execute ignored timestamp-valid __pycache__ code.
    source = path.read_bytes()
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module, source


snapshot, SNAPSHOT_SOURCE = load_source("pilot_snapshot", ROOT / "scripts/e2e/snapshot.py")
controlled_env, git = snapshot.controlled_env, snapshot.git
snapshot_commit, validate_snapshot = snapshot.snapshot_commit, snapshot.validate_snapshot


def validate_bootstrap(source):
    for path, expected in (("scripts/record-qwen-pilot.py", BOOTSTRAP_SOURCE),
                           ("scripts/e2e/snapshot.py", SNAPSHOT_SOURCE)):
        if (source / path).read_bytes() != expected:
            raise ValueError("bootstrap differs from committed source")


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def execute_pilot(args, *, env, cwd, timeout=1800):
    # Timeouts/interruption must also stop compiler and server grandchildren.
    process = subprocess.Popen(args, env=env, cwd=cwd, start_new_session=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)
        process.stdout.close()
        process.stderr.close()


def validate_record(record, nib, work, env):
    # The repository's schema is authoritative; do not publish a run that only
    # matches a hand-maintained Python approximation of that contract.
    root = work / "evidence-validation"
    destination = root / "evidence/p3/candidate"
    destination.mkdir(parents=True)
    (destination / "run.json").write_text(json.dumps(record) + "\n")
    subprocess.run([str(nib), "lint", str(root)], env=env, check=True,
                   capture_output=True, timeout=20)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--slug", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.slug) or len(args.slug) > 64:
        raise ValueError("invalid evidence slug")
    started = now()
    with tempfile.TemporaryDirectory(prefix="nib-qwen-proof-") as temporary:
        work = Path(temporary)
        env = controlled_env(work / "control")
        if git(ROOT, env, "status", "--porcelain", stdout=subprocess.PIPE).stdout:
            raise ValueError("commit source before recording")
        source = work / "source"
        revision, expected, archive_digest = snapshot_commit(ROOT, source, work / "control", env)
        validate_bootstrap(source)
        try:
            result = execute_pilot([sys.executable, "-I", "-B", str(source / "scripts/pilot/run.py"),
                                    "--inputs", str(args.inputs.resolve())], env=env, cwd=source)
            if len(result.stdout) > 65536:
                raise ValueError("oversized pilot report")
            report = json.loads(result.stdout)
            if (report.get("confidential") is not False or report.get("hardware_validated") is not False
                    or report.get("execution") != "real-cpu-model"
                    or report.get("result") not in {"pass", "fail"}
                    or (report.get("result") == "pass") != (result.returncode == 0)):
                raise ValueError("invalid pilot result")
        except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
            report = {"result": "fail", "execution": "real-cpu-model", "confidential": False,
                      "hardware_validated": False, "failure_code": "pilot-execution-or-report-failed"}
        validate_snapshot(source, expected)
        # Reuse the exclusive, traversal-resistant evidence scaffolder.
        nib = work / "nib"
        subprocess.run(["go", "build", "-trimpath", "-buildvcs=false", "-o", str(nib), "./cmd/nib"],
                       cwd=source, env=env, check=True, capture_output=True, timeout=180)
        record = {
            "schema": "nib.evidence/v1", "phase": "P3", "task": "T7", "git_sha": revision,
            "command": "python3 -I scripts/record-qwen-pilot.py --inputs " + str(args.inputs.resolve())
                       + " --slug " + args.slug,
            "started_utc": started, "ended_utc": now(),
            "host": {"provider": "local", "machine": platform.system().lower() + "/" + platform.machine(),
                     "gpus": "none", "driver": None, "cuda": None},
            "result": report["result"], "execution": "software", "hardware_validated": False,
            "notes": "Local-only real Qwen inference through standard TLS (tee=none). No fake engine, "
                     "cloud resources, public endpoint, billing integration or confidential-compute claim. "
                     "Source archive SHA-256: " + archive_digest,
        }
        validate_record(record, nib, work, env)
        created = subprocess.run([str(nib), "evidence", "new", "P3", args.slug], cwd=ROOT,
                                 env=env, check=True, capture_output=True, text=True, timeout=20)
        relative = created.stdout.strip()
        if not re.fullmatch(r"evidence/p3/\d{4}-\d{2}-\d{2}-" + args.slug, relative):
            raise ValueError("unexpected evidence path")
        destination = ROOT / relative
        (destination / "output.log").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        pending = destination / "run.json.pending"
        with pending.open("x") as stream:
            stream.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
        pending.replace(destination / "run.json")
        print(relative)
        return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        sys.exit("Pilot evidence recording failed; no successful result may be inferred.")
