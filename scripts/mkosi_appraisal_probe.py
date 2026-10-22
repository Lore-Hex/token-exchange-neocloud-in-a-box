#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise the installed guest appraiser's CLI, without devices or networking."""
import codecs
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
# Payloads inspected from the authenticated Ubuntu 24.04 packages in the lock.
# NVIDIA SDK 11b2bfeb42a1158aa08744603ecb239ccb3d2265, CLI command reference.
FILES = {
    "usr/bin/nvattest": (1128696, "9c269a22c3f4710cdb115ce21d72c1c3b8e9c9e2db9cf4a391b510ed1a1dea42"),
    "usr/lib/x86_64-linux-gnu/libnvat.so.2.0.0":
        (26794312, "e757a59f8241830553da3f99f717ddd44d47973ca756a4fa3feac78012dce896"),
}
ALIAS = "usr/lib/x86_64-linux-gnu/libnvat.so.2"
OPERATIONS = {
    "help": ((), ("--format", "--log-level")),
    "attest-help": (("attest",), ("--device", "--nonce", "--verifier", "--gpu-evidence-source", "--nras-url")),
    "token-help": (("verify-token",), ("--token-file", "--nonce", "--nras-url", "--token-type")),
}


def require(value):
    if not value:
        raise ValueError("appraisal-probe-rejected")


def module(name):
    return types.SimpleNamespace(**runpy.run_path("/nib/" + name + ".py", run_name="appraisal_" + name))


def verify_files(reader, root=ROOTFS):
    require(root == ROOTFS and root.is_dir() and not root.is_symlink())
    identities = {}
    for name, (size, digest) in FILES.items():
        raw = reader(root, name, size)
        require(len(raw) == size and hashlib.sha256(raw).hexdigest() == digest)
        identities[name] = {"bytes": size, "sha256": digest}
    alias = root / ALIAS
    info = alias.lstat()
    require(stat.S_ISLNK(info.st_mode) and info.st_nlink == 1
            and os.readlink(alias) == "libnvat.so.2.0.0")
    require(not os.path.lexists(root / "etc/ld.so.preload"))
    return identities


def operation(name):
    require(type(name) is str and name in OPERATIONS)
    return ["/usr/bin/nvattest", *OPERATIONS[name][0], "--help"]


def command(name, prefix):
    operation(name)
    root = str(ROOTFS)
    # The vendor process sees the installed root read-only, private scratch
    # and synthetic /dev, inside the already isolated network/PID namespace.
    return ["/usr/bin/python3.12", "-I", "-S", "-B", "/nib/mkosi-sandbox.py",
            "--ro-bind", "/", "/", "--ro-bind", root, root,
            "--dev", root + "/dev", "--ro-bind", "/proc", root + "/proc",
            "--tmpfs", root + "/tmp", "--chdir", "/", *prefix,
            "/usr/bin/python3.12", "-I", "-S", "-B", "/nib/appraisal-probe.py", name]


def validate_help(name, raw):
    operation(name)
    require(type(raw) is bytes and 0 < len(raw) <= 65536)
    text = raw.decode("utf-8")
    for flag in OPERATIONS[name][1]:
        require(re.search(r"(?m)^[ \t]+(?:-[A-Za-z0-9],[ \t]*)?" + re.escape(flag)
                          + r"(?:[ =,\t]|$)", text))
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def probe(processes, prefix, reader, *, report=None, output=None):
    identities = verify_files(reader)
    results = {}
    for name in OPERATIONS:
        result = processes.execute_process_group(command(name, prefix),
            env={"PATH": "/usr/bin:/usr/sbin:/bin:/sbin", "HOME": "/tmp", "TMPDIR": "/tmp",
                 "LANG": "C", "LC_ALL": "C"},
            timeout=10, max_output_bytes=65536, max_stderr_bytes=65536, stdin=subprocess.DEVNULL)
        if report is not None:
            report.update(operation=name, exit_code=result.returncode,
                          stdout_bytes=len(result.stdout), stderr_bytes=len(result.stderr),
                          stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),
                          stderr_sha256=hashlib.sha256(result.stderr).hexdigest())
        # These fixed --help commands see only public, authenticated packages,
        # never credentials, devices, network inputs or inference requests.
        if output is not None and (result.returncode != 0 or result.stderr):
            require(output == Path("/out"))
            for suffix, raw in (("stdout", result.stdout), ("stderr", result.stderr)):
                require(type(raw) is bytes and len(raw) <= 65536)
                if raw:
                    (output / ("appraisal-cli-" + name + "." + suffix)).write_bytes(raw)
        result.check_returncode()
        require(not result.stderr)
        results[name] = validate_help(name, result.stdout)
    require(verify_files(reader) == identities)
    return {"result": "pass", "files": identities, "help": results,
            "appraisal_performed": False, "gpu_access": False, "network_access": False,
            "boot_verified": False}


def unprivileged_status(text, bounding_mask):
    require(type(text) is str and len(text) <= 16384)
    expected = {"CapInh": 0, "CapPrm": 0, "CapEff": 0, "CapBnd": bounding_mask, "CapAmb": 0}
    for field, value in expected.items():
        matches = re.findall(r"^" + field + r":\s*([0-9a-f]{16})$", text, re.M)
        require(len(matches) == 1 and int(matches[0], 16) == value)
    for field in ("Uid", "Gid"):
        require(re.findall(r"^" + field + r":\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$", text, re.M)
                == [("65534",) * 4])
    require(re.findall(r"^Groups:[ \t]*([^\n]*)$", text, re.M) == [""])
    require(re.findall(r"^NoNewPrivs:\s*(\d+)$", text, re.M) == ["1"])
    require(re.findall(r"^Seccomp:\s*(\d+)$", text, re.M) == ["2"])


def drop_privileges(bounding_mask):
    # Linux setresuid(2)/capabilities(7): removing every root UID clears
    # permitted/effective capabilities. NNP prevents file-cap/setuid regain.
    # CAP_SETPCAP is already absent: do not try to alter the bounding set.
    os.setgroups([])
    os.setresgid(65534, 65534, 65534)
    os.setresuid(65534, 65534, 65534)
    require(os.getresuid() == os.getresgid() == (65534, 65534, 65534) and not os.getgroups())
    with open("/proc/self/status", encoding="ascii") as stream:
        unprivileged_status(stream.read(16385), bounding_mask)


def prepare_chroot_runtime():
    # TextIOWrapper resolves ASCII lazily. The minimal guest intentionally has
    # no Python stdlib: populate the codec cache before crossing that boundary.
    require(codecs.lookup("ascii").name == "ascii")


def main():
    require(sys.platform == "linux" and sys.version_info[:2] == (3, 12)
            and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode
            and len(sys.argv) == 2 and os.geteuid() == os.getegid() == 0
            and socket.if_nameindex() == [(1, "lo")])
    args = operation(sys.argv[1])
    confinement = module("confinement")
    confinement.check()
    verify_files(module("dkms-worker").regular)
    prepare_chroot_runtime()
    os.chroot(ROOTFS)
    os.chdir("/")
    drop_privileges(confinement.CAP_MASK)
    os.execve(args[0], args, {"PATH": "/usr/bin:/usr/sbin:/bin:/sbin", "HOME": "/tmp",
                           "TMPDIR": "/tmp", "LANG": "C", "LC_ALL": "C"})


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError):
        sys.exit("NIB_APPRAISAL_PROBE_REJECTED")
