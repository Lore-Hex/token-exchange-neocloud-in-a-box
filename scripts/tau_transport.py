# SPDX-License-Identifier: Apache-2.0
"""Bounded pipe/accounting prerequisite. No endpoints, upstream imports or live dispatch."""

from decimal import Decimal
import fcntl
import math
import os
import re
import select
import stat
import threading
import time

import tau_preflight as tau
import tau_trial_worker as worker

SCHEMA = "nib.tau-pipe/v1"
MANIFEST = "nib.tau-transport-smoke/v1"
MAX_FRAME = worker.MAX_REQUEST + 4096
MAX_WAIT_SECONDS = 1800  # Existing tau preflight maximum trial duration.
ALIASES = {role: alias for alias, role in worker.MODEL_ROLES.items()}
KWARGS = frozenset(("tool_choice", "temperature", "max_tokens", "max_completion_tokens",
                    "num_retries", "stream", "n", "seed", "reasoning_effort", "parallel_tool_calls"))


def require(value):
    if not value:
        raise worker.Abort("protocol_error")


def snapshot(value, limit):
    return worker.decode(worker.bounded_json(value, limit))


def status(exc):
    if isinstance(exc, worker.Abort):
        return exc.status
    return "cancelled" if isinstance(exc, KeyboardInterrupt) else "protocol_error"


class Channel:
    """Own two explicit pipe FDs; failed framing makes the channel unusable."""
    def __init__(self, read_fd, write_fd, *, clock=time.monotonic, cancelled=lambda: False):
        require(type(read_fd) is int and type(write_fd) is int
                and read_fd >= 3 and write_fd >= 3 and read_fd != write_fd)
        for fd, mode in ((read_fd, os.O_RDONLY), (write_fd, os.O_WRONLY)):
            require(stat.S_ISFIFO(os.fstat(fd).st_mode)
                    and fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == mode)
        self.read_fd, self.write_fd = read_fd, write_fd
        self.clock, self.cancelled, self.failed = clock, cancelled, None
        try:
            for fd in (read_fd, write_fd):
                os.set_inheritable(fd, False)
                os.set_blocking(fd, False)
        except BaseException:
            self.close()
            raise worker.Abort("protocol_error") from None

    def close(self):
        for name in ("read_fd", "write_fd"):
            fd = getattr(self, name)
            setattr(self, name, -1)
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def fail(self, reason):
        self.failed = self.failed or reason
        self.close()
        raise worker.Abort(self.failed) from None

    def check(self, deadline):
        if self.failed:
            self.fail(self.failed)
        require(type(deadline) in (int, float) and math.isfinite(deadline))
        if self.cancelled():
            self.fail("cancelled")
        left = deadline - self.clock()
        require(left <= MAX_WAIT_SECONDS)
        if left <= 0:
            self.fail("timeout")
        return min(left, 0.05)

    def transfer(self, value, deadline, *, reading):
        data, offset = bytearray() if reading else value, 0
        size = value if reading else len(value)
        fd = self.read_fd if reading else self.write_fd
        while offset < size:
            wait = self.check(deadline)
            ready = select.select([fd] if reading else [], [] if reading else [fd], [], wait)
            if not ready[0 if reading else 1]:
                continue
            try:
                if reading:
                    chunk = os.read(fd, min(size - offset, 65536))
                    require(chunk)
                    data.extend(chunk)
                    offset += len(chunk)
                else:
                    count = os.write(fd, memoryview(data)[offset:offset + 65536])
                    require(count > 0)
                    offset += count
            except (BlockingIOError, InterruptedError):
                continue
        self.check(deadline)
        return bytes(data) if reading else None

    def receive(self, deadline):
        try:
            length = int.from_bytes(self.transfer(4, deadline, reading=True), "big")
            require(0 < length <= MAX_FRAME)
            value = worker.decode(self.transfer(length, deadline, reading=True))
            worker.bounded_json(value, MAX_FRAME)
            self.check(deadline)
            return value
        except BaseException as exc:
            self.fail(status(exc))

    def send(self, value, deadline):
        try:
            raw = worker.bounded_json(value, MAX_FRAME)
            self.transfer(len(raw).to_bytes(4, "big") + raw, deadline, reading=False)
        except BaseException as exc:
            self.fail(status(exc))


