# SPDX-License-Identifier: Apache-2.0
"""Owned Linux CPU parity worker; no admission, quality or confidentiality gate."""

import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import socket
import subprocess
import sys
import time
import types

import engine_tokenizer_parity as probes
import tau_live_identity as identity
import tau_local_llama as llama

ROOT = Path(__file__).resolve().parent.parent
MODEL_IDS = ("qwen/qwen3-0.6b", "qwen/qwen3-1.7b")
BUILD_SECONDS = 900
MODEL_SECONDS = 300
TOTAL_SECONDS = 1800
STAGES = ("inputs", "build", "local-tokenizer", "readiness", "comparison", "complete")


def require(value):
    if not value:
        raise ValueError("CPU parity rejected")


def module(path, name):
    result = types.ModuleType(name)
    result.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec", dont_inherit=True), result.__dict__)
    return result


def tools():
    # This module only runs from the parent's verified private Git snapshot.
    pilot = module(ROOT / "scripts/pilot/run.py", "cpu_pilot")
    return pilot, pilot.source_module("snapshot")


def settings():
    value = json.loads((ROOT / "scripts/tau_cpu_support_models.json").read_bytes())
    require(value["engine"]["revision"] == llama.REVISION and set(value["models"]) == set(MODEL_IDS)
            and value["runtime_verified"] is False and value["quality_eligible"] is False)
    return value


