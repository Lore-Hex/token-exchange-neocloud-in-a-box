#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Identify one existing selected owner and optionally request cancellation via pidfd."""

import sys
from pathlib import Path

ROOT = Path(__file__).absolute().parents[1]
if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        sys.exit("Use python3 -I -S -B host/cancel-selected-service.py")
    sys.path.insert(0, str(ROOT / "scripts"))

import argparse
from contextlib import contextmanager
import datetime
import json
import os
import re
import select
import signal
import stat
import subprocess
import time

from e2e import snapshot
import tau_runtime as files
from tau_trial_worker import alarm

require, digest, encoded = files.require, files.digest, files.encoded
SECONDS = 60
LIMIT = 65536
OWNER = "host/record-selected-service.py"
PROC = Path("/proc")
ROOT_UID = 0


def path(value):
    require(type(value) is str and value.startswith("/"))
    files.safe_name(value[1:])
    return Path(value)


def owned(info):
    require(info.st_uid == ROOT_UID and not info.st_mode & 0o022)


@contextmanager
def directory(value, private=False):
    """Reuse no-follow traversal; admit only root-controlled ancestors."""
    value = path(str(value))
    with files.directory(value) as fd:
        for parent in (*reversed(value.parents), value):
            info = parent.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, ROOT_UID))
            # A root-owned sticky /tmp ancestor cannot replace a root-owned child.
            require(not info.st_mode & 0o022 or stat.S_IMODE(info.st_mode) == 0o1777)
        info = os.fstat(fd)
        owned(info)
        if private:
            require(stat.S_IMODE(info.st_mode) == 0o700)
        require((info.st_dev, info.st_ino) == (value.stat().st_dev, value.stat().st_ino))
        yield fd


def file_info(value):
    with directory(value.parent) as fd:
        info = os.stat(value.name, dir_fd=fd, follow_symlinks=False)
        owned(info)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
        return info


def read(value, limit, end):
    before = file_info(value)
    raw = files.read_file(value, limit, end)
    require(files.identity(before) == files.identity(file_info(value)))
    return raw


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def expectation(raw):
    value = json.loads(raw, object_pairs_hook=unique_object)
    require(type(value) is dict and set(value) == {"schema", "source_root", "source_revision",
        "executable", "executable_sha256", "argv", "state_dir", "stdout", "stderr"})
    require(value["schema"] == "nib.selected-owner-cancel-input/v1")
    for name in ("source_root", "executable", "state_dir", "stdout", "stderr"):
        path(value[name])
    for name, size in (("source_revision", 40), ("executable_sha256", 64)):
        require(type(value[name]) is str and re.fullmatch("[0-9a-f]{" + str(size) + "}", value[name]))
    argv = value["argv"]
    require(type(argv) is list and 8 <= len(argv) <= 128 and all(
        type(arg) is str and 0 < len(arg) <= 4096 and not any(ord(c) < 32 or ord(c) == 127 for c in arg)
        for arg in argv))
    require(len(encoded(argv)) <= LIMIT and len(set(argv[1:4])) == 3
            and set(argv[1:4]) == {"-I", "-S", "-B"}
            and argv[4] == str(path(value["source_root"]) / OWNER))
    require(argv[5:].count("--run") == 1 and not {"--inspect", "--watchdog"}.intersection(argv))
    for flag in ("--state-dir", "--hard-stop-at"):
        require(argv[5:].count(flag) == 1 and argv.index(flag) + 1 < len(argv))
    require(argv[argv.index("--state-dir") + 1] == value["state_dir"])
    cutoff = argv[argv.index("--hard-stop-at") + 1]
    parsed = datetime.datetime.strptime(cutoff, "%Y-%m-%dT%H:%M:%SZ")
    require(parsed.strftime("%Y-%m-%dT%H:%M:%SZ") == cutoff)
    return value


def git(root, end, *args):
    require(time.monotonic() < end)
    env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": "", "GIT_OPTIONAL_LOCKS": "0"}
    result = snapshot.execute_process_group(["/usr/bin/git", "--no-replace-objects", "-C", str(root),
        "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", *args], env=env,
        stdin=subprocess.DEVNULL, timeout=min(10, end - time.monotonic()), max_output_bytes=2 << 20)
    require(result.returncode == 0 and time.monotonic() < end)
    return result.stdout


