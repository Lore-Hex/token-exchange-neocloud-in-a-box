# SPDX-License-Identifier: Apache-2.0
"""Read-only snapshots of the lab's owned API process, not tau admission receipts."""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

SCHEMA = "nib.lab-runtime-observation/v1"
SECONDS = 15
CLEANUP_SECONDS = 25  # Existing execute_process_group: two 10s waits + 5s absence check.
EVENTS = ("before-requests", "after-requests")
DIGESTS = ("process_sha256", "argv_sha256", "exe_sha256", "listener_sha256")
COUNTERS = ("memory.current", "memory.peak", "memory.max", "memory.swap.max", "pids.current", "pids.max")


def require(value):
    if not value:
        raise ValueError("runtime observation rejected")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def enabled(contract):
    require(contract.get("runtime_observation") is True
            and contract.get("operation") == "local-correctness"
            and contract.get("gpu_family") == "B300"
            and contract.get("gpu_security", "confidential") == "confidential")
    return True


def read_kernel(path, limit, deadline):
    # Fixed procfs/cgroup paths only. Require EOF, including after short reads.
    require(time.monotonic() < deadline)
    raw = bytearray()
    with open(path, "rb", buffering=0) as stream:
        while True:
            block = stream.read(min(65536, limit + 1 - len(raw)))
            require(type(block) is bytes and time.monotonic() < deadline)
            if not block:
                break
            raw.extend(block)
            require(len(raw) <= limit)
    require(raw)
    return bytes(raw)


def process(pid, deadline):
    root = Path("/proc") / str(pid)
    raw = read_kernel(root / "stat", 4096, deadline)
    require(raw.startswith(str(pid).encode() + b" ("))
    fields = raw[raw.rindex(b") ") + 2:].split()
    # Linux v6.17 Documentation/filesystems/proc.rst, Table 1-4.
    require(len(fields) >= 20 and fields[0] not in (b"Z", b"X", b"x")
            and root.stat().st_uid == os.getuid())
    parent, group, session, start = (int(fields[i]) for i in (1, 2, 3, 19))
    require(parent > 0 and group == session == pid and start > 0)
    namespaces = {name: os.stat(root / ("ns/" + name)).st_ino for name in ("net", "pid", "cgroup")}
    require(all(value == os.stat("/proc/self/ns/" + name).st_ino for name, value in namespaces.items()))
    require(read_kernel(root / "cgroup", 4096, deadline) == b"0::/\n"
            and read_kernel("/proc/self/cgroup", 4096, deadline) == b"0::/\n")
    null = os.stat(os.devnull)
    for number in (1, 2):
        info = os.stat(root / "fd" / str(number))
        require(stat.S_ISCHR(info.st_mode) and info.st_rdev == null.st_rdev)
    return {"pid": pid, "parent": parent, "start": start, "namespaces": namespaces,
            "boot": digest(read_kernel("/proc/sys/kernel/random/boot_id", 64, deadline)),
            "argv": digest(read_kernel(root / "cmdline", 65536, deadline))}


def listener(pid, deadline):
    address = b"0100007F:1F40"  # The lab's fixed IPv4 loopback port 8000.
    selected = []
    for suffix in ("tcp", "tcp6"):
        rows = read_kernel("/proc/net/" + suffix, 4 << 20, deadline).splitlines()[1:]
        for row in rows:
            fields = row.split()
            require(len(fields) >= 10)
            if fields[3] == b"0A" and fields[1].endswith(b":1F40"):
                require(suffix == "tcp" and fields[1] == address and fields[2] == b"00000000:0000")
                selected.append(int(fields[9]))
    require(len(selected) == 1 and selected[0] > 0)
    owned = set()
    with os.scandir(Path("/proc") / str(pid) / "fd") as entries:
        for count, entry in enumerate(entries):
            require(count < 4096 and time.monotonic() < deadline)
            try:
                target = os.readlink(entry.path)
            except FileNotFoundError:
                continue
            match = re.fullmatch(r"socket:\[([0-9]+)\]", target)
            if match:
                owned.add(int(match[1]))
    require(selected[0] in owned)
    return selected[0]


