#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bounded file observations, not package provenance or runtime qualification."""
from contextlib import ExitStack, contextmanager
from email.parser import Parser
import hashlib
import json
import os
import re
import stat
import sys
import time

STATUS_PATH = ("var", "lib", "dpkg", "status")
PACKAGES = ("systemd", "systemd-container")
BINARIES = {
    "manager": (("usr", "lib", "systemd", "systemd"), ("lib", "systemd", "systemd")),
    "run": (("usr", "bin", "systemd-run"), ("bin", "systemd-run")),
    "nspawn": (("usr", "bin", "systemd-nspawn"), ("bin", "systemd-nspawn")),
}
ALIASES = {("lib",): ("usr/lib", "/usr/lib"), ("bin",): ("usr/bin", "/usr/bin")}
STATUS_LIMIT = 8 << 20
BINARY_LIMIT = 16 << 20
TOTAL_LIMIT = 64 << 20
OUTPUT_LIMIT = 16 << 10
RECORD_LIMIT = 8192
PARAGRAPH_LIMIT = 64 << 10
FIELD_LIMIT = 128
LINK_LIMIT = 32
CHUNK = 64 << 10
SECONDS = 10
REASONS = frozenset(("missing", "denied", "unavailable", "unsupported", "unsafe-path",
    "changed", "ambiguous", "malformed", "record-limit", "read-limit", "read-budget",
    "timeout", "internal", "invocation", "output-limit"))
FATAL = frozenset(("malformed", "record-limit", "read-limit", "read-budget", "timeout", "internal"))
AUTHORITY = ("package_provenance_verified", "executed_version_verified", "runtime_compatible",
             "delegation_verified", "kernel_integration_verified", "operationally_approved")


class Rejected(Exception):
    def __init__(self, code):
        self.code = code if code in REASONS else "internal"


