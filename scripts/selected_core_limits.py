# SPDX-License-Identifier: Apache-2.0
"""Fixed core policy and a bounded, same-UID container limit worker."""

import json
import os
from pathlib import Path
import resource
import signal
import sys

SETTINGS = {"fs/suid_dumpable": b"0\n", "kernel/core_uses_pid": b"0\n", "kernel/core_pattern": b"\n"}
CONFIG = b"fs.suid_dumpable = 0\nkernel.core_uses_pid = 0\nkernel.core_pattern =\n"
MAX_PROCESSES = 1024


def require(value):
    if not value:
        raise ValueError("core-policy-rejected")


def read(path, limit=16384):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit)
    return raw


def host_values():
    return {key: read(Path("/proc/sys") / key, 256).decode("ascii") for key in SETTINGS}


def check_host():
    require(host_values() == {key: value.decode() for key, value in SETTINGS.items()})


def namespace(path):
    info = os.stat(path)
    return [info.st_dev, info.st_ino]


def processes():
    result = []
    with os.scandir("/proc") as entries:
        for count, entry in enumerate(entries):
            require(count < 4096)
            if entry.name.isdecimal():
                require(len(result) < MAX_PROCESSES)
                result.append(int(entry.name))
    require(1 in result and os.getpid() in result)
    return sorted(result)


def same_user(pid):
    rows = read(Path("/proc") / str(pid) / "status").splitlines()
    for prefix in (b"Uid:", b"Gid:"):
        fields = [row.split()[1:] for row in rows if row.startswith(prefix)]
        require(fields == [[b"65532"] * 4])


def scan(apply):
    pairs, count, zero = set(), 0, 0
    for pid in processes():
        if pid == os.getpid():
            continue
        try:
            same_user(pid)
            if apply:
                resource.prlimit(pid, resource.RLIMIT_CORE, (0, 0))
            pair = resource.prlimit(pid, resource.RLIMIT_CORE)
            require(not apply or pair == (0, 0))
            pairs.add(pair)
            count += 1
            zero += pair == (0, 0)
        except ProcessLookupError:
            require(pid != 1)
        except FileNotFoundError:
            require(pid != 1)
    require(count > 0)
    return {"processes": count, "zero": zero, "limits": [list(pair) for pair in sorted(pairs)]}


def worker(mode, expected):
    require(sys.platform == "linux" and os.getpid() > 1 and mode in ("inspect", "apply"))
    require(os.getresuid() == os.getresgid() == (65532, 65532, 65532))
    require(namespace("/proc/self/ns/pid") == namespace("/proc/1/ns/pid") == expected)
    if mode == "apply":
        # Lower parents first; a second bounded pass covers children forked during
        # the first. The final read-only pass must find no nonzero limit.
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        scan(True)
        scan(True)
    result = scan(False)
    require(mode != "apply" or result["zero"] == result["processes"])
    return result


def main():
    try:
        require(len(sys.argv) == 4 and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode)
        expected = [int(value) for value in sys.argv[2:]]
        require(all(value > 0 for value in expected))
        signal.signal(signal.SIGALRM, lambda *_: sys.exit(1))
        signal.setitimer(signal.ITIMER_REAL, 8)
        result = worker(sys.argv[1], expected)
        print(json.dumps(result, sort_keys=True))
        return 0
    except BaseException:
        print('{"result":"fail","reason":"core-policy-rejected"}')
        return 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == "__main__":
    sys.exit(main())
