# SPDX-License-Identifier: Apache-2.0
"""Receive one opaque original-image input component; no transport or admission."""

from contextlib import contextmanager
import fcntl
import hashlib
import math
import os
from pathlib import Path
import re
import selectors
import stat
import time
import types

ROOT = Path(__file__).absolute().parents[1]
SELF = "scripts/original_image_input.py"
VERIFIER = "host/verify-selected-image-archive.py"
VERIFIER_SHA = "e24de243aa0ddc0f7c5bc61aab85152bf9ec1eeccd96a9e35ce4b299c0544968"
REQUEST = "nib.original-image-input-request/v1"
SCHEMA = "nib.original-image-input/v1"
CHUNK = 1 << 20
FALSE_FLAGS = ("remote_transfer_complete", "admitted", "imported", "image_loaded",
               "runtime_started", "deployable", "confidential", "cleanup_confirmed")


class Rejected(ValueError):
    """A fixed diagnostic, never a path, input body or raw operating-system error."""

    def __init__(self):
        super().__init__("original-image-input-rejected")


def require(value):
    if not value:
        raise Rejected()


def remaining(deadline):
    left = deadline - time.monotonic()
    require(left > 0)
    return left


def helper():
    path = ROOT / VERIFIER
    with path.open("rb") as stream:
        raw = stream.read(CHUNK + 1)
    require(len(raw) <= CHUNK and hashlib.sha256(raw).hexdigest() == VERIFIER_SHA)
    module = types.ModuleType("original_input_file_primitives")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module


def hex_value(value, length):
    require(type(value) is str and re.fullmatch("[0-9a-f]{%d}" % length, value))


def request(raw, expected, files):
    require(type(raw) is bytes and 0 < len(raw) <= files.MAX_REPORT)
    hex_value(expected, 64)
    require(files.sha(raw) == expected)
    value = files.strict_json(raw)
    require(type(value) is dict and set(value) == {
        "schema", "attempt_id", "producer", "image_producer", "receiver_sha256",
        "bytes", "sha256", "seconds"})
    require(value["schema"] == REQUEST and files.encoded(value) == raw)
    for key, length in (("attempt_id", 32), ("producer", 40), ("image_producer", 40),
                        ("receiver_sha256", 64), ("sha256", 64)):
        hex_value(value[key], length)
    require(value["receiver_sha256"] == files.sha(files.read(ROOT / SELF, CHUNK)))
    require(type(value["bytes"]) is int and 0 <= value["bytes"] <= files.MAX_ARCHIVE
            and type(value["seconds"]) is int and 0 < value["seconds"] <= files.SECONDS)
    require(value["bytes"] != 0 or value["sha256"] == files.sha(b""))
    return value


def directory_identity(info):
    return (info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode))


@contextmanager
def pipe_input(descriptor):
    require(type(descriptor) is int and descriptor >= 0)
    fd = os.dup(descriptor)
    blocking = None
    try:
        require(stat.S_ISFIFO(os.fstat(fd).st_mode)
                and fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY)
        blocking = os.get_blocking(fd)
        # dup shares status flags: the caller must exclusively own this pipe's
        # read side for the call. Restore its original mode without closing it.
        os.set_blocking(fd, False)
        with selectors.DefaultSelector() as selector:
            selector.register(fd, selectors.EVENT_READ)
            yield fd, selector
    finally:
        try:
            if blocking is not None:
                os.set_blocking(fd, blocking)
        finally:
            os.close(fd)


def read_chunk(fd, selector, count, deadline):
    while True:
        events = selector.select(remaining(deadline))
        remaining(deadline)
        if not events:
            continue
        require(not os.get_blocking(fd))
        try:
            raw = os.read(fd, count)
        except (BlockingIOError, InterruptedError):
            continue
        remaining(deadline)
        return raw


def copy_pipe(source, selector, output, expected, deadline):
    digest, count = hashlib.sha256(), 0
    while True:
        raw = read_chunk(source, selector, min(CHUNK, expected["bytes"] + 1 - count), deadline)
        if not raw:
            break
        require(count + len(raw) <= expected["bytes"])
        digest.update(raw)
        view = memoryview(raw)
        while view:
            remaining(deadline)
            written = os.write(output, view)
            require(type(written) is int and 0 < written <= len(view))
            view = view[written:]
            remaining(deadline)
        count += len(raw)
    require(count == expected["bytes"] and digest.hexdigest() == expected["sha256"])
    remaining(deadline)


