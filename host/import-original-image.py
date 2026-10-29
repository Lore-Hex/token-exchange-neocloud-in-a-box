#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""One private original-image import; never deploy, retry or infer daemon cancellation."""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import subprocess
import sys
import time
import types

ROOT = Path(__file__).absolute().parents[1]
SELF = "host/import-original-image.py"
VERIFIER = "host/verify-selected-image-archive.py"
VERIFIER_SHA = "3d974e684ebe1869bfc2ef09dff8cbce6c9ff4098c2aa26809aa3eec4e4ba378"
STATE = Path("/var/lib/nib-original-image-import")
SCHEMA = "nib.original-image-import/v1"
REQUEST = "nib.original-image-import-request/v1"
DOCKER = "/usr/bin/docker"
ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C",
       "DOCKER_CONFIG": "/nonexistent/nib-image-import", "DOCKER_HOST": "unix:///run/docker.sock"}
LOAD_SECONDS, POST_SECONDS, RESERVE = 1800, 90, 10 << 30
MAX_RECORD, MIN_INODES = 65536, 262144
FALSE_FLAGS = ("runtime_started", "deployable", "hardware_validated", "published", "confidential")
CANCELLED = False


def require(value):
    if not value:
        raise ValueError("original-image-import-rejected")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def exact(value, keys):
    require(type(value) is dict and set(value) == set(keys.split()))


def hex_value(value, length=64):
    require(type(value) is str and re.fullmatch("[0-9a-f]{%d}" % length, value))
    return value


def image_id(value):
    require(type(value) is str and value.startswith("sha256:"))
    hex_value(value[7:])
    return value


def verifier():
    path = ROOT / VERIFIER
    with path.open("rb") as stream:
        raw = stream.read((1 << 20) + 1)
    require(len(raw) <= 1 << 20 and sha(raw) == VERIFIER_SHA)
    module = types.ModuleType("original_import_verifier")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module


def request(raw, expected, helper):
    require(sha(raw) == hex_value(expected))
    value = helper.strict_json(raw)
    exact(value, "schema operation producer importer_sha256 image_producer verification archive preparation build target preserve_image_id")
    require(value["schema"] == REQUEST and helper.encoded(value) == raw)
    hex_value(value["operation"], 32)
    hex_value(value["producer"], 40)
    hex_value(value["image_producer"], 40)
    require(value["importer_sha256"] == sha(helper.read(ROOT / SELF, 1 << 20)))
    image_id(value["preserve_image_id"])
    for key in ("verification", "archive", "preparation", "build"):
        row = value[key]
        exact(row, "path sha256 bytes" if key == "archive" else "path sha256")
        hex_value(row["sha256"])
        require(type(row["path"]) is str and Path(row["path"]).is_absolute()
                and row["path"] == os.path.normpath(row["path"]) and ".." not in Path(row["path"]).parts)
    require(type(value["archive"]["bytes"]) is int and 0 < value["archive"]["bytes"] <= helper.MAX_ARCHIVE)
    target = value["target"]
    exact(target, "boot_id daemon_pid daemon_start_ticks daemon_cmdline_sha256 daemon_exe_sha256 docker_cli_sha256 daemon_id docker_root containerd_root socket_device socket_inode")
    require(type(target["boot_id"]) is str and re.fullmatch("[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", target["boot_id"]))
    for key in ("daemon_pid", "daemon_start_ticks", "socket_device", "socket_inode"):
        require(type(target[key]) is int and target[key] > 0)
    for key in ("daemon_cmdline_sha256", "daemon_exe_sha256", "docker_cli_sha256"):
        hex_value(target[key])
    require(type(target["daemon_id"]) is str and re.fullmatch("[A-Za-z0-9:-]{1,128}", target["daemon_id"]))
    for key in ("docker_root", "containerd_root"):
        require(type(target[key]) is str and target[key].startswith("/")
                and target[key] == os.path.normpath(target[key]) and ".." not in Path(target[key]).parts)
    return value


def kernel(path, limit=4096):
    with open(path, "rb", buffering=0) as stream:
        raw = stream.read(limit + 1)
    require(0 < len(raw) <= limit)
    return raw


def boot():
    return kernel("/proc/sys/kernel/random/boot_id", 64).decode().strip()


