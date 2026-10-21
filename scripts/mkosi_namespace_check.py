#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fixed synthetic child of the authenticated mkosi sandbox; no caller commands."""
import errno
import os
from pathlib import Path
import re
import socket
import sys

NAMES = ("user", "mnt", "net", "ipc")
SUCCESS = b"NIB_SANDBOX_OK\n"


def require(condition):
    if not condition:
        raise ValueError("namespace-check-rejected")


def check(original):
    require(len(original) == len(NAMES) and os.geteuid() == 0 and os.getegid() == 0)
    for name, before in zip(NAMES, original):
        pattern = re.escape(name) + r":\[[0-9]{1,20}\]"
        after = os.readlink("/proc/self/ns/" + name)
        require(re.fullmatch(pattern, before) and re.fullmatch(pattern, after) and before != after)
    require(socket.if_nameindex() == [(1, "lo")])
    # Only our own synthetic paths are touched. Read-only errors, not mere lack
    # of permissions, prove the worker directory cannot be changed by root.
    try:
        fd = os.open("/nib/write-probe", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        require(exc.errno == errno.EROFS)
    else:
        os.close(fd)
        os.unlink("/nib/write-probe")
        raise ValueError("namespace-check-rejected")
    path = Path("/tmp/write-probe")
    with path.open("xb") as stream:
        stream.write(b"synthetic")
    try:
        require(path.read_bytes() == b"synthetic")
    finally:
        path.unlink()


def main():
    require(sys.platform == "linux" and sys.flags.isolated and sys.flags.no_site
            and sys.flags.dont_write_bytecode)
    check(sys.argv[1:])
    sys.stdout.buffer.write(SUCCESS)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError):
        sys.exit("Namespace check rejected.")
