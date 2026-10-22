# SPDX-License-Identifier: Apache-2.0
"""Fresh private CPU-pair receipts and bounded release; not a tau trial."""

import os
import select
import selectors
import stat
import subprocess
import time

import tau_cpu_parity as cpu

identity, llama, probes = cpu.identity, cpu.llama, cpu.probes
tau, require = identity.tau, cpu.require
ROLES = ("simulator", "evaluator")
FILES = tuple(role + "-" + kind + ".json" for role in ROLES for kind in ("runtime", "tokenizer"))
READY = b"NIB-TAU-CPU-READY "
DONE = b"NIB-TAU-CPU-DONE "
RESERVE = 60


def write_exact(fd, name, raw):
    require(name in FILES and type(raw) is bytes and 0 < len(raw) <= 16384)
    target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(target, "wb") as stream:
        require(stream.write(raw) == len(raw))
        stream.flush()
        os.fsync(stream.fileno())


def send(fd, raw, deadline):
    require(type(raw) is bytes and 0 < len(raw) <= 65536)
    previous = os.get_blocking(fd)
    os.set_blocking(fd, False)
    try:
        offset = 0
        while offset < len(raw):
            remaining = deadline - time.monotonic()
            require(remaining > 0 and select.select([], [fd], [], remaining)[1])
            try:
                offset += os.write(fd, raw[offset:])
            except BlockingIOError:
                continue
    finally:
        os.set_blocking(fd, previous)


def release(raw, ready_sha):
    require(type(raw) is bytes and len(raw) <= 512 and raw.startswith(DONE)
            and raw.endswith(b"\n") and raw.count(b"\n") == 1)
    value = llama.worker.decode(raw[len(DONE):-1])
    require(value == {"ready_sha256": ready_sha, "released": True}
            and value["released"] is True)
    return value


def hold(raw, owned, processes, deadline, read_fd=0, write_fd=1, *, announce=True):
    if announce:
        send(write_fd, READY + raw + b"\n", min(deadline, time.monotonic() + 5))
    data = bytearray()
    while True:
        require(time.monotonic() < deadline and all(p.poll() is None for p in processes))
        for endpoint in owned:
            endpoint.check(deadline)
        if select.select([read_fd], [], [], min(0.1, max(0, deadline - time.monotonic())))[0]:
            block = os.read(read_fd, 513 - len(data))
            require(block)
            data.extend(block)
            require(len(data) <= 512)
            if b"\n" in data:
                release(bytes(data), tau.digest(raw))
                require(all(p.poll() is None for p in processes))
                for endpoint in owned:
                    endpoint.check(deadline)
                return


def receipts(role, model, endpoint, rows, producer, image_sha, helper_sha, deadline):
    pinned = identity.cpu.identity(model, deadline)
    require(role in ROLES and len(rows) == 4
            and [row["id"] for row in rows] == list(probes.IDS)
            and all(row["result"] == "pass" and row["local"] == row["server"] for row in rows))
    endpoint.check(deadline)
    serving = {"image_sha256": image_sha, "helper_sha256": helper_sha,
               "arguments_sha256": endpoint.expected["argv_sha256"],
               **{name: pinned[name] for name in ("tokenizer_sha256", "framing_sha256")}}
    measured = {**serving, "gguf_sha256": pinned["gguf"]["sha256"],
                "model_files_sha256": pinned["model_files_sha256"],
                "executable_sha256": endpoint.expected["exe_sha256"]}
    runtime = {"schema": "nib.tau-llama-runtime/v1", "producer": producer, "role": role,
               "model": model, "revision": pinned["revision"], "engine_revision": llama.REVISION,
               "endpoint": {"host": "127.0.0.1", "port": endpoint.port}, "process": endpoint.expected,
               "serving": serving, "gguf": pinned["gguf"], "model_files_sha256": pinned["model_files_sha256"],
               "measurements": {name: {"expected_sha256": sha, "observed_sha256": sha}
                                for name, sha in measured.items()},
               "logging": {"stdout": "null", "stderr": "null", "request_logging": False, "access_logging": False},
               "observed_unix": int(time.time())}
    raw = tau.encoded(runtime)
    tokenizer = {"schema": "nib.tau-llama-tokenizer/v1", "producer": producer,
                 "runtime_receipt_sha256": tau.digest(raw), "gguf_sha256": pinned["gguf"]["sha256"],
                 "framing_sha256": pinned["framing_sha256"], "tokenizer_sha256": pinned["tokenizer_sha256"],
                 "context_tokens": 4096, "observed_unix": int(time.time()), "cases": {}}
    for row in rows:
        tokenizer["cases"][row["id"]] = {
            "case_sha256": identity.cpu.case_hashes()[row["id"]],
            "independent_tokens_sha256": row["local"]["tokens_sha256"],
            "endpoint_tokens_sha256": row["server"]["tokens_sha256"],
            "independent_count": row["local"]["count"], "endpoint_count": row["server"]["count"]}
    return {role + "-runtime.json": raw, role + "-tokenizer.json": tau.encoded(tokenizer)}


