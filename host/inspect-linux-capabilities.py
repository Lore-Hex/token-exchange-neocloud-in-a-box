#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read-only, sanitized observations; never delegation or kernel-test proof."""
from contextlib import ExitStack, contextmanager
import json
import os
import re
import signal
import socket
import stat
import sys
import time

SMALL_LIMIT = 4096
MOUNT_LIMIT = 1 << 20
TOTAL_LIMIT = 2 << 20
OUTPUT_LIMIT = 65536
ANCESTOR_LIMIT = 16
SECONDS = 10
CGROUP_ROOT = "/sys/fs/cgroup"
CONTROLLERS = ("cpu", "memory", "pids")
NAMESPACES = ("cgroup", "mnt", "pid", "user", "time")
LIMITS = ("memory.max", "memory.swap.max", "memory.oom.group", "pids.max", "cpu.max")
ROOT_FIELDS = ("cgroup.controllers", "cgroup.subtree_control", "cgroup.type", "cgroup.procs")
LEAVES = frozenset(ROOT_FIELDS + LIMITS + ("memory.peak", "memory.events", "cgroup.kill"))
PROC_FIELDS = frozenset(("mountinfo", "cgroup", "status", "uid_map"))
FATAL = frozenset(("malformed", "read-limit", "read-budget", "timeout", "internal", "unsafe-path"))


class Rejected(Exception):
    def __init__(self, code):
        self.code = code


def require(ok, code="malformed"):
    if not ok:
        raise Rejected(code)


def error_code(error):
    if isinstance(error, FileNotFoundError):
        return "missing"
    if isinstance(error, PermissionError):
        return "denied"
    return "unavailable"


def text(raw):
    try:
        return raw.decode("ascii")
    except (UnicodeError, AttributeError):
        raise Rejected("malformed") from None


def number(value):
    require(re.fullmatch(r"[0-9]{1,20}", value) is not None)
    result = int(value)
    require(result <= (1 << 64) - 1)
    return result


def mount_field(value):
    escapes = {"040": " ", "011": "\t", "012": "\n", "134": "\\"}
    require(re.search(r"\\(?!040|011|012|134)", value) is None)
    return re.sub(r"\\(040|011|012|134)", lambda match: escapes[match[1]], value)


def mounts(raw):
    rows = []
    value = text(raw)
    require(value.endswith("\n"))
    for line in value.splitlines():
        fields = line.split(" ")
        require(len(fields) >= 10 and all(fields) and fields.count("-") == 1)
        split = fields.index("-")
        require(split >= 6 and len(fields) == split + 4)
        identifier = number(fields[0])
        require(identifier > 0)
        number(fields[1])
        require(re.fullmatch(r"[0-9]+:[0-9]+", fields[2]) is not None)
        root, target = mount_field(fields[3]), mount_field(fields[4])
        require(root.startswith("/") and target.startswith("/"))
        if target == CGROUP_ROOT or target.startswith(CGROUP_ROOT + "/"):
            options, super_options = set(fields[5].split(",")), set(fields[split + 3].split(","))
            require(len(options & {"rw", "ro"}) == 1 and len(super_options & {"rw", "ro"}) == 1)
            rows.append({"id": identifier, "target": target, "root": root,
                         "v2": fields[split + 1] == "cgroup2",
                         "rw": "rw" in options and "rw" in super_options})
    return rows


def descriptor_mount(raw):
    # Same mnt_id binding as inspect-public-weight-cache.mount_id; device alone
    # cannot distinguish a bind mount. The caller charges this bounded read.
    matches = re.findall(r"^mnt_id:[ \t]*([0-9]+)$", text(raw), re.MULTILINE)
    require(len(matches) == 1 and number(matches[0]) > 0)
    return number(matches[0])


def membership(raw):
    value = text(raw)
    require(value.endswith("\n"))
    rows = value.splitlines()
    unified = []
    for row in rows:
        fields = row.split(":", 2)
        require(len(fields) == 3 and fields[2].startswith("/"))
        number(fields[0])
        if fields[0] == "0":
            require(fields[1] == "")
            unified.append(fields[2])
    require(len(unified) <= 1)
    return {"unified_only": len(rows) == len(unified) == 1,
            "path": unified[0] if unified else None}


