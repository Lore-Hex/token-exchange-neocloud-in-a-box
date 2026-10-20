# SPDX-License-Identifier: Apache-2.0
"""Read-only observations, never an alternative overlay admission policy."""

import configparser
import csv
import io
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import sys
import sysconfig
import site
import time

HOOKS = {
    "distutils-precedence.pth": "2638ce9e2500e572a5e0de7faed6661eb569d1b696fcba07b0dd223da5f5d224",
    "nvidia_cutlass_dsl_packages.pth": "cc710b99ddb3d76361a70461461d274212a979d00c381d821fce5bb3ad9cf246",
}
STATUS = {"pass", "rejected", "unavailable"}
TOKEN = r"[A-Za-z0-9_.+!-]{1,180}"
SHA = r"[0-9a-f]{64}"
READ_ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError, UnicodeError, SyntaxError, csv.Error)
CENSUS_LIMITS = {"entries": 1000000, "depth": 32, "seconds": 120, "report_bytes": 16384}
CENSUS_BYTES = 1 << 60
CENSUS_FILE = 1 << 50
CENSUS_GROUPS = ("vllm", "torch", "nvidia", "triton", "other")
CENSUS_TYPES = ("regular", "directory", "symlink", "fifo", "socket", "block", "character", "other")
CENSUS_SIZES = {"empty": 0, "le_4k": 4096, "le_1m": 1 << 20, "le_64m": 64 << 20,
                "le_1g": 1 << 30, "gt_1g": CENSUS_FILE}
CENSUS_REASONS = {"entry-limit", "depth-limit", "time-limit", "size-limit", "unsafe-name",
                  "metadata-rejected", "directory-changed", "device-boundary", "root-selection"}
INTEGRITY_LIMITS = {"metadata_bytes": 16 << 20, "metadata_files": 2048, "record_rows": 100000,
                    "requirements": 4096, "seconds": 30, "paths_per_reason": 4, "path_chars": 180, "report_bytes": 65536}
# These observations could not be recovered or safely reported, not just compared.
INTEGRITY_INCOMPLETE_REASONS = frozenset({"diagnostic-time-limit", "diagnostic-byte-limit", "diagnostic-file-limit",
    "record-row-limit", "metadata-field-limit", "metadata-read", "metadata-invalid", "metadata-value",
    "engine-distribution", "direct-url", "entrypoints-read", "entrypoints-metadata", "console-read",
    "record-read", "record-csv", "record-row", "record-path", "wheel-read", "wheel-metadata",
    "requirement-malformed", "source-version-unavailable", "module-collision", "diagnostic-prerequisite"})
INTEGRITY_REASONS = INTEGRITY_INCOMPLETE_REASONS | frozenset({
    "duplicate-distribution", "engine-version", "requires-python", "editable-source", "entrypoints",
    "record-missing-file", "record-duplicate", "record-blank-fields",
    "record-hash-format", "record-size", "record-hash", "record-unlisted-owned", "record-unowned",
    "record-self", "record-console", "wheel-version", "wheel-purelib", "wheel-tags",
    "requirement-duplicate", "requirement-pin", "requirement-set", "dependency-missing",
    "dependency-version", "dependency-ambiguous", "source-version", "candidate-source", "engine-shadow"})


class IntegrityLimit(ValueError):
    def __init__(self, reason):
        super().__init__("bounded integrity diagnostic incomplete")
        self.reason = reason


def integrity_report():
    return {"schema": "nib.engine-integrity-diagnostic/v1", "complete": True,
        "limits": dict(INTEGRITY_LIMITS), "findings": [],
        "record": dict(complete=False, rows=0, unique_paths=0, owned_files=0, listed_missing=0,
                       listed_unowned=0, unlisted_owned=None),
        "bytecode": dict(complete=False, owned=0, with_source=0, without_source=0, source_recorded=0,
                         listed_empty=0, listed_hashed=0, listed_hash_matches=0, listed_other=0, unlisted=None,
                         unlisted_with_recorded_source=None, unlisted_without_recorded_source=None),
        "metadata": dict(distribution=None, engine_version=None, requires_python=[], wheel_version=[],
                         root_is_purelib=[], wheel_tags=[], entrypoints_match=None, entrypoint_groups=[], generated_version_match=None),
        "dependencies": []}


