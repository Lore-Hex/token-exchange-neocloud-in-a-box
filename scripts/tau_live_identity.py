# SPDX-License-Identifier: Apache-2.0
"""Private, operator-authenticated receipt admission and Linux endpoint binding.

Receipt integrity/provenance is checked here; their measurement provenance must
also be reviewed by the operator approving the deployment digest. Neither a
model name nor a localhost response authenticates a runtime. No attestation or
quality acceptance is implied by this local lab admission contract.
"""

import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

import tau_local_vllm as local
import tau_local_llama as llama
import tau_llama_identity as cpu

transport, tau, worker = local.transport, local.tau, local.worker
require = transport.require
SERVING = frozenset(("image_sha256", "overlay_sha256", "arguments_sha256", "tokenizer_sha256",
                     "tokenizer_config_sha256", "chat_template_sha256", "precision_sha256"))
PROCESS = frozenset(("pid", "start_ticks", "boot_id_sha256", "netns_inode", "exe_sha256",
                     "argv_sha256", "listener_inode"))
CPU_SERVING = frozenset(("image_sha256", "helper_sha256", "arguments_sha256",
                        "tokenizer_sha256", "framing_sha256"))


def keys(value, expected):
    require(type(value) is dict and set(value) == set(expected))


def private_read(path, deadline):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts)
    parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        info = os.fstat(parent)
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_uid == os.getuid()
                    and stat.S_IMODE(before.st_mode) == 0o600 and 0 < before.st_size <= 1 << 20)
            require(time.monotonic() < deadline)
            raw = stream.read((1 << 20) + 1)
            def identity(info):
                return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
                        info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            require(len(raw) == before.st_size and identity(before) == identity(os.fstat(stream.fileno()))
                    and identity(before) == identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False))
                    and time.monotonic() < deadline)
            return raw
    finally:
        os.close(parent)


def source_receipt(value, deadline):
    """Bind an authenticated receipt to a locally available committed producer.

    This does not execute, trust arbitrary code, fetch commits, or manufacture a
    measurement receipt. Approving its digest remains an operator trust decision.
    """
    keys(value, ("git_sha", "script", "source_sha256"))
    require(type(value["git_sha"]) is str and re.fullmatch("[0-9a-f]{40}", value["git_sha"])
            and type(value["script"]) is str
            and re.fullmatch(r"(?:host|scripts)/[A-Za-z0-9_-]+\.py", value["script"]))
    tau.sha(value["source_sha256"])
    env = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": "",
           "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    spec = value["git_sha"] + ":" + value["script"]
    def git(*args):
        require(time.monotonic() < deadline)
        result = subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(tau.ROOT), *args],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
            timeout=min(5, max(0.001, deadline - time.monotonic())))
        require(len(result.stdout) <= 1 << 20 and time.monotonic() < deadline)
        return result.stdout
    require(git("cat-file", "-t", value["git_sha"]) == b"commit\n"
            and git("cat-file", "-t", spec) == b"blob\n")
    size = git("cat-file", "-s", spec).decode().strip()
    require(size.isascii() and size.isdecimal() and 0 < int(size) <= 1 << 20)
    require(tau.digest(git("show", spec)) == value["source_sha256"])


def measurements(row, expected):
    keys(row, expected)
    for name, digest in expected.items():
        tau.sha(digest)
        keys(row[name], ("expected_sha256", "observed_sha256"))
        require(row[name] == {"expected_sha256": digest, "observed_sha256": digest})


def process_receipt(row):
    require(row["logging"] == {"stdout": "null", "stderr": "null",
                               "request_logging": False, "access_logging": False}
            and row["logging"]["request_logging"] is False and row["logging"]["access_logging"] is False)
    process = row["process"]
    keys(process, PROCESS)
    for name in ("pid", "start_ticks", "netns_inode", "listener_inode"):
        tau.integer(process[name], 1, 2**63 - 1)
    for name in ("boot_id_sha256", "exe_sha256", "argv_sha256"):
        tau.sha(process[name])
    require(process["argv_sha256"] == row["serving"]["arguments_sha256"])


