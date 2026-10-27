#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Audit a restricted static ELF graph inside exact locked Ubuntu package data."""
import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B scripts/prepare_network_runtime.py")

import argparse
import json
import lzma
from pathlib import Path
import re
import runpy
import subprocess
import tarfile
import tempfile
import time
import types

ROOT = Path(__file__).absolute().parent.parent
SCRIPT = "scripts/prepare_network_runtime.py"
LOCK = "image/inputs/guest-runtime-ubuntu2404.json"
TOOLS = "scripts/prepare_guest_tools.py"
tools = types.SimpleNamespace(**runpy.run_path(str(ROOT / TOOLS)))
base, require = tools.base, tools.require
GO_SOURCES = ("go.mod", "cmd/nib-elf-metadata/main.go", "internal/elfmeta/metadata.go")
EXTRA = (SCRIPT, LOCK, *GO_SOURCES)
ROOTS = {
    "usr/lib/systemd/systemd-networkd": (1669864, "e4ab77a36ee958f052b0100b60583716904a393159227e1e895469199e1fd8f9"),
    "usr/sbin/nft": (26856, "3f1c21553e62716ef1abfcf31f51ff94eb73ff4234a6653bac754a42f936d1c7"),
}
INTERPRETER = "lib64/ld-linux-x86-64.so.2"
DEFAULT_DIRS = ("usr/lib/x86_64-linux-gnu", "lib/x86_64-linux-gnu", "usr/lib", "lib")
RUNPATH = "/usr/lib/x86_64-linux-gnu/systemd"
MAX_OBJECTS = 128
MAX_ARCHIVES = 64 << 20
CLAIMS = dict.fromkeys(("runtime_linkage_validated", "symbol_versions_verified", "dlopen_nss_verified",
                       "package_binaries_executed", "maintainer_scripts_executed", "image_modified",
                       "kernel_network_verified", "hardware_validated",
                       "confidential", "deployable", "network_activated"), False)


class Blocked(ValueError):
    def __init__(self, category, **detail):
        super().__init__("network-static-closure-blocked")
        self.detail = {"category": category, **detail}


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def selection(lock, names):
    require(type(names) is list and 2 <= len(names) <= 32 and len(set(names)) == len(names)
            and {"systemd", "nftables"} <= set(names)
            and all(type(n) is str and re.fullmatch(r"[a-z0-9][a-z0-9+.-]{0,127}", n) for n in names))
    rows = [r for r in lock["packages"] if r["Package"] in names]
    require(len(rows) == len(names) and {r["Package"] for r in rows} == set(names)
            and all(r["Architecture"] == "amd64" and r["index"] != "nvidia"
                    and re.fullmatch("[0-9a-f]{64}", r["SHA256"]) for r in rows)
            and 0 < sum(int(r["Size"]) for r in rows) <= MAX_ARCHIVES)
    return sorted(rows, key=lambda r: r["Package"])


def metadata_check(lock, rows, directory):
    # Extraction-only subset, not an installed APT system or new signature check.
    expected = {(r["index"], r["Package"], r["Version"], r["Architecture"]): r for r in rows}
    observed = set()
    indexes = [r for r in lock["indexes"] if r["id"] in {p["index"] for p in rows}]
    require(len(indexes) <= 6 and {r["id"] for r in indexes} == {r["index"] for r in rows})
    with base.directory(directory) as fd:
        for index in indexes:
            require(index["encoding"] == "xz"
                    and index["signer_fingerprint"] == "F6ECB3762474EDA9D21B7022871920D1991BC93C")
            raw = base.read_at(fd, index["file"], 32 << 20)
            require(len(raw) == index["bytes"] and base.digest(raw) == index["sha256"])
            for count, row in enumerate(base.inputs.paragraphs(base.inputs.index_bytes(raw, "xz"))):
                require(count < 200000)
                key = index["id"], row["Package"], row["Version"], row["Architecture"]
                if key in expected:
                    require(key not in observed and row == {k: v for k, v in expected[key].items() if k != "index"})
                    observed.add(key)
    require(observed == expected.keys())
    return indexes


