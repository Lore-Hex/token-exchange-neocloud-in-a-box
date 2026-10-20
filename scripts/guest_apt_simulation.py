#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fixed entrypoint for the nonbootable, offline APT simulation tools image."""

import sys

if __name__ == "__main__" and not (
        sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
    sys.exit("Isolated no-site APT simulation entry required.")

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import types

SECONDS = 120
MAX_OUTPUT = 2 << 20
ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C",
       "TMPDIR": "/work/tmp", "APT_CONFIG": "/work/apt.conf"}


def require(condition):
    if not condition:
        raise ValueError("apt-simulation-rejected")


def read(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and 0 < before.st_size <= limit)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_nlink,
                              s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        require(len(raw) == before.st_size and identity(before) == identity(after))
        return raw
    finally:
        os.close(fd)


def targets_valid(targets):
    require(isinstance(targets, dict) and 0 < len(targets) <= 512)
    for name, version in targets.items():
        require(isinstance(name, str) and re.fullmatch("[a-z0-9][a-z0-9+.-]+", name)
                and isinstance(version, str)
                and re.fullmatch(r"[0-9][0-9A-Za-z.+:~\-]{0,255}", version))


def validate_plan(raw, targets):
    """Public APT simulation Inst/Conf records must cover exactly the requested set."""
    targets_valid(targets)
    require(len(raw) <= MAX_OUTPUT)
    text = raw.decode("utf-8", errors="strict")
    require("\x00" not in text and "\r" not in text)
    installed, configured = {}, {}
    for line in text.splitlines():
        require(len(line) <= 8192)
        require(not line.startswith(("Remv ", "Purg ", "E:", "Err:", "W:")))
        if line.startswith(("Inst ", "Conf ")):
            match = re.fullmatch(
                r"(Inst|Conf) ([a-z0-9][a-z0-9+.-]+) \(([^ \t()]+) [^\r\n()]*\)"
                r"(?: \[([^\[\]\r\n]*)\])?", line)
            require(match is not None)
            kind, name, version = match.group(1, 2, 3)
            target = installed if kind == "Inst" else configured
            require(name in targets and targets[name] == version and name not in target)
            # APT 2.8.3 pkgSimulate::ShortBreaks prints temporary unpack-order
            # dependencies. Only selected native/all package IDs are admissible;
            # final complete Inst/Conf coverage and exit zero are still required.
            if match[4] is not None:
                pending = match[4].split()
                require(len(pending) <= len(targets) and len(set(pending)) == len(pending))
                for value in pending:
                    part = re.fullmatch(r"([a-z0-9][a-z0-9+.-]+)(?::(?:amd64|all))?", value)
                    require(part is not None and part[1] in targets)
            target[name] = version
    require(installed == configured == targets)
    return {"installed": len(installed), "configured": len(configured), "removed": 0,
            "plan_sha256": hashlib.sha256(raw).hexdigest()}


def configuration():
    # APT_CONFIG is read before normal fragments. These paths and all child
    # configuration are private; never consult image defaults or host state.
    return b"""Dir::Etc "/work/etc";
Dir::Etc::main "/work/empty.conf";
Dir::Etc::parts "/work/etc/parts";
Dir::Etc::sourcelist "/work/sources.list";
Dir::Etc::sourceparts "/work/etc/sources";
Dir::State "/work/state";
Dir::State::status "/work/status";
Dir::State::lists "/work/state/lists";
Dir::Cache "/work/cache";
Dir::Cache::archives "/work/cache/archives";
Dir::Cache::pkgcache "";
Dir::Cache::srcpkgcache "";
Dir::Log "/work/log";
APT::Architecture "amd64";
APT::Architectures { "amd64"; };
APT::Install-Recommends "false";
APT::Install-Suggests "false";
APT::Get::List-Cleanup "false";
Acquire::Languages "none";
Acquire::Retries "0";
#clear DPkg::Pre-Invoke;
#clear DPkg::Post-Invoke;
#clear DPkg::Pre-Install-Pkgs;
#clear APT::Update::Pre-Invoke;
#clear APT::Update::Post-Invoke;
#clear APT::Update::Post-Invoke-Success;
"""


def setup(work):
    require(work.is_absolute() and not work.is_symlink() and work.is_dir())
    require(not list(work.iterdir()))
    for name in ("tmp", "etc/parts", "etc/sources", "state/lists/partial",
                 "cache/archives/partial", "log"):
        (work / name).mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, raw in {"apt.conf": configuration(),
                      "sources.list": b"deb [trusted=yes] file:/nib/repo ./\n",
                      "status": b"", "empty.conf": b""}.items():
        fd = os.open(work / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)


def simulate(inputs, execute, *, setup_work=True):
    require(inputs["schema"] == "nib.apt-simulation-input/v1"
            and inputs["apt_version"] == "2.8.3"
            and re.fullmatch("[0-9a-f]{40}", inputs["git_sha"])
            and re.fullmatch("[0-9a-f]{64}", inputs["guest_lock_sha256"]))
    targets_valid(inputs["targets"])
    report = {"schema": "nib.apt-simulation/v1", "result": "fail", "stage": "setup",
              "git_sha": inputs["git_sha"], "guest_lock_sha256": inputs["guest_lock_sha256"],
              "target_packages": len(inputs["targets"]), "plan": None,
              "package_installation_executed": False, "maintainer_scripts_executed": False,
              "installable_verified": False, "bootable": False, "deployable": False}
    deadline = time.monotonic() + SECONDS
    try:
        if setup_work:
            setup(Path("/work"))

        def call(args):
            remaining = deadline - time.monotonic()
            require(0 < remaining <= SECONDS)
            value = execute(args, env=ENV, timeout=remaining, max_output_bytes=MAX_OUTPUT,
                            stdin=subprocess.DEVNULL)
            value.check_returncode()
            require(time.monotonic() < deadline and not value.stderr)
            return value.stdout

        report["stage"] = "apt-version"
        version = call(["/usr/bin/apt-get", "--version"])
        require(version.startswith(b"apt 2.8.3 (amd64)\n"))
        report["stage"] = "local-index"
        call(["/usr/bin/apt-get", "update"])
        report["stage"] = "resolve"
        raw = call(["/usr/bin/apt-get", "--simulate", "--no-download",
                    "--no-install-recommends", "install"]
                   + [name + "=" + version for name, version in sorted(inputs["targets"].items())])
        report["plan"] = validate_plan(raw, inputs["targets"])
        report["stage"], report["result"] = "complete", "pass"
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        pass
    return report


def main():
    require(len(sys.argv) == 1 and sys.platform == "linux")
    inputs = json.loads(read("/nib/input.json", 1 << 20))
    raw = read("/nib/snapshot.py", 1 << 20)
    require(hashlib.sha256(raw).hexdigest() == inputs["snapshot_sha256"])
    helper = types.ModuleType("apt_processes")
    exec(compile(raw, "/nib/snapshot.py", "exec", dont_inherit=True), helper.__dict__)
    report = simulate(inputs, helper.execute_process_group)
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError):
        sys.exit("APT simulation rejected before execution.")
