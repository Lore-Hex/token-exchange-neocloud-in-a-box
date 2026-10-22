#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Explicit pinned-target driver build and opt-in image install, never loading."""
import hashlib
import os
from pathlib import Path
import re
import runpy
import socket
import stat
import subprocess
import sys
import types

ROOTFS = Path("/work/output/rootfs")
KERNEL = "6.8.0-147-generic"
DRIVER = "595.91.07"
MODULES = ("nvidia", "nvidia-modeset", "nvidia-drm", "nvidia-uvm", "nvidia-peermem")
CONTROL_NAMES = ("etc/dkms/no-autoinstall", "etc/dkms/framework.conf.d/99-nib-build.conf",
                 "etc/dkms/nvidia-" + DRIVER + "-" + KERNEL + "-x86_64.conf")
COMPILE_H = "usr/src/linux-headers-" + KERNEL + "/include/generated/compile.h"
SOURCE_PINS = {
    "usr/sbin/dkms": "a7a45df4e9f8a4fbea258a54b31d21760121560fe0b6187e34d5b39944e2767b",
    COMPILE_H: "aeebd7e60ca6d32c1ea7b4823d42a33fd5fdb455d19c957e8075e6c3d4b0613b",
    "usr/bin/x86_64-linux-gnu-nm": "3708df3d7ce16e2c1dcdcd722784ae382d4d582276a71ab7bbe2d542f5621202",
    "usr/src/nvidia-595.91.07/nvidia/internal_crypt_lib.h": "f877669b7ffa91518beca9c9bd666f331c5e1ab406818ec2bb173dddd6680e0f",
    "usr/src/nvidia-595.91.07/nvidia/libspdm_aead.c": "25dc00713ba1eaae12f3d6eb8433c87413b9d40c058e8a262ddbb6d35af34bb4",
    "usr/src/nvidia-595.91.07/nvidia/libspdm_ecc.c": "9dc820124a9f568d38d35c324bac8e845ddbc7158959ac94ed0000248749fb61",
    "usr/src/nvidia-595.91.07/nvidia/libspdm_shash.c": "5f396f9eb94ebaaf1a6636b5d9df82ccf94e988633bbd9757a0f4e42e506680b",
}
INSTALL_PINS = {
    "usr/src/nvidia-595.91.07/dkms.conf": "ecc14df50b16604c446e9e5b8609dd60c5551dc5937d711aa782aea87b6ff217",
    "etc/dkms/framework.conf": "4e1d08f13f8bd021383d2f0c42aaee498800a88325701b3556555cc4867e758f",
    "etc/dkms/framework.conf.d/autoinstall_all_kernels.conf": "e362342a516c0507da4407b889d041a1df7e187f4cb8d05c8a039b1a307d2d4b",
}
MODULE_DIRECTORY = "var/lib/dkms/nvidia/" + DRIVER + "/" + KERNEL + "/x86_64/module/"
CRYPTO_REFERENCES = ("crypto_alloc_aead", "crypto_alloc_akcipher", "crypto_alloc_shash")
MAKE_LOGS = ("var/lib/dkms/nvidia/" + DRIVER + "/build/make.log",
             "var/lib/dkms/nvidia/" + DRIVER + "/" + KERNEL + "/x86_64/log/make.log")


def require(value):
    if not value:
        raise ValueError("driver-build-rejected")


def regular(root, name, limit):
    require(type(name) is str and name and name == Path(name).as_posix()
            and not Path(name).is_absolute())
    path = root
    for part in Path(name).parts:
        require(part not in (".", "..", "/"))
        path = path / part
        require(not path.is_symlink())
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 < info.st_size <= limit)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        require(len(raw) == info.st_size)
        return raw
    finally:
        os.close(descriptor)


def verify_inputs(root, controls):
    require(root.is_dir() and not root.is_symlink() and type(controls) is dict
            and set(controls) == set(CONTROL_NAMES))
    for name, digest in SOURCE_PINS.items():
        require(hashlib.sha256(regular(root, name, 256 << 10)).hexdigest() == digest)
    for name, raw in controls.items():
        require(name.startswith("etc/dkms/") and type(raw) is bytes)
        require(regular(root, name, 16384) == raw)
    require((root / "lib/modules" / KERNEL / "build").is_symlink())
    require(os.readlink(root / "lib/modules" / KERNEL / "build") == "/usr/src/linux-headers-" + KERNEL)
    require(not (root / "var/lib/dkms/nvidia").exists())