def integrity_observations(overlay, tree, inventory, dist, contract, console):
    """Observe strict inspector predicates independently; never grant admission."""
    result = integrity_report()
    findings, spent, reads = {}, 0, 0
    deadline = min(tree.deadline, time.monotonic() + INTEGRITY_LIMITS["seconds"])

    def timely():
        if time.monotonic() >= deadline:
            raise IntegrityLimit("diagnostic-time-limit")

    def issue(reason, path=None):
        overlay.require(reason in INTEGRITY_REASONS)
        if reason in INTEGRITY_INCOMPLETE_REASONS:
            result["complete"] = False
        item = findings.setdefault(reason, {"reason": reason, "count": 0, "paths": []})
        item["count"] += 1
        if (path is not None and len(path) <= INTEGRITY_LIMITS["path_chars"]
                and re.fullmatch(r"[A-Za-z0-9_./+-]+", path)):
            try:
                overlay.safe_name(path)
            except ValueError:
                return
            if path not in item["paths"] and len(item["paths"]) < INTEGRITY_LIMITS["paths_per_reason"]:
                item["paths"].append(path)

    def read(name, reason, source=tree):
        nonlocal spent, reads
        timely()
        if reads >= INTEGRITY_LIMITS["metadata_files"]:
            raise IntegrityLimit("diagnostic-file-limit")
        reads += 1
        try:
            data = source.read(name)
        except READ_ERRORS:
            issue(reason, name if source is tree else None)
            result["complete"] = False
            return None
        spent += len(data)
        if spent > INTEGRITY_LIMITS["metadata_bytes"]:
            raise IntegrityLimit("diagnostic-byte-limit")
        return data

    def flags(checks, path=None):
        for reason, passed in checks.items():
            if not passed:
                issue(reason, path)

    def values(message, field, pattern=TOKEN):
        observed = message.get_all(field, [])
        if len(observed) > 16:
            raise IntegrityLimit("metadata-field-limit")
        if not all(isinstance(value, str) and re.fullmatch(pattern, value) for value in observed):
            issue("metadata-value")
            return []
        return list(observed)

    def observe_record():
        stats, bytecode = result["record"], result["bytecode"]
        record_name = dist + "/RECORD"
        owned = overlay.record_owned(inventory, dist)
        stats["owned_files"] = len(owned)
        seen, pyc_fields, pyc_hash_matches = set(), {}, set()
        console_item = None
        if console is not None:
            data = read("vllm", "console-read", console[1])
            if data is not None:
                console_item = {"bytes": len(data), "sha256": overlay.digest(data)}
            data = read(dist + "/entry_points.txt", "entrypoints-read")
            if data is not None:
                try:
                    matched = overlay.entrypoints_match(data)
                    result["metadata"]["entrypoints_match"] = matched
                    flags({"entrypoints": matched}, dist + "/entry_points.txt")
                    parser = configparser.ConfigParser(interpolation=None, strict=True)
                    parser.read_string(data.decode("utf-8"))
                    overlay.require(not parser.defaults())
                    if len(parser.sections()) > 16:
                        raise IntegrityLimit("metadata-field-limit")
                    groups = []
                    for group in sorted(parser.sections()):
                        overlay.require(re.fullmatch(TOKEN, group))
                        count = len(parser[group])
                        if count > 256:
                            raise IntegrityLimit("metadata-field-limit")
                        groups.append({"name": group, "entries": count})
                    result["metadata"]["entrypoint_groups"] = groups
                except IntegrityLimit:
                    raise
                except (configparser.Error, *READ_ERRORS):
                    issue("entrypoints-metadata", dist + "/entry_points.txt")
        data = read(record_name, "record-read")
        if data is not None:
            try:
                for row in csv.reader(io.StringIO(data.decode("utf-8")), strict=True):
                    timely()
                    if stats["rows"] >= INTEGRITY_LIMITS["record_rows"]:
                        raise IntegrityLimit("record-row-limit")
                    stats["rows"] += 1
                    if len(row) != 3:
                        issue("record-row")
                        continue
                    name, encoded, size = row
                    is_console = console is not None and name == console[0]
                    if not is_console:
                        try:
                            overlay.safe_name(name)
                        except ValueError:
                            issue("record-path")
                            continue
                    path = None if is_console else name
                    if name in seen:
                        issue("record-duplicate", path)
                        continue
                    seen.add(name)
                    stats["unique_paths"] = len(seen)
                    if name in owned and name.endswith(".pyc"):
                        pyc_fields[name] = "empty" if encoded == size == "" else "hashed" if encoded and size else "other"
                    item = console_item if is_console else inventory.get(name)
                    if is_console and item is None:
                        continue  # Console read failure is unknown content, not a missing inventory file.
                    if item is None:
                        stats["listed_missing"] += 1
                        issue("record-missing-file", path)
                    else:
                        flags(overlay.record_field_checks(name, encoded, size, item, record_name), path)
                        if name in pyc_fields and pyc_fields[name] == "hashed":
                            hashes = overlay.record_digest_checks(encoded, size, item)
                            flags(hashes, path)
                            if all(hashes.values()):
                                pyc_hash_matches.add(name)
                    if name not in owned and not is_console:
                        stats["listed_unowned"] += 1
                        issue("record-unowned", path)
                stats["complete"] = True
            except IntegrityLimit:
                raise
            except READ_ERRORS:
                issue("record-csv", record_name)
                result["complete"] = False
        if stats["complete"]:
            missing = owned - seen
            stats["unlisted_owned"] = len(missing)
            bytecode["unlisted_with_recorded_source"] = bytecode["unlisted_without_recorded_source"] = 0
            for name in sorted(missing):
                timely()
                issue("record-unlisted-owned", name)
            flags({"record-self": record_name in seen}, record_name)
            if console is not None:
                flags({"record-console": console[0] in seen})
            for item in contract["files"]:
                timely()
                flags({"candidate-source": overlay.candidate_file_matches(item["path"], item["sha256"][0], inventory, seen)}, item["path"])
        for name in sorted(owned):
            timely()
            if not name.endswith(".pyc"):
                continue
            bytecode["owned"] += 1
            path = PurePosixPath(name)
            candidates = {name[:-1]}
            if path.parent.name == "__pycache__":
                candidates |= {str(path.parent.parent / (path.name[:index] + ".py"))
                               for index, char in enumerate(path.name) if char == "."}
            sources = {p for p in candidates if p in owned and p.endswith(".py") and overlay.bytecode_for(name, p)}
            bytecode["with_source" if sources else "without_source"] += 1
            bytecode["source_recorded"] += int(bool(sources & seen))
            if name in pyc_fields:
                bytecode["listed_" + pyc_fields[name]] += 1
                bytecode["listed_hash_matches"] += int(name in pyc_hash_matches)
            elif stats["complete"]:
                key = "unlisted_with_recorded_source" if sources & seen else "unlisted_without_recorded_source"
                bytecode[key] += 1
        if stats["complete"]:
            bytecode["unlisted"] = bytecode["owned"] - len(pyc_fields)
        bytecode["complete"] = True

    def observe_metadata():
        distributions, distribution_counts, engine, declared = {}, {}, None, {}
        pins = contract["dependency_pins"]
        flags({"engine-shadow": overlay.engine_layout_matches(inventory)})
        for name in sorted(inventory):
            timely()
            if name.endswith(".dist-info/direct_url.json"):
                data = read(name, "direct-url")
                if data is not None:
                    try:
                        direct = overlay.load_json(data)
                        overlay.require(isinstance(direct, dict))
                        info = direct.get("dir_info", {})
                        overlay.require(isinstance(info, dict) and type(info.get("editable", False)) is bool)
                        flags({"editable-source": not info.get("editable", False)}, name)
                    except READ_ERRORS:
                        issue("direct-url", name)
            if not (name.endswith(".dist-info/METADATA") and name.count("/") == 1):
                continue
            data = read(name, "metadata-read")
            if data is None:
                continue
            try:
                package, version, meta = overlay.distribution_metadata(data, name.split("/", 1)[0])
                versions = distributions.setdefault(package, [])
                distribution_counts[package] = distribution_counts.get(package, 0) + 1
                if versions:
                    issue("duplicate-distribution", name)
                observed_version = version if re.fullmatch(TOKEN, version) else None
                if observed_version is None:
                    issue("metadata-value", name)
                if len(versions) < 2:
                    versions.append(observed_version)
                if name == dist + "/METADATA":
                    engine = meta
                    result["metadata"]["engine_version"] = observed_version
                    flags(overlay.engine_metadata_checks(version, meta), name)
                    result["metadata"]["requires_python"] = values(meta, "Requires-Python", r"[<>=!~0-9., *]{1,80}")
            except IntegrityLimit:
                raise
            except READ_ERRORS:
                issue("metadata-invalid", name)
        if engine is None:
            issue("engine-distribution", dist + "/METADATA")
        else:
            for index, dependency in enumerate(engine.get_all("Requires-Dist", [])):
                timely()
                if index >= INTEGRITY_LIMITS["requirements"]:
                    raise IntegrityLimit("metadata-field-limit")
                try:
                    package, constraint = overlay.requirement_pin(dependency)
                except READ_ERRORS:
                    issue("requirement-malformed")
                    continue
                if package in pins:
                    constraints = declared.setdefault(package, {"count": 0, "matches": True})
                    if constraints["count"]:
                        issue("requirement-duplicate")
                    constraints["count"] += 1
                    constraints["matches"] &= constraint == "==" + pins[package]
                    flags({"requirement-pin": constraint == "==" + pins[package]})
            flags({"requirement-set": set(declared) == set(pins) - {"nvidia-nccl-cu13"}})
        for package, expected in sorted(pins.items()):
            installed = distributions.get(package, [])
            constraints = declared.get(package, {"count": 0, "matches": False})
            matches = distribution_counts.get(package) == 1 and overlay.dependency_matches(package, installed[0], expected)
            flags({"dependency-missing": bool(installed), "dependency-ambiguous": len(installed) <= 1,
                   "dependency-version": not installed or matches})
            # No requirement markers, URLs, unknown package names or exception text.
            result["dependencies"].append({"name": package, "expected": expected, "installed": installed,
                "installed_count": distribution_counts.get(package, 0), "installed_matches": matches,
                "declared_count": constraints["count"], "declared_matches": constraints["matches"]})
        data = read(dist + "/WHEEL", "wheel-read")
        if data is not None:
            try:
                wheel = overlay.headers(data)
                flags(overlay.wheel_metadata_checks(wheel), dist + "/WHEEL")
                for field, key in (("Wheel-Version", "wheel_version"), ("Root-Is-Purelib", "root_is_purelib"), ("Tag", "wheel_tags")):
                    result["metadata"][key] = values(wheel, field)
            except IntegrityLimit:
                raise
            except READ_ERRORS:
                issue("wheel-metadata", dist + "/WHEEL")
        data = read("vllm/_version.py", "source-version-unavailable")
        if data is not None:
            try:
                versions = overlay.generated_versions(data)
                overlay.require(result["metadata"]["engine_version"] is not None)
                matched = versions == [result["metadata"]["engine_version"]]
                result["metadata"]["generated_version_match"] = matched
                flags({"source-version": matched}, "vllm/_version.py")
            except READ_ERRORS:
                issue("source-version-unavailable", "vllm/_version.py")
        try:
            tree.check_module_collisions(item["path"] for item in contract["files"])
        except READ_ERRORS:
            issue("module-collision")

    try:
        if dist is None or not re.fullmatch(r"vllm-[A-Za-z0-9_.+!-]{1,180}\.dist-info", dist):
            issue("engine-distribution")
            result["complete"] = False
            result["findings"] = list(findings.values())
            return result
        result["metadata"]["distribution"] = dist
        observe_record()
        observe_metadata()
        timely()
    except IntegrityLimit as failure:
        issue(failure.reason)
        result["complete"] = False
    except READ_ERRORS:
        issue("metadata-invalid")
        result["complete"] = False
    result["findings"] = [dict(item, paths=sorted(item["paths"])) for _, item in sorted(findings.items())]
    return result