def assemble(rows, archives, deadline):
    tree, origins = {}, {}
    with base.directory(archives) as fd:
        for package in rows:
            require(time.monotonic() < deadline)
            raw = base.read_at(fd, package["SHA256"] + ".deb", MAX_ARCHIVES)
            require(len(raw) == int(package["Size"]) and base.digest(raw) == package["SHA256"])
            base.controls(raw, package)
            incoming = tools.payload(raw)
            tools.merge(tree, incoming)
            for path, row in incoming.items():
                if row["kind"] != "dir":
                    actual = tools.resolve(tree, path, ancestors_only=True)
                    require(tree.get(actual) == row)
                    origins.setdefault(actual, []).append({"package": package["Package"],
                        "archive_sha256": package["SHA256"], "archive_member": path})
    tools.complete_tree(tree)
    for path in origins:
        require(tools.resolve(tree, path, ancestors_only=True) == path)
    return tree, origins


def closure(tree, origins, inspect, *, deadline):
    require(not {"etc/ld.so.cache", "etc/ld.so.preload"}.intersection(tree)
            and not any("glibc-hwcaps" in p.split("/") for p in tree))
    queue, nodes, edges, bindings = list(ROOTS), {}, [], {}
    for name, (size, sha) in ROOTS.items():
        row = tree.get(name)
        require(row is not None and row["kind"] == "file" and len(row["data"]) == size
                and base.digest(row["data"]) == sha)

    def target(parent, needed, candidates, kind):
        matches = {}
        for candidate in candidates:
            actual = tools.resolve(tree, candidate)
            row = tree.get(actual)
            if row is not None:
                require(row["kind"] == "file")
                matches.setdefault(actual, []).append(candidate)
        if len(matches) != 1:
            raise Blocked("missing-" + kind if not matches else "ambiguous-" + kind,
                          member=parent, needed=needed, candidates=candidates, matches=sorted(matches))
        actual = next(iter(matches))
        if kind == "library":
            require(bindings.setdefault(needed, actual) == actual)
        edges.append({"from": parent, "kind": kind, "needed": needed,
                      "to": actual, "matched_paths": matches[actual]})
        if actual not in nodes and actual not in queue:
            require(len(queue) + len(nodes) < MAX_OBJECTS)
            queue.append(actual)
        return actual

    while queue:
        require(time.monotonic() < deadline and len(nodes) < MAX_OBJECTS)
        path = queue.pop(0)
        require(path in origins and len(origins[path]) == 1)
        raw = tree[path]["data"]
        require(0 < len(raw) <= 16 << 20)
        meta = inspect(raw)
        require(set(meta) == {"interpreter", "needed", "rpath", "runpath", "soname"}
                and all(type(meta[k]) is list and len(meta[k]) <= 32
                        and all(type(s) is str and 0 < len(s) <= 256 for s in meta[k])
                        for k in ("needed", "rpath", "runpath", "soname"))
                and type(meta["interpreter"]) is str
                and meta["rpath"] == [] and meta["runpath"] in ([], [RUNPATH])
                and len(meta["soname"]) <= 1 and len(set(meta["needed"])) == len(meta["needed"]))
        nodes[path] = {"bytes": len(raw), "sha256": base.digest(raw), "source": origins[path][0], "elf": meta}
        require(meta["interpreter"] in ("", "/" + INTERPRETER))
        if path in ROOTS:
            require(meta["interpreter"] == "/" + INTERPRETER and meta["soname"] == [])
        if meta["interpreter"]:
            target(path, meta["interpreter"], [INTERPRETER], "interpreter")
        # RUNPATH is per-object, never inherited by a dependency.
        directories = [s[1:] for s in meta["runpath"]] + list(DEFAULT_DIRS)
        for needed in meta["needed"]:
            require(re.fullmatch(r"[A-Za-z0-9_+.-]{1,128}", needed) and needed not in (".", ".."))
            target(path, needed, [d + "/" + needed for d in directories], "library")
    for edge in edges:
        if edge["kind"] == "library":
            require(nodes[edge["to"]]["elf"]["soname"] == [edge["needed"]])
    require(time.monotonic() < deadline)
    return {"nodes": nodes, "edges": edges, "resolution_policy": "unique-explicit-virtual-paths-v1"}


