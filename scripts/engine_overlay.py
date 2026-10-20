#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Offline wheel inspection/overlay application, not image provenance or ABI proof."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site):
    sys.exit("Use python3 -I -S for the offline wheel inspector.")

import argparse
import ast
import base64
import configparser
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
# Diagnostic-only allowance, not generic inspection/application admission.
# Census: evidence/p0/2026-10-19-official-vllm-metadata-census/output.log.
# 204969 entries + 20031 headroom (9.77%); all non-entry limits stay unchanged.
DIAGNOSTIC_INVENTORY_POLICY = {
    "base_image": "vllm/vllm-openai@sha256:6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9",
    "config_sha256": "bd8a5d7a8774800c92d714c0c226b32c1863f929150f36efd610aebcd12a335d",
    "census_output_sha256": "006235bf2a5ce571a307efc942b33978f4ef79ab4be91d88db1824470e8f0fd0",
    "entry_limit": 225000,
}
# Public wheel pins and upstream AOT documentation: docs/tasks/p0-t4-cutlass-stubs.md.
# This is a diagnostic classification, never host-native or runtime admission.
CUTLASS_STUB_POLICY = {
    "base_image": "vllm/vllm-openai@sha256:6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9",
    "config_sha256": "bd8a5d7a8774800c92d714c0c226b32c1863f929150f36efd610aebcd12a335d",
    "sha256": "0e0fc2fbde506983308e2d02a9c7673f43e07b7638224cebd00ebcba1b1e5645",
    "bytes": 71320,
    "paths": [
        "nvidia_cutlass_dsl/cu12/lib/stubs/aarch64-unknown-linux-gnu/libcute_dsl_runtime.so",
        "nvidia_cutlass_dsl/cu12/lib/stubs/aarch64-unknown-nto-qnx8.0.0/libcute_dsl_runtime.so",
        "nvidia_cutlass_dsl/cu13/lib/stubs/aarch64-unknown-linux-gnu/libcute_dsl_runtime.so",
        "nvidia_cutlass_dsl/cu13/lib/stubs/aarch64-unknown-nto-qnx8.0.0/libcute_dsl_runtime.so",
    ],
}
# Exact public packaging metadata, not permission to execute plugins or bytecode.
# pyproject.toml at BASE_REVISION: docs/tasks/p0-t4-engine-packaging-contract.md.
PACKAGING_POLICY = {
    "schema": "nib.engine-packaging-policy/v1",
    "base_image": BASE_IMAGE,
    "config_sha256": "bd8a5d7a8774800c92d714c0c226b32c1863f929150f36efd610aebcd12a335d",
    "pyproject_sha256": "eaa19cba70f13234be4acdc1eeb2126b937c6f0892fd1af29863db5a62bee95b",
    "plugins_source_sha256": "06942d64e526c2c7407265e1903d625c4d2118d8d7fc39c616b1cc6aa6fc2c8f",
    "diagnosis_output_sha256": "abf7f9fbb4dce9ace3da05fa2873f4e27041baf13a18405ddce8c71e7febfc76",
    "entrypoints": {
        "console_scripts": {"vllm": "vllm.entrypoints.cli.main:main"},
        "vllm.general_plugins": {
            "lora_filesystem_resolver": "vllm.plugins.lora_resolvers.filesystem_resolver:register_filesystem_resolver",
            "lora_hf_hub_resolver": "vllm.plugins.lora_resolvers.hf_hub_resolver:register_hf_hub_resolver",
        },
    },
    "required_environment": {"VLLM_PLUGINS": ""},
    "unlisted_bytecode": "unique-recorded-source-remove-before-patching",
}
INVENTORY_REASONS = frozenset({"depth-limit", "entry-limit", "time-limit", "unsafe-name",
    "symlink", "non-regular-file", "hardlink", "file-size-limit", "total-size-limit",
    "native-header", "python-abi", "entry-read-rejected", "nonhost-asset-mismatch", "nonhost-asset-missing"})