def runtime_receipt(row, role, descriptor, manifest, deadline):
    keys(row, ("schema", "producer", "role", "model", "revision", "engine_revision", "endpoint",
               "process", "serving", "model_files_sha256", "measurements", "logging"))
    require(row["schema"] == "nib.tau-local-runtime/v1" and row["role"] == role
            and row["engine_revision"] == local.REVISION)
    source_receipt(row["producer"], deadline)
    config, framing = manifest["settings"], manifest["framing"][role]
    require(row["model"] == config["roles"][role]["model"]
            and row["revision"] == config["roles"][role]["revision"]
            and re.fullmatch("[0-9a-f]{40}", row["revision"])
            and row["endpoint"] == {"host": "127.0.0.1", "port": descriptor["port"]})
    keys(row["serving"], SERVING)
    for digest in row["serving"].values():
        tau.sha(digest)
    require(row["serving"]["tokenizer_sha256"] == framing["tokenizer_sha256"])
    if role in ("candidate", "reference"):
        require(row["serving"] == config["serving"][role]
                and row["model_files_sha256"] == tau.digest(tau.regular(
                    tau.ROOT / tau.PACK / "files.sha256", 1 << 20, deadline)))
    tau.sha(row["model_files_sha256"])
    expected = {**row["serving"], "model_files_sha256": row["model_files_sha256"],
                "framing_sha256": framing["framing_sha256"]}
    measurements(row["measurements"], expected)
    process_receipt(row)


def cpu_runtime_receipt(row, role, descriptor, manifest, expires, deadline):
    keys(row, ("schema", "producer", "role", "model", "revision", "engine_revision", "endpoint",
               "process", "serving", "gguf", "model_files_sha256", "measurements", "logging", "observed_unix"))
    require(role in ("simulator", "evaluator") and row["schema"] == "nib.tau-llama-runtime/v1"
            and row["role"] == role and row["engine_revision"] == llama.REVISION)
    source_receipt(row["producer"], deadline)
    config, framing = manifest["settings"]["roles"][role], manifest["framing"][role]
    pinned = cpu.identity(config["model"], deadline)
    require(row["model"] == config["model"] == framing["served_model"]
            and row["revision"] == config["revision"] == pinned["revision"]
            and row["gguf"] == pinned["gguf"] and type(row["gguf"]["bytes"]) is int
            and row["model_files_sha256"] == pinned["model_files_sha256"]
            and row["endpoint"] == {"host": "127.0.0.1", "port": descriptor["port"]}
            and type(row["observed_unix"]) is int and 0 <= time.time() - row["observed_unix"] < 3600
            and expires <= row["observed_unix"] + 3600
            and type(framing["context_tokens"]) is int and framing["context_tokens"] == 4096
            and framing["parallel_tool_calls"] is False
            and config["generation"]["max_tokens"] <= 256)
    keys(row["serving"], CPU_SERVING)
    for name in ("tokenizer_sha256", "framing_sha256"):
        require(row["serving"][name] == framing[name] == pinned[name])
    process_receipt(row)
    measurements(row["measurements"], {**row["serving"], "gguf_sha256": pinned["gguf"]["sha256"],
        "model_files_sha256": pinned["model_files_sha256"], "executable_sha256": row["process"]["exe_sha256"]})


