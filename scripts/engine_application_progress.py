# SPDX-License-Identifier: Apache-2.0
"""Bounded metadata from the owned CPU container; never an integrity shortcut."""

import json
import os
import re
import stat
import time

SCHEMA = "nib.cpu-application-progress/v1"
PHASES = ("layout", "inspection", "application", "complete")
OPERATIONS = ("idle", "inventory", "read", "write", "unlink")
EVENTS = ("boundary", "inventory", "periodic", "terminal")
COUNTS = ("inventories_started", "inventories_completed", "reads_completed", "read_bytes_completed",
          "writes_completed", "unlinks_completed")
MEMORY = ("anon", "file", "workingset_refault_file", "pgscan", "pgsteal")
MEMORY_EVENTS = ("high", "max", "oom", "oom_kill")
IO = ("rbytes", "wbytes", "rios", "wios")
CGROUP_KEYS = {"memory.current", "memory.peak", "memory.max", "memory.swap.max",
               *("memory.stat." + key for key in MEMORY),
               *("memory.events." + key for key in MEMORY_EVENTS), *("io." + key for key in IO)}
MAX_SAMPLES = 16
INTERVAL_SECONDS = 60
MAX_NUMBER = 2**63 - 1
FILES = ("memory.current", "memory.peak", "memory.max", "memory.swap.max",
         "memory.stat", "memory.events", "io.stat")


def require(value):
    if not value:
        raise ValueError("CPU application progress rejected")


def read_kernel(path, deadline):
    require(path in ("/proc/self/cgroup", "/proc/self/mountinfo")
            or path in tuple("/sys/fs/cgroup/" + name for name in FILES))
    require(time.monotonic() < deadline)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb", buffering=0) as stream:
        require(stat.S_ISREG(os.fstat(fd).st_mode))
        raw = bytearray()
        while True:
            block = stream.read(65537 - len(raw))
            require(type(block) is bytes and time.monotonic() < deadline)
            if not block:
                return bytes(raw)
            raw.extend(block)
            require(len(raw) <= 65536)


def number(raw):
    require(type(raw) is bytes and re.fullmatch(rb"[0-9]{1,19}", raw))
    value = int(raw)
    require(value <= MAX_NUMBER)
    return value


def keyed(raw, selected):
    result = {}
    for line in raw.splitlines():
        fields = line.split()
        require(len(fields) == 2 and re.fullmatch(rb"[a-z][a-z0-9_.]{0,63}", fields[0]))
        key = fields[0].decode("ascii")
        require(key not in result)
        result[key] = number(fields[1])
    require(set(selected) <= set(result))
    return {key: result[key] for key in selected}


def cgroup(deadline):
    # Linux v6.17 cgroup-v2.rst: private namespace root, memory and IO interfaces.
    # The owner verifies private cgroup/PID namespaces and the exact limits before start.
    require(read_kernel("/proc/self/cgroup", deadline) == b"0::/\n")
    mounts = []
    for line in read_kernel("/proc/self/mountinfo", deadline).splitlines():
        fields = line.split()
        if len(fields) >= 10 and fields[4] == b"/sys/fs/cgroup":
            separator = fields.index(b"-")
            require(fields[3] == b"/" and b"ro" in fields[5].split(b",")
                    and fields[separator + 1] == b"cgroup2")
            mounts.append(fields)
    require(len(mounts) == 1)
    raw = {name: read_kernel("/sys/fs/cgroup/" + name, deadline) for name in FILES}
    values = {name: number(raw[name].removesuffix(b"\n")) for name in FILES[:4]}
    require(values["memory.max"] == 8 << 30 and values["memory.swap.max"] == 0)
    for filename, keys in (("memory.stat", MEMORY), ("memory.events", MEMORY_EVENTS)):
        values.update({filename + "." + key: value for key, value in keyed(raw[filename], keys).items()})
    devices, totals = set(), dict.fromkeys(IO, 0)
    for line in raw["io.stat"].splitlines():
        fields = line.split()
        require(fields and re.fullmatch(rb"[0-9]{1,10}:[0-9]{1,10}", fields[0])
                and fields[0] not in devices and len(devices) < 128)
        devices.add(fields[0])
        row = keyed(b"\n".join(field.replace(b"=", b" ") for field in fields[1:]), IO)
        for key in IO:
            totals[key] += row[key]
            require(totals[key] <= MAX_NUMBER)
    values.update({"io." + key: value for key, value in totals.items()})
    require(read_kernel("/proc/self/cgroup", deadline) == b"0::/\n")
    return values