def smoke_manifest(value, expected_sha):
    """This separate, explicit manifest cannot represent the frozen quality gate."""
    value = snapshot(value, worker.MAX_REQUEST)
    tau.sha(expected_sha)
    require(tau.digest(tau.encoded(value)) == expected_sha and type(value) is dict
            and set(value) == {"schema", "purpose", "quality_eligible", "benchmark_manifest_sha256",
                               "upstream_revision", "settings", "framing"}
            and value["schema"] == MANIFEST and value["purpose"] == "nonthinking-transport-prerequisite"
            and value["quality_eligible"] is False and value["upstream_revision"] == tau.REVISION)
    tau.sha(value["benchmark_manifest_sha256"])
    config = tau.settings(value["settings"])
    require(type(value["framing"]) is dict and set(value["framing"]) == set(tau.ROLES))
    for role, row in value["framing"].items():
        require(type(row) is dict and set(row) == {"reasoning_effort", "parallel_tool_calls", "served_model",
                                                  "framing_sha256", "tokenizer_sha256", "context_tokens"}
                and row["reasoning_effort"] == "none" and type(row["parallel_tool_calls"]) is bool
                and type(row["served_model"]) is str
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,127}", row["served_model"])
                and ".." not in row["served_model"])
        tau.sha(row["framing_sha256"])
        tau.sha(row["tokenizer_sha256"])
        tau.integer(row["context_tokens"], 1, 1048576)
        if role in ("candidate", "reference"):
            require(row["tokenizer_sha256"] == config["serving"][role]["tokenizer_sha256"])
    return value


def request_body(manifest, selected_role, category, arguments):
    """Map only reviewed text kwargs; never silently discard a requested setting."""
    require(category in ALIASES and selected_role in ("candidate", "reference"))
    require(type(arguments) is dict and {"messages", "tools", "kwargs"} == set(arguments))
    role = selected_role if category == "agent" else category
    config, framing = manifest["settings"]["roles"][role], manifest["framing"][role]
    messages, tools, kwargs = arguments["messages"], arguments["tools"], arguments["kwargs"]
    require(type(messages) is list and 0 < len(messages) <= 256 and type(kwargs) is dict
            and set(kwargs) <= KWARGS)
    for message in messages:
        require(type(message) is dict and message.get("role") in ("system", "user", "assistant", "tool"))
        allowed = {"role", "content"}
        if message["role"] == "assistant":
            allowed.add("tool_calls")
        if message["role"] == "tool":
            allowed.add("tool_call_id")
        require(set(message) <= allowed and "content" in message
                and (type(message["content"]) is str
                     or message["role"] == "assistant" and message["content"] is None))
        if "tool_call_id" in message:
            require(type(message["tool_call_id"]) is str and 0 < len(message["tool_call_id"]) <= 128)
        if message.get("tool_calls") is not None:
            require(type(message["tool_calls"]) is list and 0 < len(message["tool_calls"]) <= 16)
            ids = set()
            for call in message["tool_calls"]:
                require(type(call) is dict and {"id", "type", "function"} <= set(call)
                        <= {"id", "type", "function", "name"} and call["type"] == "function"
                        and type(call["id"]) is str and 0 < len(call["id"]) <= 128 and call["id"] not in ids
                        and type(call["function"]) is dict and set(call["function"]) == {"name", "arguments"})
                ids.add(call["id"])
                function = call["function"]
                require(type(function["name"]) is str and 0 < len(function["name"]) <= 128
                        and type(function["arguments"]) is str and len(function["arguments"]) <= 32768
                        and type(worker.decode(function["arguments"])) is dict
                        and ("name" not in call or call["name"] == function["name"]))
    require(tools is None or type(tools) is list and 0 < len(tools) <= 256)
    names = []
    for tool in tools or []:
        require(type(tool) is dict and set(tool) == {"type", "function"} and tool["type"] == "function"
                and type(tool["function"]) is dict)
        function = tool["function"]
        require({"name", "parameters"} <= set(function) <= {"name", "description", "parameters", "strict"}
                and type(function["name"]) is str and 0 < len(function["name"]) <= 128
                and type(function["parameters"]) is dict)
        names.append(function["name"])
    require(len(names) == len(set(names)))
    choice = kwargs.get("tool_choice")
    if tools:
        # Upstream generate explicitly supplies auto when tools exist.
        if type(choice) is dict:
            require(set(choice) == {"type", "function"} and choice["type"] == "function"
                    and type(choice["function"]) is dict and set(choice["function"]) == {"name"}
                    and choice["function"]["name"] in names)
        else:
            require(choice in ("auto", "none", "required"))
    else:
        require(choice in (None, "none"))
    generation = config["generation"]
    for key, expected in (("stream", False), ("n", 1), ("num_retries", 0),
                          ("parallel_tool_calls", framing["parallel_tool_calls"]),
                          ("reasoning_effort", framing["reasoning_effort"])):
        if key in kwargs:
            require(type(kwargs[key]) is type(expected) and kwargs[key] == expected)
    if "temperature" in kwargs:
        temperature = kwargs["temperature"]
        require(type(temperature) in (int, float) and math.isfinite(temperature)
                and Decimal(str(temperature)) == Decimal(generation["temperature"]))
    require(not {"max_tokens", "max_completion_tokens"} <= set(kwargs))
    for key in ("max_tokens", "max_completion_tokens"):
        if key in kwargs:
            require(type(kwargs[key]) is int and kwargs[key] == generation["max_tokens"])
    body = {"model": framing["served_model"], "messages": messages, "stream": False, "n": 1,
            "temperature": float(generation["temperature"]), "max_completion_tokens": generation["max_tokens"],
            "reasoning_effort": framing["reasoning_effort"], "parallel_tool_calls": framing["parallel_tool_calls"]}
    if tools is not None:
        body["tools"] = tools
    if choice is not None:
        body["tool_choice"] = choice
    if "seed" in kwargs:
        tau.integer(kwargs["seed"], 0, 2**32 - 1)
        body["seed"] = kwargs["seed"]
    return role, snapshot(body, worker.MAX_REQUEST)