def validate_integrity_observations(overlay, value, contract):
    overlay.require(type(value) is dict and set(value) == {"schema", "complete", "limits", "findings", "record", "bytecode", "metadata", "dependencies"}
                    and value["schema"] == "nib.engine-integrity-diagnostic/v1" and type(value["complete"]) is bool
                    and value["limits"] == INTEGRITY_LIMITS
                    and len(overlay.json_bytes(value)) <= INTEGRITY_LIMITS["report_bytes"])
    rows = value["findings"]
    overlay.require(type(rows) is list and len(rows) <= len(INTEGRITY_REASONS))
    seen = []
    for row in rows:
        overlay.require(type(row) is dict and set(row) == {"reason", "count", "paths"}
                        and row["reason"] in INTEGRITY_REASONS and row["reason"] not in seen
                        and type(row["count"]) is int and 0 < row["count"] <= 1000000
                        and type(row["paths"]) is list and len(row["paths"]) <= min(row["count"], INTEGRITY_LIMITS["paths_per_reason"])
                        and row["paths"] == sorted(set(row["paths"])))
        seen.append(row["reason"])
        for path in row["paths"]:
            overlay.require(type(path) is str and 0 < len(path) <= INTEGRITY_LIMITS["path_chars"]
                            and re.fullmatch(r"[A-Za-z0-9_./+-]+", path))
            overlay.safe_name(path)
    overlay.require(seen == sorted(seen))
    overlay.require(not value["complete"] or not (set(seen) & INTEGRITY_INCOMPLETE_REASONS))
    record, bytecode = value["record"], value["bytecode"]
    overlay.require(type(record) is dict and set(record) == {"complete", "rows", "unique_paths", "owned_files", "listed_missing", "listed_unowned", "unlisted_owned"}
                    and type(record["complete"]) is bool)
    overlay.require(type(bytecode) is dict and set(bytecode) == {"complete", "owned", "with_source", "without_source", "source_recorded",
        "listed_empty", "listed_hashed", "listed_hash_matches", "listed_other", "unlisted",
        "unlisted_with_recorded_source", "unlisted_without_recorded_source"} and type(bytecode["complete"]) is bool)
    for obj, optional in ((record, {"unlisted_owned"}), (bytecode, {"unlisted", "unlisted_with_recorded_source", "unlisted_without_recorded_source"})):
        for key, count in obj.items():
            if key == "complete":
                continue
            available = record["complete"] and (obj is record or bytecode["complete"])
            overlay.require((key in optional and not available and count is None)
                            or (type(count) is int and 0 <= count <= 225000))
    overlay.require(record["rows"] <= INTEGRITY_LIMITS["record_rows"] and record["unique_paths"] <= record["rows"]
                    and record["listed_missing"] <= record["unique_paths"] and record["listed_unowned"] <= record["unique_paths"]
                    and bytecode["with_source"] + bytecode["without_source"] == bytecode["owned"]
                    and bytecode["source_recorded"] <= bytecode["with_source"]
                    and bytecode["listed_hash_matches"] <= bytecode["listed_hashed"]
                    and bytecode["listed_empty"] + bytecode["listed_hashed"] + bytecode["listed_other"] <= bytecode["owned"])
    if record["complete"]:
        overlay.require(type(record["unlisted_owned"]) is int and record["unlisted_owned"] <= record["owned_files"])
    if record["complete"] and bytecode["complete"]:
        overlay.require(type(bytecode["unlisted"]) is int)
        overlay.require(bytecode["unlisted"] == bytecode["owned"] - bytecode["listed_empty"] - bytecode["listed_hashed"] - bytecode["listed_other"])
        overlay.require(bytecode["unlisted_with_recorded_source"] + bytecode["unlisted_without_recorded_source"] == bytecode["unlisted"])
    meta = value["metadata"]
    overlay.require(type(meta) is dict and set(meta) == {"distribution", "engine_version", "requires_python", "wheel_version",
                    "root_is_purelib", "wheel_tags", "entrypoints_match", "entrypoint_groups", "generated_version_match"})
    overlay.require(meta["distribution"] is None or (type(meta["distribution"]) is str
                    and re.fullmatch(r"vllm-[A-Za-z0-9_.+!-]{1,180}\.dist-info", meta["distribution"])))
    overlay.require(meta["engine_version"] is None or (type(meta["engine_version"]) is str and re.fullmatch(TOKEN, meta["engine_version"])))
    for key in ("requires_python", "wheel_version", "root_is_purelib", "wheel_tags"):
        overlay.require(type(meta[key]) is list and len(meta[key]) <= 16)
        pattern = r"[<>=!~0-9., *]{1,80}" if key == "requires_python" else TOKEN
        overlay.require(all(type(item) is str and re.fullmatch(pattern, item) for item in meta[key]))
    for key in ("entrypoints_match", "generated_version_match"):
        overlay.require(meta[key] is None or type(meta[key]) is bool)
    if value["complete"]:
        overlay.require(all(meta[key] is not None for key in
                            ("distribution", "engine_version", "entrypoints_match", "generated_version_match")))
    groups = meta["entrypoint_groups"]
    overlay.require(type(groups) is list and len(groups) <= 16)
    names = []
    for group in groups:
        overlay.require(type(group) is dict and set(group) == {"name", "entries"}
                        and type(group["name"]) is str and re.fullmatch(TOKEN, group["name"])
                        and type(group["entries"]) is int and 0 <= group["entries"] <= 256)
        names.append(group["name"])
    overlay.require(names == sorted(set(names)))
    dependencies = value["dependencies"]
    pins, seen = contract["dependency_pins"], []
    overlay.require(type(dependencies) is list and len(dependencies) <= len(pins))
    for row in dependencies:
        overlay.require(type(row) is dict and set(row) == {"name", "expected", "installed", "installed_count", "installed_matches", "declared_count", "declared_matches"}
                        and row["name"] in pins and row["name"] not in seen and row["expected"] == pins[row["name"]]
                        and type(row["installed"]) is list and type(row["installed_count"]) is int
                        and 0 <= row["installed_count"] <= INTEGRITY_LIMITS["metadata_files"]
                        and len(row["installed"]) == min(row["installed_count"], 2)
                        and all(v is None or (type(v) is str and re.fullmatch(TOKEN, v)) for v in row["installed"])
                        and type(row["installed_matches"]) is bool and type(row["declared_matches"]) is bool
                        and type(row["declared_count"]) is int and 0 <= row["declared_count"] <= INTEGRITY_LIMITS["requirements"])
        seen.append(row["name"])
        overlay.require(not value["complete"] or None not in row["installed"])
        overlay.require(row["installed_matches"] == (row["installed_count"] == 1
            and overlay.dependency_matches(row["name"], row["installed"][0], row["expected"])))
        overlay.require(row["declared_count"] > 0 or not row["declared_matches"])
    overlay.require(seen == sorted(seen) and (not value["complete"] or (record["complete"] and bytecode["complete"] and set(seen) == set(pins))))


