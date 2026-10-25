#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare only the explicitly identified disposable CPU venue; never run Docker."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        sys.exit("Use python3 -I -S -B host/prepare-selected-cpu.py")
    sys.path.insert(0, str(ROOT / "scripts"))

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import os
import platform
import pwd
import re
import shutil
import signal
import stat
import subprocess
import tarfile
import tempfile
import time

from e2e import snapshot

SOURCES = ("host/prepare-selected-cpu.py", "host/selected-cpu-bootstrap-pins.json", "scripts/e2e/snapshot.py")
PINS = json.loads((ROOT / SOURCES[1]).read_bytes())
PREFIX = Path("/opt/nib-selected-nat-tools")
BIN = Path("/usr/bin")
RESOLVER = Path("/etc/resolv.conf")
RESOLVER_TARGETS = ("/run/systemd/resolve/stub-resolv.conf", "/run/systemd/resolve/resolv.conf")
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C",
       "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_NO_REPLACE_OBJECTS": "1",
       "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}
BUDGET = 600


def require(condition):
    if not condition:
        raise ValueError("cpu-preparation-rejected")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= limit)
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        require(len(raw) == before.st_size and identity(before) == identity(after))
    return raw


def secure(path, owners=(0,)):
    require(path.is_absolute() and ".." not in path.parts)
    for part in (path, *path.parents):
        info = part.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in owners and not info.st_mode & 0o022)


@contextmanager
def cancellation():
    state = {"cancelled": False}
    def cancel(_sig, _frame):
        state["cancelled"] = True
    old = {sig: signal.signal(sig, cancel) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield state
    finally:
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, set(old))
        try:
            for sig, handler in old.items():
                signal.signal(sig, handler)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)


class Commands:
    def __init__(self, cancelled):
        self.end = time.monotonic() + BUDGET
        self.cancelled = cancelled

    def remaining(self):
        remaining = self.end - time.monotonic()
        require(remaining > 25 and not self.cancelled["cancelled"])
        return remaining - 25

    def run(self, argv, *, cap=65536, output=None):
        result = snapshot.execute_process_group(argv, env=ENV, timeout=min(120, self.remaining()),
            stdin=subprocess.DEVNULL, max_output_bytes=cap, stdout_file=output)
        result.check_returncode()
        self.remaining()
        return result.stdout


def source_identity(commands):
    def git(*args):
        return commands.run(["git", "-C", str(ROOT), *args], cap=2 << 20)
    revision = git("rev-parse", "HEAD").decode().strip()
    require(re.fullmatch("[0-9a-f]{40}", revision))
    sources = {}
    for name in SOURCES:
        raw = read(ROOT / name, 2 << 20)
        require(raw == git("show", revision + ":" + name))
        sources[name] = sha(raw)
    require(json.loads(read(ROOT / SOURCES[1], 65536)) == PINS)
    return {"revision": revision, "sources": sources}


def artifacts():
    return [PINS["python"], PINS["docker"], *PINS["ubuntu"]]


def verify_inputs(inputs):
    data = {}
    for pin in artifacts():
        raw = read(inputs / pin["file"], pin["size"])
        require(len(raw) == pin["size"] and sha(raw) == pin["sha256"])
        data[pin["file"]] = raw
    return data


def fetch(inputs, commands):
    inputs.mkdir(mode=0o700)
    for pin in artifacts():
        commands.remaining()
        with tempfile.TemporaryFile(dir=inputs) as stream:
            commands.run(["/usr/bin/curl", "--fail", "--location", "--silent", "--show-error",
                          "--proto", "=https", "--proto-redir", "=https", "--max-time", "120",
                          pin["url"]], cap=pin["size"], output=stream)
            stream.seek(0)
            raw = stream.read(pin["size"] + 1)
            require(len(raw) == pin["size"] and sha(raw) == pin["sha256"])
            write_new(inputs / pin["file"], raw, 0o400)


def write_new(path, raw, mode):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as output:
        output.write(raw)
        output.flush()
        os.fchmod(output.fileno(), mode)
        os.fsync(output.fileno())


