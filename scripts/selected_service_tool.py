# SPDX-License-Identifier: Apache-2.0
"""One fixed synthetic tool round trip over the already owned TLS front door."""

from contextlib import ExitStack
import http.client
import json
import re
import threading
import time

import selected_service_conformance as wire

inference, process, service = wire.inference, wire.process, wire.service
measurement, transport = wire.measurement, wire.transport
require, encoded = inference.require, inference.encoded
SCHEMA = "nib.selected-service-tool/v1"
CASES = ("call", "continuation")
SECONDS, LIMIT, TEXT_LIMIT = 250, 1 << 20, 32768
NAME, ANSWER = "lookup_synthetic_value", "NIB_TOOL_OK"
PROMPT = ("Call lookup_synthetic_value with key sample exactly once. "
          "After its result, reply only with the returned value.")
TOOL = {"type": "function", "function": {"name": NAME,
    "description": "Return the fixed value for a synthetic key.",
    "parameters": {"type": "object", "properties": {"key": {"type": "string", "enum": ["sample"]}},
                   "required": ["key"], "additionalProperties": False}}}
RESULT = encoded({"value": ANSWER}).decode()


def request_bytes(assistant=None):
    messages = [{"role": "user", "content": PROMPT}]
    if assistant is not None:
        messages += [assistant, {"role": "tool", "tool_call_id": assistant["tool_calls"][0]["id"],
                                 "content": RESULT}]
    raw = encoded({"model": service.MODEL, "messages": messages, "tools": [TOOL],
        "tool_choice": "auto" if assistant is None else "none", "parallel_tool_calls": False,
        "reasoning_effort": "none", "temperature": 0, "max_tokens": service.OUTPUT,
        "stream": True, "stream_options": {"include_usage": True}})
    require(len(raw) <= 65536, "protocol")
    return raw


def contract():
    return {"schema": "nib.selected-service-tool-contract/v1", "model": service.MODEL,
        "request_sha256": process.digest(request_bytes()), "result_sha256": process.digest(RESULT.encode()),
        "answer_sha256": process.digest(ANSWER.encode()), "cases": list(CASES), "retries": 0,
        "seconds": SECONDS, "request_seconds": inference.REQUEST_SECONDS, "response_bytes": LIMIT,
        "max_output_tokens": service.OUTPUT, "max_prompt_tokens": 4096,
        "max_calls": 1, "argument_bytes": TEXT_LIMIT, "reasoning_bytes": TEXT_LIMIT,
        "authenticated_requests": 2, "arbitrary_execution": False}


