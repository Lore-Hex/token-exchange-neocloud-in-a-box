#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Offline wheel inspection/overlay application, not image provenance or ABI proof."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site):
    sys.exit("Use python3 -I -S for the offline wheel inspector.")

import argparse
import ast
import base64
from contextlib import contextmanager
import csv
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import site
import stat
import sysconfig
import time
import uuid

BASE_IMAGE = "vllm/vllm-openai@sha256:6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9"
BASE_REVISION = "5df63a7203cdb9328d824a27b682e77c7b9928be"
MAX_FILES = 100000
MAX_BYTES = 32 << 30
MAX_FILE = 4 << 30
MAX_SMALL = 2 << 20
MAX_CONTEXT = 8 << 20


def require(condition):
    if not condition:
        raise ValueError("offline overlay validation failed")


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def load_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def bundle_digest(files):
    result = hashlib.sha256()
    for name, data in sorted(files.items()):
        result.update(name.encode() + b"\0" + len(data).to_bytes(8, "big") + data)
    return result.hexdigest()


def safe_name(name):
    p = PurePosixPath(name)
    require(isinstance(name, str) and name and not p.is_absolute()
            and p.as_posix() == name and all(x not in ("", ".", "..") for x in name.split("/"))
            and not any(ord(c) < 32 or ord(c) == 127 or c == "\\" for c in name))
    return p.parts


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def directory(path):
    """Open every ancestor without following links; no resolve() before checking."""
    path = Path(path)
    require(path.is_absolute())
    parts = safe_name(path.as_posix()[1:]) if path != Path("/") else ()
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


