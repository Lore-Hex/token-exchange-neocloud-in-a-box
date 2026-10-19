# SPDX-License-Identifier: Apache-2.0
"""Private, transactional export of the image already exercised by the pilot."""

import ctypes
from contextlib import contextmanager
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tarfile
import time
import uuid
import zlib

MAX_ARCHIVE = 2 << 30
MAX_EXPANDED = 4 << 30
MAX_JSON = 1 << 20
TIMEOUT = 300
MODEL_LICENSE_SHA256 = "5de36594c10839788a8c589443a8ef9d8b8d17c65a1b5807206ae037fc36c6bd"
MODEL_LICENSE_REVISION = "23749fefcc72300e3a2ad315e1317431b06b590a"
MODEL_LICENSE_REPOSITORY = "Qwen/Qwen3-0.6B-GGUF"


class ExportError(ValueError):
    """Only fixed, non-sensitive validation reasons may enter evidence."""


def require(condition, reason):
    if not condition:
        raise ExportError(reason)


def digest(stream, limit, deadline):
    hashed, size = hashlib.sha256(), 0
    while chunk := stream.read(1 << 20):
        size += len(chunk)
        require(size <= limit and time.monotonic() < deadline, "export size/time limit")
        hashed.update(chunk)
    return {"sha256": hashed.hexdigest(), "bytes": size}


def safe_name(name):
    path = PurePosixPath(name)
    require(name and name != "." and not path.is_absolute() and ".." not in path.parts
            and path.as_posix() == name and "\\" not in name, "unsafe archive path")
    return name


def validate_archive(stream, identity):
    try:
        return inspect_archive(stream, identity)
    except ExportError:
        raise
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, EOFError,
            tarfile.TarError, zlib.error) as error:
        raise ExportError("invalid or unsupported image archive") from error


def inspect_archive(stream, identity):
    """Check Docker save's config hash and ordered, uncompressed layer DiffIDs.

    No extraction. See moby/docker-image-spec/spec.md (ImageID, DiffID, manifest).
    Accept the classic Docker layout and containerd's content-addressed layout.
    """
    deadline = time.monotonic() + TIMEOUT
    stream.seek(0)
    result = digest(stream, MAX_ARCHIVE, deadline)
    stream.seek(0)
    with tarfile.open(fileobj=stream, mode="r:") as archive:
        members, hashes = {}, {}
        for item in archive:
            name = safe_name(item.name.rstrip("/") if item.isdir() else item.name)
            require(name not in members and len(members) < 4096, "duplicate/excess archive members")
            require(item.isfile() or item.isdir(), "archive links/special files forbidden")
            require(not item.sparse and 0 <= item.size <= MAX_ARCHIVE, "invalid archive member")
            members[name] = item
            if item.isfile():
                with archive.extractfile(item) as data:
                    hashes[name] = digest(data, MAX_ARCHIVE, deadline)
                require(hashes[name]["bytes"] == item.size, "truncated archive member")
                if name.startswith("blobs/sha256/"):
                    require(name == "blobs/sha256/" + hashes[name]["sha256"], "blob digest mismatch")

        def read_json(name):
            safe_name(name)
            require(name in hashes and members[name].size <= MAX_JSON, "missing/oversized archive JSON")
            with archive.extractfile(members[name]) as data:
                return json.load(data)

        manifest = read_json("manifest.json")
        require(isinstance(manifest, list) and len(manifest) == 1, "export must contain one image")
        row = manifest[0]
        require(isinstance(row, dict) and not row.get("RepoTags"), "export must be untagged")
        config = read_json(row["Config"])
        config_id = "sha256:" + hashes[row["Config"]]["sha256"]
        identities = {config_id}
        if "repositories" in members:
            require(not read_json("repositories"), "export repository tags forbidden")
        require(config["os"] == "linux" and config["architecture"] == "amd64", "export platform mismatch")
        require(config["config"]["User"] == "65532:65532", "export user mismatch")
        layers, rootfs = row["Layers"], config["rootfs"]
        require(isinstance(layers, list) and rootfs["type"] == "layers"
                and 0 < len(layers) == len(rootfs["diff_ids"]) <= 128, "export layers mismatch")
        expanded = 0
        for name, expected in zip(layers, rootfs["diff_ids"]):
            safe_name(name)
            require(name in hashes, "missing layer")
            with archive.extractfile(members[name]) as data:
                magic = data.read(2)
                data.seek(0)
                if magic == b"\x1f\x8b":
                    with gzip.GzipFile(fileobj=data) as decoded:
                        actual = digest(decoded, MAX_EXPANDED - expanded, deadline)
                else:
                    actual = digest(data, MAX_EXPANDED - expanded, deadline)
            expanded += actual["bytes"]
            require("sha256:" + actual["sha256"] == expected, "layer DiffID mismatch")
        # Docker's containerd importer may prefer index.json over manifest.json.
        # Bind that alternative entry point to the same single image and layers.
        if "index.json" in members:
            require(read_json("oci-layout") == {"imageLayoutVersion": "1.0.0"}, "OCI layout version")
            index = read_json("index.json")
            for _ in range(3):
                descriptors = index["manifests"]
                require(len(descriptors) == 1, "OCI index must contain one image")
                descriptor = descriptors[0]
                require(re.fullmatch(r"sha256:[0-9a-f]{64}", descriptor["digest"]), "OCI digest")
                annotations = descriptor.get("annotations", {})
                require(not any(key in annotations for key in (
                    "org.opencontainers.image.ref.name", "io.containerd.image.name")), "OCI tags forbidden")
                if _ == 0:
                    identities.add(descriptor["digest"])
                name = "blobs/sha256/" + descriptor["digest"][7:]
                index = read_json(name)
                require(descriptor["size"] == members[name].size, "OCI descriptor size")
                if "manifests" not in index:
                    break
            require(index["config"]["digest"] == config_id, "OCI config mismatch")
            require(index["config"]["size"] == members[row["Config"]].size, "OCI config size")
            require([(entry["digest"], entry["size"]) for entry in index["layers"]] == [
                ("sha256:" + hashes[name]["sha256"], members[name].size) for name in layers
            ], "OCI layers mismatch")
        require(identity in identities, "export image identity mismatch")
    stream.seek(0)
    return {**result, "image_id": identity, "config_id": config_id,
            "platform": "linux/amd64", "layers": len(layers)}


