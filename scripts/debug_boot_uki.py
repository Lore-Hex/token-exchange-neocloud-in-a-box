#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fixed debug UKI supplement inside the authenticated offline tools namespace."""
import importlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import types

SUFFIX = b" systemd.unit=nib-debug-boot.target nib.debug-boot=v1"
TOPOLOGY_PROFILE = "debug-topology/v1"
UNITS = {
    "nib-debug-boot-stage.service": "etc/systemd/system/nib-debug-boot-stage.service",
    "10-nib-debug-boot.conf": "etc/systemd/system/initrd-switch-root.service.d/10-nib-debug-boot.conf",
}
INPUT = Path("/input")
OUTPUT = Path("/out")
PAYLOAD = Path("/nib")


def module(name):
    # The host binds the exact committed source bytes here; -I -S -B and
    # explicit compilation avoids ambient packages and bytecode-cache imports.
    path = PAYLOAD / (name + ".py")
    value = types.ModuleType("debug_" + name)
    value.__file__ = str(path)
    raw = path.read_bytes()
    if not 0 < len(raw) <= 1 << 20:
        raise ValueError("debug-module-rejected")
    exec(compile(raw, str(path), "exec", dont_inherit=True), value.__dict__)
    return value


def load_pe(boot):
    for name, (size, digest) in boot.TOOLS.items():
        boot.require(boot.identity(boot.read(Path("/") / name, size)) == {"bytes": size, "sha256": digest})
    boot.require(not any(name == "pefile" or name.startswith("ordlookup") for name in sys.modules))
    sys.meta_path.insert(0, boot.VendorSources())
    return importlib.import_module("pefile")


def sections(boot, pefile, raw):
    pe = pefile.PE(data=raw, fast_load=True)
    boot.require(not pe.get_warnings())
    return {name: part.get_data()[:part.Misc_VirtualSize] for name, part in boot.layout(pe, raw).items()}


def directories(root, names):
    for name in names:
        (root / name).mkdir(mode=0o755)
        (root / name).chmod(0o755)


def original(boot, pefile, config):
    report = config["original"]
    boot.require(report["schema"] == "nib.private-boot-uki/v1" and report["result"] == "pass"
                 and report["root_hash"] == config["root_hash"] and report["sections_checked"] is True)
    raw = boot.read(INPUT / "nib.efi", boot.MAX_IMAGE)
    boot.require(boot.identity(raw) == report["uki"]["image"])
    parts = sections(boot, pefile, raw)
    boot.require(parts[".cmdline"] == boot.cmdline(config["root_hash"])
                 and parts[".uname"] == boot.KERNEL.encode()
                 and boot.identity(parts[".linux"]) == dict(zip(("bytes", "sha256"),
                         boot.GUEST["boot/vmlinuz-" + boot.KERNEL])))
    stub_path = "usr/lib/systemd/boot/efi/linuxx64.efi.stub"
    stub = boot.read(PAYLOAD / "stub", 1 << 20)
    boot.require(boot.identity(stub) == dict(zip(("bytes", "sha256"), boot.GUEST[stub_path])))
    directories(boot.ROOTFS.parent, (boot.ROOTFS.name,))
    directories(boot.ROOTFS, ("boot", "usr", "usr/lib", "usr/lib/systemd", "usr/lib/systemd/boot",
                              "usr/lib/systemd/boot/efi"))
    boot.write(boot.ROOTFS / stub_path, stub)
    boot.write(boot.ROOTFS / ("boot/vmlinuz-" + boot.KERNEL), parts[".linux"])
    boot.WORK.mkdir(mode=0o700)
    directories(boot.WORK, ("tree", "tree/usr", "tree/usr/lib"))
    for name, data in {"nib.efi": raw, "initrd.cpio": parts[".initrd"], "cmdline": parts[".cmdline"],
                       "tree/usr/lib/os-release": parts[".osrel"]}.items():
        boot.write(boot.WORK / name, data)
    # Reuse the complete stub/header/section inspection, not just section hashes.
    boot.require(boot.inspect_uki(pefile) == report["uki"])
    boot.verify_tools()
    shutil.rmtree(boot.WORK)
    return parts


def supplement(boot, config):
    boot.WORK.mkdir(mode=0o700)
    directories(boot.WORK, ("tree", "tree/usr", "tree/usr/libexec", "tree/etc", "tree/etc/systemd",
                            "tree/etc/systemd/system", "tree/etc/systemd/system/initrd-switch-root.service.d"))
    probe = boot.read(PAYLOAD / "probe", 8 << 20)
    boot.require(boot.identity(probe) == config["probe"] and probe[:7] == b"\x7fELF\x02\x01\x01")
    boot.write(boot.WORK / "tree/usr/libexec/nib-debug-boot", probe, 0o500)
    for source, destination in UNITS.items():
        raw = boot.read(PAYLOAD / source, 16384)
        boot.require(boot.identity(raw) == config["units"][source])
        boot.write(boot.WORK / "tree" / destination, raw, 0o444)
    expected = boot.snapshot()
    for name in expected:
        os.utime(boot.WORK / "tree" / name, (boot.EPOCH, boot.EPOCH), follow_symlinks=False)
    boot.write(boot.WORK / "file-list", b"".join(name.encode() + b"\0" for name in sorted(expected)))
    return expected