def process(pid):
    require(type(pid) is int and pid > 1)
    root = Path("/proc") / str(pid)
    def sample():
        raw = kernel(root / "stat")
        require(raw.startswith((str(pid) + " (").encode()))
        fields = raw.rsplit(b") ", 1)[1].split()
        require(len(fields) >= 20 and fields[0] not in (b"Z", b"X", b"x"))
        return [int(fields[index]) for index in (2, 3, 19)]
    before = sample()
    command = sha(kernel(root / "cmdline", 16384))
    require(before == sample() and root.stat().st_uid == 0)
    return {"pid": pid, "pgid": before[0], "session": before[1],
            "start_ticks": before[2], "cmdline_sha256": command, "boot_id": boot()}


class State:
    """One host-wide durable claim, deliberately not automatically reusable."""
    def __init__(self, root, helper, expected):
        self.root, self.helper, self.expected = Path(root), helper, expected
        self.parse = helper.strict_json
        self.fd = helper.directory(self.root, private=True)
        info = os.fstat(self.fd)
        self.identity = [info.st_dev, info.st_ino]

    def close(self):
        os.close(self.fd)

    def check(self):
        fd = self.helper.directory(self.root, private=True)
        try:
            info = os.fstat(fd)
            require([info.st_dev, info.st_ino] == self.identity)
        finally:
            os.close(fd)

    def read(self, name):
        self.check()
        return self.parse(self.helper.read(self.root / name, MAX_RECORD, private=True))

    def has(self, name):
        self.check()
        return os.path.lexists(self.root / name)

    def write(self, name, value):
        self.check()
        self.helper.publish(self.fd, name, value)

    def event(self, name, value):
        self.write(name, {"request_sha256": self.expected, "state_identity": self.identity, **value})

    def event_read(self, name):
        row = self.read(name)
        require(row["request_sha256"] == self.expected and row["state_identity"] == self.identity)
        return row

    def claimed(self):
        raw = self.helper.read(self.root / "request.json", MAX_RECORD, private=True)
        value = request(raw, self.expected, self.helper)
        claim = self.event_read("claim.json")
        require(claim["operation"] == value["operation"] and claim["script_sha256"] == value["importer_sha256"])
        return value, claim

    @contextmanager
    def lock(self):
        fd = os.open("dispatch.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == 0
                    and stat.S_IMODE(info.st_mode) == 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)


def aborted(state):
    if CANCELLED:
        return True
    if state.has("abort.json"):
        require(state.event_read("abort.json")["abort"] is True)
        return True
    return False


def admit_inputs(value, helper, state, deadline):
    raw = helper.read(Path(value["verification"]["path"]), MAX_RECORD, private=True)
    require(sha(raw) == value["verification"]["sha256"])
    receipt = helper.strict_json(raw)
    exact(receipt, "schema result producer script_sha256 helpers profile limits started_utc ended_utc expected archive_verified worker_exit_confirmed image_loaded runtime_started deployable hardware_validated published confidential source archive")
    require(receipt["schema"] == helper.SCHEMA and receipt["result"] == "pass"
            and receipt["producer"] == value["image_producer"] and receipt["script_sha256"] == VERIFIER_SHA
            and receipt["helpers"] == helper.HELPERS and receipt["limits"] == helper.LIMITS
            and receipt["profile"] == helper.PROFILE and receipt["archive_verified"] is True
            and receipt["worker_exit_confirmed"] is True and receipt["image_loaded"] is False
            and all(receipt[key] is False for key in FALSE_FLAGS))
    args = types.SimpleNamespace(preparation=Path(value["preparation"]["path"]),
        preparation_sha256=value["preparation"]["sha256"], build_report=Path(value["build"]["path"]),
        build_sha256=value["build"]["sha256"])
    build, source = helper.source_inputs(args)
    require(source == receipt["source"] and source["build_producer"] == value["image_producer"]
            and receipt["expected"] == {"archive_sha256": value["archive"]["sha256"],
                "archive_bytes": value["archive"]["bytes"], "preparation_sha256": args.preparation_sha256,
                "build_sha256": args.build_sha256})
    require(build["image_id"] != value["preserve_image_id"] and not aborted(state))
    configs, parse = [], helper.strict_json
    def observe(raw):
        require(time.monotonic() < deadline and not aborted(state))
        row = parse(raw)
        if type(row) is dict and row.get("os") == "linux" and row.get("architecture") == "amd64":
            configs.append(row)
        return row
    # Observe the existing authenticated parser, never parse the OCI graph again.
    helper.strict_json = observe
    try:
        with helper.regular(Path(value["archive"]["path"]), private=True) as (stream, info):
            require(info.st_size == value["archive"]["bytes"])
            archive = helper.verify_archive(stream, value["archive"]["sha256"], info.st_size, build, deadline)
            require(archive == receipt["archive"] and len(configs) == 1 and not aborted(state))
            stream.seek(0)
            return {"archive": archive, "source": source, "config": configs[0]["config"],
                    "archive_identity": list(helper.identity(info))}
    finally:
        helper.strict_json = parse


