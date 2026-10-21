#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare private directories and an exact public source checkout on a test VM."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site):
    sys.exit("Run with python3 -I -S prepare-lab.py")

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import tarfile
import tempfile
import time
import types

BUNDLE_SHA256 = "764ef9f90f3c5989440b14dc56fe9da36db17a26466bc8418bc8dde3a97b6c50"
REVISION = "45e7a09039a312429a73b605021b8d18828af4b3"
PROBE_SHA256 = "dd196679dc4b6d7ee335b3d62900b25484734884bd3da6e74f925260ad7864ff"
MAX_BUNDLE = 16 << 20
MAX_FILES = 4096
MAX_TREE = 64 << 20
MAX_ARCHIVE = 80 << 20
CHECKOUT_SECONDS = 180
ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/nonexistent",
    "LANG": "C", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0",
    "GIT_ALLOW_PROTOCOL": "", "GIT_ATTR_NOSYSTEM": "1",
}


def require(condition):
    if not condition:
        raise ValueError("preparation-contract-rejected")


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def directory(path):
    path = Path(path)
    require(path.is_absolute() and str(path) == os.path.normpath(path) and ".." not in path.parts)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            require(not info.st_mode & 0o022 or info.st_mode & stat.S_ISVTX)
        yield fd
    finally:
        os.close(fd)


def read_file(parent, name, limit):
    require(name not in ("", ".", "..") and "/" not in name)
    fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and before.st_nlink == 1 and 0 <= before.st_size <= limit)
        data = stream.read(limit + 1)
        require(len(data) == before.st_size and identity(before) == identity(os.fstat(stream.fileno()))
                == identity(os.stat(name, dir_fd=parent, follow_symlinks=False)))
        return data


def private_directory(path, *, create=False):
    value = Path(path)
    if not value.is_absolute() or str(value) != str(value.resolve()):
        raise ValueError("canonical-absolute-path-required")
    with directory(value.parent) as parent_fd:
        parent = os.fstat(parent_fd)
        require(parent.st_uid == os.getuid() and not stat.S_IMODE(parent.st_mode) & 0o077)
        if create:
            try:
                os.mkdir(value.name, 0o700, dir_fd=parent_fd)
            except FileExistsError:
                pass
        fd = os.open(value.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            info = os.fstat(fd)
            require(info.st_uid == os.getuid() and not stat.S_IMODE(info.st_mode) & 0o077)
        finally:
            os.close(fd)
    return value


def command(argv, *, env=None, timeout=60, limit=65536):
    path = Path(__file__).absolute().with_name("probe.py")
    with directory(path.parent) as parent:
        data = read_file(parent, path.name, 65536)
    require(hashlib.sha256(data).hexdigest() == PROBE_SHA256)
    probe = types.ModuleType("pinned_preparation_capture")
    exec(compile(data, str(path), "exec", dont_inherit=True), probe.__dict__)
    return probe.capture(argv, timeout, env=ENV if env is None else env, limit=limit)


def expected_tree(raw):
    require(raw.endswith(b"\0"))
    rows = raw[:-1].split(b"\0")
    require(0 < len(rows) <= MAX_FILES)
    result, names, spelling, total = {}, set(), {}, 0
    for row in rows:
        meta, name = row.decode("ascii").split("\t")
        mode, kind, oid, size = meta.split()
        require(mode in ("100644", "100755") and kind == "blob" and re.fullmatch(r"[0-9a-f]{40}", oid)
                and size.isdecimal() and 0 <= int(size) <= MAX_BUNDLE
                and 0 < len(name) <= 512 and re.fullmatch(r"[A-Za-z0-9_./-]+", name)
                and all(p not in ("", ".", "..", ".git") for p in name.lower().split("/"))
                and name.lower() not in names)
        names.add(name.lower())
        parts = name.split("/")
        for end in range(1, len(parts) + 1):
            prefix = "/".join(parts[:end])
            require(spelling.setdefault(prefix.lower(), prefix) == prefix)
        result[name] = (mode, oid, int(size))
        total += int(size)
    require(total <= MAX_TREE)
    return result


def materialize(source, raw, expected, deadline):
    # Same regular-file/blob-identity contract as scripts/e2e/snapshot.py;
    # no checkout filters, hooks, submodules, or tar extraction helpers execute.
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for count, member in enumerate(archive):
            require(count < MAX_FILES * 2 and time.monotonic() < deadline)
            if member.isdir():
                continue
            require(member.isfile() and member.name in expected and member.name not in seen)
            mode, oid, size = expected[member.name]
            require(member.size == size)
            with archive.extractfile(member) as stream:
                data = stream.read(size + 1)
            require(len(data) == size and hashlib.sha1(b"blob " + str(size).encode() + b"\0" + data).hexdigest() == oid)
            path = source / member.name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with directory(path.parent) as parent:
                fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o700 if mode == "100755" else 0o600, dir_fd=parent)
                with os.fdopen(fd, "wb") as output:
                    require(output.write(data) == size)
                    output.flush()
                    os.fsync(output.fileno())
                require(read_file(parent, path.name, size) == data)
            seen.add(member.name)
    require(seen == set(expected))