def platform_check(machine_hash):
    require(os.geteuid() == 0 and platform.system() == "Linux" and platform.machine() == "x86_64"
            and sys.version_info[:2] == (3, 12))
    require(re.fullmatch("[0-9a-f]{64}", machine_hash or "")
            and sha(read(Path("/etc/machine-id"), 128)) == machine_hash)
    release = platform.freedesktop_os_release()
    require(release.get("ID") == "ubuntu" and release.get("VERSION_ID") == "24.04")
    require(Path("/sys/fs/cgroup/cgroup.controllers").is_file())
    secure(BIN)
    secure(PREFIX.parent)
    require(not os.path.lexists(PREFIX))
    for name in ("/etc/docker", "/var/lib/docker", "/var/lib/containerd", "/run/docker.sock",
                 "/run/containerd", "/etc/containerd"):
        require(not os.path.lexists(name))
    for name in PINS["docker"]["binaries"]:
        for directory in ("/usr/sbin", "/usr/bin", "/sbin", "/bin", "/usr/local/bin", "/usr/local/sbin"):
            require(not os.path.lexists(Path(directory) / name))


def ubuntu_tools(data, commands, report):
    observed = {}
    for pin in PINS["ubuntu"]:
        report["package"] = pin["package"]
        version = commands.run(["/usr/bin/dpkg-query", "-W", "-f=${Status}\t${Version}", pin["package"]])
        require(version.decode() == "install ok installed\t" + pin["version"])
        with tempfile.TemporaryDirectory(prefix="nib-cpu-deb-") as directory:
            sealed = Path(directory) / "input.deb"
            write_new(sealed, data[pin["file"]], 0o400)
            unpacked = commands.run(["/usr/bin/dpkg-deb", "--fsys-tarfile", str(sealed)], cap=24 << 20)
        with tarfile.open(fileobj=io.BytesIO(unpacked), mode="r:") as archive:
            candidates = [m for m in archive if m.isfile() and Path(m.name).name == pin["binary"]
                          and Path(m.name).parent.as_posix() in ("usr/bin", "usr/sbin", "bin", "sbin")]
            require(len(candidates) == 1 and 0 < candidates[0].size < 16 << 20)
            expected = archive.extractfile(candidates[0]).read()
        binary = shutil.which(pin["binary"], path=ENV["PATH"])
        require(binary is not None)
        binary = Path(binary).resolve(strict=True)
        secure(binary.parent)
        info = binary.lstat()
        require(info.st_uid == 0 and not info.st_mode & 0o022)
        require(read(binary, 16 << 20) == expected)
        observed[pin["binary"]] = {"package": pin["package"], "version": pin["version"], "sha256": sha(expected)}
    for name in ("iptables", "ip6tables"):
        binary = shutil.which(name, path=ENV["PATH"])
        require(binary is not None and sha(read(Path(binary).resolve(strict=True), 16 << 20))
                == observed["xtables-nft-multi"]["sha256"])
    require(commands.run(["nft", "--version"]).strip() == b"nftables v1.0.9 (Old Doc Yak #3)")
    require(commands.run(["unshare", "--version"]).strip() == b"unshare from util-linux 2.39.3")
    del report["package"]
    return observed


def resolver_input():
    secure(RESOLVER.parent)
    original = RESOLVER.lstat()
    if stat.S_ISREG(original.st_mode):
        require(original.st_uid == 0 and not original.st_mode & 0o022)
        return {"link": None, "raw": read(RESOLVER, 16384), "stat": identity(original)}
    require(stat.S_ISLNK(original.st_mode) and original.st_uid == 0)
    link = os.readlink(RESOLVER)
    target = RESOLVER.resolve(strict=True)
    require(str(target) in RESOLVER_TARGETS)
    owner = pwd.getpwnam("systemd-resolve").pw_uid
    secure(target.parent, (0, owner))
    info = target.lstat()
    require(info.st_uid in (0, owner) and not info.st_mode & 0o022)
    return {"link": link, "raw": read(target, 16384), "stat": identity(original)}