def census_counts():
    return {"entries": 0, "types": dict.fromkeys(CENSUS_TYPES, 0), "regular_bytes": 0,
            "largest_regular_bytes": 0, "multiply_linked_regular_entries": 0,
            "regular_sizes": dict.fromkeys(CENSUS_SIZES, 0)}


def census_report():
    return {"schema": "nib.engine-image-census/v1", "collection": "partial", "reason": "root-selection",
            "platform": "linux/amd64", "python": platform.python_version(),
            "candidate_status": "BLOCKED", "deployable": False, "abi_validated": False,
            "hardware_validated": False, "overlay_admission": "not-evaluated",
            "content_read": False, "link_targets_read": False, "selected_root": "sysconfig-purelib",
            "limits": dict(CENSUS_LIMITS), "max_depth": 0, "totals": census_counts(),
            "groups": {name: census_counts() for name in CENSUS_GROUPS}}


def metadata_census(overlay, tree, report):
    """Count directory entries using metadata only; never open non-directories."""
    deadline = min(tree.deadline, time.monotonic() + CENSUS_LIMITS["seconds"])
    device = os.fstat(tree.fd).st_dev

    def require(condition, reason):
        if not condition:
            raise overlay.InventoryRejected(reason)

    def timely():
        require(time.monotonic() < deadline, "time-limit")

    def walk(fd, depth, group=None):
        timely()
        before = os.fstat(fd)
        with os.scandir(fd) as entries:
            for entry in entries:
                timely()
                require(report["totals"]["entries"] < CENSUS_LIMITS["entries"], "entry-limit")
                require(depth < CENSUS_LIMITS["depth"], "depth-limit")
                try:
                    overlay.safe_name(entry.name)
                except ValueError:
                    raise overlay.InventoryRejected("unsafe-name") from None
                info = entry.stat(follow_symlinks=False)
                require(info.st_dev == device, "device-boundary")
                kind = next((name for name, predicate in (
                    ("regular", stat.S_ISREG), ("directory", stat.S_ISDIR), ("symlink", stat.S_ISLNK),
                    ("fifo", stat.S_ISFIFO), ("socket", stat.S_ISSOCK), ("block", stat.S_ISBLK),
                    ("character", stat.S_ISCHR)) if predicate(info.st_mode)), "other")
                size = info.st_size if kind == "regular" else 0
                require(0 <= size <= CENSUS_FILE and report["totals"]["regular_bytes"] + size <= CENSUS_BYTES,
                        "size-limit")
                bucket = next(key for key, ceiling in CENSUS_SIZES.items() if size <= ceiling)
                category = group or (entry.name if entry.name in CENSUS_GROUPS[:-1] else "other")
                for counts in (report["totals"], report["groups"][category]):
                    counts["entries"] += 1
                    counts["types"][kind] += 1
                    if kind == "regular":
                        counts["regular_bytes"] += size
                        counts["largest_regular_bytes"] = max(counts["largest_regular_bytes"], size)
                        counts["multiply_linked_regular_entries"] += int(info.st_nlink > 1)
                        counts["regular_sizes"][bucket] += 1
                report["max_depth"] = max(report["max_depth"], depth + 1)
                if kind == "directory":
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        require(overlay.identity(info) == overlay.identity(os.fstat(child)), "directory-changed")
                        walk(child, depth + 1, category)
                        require(overlay.identity(info) == overlay.identity(os.fstat(child))
                                == overlay.identity(os.stat(entry.name, dir_fd=fd, follow_symlinks=False)),
                                "directory-changed")
                    finally:
                        os.close(child)
                else:
                    current = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                    require(overlay.identity(info) == overlay.identity(current) and info.st_nlink == current.st_nlink,
                            "metadata-rejected")
        require(overlay.identity(before) == overlay.identity(os.fstat(fd)), "directory-changed")
        timely()

    try:
        walk(tree.fd, 0)
        report.update(collection="pass", reason=None)
    except overlay.InventoryRejected as failure:
        report["reason"] = failure.reason
    except READ_ERRORS:
        report["reason"] = "metadata-rejected"


