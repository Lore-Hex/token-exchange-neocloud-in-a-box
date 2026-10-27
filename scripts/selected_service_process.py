# SPDX-License-Identifier: Apache-2.0
"""Closed selected-service owner inside a prepared, private PID-1 container."""

import sys
from pathlib import Path

if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        sys.exit("Use python3 -I -S -B selected_service_process.py")
    sys.path.insert(0, str(Path(__file__).absolute().parent))
    # The helper's exact-type checks must see the same classes as this owner.
    from selected_service_process import main
    sys.exit(main())

import argparse
from contextlib import ExitStack
import hashlib
import http.client
import json
import os
import re
import signal
import socket
import stat
import subprocess
import threading
import time

import engine_python_bootstrap as bootstrap
import selected_service as service
import selected_service_engine as engine_entry

ROOT, PAYLOAD = Path("/opt/nib-service"), Path("/opt/nib")
TLS_PORT = 8443
PREPARATION_STAGES = frozenset(("contract", "prepared", "inputs", "owner"))
# Linux procfs prints each native-endian 32-bit word of an IPv6 address.
_mapped = socket.inet_pton(socket.AF_INET6, "::ffff:127.0.0.1")
TLS_MAPPED_LOOPBACK = "".join(format(int.from_bytes(_mapped[index:index + 4], sys.byteorder), "08X")
                              for index in range(0, 16, 4))
snapshot = service.pilot.pilot.source_module("snapshot")
require = service.require


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def stamp(path):
    value = path.stat()
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def source_files(root, deadline):
    # The prepared image carries the complete Python source tree, not a guessed
    # subset of the pilot/tau modules' transitive imports. No modules are loaded
    # from this inventory; it only authenticates the reviewed image's files.
    pending, names, visited = [(root / "scripts", 0)], set(), 0
    while pending:
        path, depth = pending.pop()
        require(depth <= 16 and time.monotonic() < deadline, "contract")
        with os.scandir(path) as entries:
            for entry in entries:
                visited += 1
                require(visited <= 16384 and time.monotonic() < deadline, "contract")
                require(not entry.is_symlink(), "identity")
                if entry.is_dir(follow_symlinks=False):
                    pending.append((Path(entry.path), depth + 1))
                else:
                    require(entry.is_file(follow_symlinks=False), "identity")
                    name = Path(entry.path).relative_to(root).as_posix()
                    require(entry.name.endswith(".py") or name == "scripts/e2e/pins.json", "identity")
                    if entry.name.endswith(".py"):
                        names.add(name)
                        require(len(names) <= 2045, "contract")
    if (root / "checker").exists():
        import selected_service_conformance as conformance
        names.update("checker/" + name for name in conformance.payload_files(root / "checker", deadline))
    return names | {"scripts/e2e/pins.json", "bin/nib-pilot-api", "bin/trcs"}


def preparation_receipt(root, lab_sha256):
    """Offline packaging helper, NOT proof of model preparation or deployment."""
    names = source_files(root, time.monotonic() + service.observer.SECONDS)
    require(0 < len(names) <= 2048, "contract")
    return {"schema": "nib.selected-service-prepared/v1", "lab_sha256": lab_sha256,
            "files": {name: digest(bootstrap.read_source(str(root / name), 128 << 20))
                      for name in sorted(names)}}


def launcher_source(root, *, selected):
    prefix = b'import sys\nif not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):\n    sys.exit("Isolated lab required.")\n'
    sources = [bootstrap.read_source(str(root / "scripts" / name)) for name in
               ("engine_python_bootstrap.py", "engine_exception.py", "engine_lab_bootstrap.py", "engine_lab.py")]
    base = prefix + b"\n".join(sources)
    return engine_entry.compose_launcher(base,
        bootstrap.read_source(str(root / "scripts/selected_service_engine.py"))) if selected else base


