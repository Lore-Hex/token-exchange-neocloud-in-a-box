# SPDX-License-Identifier: Apache-2.0
"""Selected 8K GPU process owner; callers must verify the lab source bundle."""

import os
import math
import subprocess
import selectors
import stat
import time

import engine_local_correctness as correctness
import engine_runtime_observation as observer
import engine_tokenizer_parity as parity
import tau_owner_channel as owners
import tau_selected_policy as policy

tau, identity, worker, require = owners.tau, owners.identity, owners.worker, owners.require
READY = b"NIB-TAU-GPU-READY "
SCHEMA = "nib.tau-gpu-owner-input/v1"


def enabled(contract):
    require(type(contract) is dict and contract.get("model_profile") == policy.models.select("uncensored")
            and contract.get("operation") == "local-correctness"
            and contract.get("execution_profile") == "tau8k-eager"
            and contract.get("gpu_family") == "B300" and contract.get("gpu_security") == "nonconfidential"
            and type(contract.get("data_parallel_size")) is int and contract["data_parallel_size"] == 2
            and contract.get("tokenizer_parity") is True and "tokenizer_materialization" in contract
            and contract.get("runtime_observation") is True
            and "provider_conformance" not in contract and "stream_cohort" not in contract)
    return policy.models.select("uncensored")["served_model_name"]


def selected(contract):
    require(contract.get("tau_owner") == {"schema": SCHEMA})
    return enabled(contract)


def validate_result(row):
    require(type(row) is dict and set(row) == {"ready_sha256", "status", "completed_requests"}
            and type(row["status"]) is str and row["status"] in worker.STATUSES
            and type(row["completed_requests"]) is int
            and 0 <= row["completed_requests"] <= policy.LIMITS["requests"]
            and (row["status"] != "evaluated" or row["completed_requests"] > 0))
    tau.sha(row["ready_sha256"])
    return row


def validate_join(original, finish, experiment):
    """Join observed private transport to the separately validated lab result."""
    expected = None if finish is None else {"ready_sha256": tau.digest(original), **finish}
    require(experiment["tau_owner"] == expected
            and (expected is not None or experiment["functional_pass"] is False))
    if original is not None:
        ready = worker.decode(original)
        require(experiment["tokenizer_parity"] == ready["parity"]
                and experiment["runtime_observation"]["samples"]
                and experiment["runtime_observation"]["samples"][0] == ready["sample"])
    if expected is not None:
        validate_result(expected)
    if experiment["functional_pass"]:
        require(expected["status"] == "evaluated" and experiment["engine_stopped"] is True)


def capture(argv, env, deadline, snapshot, contract, contract_sha, *, read_fd=0, write_fd=1):
    """Host-side private attach after source/image/container validation.

    Preserve final lab JSON even after a valid owner error; never print bodies.
    The outer recorder validates that JSON and the container's cleanup.
    """
    selected(contract)
    require(all(stat.S_ISFIFO(os.fstat(fd).st_mode) for fd in (read_fd, write_fd))
            and type(deadline) in (int, float) and math.isfinite(deadline)
            and 0 < deadline - time.monotonic() <= 4800)
    process = subprocess.Popen(argv, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, bufsize=0, start_new_session=True)
    output, total, original, result = bytearray(), 0, None, None
    blocking = {fd: os.get_blocking(fd) for fd in (read_fd, write_fd)}
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "output")
            selector.register(process.stderr, selectors.EVENT_READ, "error")
            while selector.get_map() or process.poll() is None:
                require(time.monotonic() < deadline)
                for key, _ in selector.select(min(.1, max(0, deadline - time.monotonic()))):
                    block = os.read(key.fd, 4096)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(block)
                    require(total <= 131072)
                    if key.data == "error":
                        continue
                    output.extend(block)
                    require(len(output) <= 65536)
                    if original is None and output.startswith(READY) and b"\n" in output:
                        line, rest = bytes(output).split(b"\n", 1)
                        require(not rest)
                        original = line[len(READY):]
                        admitted = binding(original, contract, contract_sha)
                        until = min(deadline, time.monotonic() + worker.decode(original)["expires_unix"] - time.time())
                        channel = owners.transport.Channel(os.dup(read_fd), os.dup(write_fd))
                        try:
                            channel.transfer(READY + original + b"\n", min(until, time.monotonic() + 5), reading=False)
                        finally:
                            channel.close()
                        selector.unregister(process.stdout)
                        selector.unregister(process.stderr)
                        result = owners.relay(process, admitted, until, read_fd=read_fd, write_fd=write_fd)
                        process.stdin.close()
                        output.clear()
                        selector.register(process.stdout, selectors.EVENT_READ, "output")
                        selector.register(process.stderr, selectors.EVENT_READ, "error")
                        break
            code = process.wait(timeout=max(.01, deadline - time.monotonic()))
            return code, bytes(output), original, result
    finally:
        if not process.stdin.closed:
            process.stdin.close()
        snapshot.cleanup_process_group(process)
        process.stdout.close()
        process.stderr.close()
        for fd, original_mode in blocking.items():
            os.set_blocking(fd, original_mode)


