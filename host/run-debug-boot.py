#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Independent resource/cleanup wrapper for the fixed hosted CPU debug boot."""
import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
    sys.exit("Use python3.10 -I -S -B host/run-debug-boot.py")

import argparse
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import signal
import stat
import subprocess
import time
import types

ROOT = Path(__file__).absolute().parent.parent
build = types.SimpleNamespace(**runpy.run_path(str(ROOT / "host/run-mkosi-build.py")))
base, require = build.base, build.require
resources, _RESOURCE_SOURCE = base.helper("debug_boot_resources", "host/probe-build-resources.py")
# A separate module instance: existing native build limits are not changed.
resources.LIMITS = {**resources.LIMITS, "memory.max": str(2 << 30), "pids.max": "128"}
DIRECTORY = ROOT / "build/debug-boot"
PRIVATE = DIRECTORY / "private"
BUNDLE = ROOT / "build/native-install/evidence-boot"
TIMEOUT = 900
SCRIPT = "host/run-debug-boot.py"
SELF = base.read(ROOT / SCRIPT, 1 << 20)


def capture_producer(uid, gid):
    # Audit as the runner, before launching any image/QEMU worker. Never invoke
    # Git or honor repository configuration as root, and retain this result in
    # the independent parent even when the later worker is killed.
    args = ["/usr/bin/python3.10", "-I", "-S", "-B", str(ROOT / "host/debug-boot.py"), "--source-only"]
    child = subprocess.Popen(args, user=uid, group=gid, extra_groups=[], cwd=ROOT,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C",
             "RUNNER_ENVIRONMENT": "github-hosted"},
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        raw, error = base.processes.bounded_output(child, args, 120, None, 65536, 65536)
        require(child.returncode == 0 and not error)
    finally:
        base.processes.cleanup_process_group(child)
    value = json.loads(raw)
    require(set(value) == {"schema", "source", "debug_source"} and value["schema"] == "nib.debug-producer/v1")
    revision = value["source"]["git_sha"]
    require(type(revision) is str and re.fullmatch("[0-9a-f]{40}", revision) and revision != "0" * 40
            and value["debug_source"]["git_sha"] == revision
            and value["debug_source"]["sources"][SCRIPT] == base.digest(SELF))
    return value


def launch(fd, uid, gid, arguments, timeout=TIMEOUT):
    require(0 < timeout <= TIMEOUT)
    procs = os.open("cgroup.procs", os.O_WRONLY | os.O_NOFOLLOW, dir_fd=fd)
    child = None
    try:
        child = subprocess.Popen(["/usr/bin/python3.10", "-I", "-S", "-B", "-c", build.CHILD,
            str(procs), str(uid), str(gid), str(ROOT / "host/debug-boot.py"), *arguments],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            pass_fds=(procs,), start_new_session=True, cwd=ROOT,
            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C"})
        return child.wait(timeout=timeout)
    finally:
        os.close(procs)
        if child is not None and child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=10)


def execute(uid, gid, arguments, timeout=TIMEOUT):
    state = {"schema": "nib.debug-boot-resources/v1", "result": "fail", "limits": resources.LIMITS,
             "limits_read_back": False, "empty_verified": False, "group_removed": False,
             "child_exit_code": None, "oom_kill": None}
    try:
        with resources.cgroup(state) as (fd, _):
            os.fchmod(fd, 0o755)
            state["child_exit_code"] = launch(fd, uid, gid, arguments, timeout)
        require(state["child_exit_code"] == 0 and state["limits_read_back"]
                and state["empty_verified"] and state["group_removed"] and state["oom_kill"] == 0)
        state["result"] = "pass"
    except (OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt):
        state["result"] = "fail"
    return state


def owned(path, uid):
    with base.directory(path) as fd:
        info = os.fstat(fd)
        require(info.st_uid == uid and stat.S_IMODE(info.st_mode) == 0o700)
        return info


def same(path, original):
    info = os.stat(path, follow_symlinks=False)
    require(stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino, info.st_uid)
            == (original.st_dev, original.st_ino, original.st_uid))


