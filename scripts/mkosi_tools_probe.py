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
SANDBOX_SUCCESS = b"NIB_SANDBOX_OK\n"
SANDBOX_FILES = {"/nib/mkosi-sandbox.py": "sandbox_sha256",
                 "/nib/namespace-check.py": "checker_sha256"}
FAILURE_KINDS = ("timeout", "permission-denied", "process-exit", "startup-error", "invalid-result")


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
    require(isinstance(value, dict))
    sandbox = value.get("sandbox") is True
    extra = {"sandbox", *SANDBOX_FILES.values()} if sandbox else set()
    require(set(value) == {"schema", "git_sha", "tools_receipt_sha256", "snapshot_sha256", *extra})
    require(value["schema"] == "nib.mkosi-tools-probe-input/" + ("v2" if sandbox else "v1")
            and isinstance(value["git_sha"], str)
            and re.fullmatch("[0-9a-f]{40}", value["git_sha"]))
    for name in ("tools_receipt_sha256", "snapshot_sha256", *(SANDBOX_FILES.values() if sandbox else ())):
        require(isinstance(value[name], str) and re.fullmatch("[0-9a-f]{64}", value[name]))


def sandbox_command():
    # Exact flags from pinned mkosi 27.1 sandbox.py. The child checks that
    # --unshare-net really changed namespaces; upstream permits skipping it.
    original = [os.readlink("/proc/self/ns/" + name) for name in ("user", "mnt", "net", "ipc")]
    argv = ["/usr/bin/python3.12", "-I", "-S", "-B", "/nib/mkosi-sandbox.py"]
    for path in ("/usr", "/lib", "/lib64", "/nib", "/proc"):
        argv += ["--ro-bind", path, path]
    return argv + ["--dev", "/dev", "--tmpfs", "/tmp", "--unshare-net", "--unshare-ipc",
                   "--become-root", "/usr/bin/python3.12", "-I", "-S", "-B",
                   "/nib/namespace-check.py", *original]


def probe(inputs, execute):
    validate_input(inputs)
    sandbox = inputs.get("sandbox") is True
    report = {"schema": "nib.mkosi-tools-probe/v1", "git_sha": inputs["git_sha"],
              "tools_receipt_sha256": inputs["tools_receipt_sha256"],
              "result": "fail", "stage": "preflight", "checks": [],
              "programs_attempted": 0, **{name: False for name in FALSE_CLAIMS}}
    if sandbox:
        report.update(schema="nib.mkosi-tools-probe/v2", sandbox=True, mkosi_build_executed=False,
                      failure_kind=None)
    deadline = time.monotonic() + SECONDS
    try:
        for name, argv in (*COMMANDS, *((("sandbox", None),) if sandbox else ())):
            report["stage"] = name
            remaining = min(10, deadline - time.monotonic())
            require(remaining > 0)
            if name == "sandbox":
                argv = sandbox_command()
            report["programs_attempted"] += 1
            if name == "sandbox":
                report["mkosi_executed"] = True
            result = execute(list(argv), env=dict(ENV), timeout=remaining,
                             max_output_bytes=MAX_OUTPUT, stdin=subprocess.DEVNULL)
            result.check_returncode()
            require(time.monotonic() < deadline and isinstance(result.stdout, bytes)
                    and 0 < len(result.stdout) <= MAX_OUTPUT and result.stdout.strip()
                    and result.stderr == b"")
            if name == "sandbox":
                require(result.stdout == SANDBOX_SUCCESS)
                report["namespaces_validated"] = True
            # Do not retain process output: only a bounded, fixed-class digest.
            report["checks"].append({"program": name, "result": "pass",
                                     "stdout_bytes": len(result.stdout),
                                     "stdout_sha256": hashlib.sha256(result.stdout).hexdigest()})
        report.update(stage="complete", result="pass")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        if sandbox:
            kind = "invalid-result"
            if isinstance(exc, subprocess.TimeoutExpired):
                kind = "timeout"
            elif isinstance(exc, subprocess.CalledProcessError):
                error = exc.stderr if isinstance(exc.stderr, bytes) else b""
                kind = ("permission-denied" if any(s in error for s in
                        (b"Operation not permitted", b"Permission denied")) else "process-exit")
            elif isinstance(exc, OSError):
                kind = "startup-error"
            report["failure_kind"] = kind
    return report


def main():
    require(len(sys.argv) == 1 and sys.platform == "linux" and os.geteuid() == 65534)
    inputs = json.loads(read(Path("/nib/input.json"), 16384))
    validate_input(inputs)
    if inputs.get("sandbox") is True:
        for path, field in SANDBOX_FILES.items():
            require(hashlib.sha256(read(Path(path), 1 << 20)).hexdigest() == inputs[field])
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
