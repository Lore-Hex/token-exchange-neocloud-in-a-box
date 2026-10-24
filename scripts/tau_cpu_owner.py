# SPDX-License-Identifier: Apache-2.0
"""Process-owned CPU roles after original measured readiness, not admission."""

import os
import math
import subprocess
import time

import tau_cpu_session as session
import tau_owner_channel as owners
import tau_selected_policy as policy

cpu, tau, worker, require = session.cpu, session.tau, owners.worker, owners.require


def validate(row):
    require(type(row) is dict and set(row) == {"status", "completed_requests"}
            and type(row["status"]) is str and row["status"] in worker.STATUSES
            and type(row["completed_requests"]) is int
            and 0 <= row["completed_requests"] <= policy.LIMITS["requests"]
            and (row["status"] != "evaluated" or row["completed_requests"] > 0))
    return row


def binding(raw):
    packet = worker.decode(raw)
    require(type(packet) is dict and packet.get("schema") == "nib.tau-cpu-pair-ready/v1")
    roles = {}
    for role in session.ROLES:
        runtime = worker.decode(packet["receipts"][role + "-runtime.json"].encode("ascii"))
        require(runtime["model"] == policy.configuration()["support"][role]["model"])
        roles[role] = {"model": runtime["model"], "context_tokens": policy.CONTEXTS[role]}
    return owners.binding({"ready_sha256": tau.digest(raw), "roles": roles})


def serve(raw, owned, processes, producer, image_sha, deadline, *, read_fd=0, write_fd=1):
    """Only called inside the parity worker which owns both runtime processes."""
    require(type(owned) in (list, tuple) and type(processes) in (list, tuple)
            and len(owned) == len(processes) == 2)
    # The source-verified worker has no Git repository. These originals were
    # just produced here; the host independently performs session.accept's
    # committed-source provenance checks before forwarding any request.
    require(type(raw) is bytes and 0 < len(raw) <= 32768)
    packet = worker.decode(raw)
    session.identity.keys(packet, ("schema", "producer", "image_sha256", "expires_unix", "receipts"))
    session.identity.keys(packet["receipts"], session.FILES)
    require(packet["schema"] == "nib.tau-cpu-pair-ready/v1" and session.probes.encoded(packet) == raw
            and packet["producer"] == producer and packet["image_sha256"] == image_sha
            and type(packet["expires_unix"]) is int
            and 0 < packet["expires_unix"] - time.time() <= cpu.TOTAL_SECONDS)
    for role, endpoint, process in zip(session.ROLES, owned, processes):
        original = packet["receipts"][role + "-runtime.json"].encode("ascii")
        runtime = worker.decode(original)
        tokenizer = worker.decode(packet["receipts"][role + "-tokenizer.json"].encode("ascii"))
        require(type(endpoint) is owners.identity.Endpoint and isinstance(process, subprocess.Popen)
                and process.poll() is None and endpoint.expected["pid"] == process.pid
                and runtime["producer"] == tokenizer["producer"] == producer
                and runtime["serving"]["image_sha256"] == image_sha
                and tokenizer["runtime_receipt_sha256"] == tau.digest(original)
                and runtime["process"] == endpoint.expected
                and runtime["endpoint"] == {"host": "127.0.0.1", "port": endpoint.port})
        endpoint.check(deadline)
    admitted = binding(raw)
    # Readiness expiry never renews here, even when the host takes time to attach.
    remaining = worker.decode(raw)["expires_unix"] - time.time()
    until = min(deadline, time.monotonic() + remaining)
    require(until > time.monotonic())
    clients = {role: cpu.MeasuredClient(endpoint.port, admitted["roles"][role]["model"], endpoint)
               for role, endpoint in zip(session.ROLES, owned)}
    blocking = {fd: os.get_blocking(fd) for fd in (read_fd, write_fd)}
    channel = owners.transport.Channel(os.dup(read_fd), os.dup(write_fd))
    owner = None
    try:
        owner = owners.Owner(channel, admitted, until, clients)
        session.send(write_fd, session.READY + raw + b"\n", min(until, time.monotonic() + 5))
        with worker.alarm(until):
            while True:
                require(all(process.poll() is None for process in processes))
                for endpoint in owned:
                    endpoint.check(until)
                require(owner.sequence <= 2 * policy.LIMITS["requests"])
                if owner.serve_once():
                    require(all(process.poll() is None for process in processes))
                    for endpoint in owned:
                        endpoint.check(until)
                    return validate(owner.finished)
    finally:
        channel.close()
        if owner is not None:
            owner.close()
        for fd, original in blocking.items():
            os.set_blocking(fd, original)


def relay(process, raw, deadline, *, read_fd=0, write_fd=1):
    """Private one-request/one-reply relay, bounded frames and no content logs.

    Host readiness validation is performed before this call. The two protocol
    endpoints still enforce identity, sequencing and count-before-generation.
    The relay does not generate, reserve usage or interpret completion content.
    """
    require(isinstance(process, subprocess.Popen) and process.poll() is None)
    admitted = binding(raw)
    deadline = min(deadline, time.monotonic() + worker.decode(raw)["expires_unix"] - time.time())
    require(deadline > time.monotonic())
    expected_sha, sequence = tau.digest(tau.encoded(admitted)), 1
    caller = owners.transport.Channel(os.dup(read_fd), os.dup(write_fd))
    child = owners.transport.Channel(os.dup(process.stdout.fileno()), os.dup(process.stdin.fileno()))
    try:
        with worker.alarm(deadline):
            while True:
                require(process.poll() is None and sequence <= 2 * policy.LIMITS["requests"] + 1)
                packet = caller.receive(deadline)
                require(type(packet) is dict and packet.get("schema") == owners.SCHEMA
                        and packet.get("binding_sha256") == expected_sha
                        and type(packet.get("sequence")) is int and packet["sequence"] == sequence
                        and packet.get("kind") in ("count", "complete", "finish"))
                until = packet.get("deadline")
                require(type(until) in (int, float) and math.isfinite(until)
                        and time.monotonic() < until <= deadline)
                child.send(packet, until)
                reply = child.receive(until)
                require(type(reply) is dict and reply.get("schema") == owners.SCHEMA
                        and reply.get("binding_sha256") == expected_sha
                        and type(reply.get("sequence")) is int and reply["sequence"] == sequence
                        and reply.get("kind") in (packet["kind"] + "-result", packet["kind"] + "-error"))
                if packet["kind"] == "finish":
                    require(set(reply) == {"schema", "binding_sha256", "kind", "sequence", "status", "completed_requests"}
                            and reply["kind"] == "finish-result" and reply["status"] == packet.get("status")
                            and reply["completed_requests"] == (sequence - 1) // 2)
                    result = validate({key: reply[key] for key in ("status", "completed_requests")})
                    caller.send(reply, until)
                    return result
                if reply["kind"].endswith("-error"):
                    require(set(reply) == {"schema", "binding_sha256", "kind", "sequence", "request_sha256", "status"}
                            and reply["request_sha256"] == tau.digest(tau.encoded(packet.get("body")))
                            and type(reply.get("status")) is str and reply["status"] in owners.ERRORS)
                    caller.send(reply, until)
                    # The worker now emits its failure/cleanup JSON. Do not
                    # skip the supervisor's bounded collection of that report.
                    return None
                caller.send(reply, until)
                sequence += 1
    finally:
        caller.close()
        child.close()