def mapped_parts(root, path):
    # Subtree mount roots can refer to a different namespace coordinate system.
    # Do not strip prefixes or guess the correspondence.
    require(root == "/" and type(path) is str and path.startswith("/"), "unsupported-mapping")
    parts = () if path == "/" else tuple(path[1:].split("/"))
    require(all(re.fullmatch(r"[A-Za-z0-9_.:-]+", part) and part not in (".", "..")
                for part in parts), "unsupported-mapping")
    require(len(parts) + 1 <= ANCESTOR_LIMIT, "ancestor-depth")
    return parts


def policy(raw):
    wanted = {"NoNewPrivs", "Seccomp", "CapEff"}
    values = {}
    for line in text(raw).splitlines():
        key, separator, value = line.partition(":")
        if key in wanted:
            require(separator and key not in values)
            values[key] = value.strip()
    require(set(values) == wanted and values["NoNewPrivs"] in ("0", "1")
            and values["Seccomp"] in ("0", "1", "2")
            and re.fullmatch(r"[0-9a-fA-F]{1,16}", values["CapEff"]) is not None)
    return {"no_new_privs": values["NoNewPrivs"] == "1", "seccomp": int(values["Seccomp"]),
            "cap_eff": "%016x" % int(values["CapEff"], 16)}


def uid_mapping(raw):
    rows = []
    for line in text(raw).splitlines():
        fields = line.split()
        require(len(fields) == 3)
        inside, outside, count = map(number, fields)
        require(count > 0 and max(inside + count, outside + count) <= 1 << 32)
        rows.append((inside, outside, count))
    require(rows)
    for index in (0, 1):
        ordered = sorted(rows, key=lambda row: row[index])
        require(all(a[index] + a[2] <= b[index] for a, b in zip(ordered, ordered[1:])))
    root = next((row for row in rows if row[0] == 0), None)
    return "no-root-mapping" if root is None else ("root-to-root" if root[1] == 0 else "root-mapped")


def controllers(raw):
    values = text(raw).split()
    require(len(values) == len(set(values)) and all(re.fullmatch(r"[a-z_]+", v) for v in values))
    return {name: name in values for name in CONTROLLERS}


def group_type(raw):
    value = text(raw).strip()
    require(value in ("domain", "domain threaded", "domain invalid", "threaded"))
    return value


def processes_empty(raw):
    values = text(raw).splitlines()
    require(all(number(value) > 0 for value in values))
    return not values


def limit_value(name, raw):
    value = text(raw).strip()
    if name == "memory.events":
        rows = {}
        for line in value.splitlines():
            fields = line.split()
            require(len(fields) == 2 and re.fullmatch(r"[a-z_]+", fields[0]) and fields[0] not in rows)
            rows[fields[0]] = number(fields[1])
        require("oom" in rows and "oom_kill" in rows)
        return {key: rows[key] for key in ("oom", "oom_kill", "oom_group_kill") if key in rows}
    if name == "cpu.max":
        fields = value.split()
        require(len(fields) == 2)
        quota = "max" if fields[0] == "max" else number(fields[0])
        period = number(fields[1])
        require(period > 0 and (quota == "max" or quota > 0))
        return {"quota": quota, "period": period}
    if name == "memory.oom.group":
        require(value in ("0", "1"))
        return int(value)
    if value == "max":
        require(name in ("memory.max", "memory.swap.max", "pids.max"))
        return "max"
    return number(value)


