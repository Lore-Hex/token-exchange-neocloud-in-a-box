# SPDX-License-Identifier: Apache-2.0
"""Bounded public-image Python observations, never an import/application policy."""

import importlib.machinery
import csv
import os
from pathlib import PurePosixPath
import platform
import posixpath
import re
import site
import stat
import subprocess
import sys
import sysconfig
import time

LIMITS = {"seconds": 300, "file_bytes": 64 << 20, "hashed_bytes": 256 << 20,
          "links": 8, "locations": 32, "scan_entries": 300000, "report_bytes": 131072}
SOURCES = ("site.py", "subprocess.py", "importlib/_bootstrap.py", "importlib/_bootstrap_external.py",
           "multiprocessing/__init__.py", "multiprocessing/context.py", "multiprocessing/spawn.py",
           "multiprocessing/forkserver.py", "multiprocessing/resource_tracker.py",
           "multiprocessing/popen_spawn_posix.py", "multiprocessing/popen_forkserver.py", "multiprocessing/util.py")
PATH_KEYS = ("stdlib", "platstdlib", "purelib", "platlib", "scripts")
BUILD_KEYS = ("SOABI", "EXT_SUFFIX", "MULTIARCH", "Py_DEBUG", "Py_ENABLE_SHARED")
FIXED_LOCATIONS = ("executable", "base_executable", "prefix", "base_prefix", "exec_prefix", "base_exec_prefix",
                   *PATH_KEYS, "dynload", "pycache_prefix")
FILE_NAMES = ("executable", "base_executable", *SOURCES, "distutils-precedence.pth",
              "nvidia_cutlass_dsl_packages.pth", "cutlass-init")
SCAN_NAMES = ("stdlib", "selected", "cutlass", "dynload")
ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError, UnicodeError)
SHA = r"[0-9a-f]{64}"
FILE_REASON_STAGES = {"unsafe-path": "resolution", "resolution-failed": "resolution", "time-limit": "resolution",
    "stat-failed": "stat", "confirmed-absent": "stat", "ancestor-absent": "stat", "absence-unconfirmed": "stat",
    "dangling-link": "stat", "link-limit": "resolution", "link-read-failed": "resolution", "unsafe-target": "resolution",
    "identity-changed": "identity", "ancestor-not-directory": "resolution", "directory-open-failed": "resolution",
    "not-regular": "type", "link-count": "link-count", "file-size-limit": "size-budget", "total-size-limit": "size-budget",
    "read-rejected": "read", "read-failed": "read", "identity-recheck-failed": "identity"}


def observed_stat(info=None):
    if info is None:
        return dict(type=None, mode=None, size=None, nlink=None)
    kind = next((name for name, predicate in (("regular", stat.S_ISREG), ("directory", stat.S_ISDIR),
        ("symlink", stat.S_ISLNK), ("fifo", stat.S_ISFIFO), ("socket", stat.S_ISSOCK),
        ("block", stat.S_ISBLK), ("character", stat.S_ISCHR)) if predicate(info.st_mode)), "other")
    return {"type": kind, "mode": stat.S_IMODE(info.st_mode),
            "size": info.st_size if 0 <= info.st_size < 1 << 63 else None,
            "nlink": info.st_nlink if 0 <= info.st_nlink < 1 << 64 else None}


class ObservationRejected(ValueError):
    def __init__(self, stage, reason, info=None):
        super().__init__("descriptor observation rejected")
        self.stage, self.reason, self.observed = stage, reason, observed_stat(info)


def public_path(value):
    # Only public image interpreter/library namespaces, never host/home/proc paths.
    return (isinstance(value, str) and len(value) <= 512
            and re.fullmatch(r"/[A-Za-z0-9_./+-]+", value) is not None
            and posixpath.normpath(value) == value and "//" not in value
            and any(value == p or value.startswith(p + "/") for p in ("/usr", "/lib", "/lib64", "/bin")))


def location(name, value, optional=False):
    return {"name": name, "path": value if public_path(value) else None,
            "status": "observed" if public_path(value) else "unset" if value is None and optional else "rejected"}


def file_report(name, path):
    return {"name": name, "path": path, "resolved": None, "links": [], "status": "rejected",
            "sha256": None, "bytes": None, "mode": None, "stage": "resolution", "reason": "resolution-failed",
            "observed": observed_stat()}


