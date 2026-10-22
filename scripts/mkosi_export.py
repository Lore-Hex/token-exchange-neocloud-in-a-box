# SPDX-License-Identifier: Apache-2.0
"""Fixed private boot artifacts; staging is not publication or boot validation."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import time

FILES = {"root.raw": 1568 << 20, "nib.efi": 256 << 20}
STAGE = Path("/out/boot-stage")
TIMEOUT = 120
MAX_JSON = 65536


def require(value):
    if not value:
        raise ValueError("private-boot-export-rejected")


def encoded(value):
    raw = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    require(len(raw) <= MAX_JSON)
    return raw


def unique(pairs):
    result = {}
    for name, value in pairs:
        require(name not in result)
        result[name] = value
    return result


def parsed(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_JSON)
    return json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: require(False))


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def file_identity(value, limit):
    require(type(value) is dict and set(value) == {"bytes", "sha256"}
            and type(value["bytes"]) is int and 0 < value["bytes"] <= limit
            and type(value["sha256"]) is str and re.fullmatch("[0-9a-f]{64}", value["sha256"]))


def transfer(source, expected, limit, deadline, directory, target=None):
    """Descriptor-anchored full copy/hash, then rehash the actual destination.

    Uses the existing directory walker. The bounded streaming/short-write loop
    follows cache-public-weights.copy_pack; no hard links, reflinks or regeneration.
    """
    file_identity(expected, limit)
    with directory(source.parent) as parent:
        fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb", buffering=0) as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                    and before.st_uid == os.getuid() and before.st_size == expected["bytes"])
            digest, count = hashlib.sha256(), 0
            while True:
                require(time.monotonic() < deadline)
                raw = stream.read(min(1 << 20, expected["bytes"] + 1 - count))
                if not raw:
                    break
                count += len(raw)
                require(count <= expected["bytes"])
                digest.update(raw)
                if target is not None:
                    view = memoryview(raw)
                    while view:
                        require(time.monotonic() < deadline)
                        written = target.write(view)
                        require(type(written) is int and 0 < written <= len(view))
                        view = view[written:]
            require(count == expected["bytes"] and digest.hexdigest() == expected["sha256"]
                    and stamp(before) == stamp(os.fstat(stream.fileno()))
                    and stamp(before) == stamp(os.stat(source.name, dir_fd=parent, follow_symlinks=False)))


def copy(source, destination, expected, limit, deadline, directory):
    with directory(destination.parent) as parent:
        fd = os.open(destination.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, "wb", buffering=0) as stream:
            transfer(source, expected, limit, deadline, directory, stream)
            os.fsync(stream.fileno())
    transfer(destination, expected, limit, deadline, directory)


@contextmanager
def owned(path, directory, remove):
    """Only remove the fresh directory this context created, never a collision."""
    with directory(path.parent) as parent:
        os.mkdir(path.name, 0o700, dir_fd=parent)
        fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        original = os.fstat(fd)
        try:
            yield
        except BaseException:
            remove(parent, path.name, original)
            raise
        finally:
            os.close(fd)


def source_identity(value):
    require(type(value) is dict and set(value) == {"git_sha", "sources"}
            and type(value["git_sha"]) is str and re.fullmatch("[0-9a-f]{40}", value["git_sha"])
            and type(value["sources"]) is dict and 0 < len(value["sources"]) <= 42)
    for name, digest in value["sources"].items():
        require(type(name) is str and re.fullmatch(r"[A-Za-z0-9_./-]+", name)
                and not name.startswith("/") and all(p not in ("", ".", "..") for p in name.split("/"))
                and type(digest) is str and re.fullmatch("[0-9a-f]{64}", digest))


def stage(verity, boot, source, directory):
    source_identity(source)
    require(verity["root_hash"] == boot["root_hash"] and verity["boot_partuuids_checked"] is True
            and verity["result"] == boot["result"] == "pass" and boot["sections_checked"] is True)
    files = {"root.raw": verity["images"]["root.raw"], "nib.efi": boot["uki"]["image"]}
    deadline = time.monotonic() + TIMEOUT
    for name, path in (("root.raw", Path("/work/verity-root/root.raw")),
                       ("nib.efi", Path("/work/boot-uki/nib.efi"))):
        copy(path, STAGE / name, files[name], FILES[name], deadline, directory)
    return {"schema": "nib.boot-artifact-stage/v1", "source": source,
            "root_hash": verity["root_hash"], "files": files, "published": False}


def paths(evidence):
    require(evidence.is_absolute() and ".." not in evidence.parts and evidence.name not in ("", ".", ".."))
    return (evidence.with_name("." + evidence.name + "-boot-pending"),
            evidence.with_name(evidence.name + "-boot"))


def validate_proof(report, source):
    source_identity(source)
    require(report["schema"] == "nib.mkosi-installation/v1" and report["result"] == "pass"
            and report["stage"] == "complete" and report["git_sha"] == source["git_sha"]
            and report["payload_removed"] is True and report["process_cleanup_confirmed"] is True
            and report["bootable"] is False and report["deployable"] is False)
    worker = report["worker"]
    require(worker["schema"] == "nib.mkosi-install-worker/v1" and worker["result"] == "pass"
            and worker["stage"] == "boot-uki" and worker["bootable"] is False and worker["deployable"] is False
            and worker["configured_state_verified"] is True and type(worker["installed_packages"]) is int
            and worker["installed_packages"] == 212
            and worker["dkms_built"] is True and worker["driver_build"]["result"] == "pass"
            and worker["driver_build"]["installed"] is True and worker["runtime_policy"]["result"] == "pass"
            and worker["appraisal_cli"]["result"] == "pass")
    verity, boot, staged = worker["verity_root"], worker["boot_uki"], worker["artifact_stage"]
    require(verity["schema"] == "nib.private-verity-root/v1" and boot["schema"] == "nib.private-boot-uki/v1"
            and verity["result"] == boot["result"] == "pass"
            and all(verity[k] is True for k in ("erofs_checked", "userspace_verity_verified",
                "wrong_root_rejected", "boot_partuuids_checked", "private_images_removed"))
            and all(boot[k] is True for k in ("archive_checked", "sections_checked", "private_images_removed"))
            and all(verity[k] is False for k in ("images_exported", "boot_verified", "kernel_verity_verified",
                "reproducibility_verified", "confidentiality_verified"))
            and all(boot[k] is False for k in ("signed", "images_exported", "boot_verified",
                "reproducibility_verified", "confidentiality_verified")))
    require(type(verity["root_hash"]) is str and re.fullmatch("[0-9a-f]{64}", verity["root_hash"])
            and staged == {"schema": "nib.boot-artifact-stage/v1", "source": source,
                "root_hash": verity["root_hash"], "files": {
                    "root.raw": verity["images"]["root.raw"], "nib.efi": boot["uki"]["image"]}, "published": False}
            and staged["published"] is False and boot["root_hash"] == verity["root_hash"])
    for name, row in staged["files"].items():
        file_identity(row, FILES[name])
    return staged


class Pending:
    def __init__(self, evidence, base, publication):
        self.path, self.destination = paths(evidence)
        self.base, self.publication, self.kept = base, publication, False

    def __enter__(self):
        self.fd = self.publication.open_parent(self.destination)
        try:
            for name in (self.path.name, self.destination.name):
                try:
                    os.stat(name, dir_fd=self.fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise ValueError("private-boot-export-collision")
            os.mkdir(self.path.name, 0o700, dir_fd=self.fd)
            self.original = os.stat(self.path.name, dir_fd=self.fd, follow_symlinks=False)
        except BaseException:
            os.close(self.fd)
            raise
        return self

    def __exit__(self, kind, value, traceback):
        try:
            if kind is not None or not self.kept:
                self.base.inputs.remove_flat_directory(self.fd, self.path.name, self.original)
        finally:
            os.close(self.fd)

    def capture(self, source, staged):
        require(set(staged["files"]) == set(FILES))
        with self.base.directory(source) as fd:
            require(set(os.listdir(fd)) == set(FILES))
        deadline = time.monotonic() + TIMEOUT
        for name, limit in FILES.items():
            copy(source / name, self.path / name, staged["files"][name], limit, deadline, self.base.directory)

    def validate(self, report):
        validate_proof(report, report["worker"]["artifact_stage"]["source"])

    def accept(self, report, evidence):
        self.validate(report)
        # Bind to the exact published generation receipts, not just their fields.
        rows = {name: self.base.digest(self.base.read(evidence / name, MAX_JSON))
                for name in ("run.json", "result.json")}
        with self.base.directory(self.path) as fd:
            out = os.open("staging.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            with os.fdopen(out, "wb") as stream:
                raw = encoded(rows)
                require(stream.write(raw) == len(raw))
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(fd)
        self.kept = True


@contextmanager
def deadline_alarm():
    def expired(signum, frame):
        raise ValueError("private-boot-export-timeout")
    require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0))
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, TIMEOUT)
    try:
        yield time.monotonic() + TIMEOUT
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def validate_resources(resources):
    require(resources["schema"] == "nib.mkosi-build-resources/v1" and resources["result"] == "pass"
            and type(resources["child_exit_code"]) is int and resources["child_exit_code"] == 0
            and type(resources["oom_kill"]) is int and resources["oom_kill"] == 0
            and all(resources[k] is True for k in ("limits_read_back", "empty_verified", "group_removed"))
            and resources["limits"] == {"memory.max": str(8 << 30), "memory.swap.max": "0",
                "memory.oom.group": "1", "pids.max": "256", "cpu.max": "200000 100000"})


def finish(evidence, base, publication, source):
    """Called only as the unprivileged runner, after the root receipt is durable."""
    pending, destination = paths(evidence)
    parent = publication.open_parent(destination)
    name, original = pending.name, None
    try:
        original = os.stat(name, dir_fd=parent, follow_symlinks=False)
        require(stat.S_ISDIR(original.st_mode) and stat.S_IMODE(original.st_mode) == 0o700
                and original.st_uid == os.getuid())
        with deadline_alarm() as deadline, base.directory(pending) as fd:
            require((os.fstat(fd).st_dev, os.fstat(fd).st_ino) == (original.st_dev, original.st_ino)
                    and set(os.listdir(fd)) == set(FILES) | {"staging.json"})
            hashes = parsed(base.read(pending / "staging.json", MAX_JSON))
            receipts = {leaf: base.read(evidence / leaf, MAX_JSON) for leaf in ("run.json", "result.json")}
            require(hashes == {leaf: base.digest(raw) for leaf, raw in receipts.items()})
            staged = validate_proof(parsed(receipts["result.json"]), source)
            run = parsed(receipts["run.json"])
            require(run["result"] == "pass" and run["git_sha"] == source["git_sha"]
                    and run["hardware_validated"] is False)
            receipts["resources.json"] = base.read(evidence.with_name(evidence.name + "-resources.json"), MAX_JSON)
            resources = parsed(receipts["resources.json"])
            validate_resources(resources)
            for leaf, limit in FILES.items():
                info = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
                require(stat.S_IMODE(info.st_mode) == 0o600)
                transfer(pending / leaf, staged["files"][leaf], limit, deadline, base.directory)
            manifest = {"schema": "nib.private-boot-bundle/v1", "source": source,
                "root_hash": staged["root_hash"], "files": staged["files"],
                "receipts": {leaf: {"bytes": len(raw), "sha256": base.digest(raw)} for leaf, raw in receipts.items()},
                "local_only": True, "signed": False, "boot_verified": False,
                "reproducibility_verified": False, "confidentiality_verified": False}
            for leaf, raw in {**receipts, "manifest.json": encoded(manifest)}.items():
                target = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                with os.fdopen(target, "wb") as stream:
                    require(stream.write(raw) == len(raw))
                    stream.flush()
                    os.fsync(stream.fileno())
            os.unlink("staging.json", dir_fd=fd)
            os.fsync(fd)
            require(time.monotonic() < deadline)
            check = publication.open_parent(destination)
            try:
                require((os.fstat(check).st_dev, os.fstat(check).st_ino)
                        == (os.fstat(parent).st_dev, os.fstat(parent).st_ino))
            finally:
                os.close(check)
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            require((current.st_dev, current.st_ino) == (original.st_dev, original.st_ino))
            publication.rename_exclusive(parent, name, destination.name)
            name = destination.name
            os.fsync(parent)
    except BaseException:
        if original is not None and stat.S_ISDIR(original.st_mode) and original.st_uid == os.getuid():
            base.inputs.remove_flat_directory(parent, name, original)
        raise
    finally:
        os.close(parent)
    return destination