def require(condition, code="malformed"):
    if not condition:
        raise Rejected(code)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def protected(info, kind):
    require(kind(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
            and info.st_nlink > 0, "unsafe-path")


def package_version(value):
    # Same epoch/upstream/revision validation as guest_package_inputs.version;
    # no ordering, supported-version threshold or authenticity inference.
    require(len(value) <= 128 and re.fullmatch(r"[0-9A-Za-z.+:~-]+", value) is not None)
    epoch, rest = value.split(":", 1) if ":" in value else ("0", value)
    require(epoch.isascii() and epoch.isdecimal())
    upstream, revision = rest.rsplit("-", 1) if "-" in rest else (rest, "0")
    require(upstream and upstream[0].isdigit() and revision
            and re.fullmatch(r"[0-9A-Za-z.+~]+", revision) is not None)
    return value


def package_records(raw, check):
    # Follow guest_package_inputs.paragraphs: stdlib structured header parsing,
    # no defects/payload/duplicate fields. Do not import its unrelated tooling.
    require(type(raw) is bytes and len(raw) <= STATUS_LIMIT, "read-limit")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        raise Rejected("malformed") from None
    require("\r" not in text and "\x00" not in text and (not text or text.endswith("\n")))
    selected, seen, count = {}, set(), 0
    for block in text.split("\n\n"):
        check()
        if not block.strip():
            continue
        count += 1
        require(count <= RECORD_LIMIT, "record-limit")
        require(len(block.encode("utf-8")) <= PARAGRAPH_LIMIT, "record-limit")
        row = Parser().parsestr(block + "\n\n", headersonly=True)
        keys = row.keys()
        require(not row.defects and not row.get_payload() and len(keys) <= FIELD_LIMIT)
        require(len({key.lower() for key in keys}) == len(keys)
                and all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", key) for key in keys))
        name = row.get("Package", "")
        require(re.fullmatch(r"[a-z0-9][a-z0-9+.-]{1,127}", name) is not None)
        architecture = row.get("Architecture", "")
        require(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", architecture) is not None)
        require((name, architecture) not in seen)
        seen.add((name, architecture))
        if name not in PACKAGES:
            continue
        require(name not in selected, "ambiguous")
        state = row.get("Status", "").split(" ")
        require(len(state) == 3 and state[0] in ("unknown", "install", "hold", "deinstall", "purge")
                and state[1] in ("ok", "reinstreq") and state[2] in
                ("not-installed", "config-files", "half-installed", "unpacked",
                 "half-configured", "triggers-awaited", "triggers-pending", "installed"))
        version = row.get("Version")
        require(version is not None or state[2] == "not-installed")
        if version is not None:
            package_version(version)
        selected[name] = {"status": "observed", "version": version,
                          "package_state": state[2], "status_ok": state[1] == "ok"}
    check()
    return {name: selected.get(name, {"status": "missing"}) for name in PACKAGES}


class Reader:
    def __init__(self, clock):
        self.clock, self.deadline, self.used, self.errors = clock, clock() + SECONDS, 0, []

    def check(self):
        require(self.clock() < self.deadline, "timeout")

    def call(self, function, *args, **kwargs):
        self.check()
        result = function(*args, **kwargs)
        self.check()
        return result

    def observe(self, field, function):
        try:
            self.check()
            result = function()
            self.check()  # Includes all descriptor cleanup before success.
            return result
        except Rejected as error:
            reason = error.code
        except FileNotFoundError:
            reason = "missing"
        except PermissionError:
            reason = "denied"
        except OSError:
            reason = "unavailable"
        except Exception:
            reason = "internal"
        self.errors.append({"field": field, "reason": reason})
        return {"status": "missing" if reason == "missing" else "unknown", "reason": reason}

    def chunks(self, fd, maximum):
        count = 0
        while True:
            self.check()
            room = TOTAL_LIMIT - self.used
            require(room > 0, "read-budget")
            size = min(CHUNK, maximum + 1 - count, room)
            require(size > 0, "read-limit")
            part = os.read(fd, size)
            require(type(part) is bytes, "internal")
            self.used += len(part)  # Charge even rejected, partial and sentinel bytes.
            count += len(part)
            require(len(part) <= size and self.used <= TOTAL_LIMIT, "read-budget")
            self.check()
            require(count <= maximum, "read-limit")
            if not part:
                return
            yield part

    def link(self, fd, before, choices):
        self.check()
        require(stat.S_ISLNK(before.st_mode) and before.st_uid == 0
                and before.st_nlink == 1 and 0 < before.st_size <= LINK_LIMIT, "unsafe-path")
        require(TOTAL_LIMIT - self.used >= before.st_size, "read-budget")
        # O_PATH|O_NOFOLLOW pins the link itself. Linux readlinkat's empty-name
        # form reads that immutable link, not a replaceable directory entry.
        value = os.readlink("", dir_fd=fd)
        length = len(os.fsencode(value))
        self.used += length
        require(self.used <= TOTAL_LIMIT, "read-budget")
        self.check()
        require(length == before.st_size and length <= LINK_LIMIT, "changed")
        require(identity(before) == identity(self.call(os.fstat, fd)), "changed")
        require(value in choices, "unsafe-path")
        return value.lstrip("/").split("/")


class Linux:
    """Fixed paths only. No directory enumeration or general symlink resolution."""
    def ready(self):
        require(sys.platform == "linux" and all(hasattr(os, name) for name in
                ("O_PATH", "O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK")), "unsupported")

    def retain(self, stack, io, name, flags, parent=None):
        io.check()
        try:
            fd = os.open(name, flags, dir_fd=parent)
        except FileNotFoundError:
            # Every relative open follows a successful no-follow stat. Do not
            # turn a replacement race into an absent alternative to bypass.
            if parent is not None:
                raise Rejected("changed") from None
            raise
        # Register before the post-open time check; timeout cannot leak a handle.
        stack.callback(os.close, fd)
        io.check()
        return fd

    @contextmanager
    def directory(self, parts, io):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        with ExitStack() as stack:
            root = self.retain(stack, io, "/", flags)
            root_info = io.call(os.fstat, root)
            protected(root_info, stat.S_ISDIR)
            chain = []

            def walk(path):
                fd = root
                for index, name in enumerate(path):
                    before = io.call(os.stat, name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISLNK(before.st_mode):
                        prefix = tuple(path[:index + 1])
                        require(prefix in ALIASES, "unsafe-path")
                        link = self.retain(stack, io, name, os.O_PATH | os.O_NOFOLLOW, fd)
                        require(identity(io.call(os.fstat, link)) == identity(before), "changed")
                        target = io.link(link, before, ALIASES[prefix])
                        chain.append((fd, name, before))
                        fd = walk(target)
                    else:
                        protected(before, stat.S_ISDIR)
                        child = self.retain(stack, io, name, flags, fd)
                        require(identity(io.call(os.fstat, child)) == identity(before), "changed")
                        chain.append((fd, name, before))
                        fd = child
                return fd

            result = walk(parts)
            yield result
            # Rebind all directory entries while ancestors remain open. A
            # replacement/unlink invalidates the observation, not its output path.
            require(identity(io.call(os.stat, "/", follow_symlinks=False)) == identity(root_info), "changed")
            for parent, name, before in chain:
                require(identity(io.call(os.stat, name, dir_fd=parent, follow_symlinks=False))
                        == identity(before), "changed")
        io.check()

    @contextmanager
    def file(self, path, io, limit, executable):
        require(path == STATUS_PATH or any(path in paths for paths in BINARIES.values()), "unsafe-path")
        with self.directory(path[:-1], io) as parent, ExitStack() as stack:
            before = io.call(os.stat, path[-1], dir_fd=parent, follow_symlinks=False)
            protected(before, stat.S_ISREG)
            require(before.st_nlink == 1 and (not executable or before.st_mode & 0o111), "unsafe-path")
            require((0 < before.st_size if executable else 0 <= before.st_size)
                    and before.st_size <= limit, "read-limit")
            fd = self.retain(stack, io, path[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, parent)
            require(identity(io.call(os.fstat, fd)) == identity(before), "changed")
            yield fd, before
            require(identity(io.call(os.fstat, fd)) == identity(before), "changed")
            require(identity(io.call(os.stat, path[-1], dir_fd=parent, follow_symlinks=False))
                    == identity(before), "changed")
        io.check()

    def packages(self, io):
        with self.file(STATUS_PATH, io, STATUS_LIMIT, False) as (fd, before):
            raw = b"".join(io.chunks(fd, STATUS_LIMIT))
            require(len(raw) == before.st_size, "changed")
            result = package_records(raw, io.check)
        return result

    def pid1(self, io, manager):
        with self.directory(("proc", "1"), io) as parent:
            before = io.call(os.stat, "exe", dir_fd=parent, follow_symlinks=False)
            require(stat.S_ISLNK(before.st_mode) and before.st_uid == 0, "unsafe-path")
            # The sole magic-link follow is stat-only at this fixed location.
            # No readlink text, process contents or executable bytes are read.
            target = io.call(os.stat, "exe", dir_fd=parent, follow_symlinks=True)
            protected(target, stat.S_ISREG)
            require(identity(io.call(os.stat, "exe", dir_fd=parent, follow_symlinks=True))
                    == identity(target) and identity(io.call(os.stat, "exe", dir_fd=parent,
                    follow_symlinks=False)) == identity(before), "changed")
            relation = "matches-manager" if identity(target) == identity(manager) else "different"
        return {"status": "observed", "correspondence": relation}

    def binary(self, role, io):
        paths = BINARIES[role]
        with ExitStack() as stack:
            handles = []
            for path in paths:
                try:
                    handles.append(stack.enter_context(self.file(path, io, BINARY_LIMIT, True)))
                except FileNotFoundError:
                    continue
            if not handles:
                raise FileNotFoundError
            fd, before = handles[0]
            require(all(identity(info) == identity(before) for _, info in handles), "ambiguous")
            digest, count = hashlib.sha256(), 0
            for part in io.chunks(fd, BINARY_LIMIT):
                digest.update(part)
                count += len(part)
            require(count == before.st_size, "changed")
            result = {"status": "observed", "bytes": count, "sha256": digest.hexdigest()}
            pid1 = io.observe("pid1", lambda: self.pid1(io, before)) if role == "manager" else None
        io.check()
        return result, pid1


def collect(boundary, clock=time.monotonic):
    io = Reader(clock)
    report = {"schema": "nib.runtime-file-inventory/v1", "packages": {}, "binaries": {},
              "pid1": {"status": "unknown", "reason": "unavailable"},
              **{name: False for name in AUTHORITY}}
    ready = io.observe("platform", lambda: (boundary.ready(), True)[1])
    if ready is True:
        packages = io.observe("packages", lambda: boundary.packages(io))
        report["packages"] = packages if set(packages) == set(PACKAGES) else {
            name: dict(packages) for name in PACKAGES}
        for role in BINARIES:
            result = io.observe(role, lambda role=role: boundary.binary(role, io))
            if isinstance(result, tuple):
                report["binaries"][role] = result[0]
                if role == "manager":
                    report["pid1"] = result[1]
            else:
                report["binaries"][role] = result
    else:
        report["packages"] = {name: dict(ready) for name in PACKAGES}
        report["binaries"] = {name: dict(ready) for name in BINARIES}
    io.observe("completion", lambda: None)
    report.update(collection_complete=not io.errors, bytes_read=io.used, errors=io.errors)
    return report, int(any(row["reason"] in FATAL for row in io.errors))


def render(report):
    raw = (json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")
    require(len(raw) <= OUTPUT_LIMIT, "output-limit")
    return raw


def main():
    deadline = time.monotonic() + SECONDS
    try:
        require(len(sys.argv) == 1 and sys.flags.isolated and sys.flags.no_site
                and sys.dont_write_bytecode, "invocation")
        report, status = collect(Linux())
        raw = render(report)
        require(time.monotonic() < deadline, "timeout")
    except (Exception, KeyboardInterrupt):
        raw = render({"schema": "nib.runtime-file-inventory/v1", "collection_complete": False,
                      "errors": [{"field": "collector", "reason": "unavailable"}],
                      **{name: False for name in AUTHORITY}})
        status = 1
    try:
        require(sys.stdout.buffer.write(raw) == len(raw), "unavailable")
        sys.stdout.buffer.flush()
        require(time.monotonic() < deadline, "timeout")
    except (Exception, KeyboardInterrupt):
        return 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