class Linux:
    """Fixed proc/sysfs boundary. Only the named namespace handles are followed."""
    def __init__(self):
        self.descriptors = set()
        self.group_root = None
        self.group_verify = None

    def retain(self, stack, fd):
        self.descriptors.add(fd)
        def close():
            try:
                os.close(fd)
            finally:
                self.descriptors.remove(fd)
        stack.callback(close)
        return fd

    @contextmanager
    def directory(self, parts):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        with ExitStack() as stack:
            fd = self.retain(stack, os.open("/", flags))
            for part in parts:
                require(part not in ("", ".", "..") and "/" not in part, "unsafe-path")
                fd = self.retain(stack, os.open(part, flags, dir_fd=fd))
            yield fd

    @contextmanager
    def pin_group(self, expected, mount_id):
        require(self.group_root is None, "internal")
        def verify(fd):
            require(mount_id(fd) == expected, "mount-correspondence")
        with self.directory(("sys", "fs", "cgroup")) as fd:
            verify(fd)
            self.group_root, self.group_verify = fd, verify
            try:
                yield
            finally:
                self.group_root, self.group_verify = None, None

    @contextmanager
    def group_directory(self, parts):
        require(self.group_root is not None, "mount-correspondence")
        with ExitStack() as stack:
            fd = self.group_root
            for part in parts:
                fd = self.retain(stack, os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd))
                self.group_verify(fd)
            yield fd

    def recheck_group(self):
        with self.directory(("sys", "fs", "cgroup")) as fd:
            self.group_verify(fd)

    def facts(self):
        return {"linux": sys.platform == "linux", "architecture":
                "x86_64" if os.uname().machine == "x86_64" else "other",
                "uid_zero": os.getuid() == 0, "euid_zero": os.geteuid() == 0,
                "python_interfaces": {"pidfd_open": hasattr(os, "pidfd_open"),
                    "pidfd_send_signal": hasattr(signal, "pidfd_send_signal"),
                    "so_peercred": hasattr(socket, "SO_PEERCRED"),
                    "sock_seqpacket": hasattr(socket, "SOCK_SEQPACKET")}}

    def location(self, area, parts):
        if area == "proc":
            require(len(parts) == 1 and parts[0] in PROC_FIELDS, "unsafe-path")
            # Resolve self using our own PID, not an arbitrary /proc/self link.
            return ("proc", str(os.getpid())), parts[0]
        if area == "fdinfo":
            require(len(parts) == 1 and type(parts[0]) is int and parts[0] in self.descriptors, "unsafe-path")
            return ("proc", str(os.getpid()), "fdinfo"), str(parts[0])
        require(area == "cgroup" and parts and parts[-1] in LEAVES, "unsafe-path")
        require(len(parts) <= ANCESTOR_LIMIT and all(re.fullmatch(r"[A-Za-z0-9_.:-]+", p)
                and p not in (".", "..") for p in parts[:-1]), "unsafe-path")
        return ("sys", "fs", "cgroup") + parts[:-1], parts[-1]

    @contextmanager
    def open(self, area, parts):
        parent, name = self.location(area, parts)
        directory = self.group_directory(parts[:-1]) if area == "cgroup" else self.directory(parent)
        with directory as fd, ExitStack() as stack:
            child = self.retain(stack, os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd))
            require(stat.S_ISREG(os.fstat(child).st_mode), "unsafe-path")
            if area == "cgroup":
                self.group_verify(child)
            yield child

    def read(self, fd, maximum, check):
        raw = bytearray()
        try:
            while len(raw) < maximum:
                check()
                part = os.read(fd, min(65536, maximum - len(raw)))
                if not part:
                    break
                raw.extend(part)
        except OSError as error:
            return bytes(raw), error_code(error)
        except Rejected as error:
            return bytes(raw), error.code
        return bytes(raw), None

    def root_metadata(self):
        require(self.group_root is not None, "mount-correspondence")
        info = os.fstat(self.group_root)
        return {"owned_by_root": info.st_uid == 0, "protected_mode": not bool(info.st_mode & 0o022)}

    def kill_interface(self, parts):
        # No read/write of this interface. If a read-only open is denied, its
        # mount cannot be bound by this collector and presence stays unknown.
        with self.open("cgroup", parts + ("cgroup.kill",)):
            return True

    def namespace(self, actor, name):
        require(actor in ("self", "init") and name in NAMESPACES, "unsafe-path")
        pid = str(os.getpid()) if actor == "self" else "1"
        with self.directory(("proc", pid, "ns")) as fd:
            # The only intentional magic-link follows; no content is opened.
            info = os.stat(name, dir_fd=fd, follow_symlinks=True)
            return info.st_dev, info.st_ino


