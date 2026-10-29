#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Closed original-image input-set entrypoint; invoked only by pinned bootstrap."""

from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import math
import os
from pathlib import Path
import re
import selectors
import stat
import time
import types

ROOT = Path(__file__).absolute().parents[1]
SELF = "host/receive-original-image-inputs.py"
SENDER = "scripts/original_image_transfer.py"
RECEIVER = "scripts/original_image_input.py"
VERIFIER = "host/verify-selected-image-archive.py"
MATERIALIZER = "host/prepare-lab.py"
RUNNER = "scripts/e2e/snapshot.py"
PINS = {
    RECEIVER: "0e097af8f3606c7142e2438ae0f53bb83a83b23c05458ff2ea280fbd8f2828f2",
    VERIFIER: "e24de243aa0ddc0f7c5bc61aab85152bf9ec1eeccd96a9e35ce4b299c0544968",
    MATERIALIZER: "92bb5f08d82b032ed0fbc4db9467047742ad70d497110f9d0c278f7d302c3354",
    RUNNER: "e73a5a1e3ddb7a6e7b20afce39f7c080a0317d78340f224b2bd95f705bccb59c",
}
IMAGE_PRODUCER = "d18aee4ce01062134d0e893c72555ef104a9ed84"
# SHA256(git ls-tree -rlz --full-tree <IMAGE_PRODUCER>), not preparation provenance.
SOURCE_TREE_SHA = "78e561ec5fbf998ab211942de3c7f8e7ae6d4ed37b83f86b73461ba5204b0322"
ROLES = ("source-tree", "source", "preparation-tree", "preparation", "build", "verification", "image")
CAPS = dict(zip(ROLES, (1 << 20, 80 << 20, 1 << 20, 80 << 20, 16384, 65536, 10 << 30)))
SCHEMA = "nib.original-image-input-set/v1"
FLAGS = ("remote_transfer_complete", "admitted", "imported", "image_loaded", "runtime_started",
         "deployable", "confidential", "cleanup_confirmed")


def require(value):
    if not value:
        raise ValueError("original-image-transfer-rejected")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def check(deadline):
    require(type(deadline) in (int, float) and math.isfinite(deadline))
    remaining = deadline - time.monotonic()
    require(remaining > 0)
    return remaining


def load(raw, path):
    module = types.ModuleType("original_transfer_" + Path(path).stem)
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module


def helpers(root=ROOT, bootstrap_verifier=None):
    # The independently pinned launcher authenticates this module first.
    raw = bootstrap_verifier
    if raw is None:
        with (root / VERIFIER).open("rb") as stream:
            raw = stream.read((1 << 20) + 1)
    require(sha(raw) == PINS[VERIFIER])
    files = load(raw, root / VERIFIER)
    modules = {}
    for name, expected in PINS.items():
        raw = files.read(root / name, 1 << 20)
        require(sha(raw) == expected)
        modules[name] = raw
    return files, load(modules[RECEIVER], root / RECEIVER), load(modules[MATERIALIZER], root / MATERIALIZER)


def hex_value(value, length):
    require(type(value) is str and re.fullmatch("[0-9a-f]{%d}" % length, value))


def manifest(raw, expected, files):
    require(type(raw) is bytes and 0 < len(raw) <= files.MAX_REPORT and sha(raw) == expected)
    value = files.strict_json(raw)
    require(type(value) is dict and set(value) == {"schema", "attempt_id", "producer", "image_producer",
        "seconds", "destination", "code", "components", "preparation_sha256"})
    require(files.encoded(value) == raw and value["schema"] == SCHEMA
            and value["image_producer"] == IMAGE_PRODUCER)
    for key, length in (("attempt_id", 32), ("producer", 40), ("preparation_sha256", 64)):
        hex_value(value[key], length)
    require(value["producer"] != IMAGE_PRODUCER
            and type(value["seconds"]) is int and 0 < value["seconds"] <= files.SECONDS)
    path = Path(value["destination"])
    require(path.is_absolute() and str(path) == os.path.normpath(path) and ".." not in path.parts
            and path.name == value["attempt_id"])
    require(type(value["code"]) is dict and set(value["code"]) == set(PINS) | {SELF, SENDER})
    for name, digest in value["code"].items():
        hex_value(digest, 64)
        require(name not in PINS or digest == PINS[name])
    require(type(value["components"]) is dict and set(value["components"]) == set(ROLES))
    for role, row in value["components"].items():
        require(type(row) is dict and set(row) == {"bytes", "sha256"}
                and type(row["bytes"]) is int and 0 < row["bytes"] <= CAPS[role])
        hex_value(row["sha256"], 64)
    require(value["components"]["source-tree"]["sha256"] == SOURCE_TREE_SHA)
    return value