class InventoryRejected(ValueError):
    """Bounded public-image diagnostic context; never include exception text."""

    def __init__(self, reason, path=None):
        super().__init__("offline inventory rejected")
        self.reason = reason
        self.path = path


def inventory_require(condition, reason, path=None):
    if not condition:
        raise InventoryRejected(reason, path)


def classify_cutlass_stub(name, item):
    """Check fully hashed file metadata; not an independent image-origin check."""
    header = item["header"]
    inventory_require(name in CUTLASS_STUB_POLICY["paths"]
        and item["sha256"] == CUTLASS_STUB_POLICY["sha256"] and item["bytes"] == CUTLASS_STUB_POLICY["bytes"]
        and len(header) == 64 and header[:7] == b"\x7fELF\x02\x01\x01"
        and int.from_bytes(header[16:18], "little") == 3
        and int.from_bytes(header[18:20], "little") == 183
        and int.from_bytes(header[20:24], "little") == 1, "nonhost-asset-mismatch", name)
    return {"path": name, "sha256": item["sha256"], "bytes": item["bytes"],
            "classification": "published-cutlass-aot-crosscompile-stub"}


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

    def check_startup_hooks(self):
        # Check names only: hooks must not execute during isolated inspection or
        # survive for a later ordinary Python startup. Reject packages too.
        visited = 0
        def scan(fd, cache=False):
            nonlocal visited
            with os.scandir(fd) as entries:
                for entry in entries:
                    visited += 1
                    require(visited <= MAX_FILES and time.monotonic() < self.deadline)
                    name = entry.name
                    safe_name(name)
                    require(name.split(".", 1)[0] not in ("sitecustomize", "usercustomize"))
                    if not cache:
                        require(not name.endswith((".pth", ".egg-link", ".egg-info"))
                                and "__editable__" not in name)
                        if name == "__pycache__":
                            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                            try:
                                scan(child, cache=True)
                            finally:
                                os.close(child)
        scan(self.fd)

    def check_module_collisions(self, modules):
        # A matching RECORD does not prevent a package/native sibling from
        # taking precedence over the patched .py file in Python's resolver.
        visited = 0
        for name in modules:
            module = PurePosixPath(name)
            require(module.suffix == ".py" and module.name != "__init__.py")
            with self.parent(name) as (parent, leaf):
                with os.scandir(parent) as entries:
                    for entry in entries:
                        visited += 1
                        require(visited <= MAX_FILES and time.monotonic() < self.deadline)
                        if entry.name == module.stem or entry.name.startswith(module.stem + "."):
                            # Existing source bytecode is deleted before patching;
                            # the newly introduced helper must have none at all.
                            require(entry.name in (leaf, leaf + "c", leaf + "o")
                                    and entry.is_file(follow_symlinks=False))

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

    def read(self, name, *, retain=True, max_bytes=None):
        limit = MAX_SMALL if retain else MAX_FILE
        if max_bytes is not None:
            require(type(max_bytes) is int and 0 <= max_bytes <= limit)
            limit = max_bytes
        with self.parent(name) as (parent, leaf):
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                        and before.st_size <= limit)
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

    def inventory(self, *, diagnostic_policy=None, nonhost_policy=None, classified=None):
        entry_limit = MAX_FILES
        if diagnostic_policy is not None:
            require(diagnostic_policy == DIAGNOSTIC_INVENTORY_POLICY)
            entry_limit = DIAGNOSTIC_INVENTORY_POLICY["entry_limit"]
        if nonhost_policy is not None:
            require(nonhost_policy == CUTLASS_STUB_POLICY and diagnostic_policy == DIAGNOSTIC_INVENTORY_POLICY
                    and type(classified) is list and not classified)
        else:
            require(classified is None)
        result, total, visited = {}, 0, 0
        def walk(fd, prefix, depth):
            nonlocal total, visited
            inventory_require(depth <= 32, "depth-limit")
            with os.scandir(fd) as entries:
                for entry in entries:
                    visited += 1
                    inventory_require(visited <= entry_limit, "entry-limit")
                    inventory_require(time.monotonic() < self.deadline, "time-limit")
                    name = prefix + entry.name
                    try:
                        safe_name(name)
                    except ValueError:
                        raise InventoryRejected("unsafe-name") from None
                    diagnostic_name = name if len(name) <= 512 else None
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                        try:
                            walk(child, name + "/", depth + 1)
                        finally:
                            os.close(child)
                    else:
                        inventory_require(not stat.S_ISLNK(info.st_mode), "symlink", diagnostic_name)
                        inventory_require(stat.S_ISREG(info.st_mode), "non-regular-file", diagnostic_name)
                        inventory_require(info.st_nlink == 1, "hardlink", diagnostic_name)
                        inventory_require(info.st_size <= MAX_FILE, "file-size-limit", diagnostic_name)
                        try:
                            item = self.read(name, retain=False)
                        except (OSError, ValueError):
                            raise InventoryRejected("entry-read-rejected", diagnostic_name) from None
                        total += item["bytes"]
                        inventory_require(total <= MAX_BYTES, "total-size-limit", diagnostic_name)
                        if nonhost_policy is not None and name in CUTLASS_STUB_POLICY["paths"]:
                            inventory_require(not any(row["path"] == name for row in classified), "nonhost-asset-mismatch", name)
                            classified.append(classify_cutlass_stub(name, item))
                            classified.sort(key=lambda row: row["path"])
                        elif re.search(r"\.so(?:\.|$)", entry.name):
                            header = item["header"]
                            inventory_require(len(header) == 64 and header[:6] == b"\x7fELF\x02\x01"
                                    and int.from_bytes(header[16:18], "little") == 3
                                    and int.from_bytes(header[18:20], "little") == 62,
                                    "native-header", diagnostic_name)
                            match = re.search(r"\.cpython-([0-9]+)-", entry.name)
                            inventory_require(match is None or match[1] == "312", "python-abi", diagnostic_name)
                        del item["header"]
                        result[name] = item
        walk(self.fd, "", 0)
        if nonhost_policy is not None:
            found = {row["path"] for row in classified}
            for name in CUTLASS_STUB_POLICY["paths"]:
                inventory_require(name in found, "nonhost-asset-missing", name)
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