def checkout(root, revision=None, bundle_sha256=None):
    explicit = revision is not None or bundle_sha256 is not None
    require(not explicit or (type(revision) is str and re.fullmatch(r"[0-9a-f]{40}", revision)
                            and revision != "0" * 40 and type(bundle_sha256) is str
                            and re.fullmatch(r"[0-9a-f]{64}", bundle_sha256)))
    revision, digest = (revision, bundle_sha256) if explicit else (REVISION, BUNDLE_SHA256)
    name = "source.bundle" if explicit else "source-45e7a09.bundle"
    root = private_directory(root)
    deadline = time.monotonic() + CHECKOUT_SECONDS
    with directory(root) as parent:
        fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
        original = os.fstat(parent)
        bundle = read_file(parent, name, MAX_BUNDLE)
        require(hashlib.sha256(bundle).hexdigest() == digest)
        # Only this private verified copy is passed to Git, never the transfer path.
        with tempfile.TemporaryDirectory(prefix=".checkout-", dir=root) as temporary:
            control = Path(temporary)
            snapshot = control / "source.bundle"
            with snapshot.open("xb") as output:
                os.fchmod(output.fileno(), 0o600)
                require(output.write(bundle) == len(bundle))
                output.flush()
                os.fsync(output.fileno())
            templates = control / "templates"
            templates.mkdir(mode=0o700)
            os.mkdir("source", 0o700, dir_fd=parent)
            source = root / "source"
            with directory(source) as source_fd:
                source_identity = os.fstat(source_fd)
                env = {**ENV, "TMPDIR": temporary, "GIT_TEMPLATE_DIR": str(templates)}
                git = ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "core.attributesFile=/dev/null",
                       "-c", "gc.auto=0", "-c", "maintenance.auto=false", "-C", str(source)]

                def check_directories():
                    with directory(root) as current, directory(source) as current_source:
                        for fd, before in ((current, original), (current_source, source_identity)):
                            info = os.fstat(fd)
                            require((info.st_dev, info.st_ino) == (before.st_dev, before.st_ino)
                                    and info.st_uid == os.getuid() and not stat.S_IMODE(info.st_mode) & 0o077)

                def invoke(*args, limit=65536):
                    remaining = deadline - time.monotonic()
                    require(remaining > 0)
                    check_directories()
                    result = command(git + list(args), env=env, timeout=min(60, remaining), limit=limit)
                    require(time.monotonic() < deadline)
                    check_directories()
                    return result

                invoke("init", "--quiet", "--template=" + str(templates))
                invoke("bundle", "verify", str(snapshot))
                invoke("bundle", "unbundle", str(snapshot))
                require(invoke("rev-parse", "--verify", revision + "^{commit}").strip() == revision.encode())
                invoke("fsck", "--strict", "--no-reflogs", "--no-dangling", revision)
                tree = expected_tree(invoke("ls-tree", "-rlz", "--full-tree", revision, limit=1 << 20))
                archive = invoke("archive", "--format=tar", revision, limit=MAX_ARCHIVE)
                materialize(source, archive, tree, deadline)
                invoke("read-tree", revision)
                invoke("update-ref", "--no-deref", "HEAD", revision)
                require(invoke("rev-parse", "--verify", "HEAD^{commit}").strip() == revision.encode())
                os.fsync(source_fd)
                os.fsync(parent)
    require(time.monotonic() < deadline)
    return {"source_revision": revision, "source_bundle_sha256": digest}


