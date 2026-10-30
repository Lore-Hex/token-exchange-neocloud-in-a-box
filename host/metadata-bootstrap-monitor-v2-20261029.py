"""Closed bootstrap kernel reader. No CLI, target, transport or authorization."""

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import selectors
import signal
import socket
import stat
import struct
import time

PRODUCER = "31162745cb90f4ab268f1b2b8eb8c768b750442a"
LIMITS = {"memory.max": "8589934592", "memory.swap.max": "0", "memory.oom.group": "1",
          "pids.max": "256", "cpu.max": "200000 100000"}
MAX_RECORD = 65536
FLAGS = {key: False for key in ("admitted", "runtime_ready", "metadata_controller_integrated",
                              "mount_performed", "installed_source_executed", "confidential")}


def require(value):
    if not value:
        raise ValueError("metadata-bootstrap-rejected")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii") + b"\n"


def parsed(raw, ownership=False):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_RECORD)
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value)
            value[key] = item
        return value
    value = json.loads(raw, object_pairs_hook=unique)
    canonical = ((json.dumps(value, sort_keys=True) + "\n").encode("ascii")
                 if ownership else encoded(value))
    require(type(value) is dict and canonical == raw)
    return value


def digest(value):
    require(type(value) is str and re.fullmatch("[0-9a-f]{64}", value))


def left(deadline):
    require(type(deadline) in (int, float) and math.isfinite(deadline))
    remaining = deadline - time.monotonic()
    require(remaining > 0)
    return remaining


def directory_id(info):
    require(stat.S_ISDIR(info.st_mode))
    return [info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)]


def file_id(info):
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1)
    return [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns]


@contextmanager
def directory(path):
    path = Path(path)
    require(path.is_absolute() and str(path) == os.path.normpath(path) and ".." not in path.parts)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        current = Path("/")
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            current /= part
            info = os.fstat(fd)
            sticky = (str(current) in ("/tmp", "/private/tmp", "/var/tmp")
                      and info.st_uid == 0 and info.st_mode & stat.S_ISVTX)
            require(info.st_uid in (0, os.getuid()) and (not info.st_mode & 0o022 or sticky))
        require(directory_id(os.fstat(fd))[2:] == [os.getuid(), 0o700])
        yield fd
    finally:
        os.close(fd)


def read_record(root, name):
    require("/" not in name and name not in ("", ".", ".."))
    with directory(root) as parent:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            identity = file_id(os.fstat(fd))
            raw = os.read(fd, MAX_RECORD + 1)
            require(len(raw) == identity[5] <= MAX_RECORD
                    and file_id(os.fstat(fd)) == identity
                    == file_id(os.stat(name, dir_fd=parent, follow_symlinks=False)))
            return raw
        finally:
            os.close(fd)


def publish(root, name, raw, deadline, identity):
    """Atomic no-replace publication; failures retain pending files, never adopt."""
    require(type(raw) is bytes and len(raw) <= MAX_RECORD and re.fullmatch(r"[a-z0-9.-]+", name))
    with directory(root) as parent:
        require(directory_id(os.fstat(parent)) == identity)
        left(deadline)
        pending = ".pending-" + name
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            file_id(os.fstat(fd))
            view = memoryview(raw)
            while view:
                left(deadline)
                count = os.write(fd, view)
                require(count > 0)
                view = view[count:]
            left(deadline)
            os.fsync(fd)
            left(deadline)
            require(directory_id(os.stat(root, follow_symlinks=False)) == identity)
            os.link(pending, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            left(deadline)
            os.fsync(parent)
            left(deadline)
            os.unlink(pending, dir_fd=parent)
            left(deadline)
            os.fsync(parent)
        finally:
            os.close(fd)
    left(deadline)
    require(read_record(root, name) == raw)
    left(deadline)


def claim(parent_path, name, parent_identity, deadline):
    require(re.fullmatch(r"[a-z0-9-]+", name))
    with directory(parent_path) as parent:
        require(directory_id(os.fstat(parent)) == parent_identity)
        left(deadline)
        os.mkdir(name, 0o700, dir_fd=parent)
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            identity = directory_id(os.fstat(fd))
            require(identity[2:] == [os.getuid(), 0o700])
            left(deadline)
            os.fsync(fd)
            left(deadline)
            os.fsync(parent)
        finally:
            os.close(fd)
    left(deadline)
    return identity


def kernel_read(path, limit=8192):
    with open(path, "rb") as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit)
    return raw