class Filesystem:
    """Descriptor-only anchored link resolution; overlay admission is unchanged."""

    def __init__(self, overlay, fd):
        self.overlay, self.fd = overlay, fd
        self.deadline = time.monotonic() + LIMITS["seconds"]
        self.spent = 0

    def resolve(self, path):
        if not public_path(path):
            raise ObservationRejected("resolution", "unsafe-path")
        links = []
        while True:
            if time.monotonic() >= self.deadline:
                raise ObservationRejected("resolution", "time-limit")
            parts, done = path[1:].split("/"), []
            fd = os.dup(self.fd)
            try:
                for index, part in enumerate(parts):
                    parent_info = os.fstat(fd)
                    try:
                        info = os.stat(part, dir_fd=fd, follow_symlinks=False)
                    except FileNotFoundError:
                        # Absence requires a stable anchored parent and two
                        # no-follow misses; a dangling link is never optional.
                        if links:
                            raise ObservationRejected("stat", "dangling-link") from None
                        try:
                            os.stat(part, dir_fd=fd, follow_symlinks=False)
                        except FileNotFoundError:
                            if self.overlay.identity(parent_info) == self.overlay.identity(os.fstat(fd)):
                                reason = "confirmed-absent" if index == len(parts) - 1 else "ancestor-absent"
                                raise ObservationRejected("stat", reason) from None
                        except OSError:
                            pass
                        raise ObservationRejected("stat", "absence-unconfirmed") from None
                    except OSError:
                        raise ObservationRejected("stat", "stat-failed") from None
                    target_info = info if index == len(parts) - 1 else None
                    if stat.S_ISLNK(info.st_mode):
                        if len(links) >= LIMITS["links"]:
                            raise ObservationRejected("resolution", "link-limit", target_info)
                        try:
                            value = os.readlink(part, dir_fd=fd)
                            after = os.stat(part, dir_fd=fd, follow_symlinks=False)
                        except OSError:
                            raise ObservationRejected("resolution", "link-read-failed", target_info) from None
                        if self.overlay.identity(info) != self.overlay.identity(after):
                            raise ObservationRejected("identity", "identity-changed", target_info)
                        target = posixpath.normpath(posixpath.join("/", *done, value))
                        if not public_path(target):
                            raise ObservationRejected("resolution", "unsafe-target", target_info)
                        links.append({"path": "/" + "/".join(done + [part]), "target": target})
                        path = posixpath.join(target, *parts[index + 1:])
                        if not public_path(path):
                            raise ObservationRejected("resolution", "unsafe-target", target_info)
                        break
                    done.append(part)
                    if index == len(parts) - 1:
                        return path, links, info
                    if not stat.S_ISDIR(info.st_mode):
                        raise ObservationRejected("resolution", "ancestor-not-directory")
                    try:
                        child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    except OSError:
                        raise ObservationRejected("resolution", "directory-open-failed") from None
                    try:
                        unchanged = self.overlay.identity(info) == self.overlay.identity(os.fstat(child))
                    except OSError:
                        os.close(child)
                        raise ObservationRejected("resolution", "directory-open-failed") from None
                    if not unchanged:
                        os.close(child)
                        raise ObservationRejected("identity", "identity-changed")
                    os.close(fd)
                    fd = child
                else:
                    raise ObservationRejected("resolution", "unsafe-path")
            finally:
                os.close(fd)

    def directory(self, resolved):
        # Tree.parent anchors every ancestor with O_NOFOLLOW.
        tree = self.overlay.Tree(self.fd)
        with tree.parent(resolved[1:]) as (fd, leaf):
            return os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)

    def file(self, name, path):
        row = file_report(name, path if public_path(path) else None)
        try:
            resolved, links, info = self.resolve(path)
            row["observed"] = observed_stat(info)
            if not stat.S_ISREG(info.st_mode):
                raise ObservationRejected("type", "not-regular", info)
            if info.st_nlink != 1:
                raise ObservationRejected("link-count", "link-count", info)
            if not 0 <= info.st_size <= LIMITS["file_bytes"]:
                raise ObservationRejected("size-budget", "file-size-limit", info)
            if self.spent + info.st_size > LIMITS["hashed_bytes"]:
                raise ObservationRejected("size-budget", "total-size-limit", info)
            self.spent += info.st_size
            tree = self.overlay.Tree(self.fd)
            tree.deadline = self.deadline
            row.update(stage="read", reason="read-rejected")
            try:
                item = tree.read(resolved[1:], retain=False, max_bytes=min(LIMITS["file_bytes"], info.st_size))
            except OSError:
                raise ObservationRejected("read", "read-failed", info) from None
            row.update(stage="identity", reason="identity-recheck-failed")
            try:
                final = self.resolve(path)[2]
            except ERRORS:
                raise ObservationRejected("identity", "identity-recheck-failed", info) from None
            if item["bytes"] != info.st_size or self.overlay.identity(info) != self.overlay.identity(final):
                raise ObservationRejected("identity", "identity-changed", info)
            row.update(resolved=resolved, links=links, status="read", sha256=item["sha256"],
                       bytes=item["bytes"], mode=item["mode"], stage="complete", reason=None)
        except ObservationRejected as error:
            row.update(stage=error.stage, reason=error.reason, observed=error.observed,
                       status="absent" if error.reason == "confirmed-absent" else "rejected")
        except ERRORS:
            pass
        return row


def scan_report(name, path):
    return {"name": name, "path": path, "resolved": None, "status": "rejected", "reason": "metadata-rejected",
            "entries": 0, "regular_bytes": 0, "types": dict(regular=0, directory=0, symlink=0, special=0),
            "multiply_linked": 0, "mode": None, "bytecode": dict(total=0, with_source=0, without_source=0, ambiguous=0),
            "startup_hooks": dict(pth=0, egg_link=0, editable=0, sitecustomize=0, usercustomize=0),
            "top_level_imports": 0, "top_level_imports_sha256": None, "elapsed_ms": 0}


