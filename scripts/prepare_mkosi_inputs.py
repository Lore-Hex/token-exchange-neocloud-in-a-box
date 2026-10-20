#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare pinned mkosi source, packages and script inventory; never execute them."""
import sys

if __name__ == "__main__" and not sys.flags.isolated:
    sys.exit("Use python3 -I scripts/prepare_mkosi_inputs.py")

import argparse
import gzip
import hashlib
import io
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
import time
import types

ROOT = Path(__file__).absolute().parent.parent
EXTRA = ("scripts/prepare_mkosi_inputs.py", "image/inputs/mkosi-27.1.json",
         "image/inputs/mkosi-tools-ubuntu2404.json",
         "image/patches/mkosi-27.1-service-denial.patch",
         "image/inputs/mkosi-27.1-service-denial.json")
COMMIT = "ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b"
PATCH_TARGET = "mkosi/installer/apt.py"
MAX_SOURCE = 8 << 20
MAX_PACKAGES = 320
MAX_BYTES = 640 << 20
SCRIPTS = frozenset(("preinst", "postinst", "prerm", "postrm", "config", "triggers"))


def load():
    path = ROOT / "scripts/prepare_guest_tools.py"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size < 1 << 20:
            raise ValueError("mkosi-bootstrap-rejected")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read((1 << 20) + 1)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_nlink,
                              s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if len(raw) != before.st_size or identity(before) != identity(os.fstat(fd)):
            raise ValueError("mkosi-bootstrap-rejected")
    finally:
        os.close(fd)
    module = types.ModuleType("mkosi_input_tools")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module, raw


tools, TOOLS_SOURCE = load()
base, require = tools.base, tools.require


def verify_source(raw, pin):
    require(pin["schema"] == "nib.mkosi-source/v1" and pin["version"] == "27.1"
            and pin["git_sha"] == COMMIT
            and pin["repository"] == "https://github.com/systemd/mkosi"
            and pin["archive_url"] == "https://codeload.github.com/systemd/mkosi/tar.gz/" + COMMIT
            and pin["archive_prefix"] == "mkosi-" + COMMIT + "/"
            and pin["required_python"] == ">=3.9"
            and all(pin[k] is False for k in ("executed", "build_verified", "deployable"))
            and len(raw) == pin["archive_bytes"] <= MAX_SOURCE
            and base.digest(raw) == pin["archive_sha256"])
    expected = pin["source_files"]
    require(isinstance(expected, dict) and 0 < len(expected) <= 512)
    parents = {""}
    for name, row in expected.items():
        require(tools.path_name(name) == name and row["mode"] in ("100644", "100755", "120000")
                and type(row["bytes"]) is int and 0 <= row["bytes"] <= 2 << 20
                and re.fullmatch("[0-9a-f]{64}", row["sha256"])
                and re.fullmatch("[0-9a-f]{40}", row["git_blob"]))
        parents.update(str(p) for p in PurePosixPath(name).parents if str(p) != ".")
    require(not set(expected) & parents)
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
        expanded = stream.read(MAX_SOURCE + 1)
    require(len(expanded) <= MAX_SOURCE)
    found, directories, links = {}, set(), {}
    with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
        for count, member in enumerate(archive):
            require(count < 1024 and not member.issparse()
                    and member.pax_headers in ({}, {"comment": COMMIT})
                    and member.name.startswith(pin["archive_prefix"].removesuffix("/")))
            if member.name.rstrip("/") == pin["archive_prefix"].rstrip("/"):
                require(member.isdir() and "" not in directories)
                directories.add("")
                continue
            require(member.name.startswith(pin["archive_prefix"]))
            name = member.name.removeprefix(pin["archive_prefix"]).removesuffix("/")
            require(tools.path_name(name) == name)
            if member.isdir():
                require(name in parents and name not in directories)
                directories.add(name)
                continue
            require(name in expected and name not in found and (member.isfile() or member.issym()))
            row = expected[name]
            require(0 <= member.mode <= 0o777
                    and row["mode"] == ("120000" if member.issym() else
                                    ("100755" if member.mode & 0o111 else "100644")))
            if member.issym():
                require(not member.linkname.startswith("/")
                        and tools.link_target(name, member.linkname) in set(expected) | parents)
                links[name] = {"kind": "link", "target": member.linkname}
                data = member.linkname.encode("utf-8")
            else:
                require(member.size == row["bytes"])
                with archive.extractfile(member) as stream:
                    data = stream.read((2 << 20) + 1)
            require(len(data) == row["bytes"] and base.digest(data) == row["sha256"]
                    and hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
                    == row["git_blob"])
            found[name] = data
    for name in links:
        tools.resolve(links, name)
    require(set(found) == set(expected)
            and pin["license_files"] == sorted(n for n in expected if n.startswith("LICENSES/"))
            and "LICENSES/LGPL-2.1-or-later.txt" in pin["license_files"]
            and "pyproject.toml" in found and "mkosi/__main__.py" in found)
    return ({"git_sha": COMMIT, "files": len(found), "source_bytes": sum(map(len, found.values())),
             "archive_sha256": base.digest(raw), "git_blob_correspondence": True,
             "upstream_executed": False, "license_files_preserved": True}, found)


