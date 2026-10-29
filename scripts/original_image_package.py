# SPDX-License-Identifier: Apache-2.0
"""Offline original-image input packaging; no image access, transport or admission."""

from contextlib import ExitStack
import hashlib
import io
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import time
import types

ROOT = Path(__file__).absolute().parents[1]
SELF = "scripts/original_image_package.py"
ADAPTER = "host/receive-original-image-inputs.py"
ADAPTER_SHA = "4141ad7b9bb778507ee5f205de619e75237233bd89eef72fd6030bd0cc620fde"
SCHEMA = "nib.original-image-package/v1"
ROLES = ("source-tree", "source", "preparation-tree", "preparation", "build", "verification")


def require(value):
    if not value:
        raise ValueError("original-image-package-rejected")


def helpers():
    with (ROOT / ADAPTER).open("rb") as stream:
        raw = stream.read((1 << 20) + 1)
    require(hashlib.sha256(raw).hexdigest() == ADAPTER_SHA)
    adapter = types.ModuleType("original_package_adapter")
    adapter.__file__ = str(ROOT / ADAPTER)
    exec(compile(raw, adapter.__file__, "exec", dont_inherit=True), adapter.__dict__)
    files, receiver, materializer = adapter.helpers()
    runner = files.load_pinned(adapter.RUNNER, adapter.PINS[adapter.RUNNER])
    return adapter, files, receiver, materializer, runner


def git(repository, args, limit, deadline, adapter, materializer, runner):
    env = {**materializer.ENV, "PATH": runner.TOOL_PATH}
    result = runner.execute_process_group(["git", "-c", "core.hooksPath=/dev/null",
        "-c", "core.attributesFile=/dev/null", "-c", "gc.auto=0", "-c", "maintenance.auto=false",
        "-C", str(repository), *args], env=env, timeout=adapter.check(deadline),
        stdin=subprocess.DEVNULL, max_output_bytes=max(1, limit), max_stderr_bytes=4096)
    adapter.check(deadline)
    require(result.returncode == 0 and result.stderr == b"" and len(result.stdout) <= limit)
    return result.stdout


def code_identity(deadline, adapter, files, materializer, runner):
    producer = git(ROOT, ["rev-parse", "--verify", "HEAD^{commit}"], 41,
                   deadline, adapter, materializer, runner).decode("ascii").strip()
    adapter.hex_value(producer, 40)
    raw = git(ROOT, ["show", producer + ":" + SELF], 1 << 20,
              deadline, adapter, materializer, runner)
    require(raw == files.read(ROOT / SELF, 1 << 20))
    return {"producer": producer, "script_sha256": adapter.sha(raw)}


def canonical_path(value):
    path = Path(value)
    require(path.is_absolute() and str(path) == os.path.normpath(path) and ".." not in path.parts)
    return path


def inventory(rows, materializer):
    # Let the existing inventory parser enforce all names, modes, counts and sizes.
    raw = b"".join(f"{mode} blob {oid} {size}\t{name}\0".encode("ascii")
                   for name, (mode, oid, size) in sorted(rows.items()))
    require(len(raw) <= 1 << 20)
    materializer.expected_tree(raw)
    return raw


def preparation_files(path, expected, files, materializer, runner, adapter, deadline):
    root = files.directory(path, private=True)
    try:
        identity = adapter.directory_identity(os.fstat(root))
        raw = files.read(path / "receipt.json", materializer.MAX_BUNDLE, private=True)
        require(files.sha(raw) == expected)
        receipt = files.strict_json(raw)
        require(type(receipt) is dict and type(receipt.get("files")) is dict
                and "receipt.json" not in receipt["files"]
                and 0 < len(receipt["files"]) < materializer.MAX_FILES)
        rows = {"receipt.json": ("100644", runner.blob_id(raw), len(raw))}
        for name, row in receipt["files"].items():
            require(type(name) is str and type(row) is dict and type(row.get("bytes")) is int)
            rows[name] = ("100644", "0" * 40, row["bytes"])
        inventory(rows, materializer)
        adapter.check(deadline)
        with files.preparation_helper() as image:
            observed, data, _ = image.validated_files(path, expected)
        adapter.check(deadline)
        require(observed == receipt and observed["git_sha"] == adapter.IMAGE_PRODUCER)
        with ExitStack() as stack:
            current = files.directory(path, private=True)
            stack.callback(os.close, current)
            require(adapter.directory_identity(os.fstat(current)) == identity)
        require(files.read(path / "receipt.json", materializer.MAX_BUNDLE, private=True) == raw)
        data["receipt.json"] = raw
    finally:
        os.close(root)
    adapter.check(deadline)
    return data


