# SPDX-License-Identifier: Apache-2.0
"""Private pipe prerequisite for process-owned tau endpoints, not live admission."""

import math
import os
import subprocess
import threading
import time

import tau_cpu_parity as cpu
import tau_live_identity as identity
import tau_transport as transport

tau, worker, require = transport.tau, transport.worker, transport.require
SCHEMA = "nib.tau-owner-channel/v1"
ERRORS = frozenset(worker.STATUSES) - {"evaluated"}


def relay(process, admitted, deadline, *, read_fd=0, write_fd=1):
    """Relay one private owner channel, retaining trailing supervisor JSON.

    Caller authenticates original readiness and caps the deadline at its expiry.
    This never interprets completion content, reserves usage or renews a budget.
    """
    require(isinstance(process, subprocess.Popen) and process.poll() is None
            and type(deadline) in (int, float) and math.isfinite(deadline)
            and 0 < deadline - time.monotonic() <= transport.MAX_WAIT_SECONDS)
    expected_sha, sequence = tau.digest(tau.encoded(binding(admitted))), 1
    caller = transport.Channel(os.dup(read_fd), os.dup(write_fd))
    child = transport.Channel(os.dup(process.stdout.fileno()), os.dup(process.stdin.fileno()))
    try:
        with worker.alarm(deadline):
            while True:
                require(process.poll() is None and sequence <= 65)
                packet = caller.receive(deadline)
                require(type(packet) is dict and packet.get("schema") == SCHEMA
                        and packet.get("binding_sha256") == expected_sha
                        and type(packet.get("sequence")) is int and packet["sequence"] == sequence
                        and packet.get("kind") in ("count", "complete", "finish"))
                until = packet.get("deadline")
                require(type(until) in (int, float) and math.isfinite(until)
                        and time.monotonic() < until <= deadline)
                child.send(packet, until)
                reply = child.receive(until)
                require(type(reply) is dict and reply.get("schema") == SCHEMA
                        and reply.get("binding_sha256") == expected_sha
                        and type(reply.get("sequence")) is int and reply["sequence"] == sequence
                        and reply.get("kind") in (packet["kind"] + "-result", packet["kind"] + "-error"))
                if packet["kind"] == "finish":
                    require(set(reply) == {"schema", "binding_sha256", "kind", "sequence", "status", "completed_requests"}
                            and reply["kind"] == "finish-result" and reply["status"] == packet.get("status")
                            and type(reply["status"]) is str and reply["status"] in worker.STATUSES
                            and type(reply["completed_requests"]) is int
                            and reply["completed_requests"] == (sequence - 1) // 2
                            and (reply["status"] != "evaluated" or reply["completed_requests"] > 0))
                    caller.send(reply, until)
                    return {key: reply[key] for key in ("status", "completed_requests")}
                if reply["kind"].endswith("-error"):
                    require(set(reply) == {"schema", "binding_sha256", "kind", "sequence", "request_sha256", "status"}
                            and reply["request_sha256"] == tau.digest(tau.encoded(packet.get("body")))
                            and type(reply.get("status")) is str and reply["status"] in ERRORS)
                    caller.send(reply, until)
                    return None
                caller.send(reply, until)
                sequence += 1
    finally:
        caller.close()
        child.close()


def binding(value):
    """Caller must independently admit these readiness bytes before attachment."""
    value = transport.snapshot(value, 16384)
    require(type(value) is dict and set(value) == {"ready_sha256", "roles"})
    tau.sha(value["ready_sha256"])
    roles = value["roles"]
    require(type(roles) is dict and set(roles) in ({"candidate"}, {"simulator", "evaluator"}))
    for role, row in roles.items():
        require(type(row) is dict and set(row) == {"model", "context_tokens"}
                and type(row["model"]) is str and 0 < len(row["model"]) <= 128)
        tau.integer(row["context_tokens"], 1, 1048576)
    require(len({row["model"] for row in roles.values()}) == len(roles))
    return value


def response(value, count):
    value = transport.snapshot(value, worker.MAX_RESPONSE)
    require(type(value) is dict and type(value.get("usage")) is dict
            and type(value["usage"].get("prompt_tokens")) is int
            and value["usage"]["prompt_tokens"] == count)
    return value