def open_parent(destination):
    require(destination.is_absolute() and ".." not in destination.parts
            and destination.name not in {"", ".", ".."}, "export needs an absolute non-traversing path")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in destination.parent.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            require(not info.st_mode & 0o022 or info.st_mode & stat.S_ISVTX, "unsafe export ancestor")
        info = os.fstat(fd)
        require(info.st_uid == os.getuid() and info.st_mode & 0o077 == 0, "export parent must be owner-only")
        return fd
    except BaseException:
        os.close(fd)
        raise


def rename_exclusive(fd, source, destination):
    # Linux renameat2(RENAME_NOREPLACE); Darwin renameatx_np(RENAME_EXCL).
    # No check-then-rename fallback: ordinary rename can replace an empty directory.
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "linux":
        function, flag = library.renameat2, 1
    elif sys.platform == "darwin":
        function, flag = library.renameatx_np, 4  # SDK sys/stdio.h
    else:
        raise ValueError("exclusive directory rename unsupported")
    function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    function.restype = ctypes.c_int
    if function(fd, os.fsencode(source), fd, os.fsencode(destination), flag):
        raise OSError(ctypes.get_errno(), "exclusive export publication failed")


class Bundle:
    def __init__(self, destination, source, summary):
        self.destination, self.source, self.summary = destination, source, summary
        self.fd = open_parent(destination)
        self.name = ".qwen-export-" + uuid.uuid4().hex
        self.published = False
        self.archive = None
        try:
            try:
                os.stat(destination.name, dir_fd=self.fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError("export destination already exists")
            os.mkdir(self.name, mode=0o700, dir_fd=self.fd)
        except BaseException:
            os.close(self.fd)
            raise
        self.path = destination.parent / self.name
        self.inode = os.stat(self.name, dir_fd=self.fd, follow_symlinks=False).st_ino
        summary["export"] = {"result": "pending", "published": False, "local_only": True}

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        try:
            if kind is not None or not self.published:
                self.abort()
        finally:
            os.close(self.fd)

    def abort(self):
        self.published = False
        try:
            entry = os.stat(self.name, dir_fd=self.fd, follow_symlinks=False)
            require(entry.st_ino == self.inode and stat.S_ISDIR(entry.st_mode), "export cleanup identity changed")
            # dir_fd keeps cleanup anchored even if an ancestor was renamed.
            shutil.rmtree(self.name, dir_fd=self.fd)
            self.summary["export"].update(result="fail", published=False, cleanup="pass")
        except FileNotFoundError:
            self.summary["export"].update(result="fail", published=False, cleanup="pass")
        except (OSError, ValueError):
            self.summary["export"].update(result="fail", cleanup="fail")
            print("Local export cleanup unproven; a private partial bundle may remain.", file=sys.stderr)
            raise

    def check_parent(self):
        fd = open_parent(self.destination)
        try:
            old, current = os.fstat(self.fd), os.fstat(fd)
            require((old.st_dev, old.st_ino) == (current.st_dev, current.st_ino), "export parent changed")
        finally:
            os.close(fd)

    @contextmanager
    def file(self, relative, *, create=False):
        safe_name(relative)
        self.check_parent()
        fd = os.open(self.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
        try:
            require(os.fstat(fd).st_ino == self.inode, "export staging identity changed")
            for part in PurePosixPath(relative).parts[:-1]:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL if create else os.O_RDONLY
            opened = os.open(PurePosixPath(relative).name, flags | os.O_NOFOLLOW | os.O_NONBLOCK,
                             0o600, dir_fd=fd)
            # Docker inherits the raw descriptor for stdin. Avoid a buffered
            # reader's logical offset diverging from that inherited offset.
            with os.fdopen(opened, "wb+" if create else "rb", buffering=0) as stream:
                require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "export requires regular files")
                yield stream
                if create:
                    stream.flush()
                    os.fsync(stream.fileno())
                    os.fsync(fd)
        finally:
            os.close(fd)

    def write(self, relative, data):
        with self.file(relative, create=True) as stream:
            require(stream.write(data) == len(data), "short export write")
        return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

    def licenses(self, root, context, pins):
        require(pins["model"]["revision"] == MODEL_LICENSE_REVISION
                and pins["model"]["repository"] == MODEL_LICENSE_REPOSITORY, "model license revision/repository changed")
        result = {}
        for label, directory in (("nib", root), ("trcs", context / "trcs"), ("llama", context / "engine")):
            paths = [directory / "LICENSE"]
            paths += ([directory / "NOTICE"] if (directory / "NOTICE").exists() else [])
            if label != "nib":
                paths += [path for path in directory.rglob("*") if path.parent != directory
                          and path.is_file() and re.fullmatch(r"(?:LICENSE|NOTICE)(?:[.-].*)?", path.name)]
            require(len(paths) <= 128, "excess license files")
            for path in paths:
                require(not path.is_symlink() and path.is_file() and path.stat().st_size <= MAX_JSON,
                        "invalid license file")
                name = "licenses/" + label + "/" + path.relative_to(directory).as_posix()
                result[name] = self.write(name, path.read_bytes())
        model_license = root / "third_party/qwen3-0.6b/LICENSE"
        require(not model_license.is_symlink() and model_license.stat().st_size <= MAX_JSON,
                "invalid model license")
        data = model_license.read_bytes()
        require(hashlib.sha256(data).hexdigest() == MODEL_LICENSE_SHA256, "model license digest")
        result["licenses/qwen/LICENSE"] = self.write("licenses/qwen/LICENSE", data)
        provenance = root / "third_party/qwen3-0.6b/README.md"
        require(not provenance.is_symlink() and provenance.stat().st_size <= MAX_JSON,
                "invalid model license provenance")
        result["licenses/qwen/README.md"] = self.write("licenses/qwen/README.md", provenance.read_bytes())
        return result

    def save(self, identity, root, context, pins, execute, env):
        self.check_parent()
        self.summary["export"]["stage"] = "licenses"
        self.license_files = self.licenses(root, context, pins)
        with self.file("image.tar", create=True) as stream:
            self.summary["export"]["stage"] = "save"
            execute(["docker", "image", "save", identity], env=env, stdout_file=stream,
                    max_output_bytes=MAX_ARCHIVE, timeout=TIMEOUT).check_returncode()
            stream.flush()
            os.fsync(stream.fileno())
            self.summary["export"]["stage"] = "archive-validation"
            try:
                self.archive = validate_archive(stream, identity)
            except ExportError as error:
                self.summary["export"]["failure_code"] = str(error)
                raise
            # Load the validated untagged archive while our build tag still owns
            # the image. Never remove unrelated images or create a deployment tag.
            self.summary["export"]["load_started"] = True
            self.summary["export"]["stage"] = "load"
            execute(["docker", "image", "load"], env=env, stdin=stream,
                    max_output_bytes=65536, timeout=TIMEOUT).check_returncode()
            self.summary["export"]["load_completed"] = True
            self.summary["export"]["stage"] = "inspect"
            inspected = execute(["docker", "image", "inspect", "--format", "{{.Id}} {{.Os}}/{{.Architecture}}",
                                 identity], env=env, max_output_bytes=65536, timeout=30)
            inspected.check_returncode()
            require(inspected.stdout.strip() == (identity + " linux/amd64").encode(), "loaded image mismatch")
            require(validate_archive(stream, identity) == self.archive, "archive changed during load")
        self.summary["export"].update(result="verified-pending-cleanup", stage="verified", archive=self.archive,
                                      load_checked=True)

    def publish(self):
        require(self.summary["result"] == "pass" and self.summary["container_cleanup"]["result"] == "pass"
                and self.archive is not None and self.summary["export"].get("load_checked") is True,
                "export requires proof, load verification and cleanup PASS")
        self.check_parent()
        with self.file("image.tar") as stream:
            require(validate_archive(stream, self.archive["image_id"]) == self.archive, "staged archive changed")
        for name, expected in self.license_files.items():
            with self.file(name) as stream:
                require(digest(stream, MAX_JSON, time.monotonic() + TIMEOUT) == expected,
                        "staged license changed")
        manifest = {"schema": "nib.qwen-image-bundle/v1", "source": self.source,
                    "archive": {"file": "image.tar", **self.archive},
                    "inputs": self.summary["container_inputs"], "proof": self.summary["runtime"],
                    "cleanup": self.summary["container_cleanup"], "licenses": self.license_files,
                    "local_only": True, "confidential": False, "public_endpoint": False,
                    "load_checked": True}
        self.write("manifest.json", (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
        require(os.stat(self.name, dir_fd=self.fd, follow_symlinks=False).st_ino == self.inode,
                "export staging identity changed")
        rename_exclusive(self.fd, self.name, self.destination.name)
        self.name = self.destination.name
        self.published = True
        os.fsync(self.fd)
        self.summary["export"].update(result="pass", published=True, cleanup="pass")