def capacity(archive, roots, helper, *, reserve_only=False):
    # Deliberately do not credit shared blobs/layers. Both declared stores must fit.
    needed = RESERVE if reserve_only else archive["bytes"] + 2 * archive["expanded_bytes"] + RESERVE
    rows = []
    for path in roots:
        fd = helper.directory(Path(path))
        try:
            info, space = os.fstat(fd), os.fstatvfs(fd)
            require(info.st_uid == 0 and not info.st_mode & 0o022)
            free = space.f_bavail * space.f_frsize
            require(free >= needed and space.f_favail >= MIN_INODES)
            rows.append({"device": info.st_dev, "inode": info.st_ino, "free_bytes": free,
                         "free_inodes": space.f_favail, "required_bytes": needed})
        finally:
            os.close(fd)
    return rows


def executable(path, expected, helper, deadline):
    with helper.regular(Path(path)) as (stream, info):
        require(info.st_uid == 0 and not info.st_mode & 0o022 and info.st_mode & 0o111)
        digest = hashlib.sha256()
        size = 0
        while chunk := stream.read(1 << 20):
            size += len(chunk)
            require(size <= 256 << 20 and time.monotonic() < deadline)
            digest.update(chunk)
        require(digest.hexdigest() == expected and time.monotonic() < deadline)
        return [info.st_dev, info.st_ino]


class Docker:
    def __init__(self, helper, value):
        self.helper, self.value = helper, value
        self.runner = helper.load_pinned(helper.RUNNER, helper.RUNNER_SHA)

    def command(self, args, deadline):
        remaining = min(10, deadline - time.monotonic())
        require(remaining > 0)
        result = self.runner.execute_process_group([DOCKER, *args], env=ENV,
            timeout=remaining, max_output_bytes=1 << 20, max_stderr_bytes=4096)
        require(result.returncode == 0)
        return result.stdout

    def target(self, deadline):
        target = self.value["target"]
        require(boot() == target["boot_id"])
        before = process(target["daemon_pid"])
        require(before["start_ticks"] == target["daemon_start_ticks"]
                and before["cmdline_sha256"] == target["daemon_cmdline_sha256"])
        root = Path("/proc") / str(before["pid"])
        require(os.readlink(root / "exe") == "/usr/bin/dockerd")
        actual = (root / "exe").stat()
        require([actual.st_dev, actual.st_ino] == executable("/usr/bin/dockerd", target["daemon_exe_sha256"], self.helper, deadline))
        executable(DOCKER, target["docker_cli_sha256"], self.helper, deadline)
        for space in ("pid", "net", "mnt", "user"):
            a, b = os.stat(root / ("ns/" + space)), os.stat("/proc/self/ns/" + space)
            require((a.st_dev, a.st_ino) == (b.st_dev, b.st_ino))
        sock = os.lstat("/run/docker.sock")
        require(stat.S_ISSOCK(sock.st_mode) and sock.st_uid == 0 and not sock.st_mode & 0o007
                and (sock.st_dev, sock.st_ino) == (target["socket_device"], target["socket_inode"]))
        row = self.helper.strict_json(self.command(["info", "--format", "{{json .}}"], deadline))
        require(row["ID"] == target["daemon_id"] and row["ServerVersion"] == "29.1.3"
                and row["OSType"] == "linux" and row["DockerRootDir"] == target["docker_root"]
                and ["driver-type", "io.containerd.snapshotter.v1"] in row["DriverStatus"]
                and process(target["daemon_pid"]) == before)
        after = os.lstat("/run/docker.sock")
        require((sock.st_dev, sock.st_ino, sock.st_ctime_ns) == (after.st_dev, after.st_ino, after.st_ctime_ns))
        return {"daemon": before, "socket": [sock.st_dev, sock.st_ino, sock.st_ctime_ns],
                "info_sha256": sha(self.helper.encoded({key: row[key] for key in
                    ("ID", "ServerVersion", "DockerRootDir", "Driver", "DriverStatus", "OSType")}))}

    def inspect(self, identifier, deadline):
        image_id(identifier)
        rows = self.helper.strict_json(self.command(["image", "inspect", identifier], deadline))
        require(type(rows) is list and len(rows) == 1 and rows[0]["Id"] == identifier)
        row = rows[0]
        return {key: row.get(key) for key in ("Id", "Os", "Architecture", "Config", "RootFS", "RepoTags", "RepoDigests", "Descriptor")}

    def inventory(self, deadline):
        raw = self.command(["image", "ls", "--all", "--quiet", "--no-trunc"], deadline)
        rows = raw.decode("ascii").splitlines()
        require(len(rows) <= 4096)
        return {image_id(row) for row in rows}

    def load(self, stream):
        # Only the detached owner waits for this client. Never use the runner's
        # kill-on-timeout cleanup for a mutating Docker operation.
        return subprocess.Popen([DOCKER, "image", "load", "--quiet"], env=ENV,
            stdin=stream, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def loaded_image(row, admitted):
    archive, config = admitted["archive"], admitted["config"]
    def canonical_config(value):
        require(type(value) is dict)
        # Closed Docker Config zero values, not arbitrary falsy JSON. Field
        # types come from Moby 01f442b84d6a669c1e335b800d4670997cd5aa93,
        # api/types/container/config.go; see the task doc for source/limits.
        normalized = value.copy()
        for fields, kind, nullable in (
            ("Hostname Domainname User Image WorkingDir MacAddress StopSignal", str, False),
            ("AttachStdin AttachStdout AttachStderr Tty OpenStdin StdinOnce ArgsEscaped NetworkDisabled", bool, False),
            ("Env Cmd Entrypoint OnBuild Shell", list, True),
            ("ExposedPorts Volumes Labels", dict, True),
        ):
            for key in fields.split():
                if key not in normalized:
                    continue
                item = normalized[key]
                require((nullable and item is None) or type(item) is kind)
                if kind is list and item is not None:
                    require(all(type(entry) is str for entry in item))
                if kind is dict and item is not None:
                    require(all(type(name) is str for name in item))
                    require(all(type(entry) is str if key == "Labels"
                                else type(entry) is dict and not entry for entry in item.values()))
                if item is None or not item:
                    del normalized[key]
        # Pointer defaults are null only: {} and 0 are explicit values.
        for key, kind in (("Healthcheck", dict), ("StopTimeout", int)):
            if key in normalized:
                item = normalized[key]
                require(item is None or type(item) is kind)
                if item is None:
                    del normalized[key]
        return json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False)
    require(row["Id"] == archive["image_id"] and row["Os"] == "linux"
            and row["Architecture"] == "amd64" and not row["RepoTags"]
            and canonical_config(row["Config"]) == canonical_config(config)
            and row["RootFS"] == {"Type": "layers", "Layers": archive["diff_ids"]})


