#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare a private, hash-verified local package repository; never install."""

import sys

if __name__ == "__main__" and not sys.flags.isolated:
    sys.exit("Use python3 -I scripts/prepare_guest_packages.py")

import argparse
from contextlib import contextmanager
from email.parser import Parser
import gzip
import hashlib
import io
import json
import lzma
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
import types

ROOT = Path(__file__).absolute().parent.parent
SOURCE_FILES = ("scripts/prepare_guest_packages.py", "scripts/guest_package_inputs.py",
                "scripts/e2e/snapshot.py", "image/inputs/r595-ubuntu2404.json")
MAX_ARCHIVE = 128 << 20
MAX_TOTAL = 512 << 20
MAX_CONTROL = 16 << 20
IMAGE_ID = "nib-r595-installability-candidate"
TOOL_ENV = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
            "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C"}
GIT_ENV = {**TOOL_ENV, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1",
           "GIT_ALLOW_PROTOCOL": "", "GIT_TERMINAL_PROMPT": "0"}
MKOSI_SOURCE = {
    "version": "27.1", "git_sha": "ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b",
    "files": {
        "mkosi/installer/apt.py": "5e41cd4b654ca26ec5316f0b74c728d42eb22a66d80ed7b07c525c369e7e2b87",
        "mkosi/distribution/debian.py": "568bf910caf9cba6cef2999fc92efadd225fbfaca6aace49e95a09e4cff0d7d9",
        "mkosi/distribution/ubuntu.py": "d8b4b3e171b561d82a6d21f41aee5cde00c068a9005ab330a91dc02c90d8dffa",
        "mkosi/resources/man/mkosi.1.md": "6344028a599ad9dc354711a5bfd328abb1d0d0a81fe8e0defa248527da537d0c",
    },
    "executed": False, "tools_tree_pinned": False,
}


def require(condition):
    if not condition:
        raise ValueError("guest-package-preparation-rejected")


def identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)


@contextmanager
def directory(path):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def read_at(parent, name, limit):
    require(re.fullmatch(r"[A-Za-z0-9_.+-]+", name) and name not in (".", ".."))
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and 0 < before.st_size <= limit)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        require(len(raw) == before.st_size
                and identity(before) == identity(os.fstat(fd))
                and identity(before) == identity(os.stat(name, dir_fd=parent, follow_symlinks=False)))
        return raw
    finally:
        os.close(fd)


def read(path, limit):
    path = Path(path)
    with directory(path.parent) as parent:
        return read_at(parent, path.name, limit)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def package_name(row):
    # mkosi's Debian bootstrap derives Package from the basename before "_".
    return row["Package"] + "_" + row["SHA256"] + ".deb"


def helper(name, relative):
    raw = read(ROOT / relative, 1 << 20)
    module = types.ModuleType(name)
    module.__file__ = str(ROOT / relative)
    exec(compile(raw, module.__file__, "exec", dont_inherit=True), module.__dict__)
    return module, raw


inputs, INPUT_SOURCE = helper("guest_inputs", SOURCE_FILES[1])
processes, PROCESS_SOURCE = helper("guest_processes", SOURCE_FILES[2])


