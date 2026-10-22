#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fixed private EROFS + dm-verity build; no image export or kernel activation."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import runpy
import shutil
import socket
import stat
import struct
import subprocess
import sys
import types
import uuid

ROOTFS = Path("/work/output/rootfs")
WORK = Path("/work/verity-root")
DATA = "root.erofs"
DISK = "root.raw"
HASH = "root.verity.raw"
BLOCK = 4096
MAX_DATA = 1536 << 20
MAX_HASH = 32 << 20
MAX_DISK = MAX_DATA + MAX_HASH
SEED = "1ea2d72f-6bd0-480e-89a3-132f03a4c7e1"
EPOCH = "1792454400"
# systemd-stable v255.4 repart.c:derive_salt/derive_uuid, keyed by SEED.
# These are parameter pins, NOT a replacement for cryptsetup verification.
SALT = "89609461d8139622a4a5e5b22ac0a928626ceac08652e36004266d6c7acc3c16"
VERITY_UUID = "58468b84-9dc4-4d41-b651-ceece5ff3767"
TOOL_FILES = {
    "usr/bin/mkfs.erofs": (163496, "9ad2004dd18dba23f1297a1e360cb79280c174267a99a99e53d926cb776d5180"),
    "usr/bin/fsck.erofs": (68216, "9df25cc289bbd04f1995383aa6a5a235757ef895a6085b26d0b63eb45718a448"),
    "usr/bin/systemd-repart": (199912, "5e731ad1322a7badc6a57001622ddb8ff2b5a665f293a19781f91a86c6db219d"),
}
GUEST_FILES = {
    "usr/sbin/veritysetup": (44992, "95659d771286250ebdb04c831c198711bb8f6b5e569a39e88d35b3f619cd4faf"),
}
VERIFY = ("verify", "reject-root", "verify-final")
OPERATIONS = {"mkfs": 240, "repart": 120, "fsck": 120, "verify": 120, "reject-root": 120, "verify-final": 120}
LOGS = tuple("verity-" + op + "." + stream for op in OPERATIONS for stream in ("stdout", "stderr"))


def require(value):
    if not value:
        raise ValueError("verity-root-rejected")


def module(name):
    return types.SimpleNamespace(**runpy.run_path("/nib/" + name + ".py", run_name="verity_" + name))


def environment():
    # EROFS v1.7.1 SOURCE_DATE_EPOCH overrides -T with timestamp *clamping*.
    # Omit it: the explicit -T below fixes every inode timestamp instead.
    return {"PATH": "/usr/bin:/usr/sbin:/bin:/sbin", "HOME": "/tmp", "TMPDIR": "/tmp",
            "LANG": "C", "LC_ALL": "C", "SYSTEMD_PAGER": "cat", "SYSTEMD_COLORS": "0"}


def verify_tools(reader):
    result = {}
    for label, root, pins in (("tools", Path("/"), TOOL_FILES), ("guest", ROOTFS, GUEST_FILES)):
        require(not os.path.lexists(root / "etc/ld.so.preload"))
        for name, (size, digest) in pins.items():
            raw = reader(root, name, size)
            require(len(raw) == size and hashlib.sha256(raw).hexdigest() == digest)
            result[label + "/" + name] = {"bytes": size, "sha256": digest}
    return result


@contextmanager
def directory(path):
    # Fixed private locations only; reject symlinks in every parent component.
    require(path.is_absolute() and ".." not in path.parts)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def definitions(size):
    require(type(size) is int and BLOCK <= size <= MAX_DATA and size % BLOCK == 0)
    # repart.d v255.4: CopyBlocks is exclusive with Format/CopyFiles. Making
    # EROFS directly avoids repart's extra full installed-root directory copy.
    return {
        "10-root.conf": ("[Partition]\nType=root-x86-64\nLabel=nib-root\n"
            f"CopyBlocks={WORK / DATA}\nSizeMinBytes={size}\nSizeMaxBytes={size}\n"
            "Verity=data\nVerityMatchKey=root\nReadOnly=yes\nSplitName=-\n").encode(),
        "20-verity.conf": ("[Partition]\nType=root-x86-64-verity\nLabel=nib-verity\n"
            "Verity=hash\nVerityMatchKey=root\nVerityDataBlockSizeBytes=4096\n"
            "VerityHashBlockSizeBytes=4096\nMinimize=best\n"
            f"SizeMaxBytes={MAX_HASH}\nReadOnly=yes\nSplitName=verity\n").encode(),
    }