def operation(name):
    if name == "compiler":
        return ["/usr/bin/x86_64-linux-gnu-gcc-13", "-dumpfullversion", "-dumpversion"]
    if name == "symbols":
        return ["/usr/bin/x86_64-linux-gnu-nm", "--undefined-only", "--format=posix",
                "--no-demangle", "/" + MODULE_DIRECTORY + "nvidia.ko"]
    require(name in ("add", "build", "install"))
    return ["/usr/sbin/dkms", name, "-m", "nvidia", "-v", DRIVER, "-k", KERNEL, "-a", "x86_64"]


def command(name, prefix):
    operation(name)
    root = str(ROOTFS)
    # Trusted namespace setup precedes the unchanged final capability/seccomp
    # boundary. Only the private installation root is writable in this child.
    return ["/usr/bin/python3.12", "-I", "-S", "-B", "/nib/mkosi-sandbox.py",
            "--ro-bind", "/", "/", "--bind", root, root,
            "--dev", root + "/dev", "--ro-bind", "/proc", root + "/proc",
            "--tmpfs", "/tmp", "--chdir", "/", *prefix,
            "/usr/bin/python3.12", "-I", "-S", "-B", "/nib/dkms-worker.py", name]


def inspect_modules(root, directory=MODULE_DIRECTORY):
    rows = []
    for name in MODULES:
        raw = regular(root, directory + name + ".ko", 256 << 20)
        # ELF64 little-endian relocatable x86_64, not an executable or DSO.
        require(len(raw) >= 64 and raw[:7] == b"\x7fELF\x02\x01\x01"
                and raw[16:20] == b"\x01\x00\x3e\x00")
        rows.append({"name": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    return rows


def install_inputs(root, controls):
    for name, digest in INSTALL_PINS.items():
        require(hashlib.sha256(regular(root, name, 16384)).hexdigest() == digest)
    require(set(controls) == set(CONTROL_NAMES))
    for name, raw in controls.items():
        require(regular(root, name, 16384) == raw)
    # DKMS sources overrides and optional hooks from this tree. Only the
    # authenticated package configuration and our reviewed build controls may be present.
    expected = {*CONTROL_NAMES, "etc/dkms/framework.conf.d",
                *(name for name in INSTALL_PINS if name.startswith("etc/dkms/"))}
    observed = set()
    for path in (root / "etc/dkms").rglob("*"):
        name = path.relative_to(root).as_posix()
        require(name in expected and not path.is_symlink())
        observed.add(name)
    require(observed == expected)
    require({path.name for path in (root / "var/lib/dkms").iterdir() if path.is_dir()} == {"nvidia"})
    lib = root / "lib"
    if lib.is_symlink():
        require(os.readlink(lib) == "usr/lib")
        directory = "usr/lib/modules/" + KERNEL + "/"
    else:
        require(lib.is_dir())
        directory = "lib/modules/" + KERNEL + "/"
    # Validate every parent without resolving arbitrary links out of the image.
    path = root
    for part in Path(directory).parts:
        path = path / part
        require(path.is_dir() and not path.is_symlink())
    require(not (path / "updates/dkms").exists() and not (path / "updates/dkms").is_symlink())
    require(not (path / "updates").is_symlink())
    return directory


def installed_modules(root, directory, built):
    target = directory + "updates/dkms/"
    require(inspect_modules(root, target) == built and inspect_modules(root) == built)
    require({path.name for path in (root / target).iterdir()} == {name + ".ko" for name in MODULES})
    raw = regular(root, directory + "modules.dep", 8 << 20)
    wanted = {"updates/dkms/" + name + ".ko" for name in MODULES}
    rows = [line.split(":", 1)[0] for line in raw.decode("ascii").splitlines() if ":" in line]
    require(all(rows.count(name) == 1 for name in wanted))
    indexes = {}
    for name in ("modules.dep", "modules.dep.bin", "modules.alias.bin", "modules.symbols.bin"):
        data = regular(root, directory + name, 8 << 20)
        indexes[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    return indexes


def install(processes, output, environment, controls, prefix, built, root=ROOTFS):
    require(root == ROOTFS)
    directory = install_inputs(root, controls)
    require(inspect_modules(root) == built)
    try:
        result = processes.execute_process_group(command("install", prefix), env=environment,
            timeout=120, max_output_bytes=2 << 20, max_stderr_bytes=2 << 20, stdin=subprocess.DEVNULL)
    except processes.OutputLimitExceeded as error:
        preserve_diagnostics(output, "install", error)
        raise
    preserve_diagnostics(output, "install", result)
    result.check_returncode()
    return installed_modules(root, directory, built)


def crypto_references(raw):
    """Require real strong undefined references, not text strings or weak stubs."""
    require(type(raw) is bytes and 0 < len(raw) <= 1 << 20)
    lines = raw.decode("ascii").splitlines()
    require(0 < len(lines) <= 4096)
    symbols = {}
    for line in lines:
        parts = line.split()
        require(len(parts) == 2 and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.$]*", parts[0])
                and parts[1] in ("U", "w", "v") and parts[0] not in symbols)
        symbols[parts[0]] = parts[1]
    require(all(symbols.get(name) == "U" for name in CRYPTO_REFERENCES))
    return list(CRYPTO_REFERENCES)


def preserve_diagnostics(output, name, result):
    for stream in ("stdout", "stderr"):
        raw = getattr(result, stream)
        require(type(raw) is bytes and len(raw) <= 2 << 20)
        if raw:
            (output / ("dkms-" + name + "." + stream)).write_bytes(raw)


def preserve_make_log(root, output):
    for index, name in enumerate(MAKE_LOGS):
        try:
            raw = regular(root, name, 2 << 20)
        except FileNotFoundError:
            continue
        (output / ("dkms-make-" + str(index) + ".log")).write_bytes(raw)


def build(processes, output, environment, controls, prefix, root=ROOTFS, *, install_driver=False):
    require(root == ROOTFS and type(install_driver) is bool)
    verify_inputs(root, controls)
    modules = None
    references = None
    for name in ("compiler", "add", "build", "symbols"):
        try:
            result = processes.execute_process_group(command(name, prefix), env=environment,
                timeout=1500 if name == "build" else 30,
                max_output_bytes=2 << 20, max_stderr_bytes=2 << 20, stdin=subprocess.DEVNULL)
        except processes.OutputLimitExceeded as error:
            preserve_diagnostics(output, name, error)
            raise
        finally:
            if name == "build":
                preserve_make_log(root, output)
        preserve_diagnostics(output, name, result)
        result.check_returncode()
        if name == "compiler":
            require(result.stdout == b"13.3.0\n" and not result.stderr)
        if name == "build":
            modules = inspect_modules(root)
        if name == "symbols":
            require(not result.stderr)
            references = crypto_references(result.stdout)
            require(inspect_modules(root) == modules)
    indexes = install(processes, output, environment, controls, prefix, modules, root) if install_driver else None
    return {"result": "pass", "kernel": KERNEL, "driver": DRIVER, "compiler": "13.3.0",
            "modules": modules, "crypto_references": references, "crypto_linkage_verified": True,
            "installed": install_driver, "dependency_indexes": indexes, "loaded": False,
            "lkca_verified": False, "reproducibility_verified": False, "bootable": False}


def main():
    require(sys.platform == "linux" and sys.version_info[:2] == (3, 12)
            and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode
            and len(sys.argv) == 2 and os.geteuid() == os.getegid() == 0
            and socket.if_nameindex() == [(1, "lo")])
    args = operation(sys.argv[1])
    guard = types.SimpleNamespace(**runpy.run_path("/nib/confinement.py", run_name="driver_confinement"))
    guard.check()
    require(ROOTFS.is_dir() and not ROOTFS.is_symlink())
    os.chroot(ROOTFS)
    os.chdir("/")
    os.execve(args[0], args, {"PATH": "/usr/bin:/usr/sbin:/bin:/sbin", "HOME": "/tmp",
                            "TMPDIR": "/tmp", "LANG": "C", "LC_ALL": "C",
                            "SOURCE_DATE_EPOCH": "1792454400"})


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError):
        sys.exit("NIB_DRIVER_WORKER_REJECTED")