def service_denial_patch(members, source_pin, patch, pin):
    """Apply this one pinned insertion at its exact line; no patch tool or fuzzy search."""
    require(set(pin) == {"schema", "mkosi_git_sha", "source_path", "source_url", "license",
                         "patch_path", "patch_bytes", "patch_sha256", "input_bytes", "input_sha256",
                         "result_bytes", "result_sha256"}
            and pin["schema"] == "nib.mkosi-service-denial-patch/v1"
            and pin["mkosi_git_sha"] == COMMIT
            and pin["source_path"] == PATCH_TARGET
            and pin["source_url"] == "https://github.com/systemd/mkosi/blob/" + COMMIT + "/" + PATCH_TARGET
            and pin["license"] == "LGPL-2.1-or-later" and pin["patch_path"] == EXTRA[3]
            and len(patch) == pin["patch_bytes"] <= 4096
            and base.digest(patch) == pin["patch_sha256"])
    original = members[PATCH_TARGET]
    row = source_pin["source_files"][PATCH_TARGET]
    require(row["mode"] == "100644" and len(original) == row["bytes"] == pin["input_bytes"]
            and base.digest(original) == row["sha256"] == pin["input_sha256"]
            and original.startswith(b"# SPDX-License-Identifier: LGPL-2.1-or-later\n"))
    lines = patch.splitlines(keepends=True)
    require(lines[:3] == [b"--- a/mkosi/installer/apt.py\n", b"+++ b/mkosi/installer/apt.py\n",
                          b"@@ -259,5 +259,6 @@\n"]
            and len(lines) == 9 and all(line[:1] in (b" ", b"+") for line in lines[3:])
            and [line for line in lines[3:] if line.startswith(b"+")]
            == [b"+        policyrcd.chmod(0o755)\n"])
    before = [line[1:] for line in lines[3:] if line.startswith(b" ")]
    after = [line[1:] for line in lines[3:]]
    original_lines = original.splitlines(keepends=True)
    require(original_lines[258:263] == before)
    result = b"".join(original_lines[:258] + after + original_lines[263:])
    require(len(result) == pin["result_bytes"] <= 2 << 20
            and base.digest(result) == pin["result_sha256"])
    return result


def validate_tools_lock(lock, metadata):
    base.inputs.check(lock, metadata, scope="mkosi-build-tools-dependency-coverage")
    require(lock["provided_root_packages"] == {"systemd-repart": "systemd"}
            and set(lock["upstream_requested_packages"]) == set(lock["roots"]) | {"systemd-repart"}
            and len(set(lock["upstream_requested_packages"])) == len(lock["upstream_requested_packages"]))
    rows = {r["Package"]: r for r in lock["packages"]}
    require(base.inputs.matches(rows["systemd"], ("systemd-repart", None, None, None)))


def build_contract(guest, revision):
    # This is a gate/receipt contract, not an authorization flag or a build runner.
    return {
        "schema": "nib.mkosi-build-input-contract/v1", "git_sha": revision,
        "phase": "source-and-package-preparation", "execution_permitted": False,
        "mkosi_git_sha": COMMIT, "target_kernel": "6.8.0-147-generic",
        "target_driver": "595.91.07", "target_compiler": "gcc-13",
        "target_packages": len(guest["packages"]),
        "candidate_output": "nonbootable-directory",
        "clean_builds_required": 2, "shared_incremental_cache": False,
        "source_date_epoch": 1792454400,
        "proposed_execution_bounds": {"seconds_per_build": 1800, "memory_bytes": 8 << 30,
                                      "cpus": 2, "pids": 256, "output_bytes": 2 << 20},
        "bounds_runtime_validated": False,
        "required_before_execution": [
            "matching-apt-rehearsal-pass",
            "complete-maintainer-and-hook-review",
            "reviewed-user-namespace-mount-policy",
            "verified-local-service-denial-overlay-used-for-every-apt-transaction",
            "verified-tools-runtime-and-elf-linkage",
            "explicit-target-kernel-compiler-and-job-controls",
            "independent-source-review-and-green-ci",
        ],
        "required_after_execution": [
            "exact-installed-package-and-configured-state",
            "actual-dkms-conftest-and-lkca-module-evidence",
            "two-clean-output-inventories-and-byte-comparison",
            "independent-owned-resource-cleanup",
        ],
        "reproducible_image_verified": False, "uki_built": False, "verity_built": False,
        "bootable": False, "hardware_validated": False, "deployable": False,
    }


