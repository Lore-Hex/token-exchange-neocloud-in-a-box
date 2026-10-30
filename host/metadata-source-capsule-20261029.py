"""Closed, inert metadata source capsule; live integration is UNIMPLEMENTED.

This library neither establishes supervision nor authenticates its own loader.
The caller must do both before entry. There is deliberately no install CLI.
"""

import base64
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
from types import MappingProxyType
import zlib

PRODUCER = "31162745cb90f4ab268f1b2b8eb8c768b750442a"
# Measured from the clean, exact producer checkout; never selected by a capsule.
PINS = MappingProxyType({
    "host/cache-public-weights.py": (45953, "25141adae8f894a54f4b8f90c65ba26a9d7b3f493121b640b381c4f2a0cb57f6"),
    "host/inspect-public-weight-cache.py": (25458, "370db5bdd583122b76809510500a2a0cb5c2a89e3217aae060cea984802a2cdb"),
    "host/inspect-storage-metadata.py": (17037, "c899b5af44341bac2914ddc79c9b9dff721ac666a934e3b28f5876108585c94b"),
    "host/probe-build-resources.py": (11551, "2b740ef5b1ecbc87cb3a078fcbb081017f7db5c9600c9956f97cc52330ad9fdc"),
    "host/probe.py": (7989, "dd196679dc4b6d7ee335b3d62900b25484734884bd3da6e74f925260ad7864ff"),
})
LIMITS = MappingProxyType({"memory.max": "8589934592", "memory.swap.max": "0",
    "memory.oom.group": "1", "pids.max": "256", "cpu.max": "200000 100000"})
MAX_RECORD = MAX_CAPSULE = 65536
MAX_EXPANDED = 1 << 20
CAPSULE_SCHEMA = "private.metadata-source-capsule/v1"
PREPARED_SCHEMA = "private.metadata-source-prepared/v1"
CONTEXT_SCHEMA = "private.metadata-source-supervised-context/v1"
FALSE_FLAGS = ("remote_transfer_complete", "supervision_verified", "owned_absence_confirmed",
    "cleanup_confirmed", "runtime_ready", "admitted", "operation_authorized", "sources_executed")


class Rejected(ValueError):
    def __init__(self):
        super().__init__("metadata-source-capsule-rejected")


def require(ok):
    if not ok:
        raise Rejected()


@contextmanager
def fixed_errors():
    try:
        yield
    except Exception:
        raise Rejected() from None


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii") + b"\n"


def hex_value(value, size):
    require(type(value) is str and re.fullmatch("[0-9a-f]{%d}" % size, value))


def strict(raw, limit):
    require(type(raw) is bytes and 0 < len(raw) <= limit)

    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value)
            value[key] = item
        return value

    value = json.loads(raw, object_pairs_hook=unique)
    require(type(value) is dict and encoded(value) == raw)
    return value


def authenticate(raw, expected, limit):
    hex_value(expected, 64)
    require(type(raw) is bytes and 0 < len(raw) <= limit and sha(raw) == expected)


def rows():
    return {name: {"bytes": size, "sha256": digest} for name, (size, digest) in PINS.items()}


def prepared(raw, expected):
    authenticate(raw, expected, MAX_RECORD)
    value = strict(raw, MAX_RECORD)
    require(set(value) == {"schema", "producer", "attempt_id", "request_sha256", "files"}
            and value["schema"] == PREPARED_SCHEMA and value["producer"] == PRODUCER
            and encoded(value["files"]) == encoded(rows()))
    hex_value(value["attempt_id"], 32)
    hex_value(value["request_sha256"], 64)
    return value


def sources_checked(sources):
    require(type(sources) is dict and set(sources) == set(PINS))
    for name, (size, digest) in PINS.items():
        raw = sources[name]
        require(type(raw) is bytes and len(raw) == size and sha(raw) == digest)


def unbase64(text):
    require(type(text) is str and text.isascii())
    raw = base64.b64decode(text, validate=True)
    require(base64.b64encode(raw).decode("ascii") == text)
    return raw


def encode(sources, raw_prepared, prepared_sha256):
    """Pure in-memory encoding; inputs are inert bytes, not filesystem paths."""
    with fixed_errors():
        prepared(raw_prepared, prepared_sha256)
        sources_checked(sources)
        bundle = encoded({"schema": CAPSULE_SCHEMA, "producer": PRODUCER,
            "prepared": base64.b64encode(raw_prepared).decode("ascii"),
            "sources": {name: base64.b64encode(raw).decode("ascii") for name, raw in sources.items()}})
        require(len(bundle) <= MAX_EXPANDED)
        result = base64.b64encode(zlib.compress(bundle))
        require(len(result) <= MAX_CAPSULE)
        return result