class BoundedOutput:
    """Only a counted tar/file sink; the materializer remains the archive parser."""

    def __init__(self, stream, limit, deadline, adapter):
        self.stream, self.limit, self.deadline, self.adapter = stream, limit, deadline, adapter
        self.count, self.digest = 0, hashlib.sha256()

    def tell(self):
        return self.count

    def write(self, raw):
        self.adapter.check(self.deadline)
        require(self.count + len(raw) <= self.limit)
        require(self.stream.write(raw) == len(raw))
        self.count += len(raw)
        self.digest.update(raw)
        self.adapter.check(self.deadline)
        return len(raw)


def tar_into(output, tree, read, adapter, runner, deadline):
    # Fixed metadata, sorted regular files only. PAX supports the existing 512-byte
    # path contract without introducing an extractor or shortening its limit.
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, (mode, oid, size) in sorted(tree.items()):
            adapter.check(deadline)
            raw = read(name, oid, size)
            require(type(raw) is bytes and len(raw) == size and runner.blob_id(raw) == oid)
            adapter.check(deadline)
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode = size, int(mode, 8) & 0o777
            entry.uid = entry.gid = entry.mtime = 0
            entry.uname = entry.gname = ""
            archive.addfile(entry, io.BytesIO(raw))
    adapter.check(deadline)