class Collector:
    def __init__(self, boundary, clock):
        self.boundary, self.clock = boundary, clock
        self.deadline = clock() + SECONDS
        self.used = 0
        self.errors = []

    def check(self):
        require(self.clock() < self.deadline, "timeout")

    def observe(self, field, call):
        try:
            self.check()
            value = call()
            self.check()
            return value
        except Rejected as error:
            code = error.code
        except OSError as error:
            code = error_code(error)
        except Exception:
            code = "internal"
        self.errors.append({"field": field, "reason": code})
        return None

    def read(self, area, parts, parser, limit=SMALL_LIMIT):
        self.check()
        require(self.used < TOTAL_LIMIT, "read-budget")
        with self.boundary.open(area, parts) as fd:
            # Opening a cgroup descriptor first validates its fdinfo, charging
            # the same budget before computing the remaining payload capacity.
            self.check()
            room = TOTAL_LIMIT - self.used
            require(room > 0, "read-budget")
            maximum = min(room, limit + 1)
            raw, error = self.boundary.read(fd, maximum, self.check)
            require(type(raw) is bytes, "internal")
            self.used += len(raw)  # Charge partial, rejected and sentinel bytes too.
            require(len(raw) <= maximum, "read-budget")
            self.check()
            require(error in (None, "missing", "denied", "unavailable", "unsafe-path", "timeout"), "internal")
            if error is not None:
                raise Rejected(error)
            require(len(raw) <= limit, "read-limit")
            require(not (maximum < limit + 1 and len(raw) == maximum), "read-budget")
            result = parser(raw)
        self.check()
        return result