def load_once(value, admitted, helper, state, docker, *, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + POST_SECONDS
    target = docker.target(deadline)
    preserved = docker.inspect(value["preserve_image_id"], deadline)
    require(not set(admitted["archive"]["load_ids"]) & docker.inventory(deadline))
    roots = [value["target"][name] for name in ("docker_root", "containerd_root")]
    space = capacity(admitted["archive"], roots, helper)
    state.event("admitted.json", {"target": target, "capacity": space,
        "archive": admitted["archive"], "source": admitted["source"],
        "config_sha256": sha(helper.encoded(admitted["config"])),
        "preserved_image_sha256": sha(helper.encoded(preserved))})
    with helper.regular(Path(value["archive"]["path"]), private=True) as (stream, info):
        require(list(helper.identity(info)) == admitted["archive_identity"])
        with state.lock():
            require(not aborted(state) and clock() < deadline and docker.target(deadline) == target)
            fresh_space = capacity(admitted["archive"], roots, helper)
            require([(row["device"], row["inode"]) for row in fresh_space]
                    == [(row["device"], row["inode"]) for row in space])
            until = clock() + LOAD_SECONDS
            state.event("load-intent.json", {"load_deadline": until,
                "daemon_completion": "unknown", "archive_identity": admitted["archive_identity"]})
            require(not aborted(state) and clock() < deadline)
            client = docker.load(stream)
        journal_ok, late, cancel = True, False, False
        try:
            state.event("client.json", {"identity": process(client.pid)})
        except BaseException:
            journal_ok = False
        # Keep the owner alive across caller exit, cancellation and load timeout.
        # A CLI failure never establishes daemon termination; no retry is offered.
        while client.poll() is None:
            try:
                cancel = cancel or aborted(state)
                if clock() >= until and not late:
                    late = True
                    state.event("deadline.json", {"deadline_exceeded": True, "daemon_completion": "unknown"})
            except BaseException:
                journal_ok = False
            sleep(1)
        status = client.wait()
        cancel = cancel or aborted(state)
        late = late or clock() >= until
        completion = status == 0
        state.event("client-exit.json", {"exit_code": status, "client_exit_confirmed": True,
            "daemon_completion": "unknown"})
        require(completion)
        deadline = clock() + POST_SECONDS
        require(docker.target(deadline) == target)
        state.event("daemon-complete.json", {"daemon_completion": "confirmed", "target": target})
        loaded_image(docker.inspect(admitted["archive"]["image_id"], deadline), admitted)
        require(docker.inspect(value["preserve_image_id"], deadline) == preserved)
        require(docker.target(deadline) == target)
        remaining = capacity(admitted["archive"], roots, helper, reserve_only=True)
        require([(row["device"], row["inode"]) for row in remaining]
                == [(row["device"], row["inode"]) for row in space])
        require(journal_ok and not late and not cancel and not aborted(state) and clock() < deadline)
    require(clock() < deadline)
    return {"image_loaded": True, "daemon_completion": "confirmed", "readback_verified": True,
            "preserved_image_unchanged": True, "archive_config_id": admitted["archive"]["config_id"],
            "daemon_config_bytes_rehashed": False}


def interrupt(*_):
    global CANCELLED
    CANCELLED = True


@contextmanager
def validation_budget(helper):
    import resource
    old = resource.getrlimit(resource.RLIMIT_AS)
    cpu = resource.getrlimit(resource.RLIMIT_CPU)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    cpu_limit = int(usage.ru_utime + usage.ru_stime) + helper.SECONDS + 1
    if cpu[0] >= 0:
        cpu_limit = min(cpu_limit, cpu[0])
    resource.setrlimit(resource.RLIMIT_AS, (min(helper.MEMORY, old[0]) if old[0] >= 0 else helper.MEMORY, old[1]))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu[1]))
    def expired(*_):
        raise TimeoutError()
    handlers = {sig: signal.signal(sig, expired) for sig in (signal.SIGALRM, signal.SIGXCPU)}
    signal.setitimer(signal.ITIMER_REAL, helper.SECONDS)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        resource.setrlimit(resource.RLIMIT_AS, old)
        resource.setrlimit(resource.RLIMIT_CPU, cpu)