def tokenizer_receipt(row, runtime_sha, runtime, framing, deadline, *, cpu_kind=False):
    keys(row, ("schema", "producer", "runtime_receipt_sha256", "framing_sha256", "tokenizer_sha256",
               "context_tokens", "cases") + (("gguf_sha256", "observed_unix") if cpu_kind else
                                              ("tokenizer_config_sha256",)))
    schema = "nib.tau-llama-tokenizer/v1" if cpu_kind else "nib.tau-local-tokenizer/v1"
    require(row["schema"] == schema and row["runtime_receipt_sha256"] == runtime_sha)
    source_receipt(row["producer"], deadline)
    for name in ("framing_sha256", "tokenizer_sha256", "context_tokens"):
        require(row[name] == framing[name])
    require(type(row["context_tokens"]) is int)
    if cpu_kind:
        require(row["gguf_sha256"] == runtime["gguf"]["sha256"] and type(row["observed_unix"]) is int
                and runtime["observed_unix"] <= row["observed_unix"] <= time.time())
        expected_cases = cpu.case_hashes()
    else:
        require(row["tokenizer_config_sha256"] == runtime["serving"]["tokenizer_config_sha256"])
        expected_cases = {name: None for name in ("text", "tool-history")}
    keys(row["cases"], expected_cases)
    for name, item in row["cases"].items():
        keys(item, ("case_sha256", "independent_tokens_sha256", "endpoint_tokens_sha256",
                    "independent_count", "endpoint_count"))
        for key in ("case_sha256", "independent_tokens_sha256", "endpoint_tokens_sha256"):
            tau.sha(item[key])
        if cpu_kind:
            require(item["case_sha256"] == expected_cases[name])
        tau.integer(item["independent_count"], 1, framing["context_tokens"])
        if cpu_kind:
            require(item["independent_count"] <= cpu.probes.MAX_TOKENS)
        require(type(item["endpoint_count"]) is int
                and item["independent_count"] == item["endpoint_count"]
                and item["independent_tokens_sha256"] == item["endpoint_tokens_sha256"])


def proc_read(path, limit=1 << 20):
    # Only fixed kernel procfs paths constructed below, never private input paths.
    require(type(limit) is int and 0 < limit <= 4 << 20)
    raw = bytearray()
    with open(path, "rb", buffering=0) as stream:
        while True:
            chunk = stream.read(min(65536, limit + 1 - len(raw)))
            require(type(chunk) is bytes)
            if not chunk:
                break
            raw.extend(chunk)
            require(len(raw) <= limit)
    require(raw)
    return bytes(raw)


def process_facts(pid):
    root = Path("/proc") / str(pid)
    raw = proc_read(root / "stat", 4096)
    fields = raw[raw.rindex(b") ") + 2:].split()
    require(len(fields) >= 20 and fields[0] not in (b"Z", b"X") and root.stat().st_uid == os.getuid())
    null = os.stat(os.devnull)
    for number in (1, 2):
        fd = os.stat(root / "fd" / str(number))
        require(stat.S_ISCHR(fd.st_mode) and fd.st_rdev == null.st_rdev)
    return {"start_ticks": int(fields[19]),
            "boot_id_sha256": tau.digest(proc_read("/proc/sys/kernel/random/boot_id", 64)),
            "netns_inode": os.stat(root / "ns/net").st_ino,
            "argv_sha256": tau.digest(proc_read(root / "cmdline", 65536))}


def sockets(pid):
    result = set()
    with os.scandir(Path("/proc") / str(pid) / "fd") as entries:
        for count, entry in enumerate(entries):
            require(count < 4096)
            try:
                target = os.readlink(entry.path)
            except FileNotFoundError:
                continue
            match = re.fullmatch(r"socket:\[([0-9]+)\]", target)
            if match:
                result.add(int(match[1]))
    return result


def tcp_rows(ipv6=False):
    rows = []
    for line in proc_read("/proc/net/tcp6" if ipv6 else "/proc/net/tcp", 4 << 20).splitlines()[1:]:
        parts = line.split()
        require(len(parts) >= 10)
        rows.append((parts[1].decode(), parts[2].decode(), parts[3].decode(), int(parts[9])))
    return rows