def collect_census(overlay, paths):
    report = census_report()
    # The pinned image's interpreter selects the root, not an operator path or
    # imported distribution. No METADATA, RECORD, hooks or engine files are read.
    if paths["purelib"] == paths["platlib"]:
        try:
            with overlay.directory(paths["purelib"]) as fd:
                metadata_census(overlay, overlay.Tree(fd), report)
        except READ_ERRORS:
            report.update(collection="partial", reason="metadata-rejected")
    validate_census(overlay, report)
    return report


def validate_census(overlay, report):
    expected = census_report()
    overlay.require(set(report) == set(expected))
    dynamic = {"collection", "reason", "python", "max_depth", "totals", "groups"}
    overlay.require(all(report[key] == value for key, value in expected.items() if key not in dynamic)
                    and all(report[key] is False for key in ("deployable", "abi_validated", "hardware_validated",
                                                            "content_read", "link_targets_read"))
                    and re.fullmatch(r"3\.12\.[0-9]{1,3}", report["python"])
                    and report["collection"] in {"pass", "partial"}
                    and (report["reason"] is None if report["collection"] == "pass" else report["reason"] in CENSUS_REASONS))
    overlay.require(type(report["max_depth"]) is int and 0 <= report["max_depth"] <= CENSUS_LIMITS["depth"]
                    and set(report["groups"]) == set(CENSUS_GROUPS))
    for counts in [report["totals"], *report["groups"].values()]:
        overlay.require(set(counts) == set(census_counts()) and set(counts["types"]) == set(CENSUS_TYPES)
                        and set(counts["regular_sizes"]) == set(CENSUS_SIZES))
        values = [counts["entries"], counts["multiply_linked_regular_entries"],
                  *counts["types"].values(), *counts["regular_sizes"].values()]
        overlay.require(all(type(value) is int and 0 <= value <= CENSUS_LIMITS["entries"] for value in values)
                        and type(counts["regular_bytes"]) is int and 0 <= counts["regular_bytes"] <= CENSUS_BYTES
                        and type(counts["largest_regular_bytes"]) is int
                        and 0 <= counts["largest_regular_bytes"] <= min(counts["regular_bytes"], CENSUS_FILE)
                        and counts["entries"] == sum(counts["types"].values())
                        and counts["types"]["regular"] == sum(counts["regular_sizes"].values())
                        and counts["multiply_linked_regular_entries"] <= counts["types"]["regular"])
    totals, groups = report["totals"], list(report["groups"].values())
    for key in totals:
        if isinstance(totals[key], dict):
            overlay.require(all(value == sum(group[key][name] for group in groups) for name, value in totals[key].items()))
        elif key == "largest_regular_bytes":
            overlay.require(totals[key] == max(group[key] for group in groups))
        else:
            overlay.require(totals[key] == sum(group[key] for group in groups))
    overlay.require((report["max_depth"] == 0) == (totals["entries"] == 0)
                    and len(overlay.json_bytes(report)) <= CENSUS_LIMITS["report_bytes"])