def scan(fs, diagnostic, name, path):
    """Name-counterpart accounting only; no bytecode/native/package imports."""
    row, imports, sources, caches = scan_report(name, path), set(), set(), set()
    started = time.monotonic()
    try:
        resolved, _, info = fs.resolve(path)
        fs.overlay.require(stat.S_ISDIR(info.st_mode))
        row.update(resolved=resolved, mode=stat.S_IMODE(info.st_mode))
        fd = fs.directory(resolved)
        try:
            def observe(relative, kind, entry):
                fs.overlay.require(row["entries"] < LIMITS["scan_entries"] and time.monotonic() < fs.deadline)
                row["entries"] += 1
                row["types"][kind if kind in ("regular", "directory", "symlink") else "special"] += 1
                if kind == "regular":
                    row["regular_bytes"] += entry.st_size
                    row["multiply_linked"] += int(entry.st_nlink > 1)
                    if relative.endswith(".py"):
                        sources.add(relative)
                    elif relative.endswith(".pyc"):
                        caches.add(relative)
                if "/" not in relative:
                    module = relative if kind == "directory" else relative.split(".", 1)[0]
                    if kind == "directory" or relative.endswith((".py", ".pyc", ".so")):
                        if module != "__pycache__" and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", module):
                            imports.add(module)
                if "/" not in relative or relative.startswith("__pycache__/") and relative.count("/") == 1:
                    leaf = relative.rsplit("/", 1)[-1]
                    for key, match in (("pth", leaf.endswith(".pth")), ("egg_link", leaf.endswith(".egg-link")),
                                       ("editable", "__editable__" in leaf),
                                       ("sitecustomize", leaf.split(".", 1)[0] == "sitecustomize"),
                                       ("usercustomize", leaf.split(".", 1)[0] == "usercustomize")):
                        row["startup_hooks"][key] += int(match)
            tree = fs.overlay.Tree(fd)
            tree.deadline = fs.deadline
            census = diagnostic.census_report()
            diagnostic.metadata_census(fs.overlay, tree, census, observe=observe)
            row.update(status="pass" if census["collection"] == "pass" else "partial", reason=census["reason"])
        finally:
            os.close(fd)
        counts = row["bytecode"]
        counts["total"] = len(caches)
        for cache in caches:
            p = PurePosixPath(cache)
            possible = {cache[:-1]}
            if p.parent.name == "__pycache__":
                possible |= {str(p.parent.parent / (p.name[:i] + ".py")) for i, c in enumerate(p.name) if c == "."}
            found = possible & sources
            counts["with_source" if len(found) == 1 else "ambiguous" if found else "without_source"] += 1
        row.update(top_level_imports=len(imports), top_level_imports_sha256=fs.overlay.digest(fs.overlay.json_bytes(sorted(imports))))
    except ObservationRejected as error:
        row.update(status="absent" if error.reason == "confirmed-absent" else "rejected", reason=error.reason)
    except ERRORS:
        row.update(status="partial", reason="metadata-rejected")
    row["elapsed_ms"] = min(LIMITS["seconds"] * 1000, max(0, int((time.monotonic() - started) * 1000)))
    return row, imports


def runtime_facts():
    complete = True
    build = {}
    for key in BUILD_KEYS:
        try:
            value = sysconfig.get_config_var(key)
        except ERRORS:
            value = None
        valid = type(value) is int and value in (0, 1) if key.startswith("Py_") else (
            type(value) is str and re.fullmatch(r"[A-Za-z0-9_.+-]{1,128}", value) is not None)
        build[key] = value if valid else None
        complete = complete and valid
    try:
        paths = sysconfig.get_paths()
        dynload = sysconfig.get_config_var("DESTSHARED")
    except ERRORS:
        paths, dynload, complete = {}, None, False
    locations = [location(name, value, optional=name == "pycache_prefix") for name, value in (
        ("executable", sys.executable), ("base_executable", getattr(sys, "_base_executable", None)),
        ("prefix", sys.prefix), ("base_prefix", sys.base_prefix), ("exec_prefix", sys.exec_prefix),
        ("base_exec_prefix", sys.base_exec_prefix), *((key, paths.get(key)) for key in PATH_KEYS),
        ("dynload", dynload), ("pycache_prefix", sys.pycache_prefix))]
    complete = complete and len(sys.path) <= 12
    locations.extend(location("sys_path_" + str(i), path) for i, path in enumerate(sys.path[:12]))
    try:
        roots = site.getsitepackages()
    except ERRORS:
        roots, complete = [], False
    complete = complete and len(roots) <= 4
    locations.extend(location("site_root_" + str(i), path) for i, path in enumerate(roots[:4]))
    suffixes = list(importlib.machinery.EXTENSION_SUFFIXES)
    flags = {name: bool(getattr(sys.flags, name)) for name in ("isolated", "no_site", "ignore_environment", "no_user_site", "safe_path")}
    flags["dont_write_bytecode"] = sys.dont_write_bytecode
    try:
        inherited = subprocess._args_from_interpreter_flags()
    except ERRORS:
        inherited, complete = [], False
    clean = (all(type(value) is str and re.fullmatch(r"\.[A-Za-z0-9_.-]{1,100}", value) for value in suffixes)
             and 1 <= len(suffixes) <= 8 and all(value in ("-B", "-S", "-I") for value in inherited))
    version, tag = platform.python_version(), sys.implementation.cache_tag
    complete = complete and re.fullmatch(r"3\.12\.[0-9]{1,3}", version) is not None and tag == "cpython-312"
    return {"status": "observed" if complete and clean else "rejected", "locations": locations, "build": build,
            "python": version if re.fullmatch(r"3\.12\.[0-9]{1,3}", version) else None, "cache_tag": tag if tag == "cpython-312" else None,
            "flags": flags, "extension_suffixes": suffixes if clean else [],
            "multiprocessing_flags": inherited if clean else []}


