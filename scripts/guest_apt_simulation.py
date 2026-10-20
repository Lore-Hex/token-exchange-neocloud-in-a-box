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
MAX_STDERR = 65536
ID_LIMIT = 16
PROCESS_REASONS = frozenset(("process-nonzero", "stderr-nonempty", "process-timeout",
                            "output-limit", "collector-error", "process-os-error",
                            "deadline", "collector-result", "apt-version-mismatch"))
PLAN_REASONS = frozenset(("plan-output-limit", "plan-utf8", "plan-control-character",
                         "plan-line-limit", "plan-forbidden-operation", "plan-record-syntax",
                         "plan-package-unexpected", "plan-version-mismatch", "plan-duplicate",
                         "plan-pending-rejected", "plan-coverage"))
ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C",
       "TMPDIR": "/work/tmp", "APT_CONFIG": "/work/apt.conf"}


def require(condition):
    if not condition:
        raise ValueError("apt-simulation-rejected")


class Rejected(ValueError):
    def __init__(self, reason, plan=None):
        super().__init__("apt-simulation-rejected")
        self.reason, self.plan = reason, plan


def stderr_kinds(raw):
    counts = dict.fromkeys(("error", "warning", "notice", "other"), 0)
    for line in raw.splitlines():
        if line:
            kind = {b"E:": "error", b"W:": "warning", b"N:": "notice"}.get(line[:2], "other")
            counts[kind] += 1
    return counts


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
    installed, configured = {}, {}
    subject = None

    def accept(condition, reason):
        if not condition:
            missing_inst = sorted(set(targets) - installed.keys())
            missing_conf = sorted(set(targets) - configured.keys())
            raise Rejected(reason, {
                "installed": len(installed), "configured": len(configured),
                "expected": len(targets), "subject": subject,
                "missing_installed_count": len(missing_inst),
                "missing_configured_count": len(missing_conf),
                "missing_installed": missing_inst[:ID_LIMIT],
                "missing_configured": missing_conf[:ID_LIMIT],
            })

    accept(len(raw) <= MAX_OUTPUT, "plan-output-limit")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        accept(False, "plan-utf8")
    accept("\x00" not in text and "\r" not in text, "plan-control-character")
    for line in text.splitlines():
        subject = None
        accept(len(line) <= 8192, "plan-line-limit")
        accept(not line.startswith(("Remv ", "Purg ", "E:", "Err:", "W:")),
               "plan-forbidden-operation")
        if line.startswith(("Inst ", "Conf ")):
            match = re.fullmatch(
                r"(Inst|Conf) ([a-z0-9][a-z0-9+.-]+) \(([^ \t()]+) [^\r\n()]*\)"
                r"(?: \[([^\[\]\r\n]*)\])?", line)
            accept(match is not None, "plan-record-syntax")
            kind, name, version = match.group(1, 2, 3)
            target = installed if kind == "Inst" else configured
            accept(name in targets, "plan-package-unexpected")
            subject = name
            accept(targets[name] == version, "plan-version-mismatch")
            accept(name not in target, "plan-duplicate")
            # APT 2.8.3 pkgSimulate::ShortBreaks prints temporary unpack-order
            # dependencies. Only selected native/all package IDs are admissible;
            # final complete Inst/Conf coverage and exit zero are still required.
            if match[4] is not None:
                pending = match[4].split()
                accept(len(pending) <= len(targets) and len(set(pending)) == len(pending),
                       "plan-pending-rejected")
                for value in pending:
                    part = re.fullmatch(r"([a-z0-9][a-z0-9+.-]+)(?::(?:amd64|all))?", value)
                    accept(part is not None and part[1] in targets, "plan-pending-rejected")
            target[name] = version
    subject = None
    accept(installed == configured == targets, "plan-coverage")
    return {"installed": len(installed), "configured": len(configured), "removed": 0,
            "plan_sha256": hashlib.sha256(raw).hexdigest()}


