# SPDX-License-Identifier: Apache-2.0
"""Verify fresh PID namespace/procfs before any pinned tools startup."""
import os
from pathlib import Path
import re


def require(value):
    if not value:
        raise ValueError("pid-namespace-check-rejected")


def check(original):
    # unshare --pid --fork execs this worker as PID 1. Before spawning anything,
    # a procfs mounted for that namespace must expose exactly this process.
    require(type(original) is str and re.fullmatch(r"pid:\[[0-9]{1,20}\]", original))
    current = os.readlink("/proc/self/ns/pid")
    require(re.fullmatch(r"pid:\[[0-9]{1,20}\]", current) and current != original
            and os.getpid() == 1 and os.getppid() == 0
            and os.readlink("/proc/self") == "1")
    with os.scandir("/proc") as entries:
        processes = {entry.name for entry in entries if entry.name.isascii() and entry.name.isdecimal()}
    require(processes == {"1"})
    status = Path("/proc/self/status").read_text(encoding="ascii")
    require(len(status) < 16384)
    fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    require(fields.get("NSpid", "").split() == ["1"]
            and fields.get("PPid", "").strip() == "0")