def packaging_report():
    return {"status": "unavailable", "inventory": "unavailable", "inventory_sha256": None,
            "entrypoints_match": None, "entrypoints": None, "removal_plan_sha256": None,
            "removal_count": None, "removal_bytes": None, "legacy_bytecode": None,
            "without_source": None, "ambiguous": None, "classified_nonhost_assets": [],
            "reason": "prerequisite"}


def packaging_observations(fs, contract, paths):
    result = packaging_report()
    try:
        selected, _, _ = fs.resolve(paths["purelib"])
        scripts, _, _ = fs.resolve(paths["scripts"])
        fd, scripts_fd = fs.directory(selected), None
        try:
            scripts_fd = fs.directory(scripts)
            tree, console_tree = fs.overlay.Tree(fd), fs.overlay.Tree(scripts_fd)
            tree.deadline = console_tree.deadline = fs.deadline
            classified = result["classified_nonhost_assets"]
            inventory = tree.inventory(diagnostic_policy=contract["diagnostic_inventory_policy"],
                                       nonhost_policy=contract["diagnostic_nonhost_policy"], classified=classified)
            result.update(inventory="pass", inventory_sha256=fs.overlay.digest(fs.overlay.json_bytes(inventory)))
            distributions = [name.rsplit("/", 1)[0] for name in inventory
                             if name.startswith("vllm-") and name.endswith(".dist-info/METADATA") and name.count("/") == 1]
            fs.overlay.require(len(distributions) == 1)
            dist = distributions[0]
            result["entrypoints_match"] = fs.overlay.packaging_entrypoints_match(tree.read(dist + "/entry_points.txt"))
            if result["entrypoints_match"]:
                result["entrypoints"] = fs.overlay.PACKAGING_POLICY["entrypoints"]
            result.update(legacy_bytecode=0, without_source=0, ambiguous=0)
            for name in inventory:
                if not name.startswith("vllm/") or not name.endswith(".pyc"):
                    continue
                p = PurePosixPath(name)
                result["legacy_bytecode"] += int(p.parent.name != "__pycache__")
                possible = {name[:-1]}
                if p.parent.name == "__pycache__":
                    possible |= {str(p.parent.parent / (p.name[:i] + ".py")) for i, c in enumerate(p.name) if c == "."}
                found = possible & inventory.keys()
                result["without_source"] += int(not found)
                result["ambiguous"] += int(len(found) > 1)
            before = fs.overlay.inspect_integrity(tree, contract, console=(posixpath.relpath(scripts + "/vllm", selected), console_tree),
                                                  inventory=inventory, packaging=True)
            plan = before["packaging_removals"]
            result.update(status="pass", removal_plan_sha256=fs.overlay.digest(fs.overlay.json_bytes(plan)),
                          removal_count=len(plan), removal_bytes=sum(row["bytes"] for row in plan), reason=None)
        finally:
            os.close(fd)
            if scripts_fd is not None:
                os.close(scripts_fd)
    except fs.overlay.InventoryRejected as failure:
        result.update(status="rejected", inventory="rejected", reason=failure.reason)
    except (*ERRORS, csv.Error, SyntaxError):
        result.update(status="rejected", reason="packaging-rejected")
    return result


def canary_report():
    return {"status": "not-run", "reason": "prerequisite", "path_sha256": None, "blockers": [],
            "available": {"processes": False, "payload": False},
            "attempts": {key: {"status": "not-run", "reason": "prerequisite"} for key in ("fresh", "children")},
            "fresh": None, "fork": None, "spawn": None, "grandchild": None, "forkserver": None}


def proposed_paths(facts):
    paths = {row["name"]: row["path"] for row in facts["locations"]}
    proposed = [paths.get("stdlib"), paths.get("dynload"),
                paths["purelib"] + "/nvidia_cutlass_dsl/dsl_packages" if paths.get("purelib") else None, paths.get("purelib")]
    return list(dict.fromkeys(proposed)) if all(public_path(p) for p in proposed) else []


def scan_acceptable(row, scans, facts):
    if row["status"] == "pass":
        return True
    locations = {r["name"]: r for r in facts["locations"]}
    # A site-root index is not an allowlist. Only a confirmed missing root
    # outside every required path/alias may be irrelevant to the proposed path.
    if not (row["name"].startswith("site_root_") and row["name"] in locations
            and locations[row["name"]]["status"] == "observed" and row["path"] == locations[row["name"]]["path"]
            and row["status"] == "absent" and row["reason"] == "confirmed-absent" and public_path(row["path"])):
        return False
    protected = proposed_paths(facts) + [r["resolved"] for r in scans if r["status"] == "pass" and r["resolved"]]
    return bool(proposed_paths(facts)) and not any(p == row["path"] or p.startswith(row["path"] + "/") for p in protected)


def prerequisite_blockers(facts, scans, executable, available):
    blockers = []
    if facts["status"] != "observed":
        blockers.append("runtime-unobserved")
    if not all(value is True for value in facts["flags"].values()):
        blockers.append("runtime-flags")
    paths = {row["name"]: row["path"] for row in facts["locations"]}
    blockers += ["location:" + row["name"] for row in facts["locations"] if row["status"] == "rejected"]
    if paths.get("pycache_prefix") is not None:
        blockers.append("pycache-prefix")
    if not proposed_paths(facts):
        blockers.append("proposed-paths")
    if paths.get("purelib") != paths.get("platlib"):
        blockers.append("split-library-roots")
    blockers += ["scan:" + row["name"] for row in scans if not scan_acceptable(row, scans, facts)]
    if executable["status"] != "read":
        blockers.append("executable-unverified")
    blockers += ["unavailable:" + key for key, value in available.items() if not value]
    return sorted(set(blockers))