def check_bounds(receipt, manifest, manifest_sha, role, body):
    """Consume parent-only verified-count inputs; this function does not tokenize."""
    require(type(receipt) is dict and set(receipt) == {"manifest_sha256", "request_sha256", "role",
            "framing_sha256", "tokenizer_sha256", "context_tokens", "prompt_tokens"})
    framing = manifest["framing"][role]
    require(receipt["manifest_sha256"] == manifest_sha and receipt["role"] == role
            and receipt["request_sha256"] == tau.digest(tau.encoded(body)))
    for name in ("framing_sha256", "tokenizer_sha256", "context_tokens"):
        require(type(receipt[name]) is type(framing[name]) and receipt[name] == framing[name])
    tau.integer(receipt["prompt_tokens"], 1, manifest["settings"]["limits"]["input_tokens_per_request"])
    require(receipt["prompt_tokens"] + body["max_completion_tokens"] <= framing["context_tokens"])


class WorkerProxy:
    """Completion-shaped proxy only: no Budget, model destination or credentials."""
    def __init__(self, channel, deadline):
        self.channel, self.deadline, self.sequence, self.failed = channel, deadline, 0, None
        self.busy = threading.Lock()

    def complete(self, *, model, messages, tools=None, **kwargs):
        if self.failed:
            raise worker.Abort(self.failed)
        if not self.busy.acquire(blocking=False):
            self.failed = "protocol_error"
            self.channel.close()
            raise worker.Abort(self.failed)
        try:
            require(type(model) is str and model in worker.MODEL_ROLES)
            self.sequence += 1
            self.channel.send({"schema": SCHEMA, "kind": "completion", "sequence": self.sequence,
                               "role": worker.MODEL_ROLES[model],
                               "arguments": {"messages": messages, "tools": tools, "kwargs": kwargs}}, self.deadline)
            reply = self.channel.receive(self.deadline)
            require(type(reply) is dict and set(reply) == {"schema", "kind", "sequence", "response"}
                    and reply["schema"] == SCHEMA and reply["kind"] == "response"
                    and type(reply["sequence"]) is int and reply["sequence"] == self.sequence)
            return snapshot(reply["response"], worker.MAX_RESPONSE)
        except BaseException as exc:
            self.failed = status(exc)
            self.channel.close()
            raise worker.Abort(self.failed) from None
        finally:
            self.busy.release()