def worker(state, gate):
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, interrupt)
    ready, _, _ = select.select([gate], [], [], 30)
    require(ready and os.read(gate, 2) == b"1")
    os.close(gate)
    value, claim = state.claimed()
    require(boot() == claim["boot_id"] == value["target"]["boot_id"]
            and state.event_read("owner.json")["identity"] == process(os.getpid())
            and time.monotonic() < claim["launch_expires"])
    report = {"schema": SCHEMA, "operation": value["operation"], "producer": value["producer"],
        "script_sha256": value["importer_sha256"], "verifier_sha256": VERIFIER_SHA,
        "image_producer": value["image_producer"], "verification_sha256": value["verification"]["sha256"],
        "archive_sha256": value["archive"]["sha256"], "preparation_sha256": value["preparation"]["sha256"],
        "build_sha256": value["build"]["sha256"],
        "result": "fail", "image_loaded": False, "daemon_completion": "unknown",
        **{key: False for key in FALSE_FLAGS}}
    try:
        with validation_budget(state.helper):
            admitted = admit_inputs(value, state.helper, state, time.monotonic() + state.helper.SECONDS)
        report.update(load_once(value, admitted, state.helper, state, Docker(state.helper, value)))
        report["result"] = "pass"
    except BaseException:
        # Even an owner crash or disk-full error after load-intent leaves a
        # durable unknown-operation barrier. No cleanup, deletion or adoption.
        if not state.has("load-intent.json"):
            report["daemon_completion"] = "not-started"
        elif state.has("daemon-complete.json"):
            report["daemon_completion"] = state.event_read("daemon-complete.json")["daemon_completion"]
    with state.lock():
        if report["result"] == "pass" and aborted(state):
            report["result"] = "fail"
        state.event("receipt.json", report)
    return 0 if report["result"] == "pass" else 1