def canaries(overlay, facts, scans, executable, processes, payload):
    result = canary_report()
    result["available"] = {"processes": processes is not None, "payload": payload is not None}
    result["blockers"] = prerequisite_blockers(facts, scans, executable, result["available"])
    if result["blockers"]:
        return result
    proposed = proposed_paths(facts)
    result["path_sha256"] = overlay.digest(overlay.json_bytes(proposed))
    layout = {"paths": proposed, "executable": executable["resolved"], "python": facts["python"], "payload": str(payload)}
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent", "TMPDIR": "/tmp",
           "NIB_PYTHON_CANARY": overlay.json_bytes(layout).decode()}
    for mode in ("fresh", "children"):
        attempt = result["attempts"][mode]
        attempt.update(status="failed", reason="execution-rejected")
        try:
            process = processes.execute_process_group([executable["resolved"], "-I", "-S", "-B", str(payload / "python-canary.py"), mode],
                env=env, cwd="/", timeout=45, max_output_bytes=8192)
            attempt["reason"] = "nonzero-exit"
            overlay.require(process.returncode == 0)
            attempt["reason"] = "output-rejected"
            overlay.require(not process.stderr)
            output = overlay.load_json(process.stdout)
            if mode == "fresh":
                values = {"fresh": output}
            else:
                overlay.require(type(output) is dict and set(output) == {"fork", "spawn", "forkserver"})
                values = {**output, "grandchild": output["spawn"].pop("grandchild")}
            for value in values.values():
                validate_canary(overlay, value, result["path_sha256"])
            result.update(values)
            attempt.update(status="pass", reason=None)
        except subprocess.TimeoutExpired:
            attempt["reason"] = "time-limit"
        except OSError:
            attempt["reason"] = "launch-or-io-failed"
        except (*ERRORS, subprocess.SubprocessError):
            pass
    passed = all(row["status"] == "pass" for row in result["attempts"].values())
    result.update(status="pass" if passed else "failed", reason=None if passed else "attempt-failed")
    return result


def collect(overlay, diagnostic, contract, *, filesystem_root="/", processes=None, payload=None):
    overlay.require(contract["diagnostic_mode"] == "python-descriptor" and contract["base_image"] == overlay.BASE_IMAGE
                    and contract["official_config"]["sha256"] == overlay.PACKAGING_POLICY["config_sha256"]
                    and platform.system() == "Linux" and platform.machine() == "x86_64" and sys.version_info[:2] == (3, 12))
    facts = runtime_facts()
    report = {"schema": "nib.engine-python-descriptor/v2", "collection": "partial", "limits": dict(LIMITS),
              "candidate_status": "BLOCKED", "deployable": False, "abi_validated": False, "hardware_validated": False,
              "overlay_admission": "not-evaluated", "runtime": facts, "files": [], "scans": [], "collisions": [],
              "worker_sources": [], "packaging": packaging_report(), "canaries": canary_report(), "proposed_paths": [],
              "preparation": contract["descriptor_preparation"],
              "scope": "installed-file-hashes-and-name-counterparts-not-loaded-code-or-launcher-validation"}
    paths = {item["name"]: item["path"] for item in facts["locations"]}
    def join(key, suffix):
        return paths[key] + "/" + suffix if paths.get(key) else None
    with overlay.directory(filesystem_root) as fd:
        fs = Filesystem(overlay, fd)
        files = [(key, paths.get(key)) for key in ("executable", "base_executable")]
        files += [(name, join("stdlib", name)) for name in SOURCES]
        files += [(name, join("purelib", name)) for name in diagnostic.HOOKS]
        files.append(("cutlass-init", join("purelib", "nvidia_cutlass_dsl/__init__.py")))
        report["files"] = [fs.file(name, path) for name, path in files]
        pins = contract["descriptor_source_pins"]
        overlay.require(pins["revision"] == overlay.BASE_REVISION)
        for name, expected in sorted(pins["files"].items()):
            row = fs.file(name, join("purelib", name))
            report["worker_sources"].append({**row, "expected_sha256": expected,
                                              "matches": row["status"] == "read" and row["sha256"] == expected})
        roots = [("stdlib", paths.get("stdlib")), ("selected", paths.get("purelib")),
                 ("cutlass", join("purelib", "nvidia_cutlass_dsl/dsl_packages")), ("dynload", paths.get("dynload"))]
        roots += [(name, value) for name, value in paths.items() if name.startswith("site_root_")]
        report["proposed_paths"] = proposed_paths(facts)
        names = {}
        for label, path in roots:
            row, imports = scan(fs, diagnostic, label, path)
            report["scans"].append(row)
            names[label] = imports
        for left, right in (("stdlib", "selected"), ("stdlib", "cutlass"), ("selected", "cutlass")):
            common = sorted(names[left] & names[right])
            report["collisions"].append({"roots": [left, right], "count": len(common), "sample": common[:16],
                                         "names_sha256": overlay.digest(overlay.json_bytes(common))})
        report["packaging"] = packaging_observations(fs, contract, paths)
    report["canaries"] = canaries(overlay, facts, report["scans"], report["files"][0], processes, payload)
    required_files = [row for row in report["files"] if row["name"] != "cutlass-init"]
    complete = (facts["status"] == "observed" and all(row["status"] != "rejected" for row in facts["locations"])
                and all(row["status"] == "read" for row in required_files)
                and report["files"][-1]["status"] in ("read", "absent")
                and all(scan_acceptable(row, report["scans"], facts) for row in report["scans"])
                and all(row["status"] == "read" for row in report["worker_sources"])
                and report["packaging"]["status"] == "pass" and report["canaries"]["status"] == "pass")
    report["collection"] = "pass" if complete else "partial"
    validate_report(overlay, report, contract)
    return report