def record_owned(inventory, dist):
    return {name for name in inventory if name.startswith(("vllm/", dist + "/"))}


def entrypoints_match(data):
    return data.decode("utf-8").strip() == "[console_scripts]\nvllm = vllm.entrypoints.cli.main:main"


def require_packaging_contract(contract):
    require(contract.get("experimental_packaging") == PACKAGING_POLICY
            and contract["base_image"] == PACKAGING_POLICY["base_image"]
            and contract["base_revision"] == BASE_REVISION
            and contract["official_config"]["sha256"] == PACKAGING_POLICY["config_sha256"]
            and contract["upstream_files"]["pyproject.toml"] == PACKAGING_POLICY["pyproject_sha256"]
            and contract["upstream_files"]["vllm/plugins/__init__.py"] == PACKAGING_POLICY["plugins_source_sha256"]
            and contract["status"] == "BLOCKED" and contract["deployable"] is False and contract["image"] is None)


def packaging_entrypoints_match(data):
    """Parse exact names/targets as data; never discover, import or load plugins."""
    try:
        require(len(data) <= MAX_SMALL and all(byte in (9, 10, 13) or 32 <= byte < 127 for byte in data))
        # The impossible default-section name makes even an empty [DEFAULT]
        # an ordinary unknown section, instead of silently inheriting values.
        parser = configparser.ConfigParser(interpolation=None, strict=True, default_section="\0",
                                           delimiters=("=",), empty_lines_in_values=False)
        parser.optionxform = str
        parser.read_string(data.decode("ascii"))
        require(not parser.defaults())
        return {group: dict(parser[group]) for group in parser.sections()} == PACKAGING_POLICY["entrypoints"]
    except (ValueError, UnicodeError, configparser.Error):
        raise ValueError("offline overlay validation failed") from None