class Endpoint:
    def __init__(self, port, receipt):
        self.port, self.expected, self.exe = port, receipt["process"], None

    def check(self, deadline, sock=None):
        require(sys.platform == "linux" and time.monotonic() < deadline)
        with worker.alarm(deadline):
            expected = self.expected
            facts = process_facts(expected["pid"])
            require(all(facts[name] == expected[name] for name in facts)
                    and facts["netns_inode"] == os.stat("/proc/self/ns/net").st_ino)
            # /proc/PID/exe is deliberately followed, to the live executable.
            with open(Path("/proc") / str(expected["pid"]) / "exe", "rb") as stream:
                info = os.fstat(stream.fileno())
                stamp = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= 128 << 20)
                if self.exe is None:
                    import hashlib
                    digest = hashlib.sha256()
                    count = 0
                    while block := stream.read(256 << 10):
                        count += len(block)
                        require(count <= info.st_size and time.monotonic() < deadline)
                        digest.update(block)
                    require(count == info.st_size and digest.hexdigest() == expected["exe_sha256"])
                    after = os.fstat(stream.fileno())
                    require(stamp == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns))
                    self.exe = stamp
                else:
                    require(stamp == self.exe)
            address = "0100007F:" + format(self.port, "04X")
            listeners = [row for row in tcp_rows() if row[2] == "0A" and row[0].endswith(address[8:])]
            require(listeners == [(address, "00000000:0000", "0A", expected["listener_inode"])])
            require(not any(row[2] == "0A" and row[0].endswith(address[8:]) for row in tcp_rows(True)))
            require(expected["listener_inode"] in sockets(expected["pid"]))
            if sock is not None:
                require(sock.getpeername() == ("127.0.0.1", self.port)
                        and sock.getsockname()[0] == "127.0.0.1")
                peer = "0100007F:" + format(sock.getsockname()[1], "04X")
                # An accept may follow connect briefly. Send no HTTP until the
                # exact established peer socket is owned by the admitted PID.
                until = min(deadline, time.monotonic() + 1)
                while True:
                    established = [row[3] for row in tcp_rows() if row[:3] == (address, peer, "01")]
                    if len(established) == 1 and established[0] in sockets(expected["pid"]):
                        break
                    require(time.monotonic() < until)
                    time.sleep(0.005)
            require(process_facts(expected["pid"]) == facts and time.monotonic() < deadline)


class GuardedConnection:
    def connect(self, sock, deadline):
        self.deployment.fresh()
        self.endpoint.check(deadline)
        super().connect(sock, deadline)
        self.endpoint.check(deadline, sock)
        self.deployment.fresh()


class GuardedClient(GuardedConnection, local.LocalVllm):
    def __init__(self, endpoint, deployment):
        super().__init__(endpoint.port)
        self.endpoint, self.deployment = endpoint, deployment


class GuardedLlamaClient(GuardedConnection, llama.LocalLlamaCPP):
    def __init__(self, endpoint, deployment, model):
        super().__init__(endpoint.port, model, 4096)
        self.endpoint, self.deployment = endpoint, deployment