class State:
    def __init__(self, channel, admitted, deadline, *, clock=time.monotonic):
        self.channel, self.binding, self.clock = channel, binding(admitted), clock
        require(type(deadline) in (int, float) and math.isfinite(deadline)
                and 0 < deadline - clock() <= transport.MAX_WAIT_SECONDS)
        self.deadline, self.sequence, self.failed, self.pending = deadline, 0, None, None
        self.finished = None
        self.binding_sha = tau.digest(tau.encoded(self.binding))
        self.busy = threading.Lock()

    def close(self):
        self.pending = None
        self.channel.close()

    def fail(self, reason):
        self.failed = self.failed or reason
        self.close()
        raise worker.Abort(self.failed) from None

    def check(self, deadline):
        if self.failed:
            self.fail(self.failed)
        require(self.finished is None)
        require(type(deadline) in (int, float) and math.isfinite(deadline)
                and deadline <= self.deadline)
        if deadline <= self.clock():
            raise worker.Abort("timeout")

    def envelope(self, kind, sequence):
        return {"schema": SCHEMA, "binding_sha256": self.binding_sha, "kind": kind, "sequence": sequence}

    def check_envelope(self, packet, kind, sequence, fields):
        require(type(packet) is dict
                and set(packet) == {"schema", "binding_sha256", "kind", "sequence", *fields}
                and all(packet[key] == value for key, value in self.envelope(kind, sequence).items())
                and type(packet["sequence"]) is int)

    def body(self, role, value):
        require(type(role) is str and role in self.binding["roles"])
        result = transport.snapshot(value, worker.MAX_REQUEST)
        require(type(result) is dict and result.get("model") == self.binding["roles"][role]["model"]
                and result.get("stream") is False and type(result.get("n")) is int and result["n"] == 1)
        tau.integer(result.get("max_completion_tokens"), 1, 16384)
        identity.local.require_nonthinking_auto(result)
        return result, tau.digest(tau.encoded(result))


