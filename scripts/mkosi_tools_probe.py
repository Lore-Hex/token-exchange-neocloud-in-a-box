#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fixed startup-only worker for the separately reviewed offline tools sandbox."""
import sys

if __name__ == "__main__" and not (
        sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
    sys.exit("Isolated no-site tools probe required.")

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import types

# --version is documented in each exact package's man page; see the task doc.
COMMANDS = (
    ("python", ("/usr/bin/python3.12", "-I", "-S", "-B", "--version")),
    ("apt", ("/usr/bin/apt-get", "--version")),
    ("dpkg", ("/usr/bin/dpkg", "--version")),
    ("bubblewrap", ("/usr/bin/bwrap", "--version")),
    ("tar", ("/usr/bin/bsdtar", "--version")),
    ("repart", ("/usr/bin/systemd-repart", "--version")),
    ("awk", ("/usr/bin/awk", "--version")),
)
ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C"}
SECONDS, MAX_OUTPUT = 60, 64 << 10
FALSE_CLAIMS = ("package_installation_executed", "maintainer_scripts_executed",
                "mkosi_executed", "namespaces_validated", "abi_validated",
                "installable_verified", "bootable", "deployable")


def require(condition):
    if not condition:
        raise ValueError("tools-probe-rejected")


def read(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and 0 < before.st_size <= limit)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_nlink,
                              s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        require(len(raw) == before.st_size and identity(before) == identity(os.fstat(fd)))
        return raw
    finally:
        os.close(fd)


def validate_input(value):
    require(isinstance(value, dict) and set(value) == {
        "schema", "git_sha", "tools_receipt_sha256", "snapshot_sha256"})
    require(value["schema"] == "nib.mkosi-tools-probe-input/v1"
            and isinstance(value["git_sha"], str)
            and re.fullmatch("[0-9a-f]{40}", value["git_sha"]))
    for name in ("tools_receipt_sha256", "snapshot_sha256"):
        require(isinstance(value[name], str) and re.fullmatch("[0-9a-f]{64}", value[name]))


def probe(inputs, execute):
    validate_input(inputs)
    report = {"schema": "nib.mkosi-tools-probe/v1", "git_sha": inputs["git_sha"],
              "tools_receipt_sha256": inputs["tools_receipt_sha256"],
              "result": "fail", "stage": "preflight", "checks": [],
              "programs_attempted": 0, **{name: False for name in FALSE_CLAIMS}}
    deadline = time.monotonic() + SECONDS
    try:
        for name, argv in COMMANDS:
            report["stage"] = name
            remaining = min(10, deadline - time.monotonic())
            require(remaining > 0)
            report["programs_attempted"] += 1
            result = execute(list(argv), env=dict(ENV), timeout=remaining,
                             max_output_bytes=MAX_OUTPUT, stdin=subprocess.DEVNULL)
            result.check_returncode()
            require(time.monotonic() < deadline and isinstance(result.stdout, bytes)
                    and 0 < len(result.stdout) <= MAX_OUTPUT and result.stdout.strip()
                    and result.stderr == b"")
            # Do not retain process output: only a bounded, fixed-class digest.
            report["checks"].append({"program": name, "result": "pass",
                                     "stdout_bytes": len(result.stdout),
                                     "stdout_sha256": hashlib.sha256(result.stdout).hexdigest()})
        report.update(stage="complete", result="pass")
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return report


def main():
    require(len(sys.argv) == 1 and sys.platform == "linux" and os.geteuid() == 65534)
    inputs = json.loads(read(Path("/nib/input.json"), 16384))
    validate_input(inputs)
    raw = read(Path("/nib/snapshot.py"), 1 << 20)
    require(hashlib.sha256(raw).hexdigest() == inputs["snapshot_sha256"])
    helper = types.ModuleType("tools_probe_processes")
    exec(compile(raw, "/nib/snapshot.py", "exec", dont_inherit=True), helper.__dict__)
    report = probe(inputs, helper.execute_process_group)
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError):
        sys.exit("Tools probe rejected before execution.")