def boot_id():
    boot = kernel_read("/proc/sys/kernel/random/boot_id", 38).decode("ascii").strip()
    require(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", boot))
    return boot


def clock(pid="self"):
    boot = boot_id()
    info = os.stat("/proc/" + str(pid) + "/ns/time")
    return {"boot_id": boot, "clock_namespace": [info.st_dev, info.st_ino]}


def process_stat(pid):
    require(type(pid) is int and pid > 0)
    raw = kernel_read("/proc/%d/stat" % pid)
    fields = raw.rsplit(b")", 1)[1].split()
    require(int(raw.split(b" ", 1)[0]) == pid and int(fields[19]) > 0)
    return {"pid": pid, "start_ticks": int(fields[19]), "ppid": int(fields[1]),
            "pgid": int(fields[2]), "sid": int(fields[3])}


def process(pid):
    return {**process_stat(pid), **clock(pid)}


def same_process(expected):
    try:
        return process(expected["pid"]) == expected
    except FileNotFoundError:
        return False


def alive(expected):
    # Reparenting, setsid and exec are not absence of an owned PID/start pair.
    # A zombie may retain stat after its namespace links disappear. Only the
    # stat read itself may establish PID absence; namespace checks stay above.
    require(boot_id() == expected["boot_id"])
    try:
        current = process_stat(expected["pid"])
    except FileNotFoundError:
        return False
    return current["start_ticks"] == expected["start_ticks"]


def command(pid):
    before = process(pid)
    raw = kernel_read("/proc/%d/cmdline" % pid, MAX_RECORD)
    require(raw.endswith(b"\0") and process(pid) == before)
    return [part.decode("utf-8") for part in raw[:-1].split(b"\0")]


def peer(connection):
    return struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))


@contextmanager
def nonblocking(fd):
    old = os.get_blocking(fd)
    os.set_blocking(fd, False)
    try:
        yield
    finally:
        os.set_blocking(fd, old)


def pipe_bytes(fd, deadline, *, count=None, raw=None):
    require((count is None) != (raw is None))
    reading = count is not None
    require((type(count) is int and 0 <= count <= 1 << 20) if reading
            else (type(raw) is bytes and len(raw) <= 1 << 20))
    result = bytearray()
    with nonblocking(fd), selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ if reading else selectors.EVENT_WRITE)
        total = count if reading else len(raw)
        while len(result) < total:
            require(selector.select(left(deadline)))
            left(deadline)
            try:
                if reading:
                    part = os.read(fd, min(65536, total - len(result)))
                    require(part)
                    result.extend(part)
                else:
                    written = os.write(fd, memoryview(raw)[len(result):])
                    require(written > 0)
                    result.extend(b"\0" * written)
            except (BlockingIOError, InterruptedError):
                continue
    left(deadline)
    return bytes(result) if reading else None


def frame(fd, deadline, raw=None):
    if raw is None:
        size = int.from_bytes(pipe_bytes(fd, deadline, count=4), "big")
        require(0 < size <= MAX_RECORD)
        return pipe_bytes(fd, deadline, count=size)
    require(type(raw) is bytes and 0 < len(raw) <= MAX_RECORD)
    pipe_bytes(fd, deadline, raw=len(raw).to_bytes(4, "big") + raw)


def eof(fd, deadline):
    with nonblocking(fd):
        require(select.select([fd], [], [], left(deadline))[0])
        left(deadline)
        require(os.read(fd, 1) == b"")
    left(deadline)