class Owner(State):
    """Runs beside existing guarded clients; never accepts an endpoint from a frame.

    No Budget lives here. The coordinator retains the existing Bridge/Budget and
    reserves generation before complete. One count can authorize one identical
    completion, on the same role, under the same or shorter deadline.
    """
    def __init__(self, channel, admitted, deadline, clients, *, clock=time.monotonic):
        super().__init__(channel, admitted, deadline, clock=clock)
        require(type(clients) is dict and set(clients) == set(self.binding["roles"]))
        for role, client in clients.items():
            expected = (identity.GuardedClient,) if role == "candidate" else (
                identity.GuardedLlamaClient, cpu.MeasuredClient)
            require(type(client) in expected and type(client.endpoint) is identity.Endpoint)
        self.clients = dict(clients)

    def serve_once(self):
        if not self.busy.acquire(blocking=False):
            self.fail("protocol_error")
        terminal = None
        try:
            self.check(self.deadline)
            packet = self.channel.receive(self.deadline)
            if type(packet) is dict and packet.get("kind") == "finish":
                self.check_envelope(packet, "finish", self.sequence + 1, {"status", "deadline"})
                self.check(packet["deadline"])
                require(self.pending is None and type(packet["status"]) is str
                        and packet["status"] in worker.STATUSES
                        and (packet["status"] != "evaluated" or self.sequence > 0))
                result = {"status": packet["status"], "completed_requests": self.sequence // 2}
                self.channel.send({**self.envelope("finish-result", self.sequence + 1), **result}, packet["deadline"])
                self.sequence += 1
                self.finished = result
                return True
            operation = "count" if self.pending is None else "complete"
            fields = {"role", "body", "deadline", "context_tokens" if operation == "count" else "prompt_tokens"}
            self.check_envelope(packet, operation, self.sequence + 1, fields)
            require(self.sequence < 256)
            deadline, role = packet["deadline"], packet["role"]
            self.check(deadline)
            body, digest = self.body(role, packet["body"])
            terminal = (operation, digest, deadline)
            context = self.binding["roles"][role]["context_tokens"]
            client = self.clients[role]
            if operation == "count":
                require(type(packet["context_tokens"]) is int and packet["context_tokens"] == context)
                try:
                    count = client.count(body, context, deadline)
                finally:
                    if type(client) is cpu.MeasuredClient:
                        client.ids = None
                tau.integer(count, 1, context)
                if count + body["max_completion_tokens"] > context:
                    raise worker.Abort("budget_abort")
                self.pending = (role, digest, count, deadline)
                reply = {"prompt_tokens": count, "request_sha256": digest}
            else:
                expected_role, expected_digest, count, count_deadline = self.pending
                require(role == expected_role and digest == expected_digest and deadline <= count_deadline
                        and type(packet["prompt_tokens"]) is int and packet["prompt_tokens"] == count)
                # Consume before dispatch: exceptions must never permit a retry.
                self.pending = None
                reply = {"response": response(client.complete(body, count, deadline), count), "request_sha256": digest}
            self.check(deadline)
            self.channel.send({**self.envelope(operation + "-result", self.sequence + 1), **reply}, deadline)
            self.sequence += 1
        except BaseException as exc:
            reason = transport.status(exc)
            # A known owner failure must not become EOF/protocol_error. Only
            # publish a fixed status for an admitted request, without extending
            # its deadline or retaining its body; broken pipes remain terminal.
            if terminal is not None and not self.failed:
                operation, digest, deadline = terminal
                try:
                    self.check(deadline)
                    self.channel.send({**self.envelope(operation + "-error", self.sequence + 1),
                                       "request_sha256": digest, "status": reason}, deadline)
                except BaseException:
                    pass
            self.fail(reason)
        finally:
            self.busy.release()


class Coordinator(State):
    """One shared channel per owner, substituted for LocalVllmSession endpoints."""
    def exchange(self, operation, role, body, deadline, value):
        if not self.busy.acquire(blocking=False):
            self.fail("protocol_error")
        try:
            self.check(deadline)
            body, digest = self.body(role, body)
            require(self.sequence < 256)
            context = self.binding["roles"][role]["context_tokens"]
            if operation == "count":
                require(self.pending is None and type(value) is int and value == context)
                field, reply_fields = "context_tokens", {"prompt_tokens", "request_sha256"}
            else:
                require(operation == "complete" and self.pending is not None)
                expected_role, expected_digest, count, count_deadline = self.pending
                require(role == expected_role and digest == expected_digest and deadline <= count_deadline
                        and type(value) is int and value == count)
                self.pending = None
                field, reply_fields = "prompt_tokens", {"response", "request_sha256"}
            self.channel.send({**self.envelope(operation, self.sequence + 1), "role": role,
                               "body": body, "deadline": deadline, field: value}, deadline)
            reply = self.channel.receive(deadline)
            if type(reply) is dict and reply.get("kind") == operation + "-error":
                self.check_envelope(reply, operation + "-error", self.sequence + 1,
                                    {"request_sha256", "status"})
                require(reply["request_sha256"] == digest and type(reply["status"]) is str
                        and reply["status"] in ERRORS)
                self.check(deadline)
                raise worker.Abort(reply["status"])
            self.check_envelope(reply, operation + "-result", self.sequence + 1, reply_fields)
            require(reply["request_sha256"] == digest)
            self.check(deadline)
            self.sequence += 1
            if operation == "count":
                count = reply["prompt_tokens"]
                tau.integer(count, 1, context)
                if count + body["max_completion_tokens"] > context:
                    raise worker.Abort("budget_abort")
                self.pending = role, digest, count, deadline
                return count
            return response(reply["response"], count)
        except BaseException as exc:
            self.fail(transport.status(exc))
        finally:
            self.busy.release()

    def client(self, role):
        require(type(role) is str and role in self.binding["roles"])
        return Client(self, role)

    def finish(self, status, deadline):
        if not self.busy.acquire(blocking=False):
            self.fail("protocol_error")
        try:
            self.check(deadline)
            require(self.pending is None and type(status) is str and status in worker.STATUSES
                    and (status != "evaluated" or self.sequence > 0))
            self.channel.send({**self.envelope("finish", self.sequence + 1),
                               "status": status, "deadline": deadline}, deadline)
            reply = self.channel.receive(deadline)
            self.check_envelope(reply, "finish-result", self.sequence + 1, {"status", "completed_requests"})
            require(reply["status"] == status and type(reply["completed_requests"]) is int
                    and reply["completed_requests"] == self.sequence // 2)
            self.check(deadline)
            self.sequence += 1
            self.finished = {"status": status, "completed_requests": reply["completed_requests"]}
            return dict(self.finished)
        except BaseException as exc:
            self.fail(transport.status(exc))
        finally:
            self.busy.release()


class Client:
    def __init__(self, owner, role):
        self.owner, self.role = owner, role

    def count(self, body, context_tokens, deadline):
        return self.owner.exchange("count", self.role, body, deadline, context_tokens)

    def complete(self, body, prompt_tokens, deadline):
        return self.owner.exchange("complete", self.role, body, deadline, prompt_tokens)


if __name__ == "__main__":
    raise SystemExit("Library prerequisite only; no launch, live admission or quality claim.")