def collect(boundary, clock=time.monotonic):
    io = Collector(boundary, clock)
    observations, blocked = {}, []
    facts = io.observe("process", boundary.facts)
    observations["process"] = facts
    if facts is not None:
        if not facts["linux"]: blocked.append("not-linux")
        if facts["architecture"] != "x86_64": blocked.append("not-x86_64")
        if not facts["euid_zero"]: blocked.append("not-root")
    for name, parser in (("status", policy), ("uid_map", uid_mapping)):
        observations[name] = io.observe(name, lambda name=name, parser=parser: io.read("proc", (name,), parser))
    observations["namespaces"] = {}
    for name in NAMESPACES:
        same = io.observe("namespace." + name, lambda name=name:
            boundary.namespace("self", name) == boundary.namespace("init", name))
        observations["namespaces"][name] = "unknown" if same is None else ("same-as-pid1" if same else "different-from-pid1")
    mount_rows = io.observe("mountinfo", lambda: io.read("proc", ("mountinfo",), mounts, MOUNT_LIMIT))
    member = io.observe("membership", lambda: io.read("proc", ("cgroup",), membership))
    roots = [] if mount_rows is None else [row for row in mount_rows if row["target"] == CGROUP_ROOT]
    mount = roots[0] if len(roots) == 1 else None
    observations["mount"] = None if mount_rows is None else {"count": len(roots),
        "cgroup2": mount["v2"] if mount else None, "rw": mount["rw"] if mount else None,
        "root_class": ("root" if mount["root"] == "/" else "subtree") if mount else "unknown"}
    observations["membership"] = None if member is None else {"unified_only": member["unified_only"],
        "location": "unknown" if member["path"] is None else ("root" if member["path"] == "/" else "nested")}
    if mount_rows is not None and len(roots) != 1: blocked.append("cgroup-mount-count")
    if mount is not None and not mount["v2"]: blocked.append("not-cgroup2")
    if mount is not None and not mount["rw"]: blocked.append("cgroup-read-only")
    if member is not None and not member["unified_only"]: blocked.append("not-unified-membership")
    observations["root"] = {}
    observations["ancestry"] = {"mapping": "unknown", "visible": [], "effective_limits_complete": False}
    def group_observations():
        # Any nested mount is unsupported, including a same-filesystem bind.
        require(len(mount_rows) == 1, "mount-correspondence")
        with boundary.pin_group(mount["id"], lambda fd: io.read("fdinfo", (fd,), descriptor_mount)):
            meta = io.observe("root.metadata", boundary.root_metadata)
            observations["root"]["metadata"] = meta
            if meta is not None and not all(meta.values()): blocked.append("cgroup-root-permissions")
            for name, parser in (("cgroup.controllers", controllers), ("cgroup.subtree_control", controllers),
                                 ("cgroup.type", group_type), ("cgroup.procs", processes_empty)):
                value = io.observe("root." + name, lambda name=name, parser=parser: io.read("cgroup", (name,), parser))
                observations["root"][name] = value
                if name == "cgroup.subtree_control" and value is not None and not all(value.values()):
                    blocked.append("controllers-not-enabled")
            if member is not None and member["unified_only"]:
                parts = io.observe("mapping", lambda: mapped_parts(mount["root"], member["path"]))
                if parts is not None:
                    observations["ancestry"]["mapping"] = "supported"
                    for level in range(len(parts) + 1):
                        parent = parts[:len(parts) - level]
                        row = {"level": level}
                        for name in LIMITS + ("memory.peak", "memory.events"):
                            row[name] = io.observe("ancestor.%d.%s" % (level, name), lambda name=name, parent=parent:
                                io.read("cgroup", parent + (name,), lambda raw: limit_value(name, raw)))
                        row["cgroup.kill_present"] = io.observe("ancestor.%d.cgroup.kill" % level,
                            lambda parent=parent: boundary.kill_interface(parent))
                        observations["ancestry"]["visible"].append(row)
            boundary.recheck_group()
            require(io.read("proc", ("mountinfo",), mounts, MOUNT_LIMIT) == mount_rows, "mount-correspondence")
    if mount is not None and mount["v2"]:
        before = len(io.errors)
        io.observe("mount.correspondence", group_observations)
        if len(io.errors) != before:
            observations["ancestry"]["mapping"] = "unknown"
    io.observe("completion", lambda: None)
    incomplete = bool(io.errors) or observations["ancestry"]["mapping"] != "supported"
    report = {"schema": "nib.linux-resource-capabilities/v1", "observations": observations,
        "collection_complete": not incomplete, "bytes_read": io.used, "errors": io.errors,
        "blocked_reasons": blocked, "current_helper_preconditions": "blocked" if blocked else
            ("unknown" if incomplete else "not_disproved"),
        "limitations": ["hidden-ancestors-unknown", "delegation-unverified", "syscalls-unverified"],
        "delegation_verified": False, "kernel_integration_verified": False, "operationally_approved": False}
    return report, int(any(error["reason"] in FATAL for error in io.errors))


def failure(reason):
    return {"schema": "nib.linux-resource-capabilities/v1", "collection_complete": False,
        "current_helper_preconditions": "unknown", "errors": [{"field": "collector", "reason": reason}],
        "delegation_verified": False, "kernel_integration_verified": False, "operationally_approved": False}


def render(report):
    raw = (json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")
    require(len(raw) <= OUTPUT_LIMIT, "output-limit")
    return raw


def main():
    try:
        require(len(sys.argv) == 1 and sys.flags.isolated and sys.flags.no_site
                and sys.dont_write_bytecode, "invocation")
        deadline = time.monotonic() + SECONDS
        report, status = collect(Linux())
        raw = render(report)
        require(time.monotonic() < deadline, "timeout")
    except (Exception, KeyboardInterrupt):
        raw, status = render(failure("collector-failed")), 1
    try:
        require(sys.stdout.buffer.write(raw) == len(raw), "output-write")
        sys.stdout.buffer.flush()
    except (Exception, KeyboardInterrupt):
        return 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