def rehash(path, expected, identity, files, deadline):
    digest, count = hashlib.sha256(), 0
    with files.regular(path, private=True) as (stream, info):
        require(files.identity(info) == identity and info.st_size == expected["bytes"])
        while True:
            remaining(deadline)
            raw = stream.read(min(CHUNK, expected["bytes"] + 1 - count))
            remaining(deadline)
            if not raw:
                break
            count += len(raw)
            require(count <= expected["bytes"])
            digest.update(raw)
        require(count == expected["bytes"] and digest.hexdigest() == expected["sha256"])
    remaining(deadline)


def receive(input_fd, destination, raw_request, request_sha256, *, deadline):
    """Consume an exclusively owned pipe into a fresh private attempt directory.

    The caller authenticates request_sha256 out of band and supplies an absolute
    deadline on this process's monotonic clock; no phase resets that deadline.
    Disk syscalls are not preemptible here. A receipt is a local observation,
    never remote completion or permission to import. No failed attempt is removed.
    """
    entered = time.monotonic()
    parent = root = output = None
    try:
        require(type(deadline) in (int, float) and math.isfinite(deadline))
        remaining(deadline)
        files = helper()
        value = request(raw_request, request_sha256, files)
        require(deadline <= entered + value["seconds"])
        remaining(deadline)
        path = Path(destination)
        require(path.is_absolute() and str(path) == os.path.normpath(path)
                and ".." not in path.parts and path.name not in ("", ".", ".."))
        with pipe_input(input_fd) as (source, selector):
            parent = files.directory(path.parent, private=True)
            parent_identity = directory_identity(os.fstat(parent))
            remaining(deadline)
            os.mkdir(path.name, 0o700, dir_fd=parent)
            root = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            root_identity = directory_identity(os.fstat(root))
            require(root_identity[2:] == (os.getuid(), 0o700))
            os.fsync(parent)

            def check():
                remaining(deadline)
                current = files.directory(path.parent, private=True)
                try:
                    require(directory_identity(os.fstat(current)) == parent_identity
                            and directory_identity(os.stat(path.name, dir_fd=current,
                                                           follow_symlinks=False)) == root_identity
                            and directory_identity(os.fstat(root)) == root_identity)
                finally:
                    os.close(current)
                remaining(deadline)

            check()
            common = {"schema": SCHEMA, "scope": "local-opaque-component",
                "request_sha256": request_sha256, "request": value,
                "verifier_sha256": VERIFIER_SHA,
                "parent_identity": list(parent_identity), "directory_identity": list(root_identity),
                "deadline_monotonic": deadline, **{key: False for key in FALSE_FLAGS}}
            files.publish(root, "intent.json", {**common, "kind": "intent"})
            check()
            output = os.open("payload", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=root)
            info = os.fstat(output)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600)
            copy_pipe(source, selector, output, value, deadline)
            check()
            os.fsync(output)
            os.fsync(root)
            check()
            identity = files.identity(os.fstat(output))
            rehash(path / "payload", value, identity, files, deadline)
            check()
            require(files.identity(os.fstat(output)) == identity)
            receipt = {**common, "kind": "component-observation", "component_verified": True,
                "eof_observed": True, "reopened_verified": True,
                "verified_bytes": value["bytes"], "sha256": value["sha256"],
                "payload_identity": list(identity)}
            files.publish(root, "receipt.json", receipt)
            check()
            require(files.read(path / "intent.json", files.MAX_REPORT, private=True)
                    == files.encoded({**common, "kind": "intent"}))
            require(files.read(path / "receipt.json", files.MAX_REPORT, private=True)
                    == files.encoded(receipt))
            require(files.identity(os.fstat(output)) == identity
                    == files.identity(os.stat("payload", dir_fd=root, follow_symlinks=False)))
            check()
        remaining(deadline)
        return receipt
    except Exception:
        raise Rejected() from None
    finally:
        close_failed = False
        for fd in (output, root, parent):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    close_failed = True
        if close_failed:
            raise Rejected() from None