def finish(state, private, bundle, started, producer):
    require(state["empty_verified"] is True and state["group_removed"] is True
            and os.getuid() == os.geteuid() > 0 and shutil.rmtree.avoids_symlink_attacks)
    worker = {"schema": "nib.debug-boot/v1", "result": "fail", "stage": "worker-missing",
              "boot_verified": False, "confidentiality_verified": False}
    worker_raw = base.encoded(worker)
    try:
        candidate = base.read(DIRECTORY / "worker.json", 65536)
        worker = json.loads(candidate)
        require(worker["schema"] == "nib.debug-boot/v1" and worker["confidentiality_verified"] is False)
        worker_raw = candidate
    except FileNotFoundError:
        state["result"] = "fail"
    except (OSError, ValueError, KeyError, TypeError):
        state["result"] = "fail"
        worker = {"schema": "nib.debug-boot/v1", "result": "fail", "stage": "worker-invalid",
                  "boot_verified": False, "confidentiality_verified": False}
        worker_raw = base.encoded(worker)
    same(PRIVATE, private)
    shutil.rmtree(PRIVATE)
    same(BUNDLE, bundle)
    with base.directory(BUNDLE.parent) as parent:
        base.inputs.remove_flat_directory(parent, BUNDLE.name, bundle)
    require(not os.path.lexists(PRIVATE) and not os.path.lexists(BUNDLE))
    passed = (state["result"] == "pass" and worker["result"] == "pass"
              and worker.get("stage") == "complete" and worker.get("boot_verified") is True
              and worker.get("source") == producer["source"] and worker.get("debug_source") == producer["debug_source"])
    result = {"schema": "nib.debug-boot-validation/v1", "result": "pass" if passed else "fail",
              "private_images_removed": True, "metadata_only": True, "boot_verified": passed,
              "confidentiality_verified": False, "reproducibility_verified": False,
              "production_service_verified": False, "signed": False}
    run = {"schema": "nib.evidence/v1", "phase": "P3", "task": "T1", "execution": "software",
           "git_sha": producer["source"]["git_sha"], "result": result["result"],
           "started_utc": started, "ended_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "command": "sudo /usr/bin/python3.10 -I -S -B host/run-debug-boot.py --downloads PRIVATE --archive PRIVATE --runtime-inputs PRIVATE --go PRIVATE",
           "host": {"provider": "github-actions", "machine": "ubuntu-22.04-x86_64",
                    "gpus": "none", "driver": None, "cuda": None},
           "hardware_validated": False, "notes": "One bounded nonconfidential TCG debug boot; JSON only after independent cleanup."}
    files = {"validation.json": base.encoded(result), "resources.json": base.encoded({**state, "producer": producer}),
             "worker.json": worker_raw, "run.json": base.encoded(run)}
    base.publish(DIRECTORY / "metadata", files)
    base.verify(DIRECTORY / "metadata", files)
    return result["result"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("downloads", "archive", "runtime-inputs", "go"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    uid, gid = build.identity()  # Existing hosted Ubuntu 22.04 gate, unchanged.
    require(all(p.is_absolute() and ".." not in p.parts for p in (args.downloads, args.archive, args.runtime_inputs, args.go)))
    owned(DIRECTORY, uid)
    bundle = owned(BUNDLE, uid)
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    began = time.monotonic()
    producer = capture_producer(uid, gid)
    with base.directory(DIRECTORY) as fd:
        for name in ("private", "metadata", "worker.json", "resources.json"):
            require(not os.path.lexists(DIRECTORY / name))
        os.mkdir("private", mode=0o700, dir_fd=fd)
        os.chown("private", uid, gid, dir_fd=fd, follow_symlinks=False)
        private = os.stat("private", dir_fd=fd, follow_symlinks=False)
    with build.receipt_file(DIRECTORY / "resources.json", uid, gid) as descriptor:
        def cancel(signum, frame):
            raise KeyboardInterrupt
        previous = {s: signal.signal(s, cancel) for s in (signal.SIGINT, signal.SIGTERM)}
        try:
            state = execute(uid, gid, ["--downloads", str(args.downloads), "--archive", str(args.archive),
                                      "--runtime-inputs", str(args.runtime_inputs), "--go", str(args.go)],
                            TIMEOUT - (time.monotonic() - began))
            # The cgroup has finished its independent cleanup. Cancellation
            # must not interrupt the subsequent owned-image deletion halfway.
            for s in previous:
                signal.signal(s, signal.SIG_IGN)
            raw = base.encoded(state)
            require(os.write(descriptor, raw) == len(raw))
        except BaseException:
            for s, handler in previous.items():
                signal.signal(s, handler)
            raise
    try:
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
        require(os.getuid() == os.geteuid() == uid and os.getgid() == os.getegid() == gid and os.getgroups() == [])
        def expired(signum, frame):
            raise ValueError("debug-cleanup-timeout")
        require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0))
        old_alarm = signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, 120)
        try:
            result = finish(state, private, bundle, started, producer)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_alarm)
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)
    # Fixed output authorizes JSON upload only; workflow checks pass separately.
    print(result)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        sys.exit("Debug boot rejected; metadata upload not authorized.")