def validate_diagnostic(value, stage, targets):
    """Validate only fixed categories/counts and IDs from the committed target set."""
    targets_valid(targets)
    require(type(value) is dict and set(value) == {
        "reason", "exit_code", "stdout_bytes", "stderr_bytes", "stderr_kinds", "plan"})
    reason = value["reason"]
    require(reason in PROCESS_REASONS | PLAN_REASONS | {"setup-error", "validation-error"})
    require(stage in ("setup", "apt-version", "local-index", "resolve"))
    if reason == "setup-error":
        require(stage == "setup")
    elif reason == "apt-version-mismatch":
        require(stage == "apt-version")
    elif reason in PLAN_REASONS:
        require(stage == "resolve")
    elif reason in PROCESS_REASONS:
        require(stage != "setup")
    observed = value["exit_code"] is not None
    if observed:
        require(type(value["exit_code"]) is int and -127 <= value["exit_code"] <= 255)
        for key, limit in (("stdout_bytes", MAX_OUTPUT), ("stderr_bytes", MAX_STDERR)):
            require(type(value[key]) is int and 0 <= value[key] <= limit)
        counts = value["stderr_kinds"]
        require(type(counts) is dict and set(counts) == {"error", "warning", "notice", "other"}
                and all(type(n) is int and 0 <= n <= MAX_STDERR for n in counts.values())
                and sum(counts.values()) <= value["stderr_bytes"])
    else:
        require(all(value[k] is None for k in ("stdout_bytes", "stderr_bytes", "stderr_kinds")))
    if reason in ("process-timeout", "output-limit", "collector-error", "process-os-error",
                  "collector-result", "setup-error"):
        require(not observed)
    if reason == "deadline" and observed:
        require(value["exit_code"] == 0)
    if reason == "process-nonzero":
        require(observed and value["exit_code"] != 0)
    if reason == "stderr-nonempty":
        require(observed and value["exit_code"] == 0 and value["stderr_bytes"] > 0)
    if reason == "apt-version-mismatch" or reason in PLAN_REASONS:
        require(observed and value["exit_code"] == 0 and value["stderr_bytes"] == 0)
    plan = value["plan"]
    if reason not in PLAN_REASONS:
        require(plan is None)
        return
    require(type(plan) is dict and set(plan) == {
        "installed", "configured", "expected", "subject", "missing_installed_count",
        "missing_configured_count", "missing_installed", "missing_configured"})
    require(type(plan["expected"]) is int and plan["expected"] == len(targets)
            and (plan["subject"] is None or plan["subject"] in targets))
    require((plan["subject"] is not None) == (reason in {
        "plan-version-mismatch", "plan-duplicate", "plan-pending-rejected"}))
    for kind in ("installed", "configured"):
        count, missing = plan[kind], plan["missing_" + kind + "_count"]
        ids = plan["missing_" + kind]
        require(type(count) is int and 0 <= count <= len(targets)
                and type(missing) is int and missing == len(targets) - count
                and type(ids) is list and len(ids) == min(missing, ID_LIMIT)
                and all(type(n) is str and n in targets for n in ids)
                and ids == sorted(set(ids)))
    if reason == "plan-coverage":
        require(plan["installed"] != len(targets) or plan["configured"] != len(targets))


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
    report = {"schema": "nib.apt-simulation/v2", "result": "fail", "stage": "setup",
              "git_sha": inputs["git_sha"], "guest_lock_sha256": inputs["guest_lock_sha256"],
              "target_packages": len(inputs["targets"]), "plan": None, "diagnostic": None,
              "package_installation_executed": False, "maintainer_scripts_executed": False,
              "installable_verified": False, "bootable": False, "deployable": False}
    deadline = time.monotonic() + SECONDS
    observation = dict.fromkeys(("exit_code", "stdout_bytes", "stderr_bytes", "stderr_kinds"))
    try:
        if setup_work:
            setup(Path("/work"))

        def call(args):
            observation.update(dict.fromkeys(observation))
            remaining = deadline - time.monotonic()
            if not 0 < remaining <= SECONDS:
                raise Rejected("deadline")
            try:
                value = execute(args, env=ENV, timeout=remaining, max_output_bytes=MAX_OUTPUT,
                                stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                raise Rejected("process-timeout") from None
            except OSError:
                raise Rejected("process-os-error") from None
            except (ValueError, subprocess.SubprocessError) as error:
                # Only the source-pinned collector's exact fixed exception is classified.
                # No arbitrary exception text, arguments or partial output is serialized.
                reason = ("output-limit" if type(error) is ValueError
                          and error.args == ("tool output limit exceeded",) else "collector-error")
                raise Rejected(reason) from None
            if not (type(value.returncode) is int and -127 <= value.returncode <= 255
                    and type(value.stdout) is bytes and len(value.stdout) <= MAX_OUTPUT
                    and type(value.stderr) is bytes and len(value.stderr) <= MAX_STDERR):
                raise Rejected("collector-result")
            observation.update(exit_code=value.returncode, stdout_bytes=len(value.stdout),
                               stderr_bytes=len(value.stderr), stderr_kinds=stderr_kinds(value.stderr))
            if value.returncode:
                raise Rejected("process-nonzero")
            if time.monotonic() >= deadline:
                raise Rejected("deadline")
            if value.stderr:
                raise Rejected("stderr-nonempty")
            return value.stdout

        report["stage"] = "apt-version"
        version = call(["/usr/bin/apt-get", "--version"])
        if not version.startswith(b"apt 2.8.3 (amd64)\n"):
            raise Rejected("apt-version-mismatch")
        report["stage"] = "local-index"
        call(["/usr/bin/apt-get", "update"])
        report["stage"] = "resolve"
        raw = call(["/usr/bin/apt-get", "--simulate", "--no-download",
                    "--no-install-recommends", "install"]
                   + [name + "=" + version for name, version in sorted(inputs["targets"].items())])
        report["plan"] = validate_plan(raw, inputs["targets"])
        report["stage"], report["result"] = "complete", "pass"
    except Rejected as error:
        report["diagnostic"] = {**observation, "reason": error.reason, "plan": error.plan}
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        report["diagnostic"] = {**observation, "reason": (
            "setup-error" if report["stage"] == "setup" else "validation-error"), "plan": None}
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
