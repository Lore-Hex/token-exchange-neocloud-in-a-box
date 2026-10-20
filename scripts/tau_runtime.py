#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify offline tau source and wheel inputs; never install, import, or evaluate them."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B scripts/tau_runtime.py")

import argparse
from contextlib import contextmanager
from email.parser import BytesParser
import hashlib
import io
import itertools
import json
import os
from pathlib import Path
import re
import stat
import tarfile
import time
import tomllib
import types
import zipfile

ROOT = Path(__file__).absolute().parent.parent
SCRIPT = "scripts/tau_runtime.py"
CATALOG = "bench/tau-runtime-lock.json"
CATALOG_SHA = "6b15cca1ebb3d3766756d9f9aa9b4ec6fb892bb9c936fd73d4905abaef2b8787"
PREFLIGHT_SHA = "37a6c53c31f78f952ca8abf47ee96f166feaaf1a83cf8171c76caa72637caeec"
REVISION = "fc0055dc4e0a316c3f83133267fbd6faaa770992"
PREFIX = "tau2-bench-" + REVISION + "/"
ARCHIVE_SHA = "7a227036d07fdeb088dbd0c99fdb40ee9b9e0ebc06aadbc4dec1217b3383e57e"
SOURCE_TREE = "a11f9406b7c54d840a7006900c9e4d085059d1ec"
TARGET = {
    "implementation": "cpython", "python_full_version": "3.12.13",
    "abi": "cp312", "platform": "linux/amd64", "sys_platform": "linux",
    "platform_machine": "x86_64", "libc": "glibc", "glibc_version": "2.36",
    "image": "docker.io/library/python:3.12.13-slim-bookworm@sha256:"
             "4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2",
}
MARKERS = {
    "sys_platform == 'win32'": False,
    "python_full_version < '3.13'": True,
    "platform_machine == 'AMD64' or platform_machine == 'aarch64' or "
    "platform_machine == 'amd64' or platform_machine == 'arm64' or "
    "platform_machine == 'x86_64'": True,
}
SECONDS = 180
ARCHIVE_LIMIT = 100 << 20
SOURCE_LIMIT = 32 << 20
WHEEL_LIMIT = 64 << 20
WHEELS_LIMIT = 512 << 20


def require(value):
    if not value:
        raise ValueError("tau runtime input rejected")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def safe_name(name):
    require(type(name) is str and 0 < len(name) <= 512 and not name.startswith("/")
            and all(part not in ("", ".", "..") for part in name.split("/"))
            and not any(ord(c) < 32 or ord(c) == 127 or c == "\\" for c in name))
    return name.split("/")


@contextmanager
def directory(path):
    path = Path(path)
    require(path.is_absolute())
    parts = safe_name(path.as_posix()[1:]) if path != Path("/") else []
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def read_file(path, limit, deadline):
    path = Path(path)
    with directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 <= before.st_size <= limit)
            chunks, remaining = [], before.st_size
            while remaining:
                require(time.monotonic() < deadline)
                chunk = stream.read(min(remaining, 256 << 10))
                require(chunk)
                chunks.append(chunk)
                remaining -= len(chunk)
            require(time.monotonic() < deadline and not stream.read(1)
                    and identity(before) == identity(os.fstat(stream.fileno()))
                    and identity(before) == identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)))
            return b"".join(chunks)


def load_preflight():
    path = ROOT / "scripts/tau_preflight.py"
    raw = read_file(path, 65536, time.monotonic() + 10)
    require(digest(raw) == PREFLIGHT_SHA)
    module = types.ModuleType("tau_preflight")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", flags=0, dont_inherit=True), module.__dict__)
    return module


tau = load_preflight()