def packaging_removal_plan(inventory, owned, seen):
    """Seal unlisted bytecode to unique recorded source names, never its semantics."""
    plan = []
    for name in sorted(owned - seen):
        require(len(plan) < MAX_FILES and name.startswith("vllm/"))
        path = PurePosixPath(name)
        require(path.parent.name == "__pycache__")
        match = re.fullmatch(r"(.+)\.cpython-312(?:\.opt-[12])?\.pyc", path.name)
        require(match is not None)
        source = str(path.parent.parent / (match[1] + ".py"))
        # Reject ambiguous dotted module names or a competing legacy source.
        candidates = {name[:-1]} | {str(path.parent.parent / (path.name[:index] + ".py"))
                                   for index, char in enumerate(path.name) if char == "."}
        sources = {p for p in candidates if p in inventory and p.endswith(".py") and bytecode_for(name, p)}
        require(sources == {source} and source in seen and source in owned)
        plan.append({"path": name, "sha256": inventory[name]["sha256"], "bytes": inventory[name]["bytes"],
                     "source": source, "source_sha256": inventory[source]["sha256"]})
    require(len(json_bytes(plan)) <= MAX_CONTEXT)
    return plan


def record_digest_checks(encoded, size, item):
    return {"record-hash-format": bool(re.fullmatch(r"sha256=[A-Za-z0-9_-]{43}", encoded)),
            "record-size": size == str(item["bytes"]),
            "record-hash": encoded == "sha256=" + base64.urlsafe_b64encode(bytes.fromhex(item["sha256"])).decode().rstrip("=")}


def record_field_checks(name, encoded, size, item, record_name):
    """Existing strict policy, shared with diagnostic observations, not PyPA conformance."""
    if name == record_name or name.endswith(".pyc"):
        return {"record-blank-fields": encoded == size == ""}
    return record_digest_checks(encoded, size, item)


def distribution_metadata(data, directory_name):
    meta = headers(data)
    package = one(meta, "Name").lower().replace("_", "-").replace(".", "-")
    require(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", package))
    version = one(meta, "Version")
    require(re.fullmatch(r"[0-9][a-zA-Z0-9.!+_-]*", version))
    dist_name, dist_version = directory_name.removesuffix(".dist-info").rsplit("-", 1)
    require(dist_name.lower().replace("_", "-").replace(".", "-") == package and dist_version == version)
    return package, version, meta


def dependency_matches(package, actual, expected):
    return actual == expected or (package in ("torch", "torchaudio", "torchvision") and actual == expected + "+cu130")


def requirement_pin(dependency):
    match = re.match(r"([A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,.-]+\])?\s*(.*)", dependency)
    require(match is not None)
    return match[1].lower().replace("_", "-").replace(".", "-"), match[2].split(";", 1)[0].replace(" ", "").strip("()")


def engine_metadata_checks(version, meta):
    python = meta.get_all("Requires-Python", [])
    return {"engine-version": version in ("0.32.0", "0.32.0+cu130"),
            "requires-python": len(python) == 1 and sorted(python[0].replace(" ", "").split(",")) == ["<3.15", ">=3.11"]}


def wheel_metadata_checks(wheel):
    tags = wheel.get_all("Tag", [])
    return {"wheel-version": wheel.get_all("Wheel-Version", []) == ["1.0"],
            "wheel-purelib": wheel.get_all("Root-Is-Purelib", []) == ["false"],
            "wheel-tags": bool(tags) and len(tags) == len(set(tags))
                and all(re.fullmatch(r"cp38-abi3-(?:linux|manylinux1|manylinux2014|manylinux_2_[0-9]+)_x86_64", t) for t in tags)}


def generated_versions(data):
    versions = []
    for node in ast.parse(data).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets):
            versions.append(ast.literal_eval(node.value))
    return versions