def observe(pid, contract, contract_sha, event, deadline):
    enabled(contract)
    require(sys.platform == "linux" and type(pid) is int and 1 < pid < 2**31 and event in EVENTS)
    before = process(pid, deadline)
    require(before["parent"] == os.getppid())
    inode = listener(pid, deadline)
    expected = contract["interpreter"]
    # Deliberately follow /proc/PID/exe to the running executable, not its pathname.
    with open(Path("/proc") / str(pid) / "exe", "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and 0 < info.st_size == expected["bytes"] <= 16 << 20)
        raw = stream.read((16 << 20) + 1)
        def stamp(value):
            return (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        require(len(raw) == info.st_size and digest(raw) == expected["sha256"]
                and stamp(info) == stamp(os.fstat(stream.fileno())) and time.monotonic() < deadline)
    values = {}
    for name in COUNTERS:
        raw = read_kernel("/sys/fs/cgroup/" + name, 32, deadline)
        require(re.fullmatch(rb"[0-9]+\n", raw))
        values[name] = int(raw)
    require(process(pid, deadline) == before and listener(pid, deadline) == inode)
    require(stamp(os.stat(Path("/proc") / str(pid) / "exe")) == stamp(info)
            and time.monotonic() < deadline)
    sample = {"event": event, "process_sha256": digest(encoded(before)), "argv_sha256": before["argv"],
              "exe_sha256": expected["sha256"], "listener_sha256": digest(encoded({"process": before, "inode": inode})),
              "stdio_null": True, "cgroup": values}
    require(type(contract_sha) is str and re.fullmatch("[0-9a-f]{64}", contract_sha))
    validate_sample(sample, contract)
    return sample


def validate_sample(sample, contract):
    require(type(sample) is dict and set(sample) == {"event", *DIGESTS, "stdio_null", "cgroup"}
            and sample["event"] in EVENTS and sample["stdio_null"] is True)
    for name in DIGESTS:
        require(type(sample[name]) is str and re.fullmatch("[0-9a-f]{64}", sample[name]))
    require(sample["exe_sha256"] == contract["interpreter"]["sha256"])
    values = sample["cgroup"]
    require(type(values) is dict and set(values) == set(COUNTERS)
            and all(type(n) is int and 0 <= n < 2**63 for n in values.values()))
    replicas = contract.get("data_parallel_size", 1)
    require(type(replicas) is int and replicas in (1, 2))
    require(values["memory.max"] == (512 * replicas) << 30 and values["memory.swap.max"] == 0
            and values["pids.max"] == 1024 and 0 < values["pids.current"] <= 1024
            and 0 < values["memory.current"] <= values["memory.peak"] <= values["memory.max"])


def validate(row, contract, contract_sha, *, complete=False):
    enabled(contract)
    require(type(contract_sha) is str and re.fullmatch("[0-9a-f]{64}", contract_sha))
    require(type(row) is dict and set(row) == {"schema", "contract_sha256", "tau_admission", "quality_eligible", "samples"}
            and row["schema"] == SCHEMA and row["contract_sha256"] == contract_sha
            and row["tau_admission"] is False and row["quality_eligible"] is False
            and type(row["samples"]) is list and len(row["samples"]) <= 2)
    for index, sample in enumerate(row["samples"]):
        validate_sample(sample, contract)
        require(sample["event"] == EVENTS[index])
    if len(row["samples"]) == 2:
        before, after = row["samples"]
        require(all(before[name] == after[name] for name in DIGESTS)
                and after["cgroup"]["memory.peak"] >= before["cgroup"]["memory.peak"])
    require(not complete or len(row["samples"]) == 2)
    return row