def source(value, end):
    """Bind current Python/pin bytes, not an assertion about historical loaded code."""
    root = path(value["source_root"])
    with directory(root), directory(root / ".git"):
        require(git(root, end, "rev-parse", "--show-toplevel").strip() == str(root).encode())
        require(git(root, end, "rev-parse", "HEAD").strip() == value["source_revision"].encode())
        rows = git(root, end, "ls-tree", "-rz", value["source_revision"], "--", "host", "scripts")
        expected, stamps, total = {}, {}, 0
        for row in rows.split(b"\0")[:-1]:
            mode_type_oid, name = row.split(b"\t")
            name = name.decode("utf-8")
            files.safe_name(name)
            if not name.endswith((".py", ".json")):
                continue
            mode, kind, oid = mode_type_oid.decode().split()
            require(mode in ("100644", "100755") and kind == "blob" and len(expected) < 4096)
            raw = read(root / name, 2 << 20, end)
            total += len(raw)
            require(total <= 64 << 20 and files.blob(raw) == oid)
            expected[name] = digest(raw)
            stamps[name] = files.identity(file_info(root / name))
        require(OWNER in expected and rows.endswith(b"\0"))
        # Reject untracked import shadows/extensions and bytecode, including in
        # subdirectories. Do not recursively scan unrelated weights or .git.
        seen, count = set(), 0
        for top in ("host", "scripts"):
            todo = [root / top]
            while todo:
                current = todo.pop()
                with directory(current) as fd, os.scandir(fd) as entries:
                    for entry in entries:
                        count += 1
                        require(count <= 16384 and time.monotonic() < end)
                        require(not entry.is_symlink())
                        if entry.is_dir(follow_symlinks=False):
                            todo.append(current / entry.name)
                        elif entry.name.endswith((".py", ".json", ".pyc", ".so", ".pth")):
                            seen.add(str((current / entry.name).relative_to(root)))
        require(seen == set(expected))
        return digest(encoded(expected)), stamps


def kernel(value, end, limit=LIMIT):
    raw = bytearray()
    with open(value, "rb", buffering=0) as stream:
        while True:
            require(time.monotonic() < end)
            part = stream.read(min(65536, limit + 1 - len(raw)))
            if not part:
                return bytes(raw)
            raw.extend(part)
            require(len(raw) <= limit)


def command(value):
    return b"\0".join(arg.encode("utf-8") for arg in value["argv"]) + b"\0"


def discover(value, end):
    matches = []
    with os.scandir(PROC) as entries:
        for count, entry in enumerate(entries):
            require(count < 16384 and time.monotonic() < end)
            if not entry.name.isdecimal():
                continue
            try:
                raw = kernel(PROC / entry.name / "cmdline", end)
            except (FileNotFoundError, ProcessLookupError):
                continue
            if raw == command(value):
                matches.append(int(entry.name))
    require(len(matches) == 1 and matches[0] > 1 and matches[0] != os.getpid())
    return matches[0]


def inode(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode, info.st_nlink)


def observe(pid, value, end):
    process = PROC / str(pid)
    require(kernel(process / "cmdline", end) == command(value))
    raw = kernel(process / "stat", end)
    require(raw.startswith(str(pid).encode() + b" (") and b") " in raw)
    fields = raw[raw.rindex(b") ") + 2:].split()
    require(len(fields) >= 20 and fields[0] not in (b"Z", b"X", b"x") and int(fields[19]) > 0)
    status = kernel(process / "status", end).splitlines()
    uids = [line.split()[1:] for line in status if line.startswith(b"Uid:")]
    require(uids == [[b"0"] * 4])
    masks = {}
    for name in ("SigCgt", "SigIgn", "SigBlk", "SigPnd", "ShdPnd"):
        rows = [line.split()[1:] for line in status if line.startswith((name + ":").encode())]
        require(len(rows) == 1 and len(rows[0]) == 1 and re.fullmatch(b"[0-9a-fA-F]{16}", rows[0][0]))
        masks[name] = int(rows[0][0], 16)
    term = 1 << (signal.SIGTERM - 1)
    require(masks["SigCgt"] & term and not any(masks[name] & term
            for name in ("SigIgn", "SigBlk", "SigPnd", "ShdPnd")))
    namespaces = {}
    for name in ("pid", "user", "mnt", "net"):
        left, right = (process / "ns" / name).stat(), (PROC / "self/ns" / name).stat()
        require((left.st_dev, left.st_ino) == (right.st_dev, right.st_ino))
        namespaces[name] = (left.st_dev, left.st_ino)
    executable = path(value["executable"])
    require(os.readlink(process / "exe") == str(executable))
    executable_info = file_info(executable)
    require(inode((process / "exe").stat()) == inode(executable_info))
    require(digest(read(executable, 64 << 20, end)) == value["executable_sha256"])
    logs = {}
    for number, name in ((1, "stdout"), (2, "stderr")):
        logfile = path(value[name])
        require(os.readlink(process / "fd" / str(number)) == str(logfile))
        info = file_info(logfile)
        require(inode((process / "fd" / str(number)).stat()) == inode(info))
        logs[name] = inode(info)
    with directory(path(value["state_dir"]), private=True) as fd:
        state = inode(os.fstat(fd))
    return {"pid": pid, "start_ticks": int(fields[19]), "uids": [0] * 4,
        "sigterm_caught_unblocked_not_pending": True,
        "executable": inode(executable_info), "executable_sha256": value["executable_sha256"],
        "namespaces": namespaces, "logs": logs, "state": state,
        "boot_id_sha256": digest(kernel(PROC / "sys/kernel/random/boot_id", end, 128))}