def compiler(go, work, sources):
    require(go.is_absolute() and ".." not in go.parts)
    original = base.read(go, 64 << 20)
    inputs = {name: sources[name] for name in GO_SOURCES}
    base.publish(work / "source", inputs)
    for name in ("cache", "modules", "tmp"):
        (work / name).mkdir(mode=0o700)
    env = {"PATH": str(go.parent) + ":/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C",
           "GOENV": "off", "GOWORK": "off", "GOTOOLCHAIN": "local", "GOPROXY": "off", "GOSUMDB": "off",
           "GOFLAGS": "-mod=readonly", "CGO_ENABLED": "0", "GOCACHE": str(work / "cache"),
           "GOMODCACHE": str(work / "modules"), "TMPDIR": str(work / "tmp")}
    end = time.monotonic() + 120
    def call(args):
        require(time.monotonic() < end)
        result = base.processes.execute_process_group([str(go), *args], env=env, cwd=work / "source",
            timeout=end - time.monotonic(), max_output_bytes=65536, stdin=subprocess.DEVNULL)
        result.check_returncode()
        require(not result.stderr and time.monotonic() < end)
        return result.stdout
    require(call(["env", "GOVERSION"]) == b"go1.27.2\n")
    prefix = b"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/"
    deps = call(["list", "-deps", "-f", "{{if not .Standard}}{{.ImportPath}}{{end}}", "./cmd/nib-elf-metadata"])
    require(deps.split() == [prefix + b"internal/elfmeta", prefix + b"cmd/nib-elf-metadata"])
    binary = work / "metadata"
    require(call(["build", "-trimpath", "-buildvcs=false", "-ldflags=-buildid=", "-o", str(binary), "./cmd/nib-elf-metadata"]) == b"")
    raw = base.read(binary, 8 << 20)
    require(base.read(go, 64 << 20) == original)
    base.verify(work / "source", inputs)
    return binary, {"version": "go1.27.2", "compiler_sha256": base.digest(original),
                    "helper_sha256": base.digest(raw), "compiler_authenticated": False}


def inspector(binary, deadline):
    def inspect(raw):
        remaining = min(10, deadline - time.monotonic())
        require(remaining > 0 and len(raw) <= 16 << 20)
        # A private anonymous file avoids a blocking parent write to child stdin.
        with tempfile.TemporaryFile() as stream:
            require(stream.write(raw) == len(raw))
            stream.seek(0)
            result = base.processes.execute_process_group([str(binary)],
                env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C"},
                stdin=stream, timeout=remaining, max_output_bytes=65536)
        result.check_returncode()
        require(not result.stderr and time.monotonic() < deadline)
        return json.loads(result.stdout, object_pairs_hook=unique)
    return inspect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("metadata", "archives", "go", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--package", action="append", required=True)
    args = parser.parse_args()
    tools.separate_output(args.output, (args.metadata, args.archives, args.go))
    revision, sources = tools.committed(EXTRA)
    require(sources[TOOLS] == base.read(ROOT / TOOLS, 1 << 20))
    lock = json.loads(sources[LOCK], object_pairs_hook=unique)
    rows = selection(lock, args.package)
    report = {"schema": "nib.network-static-closure/v1", "scope": "restricted-offline-static-graph-only",
              "result": "fail", "static_graph_closed": False, **CLAIMS, "git_sha": revision,
              "source_hashes": {n: base.digest(v) for n, v in sources.items()},
              "lock_sha256": base.digest(sources[LOCK]), "packages": rows}
    files = {}
    try:
        report["indexes"] = metadata_check(lock, rows, args.metadata)
        with tempfile.TemporaryDirectory(prefix="nib-network-static-") as temporary:
            helper, report["compiler"] = compiler(args.go, Path(temporary), sources)
            deadline = time.monotonic() + 120
            tree, origins = assemble(rows, args.archives, deadline)
            files["inventory.json"] = base.encoded({"members": tools.observation(tree), "origins": origins})
            require(len(files["inventory.json"]) <= 8 << 20)
            report["inventory_sha256"] = base.digest(files["inventory.json"])
            report["graph"] = closure(tree, origins, inspector(helper, deadline), deadline=deadline)
        report.update(result="pass", static_graph_closed=True)
    except Blocked as error:
        report["blocker"] = error.detail
    except (OSError, ValueError, KeyError, TypeError, EOFError, UnicodeError,
            lzma.LZMAError, tarfile.TarError, subprocess.SubprocessError):
        report["blocker"] = {"category": "input-tool-or-contract-rejected"}
    files["receipt.json"] = base.encoded(report)
    require(len(files["receipt.json"]) <= 2 << 20)
    base.publish(args.output, files)
    base.verify(args.output, files)
    print(json.dumps({k: report[k] for k in ("result", "git_sha", "static_graph_closed")}))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        sys.exit("Network static preparation rejected; no package execution authorized.")