def check(operation):
    try:
        operation()
        return "pass"
    except READ_ERRORS:
        return "rejected"


def root_observations(overlay, root):
    observed = {"entries": 0, "packages": [], "hooks": [], "contains_vllm": False,
                "startup_gate": "unavailable", "directory_inventory": "unavailable",
                "metadata_inventory": "unavailable", "hook_inventory": "unavailable"}
    try:
        with overlay.directory(root) as fd:
            tree, entries = overlay.Tree(fd), []
            try:
                with os.scandir(fd) as scan:
                    for entry in scan:
                        overlay.require(len(entries) < 4096 and time.monotonic() < tree.deadline)
                        entries.append(entry)
                observed["directory_inventory"] = "pass"
            except READ_ERRORS:
                observed["directory_inventory"] = "rejected"
            observed["entries"] = len(entries)
            observed["metadata_inventory"] = observed["hook_inventory"] = observed["directory_inventory"]
            for entry in sorted(entries, key=lambda entry: entry.name):
                name = entry.name
                if not re.fullmatch(TOKEN, name):
                    observed["directory_inventory"] = "rejected"
                    observed["metadata_inventory"] = observed["hook_inventory"] = "rejected"
                    continue
                if name.endswith((".dist-info", ".egg-info")):
                    try:
                        overlay.require(len(observed["packages"]) < 2048 and time.monotonic() < tree.deadline)
                        metadata_name = name + "/METADATA" if name.endswith(".dist-info") else (
                            name + "/PKG-INFO" if entry.is_dir(follow_symlinks=False) else name)
                        metadata = overlay.headers(tree.read(metadata_name))
                        package, version = overlay.one(metadata, "Name"), overlay.one(metadata, "Version")
                        overlay.require(re.fullmatch(TOKEN, package) and re.fullmatch(TOKEN, version))
                        observed["packages"].append({"name": package, "version": version})
                        observed["contains_vllm"] |= package.lower() == "vllm"
                    except READ_ERRORS:
                        observed["metadata_inventory"] = "rejected"
                if name.endswith(".pth") or name.split(".", 1)[0] in ("sitecustomize", "usercustomize"):
                    hook = {"name": name, "sha256": None, "text": None, "classification": "unsupported"}
                    try:
                        overlay.require(len(observed["hooks"]) < 64 and time.monotonic() < tree.deadline)
                        observed["hooks"].append(hook)
                        info = entry.stat(follow_symlinks=False)
                        overlay.require(stat.S_ISREG(info.st_mode) and info.st_size <= 16384)
                        data = tree.read(name)
                        overlay.require(len(data) <= 16384)
                        hook.update(sha256=overlay.digest(data), classification="unknown-content")
                        if HOOKS.get(name) == hook["sha256"]:
                            hook.update(text=data.decode("utf-8", errors="strict"), classification="known-public-content")
                    except READ_ERRORS:
                        observed["hook_inventory"] = "rejected"
            observed["startup_gate"] = check(tree.check_startup_hooks)
    except READ_ERRORS:
        observed["directory_inventory"] = "rejected"
    return observed


def source_observations(overlay, tree, contract):
    result = []
    for item in contract["files"]:
        actual, status = None, "rejected"
        try:
            actual, status = overlay.digest(tree.read(item["path"])), "read"
        except FileNotFoundError:
            status = "absent"
        except READ_ERRORS:
            pass
        result.append({"path": item["path"], "sha256": actual, "status": status,
                       "matches_base": status != "rejected" and actual == item["sha256"][0]})
    return result