class Tree:
    def __init__(self, fd):
        self.fd = fd
        self.deadline = time.monotonic() + 300

    @contextmanager
    def parent(self, name):
        parts = safe_name(name)
        fd = os.dup(self.fd)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd, parts[-1]
        finally:
            os.close(fd)

    def read(self, name, *, retain=True):
        with self.parent(name) as (parent, leaf):
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                        and before.st_size <= (MAX_SMALL if retain else MAX_FILE))
                h, size, data, header = hashlib.sha256(), 0, bytearray(), b""
                while chunk := stream.read(256 << 10):
                    size += len(chunk)
                    require(time.monotonic() < self.deadline and size <= before.st_size)
                    h.update(chunk)
                    if not header:
                        header = chunk[:64]
                    if retain:
                        data.extend(chunk)
                require(size == before.st_size and identity(before) == identity(os.fstat(stream.fileno()))
                        == identity(os.stat(leaf, dir_fd=parent, follow_symlinks=False)))
                return bytes(data) if retain else {"sha256": h.hexdigest(), "bytes": size,
                                                   "mode": stat.S_IMODE(before.st_mode), "header": header}

    def inventory(self):
        result, total, visited = {}, 0, 0
        def walk(fd, prefix, depth):
            nonlocal total, visited
            require(depth <= 32)
            with os.scandir(fd) as entries:
                for entry in entries:
                    visited += 1
                    require(visited <= MAX_FILES and time.monotonic() < self.deadline)
                    name = prefix + entry.name
                    safe_name(name)
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                        try:
                            walk(child, name + "/", depth + 1)
                        finally:
                            os.close(child)
                    else:
                        item = self.read(name, retain=False)
                        total += item["bytes"]
                        require(total <= MAX_BYTES)
                        if re.search(r"\.so(?:\.|$)", entry.name):
                            header = item["header"]
                            require(len(header) == 64 and header[:6] == b"\x7fELF\x02\x01"
                                    and int.from_bytes(header[16:18], "little") == 3
                                    and int.from_bytes(header[18:20], "little") == 62)
                            match = re.search(r"\.cpython-([0-9]+)-", entry.name)
                            require(match is None or match[1] == "312")
                        del item["header"]
                        result[name] = item
        walk(self.fd, "", 0)
        return result

    def write(self, name, data, mode):
        with self.parent(name) as (parent, leaf):
            temporary = ".nib-overlay-" + uuid.uuid4().hex
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fchmod(stream.fileno(), mode)
                    os.fsync(stream.fileno())
                os.rename(temporary, leaf, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass

    def unlink(self, name):
        with self.parent(name) as (parent, leaf):
            os.unlink(leaf, dir_fd=parent)
            os.fsync(parent)


def headers(data):
    message = BytesParser().parsebytes(data)
    require(not message.defects)
    return message


def one(message, key):
    values = message.get_all(key, [])
    require(len(values) == 1)
    return values[0]


def bytecode_for(name, module):
    p, m = PurePosixPath(name), PurePosixPath(module)
    return (name in (module + "c", module + "o") or
            (p.parent == m.parent / "__pycache__" and p.name.startswith(m.stem + ".")
             and p.suffix == ".pyc"))


def inspect(tree, contract, *, console=None):
    """Validate bytes/metadata. RECORD is integrity metadata, not a signature."""
    require(contract["base_image"] == BASE_IMAGE and contract["base_revision"] == BASE_REVISION)
    inventory = tree.inventory()
    require("vllm.py" not in inventory and "vllm.pyc" not in inventory)
    distributions = {}
    for name in inventory:
        # Do not execute startup hooks, editable finders, or path extensions.
        require(not name.endswith((".pth", ".egg-link", ".egg-info")) and ".egg-info/" not in name
                and "__editable__" not in name)
        if name.endswith(".dist-info/METADATA") and name.count("/") == 1:
            meta = headers(tree.read(name))
            package = one(meta, "Name").lower().replace("_", "-").replace(".", "-")
            require(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", package) and package not in distributions)
            version = one(meta, "Version")
            require(re.fullmatch(r"[0-9][a-zA-Z0-9.!+_-]*", version))
            directory_name = name.split("/", 1)[0].removesuffix(".dist-info")
            dist_name, dist_version = directory_name.rsplit("-", 1)
            require(dist_name.lower().replace("_", "-").replace(".", "-") == package and dist_version == version)
            distributions[package] = (name.rsplit("/", 1)[0], version, meta)
    require("vllm" in distributions)
    dist, version, meta = distributions["vllm"]
    require(version in ("0.32.0", "0.32.0+cu130")
            and sorted(one(meta, "Requires-Python").replace(" ", "").split(",")) == ["<3.15", ">=3.11"])
    for name in inventory:
        if name.endswith(".dist-info/direct_url.json"):
            direct = load_json(tree.read(name))
            require(isinstance(direct, dict) and not direct.get("dir_info", {}).get("editable", False))
    for package, expected in contract["dependency_pins"].items():
        require(package in distributions)
        actual = distributions[package][1]
        require(actual == expected or (package in ("torch", "torchaudio", "torchvision")
                                      and actual == expected + "+cu130"))
    declared = {}
    for dependency in meta.get_all("Requires-Dist", []):
        match = re.match(r"([A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,.-]+\])?\s*(.*)", dependency)
        require(match is not None)
        package = match[1].lower().replace("_", "-").replace(".", "-")
        if package in contract["dependency_pins"]:
            require(package not in declared)
            constraint = match[2].split(";", 1)[0].replace(" ", "").strip("()")
            require(constraint == "==" + contract["dependency_pins"][package])
            declared[package] = constraint
    require(set(declared) == set(contract["dependency_pins"]) - {"nvidia-nccl-cu13"})
    wheel = headers(tree.read(dist + "/WHEEL"))
    require(one(wheel, "Wheel-Version") == "1.0" and one(wheel, "Root-Is-Purelib") == "false")
    tags = wheel.get_all("Tag", [])
    require(tags and len(tags) == len(set(tags))
            and all(re.fullmatch(r"cp38-abi3-(?:linux|manylinux1|manylinux2014|manylinux_2_[0-9]+)_x86_64", t)
                         for t in tags))
    record_name = dist + "/RECORD"
    console_info = None
    if console is not None:
        console_name, console_tree = console
        require(console_name.endswith("/vllm") and not PurePosixPath(console_name).is_absolute())
        console_info = console_tree.read("vllm", retain=False)
        del console_info["header"]
        entries = tree.read(dist + "/entry_points.txt").decode("utf-8")
        require(entries.strip() == "[console_scripts]\nvllm = vllm.entrypoints.cli.main:main")
    rows, seen = csv.reader(io.StringIO(tree.read(record_name).decode("utf-8")), strict=True), set()
    for row in rows:
        require(len(row) == 3 and len(seen) < MAX_FILES)
        name, encoded, size = row
        if console is not None and name == console[0]:
            item = console_info  # Interpreter-derived scripts root, not a RECORD-selected path.
        else:
            safe_name(name)
            require(name in inventory)
            item = inventory[name]
        require(name not in seen)
        seen.add(name)
        if name == record_name or name.endswith(".pyc"):
            require(encoded == size == "")
        else:
            require(re.fullmatch(r"sha256=[A-Za-z0-9_-]{43}", encoded)
                    and size == str(item["bytes"]))
            require(encoded == "sha256=" + base64.urlsafe_b64encode(bytes.fromhex(item["sha256"])).decode().rstrip("="))
    owned = {name for name in inventory if name.startswith(("vllm/", dist + "/"))}
    require(seen == owned | ({console[0]} if console is not None else set()) and record_name in seen)
    require(console is None or console[0] in seen)
    versions = []
    for node in ast.parse(tree.read("vllm/_version.py")).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets):
            versions.append(ast.literal_eval(node.value))
    require(versions == [version])
    for item in contract["files"]:
        name, expected = item["path"], item["sha256"][0]
        if expected is None:
            require(name not in inventory and not any(bytecode_for(p, name) for p in inventory))
        else:
            require(name in seen and inventory[name]["sha256"] == expected)
    return {"files": inventory, "console": console_info}


def apply_overlay(tree, contract, stages, before, *, console=None):
    require(len(stages) == 5)
    for index, stage in enumerate(stages, 1):
        require(set(stage) == {item["path"] for item in contract["files"]})
        for item in contract["files"]:
            require(digest(stage[item["path"]]) == item["sha256"][index])
    require(inspect(tree, contract, console=console) == before)
    expected = dict(before["files"])
    removed = {p for p in expected if any(bytecode_for(p, item["path"]) for item in contract["files"])}
    for p in sorted(removed):
        tree.unlink(p)
        del expected[p]
    for index, stage in enumerate(stages, 1):
        for item in contract["files"]:
            name = item["path"]
            data, mode = stage[name], int(item["mode"], 8) & 0o777
            require(digest(data) == item["sha256"][index])
            tree.write(name, data, mode)
            expected[name] = {"sha256": digest(data), "bytes": len(data), "mode": mode}
        require(tree.inventory() == expected)
    if console is not None:
        actual = console[1].read("vllm", retain=False)
        del actual["header"]
        require(actual == before["console"])
    return {"schema": "nib.engine-overlay/v1", "status": "BLOCKED", "deployable": False,
            "hardware_validated": False, "abi_validated": False, "image_origin_verified": False,
            "scope": "wheel-record-and-source-integrity-only", "stages": len(stages),
            "before_inventory_sha256": digest(json_bytes(before)),
            "after_inventory_sha256": digest(json_bytes({"files": expected, "console": before["console"]})),
            "removed_bytecode_files": len(removed), "git_sha": contract["git_sha"],
            "source_bundle_sha256": contract["source_bundle_sha256"], "base_image": BASE_IMAGE,
            "native_inventory_unchanged": True, "wheel_record": "original-preserved-overlay-receipt-required",
            "dependency_scope": "selected-exact-upstream-pins-only"}


def load_context(path, expected_digest):
    require(re.fullmatch(r"[0-9a-f]{64}", expected_digest))
    with directory(path) as fd:
        tree = Tree(fd)
        inventory = tree.inventory()
        require(len(inventory) <= 100 and sum(i["bytes"] for i in inventory.values()) <= MAX_CONTEXT)
        files = {name: tree.read(name) for name in inventory}
    require(bundle_digest(files) == expected_digest)
    contract = load_json(files["contract.json"])
    require(contract["status"] == "BLOCKED" and contract["deployable"] is False and contract["image"] is None)
    stages = [{item["path"]: files[f"stages/{n}/" + item["path"]] for item in contract["files"]}
              for n in range(1, 6)]
    require(set(files) == {"contract.json", "inspector.py", "LICENSE", "NOTICE"} |
            {f"stages/{n}/" + item["path"] for n in range(1, 6) for item in contract["files"]})
    require(files["inspector.py"] == Path(__file__).read_bytes())
    return contract, stages


def installed_root():
    require(platform.system() == "Linux" and platform.machine() == "x86_64"
            and sys.version_info[:2] == (3, 12))
    paths = sysconfig.get_paths()
    roots = set(site.getsitepackages()) | {paths["purelib"], paths["platlib"]}
    found = []
    for root in sorted(roots):
        if not Path(root).exists():
            continue
        with directory(root) as fd:
            with os.scandir(fd) as entries:
                for entry in entries:
                    if entry.name.lower().startswith("vllm") and entry.name.endswith((".dist-info", ".egg-info")):
                        require(entry.is_dir(follow_symlinks=False) and entry.name.endswith(".dist-info"))
                        found.append(root)
    require(len(found) == 1)
    for root in roots - {found[0]}:
        if Path(root).exists():
            with directory(root) as fd:
                with os.scandir(fd) as entries:
                    require(not any(e.name in ("vllm", "vllm.py", "vllm.pyc") for e in entries))
    return found[0], paths["scripts"]


class QuietParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid offline overlay arguments")


def main():
    parser = QuietParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--payload-sha256", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    contract, stages = load_context(args.context, args.payload_sha256)
    root, scripts = installed_root()
    with directory(root) as fd, directory(scripts) as script_fd:
        tree = Tree(fd)
        console = (os.path.relpath(Path(scripts) / "vllm", root), Tree(script_fd))
        before = inspect(tree, contract, console=console)
        report = apply_overlay(tree, contract, stages, before, console=console) if args.apply else {
            "status": "BLOCKED", "result": "inspection-only", "abi_validated": False,
            "image_origin_verified": False, "inventory_sha256": digest(json_bytes(before))}
    report["payload_sha256"] = args.payload_sha256
    print(json_bytes(report).decode(), end="")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit("Offline overlay failed; discard any partially modified image. No runtime approval.")