def confinement():
    require(sys.platform == "linux" and os.getuid() == os.geteuid() == 65532
            and os.getgid() == os.getegid() == 65532
            and {name for _, name in socket.if_nameindex()} == {"lo"})
    status = dict(line.split(":", 1) for line in identity.proc_read("/proc/self/status", 16384).decode().splitlines())
    require(all(int(status[name], 16) == 0 for name in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"))
            and status["NoNewPrivs"].strip() == "1" and status["Seccomp"].strip() == "2")
    require(identity.proc_read("/sys/fs/cgroup/memory.max", 64).strip() == b"8589934592"
            and identity.proc_read("/sys/fs/cgroup/memory.swap.max", 64).strip() == b"0"
            and identity.proc_read("/sys/fs/cgroup/pids.max", 64).strip() == b"128")
    quota, period = identity.proc_read("/sys/fs/cgroup/cpu.max", 64).split()
    require(int(quota) == 4 * int(period) and int(period) > 0)
    require(not any(name.startswith(("nvidia", "dri", "kfd")) for name in os.listdir("/dev")))


def body(model, probe):
    value = {"model": model, **probe, "stream": False, "n": 1, "temperature": 0,
             "max_completion_tokens": 1, "reasoning_effort": "none", "parallel_tool_calls": False}
    if probe.get("tools"):
        value["tool_choice"] = "auto"
    return value


class MeasuredClient(llama.LocalLlamaCPP):
    def __init__(self, port, model, endpoint):
        super().__init__(port, model, 4096)
        self.endpoint, self.ids, self.attempted = endpoint, None, 0

    def connect(self, sock, deadline):
        self.endpoint.check(deadline)
        super().connect(sock, deadline)
        self.endpoint.check(deadline, sock)

    def post(self, path, payload, deadline):
        self.attempted += 1
        row = super().post(path, payload, deadline)
        if path == "/tokenize":
            self.ids = row.get("tokens") if type(row) is dict else None
        return row

    def measure(self, payload, deadline):
        self.ids = None
        try:
            count = super().count(payload, 4096, deadline)
            measured = probes.tokens(self.ids)
            require(measured["count"] == count)
            return measured
        finally:
            self.ids = None


def native_tokens(binary, weights, env, snapshot, deadline):
    packet = probes.encoded(probes.probes())
    require(len(packet) <= 65536)
    process = subprocess.Popen([str(binary), str(weights)], env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        os.set_blocking(process.stdin.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdin, selectors.EVENT_WRITE)
            offset = 0
            while offset < len(packet):
                require(time.monotonic() < deadline)
                if selector.select(min(0.1, deadline - time.monotonic())):
                    offset += os.write(process.stdin.fileno(), packet[offset:])
        process.stdin.close()
        raw, errors = snapshot.bounded_output(process, ["fixed-cpu-tokenizer"],
            max(0.001, deadline - time.monotonic()), None, 65536, 65536)
        require(process.returncode == 0 and not errors and time.monotonic() < deadline)
        row = llama.worker.decode(raw)
        require(type(row) is dict and set(row) == {"schema", "tokens"}
                and row["schema"] == "nib.cpu-parity-helper/v1"
                and type(row["tokens"]) is list and len(row["tokens"]) == len(probes.IDS))
        return [probes.tokens(value) for value in row["tokens"]]
    finally:
        # Native helper has no subprocess API. Compiler/server descendants stay
        # in the outer owned group, which the independent parent always removes.
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def build(pilot, archive, work, env):
    source = pilot.unpack(archive, work / "vendor")
    overlay = source / "nib-parity"
    overlay.mkdir()
    for name in ("tau_cpu_parity.cpp", "tau_cpu_parity.cmake"):
        (overlay / name).write_bytes((ROOT / "scripts" / name).read_bytes())
    cmake = source / "CMakeLists.txt"
    cmake.write_bytes(cmake.read_bytes() + b"\ninclude(nib-parity/tau_cpu_parity.cmake)\n")
    flags = ("GGML_METAL=OFF", "GGML_CUDA=OFF", "GGML_OPENMP=OFF", "GGML_CCACHE=OFF",
             "GGML_BLAS=OFF", "GGML_BACKEND_DL=OFF",
             "LLAMA_BUILD_COMMON=ON", "LLAMA_BUILD_TOOLS=ON", "LLAMA_BUILD_SERVER=ON",
             "LLAMA_BUILD_TESTS=OFF", "LLAMA_BUILD_EXAMPLES=OFF", "LLAMA_BUILD_APP=OFF",
             "LLAMA_BUILD_UI=OFF", "LLAMA_USE_PREBUILT_UI=OFF", "LLAMA_OPENSSL=OFF",
             "LLAMA_SUBPROCESS=OFF", "LLAMA_LLGUIDANCE=OFF", "BUILD_SHARED_LIBS=OFF")
    destination = work / "build"
    pilot.command(["cmake", "-S", source, "-B", destination, "-DCMAKE_BUILD_TYPE=Release",
                   *("-D" + flag for flag in flags)], env, timeout=120)
    pilot.command(["cmake", "--build", destination, "--target", "llama-server",
                   "nib-tau-tokenize", "--parallel", "4"], env, timeout=BUILD_SECONDS)
    return destination / "bin/llama-server", destination / "bin/nib-tau-tokenize"


def endpoint(process, port, binary_sha, argv, deadline):
    address = "0100007F:" + format(port, "04X")
    while time.monotonic() < deadline:
        require(process.poll() is None)
        listeners = [row for row in identity.tcp_rows() if row[2] == "0A" and row[0].endswith(address[8:])]
        if listeners:
            require(len(listeners) == 1 and listeners[0][:3] == (address, "00000000:0000", "0A"))
            facts = identity.process_facts(process.pid)
            require(facts["argv_sha256"] == hashlib.sha256(
                b"\0".join(os.fsencode(arg) for arg in argv) + b"\0").hexdigest())
            observed = {"pid": process.pid, **facts, "exe_sha256": binary_sha,
                        "listener_inode": listeners[0][3]}
            result = identity.Endpoint(port, {"process": observed})
            result.check(deadline)
            return result
        time.sleep(0.05)
    raise TimeoutError


def compare(client, model, local, deadline, rows):
    require(len(local) == len(probes.IDS) and rows == [])
    for name, probe, measured in zip(probes.IDS, probes.probes(), local):
        row = {"id": name, "local": measured, "server": None, "usage": None, "result": "fail"}
        rows.append(row)
        payload = body(model, probe)
        row["server"] = client.measure(payload, min(deadline, time.monotonic() + 30))
        if row["server"] != measured:
            return
        response = client.complete(payload, measured["count"], min(deadline, time.monotonic() + 30))
        usage = response["usage"]
        require(set(usage) == {"prompt_tokens", "completion_tokens", "total_tokens"}
                and all(type(value) is int for value in usage.values())
                and usage["prompt_tokens"] == measured["count"] and 0 <= usage["completion_tokens"] <= 1
                and usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"])
        row.update(usage=usage, result="pass")
    require(time.monotonic() < deadline)


def validate(report):
    """Closed metadata schema: no child strings, content or exception fields."""
    pins = settings()
    identity.keys(report, ("schema", "result", "stage", "engine_revision", "models", "binaries",
                           "quality_eligible", "confidential", "live_admission"))
    require(report["schema"] == "nib.tau-cpu-parity/v1" and report["result"] in ("pass", "fail")
            and report["stage"] in STAGES and report["engine_revision"] == llama.REVISION
            and all(report[key] is False for key in ("quality_eligible", "confidential", "live_admission")))
    require(type(report["binaries"]) is dict and set(report["binaries"]) in (set(), {"server", "helper"}))
    for digest in report["binaries"].values():
        require(type(digest) is str and re.fullmatch("[0-9a-f]{64}", digest))
    require(type(report["models"]) is list and len(report["models"]) <= 2)
    for model, item in zip(MODEL_IDS, report["models"]):
        identity.keys(item, ("model", "weight_sha256", "result", "ready_ms", "cases", "process", "owner_socket_checked"))
        require(item["model"] == model and item["weight_sha256"] == pins["models"][model]["sha256"]
                and item["result"] in ("pass", "fail") and type(item["owner_socket_checked"]) is bool
                and (item["ready_ms"] is None or type(item["ready_ms"]) is int and 0 <= item["ready_ms"] <= 90000)
                and type(item["cases"]) is list and len(item["cases"]) <= 4)
        if item["process"] is not None:
            identity.keys(item["process"], identity.PROCESS)
            for name, value in item["process"].items():
                if name.endswith("sha256"):
                    require(type(value) is str and re.fullmatch("[0-9a-f]{64}", value))
                else:
                    require(type(value) is int and 0 < value < 2**63)
            require(item["process"]["exe_sha256"] == report["binaries"].get("server"))
        for name, case in zip(probes.IDS, item["cases"]):
            identity.keys(case, ("id", "local", "server", "usage", "result"))
            require(case["id"] == name and case["result"] in ("pass", "fail"))
            probes.measurement(case["local"])
            if case["server"] is not None:
                probes.measurement(case["server"])
            if case["usage"] is not None:
                usage = case["usage"]
                identity.keys(usage, ("prompt_tokens", "completion_tokens", "total_tokens"))
                require(all(type(value) is int for value in usage.values())
                        and usage["prompt_tokens"] == case["local"]["count"]
                        and 0 <= usage["completion_tokens"] <= 1
                        and usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"])
            if case["result"] == "pass":
                require(case["local"] == case["server"] and case["usage"] is not None)
        if item["result"] == "pass":
            require(item["owner_socket_checked"] and item["ready_ms"] is not None and item["process"] is not None
                    and len(item["cases"]) == 4 and all(case["result"] == "pass" for case in item["cases"]))
    if report["result"] == "pass":
        require(report["stage"] == "complete" and len(report["binaries"]) == 2
                and len(report["models"]) == 2 and all(item["result"] == "pass" for item in report["models"]))
    return report


def run(archive, models, work):
    pins = settings()
    report = {"schema": "nib.tau-cpu-parity/v1", "result": "fail", "stage": "inputs",
              "engine_revision": llama.REVISION, "models": [], "binaries": {},
              "quality_eligible": False, "confidential": False, "live_admission": False}
    try:
        confinement()
        pilot, snapshot = tools()
        env = pilot.controlled_env(work / "control")
        env.update(GOPROXY="off", GOSUMDB="off", GIT_ALLOW_PROTOCOL="",
                   OMP_NUM_THREADS="4", OPENBLAS_NUM_THREADS="4",
                   PATH="/opt/cmake-wheel/cmake/data/bin:/usr/local/bin:/usr/bin:/bin")
        # Inputs were copied/hash-checked by the parent into the read-only image.
        # Rehash the exact files used here, not their host-side originals.
        require(archive.stat().st_size <= 64 << 20 and pilot.file_digest(archive) == pins["engine"]["archive_sha256"])
        weights = []
        for index, model in enumerate(MODEL_IDS):
            item = pins["models"][model]
            destination = models[index]
            require(destination.stat().st_size == item["bytes"] and pilot.file_digest(destination) == item["sha256"])
            weights.append(destination)
        report["stage"] = "build"
        server, helper = build(pilot, archive, work, env)
        report["binaries"] = {"server": pilot.file_digest(server), "helper": pilot.file_digest(helper)}
        for model, weight in zip(MODEL_IDS, weights):
            item = {"model": model, "weight_sha256": pins["models"][model]["sha256"],
                    "result": "fail", "ready_ms": None, "cases": [], "process": None, "owner_socket_checked": False}
            report["models"].append(item)
            deadline = time.monotonic() + MODEL_SECONDS
            report["stage"] = "local-tokenizer"
            local = native_tokens(helper, weight, env, snapshot, min(deadline, time.monotonic() + 60))
            port = pilot.free_port()
            argv = pilot.engine_command(server, weight, model, port, "unused")
            require(argv[-2:] == ["--api-key-file", "unused"])
            # Only in a closed loopback-only Linux namespace. The ordinary pilot
            # stays authenticated; this owned, synthetic-only probe is ephemeral.
            argv = argv[:-2]
            process = subprocess.Popen(argv, env=env, cwd=work, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                report["stage"] = "readiness"
                started = time.monotonic()
                owned = endpoint(process, port, report["binaries"]["server"], argv, min(deadline, started + 90))
                with llama.worker.alarm(min(deadline, started + 90)):
                    pilot.ready(process, port, "/health")
                owned.check(deadline)
                item["process"] = owned.expected
                item["ready_ms"] = int((time.monotonic() - started) * 1000)
                item["owner_socket_checked"] = True
                report["stage"] = "comparison"
                compare(MeasuredClient(port, model, owned), model, local, deadline, item["cases"])
                require(pilot.file_digest(weight) == pins["models"][model]["sha256"])
                if len(item["cases"]) == 4 and all(row["result"] == "pass" for row in item["cases"]):
                    item["result"] = "pass"
            finally:
                require(pilot.stop(process))
            require(time.monotonic() < deadline)
            if item["result"] != "pass":
                break
        if len(report["models"]) == 2 and all(item["result"] == "pass" for item in report["models"]):
            report.update(result="pass", stage="complete")
    except BaseException:
        # No dependency text, paths, prompts, completions or traceback escapes.
        report["result"] = "fail"
    return report