def clock():
    # Linux monotonic values are compared only within this boot/time namespace.
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    require(re.fullmatch("[0-9a-f-]{36}", boot))
    info = os.stat("/proc/self/ns/time")
    return {"boot_id": boot, "clock_namespace": [info.st_dev, info.st_ino],
            "monotonic": time.monotonic()}


def clock_check(binding, deadline):
    require(type(binding) is dict and set(binding) == {"boot_id", "clock_namespace", "monotonic"}
            and type(binding["boot_id"]) is str and re.fullmatch("[0-9a-f-]{36}", binding["boot_id"])
            and type(binding["clock_namespace"]) is list and len(binding["clock_namespace"]) == 2
            and all(type(n) is int and n >= 0 for n in binding["clock_namespace"])
            and type(binding["monotonic"]) in (int, float) and math.isfinite(binding["monotonic"]))
    current = clock()
    require(current["boot_id"] == binding["boot_id"]
            and current["clock_namespace"] == binding["clock_namespace"]
            and current["monotonic"] >= binding["monotonic"])
    check(deadline)


def directory_identity(info):
    return [info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)]


def anchored(root, parent, fd, files, deadline):
    check(deadline)
    current = files.directory(root.parent, private=True)
    try:
        require(directory_identity(os.fstat(fd))[2:] == [os.getuid(), 0o700]
                and directory_identity(os.fstat(current)) == directory_identity(os.fstat(parent))
                and directory_identity(os.stat(root.name, dir_fd=current, follow_symlinks=False))
                == directory_identity(os.fstat(fd)))
    finally:
        os.close(current)
    check(deadline)


