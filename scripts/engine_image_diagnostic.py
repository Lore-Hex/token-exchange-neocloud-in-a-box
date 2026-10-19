# SPDX-License-Identifier: Apache-2.0
"""Read-only observations, never an alternative overlay admission policy."""

import csv
import os
from pathlib import Path
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
        except READ_ERRORS:
            report["collection"] = "partial"
    if any(item["status"] == "rejected" for item in report["source_files"]):
        report["collection"] = "partial"
    if (contract.get("diagnostic_nonhost_policy") is None and report["collection"] == "pass"
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
    overlay.require(set(report) - {"inventory_failure", "inventory_policy", "nonhost_policy", "classified_nonhost_assets"} == {"schema", "collection", "platform", "python", "candidate_status", "deployable",
        "abi_validated", "hardware_validated", "roots", "source_files", "checks", "overlay_admission"})
    overlay.require(report["schema"] == "nib.engine-image-diagnostic/v1" and report["collection"] in {"pass", "partial"}
        and report["platform"] == "linux/amd64" and re.fullmatch(r"3\.12\.[0-9]{1,3}", report["python"])
        and report["candidate_status"] == "BLOCKED" and report["deployable"] is False
        and report["abi_validated"] is False and report["hardware_validated"] is False)
    overlay.require(set(report["checks"]) == {"root_admission", "wheel_integrity", "record_integrity", "selected_root_admission", "filesystem_inventory"}
                    and all(value in STATUS for value in report["checks"].values()))
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
    accepted = (contract.get("diagnostic_nonhost_policy") is None and report["collection"] == "pass"
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