def prerequisites():
    names = ("python3", "git", "docker", "containerd", "nvidia-container-cli",
             "nvidia-ctk", "nvidia-smi", "apt-cache", "apt-get")
    return {name: shutil.which(name, path=ENV["PATH"]) is not None for name in names}


def ram_capacity(meminfo, mountinfo, available_bytes):
    values = {}
    for line in meminfo.splitlines():
        parts = line.split()
        if parts and parts[0] in ("MemTotal:", "MemAvailable:", "SwapTotal:"):
            require(len(parts) == 3 and parts[2] == "kB" and parts[1].isascii()
                    and parts[1].isdecimal() and parts[0] not in values)
            values[parts[0]] = int(parts[1]) * 1024
    require(set(values) == {"MemTotal:", "MemAvailable:", "SwapTotal:"})
    require(values["MemTotal:"] >= 1536 << 30 and values["MemAvailable:"] >= 1536 << 30
            and values["MemAvailable:"] <= values["MemTotal:"] and values["SwapTotal:"] == 0)
    mounts = []
    for line in mountinfo.splitlines():
        fields = line.split()
        if len(fields) >= 10 and fields[4] == "/dev/shm":
            separator = fields.index("-")
            require(separator >= 6 and len(fields) == separator + 4)
            mounts.append((fields[separator + 1], set(fields[5].split(","))))
    require(len(mounts) == 1 and mounts[0][0] == "tmpfs"
            and {"rw", "nosuid", "nodev"} <= mounts[0][1] and "ro" not in mounts[0][1])
    require(type(available_bytes) is int and 600 << 30 <= available_bytes < 1 << 63)
    return {"host_available_bytes": values["MemAvailable:"], "tmpfs_available_bytes": available_bytes,
            "swap_bytes": 0, "storage": "existing-tmpfs", "confidential_qualified": False}


def prepare_ram_stage(root):
    # A disposable public-weight cache only. No mount, swap or system setting changes.
    require(sys.platform == "linux" and os.getuid() == 0
            and re.fullmatch(r"/dev/shm/nib-[a-z0-9-]{1,64}", root))
    def kernel_text(name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "r", encoding="ascii") as stream:
            text = stream.read(65537)
        require(0 < len(text) <= 65536)
        return text
    with directory("/dev/shm") as parent:
        info = os.fstat(parent)
        require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o1777)
        space = os.fstatvfs(parent)
        result = ram_capacity(kernel_text("/proc/meminfo"), kernel_text("/proc/self/mountinfo"),
                              space.f_bavail * space.f_frsize)
        name = Path(root).name
        os.mkdir(name, 0o700, dir_fd=parent)  # Existing paths reject; never reuse or remove another cache.
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            info = os.fstat(fd)
            require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700
                    and info.st_dev == os.fstat(parent).st_dev)
        finally:
            os.close(fd)
    return result


def main():
    class QuietParser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError("preparation-arguments-rejected")
    parser = QuietParser(description=__doc__)
    parser.add_argument("operation", choices=("init", "checkout", "prerequisites", "ram-stage"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--revision", help="Exact approved 40-character commit; requires --bundle-sha256.")
    parser.add_argument("--bundle-sha256", help="Independently approved transfer digest; reads source.bundle.")
    args = parser.parse_args()
    require(args.operation == "checkout" or (args.revision is None and args.bundle_sha256 is None))
    def cancelled(*_):
        raise ValueError("preparation-cancelled")
    signal.signal(signal.SIGTERM, cancelled)
    signal.signal(signal.SIGINT, cancelled)
    os.umask(0o077)
    if args.operation == "ram-stage":
        print(json.dumps({"operation": "ram-stage", **prepare_ram_stage(args.root)}, sort_keys=True))
        return 0
    root = private_directory(args.root, create=args.operation == "init")
    result = {"operation": args.operation, "confidential_qualified": False}
    if args.operation == "checkout":
        result.update(checkout(root, args.revision, args.bundle_sha256))
    elif args.operation == "prerequisites":
        result["commands_present"] = prerequisites()
    else:
        private_directory(root / "stage", create=True)
    result["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, tarfile.TarError, subprocess.SubprocessError):
        print('{"result":"fail","reason":"lab-preparation-rejected"}')
        sys.exit(1)