def operation(processes, boot, name):
    result = processes.execute_process_group(
        ["/usr/bin/python3.12", "-I", "-S", "-B", "/nib/debug-uki.py", name],
        env=boot.environment(), timeout=boot.OPERATIONS[name], max_output_bytes=65536,
        max_stderr_bytes=65536, stdin=subprocess.DEVNULL)
    result.check_returncode()
    # Public tool diagnostics are bounded but never included in the proof channel.
    return result


def build(boot, pefile, processes, config, report):
    report["stage"] = "original-uki"
    topology = input_profile(config)
    boot.require(not boot.WORK.exists() and not boot.ROOTFS.exists())
    if topology:
        report.update(schema="nib.debug-topology-uki/v1", profile=TOPOLOGY_PROFILE)
    boot.ROOTFS.parent.mkdir(mode=0o700)
    parts = original(boot, pefile, config)
    report["stage"] = "supplement"
    expected = supplement(boot, config)
    operation(processes, boot, "cpio")
    archive = boot.read(boot.WORK / "initrd.cpio", 9 << 20)
    boot.check_cpio(archive, expected)
    combined = parts[".initrd"] + archive
    boot.require(len(combined) <= boot.MAX_IMAGE)
    (boot.WORK / "initrd.cpio").unlink()
    boot.write(boot.WORK / "initrd.cpio", combined)
    boot.write(boot.WORK / "cmdline", boot.cmdline(config["root_hash"]) + SUFFIX)
    boot.write(boot.WORK / "ukify.conf", b"[UKI]\n", 0o400)
    directories(boot.WORK / "tree", ("usr/lib",))
    boot.write(boot.WORK / "tree/usr/lib/os-release", parts[".osrel"])
    report["stage"] = "ukify"
    operation(processes, boot, "ukify")
    report["stage"] = "inspect"
    result = operation(processes, boot, "inspect")
    observed = json.loads(result.stdout, object_pairs_hook=boot.unique_object)
    image = boot.read(boot.WORK / "nib.efi", boot.MAX_IMAGE)
    boot.require(observed == boot.inspect_uki(pefile) and observed["image"] == boot.identity(image)
                 and observed["image"] != config["original"]["uki"]["image"]
                 and observed["sections"][".initrd"] == boot.identity(combined)
                 and observed["sections"][".cmdline"] == boot.identity(boot.cmdline(config["root_hash"]) + SUFFIX))
    boot.write(OUTPUT / "debug.efi", image)
    report.update(result="pass", stage="complete", root_hash=config["root_hash"],
                  original_uki=config["original"]["uki"]["image"], probe=config["probe"],
                  supplement=boot.identity(archive), uki=observed)
    return report


def input_profile(config):
    if type(config) is not dict:
        raise ValueError("debug-uki-rejected")
    if config.get("schema") == "nib.debug-uki-input/v1" and "profile" not in config:
        return False
    if (config.get("schema") == "nib.debug-uki-input/v2" and config.get("profile") == TOPOLOGY_PROFILE
            and set(config) == {"schema", "profile", "root_hash", "original", "probe", "units"}):
        return True
    raise ValueError("debug-uki-rejected")


def main():
    boot = module("boot-uki")
    boot.require(sys.platform == "linux" and sys.version_info[:2] == (3, 12)
                 and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode)
    if len(sys.argv) == 2 and sys.argv[1] in ("cpio", "ukify", "inspect"):
        # Existing exact fixed commands and pinned-source imports remain intact.
        boot.main()
        return 0
    boot.require(len(sys.argv) == 6)
    module("namespace-check").check(sys.argv[1:5])
    module("pid-check").check(sys.argv[5])
    module("ownership").check(json.loads(boot.read(PAYLOAD / "ownership-input.json", 4096)))
    module("confinement").check()
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (boot.MAX_IMAGE, boot.MAX_IMAGE))
    config = json.loads(boot.read(PAYLOAD / "debug-input.json", 65536), object_pairs_hook=boot.unique_object)
    pefile = load_pe(boot)
    report = {"schema": "nib.debug-uki/v1", "result": "fail", "stage": "preflight", "signed": False,
              "boot_verified": False, "confidentiality_verified": False}
    try:
        build(boot, pefile, module("snapshot"), config, report)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        report["result"] = "fail"
    finally:
        for path in (boot.WORK, boot.ROOTFS.parent):
            if path.exists():
                shutil.rmtree(path)
    boot.write(OUTPUT / "debug.json", (json.dumps(report, sort_keys=True) + "\n").encode())
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        sys.exit("NIB_DEBUG_UKI_REJECTED")