def prepare(metadata, archives, source_archive, revision, sources):
    pin, lock, guest = (json.loads(sources[p]) for p in
                        (EXTRA[1], EXTRA[2], tools.FILES[-1]))
    source, members = verify_source(source_archive, pin)
    patch_pin = json.loads(sources[EXTRA[4]])
    patched = service_denial_patch(members, pin, sources[EXTRA[3]], patch_pin)
    validate_tools_lock(lock, metadata)
    base.inputs.check(guest, metadata)
    require(len(lock["packages"]) <= 256 and lock["archive_bytes"] <= 128 << 20)
    rows = {}
    for row in [*lock["packages"], *guest["packages"]]:
        old = rows.setdefault(row["Package"], row)
        require(old == row)
    require(len(rows) <= MAX_PACKAGES and sum(int(r["Size"]) for r in rows.values()) <= MAX_BYTES)
    files, observations, indexes = {}, [], []
    deadline = time.monotonic() + 600
    with base.directory(archives) as fd:
        for name, row in sorted(rows.items()):
            require(time.monotonic() < deadline)
            raw = base.read_at(fd, row["SHA256"] + ".deb", base.MAX_ARCHIVE)
            require(len(raw) == int(row["Size"]) and base.digest(raw) == row["SHA256"])
            index, members = base.controls(raw, row)
            files["repo/pool/" + base.package_name(row)] = raw
            indexes.append(index)
            observations.append({"package": name, "version": row["Version"],
                                 "archive_sha256": row["SHA256"], "control_members": members,
                                 "maintainer_members": sorted(set(members) & SCRIPTS),
                                 "execution_review_complete": False})
        require(time.monotonic() < deadline)
    files["repo/Packages"] = b"".join(indexes)
    seen_indexes = {}
    for index in [*lock["indexes"], *guest["indexes"]]:
        require(seen_indexes.setdefault(index["file"], index) == index)
    with base.directory(metadata) as fd:
        for name, index in sorted(seen_indexes.items()):
            raw = base.read_at(fd, name, 32 << 20)
            require(len(raw) == index["bytes"] and base.digest(raw) == index["sha256"])
            files["metadata/" + name] = raw
    files.update({
        "mkosi-source.tar.gz": source_archive,
        "mkosi-source.json": sources[EXTRA[1]],
        "mkosi-service-denial.patch": sources[EXTRA[3]],
        "mkosi-service-denial.json": sources[EXTRA[4]],
        "mkosi-overlay/" + PATCH_TARGET: patched,
        "mkosi-tools-inputs.json": sources[EXTRA[2]],
        "guest-inputs.json": sources[tools.FILES[-1]],
        "mkosi.conf.candidate": base.recipe(guest),
        "build-contract.json": base.encoded(build_contract(guest, revision)),
        "maintainer-inventory.json": base.encoded({"packages": observations,
                                                 "execution_review_complete": False}),
        "tools-packages.txt": "".join(r["Package"] + "=" + r["Version"] + "\n"
                                     for r in lock["packages"]).encode(),
    })
    require(sum(map(len, files.values())) <= MAX_BYTES)
    report = {
        "schema": "nib.mkosi-input-preparation/v1", "result": "pass", "git_sha": revision,
        "scope": "verified-source-package-and-script-inputs", "mkosi": source,
        "local_patch": {"pin": patch_pin, "result_path": "mkosi-overlay/" + PATCH_TARGET,
                        "applied_in_memory": True, "executed": False},
        "source_hashes": {p: base.digest(raw) for p, raw in sources.items()},
        "tools_packages": len(lock["packages"]), "guest_packages": len(guest["packages"]),
        "unique_archives": len(rows),
        "archive_bytes": sum(int(r["Size"]) for r in rows.values()),
        "control_members": sum(len(r["control_members"]) for r in observations),
        "maintainer_members": sum(len(r["maintainer_members"]) for r in observations),
        "execution_review_complete": False, "apt_rehearsal_passed": False,
        "upstream_executed": False, "packages_executed": False, "image_built": False,
        "reproducibility_verified": False, "bootable": False, "deployable": False,
        "files": {name: {"bytes": len(raw), "sha256": base.digest(raw)}
                  for name, raw in sorted(files.items())},
    }
    files["receipt.json"] = base.encoded(report)
    require(sum(map(len, files.values())) <= MAX_BYTES)
    return files, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("metadata", "archives", "mkosi-source"):
        parser.add_argument("--" + name, type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--output", type=Path)
    mode.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.output:
        tools.separate_output(args.output, (args.metadata, args.archives, args.mkosi_source))
    revision, sources = tools.committed(EXTRA)
    require(sources[tools.FILES[0]] == TOOLS_SOURCE)
    raw = base.read(args.mkosi_source, MAX_SOURCE)
    files, report = prepare(args.metadata, args.archives, raw, revision, sources)
    if args.output:
        base.publish(args.output, files)
    base.verify(args.output or args.verify, files)
    print(json.dumps({k: report[k] for k in ("result", "git_sha", "tools_packages", "guest_packages",
                      "unique_archives", "archive_bytes", "packages_executed", "image_built",
                      "reproducibility_verified", "deployable")}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, EOFError, UnicodeError,
            tarfile.TarError, lzma.LZMAError, subprocess.SubprocessError):
        sys.exit("Mkosi input preparation rejected; no package or upstream code executed.")