def binding(raw, contract, contract_sha):
    """Host-side structural joins, not proof of runtime or receipt provenance."""
    model = enabled(contract)
    tau.sha(contract_sha)
    require(type(raw) is bytes and 0 < len(raw) <= 16384)
    value = worker.decode(raw)
    identity.keys(value, ("schema", "contract_sha256", "model", "expires_unix", "runtime",
                          "process", "sample", "parity"))
    require(value["schema"] == "nib.tau-gpu-owner-ready/v1" and observer.encoded(value) == raw
            and value["contract_sha256"] == contract_sha and value["model"] == model
            and type(value["expires_unix"]) is int
            and 0 < value["expires_unix"] - time.time() <= policy.LIMITS["run_seconds"])
    parity.validate(value["parity"], correctness, max_model_len=policy.CONTEXTS["candidate"])
    require(value["parity"]["all_cases_pass"] is True)
    observer.validate_sample(value["sample"], contract)
    require(value["sample"]["event"] == "before-requests")
    runtime, process, sample = value["runtime"], value["process"], value["sample"]
    identity.keys(runtime, ("pid", "parent", "start", "namespaces", "boot", "argv"))
    identity.keys(runtime["namespaces"], ("net", "pid", "cgroup"))
    identity.keys(process, identity.PROCESS)
    for number in (runtime["pid"], runtime["parent"], runtime["start"], process["listener_inode"],
                   *runtime["namespaces"].values()):
        tau.integer(number, 1, 2**63 - 1)
    for digest in (runtime["boot"], runtime["argv"]):
        tau.sha(digest)
    require(process == {"pid": runtime["pid"], "start_ticks": runtime["start"],
        "boot_id_sha256": runtime["boot"], "netns_inode": runtime["namespaces"]["net"],
        "exe_sha256": contract["interpreter"]["sha256"], "argv_sha256": runtime["argv"],
        "listener_inode": process["listener_inode"]}
        and all(type(process[key]) is int for key in ("pid", "start_ticks", "netns_inode", "listener_inode"))
        and sample["process_sha256"] == observer.digest(observer.encoded(runtime))
        and sample["argv_sha256"] == runtime["argv"]
        and sample["listener_sha256"] == observer.digest(observer.encoded({"process": runtime, "inode": process["listener_inode"]})))
    return owners.binding({"ready_sha256": tau.digest(raw),
        "roles": {"candidate": {"model": model, "context_tokens": policy.CONTEXTS["candidate"]}}})


class Lifetime:
    """Only the already owned local process; never caller-supplied network data."""
    def __init__(self, process, deadline):
        require(isinstance(process, subprocess.Popen) and process.poll() is None)
        self.process, self.deadline = process, deadline

    def fresh(self):
        if time.monotonic() >= self.deadline:
            raise worker.Abort("timeout")
        require(self.process.poll() is None)


def serve(process, contract, contract_sha, sample, measured_parity, deadline, *, read_fd=0, write_fd=1):
    """Called by the source-verified lab after its actual owned-engine 8K parity.

    The host must independently admit this original readiness against its pinned
    lab receipt before sending a frame. No Git command runs inside the image.
    """
    model = enabled(contract)
    tau.sha(contract_sha)
    require(type(deadline) in (int, float) and math.isfinite(deadline) and deadline > time.monotonic())
    until = min(deadline, time.monotonic() + policy.LIMITS["run_seconds"])
    life = Lifetime(process, until)
    life.fresh()
    runtime = observer.process(process.pid, until)
    require(runtime["parent"] == os.getpid())
    inode = observer.listener(process.pid, until)
    facts = identity.process_facts(process.pid)
    expected = {"pid": process.pid, **facts, "exe_sha256": contract["interpreter"]["sha256"], "listener_inode": inode}
    endpoint = identity.Endpoint(8000, {"process": expected})
    endpoint.check(until)
    packet = {"schema": "nib.tau-gpu-owner-ready/v1", "contract_sha256": contract_sha,
              "model": model, "expires_unix": int(time.time() + until - time.monotonic()),
              "runtime": runtime, "process": expected, "sample": sample, "parity": measured_parity}
    raw = observer.encoded(packet)
    admitted = binding(raw, contract, contract_sha)
    # Round down once to the published expiry. Neither attachment nor a request
    # may renew it; the host reserves trial/teardown time within this window.
    until = min(until, time.monotonic() + packet["expires_unix"] - time.time())
    life.deadline = until
    blocking = {fd: os.get_blocking(fd) for fd in (read_fd, write_fd)}
    channel = owners.transport.Channel(os.dup(read_fd), os.dup(write_fd))
    owner = None
    try:
        owner = owners.Owner(channel, admitted, until, {"candidate": identity.GuardedClient(endpoint, life)})
        # Existing bounded byte transfer also supports this first line, without
        # mixing the following binary owner frames with the final lab JSON.
        channel.transfer(READY + raw + b"\n", min(until, time.monotonic() + 5), reading=False)
        with worker.alarm(until):
            while True:
                life.fresh()
                endpoint.check(until)
                require(owner.sequence <= 2 * policy.LIMITS["requests"])
                if owner.serve_once():
                    life.fresh()
                    endpoint.check(until)
                    require(0 <= owner.finished["completed_requests"] <= policy.LIMITS["requests"])
                    return {"ready_sha256": tau.digest(raw), **owner.finished}
    finally:
        channel.close()
        if owner is not None:
            owner.close()
        for fd, original in blocking.items():
            os.set_blocking(fd, original)


if __name__ == "__main__":
    raise SystemExit("Library only; no standalone launch or runtime admission.")