def live(pidfd):
    poll = select.poll()
    poll.register(pidfd, select.POLLIN)
    require(not poll.poll(0))


def persist(fd, name, result):
    raw = encoded(result)
    out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(out, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(fd)


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def report():
    return {"schema": "nib.selected-owner-cancellation/v1", "outcome": "rejected", "stage": "input",
        "started_at": now(), "finished_at": None, "signal_intent_at": None,
        "budget_seconds": SECONDS, "expectation_sha256": None, "source_revision": None,
        "source_closure_sha256": None, "current_identity": None, "identity_verified": False,
        "pidfd_bound": False, "signal_may_have_been_sent": False, "signal_sent": False,
        "original_launch_identity_proven": False, "owner_exit_observed": False,
        "cleanup_confirmed": False, "original_run_pass": False, "soak_validated": False,
        "helper_sources": {name: digest((ROOT / name).read_bytes()) for name in (
            "host/cancel-selected-service.py", "scripts/tau_runtime.py", "scripts/tau_trial_worker.py",
            "scripts/e2e/snapshot.py")}}


def request(value, result, end, output, send):
    require(sys.platform == "linux" and os.getuid() == 0)
    result.update(stage="source", source_revision=value["source_revision"])
    closure, stamps = source(value, end)
    result.update(stage="discovery", source_closure_sha256=closure)
    pid = discover(value, end)
    before = observe(pid, value, end)
    result["stage"] = "pidfd"
    # No numeric-PID fallback. ESRCH, permission denial and unsupported kernels
    # reject the operation, including a process exit during binding.
    pidfd = os.pidfd_open(pid, 0)
    try:
        result["pidfd_bound"] = True
        live(pidfd)
        result["stage"] = "recheck"
        require(observe(pid, value, end) == before and discover(value, end) == pid)
        require(source(value, end) == (closure, stamps))
        require(observe(pid, value, end) == before)
        live(pidfd)
        require(time.monotonic() < end)
        result.update(current_identity=before, identity_verified=True, outcome="identified", stage="identified")
        if send:
            result.update(signal_may_have_been_sent=True, outcome="unconfirmed", stage="signal", signal_intent_at=now())
            # Durable intent precedes the only target mutation. A crash or an
            # interrupted syscall must leave an ambiguous request, not a retry.
            persist(output, "signal-intent.json", result)
            require(discover(value, end) == pid)
            for name, stamp in stamps.items():
                require(time.monotonic() < end and
                        files.identity(file_info(path(value["source_root"]) / name)) == stamp)
            require(observe(pid, value, end) == before)
            live(pidfd)
            require(time.monotonic() < end)
            signal.pidfd_send_signal(pidfd, signal.SIGTERM, None, 0)
            result.update(signal_sent=True, outcome="signal-requested", stage="completed")
    finally:
        os.close(pidfd)


class QuietParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("cancellation input rejected")


def main(argv=None):
    result = report()
    handlers, output = {}, None
    cancelled = False
    restoring = False
    def cancel(*_):
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            if not restoring:
                raise InterruptedError("cancellation helper interrupted")
    try:
        end = time.monotonic() + SECONDS
        parser = QuietParser(description=__doc__, allow_abbrev=False)
        modes = parser.add_mutually_exclusive_group(required=True)
        modes.add_argument("--inspect", action="store_true")
        modes.add_argument("--cancel", action="store_true")
        parser.add_argument("--expectation", required=True)
        parser.add_argument("--output-dir", required=True)
        args = parser.parse_args(argv)
        for sig in (signal.SIGINT, signal.SIGTERM):
            handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, cancel)
        destination = path(args.output_dir)
        expected_path = path(args.expectation)
        require(stat.S_IMODE(file_info(expected_path).st_mode) == 0o600)
        raw = read(expected_path, LIMIT, end)
        value = expectation(raw)
        result["expectation_sha256"] = digest(raw)
        for name in ("source_root", "state_dir"):
            protected = path(value[name])
            require(not destination.is_relative_to(protected) and not protected.is_relative_to(destination))
        with directory(destination.parent, private=True) as parent:
            os.mkdir(destination.name, mode=0o700, dir_fd=parent)
            os.fsync(parent)
        with directory(destination, private=True) as output:
            persist(output, "initial.json", result)
            try:
                with alarm(end):
                    request(value, result, end, output, args.cancel)
            except BaseException:
                if not result["signal_may_have_been_sent"]:
                    result["outcome"] = "rejected"
            result["finished_at"] = now()
            persist(output, "receipt.json", result)
    except BaseException:
        # initial/intent remain on disk if writing the terminal receipt failed.
        result["outcome"] = "unconfirmed" if result["signal_may_have_been_sent"] else "rejected"
    finally:
        restoring = True
        if handlers:
            mask = signal.pthread_sigmask(signal.SIG_BLOCK, set(handlers))
            try:
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, mask)
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result["outcome"] in ("identified", "signal-requested") else 1


if __name__ == "__main__":
    sys.exit(main())