class Progress:
    def __init__(self, source_sha256, started, deadline):
        self.started, self.deadline, self.cpu_started = started, deadline, time.process_time()
        self.phase, self.operation = "layout", "idle"
        self.counts = dict.fromkeys(COUNTS, 0)
        self.last_sample = started - INTERVAL_SECONDS
        self.row = {"schema": SCHEMA, "source_sha256": source_sha256, "samples": [], "samples_omitted": 0}

    def sample(self, event):
        now = time.monotonic()
        if event == "periodic" and now - self.last_sample < INTERVAL_SECONDS:
            return
        if event != "terminal" and len(self.row["samples"]) >= MAX_SAMPLES - 1:
            self.row["samples_omitted"] += 1
            self.last_sample = now
            return
        require(len(self.row["samples"]) < MAX_SAMPLES)
        state, values = "observed", None
        try:
            values = cgroup(self.deadline)
        except Exception:
            state = "deadline" if time.monotonic() >= self.deadline else "unavailable"
        sample = {"event": event, "phase": self.phase, "operation": self.operation,
                  "elapsed_ms": int((time.monotonic() - self.started) * 1000),
                  "process_cpu_ms": int((time.process_time() - self.cpu_started) * 1000),
                  **self.counts, "cgroup_status": state, "cgroup": values}
        self.row["samples"].append(sample)
        self.last_sample = now
        # Preserve fixed failure metadata, but never continue without telemetry.
        require(state == "observed" or event == "terminal")

    def boundary(self, phase):
        require(phase in PHASES and PHASES.index(phase) >= PHASES.index(self.phase))
        self.phase = phase
        self.sample("boundary")

    def finish(self, completed=False):
        self.sample("terminal")
        final = self.row["samples"][-1]
        if completed and final["cgroup_status"] == "observed" and time.monotonic() <= self.deadline:
            self.phase = final["phase"] = "complete"
            return True
        return False

    def wrap(self, tree):
        progress = self
        class ObservedTree(type(tree)):
            def inventory(self):
                progress.counts["inventories_started"] += 1
                require(progress.counts["inventories_started"] <= 8)
                progress.operation = "inventory"
                progress.sample("inventory")
                result = super().inventory()
                progress.counts["inventories_completed"] += 1
                progress.operation = "idle"
                return result

            def read(self, name, *, retain=True, max_bytes=None):
                previous, progress.operation = progress.operation, "read"
                result = super().read(name, retain=retain, max_bytes=max_bytes)
                progress.counts["reads_completed"] += 1
                progress.counts["read_bytes_completed"] += len(result) if retain else result["bytes"]
                progress.operation = previous
                progress.sample("periodic")
                return result

            def write(self, name, data, mode):
                progress.operation = "write"
                super().write(name, data, mode)
                progress.counts["writes_completed"] += 1
                progress.operation = "idle"

            def unlink(self, name):
                progress.operation = "unlink"
                super().unlink(name)
                progress.counts["unlinks_completed"] += 1
                progress.operation = "idle"
        result = ObservedTree(tree.fd)
        result.deadline = tree.deadline
        return result


def validate(row, source_sha256, *, application_completed, elapsed_ms):
    require(len(json.dumps(row, sort_keys=True, indent=2, allow_nan=False).encode()) <= 32768)
    require(type(row) is dict and set(row) == {"schema", "source_sha256", "samples", "samples_omitted"}
            and row["schema"] == SCHEMA and row["source_sha256"] == source_sha256
            and type(source_sha256) is str and re.fullmatch(r"[0-9a-f]{64}", source_sha256)
            and type(row["samples_omitted"]) is int and 0 <= row["samples_omitted"] <= 1000
            and type(row["samples"]) is list and 1 <= len(row["samples"]) <= MAX_SAMPLES)
    previous = None
    for index, sample in enumerate(row["samples"]):
        require(type(sample) is dict and set(sample) == {"event", "phase", "operation", "elapsed_ms",
                "process_cpu_ms", *COUNTS, "cgroup_status", "cgroup"}
                and sample["event"] in EVENTS and sample["phase"] in PHASES
                and sample["operation"] in OPERATIONS
                and all(type(sample[key]) is int and 0 <= sample[key] <= MAX_NUMBER
                        for key in (*COUNTS, "elapsed_ms", "process_cpu_ms"))
                # CPU phases independently truncate milliseconds; allow only that rounding.
                and sample["elapsed_ms"] <= elapsed_ms + 2
                and sample["inventories_completed"] <= sample["inventories_started"] <= 8
                and sample["inventories_started"] - sample["inventories_completed"] <= 1
                and sample["cgroup_status"] in ("observed", "unavailable", "deadline"))
        require((sample["event"] == "terminal") == (index == len(row["samples"]) - 1))
        if index == 0:
            require(sample["event"] == "boundary" and sample["phase"] == "layout"
                    and sample["operation"] == "idle" and not any(sample[key] for key in COUNTS))
        if sample["cgroup_status"] == "deadline":
            require(sample["elapsed_ms"] >= 720000)
        if sample["cgroup_status"] == "observed":
            values = sample["cgroup"]
            require(type(values) is dict and set(values) == CGROUP_KEYS
                    and all(type(value) is int and 0 <= value <= MAX_NUMBER for value in values.values())
                    and values["memory.max"] == 8 << 30 and values["memory.swap.max"] == 0)
        else:
            require(sample["cgroup"] is None and not application_completed)
        if previous is not None:
            require(all(sample[key] >= previous[key] for key in (*COUNTS, "elapsed_ms", "process_cpu_ms"))
                    and PHASES.index(sample["phase"]) >= PHASES.index(previous["phase"]))
        if sample["phase"] in ("layout", "inspection"):
            require(sample["inventories_started"] <= (0 if sample["phase"] == "layout" else 1)
                    and sample["writes_completed"] == sample["unlinks_completed"] == 0)
        if sample["event"] == "inventory":
            require(sample["operation"] == "inventory"
                    and sample["inventories_started"] == sample["inventories_completed"] + 1)
        previous = sample
    require((previous["phase"] == "complete") == application_completed)
    if application_completed:
        require(previous["inventories_started"] == previous["inventories_completed"] == 8
                and previous["operation"] == "idle" and previous["reads_completed"] >= 8
                and previous["read_bytes_completed"] > 0)