def prepare_payload(payload):
    """Derive a NEW service payload from an already validated non-owner lab.

    The builder must first use the existing recorder's exact bundle validator,
    apply/verify the reviewed application offline, and preserve the original
    receipt. This transformation neither applies that overlay nor runs a model.
    """
    source_root = Path(__file__).absolute().parents[1]
    require(payload["launch.py"] == launcher_source(source_root, selected=False), "identity")
    contract = service.correctness.strict_json(payload["lab.json"])
    engine_entry.canonical_arguments(contract)
    service.models.validate_contract(contract)
    require(contract["model_profile"] == service.models.select("uncensored")
            and set(payload) == set(contract["sources"]) | {"lab.json", "python"}
            and all(digest(payload[name]) == sha for name, sha in contract["sources"].items()), "identity")
    service.models.validate_manifest("uncensored", payload["pack/files.sha256"])
    require(payload["python"] == service.engine_lab.wrapper_bytes(contract["interpreter"]["resolved"],
        contract["environment"], digest(payload["lab.json"])), "identity")
    result = dict(payload)
    result["launch.py"] = launcher_source(source_root, selected=True)
    contract["sources"]["launch.py"] = digest(result["launch.py"])
    result["lab.json"] = json.dumps(contract, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    result["python"] = service.engine_lab.wrapper_bytes(contract["interpreter"]["resolved"],
        contract["environment"], digest(result["lab.json"]))
    return result


class Prepared:
    def __init__(self, expected, deadline):
        require(sys.version_info[:3] == (3, 12, 3), "contract")
        require(type(expected) is str and re.fullmatch("[0-9a-f]{64}", expected), "contract")
        raw = bootstrap.read_source(str(ROOT / "prepared.json"))
        require(digest(raw) == expected, "contract")
        row = service.correctness.strict_json(raw)
        require(type(row) is dict and set(row) == {"schema", "lab_sha256", "files"}
                and row["schema"] == "nib.selected-service-prepared/v1"
                and type(row["files"]) is dict and 0 < len(row["files"]) <= 2048
                and set(row["files"]) == source_files(ROOT, deadline), "contract")
        self.stamps = {}
        total = 0
        for name, expected_sha in row["files"].items():
            path = ROOT / name
            raw = bootstrap.read_source(str(path), 128 << 20)
            total += len(raw)
            require(digest(raw) == expected_sha and total <= 256 << 20
                    and time.monotonic() < deadline, "identity")
            self.stamps[path] = stamp(path)
        self.stamps[ROOT / "prepared.json"] = stamp(ROOT / "prepared.json")
        self.files = row["files"]
        pins = service.correctness.strict_json(bootstrap.read_source(str(ROOT / "scripts/e2e/pins.json")))
        require(self.files["bin/trcs"] == pins["trcs"]["binary_sha256"], "identity")
        raw = bootstrap.read_source(str(PAYLOAD / "lab.json"))
        require(digest(raw) == row["lab_sha256"], "identity")
        self.lab_sha, self.lab = row["lab_sha256"], service.correctness.strict_json(raw)
        engine_entry.canonical_arguments(self.lab)
        service.models.validate_contract(self.lab)
        require(self.lab["model_profile"] == service.models.select("uncensored")
                and self.lab["manifest_sha256"] == service.models.select("uncensored")["manifest_sha256"]
                and digest(bootstrap.read_source(str(PAYLOAD / "pack/files.sha256"))) ==
                self.lab["manifest_sha256"], "model")
        require(type(self.lab["sources"]) is dict and len(self.lab["sources"]) <= 110, "contract")
        for name, sha in self.lab["sources"].items():
            require(type(name) is str and re.fullmatch(r"[A-Za-z0-9_./-]+", name)
                    and all(part not in ("", ".", "..") for part in name.split("/")), "contract")
            path = PAYLOAD / name
            raw = bootstrap.read_source(str(path), 64 << 20 if name == "nib" else 2 << 20)
            total += len(raw)
            require(digest(raw) == sha and total <= 384 << 20
                    and time.monotonic() < deadline, "identity")
            self.stamps[path] = stamp(path)
        self.stamps[PAYLOAD / "lab.json"] = stamp(PAYLOAD / "lab.json")
        launcher = launcher_source(ROOT, selected=True)
        require(bootstrap.read_source(str(PAYLOAD / "launch.py")) == launcher, "identity")
        interpreter = self.lab["interpreter"]
        self.interpreter = interpreter["resolved"]
        require(str(Path(sys.executable).resolve()) == self.interpreter, "identity")
        raw = bootstrap.read_source(self.interpreter, 16 << 20)
        require(digest(raw) == interpreter["sha256"] and len(raw) == interpreter["bytes"], "identity")
        wrapper = service.engine_lab.wrapper_bytes(self.interpreter, self.lab["environment"], self.lab_sha)
        require(bootstrap.read_source(str(PAYLOAD / "python")) == wrapper, "identity")
        self.stamps[PAYLOAD / "python"] = stamp(PAYLOAD / "python")
        self.stamps[Path(self.interpreter)] = stamp(Path(self.interpreter))
        self.mounts = self.mount_state()
        self.receipt_sha256 = expected

    @staticmethod
    def mount_state():
        require(sys.platform == "linux" and os.getpid() == 1 and os.getuid() != 0, "contract")
        service.engine_lab.check_scratch_mounts()
        values = []
        for path in (Path("/"), ROOT, PAYLOAD, Path("/model")):
            info, flags = path.stat(), os.statvfs(path).f_flag
            require(flags & os.ST_RDONLY, "mounts")
            values.append((info.st_dev, info.st_ino, flags))
        return tuple(values)

    def check(self, deadline):
        require(self.mount_state() == self.mounts
                and all(stamp(path) == value for path, value in self.stamps.items()), "mounts")
        for name, expected in (("memory.max", service.MEMORY_BYTES), ("memory.swap.max", 0)):
            raw = service.observer.read_kernel("/sys/fs/cgroup/" + name, 32, deadline)
            require(raw == str(expected).encode() + b"\n", "runtime-limits")
        require(time.monotonic() < deadline, "deadline")
        return True


class ProcessOwner:
    def __init__(self, prepared, inputs, hostname, shutdown):
        require(type(hostname) is str and re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", hostname), "contract")
        self.prepared, self.inputs, self.hostname = prepared, inputs, hostname
        self.shutdown = shutdown
        self.processes, self.commands, self.identities = {}, {}, {}
        self.trust = service.pilot.tls_client_context(inputs.root / "cert.pem")
        self.env = service.pilot.pilot.controlled_env(inputs.root / "control")

    def check_mounts(self, deadline):
        return self.prepared.check(deadline)

    def spawn(self, role, command, env):
        require(not self.shutdown.is_set(), "cancelled")
        child = subprocess.Popen(command, env=env, cwd="/", stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
        self.processes[role], self.commands[role] = child, command
        return child

    def start(self, role, contract, inputs, register, deadline):
        require(self.prepared.lab["execution_profile"] == contract.execution_profile, "runtime-limits")
        if service.ordinary_profile(contract.lifetime_profile):
            service.progress("weights" if role == "engine" else role + "-starting", contract,
                             self.prepared.receipt_sha256)
        if role == "engine":
            # Full existing manifest checker, not an asserted prepared=true bit.
            validator = self.spawn("weights", [str(PAYLOAD / "nib"), "model", "check-local",
                "--workers", "8", str(PAYLOAD / "pack"), "/model"], self.env)
            valid = False
            try:
                while True:
                    require(not self.shutdown.is_set(), "cancelled")
                    remaining = deadline - time.monotonic() - service.REAP_SECONDS
                    require(remaining > 0, "deadline")
                    try:
                        valid = validator.wait(timeout=min(.1, remaining)) == 0
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                absent = self.reap("weights", validator, min(deadline, time.monotonic() + service.REAP_SECONDS))
            require(valid and absent, "model")
            self.check_mounts(deadline)
            if service.ordinary_profile(contract.lifetime_profile):
                service.progress("engine-starting", contract, self.prepared.receipt_sha256)
            command = [self.prepared.interpreter, "-I", "-S", "-B", str(PAYLOAD / "launch.py"),
                       self.prepared.lab_sha, "selected-service-engine"]
            env = self.prepared.lab["environment"]
        elif role == "guard":
            command = [str(ROOT / "bin/nib-pilot-api"), *contract.guard_arguments(inputs)]
            env = self.env
        elif role == "tls":
            command = service.pilot.pilot.shim_command(ROOT / "bin/trcs", TLS_PORT, 8001,
                inputs.root / "cert.pem", inputs.root / "key.pem", host="0.0.0.0")
            command[command.index("--max-inflight") + 1] = str(contract.max_inflight)
            if contract.execution_profile in engine_entry.LONG_CONTEXT_PROFILES:
                command[command.index("--max-request-bytes") + 1] = str(service.request_bytes(contract.execution_profile))
                command += ["--shutdown-grace", str(contract.request_seconds + 10) + "s"]
            env = self.env
        else:
            raise service.Rejected("contract")
        require(time.monotonic() < deadline, "deadline")
        register(self.spawn(role, command, env))

    def bound(self, role, child, deadline):
        port = {"engine": 8000, "guard": 8001, "tls": TLS_PORT}[role]
        rows = [row for row in service.identity.tcp_rows() if row[2] == "0A"
                and row[0].endswith(":" + format(port, "04X"))]
        tls_ipv6 = False
        if role == "tls":
            # Pinned trcs uses Go net.Listen("tcp", "0.0.0.0:8443"). Go's
            # wildcard selection may produce a single dual-stack tcp6 socket.
            ipv6 = [row for row in service.identity.tcp_rows(True) if row[2] == "0A"
                    and row[0].endswith(":" + format(port, "04X"))]
            require(len(rows) + len(ipv6) <= 1, "identity")
            tls_ipv6 = bool(ipv6)
            rows += ipv6
        if not rows:
            return None
        command = self.commands[role]
        argv_sha = digest(b"\0".join(os.fsencode(value) for value in command) + b"\0")
        exe_sha = (self.prepared.lab["interpreter"]["sha256"] if role == "engine"
                   else self.prepared.files["bin/" + ("trcs" if role == "tls" else "nib-pilot-api")])
        if role == "engine":
            value = service.owned_endpoint(child, executable_sha256=exe_sha,
                                            arguments_sha256=argv_sha, deadline=deadline)
            current = dict(value.expected)
        else:
            observed = service.observer.process(child.pid, deadline)
            require(observed["parent"] == os.getpid() and observed["argv"] == argv_sha, "identity")
            zero = "0" * (32 if tls_ipv6 else 8)
            address = (zero if role == "tls" else "0100007F") + ":" + format(port, "04X")
            require(len(rows) == 1 and rows[0][:3] == (address, zero + ":0000", "0A")
                    and rows[0][3] in service.identity.sockets(child.pid)
                    and (role == "tls" or not any(row[2] == "0A"
                        and row[0].endswith(":" + format(port, "04X"))
                        for row in service.identity.tcp_rows(True))), "identity")
            # Deliberately follow the live /proc executable, not its original path.
            with open("/proc/" + str(child.pid) + "/exe", "rb") as stream:
                info = os.fstat(stream.fileno())
                require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= 128 << 20, "identity")
                raw = stream.read((128 << 20) + 1)
                require(len(raw) == info.st_size and digest(raw) == exe_sha, "identity")
            current = {"pid": child.pid, **service.identity.process_facts(child.pid),
                       "exe_sha256": exe_sha, "listener_inode": rows[0][3]}
            if role == "tls":
                current["listener_ipv6"] = tls_ipv6
            require(service.observer.process(child.pid, deadline) == observed, "identity")
            value = service.identity.Endpoint(port, {"process": current}) if role == "guard" else current
        require(role not in self.identities or self.identities[role] == current, "identity")
        self.identities[role] = current
        require(time.monotonic() < deadline, "deadline")
        return value

    def peer(self, role, bound, sock, deadline):
        if role != "tls":
            bound.check(deadline, sock)
            return
        require(sock.getpeername() == ("127.0.0.1", TLS_PORT)
                and sock.getsockname()[0] == "127.0.0.1", "identity")
        until = min(deadline, time.monotonic() + 1)
        while True:
            peers = []
            for ipv6, loopback in ((False, "0100007F"), (True, TLS_MAPPED_LOOPBACK)):
                local = loopback + ":" + format(TLS_PORT, "04X")
                remote = loopback + ":" + format(sock.getsockname()[1], "04X")
                peers.extend((ipv6, row[3]) for row in service.identity.tcp_rows(ipv6)
                             if row[:3] == (local, remote, "01"))
            require(len(peers) <= 1, "identity")
            if peers:
                require(peers[0][0] == bound["listener_ipv6"], "identity")
            if peers and peers[0][1] in service.identity.sockets(bound["pid"]):
                return
            require(time.monotonic() < until, "identity")
            time.sleep(.005)

    def probe(self, role, child, deadline):
        # The fixed PR317 alarm is a required deployment dependency. A socket
        # timeout alone cannot bound slow headers/body chunks over many reads.
        try:
            with service.identity.worker.alarm(min(deadline, time.monotonic() + service.observer.SECONDS)):
                return self._probe(role, child, deadline)
        except service.identity.worker.Abort as error:
            if error.status != "timeout":
                raise
            return None

    def _probe(self, role, child, deadline):
        bound = self.bound(role, child, deadline)
        if bound is None:
            return None
        port = {"engine": 8000, "guard": 8001, "tls": TLS_PORT}[role]
        path = {"engine": "/v1/models", "guard": "/health", "tls": "/healthz"}[role]
        try:
            with ExitStack() as stack:
                raw = stack.enter_context(socket.create_connection(("127.0.0.1", port),
                    timeout=min(2, max(.001, deadline - time.monotonic()))))
                self.peer(role, bound, raw, deadline)  # Before sending any HTTP.
                connection = stack.enter_context(self.trust.wrap_socket(raw, server_hostname=self.hostname)) \
                    if role == "tls" else raw
                connection.sendall(("GET " + path + " HTTP/1.1\r\nHost: " + self.hostname +
                                    "\r\nConnection: close\r\n\r\n").encode("ascii"))
                response = stack.enter_context(http.client.HTTPResponse(connection))
                response.begin()
                body = response.read((1 << 20) + 1)
                require(len(body) <= 1 << 20 and time.monotonic() < deadline, "readiness")
                if response.status != 200:
                    return None
        except (OSError, http.client.HTTPException):
            return None
        if self.bound(role, child, deadline) is None:
            return None
        if role == "engine":
            self.check_mounts(deadline)
            profile = self.prepared.lab["execution_profile"]
            return service.EngineReady(bound, body, engine_entry.execution_limits(profile)["context_tokens"],
                                       service.MEMORY_BYTES, 0, profile)
        return True

    def drain(self, role, child, deadline):
        # Same front-first TERM/wait contract as pilot.stop_serving. Its fixed
        # 140+10 helper cannot honor a shorter absolute deadline, so forced
        # cleanup stays in reap(), and can never count as a graceful drain.
        return self.begin_drain(role, child, deadline) and self.wait_drain(role, child, deadline)

    def begin_drain(self, role, child, deadline):
        if child.poll() is not None:
            return False
        child.terminate()
        return True

    def wait_drain(self, role, child, deadline):
        # The supervisor supplies the profile-bound absolute deadline. Never
        # restart a timeout here or cap a long stream at the legacy 140 seconds.
        wait = service.request_seconds(self.prepared.lab["execution_profile"]) + 20
        return child.wait(timeout=max(0, min(wait, deadline - time.monotonic()))) == 0

    def reap(self, role, child, deadline):
        reaped = False
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(child.pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                child.wait(timeout=max(0, min(10, deadline - time.monotonic())))
                reaped = True
            except subprocess.TimeoutExpired:
                reaped = False
        while reaped and time.monotonic() <= deadline:
            snapshot.reap_group_children(child.pid)
            try:
                os.killpg(child.pid, 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
            time.sleep(min(.05, max(0, deadline - time.monotonic())))
        return False

    def reap_unregistered(self, deadline):
        # This CLI must be PID 1 in its own container. Only that closed namespace
        # permits sweeping a Popen interrupted before it returned its child PID.
        require(sys.platform == "linux" and os.getpid() == 1, "contract")
        while time.monotonic() <= deadline:
            for child in self.processes.values():
                child.poll()
            for _ in range(4096):
                try:
                    if os.waitpid(-1, os.WNOHANG)[0] == 0:
                        break
                except ChildProcessError:
                    break
            pids = []
            for count, path in enumerate(Path("/proc").iterdir(), 1):
                require(count <= 8192 and time.monotonic() <= deadline, "identity")
                if path.name.isdecimal() and int(path.name) > 1:
                    pids.append(int(path.name))
                    require(len(pids) <= 4096, "identity")
            if not pids:
                return all(child.poll() is not None for child in self.processes.values())
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    return False
            time.sleep(min(.05, max(0, deadline - time.monotonic())))
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-sha256", required=True)
    parser.add_argument("--provider-config", type=Path, required=True)
    parser.add_argument("--api-key-file", type=Path, required=True)
    parser.add_argument("--cert", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--input-price", required=True)
    parser.add_argument("--output-price", required=True)
    parser.add_argument("--hard-stop-at", required=True)
    parser.add_argument("--lifetime-profile", default=service.SMOKE_PROFILE)
    parser.add_argument("--execution-profile", default=engine_entry.EAGER_PROFILE)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--inference-smoke", action="store_true")
    mode.add_argument("--conformance-smoke", action="store_true")
    mode.add_argument("--protocol-smoke", action="store_true")
    mode.add_argument("--tool-smoke", action="store_true")
    mode.add_argument("--long-qualification", action="store_true")
    args = parser.parse_args(argv)
    shutdown = threading.Event()
    handlers = {number: signal.signal(number, lambda *_: shutdown.set())
                for number in (signal.SIGINT, signal.SIGTERM)}
    result, stage, contract = None, "contract", None
    def publish(row):
        if args.execution_profile in engine_entry.BOUND_PROFILES and row["schema"] != service.EXECUTION_SCHEMA:
            row = service.execution_result(row, args.execution_profile, args.prepared_sha256
                if re.fullmatch("[0-9a-f]{64}", args.prepared_sha256) else None)
        print(json.dumps(row, sort_keys=True))
    try:
        contract = service.Contract.begin(args.hard_stop_at, input_price=args.input_price,
            output_price=args.output_price, lifetime_profile=args.lifetime_profile,
            execution_profile=args.execution_profile)
        service.qualification_options(args.long_qualification, contract.lifetime_profile,
            contract.execution_profile, smoke=any((args.inference_smoke, args.conformance_smoke,
                                                   args.protocol_smoke, args.tool_smoke)))
        if args.long_qualification:
            import selected_long_qualification as qualification
            require(contract.hard_monotonic - time.monotonic() > service.engine_lab.STARTUP_SECONDS
                    + qualification.workload_profile(contract.execution_profile).run_seconds
                    + contract.cleanup_reserve, "deadline")
        require(args.lifetime_profile == service.SMOKE_PROFILE or not any((args.inference_smoke,
            args.conformance_smoke, args.protocol_smoke, args.tool_smoke)), "contract")
        service.progress("preparation", contract, args.prepared_sha256)
        stage = "prepared"
        prepared = Prepared(args.prepared_sha256, contract.startup_deadline)
        require(prepared.lab["execution_profile"] == contract.execution_profile, "runtime-limits")
        if args.conformance_smoke or args.protocol_smoke:
            require("checker/trial.json" in prepared.files, "contract")
        stage = "inputs"
        service.progress("inputs", contract, args.prepared_sha256)
        with service.snapshot_inputs(provider=args.provider_config, cert=args.cert, key=args.key,
                                     api_key=args.api_key_file) as inputs:
            stage = "owner"
            owner = ProcessOwner(prepared, inputs, args.hostname, shutdown)
            result = service.Supervisor(contract, inputs, owner, shutdown).run(
                inference_smoke=args.inference_smoke, conformance_smoke=args.conformance_smoke,
                protocol_smoke=args.protocol_smoke, tool_smoke=args.tool_smoke,
                **({"long_qualification": True} if args.long_qualification else {}))
        if (args.inference_smoke or args.conformance_smoke or args.protocol_smoke or args.tool_smoke
                or args.long_qualification) and shutdown.is_set() and result["status"] != "failed":
            result.update(status="failed", reason="cancelled")
        publish(result)
        return 0 if result["status"] in ("retired", "cancelled") else 1
    except BaseException as error:
        if args.long_qualification:
            if result is not None:
                result.update(status="failed", reason="inputs-changed")
            else:
                result = {"schema": service.LONG_SCHEMA, "status": "failed", "reason": "preparation",
                    "deployable": False, "hardware_validated": False, "mode": service.LONG_MODE,
                    "qualification": None, "lifetime": contract.lifetime if contract is not None else None,
                    "prepared_sha256": args.prepared_sha256
                        if re.fullmatch("[0-9a-f]{64}", args.prepared_sha256) else None}
            publish(result)
            return 1
        if not (args.inference_smoke or args.conformance_smoke or args.protocol_smoke or args.tool_smoke):
            if service.ordinary_profile(args.lifetime_profile):
                publish({"schema": service.ORDINARY_SCHEMA, "status": "failed",
                    "reason": "preparation", "deployable": False, "hardware_validated": False,
                    "lifetime": contract.lifetime if contract is not None else None,
                    "prepared_sha256": args.prepared_sha256
                        if re.fullmatch("[0-9a-f]{64}", args.prepared_sha256) else None})
                return 1
            if args.execution_profile in engine_entry.BOUND_PROFILES:
                publish({"schema": "nib.selected-service-lifecycle/v1", "status": "failed", "reason": "preparation",
                         "deployable": False, "hardware_validated": False})
                return 1
            print('{"schema":"nib.selected-service-lifecycle/v1","status":"failed","reason":"preparation",'
                  '"deployable":false,"hardware_validated":false}')
            return 1
        if result is not None:
            result.update(status="failed", reason="inputs-changed")
        else:
            category = error.category if isinstance(error, service.Rejected) \
                and type(error.category) is str and error.category in service.FAILURES else "internal"
            result = {"schema": service.INFERENCE_SCHEMA, "status": "failed", "reason": "preparation",
                      "deployable": False, "hardware_validated": False,
                      "mode": service.INFERENCE_MODE, "inference": None,
                      "preparation_stage": stage, "preparation_category": category}
            if args.conformance_smoke:
                result.update(schema=service.CONFORMANCE_SCHEMA, mode=service.CONFORMANCE_MODE, conformance=None)
            if args.protocol_smoke:
                result.update(schema=service.PROTOCOL_SCHEMA, mode=service.PROTOCOL_MODE, protocol=None)
            if args.tool_smoke:
                result.update(schema=service.TOOL_SCHEMA, mode=service.TOOL_MODE, tool_roundtrip=None)
        publish(result)
        return 1
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