def decode(capsule, capsule_sha256, prepared_sha256):
    """Authenticate against independently supplied pins before bounded decoding."""
    with fixed_errors():
        authenticate(capsule, capsule_sha256, MAX_CAPSULE)
        compressed = unbase64(capsule.decode("ascii"))
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, MAX_EXPANDED + 1)
        require(len(raw) <= MAX_EXPANDED and decoder.eof
                and not decoder.unused_data and not decoder.unconsumed_tail)
        value = strict(raw, MAX_EXPANDED)
        require(set(value) == {"schema", "producer", "prepared", "sources"}
                and value["schema"] == CAPSULE_SCHEMA and value["producer"] == PRODUCER
                and type(value["sources"]) is dict and set(value["sources"]) == set(PINS))
        raw_prepared = unbase64(value["prepared"])
        prepared(raw_prepared, prepared_sha256)
        sources = {name: unbase64(text) for name, text in value["sources"].items()}
        sources_checked(sources)
        return sources, raw_prepared


def remaining(deadline):
    require(type(deadline) in (int, float) and math.isfinite(deadline)
            and deadline > time.monotonic())


def context(raw, expected, deadline):
    authenticate(raw, expected, MAX_RECORD)
    value = strict(raw, MAX_RECORD)
    require(set(value) == {"schema", "producer", "attempt_id", "uid", "boot_id",
        "clock_namespace", "installer", "supervisor", "cgroup_identity", "limits",
        "consume_intent_sha256", "ownership_record_sha256", "request_sha256",
        "prepared_sha256", "capsule_sha256", "deadline_monotonic", "parent_identity"}
        and value["schema"] == CONTEXT_SCHEMA and value["producer"] == PRODUCER)
    hex_value(value["attempt_id"], 32)
    for name in ("consume_intent_sha256", "ownership_record_sha256", "request_sha256",
                 "prepared_sha256", "capsule_sha256"):
        hex_value(value[name], 64)
    require(type(value["uid"]) is int and value["uid"] == os.getuid() == os.geteuid())
    require(type(value["boot_id"]) is str
            and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value["boot_id"]))
    for name in ("clock_namespace", "cgroup_identity"):
        pair = value[name]
        require(type(pair) is list and len(pair) == 2
                and all(type(n) is int and n >= 0 for n in pair) and pair[1] > 0)
    for name in ("installer", "supervisor"):
        process = value[name]
        require(type(process) is dict and set(process) == {"pid", "start_ticks"}
                and all(type(n) is int and n > 0 for n in process.values()))
    require(value["installer"]["pid"] == os.getpid()
            and value["supervisor"]["pid"] != os.getpid()
            and encoded(value["limits"]) == encoded(dict(LIMITS)))
    parent = value["parent_identity"]
    require(type(parent) is list and len(parent) == 4
            and all(type(n) is int and n >= 0 for n in parent)
            and parent[1] > 0 and parent[2:] == [os.getuid(), 0o700])
    require(type(value["deadline_monotonic"]) is type(deadline)
            and value["deadline_monotonic"] == deadline)
    remaining(deadline)
    return value


def directory_identity(info):
    require(stat.S_ISDIR(info.st_mode))
    return (info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode))