def candidate_file_matches(name, expected, inventory, seen):
    if expected is None:
        return name not in inventory and not any(bytecode_for(p, name) for p in inventory)
    return name in seen and name in inventory and inventory[name]["sha256"] == expected


def engine_layout_matches(inventory):
    return "vllm.py" not in inventory and "vllm.pyc" not in inventory


def inspect_record(tree, inventory, dist, *, console=None, packaging=None):
    """RECORD consistency only; neither import admission nor dependency validation."""
    require("/" not in dist and dist.endswith(".dist-info"))
    record_name = dist + "/RECORD"
    if packaging is not None:
        require_packaging_contract(packaging)
        require(console is not None)
    console_info = None
    if console is not None:
        console_name, console_tree = console
        require(console_name.endswith("/vllm") and not PurePosixPath(console_name).is_absolute())
        console_info = console_tree.read("vllm", retain=False)
        del console_info["header"]
        entries = tree.read(dist + "/entry_points.txt")
        require(packaging_entrypoints_match(entries) if packaging is not None else entrypoints_match(entries))
    rows, seen = csv.reader(io.StringIO(tree.read(record_name).decode("utf-8")), strict=True), set()
    for row in rows:
        require(len(row) == 3 and len(seen) < MAX_FILES)
        name, encoded, size = row
        if console is not None and name == console[0]:
            item = console_info
        else:
            safe_name(name)
            require(name in inventory)
            item = inventory[name]
        require(name not in seen)
        seen.add(name)
        require(all(record_field_checks(name, encoded, size, item, record_name).values()))
    owned = record_owned(inventory, dist)
    extra = {console[0]} if console is not None else set()
    if packaging is not None:
        require(seen <= owned | extra)
        packaging_removal_plan(inventory, owned, seen)
    else:
        require(seen == owned | extra)
    require(record_name in seen)
    require(console is None or console[0] in seen)
    return seen, console_info


def inspect(tree, contract, *, console=None, packaging=False):
    """Admission gate; diagnostics must never substitute a partial check."""
    require(contract["base_image"] == BASE_IMAGE and contract["base_revision"] == BASE_REVISION)
    tree.check_startup_hooks()
    inventory = tree.inventory()
    for name in inventory:
        require(not name.endswith((".pth", ".egg-link", ".egg-info")) and ".egg-info/" not in name
                and "__editable__" not in name)
    return inspect_integrity(tree, contract, console=console, inventory=inventory, packaging=packaging)


def inspect_integrity(tree, contract, *, console=None, inventory=None, packaging=False):
    """Partial byte/metadata check only, NOT startup/root admission or ABI proof."""
    require(contract["base_image"] == BASE_IMAGE and contract["base_revision"] == BASE_REVISION)
    require(type(packaging) is bool)
    if packaging:
        require_packaging_contract(contract)
    tree.check_module_collisions(item["path"] for item in contract["files"])
    if inventory is None:
        inventory = tree.inventory()
    require(engine_layout_matches(inventory))
    distributions = {}
    for name in inventory:
        if name.endswith(".dist-info/METADATA") and name.count("/") == 1:
            package, version, meta = distribution_metadata(tree.read(name), name.split("/", 1)[0])
            require(package not in distributions)
            distributions[package] = (name.rsplit("/", 1)[0], version, meta)
    require("vllm" in distributions)
    dist, version, meta = distributions["vllm"]
    require(all(engine_metadata_checks(version, meta).values()))
    for name in inventory:
        if name.endswith(".dist-info/direct_url.json"):
            direct = load_json(tree.read(name))
            require(isinstance(direct, dict) and not direct.get("dir_info", {}).get("editable", False))
    for package, expected in contract["dependency_pins"].items():
        require(package in distributions)
        actual = distributions[package][1]
        require(dependency_matches(package, actual, expected))
    declared = {}
    for dependency in meta.get_all("Requires-Dist", []):
        package, constraint = requirement_pin(dependency)
        if package in contract["dependency_pins"]:
            require(package not in declared)
            require(constraint == "==" + contract["dependency_pins"][package])
            declared[package] = constraint
    require(set(declared) == set(contract["dependency_pins"]) - {"nvidia-nccl-cu13"})
    wheel = headers(tree.read(dist + "/WHEEL"))
    require(all(wheel_metadata_checks(wheel).values()))
    seen, console_info = inspect_record(tree, inventory, dist, console=console, packaging=contract if packaging else None)
    require(generated_versions(tree.read("vllm/_version.py")) == [version])
    for item in contract["files"]:
        require(candidate_file_matches(item["path"], item["sha256"][0], inventory, seen))
    result = {"files": inventory, "console": console_info}
    if packaging:
        result["packaging_removals"] = packaging_removal_plan(inventory, record_owned(inventory, dist), seen)
    return result