def validate_canary(overlay, value, expected):
    overlay.require(type(value) is dict and set(value) == {"flags", "path_sha256", "before_unpickle", "no_engine_modules"}
                    and type(value["flags"]) is dict and set(value["flags"]) == {"isolated", "no_site", "dont_write_bytecode", "ignore_environment"}
                    and all(v is True for v in value["flags"].values()) and value["path_sha256"] == expected
                    and value["before_unpickle"] is True and value["no_engine_modules"] is True)


def validate_report(overlay, report, contract):
    require = overlay.require
    require(contract.get("diagnostic_mode") == "python-descriptor" and contract["base_image"] == overlay.BASE_IMAGE
            and contract["official_config"]["sha256"] == overlay.PACKAGING_POLICY["config_sha256"])
    require(set(report) == {"schema", "collection", "limits", "candidate_status", "deployable", "abi_validated",
        "hardware_validated", "overlay_admission", "runtime", "files", "scans", "collisions", "scope",
        "worker_sources", "packaging", "canaries", "preparation", "proposed_paths"})
    preparation = report["preparation"]
    require(preparation == contract["descriptor_preparation"] and set(preparation) == {"stages", "candidate_bundle_sha256", "context_sha256", "image_writes"}
            and preparation["stages"] == 5 and preparation["image_writes"] is False
            and all(re.fullmatch(SHA, preparation[key]) for key in ("candidate_bundle_sha256", "context_sha256")))
    require(report["schema"] == "nib.engine-python-descriptor/v2" and report["collection"] in ("pass", "partial")
        and report["limits"] == LIMITS and report["candidate_status"] == "BLOCKED" and report["deployable"] is False
        and report["abi_validated"] is False and report["hardware_validated"] is False and report["overlay_admission"] == "not-evaluated"
        and report["scope"] == "installed-file-hashes-and-name-counterparts-not-loaded-code-or-launcher-validation")
    facts = report["runtime"]
    require(set(facts) == {"status", "locations", "python", "cache_tag", "flags", "extension_suffixes", "multiprocessing_flags", "build"})
    require(set(facts["build"]) == set(BUILD_KEYS))
    for key, value in facts["build"].items():
        require(value is None or (type(value) is int and value in (0, 1) if key.startswith("Py_") else
                type(value) is str and re.fullmatch(r"[A-Za-z0-9_.+-]{1,128}", value)))
    require(facts["status"] != "observed" or all(value is not None for value in facts["build"].values()))
    require(facts["status"] in ("observed", "rejected") and (facts["python"] is None or re.fullmatch(r"3\.12\.[0-9]{1,3}", facts["python"]))
            and facts["cache_tag"] in (None, "cpython-312"))
    require(set(facts["flags"]) == {"isolated", "no_site", "ignore_environment", "no_user_site", "safe_path", "dont_write_bytecode"}
            and all(type(value) is bool for value in facts["flags"].values()))
    require(type(facts["extension_suffixes"]) is list and len(facts["extension_suffixes"]) <= 8
            and all(type(value) is str and re.fullmatch(r"\.[A-Za-z0-9_.-]{1,100}", value) for value in facts["extension_suffixes"]))
    require(type(facts["multiprocessing_flags"]) is list and len(facts["multiprocessing_flags"]) <= 3
            and set(facts["multiprocessing_flags"]) <= {"-B", "-S", "-I"})
    locations = facts["locations"]
    require(type(locations) is list and len(FIXED_LOCATIONS) <= len(locations) <= LIMITS["locations"])
    seen = set()
    for i, row in enumerate(locations):
        require(set(row) == {"name", "path", "status"} and row["name"] not in seen)
        seen.add(row["name"])
        require(row["name"] == FIXED_LOCATIONS[i] if i < len(FIXED_LOCATIONS)
                else re.fullmatch(r"sys_path_(?:[0-9]|1[01])|site_root_[0-3]", row["name"]))
        require(row["status"] in ("observed", "unset", "rejected"))
        require(public_path(row["path"]) if row["status"] == "observed" else row["path"] is None)
        require(row["status"] != "unset" or row["name"] == "pycache_prefix")
    paths = {row["name"]: row["path"] for row in locations}
    proposed = [paths["stdlib"], paths["dynload"], paths["purelib"] + "/nvidia_cutlass_dsl/dsl_packages" if paths["purelib"] else None, paths["purelib"]]
    require(report["proposed_paths"] == (list(dict.fromkeys(proposed)) if all(public_path(p) for p in proposed) else []))
    require(type(report["files"]) is list and [row["name"] for row in report["files"]] == list(FILE_NAMES))
    require(type(report["worker_sources"]) is list and [row["name"] for row in report["worker_sources"]] == sorted(contract["descriptor_source_pins"]["files"]))
    for row in report["worker_sources"]:
        require(set(row) == set(file_report("", None)) | {"expected_sha256", "matches"}
                and row["expected_sha256"] == contract["descriptor_source_pins"]["files"][row["name"]]
                and type(row["matches"]) is bool and row["matches"] == (row["status"] == "read" and row["sha256"] == row["expected_sha256"]))
    for row in report["files"] + report["worker_sources"]:
        require(row["status"] in ("read", "absent", "rejected"))
        if row in report["files"]:
            require(set(row) == set(file_report("", None)))
        observation = row["observed"]
        require(set(observation) == {"type", "mode", "size", "nlink"}
                and observation["type"] in (None, "regular", "directory", "symlink", "fifo", "socket", "block", "character", "other"))
        for key, bound in (("mode", 0o10000), ("size", 1 << 63), ("nlink", 1 << 64)):
            require(observation[key] is None or type(observation[key]) is int and 0 <= observation[key] < bound)
        require(observation["type"] is not None or all(value is None for value in observation.values()))
        require(row["stage"] == "complete" and row["reason"] is None if row["status"] == "read"
                else row["reason"] in FILE_REASON_STAGES and row["stage"] == FILE_REASON_STAGES[row["reason"]])
        require(row["path"] is None or public_path(row["path"]))
        key = "stdlib" if row["name"] in SOURCES else "purelib"
        suffix = "nvidia_cutlass_dsl/__init__.py" if row["name"] == "cutlass-init" else row["name"]
        expected_path = paths.get(row["name"]) if row["name"] in ("executable", "base_executable") else (
            paths[key] + "/" + suffix if paths[key] else None)
        require(row["path"] == expected_path)
        require(type(row["links"]) is list and len(row["links"]) <= LIMITS["links"])
        for link in row["links"]:
            require(set(link) == {"path", "target"} and all(public_path(value) for value in link.values()))
        if row["status"] == "read":
            require(public_path(row["resolved"]) and re.fullmatch(SHA, row["sha256"])
                    and type(row["bytes"]) is int and 0 <= row["bytes"] <= LIMITS["file_bytes"]
                    and type(row["mode"]) is int and 0 <= row["mode"] <= 0o7777)
            require(observation == dict(type="regular", mode=row["mode"], size=row["bytes"], nlink=1))
        else:
            require(not row["links"] and all(row[key] is None for key in ("resolved", "sha256", "bytes", "mode")))
            require((row["status"] == "absent") == (row["reason"] == "confirmed-absent"))
            require(row["status"] != "absent" or observation == observed_stat())
            if row["reason"] == "not-regular":
                require(observation["type"] not in (None, "regular"))
            if row["reason"] == "link-count":
                require(observation["type"] == "regular" and observation["nlink"] != 1)
    require(sum(row["bytes"] or 0 for row in report["files"] + report["worker_sources"]) <= LIMITS["hashed_bytes"])
    require(type(report["scans"]) is list and [row["name"] for row in report["scans"]] == list(SCAN_NAMES) + [row["name"] for row in locations if row["name"].startswith("site_root_")])
    for row in report["scans"]:
        require(set(row) == set(scan_report("", None)) and row["status"] in ("pass", "partial", "absent", "rejected"))
        require(row["path"] is None or public_path(row["path"]))
        expected_path = (paths["purelib"] + "/nvidia_cutlass_dsl/dsl_packages" if paths["purelib"] else None) if row["name"] == "cutlass" else paths.get(
            "purelib" if row["name"] == "selected" else row["name"])
        require(row["path"] == expected_path)
        require(row["resolved"] is None or public_path(row["resolved"]))
        require(row["reason"] in {None, "metadata-rejected", "time-limit", "entry-limit", "depth-limit", "unsafe-name",
                                   "size-limit", "directory-changed", "device-boundary"} | FILE_REASON_STAGES.keys())
        require((row["status"] == "absent") == (row["reason"] == "confirmed-absent"))
        require(set(row["types"]) == {"regular", "directory", "symlink", "special"}
                and set(row["bytecode"]) == {"total", "with_source", "without_source", "ambiguous"}
                and set(row["startup_hooks"]) == {"pth", "egg_link", "editable", "sitecustomize", "usercustomize"})
        for value in (row["entries"], row["multiply_linked"], row["top_level_imports"], *row["types"].values(), *row["bytecode"].values(), *row["startup_hooks"].values()):
            require(type(value) is int and 0 <= value <= LIMITS["scan_entries"])
        require(sum(row["types"].values()) == row["entries"] and row["bytecode"]["total"] == sum(
            row["bytecode"][key] for key in ("with_source", "without_source", "ambiguous")))
        require(type(row["regular_bytes"]) is int and 0 <= row["regular_bytes"] <= 1 << 60
                and type(row["elapsed_ms"]) is int and 0 <= row["elapsed_ms"] <= LIMITS["seconds"] * 1000)
        require(row["mode"] is None or type(row["mode"]) is int and 0 <= row["mode"] <= 0o7777)
        require(row["top_level_imports_sha256"] is None or re.fullmatch(SHA, row["top_level_imports_sha256"]))
        if row["status"] == "absent":
            require(row == {**scan_report(row["name"], row["path"]), "status": "absent",
                            "reason": "confirmed-absent", "elapsed_ms": row["elapsed_ms"]})
        if row["status"] == "pass":
            require(row["reason"] is None and row["mode"] is not None and row["resolved"] is not None
                    and row["top_level_imports_sha256"] is not None)
    require(type(report["collisions"]) is list and len(report["collisions"]) == 3)
    for row, pair in zip(report["collisions"], (("stdlib", "selected"), ("stdlib", "cutlass"), ("selected", "cutlass")), strict=True):
        require(set(row) == {"roots", "count", "sample", "names_sha256"} and row["roots"] == list(pair)
                and type(row["count"]) is int and 0 <= row["count"] <= LIMITS["scan_entries"]
                and type(row["sample"]) is list and len(row["sample"]) == min(16, row["count"])
                and all(type(value) is str and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", value) for value in row["sample"])
                and row["sample"] == sorted(set(row["sample"])) and re.fullmatch(SHA, row["names_sha256"]))
    packaging = report["packaging"]
    require(set(packaging) == set(packaging_report()) and packaging["status"] in ("pass", "unavailable", "rejected")
            and packaging["inventory"] in ("pass", "unavailable", "rejected")
            and packaging["reason"] in {None, "prerequisite", "packaging-rejected"} | overlay.INVENTORY_REASONS)
    for key in ("inventory_sha256", "removal_plan_sha256"):
        require(packaging[key] is None or re.fullmatch(SHA, packaging[key]))
    for key in ("removal_count", "legacy_bytecode", "without_source", "ambiguous"):
        require(packaging[key] is None or type(packaging[key]) is int and 0 <= packaging[key] <= overlay.MAX_FILES)
    require(packaging["removal_bytes"] is None or type(packaging["removal_bytes"]) is int and 0 <= packaging["removal_bytes"] <= overlay.MAX_BYTES)
    require(packaging["entrypoints_match"] is None or type(packaging["entrypoints_match"]) is bool)
    require(packaging["entrypoints"] == overlay.PACKAGING_POLICY["entrypoints"] if packaging["entrypoints_match"] is True else packaging["entrypoints"] is None)
    assets = packaging["classified_nonhost_assets"]
    require(type(assets) is list and len(assets) <= 4)
    names = []
    for asset in assets:
        require(asset == {"path": asset["path"], "sha256": overlay.CUTLASS_STUB_POLICY["sha256"], "bytes": overlay.CUTLASS_STUB_POLICY["bytes"],
                          "classification": "published-cutlass-aot-crosscompile-stub"} and asset["path"] in overlay.CUTLASS_STUB_POLICY["paths"])
        names.append(asset["path"])
    require(names == sorted(set(names)))
    if packaging["status"] == "pass":
        require(packaging["inventory"] == "pass" and packaging["reason"] is None and packaging["entrypoints_match"] is True
                and all(packaging[key] is not None for key in ("inventory_sha256", "removal_plan_sha256", "removal_count", "removal_bytes",
                                                            "legacy_bytecode", "without_source", "ambiguous"))
                and names == sorted(overlay.CUTLASS_STUB_POLICY["paths"]))
    children = report["canaries"]
    require(set(children) == set(canary_report()) and children["status"] in ("pass", "not-run", "failed")
            and set(children["available"]) == {"processes", "payload"}
            and all(type(value) is bool for value in children["available"].values()))
    blockers = prerequisite_blockers(facts, report["scans"], report["files"][0], children["available"])
    require(children["blockers"] == blockers and len(blockers) <= 64 and set(children["attempts"]) == {"fresh", "children"})
    if blockers:
        require(children["status"] == "not-run" and children["reason"] == "prerequisite" and children["path_sha256"] is None)
        require(all(row == {"status": "not-run", "reason": "prerequisite"} for row in children["attempts"].values()))
        require(all(children[key] is None for key in ("fresh", "fork", "spawn", "grandchild", "forkserver")))
    else:
        require(report["proposed_paths"] and children["path_sha256"] == overlay.digest(overlay.json_bytes(report["proposed_paths"])))
        for mode, keys in (("fresh", ("fresh",)), ("children", ("fork", "spawn", "grandchild", "forkserver"))):
            attempt = children["attempts"][mode]
            require(set(attempt) == {"status", "reason"} and attempt["status"] in ("pass", "failed"))
            if attempt["status"] == "pass":
                require(attempt["reason"] is None)
                for key in keys:
                    validate_canary(overlay, children[key], children["path_sha256"])
            else:
                require(attempt["reason"] in {"execution-rejected", "nonzero-exit", "output-rejected", "time-limit", "launch-or-io-failed"}
                        and all(children[key] is None for key in keys))
        passed = all(row["status"] == "pass" for row in children["attempts"].values())
        require(children["status"] == ("pass" if passed else "failed") and children["reason"] == (None if passed else "attempt-failed"))
    if report["collection"] == "pass":
        require(facts["status"] == "observed" and all(row["status"] != "rejected" for row in locations)
                and facts["python"] is not None and facts["cache_tag"] == "cpython-312"
                and facts["extension_suffixes"] and set(facts["multiprocessing_flags"]) == {"-B", "-S", "-I"}
                and all(row["status"] == "read" for row in report["files"][:-1])
                and report["files"][-1]["status"] in ("read", "absent")
                and all(scan_acceptable(row, report["scans"], facts) for row in report["scans"]))
        require(all(row["status"] == "read" for row in report["worker_sources"])
                and packaging["status"] == "pass" and children["status"] == "pass")
    require(len(overlay.json_bytes(report)) <= LIMITS["report_bytes"])
