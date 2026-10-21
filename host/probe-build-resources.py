#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bounded, synthetic cgroup-v2 probe on an explicitly approved root lab VM."""
import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B probe-build-resources.py")

import argparse
from contextlib import contextmanager
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import time
import uuid

BASE = Path("/sys/fs/cgroup")
LIMITS = {"memory.max": str(8 << 30), "memory.swap.max": "0", "memory.oom.group": "1",
          "pids.max": "256", "cpu.max": "200000 100000"}
ALLOWED = set(LIMITS) | {"cgroup.controllers", "cgroup.subtree_control", "cgroup.type", "cgroup.events",
                         "cgroup.procs", "cgroup.kill", "memory.current", "memory.peak", "memory.events"}
CHILD = """
import os, pathlib, signal, sys
fd, name = int(sys.argv[1]), sys.argv[2]
assert name.startswith('nib-build-') and len(name) == 42
os.write(fd, str(os.getpid()).encode('ascii'))
os.close(fd)
root = pathlib.Path('/sys/fs/cgroup') / name
assert pathlib.Path('/proc/self/cgroup').read_text() == '0::/' + name + '\\n'
limits = {key: (root / key).read_text().strip() for key in
          ('memory.max', 'memory.swap.max', 'memory.oom.group', 'pids.max', 'cpu.max')}
assert limits == {'memory.max': '8589934592', 'memory.swap.max': '0', 'memory.oom.group': '1',
                  'pids.max': '256', 'cpu.max': '200000 100000'}
payload = bytearray(32 << 20)
for offset in range(0, len(payload), 4096):
    payload[offset] = 1
ready_read, ready_write = os.pipe()
child = os.fork()
if child == 0:
    os.close(ready_read)
    os.setsid()
    signal.alarm(60)
    assert os.getsid(0) == os.getpid()
    assert pathlib.Path('/proc/self/cgroup').read_text() == '0::/' + name + '\\n'
    # Close the output pipe before reporting readiness so communicate() can
    # finish while this bounded, detached descendant deliberately remains.
    for descriptor in (0, 1, 2):
        os.close(descriptor)
    assert os.write(ready_write, b'READY') == 5
    os.close(ready_write)
    while True:
        signal.pause()
os.close(ready_write)
assert os.read(ready_read, 6) == b'READY'
os.close(ready_read)
assert os.waitpid(child, os.WNOHANG) == (0, 0)
assert int((root / 'memory.current').read_text()) >= len(payload)
print('NIB_BUILD_CGROUP_OK', flush=True)
"""


def require(value):
    if not value:
        raise ValueError("build-resource-probe-rejected")


def read(fd, name):
    require(name in ALLOWED)
    child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        raw = os.read(child, 4097)
        require(len(raw) <= 4096)
        return raw.decode("ascii").strip()
    finally:
        os.close(child)


def write(fd, name, value):
    require(name in set(LIMITS) | {"cgroup.kill"} and type(value) is str and len(value) <= 64)
    child = os.open(name, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        raw = value.encode("ascii")
        require(os.write(child, raw) == len(raw))
    finally:
        os.close(child)


def numeric_fields(text):
    rows = [line.split() for line in text.splitlines()]
    require(rows and all(len(row) == 2 and re.fullmatch(r"[a-z_]+", row[0])
                        and row[1].isascii() and row[1].isdecimal() for row in rows))
    require(len({row[0] for row in rows}) == len(rows))
    return {key: int(value) for key, value in rows}


def preflight():
    require(sys.platform == "linux" and os.uname().machine == "x86_64" and os.geteuid() == 0)
    mounts = Path("/proc/self/mountinfo").read_text().splitlines()
    matches = [line.split() for line in mounts if len(line.split()) > 6 and line.split()[4] == str(BASE)]
    require(len(matches) == 1)
    row = matches[0]
    require(row[row.index("-") + 1] == "cgroup2" and "rw" in row[5].split(","))


def open_root():
    preflight()
    root = os.open(BASE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        require(os.fstat(root).st_uid == 0 and not os.fstat(root).st_mode & 0o022)
        require({"cpu", "memory", "pids"} <= set(read(root, "cgroup.subtree_control").split()))
    except BaseException:
        os.close(root)
        raise
    return root


@contextmanager
def cgroup(state):
    root = open_root()
    fd, name, created = None, "nib-build-" + uuid.uuid4().hex, False
    try:
        # Never enable parent controllers, move existing processes or reuse a group.
        os.mkdir(name, 0o700, dir_fd=root)
        created = True
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        identity = os.fstat(fd)
        require(read(fd, "cgroup.type") == "domain"
                and numeric_fields(read(fd, "cgroup.events"))["populated"] == 0
                and read(fd, "cgroup.procs") == "")
        for key, value in LIMITS.items():
            write(fd, key, value)
            require(read(fd, key) == value)
        state["limits_read_back"] = True
        yield fd, name
    finally:
        old_signals = {signum: signal.signal(signum, signal.SIG_IGN) for signum in (signal.SIGINT, signal.SIGTERM)}
        try:
            if fd is not None:
                current = os.stat(name, dir_fd=root, follow_symlinks=False)
                require((current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino))
                state["populated_before_cleanup"] = numeric_fields(read(fd, "cgroup.events"))["populated"]
                # cgroup.kill includes descendants which escaped a process group.
                write(fd, "cgroup.kill", "1")
                deadline = time.monotonic() + 10
                while numeric_fields(read(fd, "cgroup.events"))["populated"] != 0:
                    require(time.monotonic() < deadline)
                    time.sleep(0.05)
                state["empty_verified"] = True
                events = numeric_fields(read(fd, "memory.events"))
                state["oom_kill"] = events.get("oom_kill")
                peak = read(fd, "memory.peak")
                require(peak.isdecimal())
                state["memory_peak_bytes"] = int(peak)
                os.close(fd)
                fd = None
                os.rmdir(name, dir_fd=root)
                state["group_removed"] = True
            elif created:
                # This exact just-created empty directory only; never recursive.
                os.rmdir(name, dir_fd=root)
                state["group_removed"] = True
        finally:
            if fd is not None:
                os.close(fd)
            os.close(root)
            for signum, handler in old_signals.items():
                signal.signal(signum, handler)


def run_child(fd, name):
    procs = os.open("cgroup.procs", os.O_WRONLY | os.O_NOFOLLOW, dir_fd=fd)
    child = None
    try:
        # The fixed trusted child joins the bounded group before allocating or
        # forking. No caller command, shell or package hook can be substituted.
        child = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", CHILD, str(procs), name],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C"},
            pass_fds=(procs,), start_new_session=True)
        output, _ = child.communicate(timeout=20)
        require(child.returncode == 0 and output == b"NIB_BUILD_CGROUP_OK\n")
    finally:
        os.close(procs)
        if child is not None and child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)


