#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Assemble a pinned, nonbootable APT simulation image without executing packages."""

import sys

if __name__ == "__main__" and not sys.flags.isolated:
    sys.exit("Use python3 -I scripts/prepare_guest_tools.py")

import argparse
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import subprocess
import tarfile
import time
import types

ROOT = Path(__file__).absolute().parent.parent
MAX_EXPANDED = 256 << 20
MAX_TREE = 512 << 20
MAX_ENTRIES = 50000
EPOCH = 1792454400
ARCHIVE_FORMAT_SOURCE = {
    "repository": "https://github.com/moby/moby",
    "git_sha": "f8215cc266744ef195a50a70d427c345da2acdbb",
    "path": "image/tarexport/load.go",
    "git_blob": "3713203cd14e1ce484b6b67416f0344698ba8e78",
    "executed": False, "host_docker_binary_pinned": False,
}
FILES = (
    "scripts/prepare_guest_tools.py", "scripts/guest_apt_simulation.py",
    "scripts/prepare_guest_packages.py", "scripts/guest_package_inputs.py",
    "scripts/e2e/snapshot.py", "host/simulate-guest-apt.py", "image/inputs/apt-tools-ubuntu2404.json",
    "image/inputs/r595-ubuntu2404.json",
)


def bootstrap():
    path = ROOT / FILES[2]
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or not 0 < st.st_size < 1 << 20:
            raise ValueError("tools-preparation-rejected")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read((1 << 20) + 1)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_nlink,
                              s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if len(raw) != st.st_size or identity(os.fstat(fd)) != identity(st):
            raise ValueError("tools-preparation-rejected")
    finally:
        os.close(fd)
    module = types.ModuleType("guest_package_preparer")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module, raw


base, BASE_SOURCE = bootstrap()
require, digest, encoded = base.require, base.digest, base.encoded