def component(root, root_fd, role, write, deadline, adapter, files, receiver):
    require(role in ROLES)
    adapter.check(deadline)
    os.mkdir(role, 0o700, dir_fd=root_fd)
    with ExitStack() as stack:
        parent = os.open(role, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        stack.callback(os.close, parent)
        require(adapter.directory_identity(os.fstat(parent))[2:] == [os.getuid(), 0o700])
        os.fsync(root_fd)
        adapter.check(deadline)
        fd = os.open("payload", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        stream = stack.enter_context(os.fdopen(fd, "wb"))
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600)
        output = BoundedOutput(stream, adapter.CAPS[role], deadline, adapter)
        write(output)
        require(output.count > 0)
        stream.flush()
        os.fsync(stream.fileno())
        os.fsync(parent)
        adapter.check(deadline)
        row = {"bytes": output.count, "sha256": output.digest.hexdigest()}
        identity = files.identity(os.fstat(stream.fileno()))
        receiver.rehash(root / role / "payload", row, identity, files, deadline)
        require(files.identity(os.fstat(stream.fileno())) == identity)
    adapter.check(deadline)
    return row


def failed_attempt(root, identity, common, stage, deadline, adapter, files):
    # A deadline or failed journal can prevent this best-effort record. The
    # already-durable intent and any partials still prohibit replay/adoption.
    try:
        adapter.check(deadline)
        with adapter.owned(root, files, identity, deadline) as fd:
            files.publish(fd, "failure.json", {**common, "kind": "failure", "result": "fail",
                "stage": stage, "failure": "package-rejected", "components_verified": False})
    except Exception:
        pass


def package(repository, preparation, build, verification, output, *, preparation_sha256,
            build_sha256, verification_sha256, deadline):
    """One fresh private output; no image path, transport or operational manifest.

    Pins must be independently authenticated by the caller. Filesystem calls
    cannot be preempted; the parent owns resource containment and uncertainty.
    """
    entered = time.monotonic()
    identity = common = None
    stage = "claim"
    try:
        adapter, files, receiver, materializer, runner = helpers()
        adapter.check(deadline)
        require(deadline <= entered + files.SECONDS)
        repository, preparation, build, verification, output = map(canonical_path,
            (repository, preparation, build, verification, output))
        adapter.hex_value(output.name, 32)
        require(not build.is_relative_to(output) and not verification.is_relative_to(output))
        for pin in (preparation_sha256, build_sha256, verification_sha256):
            adapter.hex_value(pin, 64)
        code = code_identity(deadline, adapter, files, materializer, runner)
        common = {"schema": SCHEMA, "scope": "offline-local-input-package", **code,
            "attempt_id": output.name, "image_producer": adapter.IMAGE_PRODUCER,
            "adapter_sha256": ADAPTER_SHA, "helpers": adapter.PINS,
            "inputs": {"preparation_sha256": preparation_sha256, "build_sha256": build_sha256,
                       "verification_sha256": verification_sha256}, "deadline_monotonic": deadline,
            "image_content_verified": False, "hardware_validated": False, "published": False,
            **{name: False for name in adapter.FLAGS}}
        with ExitStack() as stack:
            # Lexical containment is meaningful only after rejecting aliases in
            # every input path component. Keep the validated roots open.
            input_identities = set()
            for path in (repository, preparation, ROOT):
                adapter.check(deadline)
                source_fd = files.directory(path, private=path == preparation)
                stack.callback(os.close, source_fd)
                input_identities.add(tuple(adapter.directory_identity(os.fstat(source_fd))[:2]))
                require(not output.is_relative_to(path) and not path.is_relative_to(output))
            parent = files.directory(output.parent, private=True)
            stack.callback(os.close, parent)
            # Also catch different spellings of the same directory identity.
            for path in (output.parent, *output.parent.parents):
                adapter.check(deadline)
                ancestor = files.directory(path)
                try:
                    require(tuple(adapter.directory_identity(os.fstat(ancestor))[:2])
                            not in input_identities)
                finally:
                    os.close(ancestor)
            adapter.check(deadline)
            os.mkdir(output.name, 0o700, dir_fd=parent)
            fd = files.directory(output, private=True)
            stack.callback(os.close, fd)
            identity = adapter.directory_identity(os.fstat(fd))
            os.fsync(parent)
            adapter.anchored(output, parent, fd, files, deadline)
            files.publish(fd, "intent.json", {**common, "kind": "intent", "root_identity": identity})
            adapter.anchored(output, parent, fd, files, deadline)

            def emit(role, writer):
                adapter.anchored(output, parent, fd, files, deadline)
                row = component(output, fd, role, writer, deadline, adapter, files, receiver)
                adapter.anchored(output, parent, fd, files, deadline)
                return row

            stage = "records"
            components = {}
            for role, path, pin in (("build", build, build_sha256),
                                    ("verification", verification, verification_sha256)):
                raw = files.read(path, adapter.CAPS[role], private=True)
                require(files.sha(raw) == pin)
                components[role] = emit(role, lambda stream: stream.write(raw))
                if role == "verification":
                    original = files.strict_json(raw)
            image = original["archive"]
            adapter.hex_value(image["sha256"], 64)
            require(type(image["bytes"]) is int and 0 < image["bytes"] <= adapter.CAPS["image"])
            components["image"] = {"bytes": image["bytes"], "sha256": image["sha256"]}

            stage = "source"
            tree_raw = git(repository, ["ls-tree", "-rlz", "--full-tree", adapter.IMAGE_PRODUCER],
                           adapter.CAPS["source-tree"], deadline, adapter, materializer, runner)
            require(files.sha(tree_raw) == adapter.SOURCE_TREE_SHA)
            tree = materializer.expected_tree(tree_raw)
            components["source-tree"] = emit("source-tree", lambda stream: stream.write(tree_raw))
            def blob(name, oid, size):
                return git(repository, ["cat-file", "blob", oid], size,
                           deadline, adapter, materializer, runner)
            components["source"] = emit("source", lambda stream:
                tar_into(stream, tree, blob, adapter, runner, deadline))

            stage = "preparation"
            data = preparation_files(preparation, preparation_sha256, files, materializer,
                                     runner, adapter, deadline)
            tree_raw = inventory({name: ("100644", runner.blob_id(raw), len(raw))
                                  for name, raw in data.items()}, materializer)
            tree = materializer.expected_tree(tree_raw)
            components["preparation-tree"] = emit("preparation-tree", lambda stream: stream.write(tree_raw))
            components["preparation"] = emit("preparation", lambda stream:
                tar_into(stream, tree, lambda name, *_: data[name], adapter, runner, deadline))
            del data

            stage = "joins"
            joined = adapter.joined_inputs(output, {"components": components,
                "preparation_sha256": preparation_sha256}, files, materializer, deadline)
            adapter.anchored(output, parent, fd, files, deadline)
            stage = "receipt"
            report = {**common, "kind": "local-package-observation", "components": components,
                "written_components": list(ROLES), "components_verified": True, **joined}
            files.publish(fd, "receipt.json", report)
            adapter.anchored(output, parent, fd, files, deadline)
            require(files.read(output / "receipt.json", files.MAX_REPORT, private=True) == files.encoded(report))
            stage = "cleanup"
        adapter.check(deadline)
        return report
    except Exception:
        if identity is not None:
            failed_attempt(output, identity, common, stage, deadline, adapter, files)
        raise ValueError("original-image-package-rejected") from None