def apply_overlay(tree, contract, stages, before, *, console=None, packaging=False):
    require(inspect(tree, contract, console=console, packaging=packaging) == before)
    return apply_verified_stages(tree, contract, stages, before, console=console, packaging=packaging)


def apply_verified_stages(tree, contract, stages, before, *, console=None, packaging=False):
    """Shared writer, called only after the caller's complete admission/recheck.

    The generic CLI always uses apply_overlay. The separate exact-image
    experiment supplies its own measured preflight; this is not an admission API.
    """
    require(len(stages) == 5)
    for index, stage in enumerate(stages, 1):
        require(set(stage) == {item["path"] for item in contract["files"]})
        for item in contract["files"]:
            require(digest(stage[item["path"]]) == item["sha256"][index])
    expected = dict(before["files"])
    removed = {p for p in expected if any(bytecode_for(p, item["path"]) for item in contract["files"])}
    planned = before["packaging_removals"] if packaging else []
    removed |= {item["path"] for item in planned}
    for p in sorted(removed):
        if packaging:
            actual = tree.read(p, retain=False)
            del actual["header"]
            require(actual == expected[p])
        tree.unlink(p)
        del expected[p]
    # A suppressed deletion or any source/native change fails before patch writes.
    if packaging:
        require(tree.inventory() == expected)
    for index, stage in enumerate(stages, 1):
        for item in contract["files"]:
            name = item["path"]
            data, mode = stage[name], int(item["mode"], 8) & 0o777
            require(digest(data) == item["sha256"][index])
            tree.write(name, data, mode)
            expected[name] = {"sha256": digest(data), "bytes": len(data), "mode": mode}
        tree.check_module_collisions(item["path"] for item in contract["files"])
        require(tree.inventory() == expected)
    if console is not None:
        actual = console[1].read("vllm", retain=False)
        del actual["header"]
        require(actual == before["console"])
    report = {"schema": "nib.engine-overlay/v1", "status": "BLOCKED", "deployable": False,
            "hardware_validated": False, "abi_validated": False, "image_origin_verified": False,
            "scope": "wheel-record-and-source-integrity-only", "stages": len(stages),
            "before_inventory_sha256": digest(json_bytes(before)),
            "after_inventory_sha256": digest(json_bytes({"files": expected, "console": before["console"]})),
            "removed_bytecode_files": len(removed), "git_sha": contract["git_sha"],
            "source_bundle_sha256": contract["source_bundle_sha256"], "base_image": BASE_IMAGE,
            "native_inventory_unchanged": True, "wheel_record": "original-preserved-overlay-receipt-required",
            "dependency_scope": "selected-exact-upstream-pins-only"}
    if packaging:
        report["packaging"] = {"policy_sha256": digest(json_bytes(PACKAGING_POLICY)),
            "removal_plan_sha256": digest(json_bytes(planned)), "removed_unlisted_bytecode_files": len(planned),
            "removal_verified_before_patch": True, "bytecode_executed": False, "plugins_loaded": False,
            "entrypoint_scope": "exact-upstream-map-only", "listed_bytecode_policy": "unchanged"}
    return report


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
            Tree(fd).check_startup_hooks()
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
                    require(next(entries, None) is None)
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