class Deployment:
    def __init__(self, path, sha, benchmark, deadline):
        with worker.alarm(deadline):
            self.load(path, sha, benchmark, deadline)

    def load(self, path, sha, benchmark, deadline):
        tau.sha(sha)
        raw = private_read(path, deadline)
        require(tau.digest(raw) == sha)
        row = tau.json_value(raw)
        require(type(row) is dict)
        # V2 scopes liveness only. V3 explicitly selects mixed runtime kinds.
        mixed = row.get("schema") == "nib.tau-local-deployment/v3"
        selected = mixed or row.get("schema") == "nib.tau-local-deployment/v2"
        keys(row, ("schema", "expires_unix", "manifest", "roles") + (("selected_target",) if selected else ()))
        require(selected or row["schema"] == "nib.tau-local-deployment/v1")
        self.selected_target = row["selected_target"] if selected else None
        active = local.trial_roles(self.selected_target) if selected else tau.ROLES
        self.expires = row["expires_unix"]
        self.fresh()
        self.manifest = transport.smoke_manifest(row["manifest"], tau.digest(tau.encoded(row["manifest"])))
        require(self.manifest["benchmark_manifest_sha256"] == tau.digest(tau.encoded(benchmark))
                and self.manifest["settings"] == benchmark["settings"])
        roles = self.manifest["settings"]["roles"]
        require(roles["simulator"]["model"] != roles["evaluator"]["model"])
        keys(row["roles"], active)
        self.endpoints, self.kinds, receipts, inventories = {}, {}, {}, {}
        pids, inodes, ports = set(), set(), set()
        for role, descriptor in row["roles"].items():
            keys(descriptor, ("host", "port", "runtime_sha256", "tokenizer_sha256") +
                 (("engine_kind",) if mixed else ()))
            kind = descriptor["engine_kind"] if mixed else "vllm"
            require(kind == "vllm" or kind == "llama.cpp" and role in ("simulator", "evaluator"))
            self.kinds[role] = kind
            require(descriptor["host"] == "127.0.0.1")
            tau.integer(descriptor["port"], 1024, 65535)
            values = {}
            for receipt_kind in ("runtime", "tokenizer"):
                digest = descriptor[receipt_kind + "_sha256"]
                tau.sha(digest)
                raw = private_read(Path(path).parent / (role + "-" + receipt_kind + ".json"), deadline)
                require(tau.digest(raw) == digest)
                values[receipt_kind] = tau.json_value(raw)
            if kind == "llama.cpp":
                cpu_runtime_receipt(values["runtime"], role, descriptor, self.manifest, self.expires, deadline)
            else:
                runtime_receipt(values["runtime"], role, descriptor, self.manifest, deadline)
            tokenizer_receipt(values["tokenizer"], descriptor["runtime_sha256"], values["runtime"],
                              self.manifest["framing"][role], deadline, cpu_kind=kind == "llama.cpp")
            process = values["runtime"]["process"]
            inventories[role] = values["runtime"]["model_files_sha256"]
            pids.add(process["pid"])
            inodes.add(process["listener_inode"])
            ports.add(descriptor["port"])
            self.endpoints[role] = Endpoint(descriptor["port"], values["runtime"])
            receipts[role] = {key: descriptor[key] for key in ("runtime_sha256", "tokenizer_sha256")}
        require(len(pids) == len(inodes) == len(ports) == len(active))
        require(len(set(inventories.values())) == 3)
        self.binding = {"schema": "nib.tau-live-binding/v3" if mixed else
                                  "nib.tau-live-binding/v2" if selected else "nib.tau-live-binding/v1",
                        "deployment_sha256": sha,
                        "transport_manifest_sha256": tau.digest(tau.encoded(self.manifest)), "receipts": receipts}
        if selected:
            self.binding["selected_target"] = self.selected_target
        if mixed:
            self.binding["engine_kinds"] = dict(self.kinds)

    def require_target(self, role):
        local.trial_roles(role)
        require(self.selected_target is None or role == self.selected_target)

    def fresh(self):
        require(type(self.expires) is int and 0 < self.expires - time.time() <= 3600)

    def check(self, deadline):
        self.fresh()
        for endpoint in self.endpoints.values():
            endpoint.check(deadline)
        self.fresh()

    def session(self, channel, role, assertions):
        self.require_target(role)
        self.fresh()
        session = local.LocalVllmSession(channel, self.manifest, self.binding["transport_manifest_sha256"], role,
            {name: endpoint.port for name, endpoint in self.endpoints.items()}, assertions=assertions,
            selected_target_only=self.selected_target is not None)
        session.bridge.budget.trial_deadline = min(session.bridge.budget.trial_deadline,
                                                  time.monotonic() + self.expires - time.time())
        session.endpoints = {name: GuardedLlamaClient(endpoint, self, self.manifest["framing"][name]["served_model"])
                             if self.kinds[name] == "llama.cpp" else GuardedClient(endpoint, self)
                             for name, endpoint in self.endpoints.items()}
        return session