def collect(overlay, contract):
    overlay.require(platform.system() == "Linux" and platform.machine() == "x86_64"
                    and sys.version_info[:2] == (3, 12))
    paths = sysconfig.get_paths()
    mode = contract.get("diagnostic_mode", "integrity")
    overlay.require(mode in {"integrity", "metadata-census"})
    overlay.require(contract.get("diagnostic_integrity_details") is None or contract.get("diagnostic_integrity_details") is True)
    if mode == "metadata-census":
        return collect_census(overlay, paths)
    roots = sorted(set(site.getsitepackages()) | {paths["purelib"], paths["platlib"]})
    overlay.require(len(roots) <= 4)
    report = {"schema": "nib.engine-image-diagnostic/v1", "collection": "pass",
              "platform": "linux/amd64", "python": platform.python_version(),
              "candidate_status": "BLOCKED", "deployable": False, "abi_validated": False,
              "hardware_validated": False, "roots": [], "source_files": [],
              "checks": {"root_admission": check(overlay.installed_root), "wheel_integrity": "unavailable",
                         "record_integrity": "unavailable", "selected_root_admission": "unavailable",
                         "filesystem_inventory": "unavailable"},
              "overlay_admission": "rejected"}
    candidates = []
    for root in roots:
        if not os.path.lexists(root):
            continue
        observed = root_observations(overlay, root)
        observed["label"] = "root-" + str(len(report["roots"]))
        report["roots"].append(observed)
        if observed["contains_vllm"]:
            candidates.append(root)
        if any(observed[key] != "pass" for key in ("directory_inventory", "metadata_inventory", "hook_inventory")):
            report["collection"] = "partial"
    if len(candidates) == 1:
        try:
            with overlay.directory(candidates[0]) as fd:
                tree = overlay.Tree(fd)
                report["source_files"] = source_observations(overlay, tree, contract)
                report["checks"]["filesystem_inventory"] = "rejected"
                policy = contract.get("diagnostic_inventory_policy")
                if policy is not None:
                    overlay.require(policy == overlay.DIAGNOSTIC_INVENTORY_POLICY
                                    and contract["base_image"] == policy["base_image"]
                                    and contract["official_config"]["sha256"] == policy["config_sha256"])
                    report["inventory_policy"] = policy
                nonhost = contract.get("diagnostic_nonhost_policy")
                classified = None
                if nonhost is not None:
                    overlay.require(nonhost == overlay.CUTLASS_STUB_POLICY and policy == overlay.DIAGNOSTIC_INVENTORY_POLICY
                                    and contract["base_image"] == nonhost["base_image"]
                                    and contract["official_config"]["sha256"] == nonhost["config_sha256"])
                    report["nonhost_policy"] = nonhost
                    classified = report["classified_nonhost_assets"] = []
                try:
                    inventory = tree.inventory(diagnostic_policy=policy, nonhost_policy=nonhost, classified=classified)
                except overlay.InventoryRejected as failure:
                    report["inventory_failure"] = {"reason": failure.reason, "path": failure.path}
                    raise
                report["checks"]["filesystem_inventory"] = "pass"
                with overlay.directory(paths["scripts"]) as scripts_fd:
                    console = (os.path.relpath(Path(paths["scripts"]) / "vllm", candidates[0]), overlay.Tree(scripts_fd))
                    distributions = [name.rsplit("/", 1)[0] for name in inventory
                                     if name.startswith("vllm-") and name.endswith(".dist-info/METADATA") and name.count("/") == 1]
                    if len(distributions) == 1:
                        report["checks"]["record_integrity"] = check(
                            lambda: overlay.inspect_record(tree, inventory, distributions[0], console=console))
                    report["checks"]["wheel_integrity"] = check(
                        lambda: overlay.inspect_integrity(tree, contract, console=console, inventory=inventory))
                    report["checks"]["selected_root_admission"] = check(lambda: overlay.inspect(tree, contract, console=console))
                    if contract.get("diagnostic_integrity_details") is True:
                        details = integrity_observations(overlay, tree, inventory,
                            distributions[0] if len(distributions) == 1 else None, contract, console)
                        report["integrity_details"] = details
                        if not details["complete"]:
                            report["collection"] = "partial"
        except READ_ERRORS:
            report["collection"] = "partial"
    if any(item["status"] == "rejected" for item in report["source_files"]):
        report["collection"] = "partial"
    if (contract.get("diagnostic_integrity_details") is True and report["checks"]["filesystem_inventory"] == "pass"
            and "integrity_details" not in report):
        details = integrity_report()
        details.update(complete=False, findings=[{"reason": "diagnostic-prerequisite", "count": 1, "paths": []}])
        report["integrity_details"] = details
        report["collection"] = "partial"
    if (contract.get("diagnostic_nonhost_policy") is None and contract.get("diagnostic_integrity_details") is None and report["collection"] == "pass"
            and all(value == "pass" for value in report["checks"].values())):
        report["overlay_admission"] = "accepted"
    validate_report(overlay, report, contract)
    return report