def packet(connection, deadline, raw=None):
    connection.setblocking(False)
    with selectors.DefaultSelector() as selector:
        selector.register(connection, selectors.EVENT_READ if raw is None else selectors.EVENT_WRITE)
        while True:
            require(selector.select(left(deadline)))
            left(deadline)
            try:
                if raw is None:
                    value, _, flags, _ = connection.recvmsg(MAX_RECORD + 1)
                    require(value and len(value) <= MAX_RECORD and not flags & socket.MSG_TRUNC)
                    return value
                require(type(raw) is bytes and 0 < len(raw) <= MAX_RECORD
                        and connection.send(raw) == len(raw))
                return None
            except (BlockingIOError, InterruptedError):
                continue


def group_read(group, members=False):
    require(set(group) == {"name", "device", "inode", "limits"}
            and re.fullmatch(r"nib-build-[0-9a-f]{32}", group["name"]) and group["limits"] == LIMITS)
    base = os.open("/sys/fs/cgroup", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            fd = os.open(group["name"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base)
        except FileNotFoundError:
            return {"present": False, "identity": [group["device"], group["inode"]], "processes": []}
        try:
            info = os.fstat(fd)
            require([info.st_dev, info.st_ino] == [group["device"], group["inode"]])
            def field(parent, name):
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    value = os.read(child, MAX_RECORD + 1)
                    require(len(value) <= MAX_RECORD)
                    return value.decode("ascii").strip()
                finally:
                    os.close(child)
            limits = {key: field(fd, key) for key in LIMITS}
            events = dict(line.split() for line in field(fd, "memory.events").splitlines())
            require(limits == LIMITS and int(events["oom_kill"]) == 0)
            processes = []
            def visit(parent, path):
                for pid in map(int, field(parent, "cgroup.procs").split()):
                    try:
                        row = process(pid)
                        require(kernel_read("/proc/%d/cgroup" % pid, 4096) == ("0::/" + path + "\n").encode()
                                and same_process(row))
                        processes.append(row)
                    except FileNotFoundError:
                        pass
                for name in os.listdir(parent):
                    if stat.S_ISDIR(os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode):
                        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                        try:
                            visit(child, path + "/" + name)
                        finally:
                            os.close(child)
            if members:
                visit(fd, group["name"])
            return {"present": True, "identity": [info.st_dev, info.st_ino], "limits": limits,
                    "oom_kill": 0, "processes": processes}
        finally:
            os.close(fd)
    finally:
        os.close(base)


def worker_context(request, root, consume_raw, worker_raw, group, parent_id):
    value = parsed(worker_raw)
    return {"schema": "private.metadata-source-supervised-context/v1", "producer": PRODUCER,
        "attempt_id": request["attempt_id"], "uid": os.getuid(), **clock(),
        "installer": {key: value["worker"][key] for key in ("pid", "start_ticks")},
        "supervisor": {key: value["owner"][key] for key in ("pid", "start_ticks")},
        "cgroup_identity": [group["device"], group["inode"]], "limits": LIMITS,
        "consume_intent_sha256": sha(consume_raw), "ownership_record_sha256": sha(worker_raw),
        "request_sha256": request["work_request_sha256"], "prepared_sha256": request["prepared_sha256"],
        "capsule_sha256": request["capsule_sha256"], "deadline_monotonic": request["deadline_monotonic"],
        "parent_identity": parent_id}


class KernelLease:
    """Real readback, not a caller echo. The owner and helper pidfds are opened first."""
    def __init__(self, request, root, raw_context):
        self.request, self.root, self.raw = request, Path(root), raw_context
        self.value = parsed(raw_context)
        self.worker_raw = read_record(root, "worker.json")
        self.worker = parsed(self.worker_raw)
        self.consume_raw = read_record(root, "consume.json")
        self.group_raw = read_record(Path(root) / "ownership", "group.json")
        self.group = parsed(self.group_raw, ownership=True)["group"]
        self.owner_raw = read_record(root, "owner.json")
        self.fds = []
        try:
            for key in ("owner", "helper"):
                self.fds.append(os.pidfd_open(self.worker[key]["pid"], 0))
            self.check(raw_context, request["deadline_monotonic"])
        except BaseException:
            self.close()
            raise

    def check(self, raw, deadline):
        request, value = self.request, self.value
        left(deadline)
        require(raw == self.raw and type(deadline) is type(request["deadline_monotonic"])
                and deadline == request["deadline_monotonic"])
        require(clock() == {key: request[key] for key in ("boot_id", "clock_namespace")})
        require(all(not select.select([fd], [], [], 0)[0] for fd in self.fds))
        require(all(same_process(self.worker[key]) for key in ("owner", "helper", "worker"))
                and self.worker["worker"]["pid"] == os.getpid()
                and parsed(self.owner_raw) == self.worker["owner"]
                and self.worker["helper"]["ppid"] == self.worker["owner"]["pid"]
                and self.worker["worker"]["ppid"] == self.worker["helper"]["pid"])
        require(read_record(self.root, "worker.json") == self.worker_raw
                and read_record(self.root, "consume.json") == self.consume_raw
                and read_record(self.root / "ownership", "group.json") == self.group_raw
                and read_record(self.root, "owner.json") == self.owner_raw
                and read_record(self.root, "context.json") == raw)
        group_record = parsed(self.group_raw, ownership=True)
        require(group_record["schema"] == "nib.cache-inspection-ownership/v1"
                and group_record["attempt_id"] == request["attempt_id"] and group_record["producer"] == PRODUCER
                and group_record["script_sha256"] == request["seed"]["adapter"]["sha256"]
                and group_record["supervisor"] == {key: self.worker["owner"][key]
                    for key in ("pid", "start_ticks", "pgid", "boot_id")})
        require(parsed(self.consume_raw)["request_sha256"] == sha(encoded(request))
                and self.worker["request_sha256"] == sha(encoded(request))
                and self.worker["group_sha256"] == sha(self.group_raw))
        with directory(self.root / "inputs") as parent:
            parent_id = directory_id(os.fstat(parent))
        require(worker_context(request, self.root, self.consume_raw, self.worker_raw,
                               self.group, parent_id) == value)
        observed = group_read(self.group, members=True)
        require(observed["present"] and self.worker["worker"] in observed["processes"]
                and kernel_read("/proc/self/cgroup", 4096) == ("0::/" + self.group["name"] + "\n").encode())
        left(deadline)
        return True

    def close(self):
        failed = False
        for fd in self.fds:
            try:
                os.close(fd)
            except OSError:
                failed = True
        self.fds = []
        require(not failed)


def snapshot(request, known):
    """No adoption: completeness comes only from retained pre-payload inventory."""
    require(clock() == {key: request[key] for key in ("boot_id", "clock_namespace")})
    result = {"request_sha256": sha(encoded(request)), "records": {}, "errors": [], "processes": [],
              "group": None, "inventory_complete": known.get("inventory_complete") is True}
    root = Path(request["parent"]) / ("bootstrap-" + request["attempt_id"])
    try:
        with directory(request["parent"]) as parent:
            require(directory_id(os.fstat(parent)) == request["parent_identity"])
        with directory(root) as parent:
            result["root_identity"] = directory_id(os.fstat(parent))
        require(known.get("root_identity", result["root_identity"]) == result["root_identity"])
    except FileNotFoundError:
        result["root_identity"] = None
        if known.get("root_identity") is not None:
            result["errors"].append("root-disappeared")
    except Exception:
        result["errors"].append("root-identity-unavailable")
    paths = {name: root for name in ("consume.json", "owner.json", "worker.json", "context.json", "terminal.json")}
    paths.update({name: root / "inputs" / request["attempt_id"]
                  for name in ("installation.json", "prepared.json", "bootstrap-layout.json")})
    for name, directory_path in paths.items():
        try:
            result["records"][name] = read_record(directory_path, name).decode("ascii")
        except FileNotFoundError:
            if name in known.get("records", {}):
                result["errors"].append("record-disappeared")
        except Exception:
            result["errors"].append("record-unavailable")
    rows = list(known.get("processes", []))
    worker_raw = result["records"].get("worker.json")
    if worker_raw:
        try:
            worker = parsed(worker_raw.encode("ascii"))
            require(worker["request_sha256"] == result["request_sha256"]
                    and worker["schema"] == "private.metadata-bootstrap-workers/v2"
                    and worker["owner"] in known.get("processes", [])
                    and parsed(result["records"]["owner.json"].encode()) == worker["owner"])
            rows.extend(worker[key] for key in ("owner", "helper", "worker"))
            group_raw = read_record(root / "ownership", "group.json")
            group_record = parsed(group_raw, ownership=True)
            require(sha(group_raw) == worker["group_sha256"]
                    and group_record["attempt_id"] == request["attempt_id"]
                    and group_record["producer"] == PRODUCER
                    and group_record["script_sha256"] == request["seed"]["adapter"]["sha256"]
                    and group_record["supervisor"] == {key: worker["owner"][key]
                        for key in ("pid", "start_ticks", "pgid", "boot_id")})
            result["records"]["group.json"] = group_raw.decode("ascii")
            result["group"] = group_read(group_record["group"], members=True)
            rows.extend(result["group"]["processes"])
        except Exception:
            result["errors"].append("group-unavailable")
    for name, raw in known.get("records", {}).items():
        if result["records"].get(name) != raw:
            result["errors"].append("record-changed")
    if "bootstrap-layout.json" in result["records"]:
        try:
            layout = parsed(result["records"]["bootstrap-layout.json"].encode())
            installed = root / "inputs" / request["attempt_id"]
            with directory(installed) as parent:
                require(directory_id(os.fstat(parent)) == layout["installed_directory"])
                require(file_id(os.stat("launch.lock", dir_fd=parent, follow_symlinks=False))
                        == layout["launch_lock"] and layout["launch_lock"][5] == 0)
            with directory(installed / "destination-mount") as mount:
                require(directory_id(os.fstat(mount)) == layout["mount_directory"] and not os.listdir(mount))
        except Exception:
            result["errors"].append("layout-identity-unavailable")
    for row in rows:
        try:
            if alive(row) and row not in result["processes"]:
                result["processes"].append(row)
        except Exception:
            result["errors"].append("process-unavailable")
    result["owned_absence_confirmed"] = (result["inventory_complete"] and not result["errors"]
        and not result["processes"] and result["group"] is not None and result["group"]["present"] is False)
    return result


def cancel(request, known, resource):
    """Only previously authenticated identities; never numeric-signal fallback."""
    require(clock() == {key: request[key] for key in ("boot_id", "clock_namespace")})
    sent, errors = [], []
    for row in known.get("processes", []):
        fd = None
        try:
            fd = os.pidfd_open(row["pid"], 0)
            if alive(row):
                signal.pidfd_send_signal(fd, signal.SIGTERM, None, 0)
                sent.append(row["pid"])
        except ProcessLookupError:
            pass
        except Exception:
            errors.append("pidfd-cancel-unavailable")
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    errors.append("pidfd-close-unavailable")
    # The pinned helper's write primitive also covers detached descendants when
    # the main owner is already gone. No group discovery or name-only adoption.
    raw_group = known.get("records", {}).get("group.json")
    if raw_group is not None:
        group_record = parsed(raw_group.encode("ascii"), ownership=True)
        worker = parsed(known["records"]["worker.json"].encode("ascii"))
        require(sha(raw_group.encode()) == worker["group_sha256"]
                and worker["request_sha256"] == sha(encoded(request))
                and group_record["attempt_id"] == request["attempt_id"]
                and group_record["script_sha256"] == request["seed"]["adapter"]["sha256"])
        group = group_record["group"]
        require(re.fullmatch(r"nib-build-[0-9a-f]{32}", group["name"])
                and group["limits"] == LIMITS)
        # Bad limit/OOM readback must block PASS, not block cancellation of the
        # exact already-owned inode. No health checks are prerequisites here.
        root = os.open("/sys/fs/cgroup", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            try:
                fd = os.open(group["name"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            except FileNotFoundError:
                fd = None
            if fd is not None:
                try:
                    info = os.fstat(fd)
                    require([info.st_dev, info.st_ino] == [group["device"], group["inode"]])
                    resource.write(fd, "cgroup.kill", "1")
                finally:
                    os.close(fd)
        finally:
            os.close(root)
    return {"request_sha256": sha(encoded(request)), "pidfd_term_sent": sent,
            "errors": errors, "owned_absence_confirmed": False}


if __name__ == "__main__":
    raise SystemExit("No standalone bootstrap observation or cancellation.")