def run(archive, models, work, producer, image_sha, *, read_fd=0, write_fd=1, probe=None):
    deadline = time.monotonic() + cpu.TOTAL_SECONDS - RESERVE
    report = {"schema": "nib.tau-cpu-session/v1", "result": "fail", "stage": "inputs",
              "receipts": {}, "ready_sha256": None, "acknowledged": False,
              "cleanup": "unknown", "quality_eligible": False, "confidential": False, "live_admission": False}
    if probe is not None:
        report.update(schema="nib.tau-cpu-session/v2", probe=None)
    processes, owned, originals = [], [], {}
    pilot = None
    try:
        with llama.worker.alarm(deadline):
            cpu.confinement()
            tau.sha(image_sha)
            pilot, snapshot = cpu.tools()
            pins = cpu.settings()
            env = pilot.controlled_env(work / "control")
            env.update(GOPROXY="off", GOSUMDB="off", GIT_ALLOW_PROTOCOL="", OMP_NUM_THREADS="4",
                       OPENBLAS_NUM_THREADS="4", PATH="/opt/cmake-wheel/cmake/data/bin:/usr/local/bin:/usr/bin:/bin")
            require(len(models) == 2 and archive.stat().st_size <= 64 << 20
                    and pilot.file_digest(archive) == pins["engine"]["archive_sha256"])
            for model, weight in zip(cpu.MODEL_IDS, models):
                require(weight.stat().st_size == pins["models"][model]["bytes"]
                        and pilot.file_digest(weight) == pins["models"][model]["sha256"])
            report["stage"] = "build"
            server, helper = cpu.build(pilot, archive, work, env)
            server_sha, helper_sha = pilot.file_digest(server), pilot.file_digest(helper)
            for role, model, weight in zip(ROLES, cpu.MODEL_IDS, models):
                until = min(deadline, time.monotonic() + cpu.MODEL_SECONDS)
                report["stage"] = "parity"
                local = cpu.native_tokens(helper, weight, env, snapshot, min(until, time.monotonic() + 60))
                port = pilot.free_port()
                argv = pilot.engine_command(server, weight, model, port, "unused")
                require(argv[-2:] == ["--api-key-file", "unused"])
                argv = argv[:-2]  # Same closed synthetic namespace as the parity prerequisite.
                process = subprocess.Popen(argv, env=env, cwd=work, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                processes.append(process)
                ready_until = min(until, time.monotonic() + 90)
                endpoint = cpu.endpoint(process, port, server_sha, argv, ready_until)
                owned.append(endpoint)
                with llama.worker.alarm(ready_until):
                    pilot.ready(process, port, "/health")
                rows = []
                cpu.compare(cpu.MeasuredClient(port, model, endpoint), model, local, until, rows)
                require(pilot.file_digest(weight) == pins["models"][model]["sha256"]
                        and pilot.file_digest(helper) == helper_sha)
                originals.update(receipts(role, model, endpoint, rows, producer, image_sha, helper_sha, until))
            require(len({p.pid for p in processes}) == 2 and len({e.port for e in owned}) == 2
                    and len({e.expected["listener_inode"] for e in owned}) == 2)
            for endpoint in owned:
                endpoint.check(deadline)
            directory = work / "receipts"
            directory.mkdir(mode=0o700)
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                for name in FILES:
                    write_exact(fd, name, originals[name])
                os.fsync(fd)
            finally:
                os.close(fd)
            packet = {"schema": "nib.tau-cpu-pair-ready/v1", "producer": producer,
                      "image_sha256": image_sha, "expires_unix": int(time.time() + deadline - time.monotonic()),
                      "receipts": {name: originals[name].decode("ascii") for name in FILES}}
            raw = probes.encoded(packet)
            require(len(raw) <= 32768)
            report.update(stage="hold", receipts={name: tau.digest(originals[name]) for name in FILES},
                          ready_sha256=tau.digest(raw))
            if probe is not None:
                import tau_cpu_probe
                send(write_fd, READY + raw + b"\n", min(deadline, time.monotonic() + 5))
                report["probe"] = tau_cpu_probe.execute(probe, dict(zip(ROLES, owned)),
                    {role: llama.worker.decode(originals[role + "-runtime.json"]) for role in ROLES},
                    processes, os.dup(read_fd), os.dup(write_fd), min(deadline, time.monotonic() + tau_cpu_probe.SECONDS))
                # Model responses go only to the isolated scorer's pipe. The
                # separate fixed metadata frame lets the host handle failures.
                send(2, tau_cpu_probe.REPORT + probes.encoded(report["probe"]) + b"\n",
                     min(deadline, time.monotonic() + 5))
                hold(raw, owned, processes, deadline, read_fd, write_fd, announce=False)
            else:
                hold(raw, owned, processes, deadline, read_fd, write_fd)
            report.update(result="pass" if probe is None or report["probe"]["result"] == "pass" else "fail",
                          stage="released", acknowledged=True)
    except BaseException:
        report["result"] = "fail"
    finally:
        stopped = True
        for process in reversed(processes):
            try:
                if not pilot.stop(process):
                    stopped = False
            except BaseException:
                stopped = False
        report["cleanup"] = "confirmed" if stopped else "unknown"
        if not stopped:
            report["result"] = "fail"
    return validate(report)


def validate(row):
    probing = type(row) is dict and row.get("schema") == "nib.tau-cpu-session/v2"
    identity.keys(row, ("schema", "result", "stage", "receipts", "ready_sha256", "acknowledged",
                        "cleanup", "quality_eligible", "confidential", "live_admission") + (("probe",) if probing else ()))
    require(row["schema"] == ("nib.tau-cpu-session/v2" if probing else "nib.tau-cpu-session/v1")
            and row["result"] in ("pass", "fail")
            and row["stage"] in ("inputs", "build", "parity", "hold", "released")
            and type(row["acknowledged"]) is bool and row["cleanup"] in ("confirmed", "unknown")
            and all(row[key] is False for key in ("quality_eligible", "confidential", "live_admission")))
    require(type(row["receipts"]) is dict and set(row["receipts"]) in (set(), set(FILES)))
    for sha in row["receipts"].values():
        tau.sha(sha)
    if row["ready_sha256"] is not None:
        tau.sha(row["ready_sha256"])
        require(set(row["receipts"]) == set(FILES))
    if row["result"] == "pass":
        require(row["stage"] == "released" and row["acknowledged"] and row["cleanup"] == "confirmed"
                and row["ready_sha256"] is not None)
    if probing:
        import tau_cpu_probe
        if row["probe"] is not None:
            tau_cpu_probe.validate(row["probe"])
        require(row["result"] != "pass" or row["probe"] is not None and row["probe"]["result"] == "pass")
    return row


def accept(raw, producer, image_sha, deadline):
    """Host checks provenance/schema only; live kernel checks run in the worker."""
    require(type(raw) is bytes and len(raw) <= 32768)
    packet = llama.worker.decode(raw)
    identity.keys(packet, ("schema", "producer", "image_sha256", "expires_unix", "receipts"))
    require(packet["schema"] == "nib.tau-cpu-pair-ready/v1" and packet["producer"] == producer
            and packet["image_sha256"] == image_sha and probes.encoded(packet) == raw
            and type(packet["expires_unix"]) is int
            and 0 < packet["expires_unix"] - time.time() <= cpu.TOTAL_SECONDS)
    identity.keys(packet["receipts"], FILES)
    originals, runtimes = {}, []
    for role, model in zip(ROLES, cpu.MODEL_IDS):
        docs = []
        for kind in ("runtime", "tokenizer"):
            name = role + "-" + kind + ".json"
            require(type(packet["receipts"][name]) is str)
            original = packet["receipts"][name].encode("ascii")
            require(0 < len(original) <= 16384)
            value = llama.worker.decode(original)
            require(tau.encoded(value) == original and value["producer"] == producer)
            originals[name] = original
            docs.append(value)
        runtime, tokenizer = docs
        pinned = identity.cpu.identity(model, deadline)
        framing = {"served_model": model, "context_tokens": 4096, "parallel_tool_calls": False,
                   **{name: pinned[name] for name in ("framing_sha256", "tokenizer_sha256")}}
        manifest = {"framing": {role: framing}, "settings": {"roles": {role: {
            "model": model, "revision": pinned["revision"], "generation": {"max_tokens": 256}}}}}
        tau.integer(runtime["endpoint"]["port"], 1, 65535)
        identity.cpu_runtime_receipt(runtime, role, runtime["endpoint"], manifest, packet["expires_unix"], deadline)
        identity.tokenizer_receipt(tokenizer, tau.digest(originals[role + "-runtime.json"]), runtime,
                                   framing, deadline, cpu_kind=True)
        require(runtime["serving"]["image_sha256"] == image_sha)
        runtimes.append(runtime)
    for key in ("pid", "listener_inode"):
        require(len({row["process"][key] for row in runtimes}) == 2)
    require(len({row["endpoint"]["port"] for row in runtimes}) == 2
            and len({row["model_files_sha256"] for row in runtimes}) == 2)
    return originals, {"schema": "nib.tau-cpu-pair-held/v1", "ready_sha256": tau.digest(raw),
                       "expires_unix": packet["expires_unix"],
                       "receipts": {name: tau.digest(originals[name]) for name in FILES}}


def relay(argv, env, snapshot, producer, image_sha, output_fd, *, read_fd=0, write_fd=1, probe=None):
    """A private controller pipe, never an HTTP endpoint or arbitrary command."""
    require(all(stat.S_ISFIFO(os.fstat(fd).st_mode) for fd in (read_fd, write_fd)))
    deadline = time.monotonic() + cpu.TOTAL_SECONDS
    process = subprocess.Popen(argv, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, bufsize=0, start_new_session=True)
    output, errors, ack = bytearray(), bytearray(), bytearray()
    held, acknowledged, total = None, False, 0
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "output")
            selector.register(process.stderr, selectors.EVENT_READ, "error")
            selector.register(read_fd, selectors.EVENT_READ, "ack")
            while any(key.data != "ack" for key in selector.get_map().values()) or process.poll() is None:
                require(time.monotonic() < deadline)
                for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                    block = os.read(key.fd, 4096 if key.data != "ack" else 513 - len(ack))
                    if key.data == "ack":
                        require(block and held is not None and not acknowledged)
                        ack.extend(block)
                        require(len(ack) <= 512)
                        if b"\n" in ack:
                            release(bytes(ack), held["ready_sha256"])
                            send(process.stdin.fileno(), bytes(ack), deadline)
                            process.stdin.close()
                            acknowledged = True
                            selector.unregister(read_fd)
                        continue
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(block)
                    require(total <= 65536)
                    if key.data == "error":
                        errors.extend(block)
                        require(not errors)
                    else:
                        output.extend(block)
                        if held is None and b"\n" in output and output.startswith(READY):
                            line, rest = bytes(output).split(b"\n", 1)
                            require(not rest)
                            originals, held = accept(line[len(READY):], producer, image_sha, deadline)
                            for name in FILES:
                                write_exact(output_fd, name, originals[name])
                            os.fsync(output_fd)
                            send(write_fd, READY + probes.encoded(held) + b"\n", min(deadline, time.monotonic() + 5))
                            output.clear()
                            if probe is not None:
                                runner = cpu.module(cpu.ROOT / "scripts/tau_trial.py", "cpu_probe_runner")
                                selector.unregister(process.stdout)
                                selector.unregister(process.stderr)
                                probe["record"] = runner.run(probe["payload"], probe["sha"], probe["output"], probe["socket"],
                                    parent_pipe=True, support_probe={"process": process,
                                        "ready_sha256": held["ready_sha256"], "controller_fd": read_fd})
                                selector.register(process.stdout, selectors.EVENT_READ, "output")
                                selector.register(process.stderr, selectors.EVENT_READ, "error")
                                notice = {"schema": "nib.tau-cpu-probe-held/v1", "ready_sha256": held["ready_sha256"],
                                    "plan_sha256": probe["sha"], "scorer_sha256": tau.digest(tau.encoded(probe["record"])),
                                    "result": probe["record"]["result"], "cleanup": probe["record"]["cleanup"],
                                    "diagnostic": probe["record"]["diagnostic"]}
                                raw_notice = probes.encoded(notice)
                                require(len(raw_notice) <= 16384)
                                send(write_fd, b"NIB-TAU-CPU-PROBE " + raw_notice + b"\n", min(deadline, time.monotonic() + 5))
                            break  # The fixed probe may have consumed readiness from other selected FDs.
            code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
            result = validate(llama.worker.decode(bytes(output)))
            require(code in (0, 1) and (code == 0) == (result["result"] == "pass"))
            if held is not None:
                require(result["ready_sha256"] == held["ready_sha256"] and result["receipts"] == held["receipts"])
            else:
                require(result["ready_sha256"] is None and not result["receipts"])
            require(not result["acknowledged"] or acknowledged)
            require((result["schema"] == "nib.tau-cpu-session/v2") is (probe is not None))
            if probe is not None and probe["record"] is not None:
                require(result["probe"] == probe["record"]["diagnostic"])
            return result
    finally:
        if not process.stdin.closed:
            process.stdin.close()
        snapshot.cleanup_process_group(process)
        for stream in (process.stdout, process.stderr):
            stream.close()