class ParentSession:
    """One existing Bridge/Budget; scripted replies are the ONLY dispatch source.

    `records` are trusted-parent inputs, never accepted from worker frames. Their
    counts are synthetic in tests; a real count verifier/endpoint adapter is absent.
    """
    def __init__(self, channel, manifest, manifest_sha, role, records, *, assertions=(), clock=time.monotonic):
        self.manifest = smoke_manifest(manifest, manifest_sha)
        require(role in ("candidate", "reference") and type(records) is list and len(records) <= 128)
        self.records = snapshot(records, 4 << 20)
        for record in self.records:
            require(type(record) is dict and set(record) == {"bounds", "response"})
        self.channel, self.manifest_sha, self.role, self.sequence = channel, manifest_sha, role, 0
        self.bridge = worker.Bridge(tau, self.manifest["settings"], role, self.dispatch, clock=clock)
        require(type(assertions) in (list, tuple) and all(type(item) is str for item in assertions))
        self.bridge.assertions = snapshot(list(assertions), worker.MAX_RESPONSE)
        self.current, self.response_deadline = None, None
        self.busy = threading.Lock()

    def dispatch(self, category, request, bounds):
        require(self.current is not None and self.bridge.budget.pending is not None)
        self.response_deadline = self.bridge.budget.pending[4]
        body, record = self.current
        require(category in ALIASES and request == {"messages": body["messages"], "tools": body.get("tools")}
                and bounds["max_tokens"] == body["max_completion_tokens"] and bounds["num_retries"] == 0)
        response = snapshot(record["response"], worker.MAX_RESPONSE)
        require(type(response) is dict and type(response.get("usage")) is dict
                and response["usage"].get("prompt_tokens") == record["bounds"]["prompt_tokens"])
        return response

    def prepare_record(self, role, body, deadline):
        require(self.sequence < len(self.records))
        return self.records[self.sequence]

    def serve_once(self):
        if self.bridge.failed:
            raise worker.Abort(self.bridge.failed)
        if not self.busy.acquire(blocking=False):
            self.channel.close()
            self.bridge.stop("protocol_error")
        deadline = self.bridge.budget.trial_deadline
        try:
            # Includes framing, validation, dispatch, response validation and delivery.
            with worker.alarm(deadline, self.bridge.clock):
                packet = self.channel.receive(deadline)
                require(type(packet) is dict and set(packet) == {"schema", "kind", "sequence", "role", "arguments"}
                        and packet["schema"] == SCHEMA and packet["kind"] == "completion"
                        and type(packet["sequence"]) is int and packet["sequence"] == self.sequence + 1
                        and packet["role"] in ALIASES)
                role, body = request_body(self.manifest, self.role, packet["role"], packet["arguments"])
                record = self.prepare_record(role, body, deadline)
                check_bounds(record["bounds"], self.manifest, self.manifest_sha, role, body)
                self.current = body, record
                response = self.bridge.complete(model=ALIASES[packet["role"]], messages=body["messages"],
                                                tools=body.get("tools"), num_retries=0, stream=False)
                self.channel.send({"schema": SCHEMA, "kind": "response", "sequence": packet["sequence"],
                                   "response": response}, self.response_deadline)
                self.sequence += 1
        except BaseException as exc:
            self.channel.close()
            try:
                self.bridge.stop(status(exc))
            except worker.Abort as stopped:
                raise worker.Abort(stopped.status) from None
        finally:
            self.current, self.response_deadline = None, None
            self.busy.release()

    def close(self):
        self.channel.close()
        self.records.clear()
        self.bridge.assertions.clear()


if __name__ == "__main__":
    raise SystemExit("Library prerequisite only; no live or upstream execution operation.")
