#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Import one reviewed historical diagnostic; never execute or retry tests."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = "scripts/record_go_test_diagnostic.py"
SCRUB = "scripts/scrub-secrets.sh"
OBSERVED_REVISION = "1e5273062a49fb45f643a984f436bc300f777d70"
INPUTS = {
    "original-failed-full-check.log": "3d9f4d6cbf07870c47bd501bb2b0cc313e5516c3c63f9de8c36777ae4c33a96c",
    "full-breadth-once/01-default-parallel.log": "374d568804e1381305676d9f3f8a6c2d6ae78c7a031fac6d8bc94402c893fc38",
    "full-breadth-once/02-serialized.log": "af1286096ac9f834c0fe488d667369e6d9b9d9746a2f43419283430a5cee7878",
    "full-breadth-once/invocation.json": "81251a1eddc96bead1e322cb66e8d352cb4d71d55e3bda5bf64ac6363ecc57b6",
    "full-breadth-once/result.json": "65ae413086e1d21b5def0ab0b67a82458dabfa6d981bff06c204ae70e00b7791",
    "full-breadth-once/source-before.json": "39e83aea088c3c1b66e699973897ff177cca694a5bfe87f7d11a806ddd5ce322",
    "full-breadth-once/source-after.json": "39e83aea088c3c1b66e699973897ff177cca694a5bfe87f7d11a806ddd5ce322",
    "full-breadth-once/trace-summary.json": "7c3d9fa07ce302ff69f80d5acdc5dc59690f8ed3d12ca139491dd2db42e74c05",
}
ENV = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent",
       "LANG": "C", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_NO_REPLACE_OBJECTS": "1",
       "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": "", "GIT_TERMINAL_PROMPT": "0"}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def checked(raw, expected):
    if not 0 < len(raw) <= 1 << 20 or digest(raw) != expected:
        raise ValueError("historical input rejected")
    return raw


def read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("regular input required")
        raw = stream.read((1 << 20) + 1)
    if not 0 < len(raw) <= 1 << 20:
        raise ValueError("input size rejected")
    return raw


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], env=ENV,
                          stdin=subprocess.DEVNULL, capture_output=True,
                          check=True, timeout=10).stdout


def committed():
    revision = git("rev-parse", "HEAD").decode("ascii").strip()
    for name in (SCRIPT, SCRUB):
        if git("show", revision + ":" + name) != read(ROOT / name):
            raise ValueError("committed importer and scrubber required")
    return revision


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def publish(output, artifacts):
    output.mkdir(mode=0o700)
    # On any failure retain partial evidence; never replace an existing directory.
    for name, raw in artifacts.items():
        with (output / name).open("xb") as stream:
            stream.write(raw)


def record(source, deny, output):
    started = datetime.now(timezone.utc).isoformat()
    revision = committed()
    artifacts, hashes = {}, {}
    for relative, expected in INPUTS.items():
        raw = checked(read(source / relative), expected)
        scrubbed = subprocess.run(
            ["sh", str(ROOT / SCRUB), "--deny-file", str(deny)], input=raw,
            env={**ENV, "NIB_BIN": os.environ["NIB_BIN"]}, capture_output=True,
            check=True, timeout=10).stdout
        name = Path(relative).name
        # Keep full source listings private; their exact before/after digests
        # bind the closure without publishing unrelated credential-named hashes.
        published = name not in ("source-before.json", "source-after.json")
        if published:
            artifacts[name] = scrubbed
        hashes[name] = {"input_sha256": expected, "scrubbed_sha256": digest(scrubbed),
                        "published": published}
    if committed() != revision:
        raise ValueError("importer changed")
    artifacts["output.log"] = encoded({
        "operation": "historical-import-only", "observed_revision": OBSERVED_REVISION,
        "importer_revision": revision, "files": hashes,
        "original_full_gate": "fail", "observation_exit_codes": [1, 0],
        "new_test_execution": False, "hardware_validated": False,
    })
    artifacts["run.json"] = encoded({
        "schema": "nib.evidence/v1", "phase": "P2", "task": "T3", "git_sha": revision,
        "command": "NIB_BIN=<reviewed-nib> python3 -I " + SCRIPT
                   + " --input <private-input> --deny-file <private-deny> --output <new-evidence>",
        "started_utc": started, "ended_utc": datetime.now(timezone.utc).isoformat(),
        "host": {"provider": "local", "machine": "historical-import", "gpus": "none",
                 "driver": None, "cuda": None},
        "result": "pass", "execution": "software", "hardware_validated": False,
        "notes": "PASS means hash-checked historical import only, not a test pass. Original full "
                 "gate failed; default diagnostic exited 1 and serialized diagnostic exited 0. "
                 "Observed test-only instrumented source based on " + OBSERVED_REVISION + ". "
                 "Original execution times/commands remain in invocation.json and result.json. "
                 "Supports concurrency-sensitive startup latency, not a definitive OS cause. "
                 "One ordered pair has cache/order/host-activity confounders. No new tests, "
                 "GPU, paid calls, production changes or benchmark qualification.",
    })
    publish(output, artifacts)


if __name__ == "__main__":
    if not sys.flags.isolated:
        sys.exit("Use python3 -I scripts/record_go_test_diagnostic.py")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--deny-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        record(args.input, args.deny_file, args.output)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        sys.exit("Historical diagnostic import failed; any partial output retained.")