def file_identity(info):
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600)
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def install(capsule, destination, raw_context, context_sha256, *, deadline, supervisor_check):
    """Install only under an ALREADY supervised, consumed, authenticated context.

    supervisor_check(raw_context, deadline) MUST verify the pre-existing live
    ownership/resources/clock/deadline and return exactly True, or raise. There
    is no default implementation. A supplied record/hook is not self-proving.
    No source is imported/executed. Partial artifacts and claims are never removed.
    """
    descriptors, directories, files = [], [], []
    observation = None
    with fixed_errors():
        try:
            value = context(raw_context, context_sha256, deadline)
            require(callable(supervisor_check))

            def supervised():
                remaining(deadline)
                require(supervisor_check(raw_context, deadline) is True)
                remaining(deadline)

            def guard():
                supervised()
                for fd, parent, name, identity in directories:
                    require(directory_identity(os.fstat(fd)) == identity)
                    if parent is not None:
                        require(directory_identity(os.stat(name, dir_fd=parent,
                                                           follow_symlinks=False)) == identity)
                remaining(deadline)

            supervised()
            sources, raw_prepared = decode(capsule, value["capsule_sha256"], value["prepared_sha256"])
            specification = prepared(raw_prepared, value["prepared_sha256"])
            require(specification["attempt_id"] == value["attempt_id"]
                    and specification["request_sha256"] == value["request_sha256"])
            require(type(destination) is str and destination.startswith("/")
                    and "\0" not in destination and os.path.normpath(destination) == destination
                    and ".." not in destination.split("/") and not destination.startswith("//"))
            path = Path(destination)
            require(path.name == value["attempt_id"])

            def opened_directory(parent, name):
                guard()
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                descriptors.append(fd)
                identity = directory_identity(os.fstat(fd))
                directories.append((fd, parent, name, identity))
                return fd, identity

            parent, _ = opened_directory(None, "/")
            current = Path("/")
            for part in path.parent.parts[1:]:
                parent, identity = opened_directory(parent, part)
                current /= part
                sticky = (str(current) in ("/tmp", "/private/tmp", "/var/tmp")
                          and identity[2] == 0 and identity[3] & stat.S_ISVTX)
                require(identity[2] in (0, os.getuid()) and (not identity[3] & 0o022 or sticky))
            require(list(directory_identity(os.fstat(parent))) == value["parent_identity"])

            def new_directory(parent_fd, name):
                guard()
                os.mkdir(name, 0o700, dir_fd=parent_fd)
                fd, identity = opened_directory(parent_fd, name)
                require(identity[2:] == (os.getuid(), 0o700))
                guard()
                os.fsync(parent_fd)
                guard()
                os.fsync(fd)
                guard()
                return fd

            root = new_directory(parent, path.name)

            def reopen(parent_fd, name, raw, identity):
                guard()
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
                try:
                    require(file_identity(os.fstat(fd)) == identity)
                    chunks, count = [], 0
                    while count <= len(raw):
                        guard()
                        chunk = os.read(fd, min(65536, len(raw) + 1 - count))
                        guard()
                        if not chunk:
                            break
                        chunks.append(chunk)
                        count += len(chunk)
                    reopened = b"".join(chunks)
                    require(reopened == raw and sha(reopened) == sha(raw)
                            and file_identity(os.fstat(fd)) == identity
                            == file_identity(os.stat(name, dir_fd=parent_fd, follow_symlinks=False)))
                finally:
                    os.close(fd)
                guard()

            def write(parent_fd, name, raw):
                guard()
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent_fd)
                descriptors.append(fd)
                file_identity(os.fstat(fd))
                view = memoryview(raw)
                while view:
                    guard()
                    count = os.write(fd, view)
                    require(type(count) is int and 0 < count <= len(view))
                    view = view[count:]
                guard()
                os.fsync(fd)
                guard()
                os.fsync(parent_fd)
                guard()
                identity = file_identity(os.fstat(fd))
                require(identity[5] == len(raw))
                reopen(parent_fd, name, raw, identity)
                files.append((parent_fd, name, raw, identity))
                return identity

            intent = encoded({"schema": "private.metadata-source-install-intent/v1",
                "context_sha256": context_sha256, "context": value,
                "directory_identity": list(directory_identity(os.fstat(root))),
                "uncertain_until_external_completion": True,
                **{key: False for key in FALSE_FLAGS}})
            write(root, "intent.json", intent)
            source = new_directory(root, "source")
            host = new_directory(source, "host")
            installed = {}
            for name in sorted(PINS):
                installed[name] = list(write(host, name.split("/")[1], sources[name]))

            def membership(root_names):
                guard()
                require(set(os.listdir(root)) == root_names
                        and os.listdir(source) == ["host"]
                        and set(os.listdir(host)) == {name.split("/")[1] for name in PINS})
                guard()

            membership({"intent.json", "source"})
            write(root, "prepared.json", raw_prepared)
            for parent_fd, name, raw, identity in files:
                reopen(parent_fd, name, raw, identity)
            observation = encoded({"schema": "private.metadata-source-install-observation/v1",
                "kind": "local-file-observation", "producer": PRODUCER,
                "attempt_id": value["attempt_id"], "context_sha256": context_sha256,
                "capsule_sha256": value["capsule_sha256"], "prepared_sha256": value["prepared_sha256"],
                "intent_sha256": sha(intent), "deadline_monotonic": deadline,
                "directory_identity": list(directory_identity(os.fstat(root))),
                "files": rows(), "file_identities": installed, "reopened_verified": True,
                **{key: False for key in FALSE_FLAGS}})
            write(root, "installation.json", observation)
            membership({"intent.json", "source", "prepared.json", "installation.json"})
            for parent_fd, name, raw, identity in files:
                reopen(parent_fd, name, raw, identity)
            guard()
        finally:
            close_failed = False
            for fd in reversed(descriptors):
                try:
                    os.close(fd)
                except OSError:
                    close_failed = True
            require(not close_failed)
        # Descriptor cleanup and the final supervisor check share the entry cutoff.
        supervised()
        return observation