def committed_inputs():
    def git(*args):
        value = processes.execute_process_group(
            ["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(ROOT), *args],
            env=GIT_ENV, timeout=30, max_output_bytes=2 << 20, stdin=subprocess.DEVNULL)
        value.check_returncode()
        return value.stdout
    revision = git("rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    require(re.fullmatch(r"[0-9a-f]{40}", revision))
    sources = {}
    for name in SOURCE_FILES:
        row = git("ls-tree", revision, "--", name).decode().split()
        require(len(row) == 4 and row[:2] == ["100644", "blob"] and row[3] == name)
        require(int(git("cat-file", "-s", row[2])) <= 2 << 20)
        raw = git("cat-file", "blob", row[2])
        require(raw == read(ROOT / name, 2 << 20))
        sources[name] = raw
    require(sources[SOURCE_FILES[1]] == INPUT_SOURCE and sources[SOURCE_FILES[2]] == PROCESS_SOURCE)
    return revision, sources


def archive_members(raw):
    """The fixed .deb 2.0 ar layout, with no extraction or package execution."""
    require(raw.startswith(b"!<arch>\n"))
    offset, members = 8, []
    while offset < len(raw):
        require(len(members) < 4 and offset + 60 <= len(raw))
        header = raw[offset:offset + 60]
        require(header[-2:] == b"`\n")
        name = header[:16].decode("ascii").rstrip().removesuffix("/")
        require(re.fullmatch(r"[a-z0-9_.-]+", name))
        length = header[48:58].decode("ascii").strip()
        require(re.fullmatch(r"[0-9]{1,10}", length))
        size = int(length)
        start, end = offset + 60, offset + 60 + size
        require(end <= len(raw))
        members.append((name, raw[start:end]))
        if size % 2:
            require(raw[end:end + 1] == b"\n")
        offset = end + size % 2
    # deb(5) permits trailing members. These authenticated NVIDIA archives use
    # one _gpgbuilder member; it is not a second authentication mechanism here.
    require(len(members) == 3 or (len(members) == 4 and members[3][0] == "_gpgbuilder"
                                 and 0 < len(members[3][1]) <= 16 << 10))
    require(offset == len(raw)
            and members[0] == ("debian-binary", b"2.0\n")
            and members[1][0] in ("control.tar", "control.tar.gz", "control.tar.xz", "control.tar.zst")
            and members[2][0] in ("data.tar", "data.tar.gz", "data.tar.xz", "data.tar.zst",
                                   "data.tar.bz2", "data.tar.lzma"))
    require(len(members[1][1]) <= MAX_CONTROL)
    return members[1], members[2]


def control_member(raw):
    return archive_members(raw)[0]


def decompress(name, raw, limit=None):
    limit = MAX_CONTROL if limit is None else limit
    if name.endswith(".tar"):
        require(len(raw) <= limit)
        return raw
    if name.endswith(".gz"):
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            data = stream.read(limit + 1)
    elif name.endswith(".xz"):
        decoder = lzma.LZMADecompressor(memlimit=limit * 4)
        data = decoder.decompress(raw, max_length=limit + 1)
        require(decoder.eof and not decoder.unused_data)
    else:
        require(name.endswith(".zst"))
        # This is a trusted host decompressor, never an archive member. Only
        # authenticated control bytes reach it; both pipes/group lifetime are bounded.
        zstd = shutil.which("zstd", path=TOOL_ENV["PATH"])
        require(zstd is not None)
        with tempfile.TemporaryFile() as source:
            source.write(raw)
            source.seek(0)
            value = processes.execute_process_group(
                [zstd, "--decompress", "--stdout", "--quiet"], env=TOOL_ENV,
                stdin=source, timeout=20, max_output_bytes=limit)
        value.check_returncode()
        data = value.stdout
    require(len(data) <= limit)
    return data


def controls(raw, expected):
    name, packed = control_member(raw)
    members = {}
    with tarfile.open(fileobj=io.BytesIO(decompress(name, packed)), mode="r:") as archive:
        for count, member in enumerate(archive):
            require(count < 64 and not member.pax_headers and not member.issparse())
            if member.name in (".", "./") and member.isdir():
                continue
            name = member.name.removeprefix("./")
            require(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.+-]*", name)
                    and name not in members and member.isfile() and 0 <= member.size <= 8 << 20)
            with archive.extractfile(member) as stream:
                data = stream.read((8 << 20) + 1)
            require(len(data) == member.size)
            members[name] = data
    require("control" in members)
    control = members["control"]
    require(len(control) <= 1 << 20 and b"\r" not in control and b"\x00" not in control)
    text = control.decode("utf-8")
    message = Parser().parsestr(text.rstrip("\n") + "\n\n", headersonly=True)
    require(not message.defects and not message.get_payload())
    keys = [key.lower() for key in message.keys()]
    require(len(keys) == len(set(keys)) and not {"filename", "size", "sha256"} & set(keys))
    require(all(re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", key) for key in message.keys()))
    observed = list(inputs.paragraphs(control.rstrip(b"\n") + b"\n\n"))
    require(len(observed) == 1)
    for key in inputs.FIELDS:
        if key not in ("Filename", "Size", "SHA256"):
            require(observed[0][key] == expected[key])
    # Keep full public control metadata for apt, including Replaces and
    # Installed-Size, which the earlier dependency-coverage lock did not use.
    index = (text.rstrip("\n") + "\nFilename: pool/" + package_name(expected) + "\n"
             + "Size: " + expected["Size"] + "\nSHA256: " + expected["SHA256"] + "\n\n").encode()
    observations = {name: {"bytes": len(data), "sha256": digest(data)}
                    for name, data in sorted(members.items())}
    return index, observations


def recipe(lock, *, skeleton_tree=None):
    # mkosi ecf7f570: PackageDirectories creates a local repository, but install
    # runs maintainer scripts. This directory-only recipe is NOT invoked here.
    require(skeleton_tree in (None, "mkosi-skeleton"))
    skeleton = "" if skeleton_tree is None else "SkeletonTrees=" + skeleton_tree + "\n"
    packages = "\n         ".join(row["Package"] + "=" + row["Version"] for row in lock["packages"])
    return ("""# Candidate package-installation rehearsal only; not a bootable guest.
# Requires a separately reviewed pinned Linux/mkosi tools tree.
[Distribution]
Distribution=ubuntu
Release=noble
Architecture=x86-64

[Output]
Format=directory
ImageId=""" + IMAGE_ID + """

[Content]
Packages=""" + packages + """
PackageDirectories=repo/pool
""" + skeleton + """WithRecommends=no
Bootable=no
Ssh=never
Autologin=no
SourceDateEpoch=1792454400

[Build]
ToolsTree=tools-tree
CacheOnly=always
WithNetwork=no
Incremental=no
""").encode()


def prepare(lock, metadata, archives, revision, source_hashes, *, from_bundle=False):
    inputs.check(lock, metadata)
    require(0 < lock["archive_bytes"] <= MAX_TOTAL)
    wanted = {package_name(row) if from_bundle else row["SHA256"] + ".deb"
              for row in lock["packages"]}
    require(len(wanted) == len(lock["packages"]))
    files, rows, indexes = {}, [], []
    deadline = time.monotonic() + 600
    with directory(archives) as fd:
        with os.scandir(fd) as entries:
            names = set()
            for entry in entries:
                require(len(names) < 512)
                names.add(entry.name)
        require(names == wanted)
        total = 0
        for row in sorted(lock["packages"], key=lambda value: value["Package"]):
            require(time.monotonic() < deadline)
            name = package_name(row) if from_bundle else row["SHA256"] + ".deb"
            raw = read_at(fd, name, MAX_ARCHIVE)
            require(len(raw) == int(row["Size"]) and digest(raw) == row["SHA256"])
            total += len(raw)
            require(total <= MAX_TOTAL)
            index, observation = controls(raw, row)
            indexes.append(index)
            files["repo/pool/" + package_name(row)] = raw
            rows.append({"package": row["Package"], "version": row["Version"],
                         "architecture": row["Architecture"], "archive_sha256": row["SHA256"],
                         "control_members": observation})
        require(total == lock["archive_bytes"] and time.monotonic() < deadline)
    files["repo/Packages"] = b"".join(indexes)
    with directory(metadata) as fd:
        for index in lock["indexes"]:
            raw = read_at(fd, index["file"], 32 << 20)
            require(len(raw) == index["bytes"] and digest(raw) == index["sha256"])
            files["metadata/" + index["file"]] = raw
    files["mkosi.conf.candidate"] = recipe(lock)
    files["packages.txt"] = ("".join(row["Package"] + "=" + row["Version"] + "\n"
                                    for row in lock["packages"])).encode()
    files["package-inputs.json"] = encoded(lock)
    report = {
        "schema": "nib.guest-package-preparation/v1", "result": "pass",
        "scope": "verified-local-package-repository", "git_sha": revision,
        "source_hashes": source_hashes, "packages": rows, "archive_bytes": total,
        "recipe_source": MKOSI_SOURCE,
        "maintainer_scripts_executed": False, "apt_simulation_executed": False,
        "installable_verified": False, "image_built": False, "boot_validated": False,
        "hardware_validated": False, "deployable": False,
        "files": {name: {"bytes": len(raw), "sha256": digest(raw)}
                  for name, raw in sorted(files.items())},
    }
    files["receipt.json"] = encoded(report)
    require(sum(len(raw) for raw in files.values()) <= MAX_TOTAL)
    return files, report


def publish(destination, files):
    destination = Path(destination)
    require(destination.name not in ("", ".", ".."))
    with directory(destination.parent) as parent:
        mode = os.fstat(parent).st_mode
        require(not mode & 0o022 or mode & stat.S_ISVTX)
        os.mkdir(destination.name, mode=0o700, dir_fd=parent)
        root = os.open(destination.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            # The receipt is written last; a failed publication is not success.
            for name, raw in sorted(files.items(), key=lambda row: (row[0] == "receipt.json", row[0])):
                parts = name.split("/")
                # Pinned upstream systemd templates contain a literal @.
                require(all(re.fullmatch(r"[A-Za-z0-9_.+@-]+", p) and p not in (".", "..") for p in parts))
                current = os.dup(root)
                try:
                    for part in parts[:-1]:
                        try:
                            os.mkdir(part, mode=0o700, dir_fd=current)
                        except FileExistsError:
                            pass
                        child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                        os.fsync(current)
                        os.close(current)
                        current = child
                    fd = os.open(parts[-1], os.O_WRONLY | os.O_EXCL | os.O_CREAT | os.O_NOFOLLOW,
                                 0o600, dir_fd=current)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.fsync(current)
                finally:
                    os.close(current)
            os.fsync(root)
            os.fsync(parent)
        finally:
            os.close(root)


def verify(destination, files):
    """Compare every byte and the exact tree against freshly verified inputs."""
    expected_dirs = {""}
    for name in files:
        for depth in range(1, len(name.split("/"))):
            expected_dirs.add("/".join(name.split("/")[:depth]))
    seen_files, seen_dirs = set(), {""}
    deadline = time.monotonic() + 600

    def walk(fd, prefix):
        require(stat.S_IMODE(os.fstat(fd).st_mode) == 0o700)
        with os.scandir(fd) as entries:
            for entry in entries:
                require(time.monotonic() < deadline)
                name = prefix + entry.name
                require(name in files or name in expected_dirs)
                if name in expected_dirs:
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        seen_dirs.add(name)
                        walk(child, name + "/")
                    finally:
                        os.close(child)
                else:
                    require(stat.S_IMODE(os.stat(entry.name, dir_fd=fd, follow_symlinks=False).st_mode)
                            == 0o600)
                    require(read_at(fd, entry.name, len(files[name])) == files[name])
                    seen_files.add(name)

    with directory(destination) as root:
        walk(root, "")
    require(seen_files == set(files) and seen_dirs == expected_dirs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, help="pinned indexes; defaults to bundle metadata in verify mode")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--archives", type=Path, help="exact private archive cache; requires --output")
    mode.add_argument("--verify", type=Path, help="verify a bundle from this exact source commit, without writes")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    require((args.archives is not None) == (args.output is not None))
    require(args.metadata is not None or args.verify is not None)
    revision, sources = committed_inputs()
    lock = json.loads(sources[SOURCE_FILES[-1]])
    archives = args.archives if args.archives is not None else args.verify / "repo/pool"
    metadata = args.metadata if args.metadata is not None else args.verify / "metadata"
    files, report = prepare(lock, metadata, archives, revision,
                            {name: digest(raw) for name, raw in sources.items()},
                            from_bundle=args.verify is not None)
    if args.verify is not None:
        verify(args.verify, files)
    else:
        publish(args.output, files)
        verify(args.output, files)
    print(json.dumps({"result": report["result"], "operation": "verify" if args.verify else "prepare",
                      "packages": len(report["packages"]),
                      "archive_bytes": report["archive_bytes"], "receipt_sha256": digest(files["receipt.json"]),
                      "installable_verified": False, "image_built": False, "deployable": False},
                     sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, KeyError, EOFError, UnicodeError, tarfile.TarError,
            lzma.LZMAError, subprocess.SubprocessError):
        sys.exit("Guest package preparation rejected; no installation or image build occurred.")