def validate_report(overlay, report, contract):
    """Reject unknown child fields/strings before any child output is published."""
    mode = contract.get("diagnostic_mode", "integrity")
    overlay.require(mode in {"integrity", "metadata-census"})
    if mode == "metadata-census":
        validate_census(overlay, report)
        return
    overlay.require(set(report) - {"inventory_failure", "inventory_policy", "nonhost_policy", "classified_nonhost_assets", "integrity_details"} == {"schema", "collection", "platform", "python", "candidate_status", "deployable",
        "abi_validated", "hardware_validated", "roots", "source_files", "checks", "overlay_admission"})
    overlay.require(report["schema"] == "nib.engine-image-diagnostic/v1" and report["collection"] in {"pass", "partial"}
        and report["platform"] == "linux/amd64" and re.fullmatch(r"3\.12\.[0-9]{1,3}", report["python"])
        and report["candidate_status"] == "BLOCKED" and report["deployable"] is False
        and report["abi_validated"] is False and report["hardware_validated"] is False)
    overlay.require(set(report["checks"]) == {"root_admission", "wheel_integrity", "record_integrity", "selected_root_admission", "filesystem_inventory"}
                    and all(value in STATUS for value in report["checks"].values()))
    if "integrity_details" in report:
        overlay.require(contract.get("diagnostic_integrity_details") is True
                        and contract["base_image"] == overlay.CUTLASS_STUB_POLICY["base_image"]
                        and contract["official_config"]["sha256"] == overlay.CUTLASS_STUB_POLICY["config_sha256"]
                        and report["checks"]["filesystem_inventory"] == "pass")
        validate_integrity_observations(overlay, report["integrity_details"], contract)
        overlay.require(report["integrity_details"]["complete"] or report["collection"] == "partial")
    if contract.get("diagnostic_integrity_details") is True and report["checks"]["filesystem_inventory"] == "pass":
        overlay.require("integrity_details" in report)
    if "inventory_policy" in report:
        policy = report["inventory_policy"]
        overlay.require(policy == contract.get("diagnostic_inventory_policy") == overlay.DIAGNOSTIC_INVENTORY_POLICY
                        and contract["base_image"] == policy["base_image"]
                        and contract["official_config"]["sha256"] == policy["config_sha256"])
    if contract.get("diagnostic_inventory_policy") is not None and report["checks"]["filesystem_inventory"] == "pass":
        overlay.require("inventory_policy" in report)
    if "nonhost_policy" in report or "classified_nonhost_assets" in report:
        policy = report["nonhost_policy"]
        overlay.require(policy == contract.get("diagnostic_nonhost_policy") == overlay.CUTLASS_STUB_POLICY
                        and report.get("inventory_policy") == overlay.DIAGNOSTIC_INVENTORY_POLICY
                        and contract["base_image"] == policy["base_image"]
                        and contract["official_config"]["sha256"] == policy["config_sha256"])
        rows = report["classified_nonhost_assets"]
        overlay.require(type(rows) is list and len(rows) <= 4)
        seen = []
        for row in rows:
            overlay.require(row == {"path": row["path"], "sha256": policy["sha256"], "bytes": policy["bytes"],
                                    "classification": "published-cutlass-aot-crosscompile-stub"}
                            and row["path"] in policy["paths"] and row["path"] not in seen)
            seen.append(row["path"])
        overlay.require(seen == sorted(seen) and report["checks"]["selected_root_admission"] != "pass"
                        and report["checks"]["filesystem_inventory"] in {"pass", "rejected"})
        if report["checks"]["filesystem_inventory"] == "pass":
            overlay.require(seen == policy["paths"])
    if contract.get("diagnostic_nonhost_policy") is not None and report["checks"]["filesystem_inventory"] == "pass":
        overlay.require("nonhost_policy" in report)
    if "inventory_failure" in report:
        failure = report["inventory_failure"]
        overlay.require(isinstance(failure, dict) and set(failure) == {"reason", "path"}
                        and failure["reason"] in overlay.INVENTORY_REASONS
                        and report["collection"] == "partial"
                        and report["checks"]["filesystem_inventory"] == "rejected")
        if failure["path"] is not None:
            overlay.require(isinstance(failure["path"], str) and 0 < len(failure["path"]) <= 512)
            overlay.safe_name(failure["path"])
        if failure["reason"] in {"nonhost-asset-mismatch", "nonhost-asset-missing"}:
            overlay.require("nonhost_policy" in report and failure["path"] in overlay.CUTLASS_STUB_POLICY["paths"]
                            and all(row["path"] != failure["path"] for row in report["classified_nonhost_assets"]))
    accepted = (contract.get("diagnostic_nonhost_policy") is None and contract.get("diagnostic_integrity_details") is None and report["collection"] == "pass"
                and all(value == "pass" for value in report["checks"].values()))
    overlay.require(report["overlay_admission"] == ("accepted" if accepted else "rejected"))
    overlay.require(isinstance(report["roots"], list) and len(report["roots"]) <= 4)
    for index, root in enumerate(report["roots"]):
        overlay.require(set(root) == {"entries", "packages", "hooks", "contains_vllm", "startup_gate", "label",
                                      "directory_inventory", "metadata_inventory", "hook_inventory"}
            and root["label"] == "root-" + str(index) and type(root["entries"]) is int
            and 0 <= root["entries"] <= 4096 and type(root["contains_vllm"]) is bool
            and root["startup_gate"] in STATUS and isinstance(root["packages"], list)
            and isinstance(root["hooks"], list) and len(root["packages"]) <= 2048 and len(root["hooks"]) <= 64)
        for key in ("directory_inventory", "metadata_inventory", "hook_inventory"):
            overlay.require(root[key] in STATUS and (root[key] == "pass" or report["collection"] == "partial"))
        for package in root["packages"]:
            overlay.require(set(package) == {"name", "version"}
                            and all(re.fullmatch(TOKEN, value) for value in package.values()))
        for hook in root["hooks"]:
            overlay.require(set(hook) == {"name", "sha256", "text", "classification"}
                            and re.fullmatch(TOKEN, hook["name"]))
            overlay.require(hook["sha256"] is None or re.fullmatch(SHA, hook["sha256"]))
            overlay.require(hook["classification"] in {"unsupported", "unknown-content", "known-public-content"})
            if hook["text"] is not None:
                overlay.require(hook["classification"] == "known-public-content"
                    and isinstance(hook["text"], str) and len(hook["text"].encode()) <= 16384
                    and overlay.digest(hook["text"].encode()) == hook["sha256"] == HOOKS.get(hook["name"]))
            else:
                overlay.require(hook["classification"] != "known-public-content")
    overlay.require(isinstance(report["source_files"], list)
                    and len(report["source_files"]) in (0, len(contract["files"])))
    for observed, expected in zip(report["source_files"], contract["files"]):
        overlay.require(set(observed) == {"path", "sha256", "matches_base", "status"} and observed["path"] == expected["path"]
            and (observed["sha256"] is None or re.fullmatch(SHA, observed["sha256"]))
            and observed["status"] in {"read", "absent", "rejected"}
            and (observed["sha256"] is not None) == (observed["status"] == "read")
            and (observed["status"] != "rejected" or report["collection"] == "partial")
            and type(observed["matches_base"]) is bool
            and observed["matches_base"] == (observed["status"] != "rejected" and observed["sha256"] == expected["sha256"][0]))
    if report["checks"]["filesystem_inventory"] != "pass":
        overlay.require(all(report["checks"][key] == "unavailable" for key in
                            ("wheel_integrity", "record_integrity", "selected_root_admission")))
        if report["checks"]["filesystem_inventory"] == "rejected":
            overlay.require(report["collection"] == "partial")
    if report["checks"]["wheel_integrity"] == "pass":
        overlay.require(len(report["source_files"]) == len(contract["files"])
                        and all(item["matches_base"] for item in report["source_files"]))
    if report["checks"]["root_admission"] == "pass" and report["collection"] == "pass":
        overlay.require(sum(root["contains_vllm"] for root in report["roots"]) == 1
                        and all(root["startup_gate"] == "pass" and not root["hooks"]
                                and (root["contains_vllm"] or root["entries"] == 0) for root in report["roots"]))