def committed(extra=()):
    require(isinstance(extra, tuple) and len(extra) <= 16
            and all(isinstance(p, str) and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]*", p)
                    and not p.startswith("/") and ".." not in p.split("/") for p in extra))
    def git(*args):
        value = base.processes.execute_process_group(
            ["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(ROOT), *args],
            env=base.GIT_ENV, timeout=30, max_output_bytes=2 << 20, stdin=subprocess.DEVNULL)
        value.check_returncode()
        return value.stdout
    revision = git("rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    require(re.fullmatch("[0-9a-f]{40}", revision))
    sources = {}
    for path in dict.fromkeys((*FILES, *extra)):
        row = git("ls-tree", revision, "--", path).decode().split()
        require(len(row) == 4 and row[:2] == ["100644", "blob"] and row[3] == path)
        require(int(git("cat-file", "-s", row[2])) <= 2 << 20)
        raw = git("cat-file", "blob", row[2])
        require(raw == base.read(ROOT / path, 2 << 20))
        sources[path] = raw
    require(sources[FILES[2]] == BASE_SOURCE
            and sources[FILES[3]] == base.INPUT_SOURCE
            and sources[FILES[4]] == base.PROCESS_SOURCE)
    return revision, sources


def separate_output(destination, inputs):
    require(destination.is_absolute() and ".." not in destination.parts)
    for path in inputs:
        require(path.is_absolute() and ".." not in path.parts
                and destination != path and destination not in path.parents
                and path not in destination.parents)


def path_name(value):
    require(isinstance(value, str) and 0 < len(value) <= 1024 and not value.startswith("/"))
    value = value.removeprefix("./").removesuffix("/")
    parts = value.split("/")
    require(len(parts) <= 32 and all(p not in ("", ".", "..")
            and all(32 < ord(c) < 127 for c in p) for p in parts))
    return "/".join(parts)


def link_target(name, target):
    require(isinstance(target, str) and 0 < len(target) <= 1024
            and all(32 < ord(c) < 127 for c in target))
    parts = [] if target.startswith("/") else name.split("/")[:-1]
    for p in target.split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            require(parts)
            parts.pop()
        else:
            parts.append(p)
    require(parts and len(parts) <= 32)
    return "/".join(parts)


def resolve(tree, path, *, ancestors_only=False):
    """Resolve virtual package paths without touching the host filesystem."""
    parts = path.split("/")
    for _ in range(40):
        changed = False
        for index in range(len(parts) - (1 if ancestors_only else 0)):
            prefix = "/".join(parts[:index + 1])
            row = tree.get(prefix)
            if row is None:
                continue
            if row["kind"] == "link":
                target = link_target(prefix, row["target"])
                parts = target.split("/") + parts[index + 1:]
                changed = True
                break
            require(row["kind"] == "dir" or index == len(parts) - 1)
        if not changed:
            return "/".join(parts)
    raise ValueError("tools-link-cycle")


def payload(raw, *, exact_data=None):
    # Only a caller's source-pinned archive/member inventory may name literal
    # non-ASCII regular data. The default path and executable policies stay strict.
    require(exact_data is None or type(exact_data) is dict)
    exact_data = {} if exact_data is None else exact_data
    allowed = exact_data.get("members", {})
    if exact_data:
        require(set(exact_data) == {"archive_sha256", "members"}
                and digest(raw) == exact_data["archive_sha256"]
                and type(allowed) is dict and 0 < len(allowed) <= 8)
        for name, row in allowed.items():
            require(type(name) is str and not name.isascii()
                    and name.isprintable() and len(name.encode("utf-8")) <= 1024
                    and not name.startswith("/") and len(name.split("/")) <= 32
                    and all(p not in ("", ".", "..") for p in name.split("/")))
            require(type(row) is dict and set(row) == {"bytes", "sha256", "mode"}
                    and type(row["bytes"]) is int and 0 < row["bytes"] <= 1 << 20
                    and re.fullmatch("[0-9a-f]{64}", row["sha256"])
                    and type(row["mode"]) is int and 0 <= row["mode"] <= 0o666
                    and not row["mode"] & 0o111)
    _, (name, packed) = base.archive_members(raw)
    expanded = base.decompress(name, packed, MAX_EXPANDED)
    result = {}
    with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
        for count, member in enumerate(archive):
            require(count < MAX_ENTRIES and not member.pax_headers and not member.issparse())
            if member.name in (".", "./") and member.isdir():
                continue
            literal = member.name.removeprefix("./")
            if literal in allowed:
                name = literal
                require(member.isfile() and member.mode == allowed[name]["mode"]
                        and member.size == allowed[name]["bytes"])
            else:
                name = path_name(member.name)
            require(name not in result and 0 <= member.uid <= 65535
                    and 0 <= member.gid <= 65535 and 0 <= member.mode <= 0o7777)
            # Hard links are copied only after their in-archive regular target is verified.
            if member.isdir():
                row = {"kind": "dir", "mode": 0o755}
            elif member.issym():
                link_target(name, member.linkname)
                row = {"kind": "link", "target": member.linkname, "mode": 0o777}
            elif member.islnk():
                row = {"kind": "hard", "target": path_name(member.linkname), "mode": member.mode & 0o777}
            else:
                require(member.isfile() and 0 <= member.size <= 64 << 20)
                with archive.extractfile(member) as stream:
                    data = stream.read((64 << 20) + 1)
                require(len(data) == member.size)
                if name in allowed:
                    require(digest(data) == allowed[name]["sha256"])
                row = {"kind": "file", "data": data, "mode": member.mode & 0o777}
            result[name] = row
    require(set(allowed) <= set(result))
    for row in result.values():
        if row["kind"] == "hard":
            target = result.get(row["target"])
            require(target is not None and target["kind"] == "file" and row["mode"] == target["mode"])
            row.clear()
            row.update(target)
    return result


def merge(tree, incoming):
    # Establish merged-/usr aliases before mapping payload destinations. No extraction
    # follows symlinks: the only filesystem output is a deterministic tar byte stream.
    for name, row in sorted(incoming.items(), key=lambda pair: (pair[0].count("/"), pair[0])):
        actual = resolve(tree, name, ancestors_only=row["kind"] != "dir")
        old = tree.get(actual)
        if old is not None:
            if old["kind"] == "dir" and row["kind"] == "dir":
                continue
            require(old == row)
        tree[actual] = row
        require(len(tree) <= MAX_ENTRIES)
    require(sum(len(r.get("data", b"")) for r in tree.values()) <= MAX_TREE)


def complete_tree(tree):
    for name, row in list(tree.items()):
        for parent in PurePosixPath(name).parents:
            if str(parent) == ".":
                continue
            key = str(parent)
            require(key not in tree or tree[key]["kind"] == "dir")
            tree.setdefault(key, {"kind": "dir", "mode": 0o755})
        if row["kind"] == "link":
            # Dangling links are permitted as inert package data, but cycles/escapes are not.
            resolve(tree, name)
    require(len(tree) <= MAX_ENTRIES)


def tar_bytes(tree):
    complete_tree(tree)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, row in sorted(tree.items()):
            member = tarfile.TarInfo(name)
            member.uid = member.gid = 0
            member.mtime = EPOCH
            member.mode = row["mode"]
            if row["kind"] == "dir":
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
            elif row["kind"] == "link":
                member.type = tarfile.SYMTYPE
                member.linkname = row["target"]
                archive.addfile(member)
            else:
                require(row["kind"] == "file")
                member.size = len(row["data"])
                archive.addfile(member, io.BytesIO(row["data"]))
    return stream.getvalue()


def observation(tree):
    return {name: ({k: v for k, v in row.items() if k != "data"}
                  | ({"bytes": len(row["data"]), "sha256": digest(row["data"])}
                     if row["kind"] == "file" else {}))
            for name, row in sorted(tree.items())}


def executable(tree, name):
    row = tree.get(resolve(tree, name.lstrip("/")))
    require(row is not None and row["kind"] == "file" and row["mode"] & 0o111)
    raw = row["data"]
    require(len(raw) >= 64 and raw[:6] == b"\x7fELF\x02\x01" and raw[18:20] == b"\x3e\x00")
    offset = struct.unpack_from("<Q", raw, 32)[0]
    width, count = struct.unpack_from("<HH", raw, 54)
    require(width == 56 and 0 < count <= 256 and offset + count * width <= len(raw))
    interpreters = []
    for index in range(count):
        start = offset + index * width
        if struct.unpack_from("<I", raw, start)[0] == 3:
            begin = struct.unpack_from("<Q", raw, start + 8)[0]
            size = struct.unpack_from("<Q", raw, start + 32)[0]
            require(1 < size <= 1024 and begin + size <= len(raw))
            value = raw[begin:begin + size]
            require(value[-1:] == b"\0" and b"\0" not in value[:-1] and value.startswith(b"/"))
            interpreter = value[:-1].decode("ascii")
            path_name(interpreter[1:])
            target = tree.get(resolve(tree, interpreter[1:]))
            require(target is not None and target["kind"] == "file" and target["mode"] & 0o111
                    and target["data"][:6] == b"\x7fELF\x02\x01"
                    and target["data"][18:20] == b"\x3e\x00")
            interpreters.append({"path": interpreter, "sha256": digest(target["data"])})
    require(len(interpreters) == 1)
    return {"sha256": digest(raw), "interpreter": interpreters[0],
            "runtime_linkage_validated": False}


def docker_archive(layers, revision, *, entrypoint, purpose, tag_prefix):
    """Serialize verified layers; this never loads or runs the resulting image."""
    require(isinstance(layers, tuple) and 0 < len(layers) <= 2
            and all(isinstance(layer, bytes) and 0 < len(layer) <= MAX_TREE for layer in layers)
            and sum(map(len, layers)) <= MAX_TREE
            and re.fullmatch("[0-9a-f]{40}", revision)
            and re.fullmatch("nib-[a-z-]+", tag_prefix)
            and re.fullmatch("[a-z-]+", purpose)
            and isinstance(entrypoint, list) and 0 < len(entrypoint) <= 8
            and all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_./-]+", value)
                    for value in entrypoint)
            and entrypoint[0].startswith("/"))
    layer_hashes = [digest(layer) for layer in layers]
    config = encoded({
        "architecture": "amd64", "os": "linux", "created": "2026-10-20T00:00:00Z",
        "config": {
            "User": "65534:65534", "WorkingDir": "/work",
            "Env": ["PATH=/usr/bin:/bin", "HOME=/nonexistent", "LANG=C", "LC_ALL=C"],
            "Entrypoint": entrypoint,
            "Labels": {"org.nib.purpose": purpose, "org.opencontainers.image.revision": revision},
        },
        "rootfs": {"type": "layers", "diff_ids": ["sha256:" + value for value in layer_hashes]},
        "history": [{"created": "2026-10-20T00:00:00Z",
                     "created_by": "nib nonexecuting authenticated package assembly"} for _ in layers],
    })
    config_hash = digest(config)
    tag = tag_prefix + ":" + config_hash[:24]
    names = ["layer.tar"] if len(layers) == 1 else ["layer0.tar", "layer1.tar"]
    manifest = encoded([{"Config": config_hash + ".json", "RepoTags": [tag], "Layers": names}])
    files = {config_hash + ".json": config, "manifest.json": manifest, **dict(zip(names, layers))}
    image = tar_bytes({name: {"kind": "file", "mode": 0o600, "data": raw}
                       for name, raw in files.items()})
    require(len(image) <= MAX_TREE)
    return image, config_hash, tag, layer_hashes


