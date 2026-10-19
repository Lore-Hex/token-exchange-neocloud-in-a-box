#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Record the committed full container harness, including verified cleanup."""

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import platform
import pwd
import re
import stat
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]


def load_module(path):
    spec = importlib.util.spec_from_file_location("e2e_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


snapshot = load_module(Path(__file__).with_name("snapshot.py"))


def utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def read_record(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 1 << 20:
                raise ValueError("artifact must be a bounded regular file")
            data = source.read((1 << 20) + 1)
        if len(data) > 1 << 20:
            raise ValueError("oversized artifact")
        record = json.loads(data)
        if not isinstance(record, dict):
            raise ValueError("invalid artifact object")
        return record
    except OSError:
        raise ValueError("cannot read artifact safely") from None


def collect_results(stdout, root=ROOT):
    lines = stdout.decode().splitlines()
    prefix = "Software-only test artifacts: "
    if not lines or not lines[-1].startswith(prefix):
        raise ValueError("missing harness artifacts")
    directory = Path(lines[-1][len(prefix):])
    relative = directory.relative_to(root / "build/e2e")
    if len(relative.parts) != 1 or not re.fullmatch("[0-9a-f]{12}", relative.name):
        raise ValueError("invalid harness artifact directory")
    if directory.resolve() != directory:
        raise ValueError("symlinked harness artifacts")
    records = {}
    for name in ("inputs", "result"):
        records[name] = read_record(directory / (name + ".json"))
    return records


def validate_inputs(inputs, revision, expected_digest, pins):
    if inputs.get("git_sha") != revision or inputs.get("source_sha256") != expected_digest:
        raise ValueError("harness source is not the committed snapshot")
    if inputs.get("scope") != "full-fake-node" or inputs.get("pins") != pins:
        raise ValueError("harness inputs differ from the full pinned snapshot")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    relative = Path(args.output)
    if relative.is_absolute() or ".." in relative.parts or relative.parts[:2] != ("evidence", "p1"):
        parser.error("output must be below evidence/p1")
    output = ROOT / relative
    if output.resolve() != output:
        parser.error("output must not traverse symlinks")
    with tempfile.TemporaryDirectory(prefix="nib-e2e-proof-") as temporary:
        control = Path(temporary).resolve()
        env = snapshot.controlled_env(control)
        revision = snapshot.git(ROOT, env, "rev-parse", "--verify", "HEAD^{commit}", stdout=subprocess.PIPE).stdout.decode().strip()
        output.mkdir(parents=True, exist_ok=False)
        return record_proof(relative, output, control, env, revision)


def record_proof(relative, output, control, env, revision):
    run = {
        "schema": "nib.evidence/v1", "phase": "P1", "task": "T3",
        "git_sha": revision,
        "command": "python3 scripts/e2e/prove.py --output " + relative.as_posix(),
        "started_utc": utc(), "ended_utc": None,
        "host": {"provider": "local", "machine": f"{platform.system()} {platform.release()} {platform.machine()}",
                 "gpus": "none used", "driver": None, "cuda": None},
        "execution": "fake", "hardware_validated": False, "result": "fail",
        "notes": "Runs make test-e2e from a private exact Git archive with replacement objects disabled, controlled child environments, isolated Go caches and module verification. Original Git revision and expected/actual build-context hashes are bound to the committed tree. Local Docker Unix socket only; runtime network none, no devices, mounts, published ports or API secrets. Explicit fake CPU/GPU evidence; no confidential or hardware claim. Success requires verified container/image absence. Docker build cache is retained. CI acceptance is separate.",
    }
    logs = []
    source = control / "source"
    expected = None
    trusted_scrubber = False
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    try:
        _, expected, archive_hash = snapshot.snapshot_commit(ROOT, source, control, env, revision)
        # The bootstrap recorder and its helper must themselves be committed.
        for name in ("prove.py", "snapshot.py"):
            if (ROOT / "scripts/e2e" / name).read_bytes() != (source / "scripts/e2e" / name).read_bytes():
                raise ValueError("recorder changes must be committed first")
        runner = load_module(source / "scripts/e2e/run.py")
        expected_digest = snapshot.context_digest(source, runner.source_paths(source, env=env))
        pins = json.loads((source / "scripts/e2e/pins.json").read_text())
        write_json(output / "snapshot.json", {"git_sha": revision, "git_archive_sha256": archive_hash,
                                              "expected_source_sha256": expected_digest,
                                              "replacement_objects": False, "working_tree_used": False})
        env = {**env, "DOCKER_HOST": snapshot.local_docker_endpoint(env, home)}
        for command in (["go", "mod", "download"], ["go", "mod", "verify"], ["make", "build"]):
            snapshot.validate_snapshot(source, expected)
            built = subprocess.run(command, cwd=source, env=env, capture_output=True, timeout=300)
            logs.extend([built.stdout, built.stderr])
            if built.returncode:
                raise ValueError("verified scrubber build failed")
        snapshot.validate_snapshot(source, expected)
        trusted_scrubber = True
        # run.py owns bounded execution and cleanup. Do not kill its finally
        # block with an outer timeout while Docker resources may still exist.
        tested = subprocess.run(["make", "test-e2e"], cwd=source, env=env, capture_output=True)
        logs.extend([tested.stdout, tested.stderr])
        snapshot.validate_snapshot(source, expected)
        records = collect_results(tested.stdout, source)
        for name, record in records.items():
            write_json(output / (name + ".json"), record)
        validate_inputs(records["inputs"], revision, expected_digest, pins)
        result = records["result"]
        if tested.returncode or result.get("result") != "pass" or result.get("cleanup", {}).get("result") != "pass":
            raise ValueError("full harness or cleanup failed")
        run["result"] = "pass"
    except (ValueError, OSError, subprocess.SubprocessError):
        logs.append(b"FAIL: full container proof; inspect bounded result and cleanup records.\n")
    finally:
        try:
            if not trusted_scrubber:
                raise ValueError("no trusted scrubber")
            snapshot.validate_snapshot(source, expected)
            deny = control / "deny.txt"
            deny.write_text(str(ROOT) + "\n" + str(home) + "\n" + str(control) + "\n")
            deny.chmod(0o600)
            scrubbed = subprocess.run(["sh", "scripts/scrub-secrets.sh", "--deny-file", str(deny)], cwd=source,
                                     env={**env, "NIB_BIN": str(source / "build/nib")},
                                     input=b"".join(logs), capture_output=True, timeout=30)
            if scrubbed.returncode:
                raise ValueError("output scrubbing failed")
            (output / "output.log").write_bytes(scrubbed.stdout)
        except (ValueError, OSError, subprocess.SubprocessError):
            run["result"] = "fail"
            run["notes"] += " Output scrubbing failed; raw output was not saved."
            (output / "output.log").write_text("FAIL: no trusted scrubbed operational output; raw output omitted.\n")
        run["ended_utc"] = utc()
        write_json(output / "run.json", run)
    print("Full software-only container proof:", run["result"], relative.as_posix())
    return 0 if run["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
