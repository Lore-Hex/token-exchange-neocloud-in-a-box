#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Record the committed full container harness, including verified cleanup."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]


def utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


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
        path = directory / (name + ".json")
        if path.is_symlink() or path.stat().st_size > 1 << 20:
            raise ValueError("invalid harness artifact")
        records[name] = json.loads(path.read_text())
        if not isinstance(records[name], dict):
            raise ValueError("invalid harness artifact object")
    return records


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
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT):
        parser.error("commit source changes before recording evidence")
    output.mkdir(parents=True, exist_ok=False)
    run = {
        "schema": "nib.evidence/v1", "phase": "P1", "task": "T3",
        "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "command": "python3 scripts/e2e/prove.py --output " + relative.as_posix(),
        "started_utc": utc(), "ended_utc": None,
        "host": {"provider": "local", "machine": f"{platform.system()} {platform.release()} {platform.machine()}",
                 "gpus": "none used", "driver": None, "cuda": None},
        "execution": "fake", "hardware_validated": False, "result": "fail",
        "notes": "Runs make test-e2e with merged, hash-pinned trcs. Local Docker only: runtime network none, no devices, mounts, published ports or API secrets. Explicit fake CPU/GPU evidence; no confidential or hardware claim. Success requires verified container/image absence. Build cache is retained. CI acceptance is separate.",
    }
    logs = []
    try:
        built = subprocess.run(["make", "build"], cwd=ROOT, capture_output=True, timeout=300)
        logs.extend([built.stdout, built.stderr])
        if built.returncode:
            raise ValueError("scrubber build failed")
        # run.py owns bounded execution and cleanup. Do not kill its finally
        # block with an outer timeout while Docker resources may still exist.
        tested = subprocess.run(["make", "test-e2e"], cwd=ROOT, capture_output=True)
        logs.extend([tested.stdout, tested.stderr])
        records = collect_results(tested.stdout)
        for name, record in records.items():
            write_json(output / (name + ".json"), record)
        if records["inputs"].get("git_sha") != run["git_sha"]:
            raise ValueError("source revision changed during test")
        result = records["result"]
        if tested.returncode or result.get("result") != "pass" or result.get("cleanup", {}).get("result") != "pass":
            raise ValueError("full harness or cleanup failed")
        if records["inputs"].get("scope") != "full-fake-node":
            raise ValueError("engine-only result is not full evidence")
        run["result"] = "pass"
    except (ValueError, OSError, subprocess.SubprocessError):
        logs.append(b"FAIL: full container proof; inspect bounded result and cleanup records.\n")
    finally:
        try:
            with tempfile.TemporaryDirectory(prefix="nib-e2e-scrub-") as temporary:
                deny = Path(temporary) / "deny.txt"
                deny.write_text(str(ROOT) + "\n" + str(Path.home()) + "\n")
                deny.chmod(0o600)
                scrubbed = subprocess.run(["sh", "scripts/scrub-secrets.sh", "--deny-file", str(deny)], cwd=ROOT,
                                         env={**os.environ, "NIB_BIN": str(ROOT / "build/nib")},
                                         input=b"".join(logs), capture_output=True, timeout=30)
                if scrubbed.returncode:
                    raise ValueError("output scrubbing failed")
                (output / "output.log").write_bytes(scrubbed.stdout)
        except (ValueError, OSError, subprocess.SubprocessError):
            run["result"] = "fail"
            run["notes"] += " Output scrubbing failed; raw output was not saved."
        run["ended_utc"] = utc()
        write_json(output / "run.json", run)
    print("Full software-only container proof:", run["result"], relative.as_posix())
    return 0 if run["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