def blob(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def git_tree(files):
    """Reconstruct the complete src/tau2 Git subtree, including empty files."""
    root = {}
    for name, row in files.items():
        current = root
        parts = safe_name(name)
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        require(parts[-1] not in current and row["mode"] in ("100644", "100755"))
        current[parts[-1]] = (row["mode"], row["git_blob"])

    def tree(items):
        raw = b""
        for name in sorted(items, key=lambda name: (name + ("/" if type(items[name]) is dict else "")).encode()):
            value = items[name]
            mode, sha = ("40000", tree(value)) if type(value) is dict else value
            raw += mode.encode() + b" " + name.encode() + b"\0" + bytes.fromhex(sha)
        return hashlib.sha1(b"tree " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    return tree(root)


def source_inputs(archive, old_pins, deadline):
    require(len(archive) <= ARCHIVE_LIMIT and digest(archive) == ARCHIVE_SHA)
    requested = set(old_pins["files"]) | {"README.md"}
    files, sources, seen = {}, {}, set()
    count = expanded = retained = 0
    # Stream without extraction. Unselected upstream assets are not runtime inputs.
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r|gz") as tar:
        for item in tar:
            require(time.monotonic() < deadline)
            count += 1
            expanded += item.size
            require(count <= 2000 and expanded <= 850 << 20 and 0 <= item.size <= 512 << 20)
            require(item.name == PREFIX[:-1] or item.name.startswith(PREFIX))
            name = item.name[len(PREFIX):]
            if not name:
                require(item.isdir())
                continue
            safe_name(name)
            require(name not in seen)
            seen.add(name)
            selected = name in requested or name.startswith("src/tau2/") and not item.isdir()
            if not selected:
                continue
            require(item.isfile() and not item.issparse() and item.size <= 16 << 20)
            retained += item.size
            require(retained <= SOURCE_LIMIT)
            with tar.extractfile(item) as stream:
                raw = stream.read(item.size + 1)
            require(len(raw) == item.size and time.monotonic() < deadline)
            row = {"bytes": len(raw), "sha256": digest(raw), "git_blob": blob(raw),
                   "mode": "100755" if item.mode & 0o111 else "100644"}
            if name in old_pins["files"]:
                require({key: row[key] for key in ("bytes", "sha256", "git_blob")} == old_pins["files"][name])
            files[name], sources[name] = row, raw
    require(requested <= set(files))
    tree = git_tree({name.removeprefix("src/tau2/"): row for name, row in files.items()
                     if name.startswith("src/tau2/")})
    require(tree == SOURCE_TREE and time.monotonic() < deadline)
    return files, sources


def active(dependency):
    marker = dependency.get("marker")
    require(marker is None or marker in MARKERS)
    require(not dependency.get("extra") and not dependency.get("extras"))
    return marker is None or MARKERS[marker]


def platform_ok(tag):
    if tag == "any":
        return True
    if tag in ("manylinux1_x86_64", "manylinux2010_x86_64", "manylinux2014_x86_64"):
        return True
    match = re.fullmatch(r"manylinux_2_([0-9]+)_x86_64", tag)
    return match is not None and 5 <= int(match[1]) <= 36


def expand_tags(values):
    """Compare compressed filename and WHEEL triples with the same finite rules."""
    require(0 < len(values) <= 128)
    tags, count = set(), 0
    for value in values:
        require(type(value) is str and 0 < len(value) <= 512)
        parts = value.split("-")
        require(len(parts) == 3 and all(re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*", part)
                                       for part in parts))
        fields = [part.split(".") for part in parts]
        count += len(fields[0]) * len(fields[1]) * len(fields[2])
        require(count <= 256)
        tags.update(itertools.product(*fields))
    return tags


def wheel_tags(filename):
    require(filename.endswith(".whl") and "/" not in filename and "\\" not in filename)
    parts = filename[:-4].split("-")
    require(len(parts) in (5, 6))
    for py, ab, plat in expand_tags(["-".join(parts[-3:])]):
        if not platform_ok(plat):
            continue
        if py in ("py3", "py312") and ab == "none" and plat == "any":
            return True
        if py == "cp312" and ab in ("cp312", "abi3", "none"):
            return True
        if ab == "abi3" and py in ("cp32", "cp33", "cp34", "cp35", "cp36", "cp37",
                                  "cp38", "cp39", "cp310", "cp311"):
            return True
    return False


def dependency_selection(sources):
    project = tomllib.loads(sources["pyproject.toml"].decode())
    upstream = tomllib.loads(sources["uv.lock"].decode())
    require(project["project"]["name"] == "tau2" and project["project"]["version"] == "1.0.1"
            and project["project"]["requires-python"] == ">=3.12,<3.14"
            and project["build-system"] == {"requires": ["hatchling"], "build-backend": "hatchling.build"})
    packages = {row["name"]: row for row in upstream["package"]}
    require(len(packages) == len(upstream["package"]))
    root = packages["tau2"]
    require(root["version"] == "1.0.0" and root["source"] == {"editable": "."})
    # This known mismatch is root metadata only. Compare the complete base requirements.
    declared = {row["name"] + row.get("specifier", "") for row in root["metadata"]["requires-dist"]
                if "marker" not in row}
    require(declared == {item.lower() for item in project["project"]["dependencies"]})
    todo, selected = ["tau2"], {}
    while todo:
        name = todo.pop()
        if name in selected:
            continue
        row = packages[name]
        dependencies = sorted(dep["name"] for dep in row.get("dependencies", []) if active(dep))
        if name == "tau2":
            selected[name] = {"dependencies": dependencies}
        else:
            require(row["source"] == {"registry": "https://pypi.org/simple"})
            candidates = [item for item in row.get("wheels", [])
                          if wheel_tags(item["url"].rsplit("/", 1)[-1])]
            require(candidates)
            item = min(candidates, key=lambda item: item["url"].rsplit("/", 1)[-1])
            filename = item["url"].rsplit("/", 1)[-1]
            require(item["url"].startswith("https://files.pythonhosted.org/packages/")
                    and re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", filename)
                    and re.fullmatch("sha256:[0-9a-f]{64}", item["hash"])
                    and type(item["size"]) is int and 0 < item["size"] <= WHEEL_LIMIT)
            selected[name] = {"version": row["version"], "dependencies": dependencies,
                              "filename": filename, "url": item["url"], "sha256": item["hash"][7:],
                              "bytes": item["size"]}
        todo.extend(dependencies)
    root_dependencies = selected.pop("tau2")["dependencies"]
    require(len(selected) <= 100 and sum(row["bytes"] for row in selected.values()) <= WHEELS_LIMIT)
    return root_dependencies, dict(sorted(selected.items()))


def make_catalog(files, sources):
    dependencies, wheels = dependency_selection(sources)
    return {"schema": "nib.tau-runtime-lock/v1", "revision": REVISION, "archive_sha256": ARCHIVE_SHA,
            "source_tree": SOURCE_TREE, "upstream_lock_sha256": digest(sources["uv.lock"]),
            "target": TARGET, "source_files": files, "root": {
                "name": "tau2", "source_version": "1.0.1", "upstream_lock_version": "1.0.0",
                "mode": "verified-source-tree-not-installed", "dependencies": dependencies,
                "upstream_lock_modified": False, "editable_installation_allowed": False,
                "build_backend": "hatchling.build", "build_requirements": ["hatchling"],
                "build_backend_locked": False, "building_allowed": False,
            }, "wheels": wheels, "extras": [], "domains": list(tau.DOMAINS),
            "scope": "complete-tau-package-source-and-declared-base-wheel-closure",
            "runtime_imports_verified": False, "content_logging_suppression_verified": False}


def check_wheel(raw, expected, deadline):
    require(len(raw) == expected["bytes"] and digest(raw) == expected["sha256"]
            and wheel_tags(expected["filename"]))
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        rows = archive.infolist()
        require(0 < len(rows) <= 10000)
        seen, total, metadata, wheel = set(), 0, None, None
        for row in rows:
            require(time.monotonic() < deadline)
            name = row.filename[:-1] if row.is_dir() else row.filename
            safe_name(name)
            require(name not in seen and not row.flag_bits & 1
                    and row.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED))
            seen.add(name)
            mode = row.external_attr >> 16
            require(stat.S_IFMT(mode) in (0, stat.S_IFDIR if row.is_dir() else stat.S_IFREG))
            require(row.file_size <= 128 << 20)
            total += row.file_size
            require(total <= 256 << 20)
            # Check archive integrity without extraction or loading native/code members.
            with archive.open(row) as stream:
                size = 0
                while True:
                    require(time.monotonic() < deadline)
                    chunk = stream.read(256 << 10)
                    if not chunk:
                        break
                    size += len(chunk)
                    require(size <= row.file_size)
                require(size == row.file_size)
            if name.endswith(".dist-info/METADATA") or name.endswith(".dist-info/WHEEL"):
                require(row.file_size <= 1 << 20 and len(name.split("/")) == 2)
                data = archive.read(row)
                if name.endswith("/METADATA"):
                    require(metadata is None)
                    metadata = BytesParser().parsebytes(data)
                else:
                    require(wheel is None)
                    wheel = BytesParser().parsebytes(data)
        require(metadata is not None and wheel is not None)
        normalize = lambda value: re.sub(r"[-_.]+", "-", value).lower()
        tags = expand_tags(["-".join(expected["filename"][:-4].split("-")[-3:])])
        require(len(metadata.get_all("Name", [])) == len(metadata.get_all("Version", [])) == 1
                and normalize(metadata["Name"]) == normalize(expected["filename"].split("-")[0])
                and metadata["Version"] == expected["version"]
                and wheel.get_all("Wheel-Version") == ["1.0"]
                and expand_tags(wheel.get_all("Tag", [])) == tags)
    return total


def validate(archive, wheelhouse, catalog, old_pins, toolkit, deadline):
    files, sources = source_inputs(archive, old_pins, deadline)
    require(make_catalog(files, sources) == catalog)
    verified, expanded = 0, 0
    # Wheel mode requires every artifact. Source-only is explicit, never a fallback.
    if wheelhouse is not None:
        for row in catalog["wheels"].values():
            raw = read_file(Path(wheelhouse) / row["filename"], WHEEL_LIMIT, deadline)
            expanded += check_wheel(raw, row, deadline)
            require(expanded <= 1 << 30)
            verified += len(raw)
    require(time.monotonic() < deadline)
    return {"schema": "nib.tau-runtime-inputs/v1",
            "status": "offline-inputs-verified" if wheelhouse is not None else "source-inputs-verified",
            "toolkit": toolkit, "runtime_lock_sha256": digest(encoded(catalog)),
            "upstream_revision": REVISION, "upstream_lock_sha256": catalog["upstream_lock_sha256"],
            "target": TARGET, "source_files": len(files), "source_bytes": sum(row["bytes"] for row in files.values()),
            "selected_wheels": len(catalog["wheels"]),
            "verified_wheels": len(catalog["wheels"]) if wheelhouse is not None else 0, "wheel_bytes": verified,
            "source_tree_verified": True, "selected_wheel_bytes_verified": wheelhouse is not None,
            "dependencies_installed": False, "runtime_verified": False, "upstream_executed": False,
            "content_logging_suppression_verified": False, "benchmark_executed": False,
            "quality_accepted": False, "confidential": False,
            "root_mode": catalog["root"]["mode"],
            "blocked_on": ["reviewed-content-free-single-trial-integration",
                           "installed-runtime-and-import-closure-verification",
                           "all-role-tokenizer-and-transport-bounds", "approved-endpoints-and-resources"]}


def main():
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError("tau runtime arguments rejected")
    parser = Parser(description=__doc__)
    parser.add_argument("--source-archive", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--wheelhouse", help="Verify every selected local wheel without installing.")
    mode.add_argument("--source-only", action="store_true", help="Verify source and derive the lock; no wheel claim.")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    deadline = time.monotonic() + SECONDS
    raw_catalog = read_file(ROOT / CATALOG, 1 << 20, deadline)
    require(digest(raw_catalog) == CATALOG_SHA)
    catalog = tau.json_value(raw_catalog)
    pins = read_file(ROOT / tau.PINS, 1 << 20, deadline)
    require(digest(pins) == tau.PINS_SHA)
    toolkit = tau.committed_identity(deadline, extra_files=(SCRIPT, CATALOG, "scripts/e2e/pins.json"))
    image_pins = tau.json_value(read_file(ROOT / "scripts/e2e/pins.json", 4096, deadline))
    require(image_pins["python_image"] == TARGET["image"] and image_pins["platform"] == TARGET["platform"])
    archive = read_file(Path(args.source_archive), ARCHIVE_LIMIT, deadline)
    report = validate(archive, Path(args.wheelhouse) if args.wheelhouse else None,
                      catalog, tau.json_value(pins), toolkit, deadline)
    tau.publish(Path(args.output), report, filename="summary.json")
    print(json.dumps({"source_inputs_verified": True,
                      "selected_wheel_bytes_verified": report["selected_wheel_bytes_verified"],
                      "runtime_verified": False,
                      "receipt_sha256": digest(encoded(report))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit("Tau runtime inputs rejected; no package or benchmark was executed.")