def probe():
    state = {"limits_read_back": False, "worker_join_and_inheritance_verified": False,
             "empty_verified": False, "group_removed": False, "oom_kill": None,
             "memory_peak_bytes": None, "populated_before_cleanup": None}
    try:
        with cgroup(state) as (fd, name):
            run_child(fd, name)
            state["worker_join_and_inheritance_verified"] = True
    except (Exception, KeyboardInterrupt):
        pass
    passed = (all(state[key] is True for key in ("limits_read_back", "worker_join_and_inheritance_verified",
                                                 "empty_verified", "group_removed"))
              and state["oom_kill"] == 0 and state["populated_before_cleanup"] == 1)
    return {"schema": "nib.build-resource-probe/v2", "result": "pass" if passed else "fail", **state,
            "limits": LIMITS, "probe_seconds": 20, "cleanup_seconds": 10,
            "detached_child_fallback_seconds": 60,
            "detached_child_cleanup_verified": passed,
            "host_runtime_pinned": False, "package_installation_executed": False,
            "image_built": False, "confidential_qualified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    raw = Path(__file__).read_bytes()
    require(re.fullmatch(r"[0-9a-f]{64}", args.source_sha256)
            and hashlib.sha256(raw).hexdigest() == args.source_sha256
            and re.fullmatch(r"[0-9a-f]{40}", args.git_sha))
    require(args.output.is_absolute() and args.output.parent.resolve() == args.output.parent
            and not args.output.exists() and not args.output.is_symlink())
    parent = args.output.parent.stat()
    require(parent.st_uid == os.geteuid() and stat.S_IMODE(parent.st_mode) == 0o700)
    utc = lambda: datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    started = utc()
    old = signal.getsignal(signal.SIGTERM)
    def cancel(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, cancel)
    try:
        result = probe()
    finally:
        signal.signal(signal.SIGTERM, old)
    result.update(source_sha256=args.source_sha256, git_sha=args.git_sha)
    record = {"schema": "nib.evidence/v1", "phase": "P3", "task": "T1", "git_sha": args.git_sha,
              "command": "python3 -I -S -B host/probe-build-resources.py --source-sha256 " + args.source_sha256
                         + " --git-sha " + args.git_sha + " --output PRIVATE",
              "started_utc": started, "ended_utc": utc(), "execution": "software", "hardware_validated": False,
              "host": {"provider": "operator-approved-test-host", "machine": "root-linux-amd64",
                       "gpus": "not-used", "driver": None, "cuda": None}, "result": result["result"],
              "notes": "Synthetic resource-controller probe only. Own fresh cgroup, no parent controller changes, "
                       "no host account changes, no package installation or inference. Source digest supplied from the committed local checkout."}
    args.output.mkdir(mode=0o700)
    for name, value in (("run.json", record), ("result.json", result)):
        with (args.output / name).open("x") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
    print("Build resource probe: " + result["result"])
    return 0 if result["result"] == "pass" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit("Build resource probe rejected; no image qualification.")