def regular_resolver(before):
    current = resolver_input()
    require(current == before)
    if before["link"] is None:
        return False
    # Keep the exact original link and resolver bytes private for operator recovery.
    write_new(PREFIX / "resolver-link", before["link"].encode(), 0o600)
    write_new(PREFIX / "resolver-bytes", before["raw"], 0o600)
    fd, temporary = tempfile.mkstemp(prefix=".nib-resolver-", dir=RESOLVER.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(before["raw"])
            output.flush()
            os.fchmod(output.fileno(), 0o644)
            os.fsync(output.fileno())
        require(resolver_input() == before)
        os.replace(temporary, RESOLVER)
        require(not RESOLVER.is_symlink() and read(RESOLVER, 16384) == before["raw"])
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)
    return True


def extract(raw, root, prefix, commands):
    count, total = 0, 0
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for member in archive:
            commands.remaining()
            count += 1
            total += member.size
            path = Path(member.name)
            require(count <= 6000 and total <= 350 << 20 and member.size <= 100 << 20
                    and not path.is_absolute() and ".." not in path.parts and path.parts[0] == prefix
                    and member.name not in seen and not member.islnk() and not member.issparse()
                    and (member.isfile() or member.isdir() or member.issym()))
            seen.add(member.name)
            archive.extract(member, path=root, filter="data")


def install(data, before, commands):
    PREFIX.mkdir(mode=0o700)
    for key in ("python", "docker"):
        extract(data[PINS[key]["file"]], PREFIX, key, commands)
    python = PREFIX / "python/bin/python3.12"
    require(sha(read(python, 40 << 20)) == PINS["python"]["binary_sha256"])
    for name, digest in PINS["docker"]["binaries"].items():
        path = PREFIX / "docker" / name
        require(sha(read(path, 100 << 20)) == digest)
        path.chmod(0o755)
    require({p.name for p in (PREFIX / "docker").iterdir()} == set(PINS["docker"]["binaries"]))
    require(commands.run([str(python), "-I", "-S", "-B", "-c",
                         "import sys,ssl,hashlib,tarfile; assert sys.version_info[:3]==(3,12,13); print('ok')"]).strip() == b"ok")
    require(commands.run([str(PREFIX / "docker/dockerd"), "--version"]).startswith(b"Docker version 29.1.3,"))
    for name in PINS["docker"]["binaries"]:
        commands.remaining()
        # Atomic no-replace publication; never overwrite distro or operator tools.
        os.link(PREFIX / "docker" / name, BIN / name)
    commands.remaining()
    changed = regular_resolver(before)
    commands.remaining()
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--download", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--install", action="store_true")
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--machine-id-sha256")
    parser.add_argument("--regular-resolver", action="store_true")
    args = parser.parse_args()
    report = {"result": "fail", "stage": "source", "installed": False, "mutation_started": False,
              "namespace_proof_executed": False, "venue_qualified": False, "daemon_started": False,
              "started_at": datetime.now(timezone.utc).isoformat()}
    try:
        with cancellation() as cancelled:
            commands = Commands(cancelled)
            report.update(source_identity(commands))
            require(args.inputs.is_absolute() and ".." not in args.inputs.parts)
            if args.download:
                report["stage"] = "download"
                fetch(args.inputs, commands)
            else:
                report["stage"] = "host"
                platform_check(args.machine_id_sha256)
                secure(args.inputs)
                report["stage"] = "inputs"
                data = verify_inputs(args.inputs)
                report["stage"] = "ubuntu-tools"
                report["ubuntu_tools"] = ubuntu_tools(data, commands, report)
                report["stage"] = "resolver"
                before = resolver_input()
                require(before["link"] is None or args.regular_resolver)
                report["kernel"] = platform.release()
                if args.install:
                    report["stage"] = "install"
                    commands.remaining()
                    report["mutation_started"] = True
                    report["resolver_changed"] = install(data, before, commands)
                    report["installed"] = True
            commands.remaining()
            report.update(result="pass", stage="complete")
    except (OSError, ValueError, KeyError, KeyboardInterrupt, tarfile.TarError, subprocess.SubprocessError):
        pass
    report["ended_at"] = datetime.now(timezone.utc).isoformat()
    print(json.dumps(report, sort_keys=True))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
