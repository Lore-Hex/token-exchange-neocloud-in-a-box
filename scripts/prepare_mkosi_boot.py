#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Derive an initrd member inventory from the existing authenticated packages."""
import json

KERNEL = "6.8.0-147-generic"
LOCK = "image/inputs/guest-runtime-ubuntu2404.json"
# Library dependency closure of systemd, udev, kmod and mount in this exact lock.
# No new resolver, package, package hook or host runtime is introduced.
LIBRARIES = frozenset("""libacl1 libapparmor1 libargon2-1 libaudit-common libaudit1
libblkid1 libc6 libcap-ng0 libcap2 libcrypt1 libcryptsetup12 libdevmapper1.02.1
libfdisk1 libgcc-s1 libgcrypt20 libgpg-error0 libjson-c5 libkmod2 liblz4-1 liblzma5
libmount1 libpam0g libpcre2-8-0 libseccomp2 libselinux1 libsmartcols1 libssl3t64
libsystemd-shared libsystemd0 libudev1 libuuid1 libzstd1""".split())
PACKAGES = LIBRARIES | {"base-files", "systemd", "udev", "kmod", "mount", "dmsetup",
                        "linux-modules-" + KERNEL}
MAX_BYTES = 192 << 20
MAX_ENTRIES = 8192
INVENTORY_SHA256 = "9e58a03558396b6cf3370b44405fad56acc18c7962e6b80661eb8942ce46d540"


def selected(package, path, kind):
    if kind == "dir":
        return False
    if package == "base-files":
        return path == "usr/lib/os-release"
    if package.startswith("linux-modules-"):
        return path.startswith("lib/modules/" + KERNEL + "/")
    if package in LIBRARIES:
        return path.startswith(("lib/", "lib64/", "usr/lib/", "usr/lib64/"))
    return path.startswith(("usr/bin/", "usr/sbin/", "usr/lib/", "lib/", "sbin/", "bin/"))


def prepare(archives, lock_raw, tools):
    base, require = tools.base, tools.require
    lock = json.loads(lock_raw)
    require(lock["scope"] == "guest-runtime-dependency-coverage" and len(lock["packages"]) == 212)
    rows = {r["Package"]: r for r in lock["packages"]}
    require(PACKAGES <= rows.keys())
    tree = {p: {"kind": "link", "target": "usr/" + p, "mode": 0o777}
            for p in ("bin", "sbin", "lib", "lib64")}
    packages = {}
    with base.directory(archives) as directory:
        for package in sorted(PACKAGES):
            row = rows[package]
            raw = base.read_at(directory, row["SHA256"] + ".deb", base.MAX_ARCHIVE)
            require(len(raw) == int(row["Size"]) and base.digest(raw) == row["SHA256"])
            payload = tools.payload(raw)
            tools.merge(tree, {p: r for p, r in payload.items() if selected(package, p, r["kind"])})
            packages[package] = {"sha256": row["SHA256"], "version": row["Version"]}
    tools.complete_tree(tree)
    require(len(tree) <= MAX_ENTRIES and sum(len(r.get("data", b"")) for r in tree.values()) <= MAX_BYTES)
    for name in ("usr/lib/systemd/systemd", "usr/bin/systemctl", "usr/bin/udevadm", "usr/bin/kmod",
                 "usr/bin/mount", "usr/lib/systemd/systemd-veritysetup",
                 "usr/lib/systemd/system-generators/systemd-veritysetup-generator",
                 "usr/lib/systemd/system-generators/systemd-fstab-generator"):
        tools.executable(tree, name)
    for name in ("kernel/drivers/md/dm-verity.ko.zst", "kernel/fs/erofs/erofs.ko.zst",
                 "modules.builtin", "modules.builtin.modinfo"):
        require("usr/lib/modules/" + KERNEL + "/" + name in tree)
    result = base.encoded({"schema": "nib.initrd-inputs/v1", "kernel": KERNEL,
                           "guest_lock_sha256": base.digest(lock_raw), "packages": packages,
                           "members": tools.observation(tree), "native_execution_verified": False})
    require(base.digest(result) == INVENTORY_SHA256)
    return result