class ToolStream(measurement.StreamMeasurement):
    """Reuse frozen SSE framing; native tool deltas and usage stay local."""

    def __init__(self):
        super().__init__(service.MODEL, timeout_seconds=inference.REQUEST_SECONDS,
                         max_prompt_tokens=4096, max_output_tokens=service.OUTPUT)
        self.call = None
        self.text = self.reasoning = ""
        self.reasoning_field = None

    def append(self, old, fragment, limit):
        measurement.require(type(fragment) is str and len(old.encode()) + len(fragment.encode()) <= limit)
        return old + fragment

    def message(self, payload):
        check = measurement.require
        check(not self.done)
        if payload == b"[DONE]":
            check(self.finish_reason is not None and self.usage is not None and self.events > 0)
            self.done = True
            return
        check(self.usage is None)
        row = json.loads(payload, object_pairs_hook=measurement.unique,
                         parse_constant=lambda _: check(False))
        check(type(row) is dict and "error" not in row and row.get("model") == self.model
              and type(row.get("choices")) is list and len(row["choices"]) <= 1)
        if row["choices"]:
            check(self.finish_reason is None)
            choice = row["choices"][0]
            check(type(choice) is dict and type(choice.get("index")) is int and choice["index"] == 0
                  and type(choice.get("delta")) is dict)
            delta = choice["delta"]
            check(set(delta) <= {"role", "content", "reasoning", "reasoning_content", "tool_calls"}
                  and delta.get("role") in (None, "assistant"))
            output = False
            for name in ("content", "reasoning", "reasoning_content"):
                fragment = delta.get(name)
                check(fragment is None or type(fragment) is str)
                if fragment:
                    if name == "content":
                        self.text = self.append(self.text, fragment, TEXT_LIMIT)
                        if self.content is None:
                            self.content = self.elapsed
                    else:
                        check(self.reasoning_field in (None, name))
                        self.reasoning_field = name
                        self.reasoning = self.append(self.reasoning, fragment, TEXT_LIMIT)
                    output = True
            calls = delta.get("tool_calls")
            if calls is not None:
                check(type(calls) is list and len(calls) <= 1)
                for call in calls:
                    check(type(call) is dict and set(call) <= {"index", "id", "type", "function"}
                          and type(call.get("index")) is int and call["index"] == 0)
                    function = call.get("function")
                    check(type(function) is dict and set(function) <= {"name", "arguments"})
                    if self.call is None:
                        check(call.get("type") == "function" and type(call.get("id")) is str
                              and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", call["id"]))
                        self.call = {"id": call["id"], "type": "function",
                                     "function": {"name": "", "arguments": ""}}
                    else:
                        check(call.get("id") is None and call.get("type") in (None, "function"))
                    for name, limit in (("name", 64), ("arguments", TEXT_LIMIT)):
                        fragment = function.get(name)
                        if fragment is not None:
                            self.call["function"][name] = self.append(
                                self.call["function"][name], fragment, limit)
                            output |= bool(fragment)
            if output:
                self.events += 1
                self.first = self.elapsed if self.first is None else self.first
                self.last = self.elapsed
            finish = choice.get("finish_reason")
            check(finish in (None, "stop", "tool_calls", "length"))
            self.finish_reason = finish
        else:
            check(self.finish_reason is not None and row.get("usage") is not None)
        if row.get("usage") is not None:
            self.record_usage(row["usage"])

    def record_usage(self, usage):
        # Mirror the legacy arithmetic without changing its source-bound bytes.
        check, integer = measurement.require, measurement.integer
        check(self.finish_reason is not None and self.usage is None and type(usage) is dict)
        prompt, output, total = (usage.get(key) for key in
                                 ("prompt_tokens", "completion_tokens", "total_tokens"))
        check(integer(prompt, self.max_prompt) and integer(output, self.max_output) and output > 0
              and integer(total, self.max_prompt + self.max_output) and total == prompt + output)
        details = usage.get("prompt_tokens_details")
        check(details is None or type(details) is dict)
        cached = None if details is None else details.get("cached_tokens")
        check(cached is None or integer(cached, prompt))
        details = usage.get("completion_tokens_details")
        check(details is None or type(details) is dict)
        reasoning = None if details is None else details.get("reasoning_tokens")
        check(reasoning is None or integer(reasoning, output))
        self.usage = (prompt, output, cached)

    def qualify(self, case):
        require(self.closed and not self.failure and self.done and self.usage is not None
                and self.usage[0] > 0 and self.usage[1] < service.OUTPUT, "completion")
        if case == "call":
            require(self.finish_reason == "tool_calls" and self.call is not None
                    and self.call["function"]["name"] == NAME, "completion")
            arguments = service.correctness.strict_json(self.call["function"]["arguments"].encode())
            require(arguments == {"key": "sample"}, "completion")
            assistant = {"role": "assistant", "content": self.text or None, "tool_calls": [self.call]}
            if self.reasoning:
                assistant["reasoning"] = self.reasoning
            return assistant
        require(case == "continuation" and self.finish_reason == "stop" and self.call is None
                and self.text.strip() == ANSWER, "completion")
        return None


def exchange(supervisor, identities, deadline, key, case, body, report):
    attempt = {"attempts": {"stream": False}, "usage_unknown": False}
    meter, started = ToolStream(), time.monotonic()
    stack = ExitStack()
    report["resources_closed"][case] = False
    try:
        try:
            state = stack.enter_context(wire.response(supervisor, identities, deadline, "stream", key, body, attempt))
            observed, connection, request_deadline = state
            length, count = transport.response_length(observed), 0
            while True:
                connection.settimeout(max(.001, request_deadline - time.monotonic()))
                block = observed.read1(measurement.MAX_EVENT)
                if not block:
                    break
                count += len(block)
                require(count <= LIMIT, "protocol")
                meter.feed(block, time.monotonic() - started)
                require(meter.failure is None, "deadline" if meter.failure == "timeout" else "protocol")
                inference.checkpoint(supervisor, identities, request_deadline)
            require(length is None or count == length, "protocol")
            require(observed.tail is not None and observed.tail.read1(1) == b"", "protocol")
            sample = meter.close(time.monotonic() - started)
            require(sample.status == "completed", "protocol")
            inference.checkpoint(supervisor, identities, request_deadline)
        finally:
            stack.close()
            report["resources_closed"][case] = True
        return meter.qualify(case)
    finally:
        report["attempts"][case] = attempt["attempts"]["stream"]
        report["finish"][case] = meter.finish_reason
        if meter.usage is not None:
            report["usage"][case] = {"prompt_tokens": meter.usage[0], "completion_tokens": meter.usage[1]}
        report["usage_unknown"][case] = report["attempts"][case] and meter.usage is None


def run_once(supervisor, prior):
    report = {"schema": SCHEMA, "result": "fail", "failure": "internal",
        "contract_sha256": process.digest(encoded(contract())), "prepared_sha256": None,
        "inference_sha256": process.digest(encoded(prior)), "attempts": dict.fromkeys(CASES, False),
        "checks": dict.fromkeys(CASES, False), "usage": dict.fromkeys(CASES),
        "usage_unknown": dict.fromkeys(CASES, False), "finish": dict.fromkeys(CASES),
        "resources_closed": dict.fromkeys(CASES, True), **dict.fromkeys(inference.CLAIMS, False)}
    try:
        require(threading.current_thread() is threading.main_thread()
            and type(supervisor) is service.Supervisor and supervisor.used is True
            and type(supervisor.owner) is process.ProcessOwner
            and type(supervisor.owner.prepared) is process.Prepared, "contract")
        prepared = supervisor.owner.prepared
        report["prepared_sha256"] = prepared.receipt_sha256
        require(not getattr(supervisor, "_selected_tool_used", False), "already-used")
        supervisor._selected_tool_used = True
        inference.validate_report(encoded(prior), prepared.receipt_sha256)
        require(prior["result"] == "pass" and getattr(supervisor, "_selected_inference_used", False)
            and set(supervisor.children) == set(service.ROLES)
            and all(supervisor.children[role] is supervisor.owner.processes[role] for role in service.ROLES),
            "not-ready")
        identities = {role: dict(supervisor.owner.identities[role]) for role in service.ROLES}
        deadline = min(time.monotonic() + SECONDS, supervisor.cutoff() - supervisor.contract.cleanup_reserve)
        with inference.cancellation(supervisor.shutdown), service.identity.worker.alarm(deadline):
            inference.checkpoint(supervisor, identities, deadline)
            require(prepared.files["scripts/selected_service_tool.py"] ==
                    process.digest(process.bootstrap.read_source(__file__)), "identity")
            key = service.identity.private_read(supervisor.inputs.root / "api-key", deadline)
            service.pilot.validate_api_key(key)
            assistant = None
            for case in CASES:
                assistant = exchange(supervisor, identities, deadline, key.removesuffix(b"\n"),
                                     case, request_bytes(assistant), report)
                report["checks"][case] = True
                inference.checkpoint(supervisor, identities, deadline)
        report.update(result="pass", failure="none")
    except inference.Rejected as error:
        report["failure"] = error.category
    except service.Rejected as error:
        report["failure"] = error.category if error.category in ("deadline", "cancelled") else "identity"
    except service.identity.worker.Abort as error:
        report["failure"] = "deadline" if error.status == "timeout" else "internal"
    except (OSError, http.client.HTTPException):
        report["failure"] = "transport"
    except KeyboardInterrupt:
        report["failure"] = "cancelled"
    except (ValueError, TypeError, KeyError):
        report["failure"] = "protocol"
    except BaseException:
        report["failure"] = "internal"
    return report


def validate_report(raw, prepared_sha256, prior):
    require(type(raw) is bytes and len(raw) <= 8192, "protocol")
    inference.validate_report(encoded(prior), prepared_sha256)
    require(prior["result"] == "pass", "protocol")
    row = service.correctness.strict_json(raw)
    require(type(row) is dict and set(row) == {"schema", "result", "failure", "contract_sha256",
        "prepared_sha256", "inference_sha256", "attempts", "checks", "usage", "usage_unknown",
        "finish", "resources_closed", *inference.CLAIMS} and row["schema"] == SCHEMA
        and row["result"] in ("pass", "fail") and type(row["failure"]) is str
        and row["failure"] in inference.FAILURES and (row["failure"] == "none") == (row["result"] == "pass")
        and row["contract_sha256"] == process.digest(encoded(contract()))
        and row["prepared_sha256"] == prepared_sha256 and row["inference_sha256"] == process.digest(encoded(prior))
        and all(row[key] is False for key in inference.CLAIMS), "protocol")
    for key in ("attempts", "checks", "usage", "usage_unknown", "finish", "resources_closed"):
        require(type(row[key]) is dict and set(row[key]) == set(CASES), "protocol")
    for index, case in enumerate(CASES):
        for key in ("attempts", "checks", "usage_unknown", "resources_closed"):
            require(type(row[key][case]) is bool, "protocol")
        usage, finish = row["usage"][case], row["finish"][case]
        require(usage is None or type(usage) is dict and set(usage) == {"prompt_tokens", "completion_tokens"}
            and type(usage["prompt_tokens"]) is int and 0 <= usage["prompt_tokens"] <= 4096
            and type(usage["completion_tokens"]) is int and 0 < usage["completion_tokens"] <= service.OUTPUT,
            "protocol")
        require(finish is None or type(finish) is str and finish in ("stop", "tool_calls", "length"), "protocol")
        require((usage is None and finish is None or row["attempts"][case])
            and row["usage_unknown"][case] == (row["attempts"][case] and usage is None)
            and (not row["attempts"][case] or all(row["checks"][p] for p in CASES[:index]))
            and (not row["checks"][case] or row["attempts"][case] and row["resources_closed"][case]
                 and usage is not None and usage["prompt_tokens"] > 0 and usage["completion_tokens"] < service.OUTPUT
                 and finish == ("tool_calls" if case == "call" else "stop")), "protocol")
    require(row["result"] != "pass" or all(row["checks"].values()), "protocol")
    return row