def start(state, raw):
    value = request(raw, state.expected, state.helper)
    with os.scandir(state.fd) as entries:
        require(next(entries, None) is None)
    # Filesystem durability precedes any child or Docker invocation. This fixed
    # host-wide directory is never reset automatically, even after a PASS.
    state.write("request.json", value)
    state.event("claim.json", {"operation": value["operation"], "script_sha256": value["importer_sha256"],
        "boot_id": boot(), "launch_expires": time.monotonic() + 30})
    fd = os.open("dispatch.lock", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=state.fd)
    os.fsync(fd)
    os.close(fd)
    os.fsync(state.fd)
    read_fd, write_fd = os.pipe()
    try:
        command = [sys.executable, "-I", "-S", "-B", str(ROOT / SELF), "worker",
            "--request-sha256", state.expected, "--gate", str(read_fd)]
        child = subprocess.Popen(command, env={key: ENV[key] for key in ("PATH", "HOME", "LANG", "LC_ALL")},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, pass_fds=(read_fd,))
        identity = process(child.pid)
        require(identity["cmdline_sha256"] == sha(b"\0".join(part.encode() for part in command) + b"\0")
                and identity["pgid"] == identity["session"] == child.pid)
        state.event("owner.json", {"identity": identity})
        with state.lock():
            _, claim = state.claimed()
            require(not aborted(state) and boot() == claim["boot_id"]
                    and process(child.pid) == identity and time.monotonic() < claim["launch_expires"])
            require(os.write(write_fd, b"1") == 1)
    finally:
        os.close(read_fd)
        os.close(write_fd)
    return {"result": "pending", "operation": value["operation"], "daemon_completion": "unknown"}


def status(state):
    value, _ = state.claimed()
    if state.has("receipt.json"):
        row = state.event_read("receipt.json")
        require(row["schema"] == SCHEMA and row["operation"] == value["operation"]
                and row["producer"] == value["producer"] and row["script_sha256"] == value["importer_sha256"]
                and row["verifier_sha256"] == VERIFIER_SHA and row["result"] in ("pass", "fail")
                and all(row[key] is False for key in FALSE_FLAGS))
        if row["result"] == "pass":
            require(row["image_loaded"] is True and row["readback_verified"] is True
                    and row["daemon_completion"] == "confirmed" and row["preserved_image_unchanged"] is True
                    and not state.has("abort.json") and not state.has("deadline.json"))
        return row
    alive = False
    if state.has("owner.json"):
        identity = state.event_read("owner.json")["identity"]
        try:
            alive = process(identity["pid"]) == identity
        except (OSError, ValueError):
            pass
    return {"result": "pending" if alive else "unknown", "operation": value["operation"],
            "owner_identity_live": alive, "daemon_completion": "unknown",
            "deadline_exceeded": state.has("deadline.json"), "abort_requested": state.has("abort.json")}


def cancel(state):
    state.claimed()
    # Serialize with dispatch; after dispatch this requests no retry/readiness,
    # not a fictitious cancellation of the daemon's image-load operation.
    with state.lock():
        if state.has("receipt.json"):
            return status(state)
        if not state.has("abort.json"):
            state.event("abort.json", {"abort": True})
        else:
            require(state.event_read("abort.json")["abort"] is True)
    return status(state)


class QuietParser(argparse.ArgumentParser):
    def error(self, _message):
        raise ValueError("original-image-import-rejected")


def main():
    parser = QuietParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("mode", choices=("start", "status", "cancel", "worker"))
    parser.add_argument("--request-sha256", required=True)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--gate", type=int, help=argparse.SUPPRESS)
    state = None
    try:
        args = parser.parse_args()
        require(sys.platform == "linux" and os.getuid() == 0 and sys.flags.isolated
                and sys.flags.no_site and sys.dont_write_bytecode)
        hex_value(args.request_sha256)
        helper = verifier()
        state = State(STATE, helper, args.request_sha256)
        if args.mode == "worker":
            require(type(args.gate) is int and args.gate >= 3)
            return worker(state, args.gate)
        if args.mode == "start":
            require(args.request is not None and args.gate is None)
            result = start(state, helper.read(args.request, MAX_RECORD, private=True))
        else:
            require(args.request is None and args.gate is None)
            result = cancel(state) if args.mode == "cancel" else status(state)
        # Private JSON only. No raw Docker output, labels, operator paths or errors.
        print(json.dumps(result, sort_keys=True))
        return 0 if result["result"] in ("pass", "pending") else 1
    except BaseException:
        print('{"result":"fail","daemon_completion":"unknown"}')
        return 1
    finally:
        if state is not None:
            state.close()


if __name__ == "__main__":
    raise SystemExit(main())