def write_definitions(size):
    with directory(WORK) as fd:
        os.mkdir("definitions", 0o700, dir_fd=fd)
    with directory(WORK / "definitions") as fd:
        for name, raw in definitions(size).items():
            out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            with os.fdopen(out, "wb") as stream:
                stream.write(raw)


def inspect_file(name, limit, *, offset=0, length=None):
    require(name in (DATA, DISK, HASH) and type(offset) is int and offset >= 0)
    with directory(WORK) as fd:
        source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(source, "rb") as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 < info.st_size <= limit
                    and info.st_size % BLOCK == 0)
            count = info.st_size if length is None else length
            require(type(count) is int and 0 < count <= info.st_size - offset)
            stream.seek(offset)
            digest = hashlib.sha256()
            remaining = count
            while remaining:
                raw = stream.read(min(1 << 20, remaining))
                require(raw)
                digest.update(raw)
                remaining -= len(raw)
            after = os.fstat(stream.fileno())
            require((info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                    == (after.st_dev, after.st_ino, after.st_mode, after.st_nlink, after.st_size,
                        after.st_mtime_ns, after.st_ctime_ns))
            return {"bytes": count, "sha256": digest.hexdigest()}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def partition_report(raw, data, hashes, disk, *, require_boot_ids=False):
    require(type(raw) is bytes and 0 < len(raw) <= 65536)
    rows = json.loads(raw, object_pairs_hook=unique_object)
    require(type(rows) is list and len(rows) == 2 and all(type(row) is dict for row in rows))
    root_hash = rows[0].get("roothash")
    require(type(root_hash) is str and re.fullmatch(r"[0-9a-f]{64}", root_hash))
    require(type(require_boot_ids) is bool)
    if require_boot_ids:
        # systemd v255.4 repart assigns these DPS UUIDs after verity generation;
        # its initrd veritysetup-generator uses exactly this discovery scheme.
        require([row.get("uuid") for row in rows]
                == [str(uuid.UUID(hex=root_hash[:32])), str(uuid.UUID(hex=root_hash[32:]))])
    end = BLOCK
    for index, (row, label, kind, identity) in enumerate(zip(rows, ("nib-root", "nib-verity"),
            ("root-x86-64", "root-x86-64-verity"), (data, hashes))):
        require(row.get("label") == label and row.get("type") == kind and row.get("roothash") == root_hash
                and type(row.get("partno")) is int and row["partno"] == index
                and type(row.get("raw_size")) is int and row["raw_size"] == identity["bytes"]
                and type(row.get("offset")) is int and row["offset"] >= end and row["offset"] % BLOCK == 0)
        end = row["offset"] + identity["bytes"]
        require(end <= disk["bytes"] - BLOCK)
        # Verify the private GPT image contains exactly the EROFS and split
        # hash bytes being verified, without parsing or trusting device paths.
        require(inspect_file(DISK, MAX_DISK, offset=row["offset"], length=identity["bytes"]) == identity)
    return root_hash


def superblock(data_size):
    # cryptsetup v2.7.0 lib/verity/verity.c:struct verity_sb. This only checks
    # parameters/coverage. All hash-tree verification is done by veritysetup.
    with directory(WORK) as fd:
        source = os.open(HASH, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(source, "rb") as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and BLOCK <= info.st_size <= MAX_HASH)
            raw = stream.read(512)
    require(len(raw) == 512)
    sig, version, kind, ident, algorithm, db, hb, blocks, size, pad, salt, tail = struct.unpack(
        "<8sII16s32sIIQH6s256s168s", raw)
    require(sig == b"verity\0\0" and version == kind == 1 and ident == uuid.UUID(VERITY_UUID).bytes
            and algorithm == b"sha256" + bytes(26) and db == hb == BLOCK and blocks * BLOCK == data_size
            and size == 32 and salt == bytes.fromhex(SALT) + bytes(224) and pad == bytes(6) and tail == bytes(168))
    return {"hash_algorithm": "sha256", "format": 1, "data_block_bytes": db,
            "hash_block_bytes": hb, "data_blocks": blocks, "salt": SALT, "uuid": VERITY_UUID}


def operation(name, root_hash=None):
    require(name in OPERATIONS)
    if name in VERIFY:
        require(type(root_hash) is str and re.fullmatch(r"[0-9a-f]{64}", root_hash))
        if name == "reject-root":
            root_hash = format(int(root_hash[0], 16) ^ 1, "x") + root_hash[1:]
        return ["/usr/sbin/veritysetup", "verify", "/tmp/" + DATA, "/tmp/" + HASH, root_hash]
    require(root_hash is None)
    if name == "mkfs":
        return ["/usr/bin/mkfs.erofs", "-d", "0", "-b", "4096", "-z", "lz4", "-T", EPOCH,
                "-U", SEED, "-L", "nib-root", str(WORK / DATA), str(ROOTFS)]
    if name == "fsck":
        # Bare --extract checks every compressed file; no extraction directory.
        return ["/usr/bin/fsck.erofs", "--extract", str(WORK / DATA)]
    return ["/usr/bin/systemd-repart", "--offline=yes", "--empty=create", "--size=auto",
            "--dry-run=no", "--discard=no", "--split=yes", "--sector-size=4096",
            "--seed=" + SEED, "--definitions=" + str(WORK / "definitions"),
            "--json=short", str(WORK / DISK)]


def command(name, prefix, root_hash=None):
    operation(name, root_hash)
    command = ["/usr/bin/python3.12", "-I", "-S", "-B", "/nib/mkosi-sandbox.py",
               "--ro-bind", "/", "/", "--ro-bind", str(ROOTFS), str(ROOTFS), "--chdir", "/"]
    if name in ("mkfs", "repart"):
        command += ["--bind", str(WORK), str(WORK), "--tmpfs", "/tmp"]
        if name == "repart":
            command += ["--ro-bind", str(WORK / DATA), str(WORK / DATA),
                        "--ro-bind", str(WORK / "definitions"), str(WORK / "definitions")]
    elif name in VERIFY:
        command += ["--dev", str(ROOTFS / "dev"), "--ro-bind", "/proc", str(ROOTFS / "proc"),
                    "--ro-bind", str(WORK), str(ROOTFS / "tmp")]
    command += [*prefix, "/usr/bin/python3.12", "-I", "-S", "-B", "/nib/verity-root.py", name]
    if root_hash is not None:
        command.append(root_hash)
    return command


def write_log(output, name, stream, raw):
    leaf = "verity-" + name + "." + stream
    require(leaf in LOGS and type(raw) is bytes and len(raw) <= 65536)
    if not raw:
        return
    with directory(output) as fd:
        target = os.open(leaf, os.O_WRONLY | os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=fd)
        with os.fdopen(target, "wb") as file:
            file.write(raw)


def execute(processes, prefix, name, output, root_hash=None):
    try:
        result = processes.execute_process_group(command(name, prefix, root_hash), env=environment(),
            timeout=OPERATIONS[name], max_output_bytes=65536, max_stderr_bytes=65536, stdin=subprocess.DEVNULL)
    except processes.OutputLimitExceeded as error:
        for stream, raw in (("stdout", error.stdout), ("stderr", error.stderr)):
            write_log(output, name, stream, raw)
        raise
    for stream, raw in (("stdout", result.stdout), ("stderr", result.stderr)):
        write_log(output, name, stream, raw)
    if name == "reject-root":
        # Never accept exec failure, a signal, timeout, or a zero exit as the
        # negative control. A good verification runs before and after this.
        # v2.7.0 VERITY_create_or_verify_hash returns -EFAULT for root mismatch;
        # translate_errno maps that to 1, also used for other failures. Require
        # the precise C-locale diagnostic as well as that exit status.
        require(result.returncode == 1 and not result.stdout
                and result.stderr.splitlines().count(b"Verification of root hash failed.") == 1)
    else:
        result.check_returncode()
    return result


def build(processes, prefix, reader, output=Path("/out"), *, require_boot_ids=False):
    require(type(require_boot_ids) is bool)
    identities = verify_tools(reader)
    with directory(ROOTFS):
        pass
    with directory(WORK.parent) as fd:
        os.mkdir(WORK.name, 0o700, dir_fd=fd)  # Collision rejects; never reuse/delete a prior workspace.
    try:
        execute(processes, prefix, "mkfs", output)
        data = inspect_file(DATA, MAX_DATA)
        write_definitions(data["bytes"])
        result = execute(processes, prefix, "repart", output)
        require(set(path.name for path in WORK.iterdir()) == {DATA, DISK, HASH, "definitions"})
        require(inspect_file(DATA, MAX_DATA) == data)
        hashes, disk = inspect_file(HASH, MAX_HASH), inspect_file(DISK, MAX_DISK)
        root_hash = partition_report(result.stdout, data, hashes, disk, require_boot_ids=require_boot_ids)
        parameters = superblock(data["bytes"])
        with directory(WORK) as fd:
            os.unlink(DISK, dir_fd=fd)
        execute(processes, prefix, "fsck", output)
        execute(processes, prefix, "verify", output, root_hash)
        execute(processes, prefix, "reject-root", output, root_hash)
        execute(processes, prefix, "verify-final", output, root_hash)
        require(inspect_file(DATA, MAX_DATA) == data and inspect_file(HASH, MAX_HASH) == hashes
                and verify_tools(reader) == identities)
        report = {"schema": "nib.private-verity-root/v1", "result": "pass", "seed": SEED,
                  "erofs_uuid": SEED, "erofs_timestamp": int(EPOCH), "parameters": parameters,
                  "root_hash": root_hash, "images": {DATA: data, HASH: hashes, DISK: disk}, "tools": identities,
                  "erofs_checked": True, "userspace_verity_verified": True, "wrong_root_rejected": True,
                  "images_exported": False, "boot_verified": False, "kernel_verity_verified": False,
                  "reproducibility_verified": False, "confidentiality_verified": False}
        if require_boot_ids:
            report["boot_partuuids_checked"] = True
    finally:
        # No children survive execute_process_group; all paths are in our new
        # private workspace. Do not suppress cleanup errors or publish success.
        shutil.rmtree(WORK)
    require(not os.path.lexists(WORK))
    report["private_images_removed"] = True
    return report


def main():
    require(sys.platform == "linux" and sys.version_info[:2] == (3, 12)
            and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode
            and len(sys.argv) in (2, 3) and os.geteuid() == os.getegid() == 0
            and socket.if_nameindex() == [(1, "lo")])
    name = sys.argv[1]
    args = operation(name, sys.argv[2] if len(sys.argv) == 3 else None)
    module("confinement").check()
    verify_tools(module("dkms-worker").regular)
    os.umask(0o077)
    limit = MAX_DATA if name == "mkfs" else MAX_DISK if name == "repart" else 0
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if name in VERIFY:
        os.chroot(ROOTFS)
        os.chdir("/")
    os.execve(args[0], args, environment())


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError):
        sys.exit("NIB_VERITY_ROOT_REJECTED")