def write_file(parent, name, raw, deadline):
    check(deadline)
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    with os.fdopen(fd, "wb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600)
        check(deadline)
        require(stream.write(raw) == len(raw))
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(parent)
    check(deadline)


@contextmanager
def private_parents(root, name, files, deadline):
    # Walk authenticated relative names one component at a time: mkdir(parents=True)
    # would give intermediate directories the ambient umask's broader permissions.
    parts = name.split("/")
    require(all(part not in ("", ".", "..") for part in parts))
    with ExitStack() as stack:
        fd = files.directory(root, private=True)
        stack.callback(os.close, fd)
        for part in parts[:-1]:
            check(deadline)
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            stack.callback(os.close, child)
            require(directory_identity(os.fstat(child))[2:] == [os.getuid(), 0o700])
            os.fsync(fd)
            fd = child
        check(deadline)
        yield fd
    check(deadline)


@contextmanager
def owned(root, files, identity, deadline):
    fd = files.directory(root, private=True)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(directory_identity(os.fstat(fd)) == identity)
        check(deadline)
        yield fd
        current = files.directory(root, private=True)
        try:
            require(directory_identity(os.fstat(current)) == identity)
        finally:
            os.close(current)
    finally:
        os.close(fd)
    check(deadline)


def ack(files, value, manifest_sha, phase, previous, deadline, binding, **fields):
    return {"schema": SCHEMA, "kind": "terminal-ack", "phase": phase,
        "manifest_sha256": manifest_sha, "attempt_id": value["attempt_id"],
        "producer": value["producer"], "image_producer": value["image_producer"],
        "previous_ack_sha256": previous, "deadline_monotonic": deadline,
        "clock": binding, **{name: False for name in FLAGS}, **fields}


def initialize(packet, sources):
    # sources arrive as bytes authenticated in the trusted command before exec.
    raw = bytes.fromhex(packet["manifest"])
    require(sha(sources[VERIFIER]) == PINS[VERIFIER])
    files = load(sources[VERIFIER], Path(packet["root"]) / VERIFIER)
    value = manifest(raw, packet["manifest_sha256"], files)
    require(set(sources) == set(value["code"]) and all(sha(sources[name]) == digest
            for name, digest in value["code"].items()))
    root, deadline = Path(value["destination"]), packet["deadline"]
    require(str(root) == packet["root"])
    binding = packet["clock"]
    clock_check(binding, deadline)
    require(deadline <= binding["monotonic"] + value["seconds"])
    parent = files.directory(root.parent, private=True)
    try:
        check(deadline)
        os.mkdir(root.name, 0o700, dir_fd=parent)
        fd = os.open(root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            os.fsync(parent)
            identity = directory_identity(os.fstat(fd))
            anchored(root, parent, fd, files, deadline)
            state = {"manifest_sha256": packet["manifest_sha256"],
                     "root_identity": identity, "clock": binding, "deadline": deadline}
            files.publish(fd, "intent.json", state)
            anchored(root, parent, fd, files, deadline)
            write_file(fd, "manifest.json", raw, deadline)
            # Only the closed, authenticated helper graph is installed here.
            for name, data in sources.items():
                target = root / name
                anchored(root, parent, fd, files, deadline)
                with private_parents(root, name, files, deadline) as child:
                    write_file(child, target.name, data, deadline)
            sync_tree(root, files, deadline)
            result = ack(files, value, packet["manifest_sha256"], "bootstrap", None,
                         deadline, binding, intent_sha256=sha(files.encoded(state)))
            anchored(root, parent, fd, files, deadline)
            files.publish(fd, "bootstrap.ack.json", result)
            anchored(root, parent, fd, files, deadline)
        finally:
            os.close(fd)
    finally:
        os.close(parent)
    clock_check(binding, deadline)
    return result


def sync_tree(root, files, deadline):
    def failed(error):
        raise error
    for directory, _, _ in os.walk(root, followlinks=False, onerror=failed):
        check(deadline)
        fd = files.directory(Path(directory), private=True)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    check(deadline)


def component_request(value, role, files):
    return files.encoded({"schema": "nib.original-image-input-request/v1",
        "attempt_id": value["attempt_id"], "producer": value["producer"],
        "image_producer": value["image_producer"], "receiver_sha256": PINS[RECEIVER],
        "seconds": value["seconds"], **value["components"][role]})


def materialize(root, role, value, files, materializer, deadline):
    raw_tree = files.read(root / (role + "-tree") / "payload", CAPS[role + "-tree"], private=True)
    require(sha(raw_tree) == value["components"][role + "-tree"]["sha256"])
    tree = materializer.expected_tree(raw_tree)
    raw = files.read(root / role / "payload", CAPS[role], private=True)
    require(sha(raw) == value["components"][role]["sha256"])
    target = root / (role + "-files")
    parent = files.directory(root, private=True)
    try:
        check(deadline)
        os.mkdir(target.name, 0o700, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)
    check(deadline)
    for name in tree:
        with private_parents(target, name, files, deadline):
            pass
    check(deadline)
    materializer.materialize(target, raw, tree, deadline)
    check(deadline)
    sync_tree(target, files, deadline)
    return target


def joined_inputs(root, value, files, materializer, deadline):
    source = materialize(root, "source", value, files, materializer, deadline)
    preparation = materialize(root, "preparation", value, files, materializer, deadline)
    # Execute the original verifier only after the entire pinned d18 tree exists.
    raw = files.read(source / VERIFIER, 1 << 20)
    require(sha(raw) == PINS[VERIFIER])
    verifier = load(raw, source / VERIFIER)
    args = types.SimpleNamespace(preparation=preparation,
        preparation_sha256=value["preparation_sha256"], build_report=root / "build" / "payload",
        build_sha256=value["components"]["build"]["sha256"])
    check(deadline)
    build, source_join = verifier.source_inputs(args)
    check(deadline)
    raw = files.read(root / "verification" / "payload", CAPS["verification"], private=True)
    require(sha(raw) == value["components"]["verification"]["sha256"])
    receipt = files.strict_json(raw)
    require(type(receipt) is dict and set(receipt) == set(
        "schema result producer script_sha256 helpers profile limits started_utc ended_utc expected "
        "archive_verified worker_exit_confirmed image_loaded runtime_started deployable hardware_validated "
        "published confidential source archive".split()))
    require(receipt["schema"] == verifier.SCHEMA and receipt["result"] == "pass"
            and receipt["producer"] == IMAGE_PRODUCER and receipt["script_sha256"] == PINS[VERIFIER]
            and receipt["helpers"] == verifier.HELPERS and receipt["limits"] == verifier.LIMITS
            and receipt["profile"] == verifier.PROFILE and receipt["archive_verified"] is True
            and receipt["worker_exit_confirmed"] is True
            and all(receipt[key] is False for key in ("image_loaded", "runtime_started", "deployable",
                                                     "hardware_validated", "published", "confidential")))
    require(source_join == receipt["source"] and source_join["build_producer"] == IMAGE_PRODUCER
            and receipt["expected"] == {"archive_sha256": value["components"]["image"]["sha256"],
                "archive_bytes": value["components"]["image"]["bytes"],
                "preparation_sha256": value["preparation_sha256"], "build_sha256": args.build_sha256})
    require(receipt["archive"]["image_id"] == build["image_id"]
            and receipt["archive"]["sha256"] == value["components"]["image"]["sha256"]
            and receipt["archive"]["bytes"] == value["components"]["image"]["bytes"]
            and receipt["archive"]["platform"] == "linux/amd64")
    return {"source": source_join, "image_id": build["image_id"],
            "archive": value["components"]["image"], "inputs_joined": True}


def phase(root, manifest_sha, name, previous_sha, input_fd, binding, deadline, intent_sha, verifier_raw):
    files, receiver, materializer = helpers(root, verifier_raw)
    raw = files.read(root / "manifest.json", files.MAX_REPORT, private=True)
    value = manifest(raw, manifest_sha, files)
    require(str(root) == value["destination"] and name in (*ROLES, "final"))
    raw = files.read(root / "intent.json", files.MAX_REPORT, private=True)
    require(sha(raw) == intent_sha)
    state = files.strict_json(raw)
    require(state["manifest_sha256"] == manifest_sha and state["deadline"] == deadline
            and state["clock"] == binding)
    clock_check(binding, deadline)
    sequence = ("bootstrap", *ROLES, "final")
    previous = sequence[sequence.index(name) - 1]
    with owned(root, files, state["root_identity"], deadline) as fd:
        raw = files.read(root / (previous + ".ack.json"), files.MAX_REPORT, private=True)
        require(sha(raw) == previous_sha)
        before = files.strict_json(raw)
        require(before["phase"] == previous and before["manifest_sha256"] == manifest_sha
                and before["deadline_monotonic"] == deadline and before["clock"] == binding
                and before["intent_sha256"] == intent_sha)
        check(deadline)
        # Exclusive phase intent forbids concurrent or later replay, even after failure.
        files.publish(fd, name + ".intent.json", {"previous_ack_sha256": previous_sha,
                                                 "manifest_sha256": manifest_sha})
        check(deadline)
        if name == "final":
            validate_components(root, value, files, deadline, manifest_sha, previous_sha, intent_sha)
            fields = joined_inputs(root, value, files, materializer, deadline)
        else:
            request = component_request(value, name, files)
            observed = receiver.receive(input_fd, root / name, request, sha(request), deadline=deadline)
            fields = {"component": name, "request_sha256": sha(request),
                "component_receipt_sha256": sha(files.encoded(observed)),
                "verified_bytes": observed["verified_bytes"], "sha256": observed["sha256"]}
        clock_check(binding, deadline)
        result = ack(files, value, manifest_sha, name, previous_sha, deadline, binding,
                     intent_sha256=intent_sha, **fields)
        check(deadline)
        files.publish(fd, name + ".ack.json", result)
        check(deadline)
    clock_check(binding, deadline)
    return result


def validate_components(root, value, files, deadline, manifest_sha, final_previous, intent_sha):
    previous = sha(files.read(root / "bootstrap.ack.json", files.MAX_REPORT, private=True))
    for role in ROLES:
        check(deadline)
        raw = files.read(root / (role + ".ack.json"), files.MAX_REPORT, private=True)
        terminal = files.strict_json(raw)
        require(terminal["manifest_sha256"] == manifest_sha and terminal["phase"] == role
                and terminal["previous_ack_sha256"] == previous and terminal["intent_sha256"] == intent_sha)
        previous = sha(raw)
        raw = files.read(root / role / "receipt.json", files.MAX_REPORT, private=True)
        require(sha(raw) == terminal["component_receipt_sha256"])
        row = files.strict_json(raw)
        request = component_request(value, role, files)
        require(row["request"] == files.strict_json(request) and row["request_sha256"] == sha(request)
                and row["component_verified"] is True and row["eof_observed"] is True
                and row["reopened_verified"] is True and row["deadline_monotonic"] == deadline)
        # Identity readback binds even the opaque image to the inode rehashed by
        # receive(). Actual admission later rehashes/parses it in the importer.
        with files.regular(root / role / "payload", private=True) as (_, info):
            require(list(files.identity(info)) == row["payload_identity"])
        check(deadline)
    require(previous == final_previous)


def emit(result, files):
    # A record is not an exit proof. The sender must also observe exit 0.
    clock_check(result["clock"], result["deadline_monotonic"])
    raw = files.encoded(result)
    require(len(raw) <= files.MAX_REPORT)
    blocking = os.get_blocking(1)
    try:
        os.set_blocking(1, False)
        with selectors.DefaultSelector() as selector:
            selector.register(1, selectors.EVENT_WRITE)
            view = memoryview(raw)
            while view:
                require(selector.select(check(result["deadline_monotonic"])))
                check(result["deadline_monotonic"])
                try:
                    written = os.write(1, view)
                except (BlockingIOError, InterruptedError):
                    continue
                require(written > 0)
                view = view[written:]
    finally:
        os.set_blocking(1, blocking)
    clock_check(result["clock"], result["deadline_monotonic"])