def build(metadata, archives, revision, sources):
    tools, guest = (json.loads(sources[p]) for p in FILES[-2:])
    base.inputs.check(tools, metadata, scope="apt-tools-dependency-coverage")
    base.inputs.check(guest, metadata)
    require(len(tools["packages"]) <= 128 and tools["archive_bytes"] <= 64 << 20)
    tree = {p: {"kind": "link", "target": "usr/" + p, "mode": 0o777}
            for p in ("bin", "sbin", "lib", "lib64")}
    indexes = []
    deadline = time.monotonic() + 600
    tool_map = {r["SHA256"]: r for r in tools["packages"]}
    guest_map = {r["SHA256"]: r for r in guest["packages"]}
    all_rows = {**tool_map, **guest_map}
    with base.directory(archives) as fd:
        for sha, row in sorted(all_rows.items()):
            require(time.monotonic() < deadline)
            raw = base.read_at(fd, sha + ".deb", base.MAX_ARCHIVE)
            require(len(raw) == int(row["Size"]) and digest(raw) == sha)
            index, _ = base.controls(raw, row)
            if sha in guest_map:
                indexes.append((row["Package"], index))
            if sha in tool_map:
                merge(tree, payload(raw))
    executables = {name: executable(tree, name)
                   for name in ("/usr/bin/python3.12", "/usr/bin/apt-get", "/usr/bin/dpkg")}
    targets = {r["Package"]: r["Version"] for r in guest["packages"]}
    bindings = {p: digest(raw) for p, raw in sources.items()}
    runtime = {"schema": "nib.apt-simulation-input/v1", "git_sha": revision,
               "source_hashes": bindings, "targets": targets,
               "snapshot_sha256": bindings[FILES[4]], "apt_version": "2.8.3",
               "guest_lock_sha256": bindings[FILES[-1]]}
    extras = {
        "nib/simulate.py": sources[FILES[1]],
        "nib/snapshot.py": sources[FILES[4]],
        "nib/input.json": encoded(runtime),
        "nib/repo/Packages": b"".join(raw for _, raw in sorted(indexes)),
    }
    require(not any(name == "nib" or name.startswith("nib/") for name in tree))
    for name, raw in extras.items():
        tree[name] = {"kind": "file", "mode": 0o444, "data": raw}
    layer = tar_bytes(tree)
    layer_hash = digest(layer)
    image, config_hash, tag, _ = docker_archive(
        (layer,), revision,
        entrypoint=["/usr/bin/python3.12", "-I", "-S", "-B", "/nib/simulate.py"],
        purpose="offline-apt-simulation", tag_prefix="nib-apt-simulation")
    report = {
        "schema": "nib.apt-tools-preparation/v1", "result": "pass",
        "git_sha": revision, "source_hashes": bindings, "platform": "linux/amd64",
        "tools_packages": len(tools["packages"]), "target_packages": len(targets),
        "config_sha256": config_hash, "layer_sha256": layer_hash, "image_tag": tag,
        "archive_format_source": ARCHIVE_FORMAT_SOURCE, "executables": executables,
        "archive_sha256": digest(image), "archive_bytes": len(image),
        "root_inventory_sha256": digest(encoded(observation(tree))),
        "package_scripts_executed": False, "package_binaries_executed": False,
        "apt_simulation_executed": False, "installable_verified": False,
        "complete_mkosi_tools_tree": False, "bootable": False, "deployable": False,
    }
    return {"image.tar": image, "receipt.json": encoded(report),
            "root-inventory.json": encoded(observation(tree))}, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--archives", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.output:
        separate_output(args.output, (args.metadata, args.archives))
    revision, sources = committed()
    files, report = build(args.metadata, args.archives, revision, sources)
    if args.output:
        base.publish(args.output, files)
    base.verify(args.output or args.verify, files)
    print(json.dumps({k: report[k] for k in ("result", "git_sha", "tools_packages",
                      "target_packages", "config_sha256", "layer_sha256", "archive_sha256",
                      "apt_simulation_executed", "installable_verified", "deployable")}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, KeyError, EOFError, UnicodeError,
            tarfile.TarError, subprocess.SubprocessError):
        sys.exit("Tools preparation rejected; no package installation or execution occurred.")
