# SPDX-License-Identifier: Apache-2.0
"""Scripted original-worker peers. No network or live factory."""

import time

import tau_external as external
import tau_telecom_catalog as catalog

worker, require, policy = external.worker, external.require, external.policy
TOKENS = 100  # Deliberately synthetic, not a tokenizer or endpoint measurement.
BINDING = worker.digest(b"offline telecom scripted peers; synthetic input tokens=100; v1")


class Counter:
    def count(self, wire):
        return TOKENS


class Peer:
    """Only fixed fixture responses; never accepts a URL, key or HTTP client."""
    def __init__(self, role, assertions):
        require(role in ("candidate", "simulator", "evaluator"))
        self.role, self.assertions = role, assertions
        self.steps = self.counts = self.tool_round_trips = 0
        self.pair = None

    def post(self, route, body, key, deadline, *, before_send=None):
        require(key == b"" and time.monotonic() < deadline)
        if route == external.COUNT:
            require(before_send is None and self.role != "candidate")
            if self.role == "simulator":
                require(self.pair is not None and body == self.pair["count"])
            self.counts += 1
            return {"object": "response.input_tokens", "input_tokens": TOKENS}
        require(route == external.CHAT and callable(before_send))
        if self.role == "simulator":
            require(self.pair is not None and body == self.pair["chat"])
            self.pair = None
        before_send()
        self.steps += 1
        message = self.message(body)
        return {"model": body["model"], "service_tier": "default",
                "choices": [{"message": message, "finish_reason": "tool_calls" if "tool_calls" in message else "stop"}],
                "usage": {"prompt_tokens": TOKENS, "completion_tokens": 1, "total_tokens": TOKENS + 1}}

    def message(self, body):
        message = {"role": "assistant", "content": None}
        if self.role in ("candidate", "simulator") and self.steps == 1:
            name = "check_status_bar" if self.role == "simulator" else "get_customer_by_name"
            arguments = {} if self.role == "simulator" else {"full_name": "SYNTHETIC_NO_MATCH", "dob": "1900-01-01"}
            require(name in {tool["function"]["name"] for tool in body["tools"]})
            message["tool_calls"] = [{"id": "offline_" + self.role, "type": "function",
                "function": {"name": name, "arguments": worker.encoded(arguments).decode().strip()}}]
        elif self.role in ("candidate", "simulator") and self.steps == 2:
            last = body["messages"][-1]
            require(last["role"] == "tool" and last["tool_call_id"] == "offline_" + self.role
                    and type(last["content"]) is str and last["content"])
            self.tool_round_trips += 1
            message["content"] = "Please help with my request." if self.role == "simulator" else "I cannot complete this request."
        elif self.role == "simulator" and self.steps == 3:
            message["content"] = "###STOP###"
        elif self.role == "evaluator" and self.steps == 1:
            message["content"] = worker.encoded({"results": [{"expectedOutcome": item,
                "metExpectation": False, "reasoning": "Scripted integration fixture."}
                for item in self.assertions]}).decode()
        else:
            require(False)
        return message


class AirlinePeer(Peer):
    """Pinned airline has agent tools but no user tools; exercise that asymmetry."""
    def post(self, route, body, key, deadline, *, before_send=None):
        if self.role == "simulator":
            if route == external.COUNT:
                self.pair = {"count": worker.decode(worker.encoded(body))}
            elif route == external.CHAT:
                require("tools" not in body and self.pair is not None and self.pair["count"] ==
                        {"model": body["model"], "input": body["messages"], "truncation": "disabled"})
                self.pair["chat"] = body
        return super().post(route, body, key, deadline, before_send=before_send)

    def message(self, body):
        if self.role == "evaluator":
            return super().message(body)
        message = {"role": "assistant", "content": None}
        if self.role == "candidate" and self.steps == 1:
            require("calculate" in {tool["function"]["name"] for tool in body["tools"]})
            message["tool_calls"] = [{"id": "offline_candidate", "type": "function",
                "function": {"name": "calculate", "arguments": '{"expression":"1+1"}'}}]
        elif self.role == "candidate" and self.steps == 2:
            last = body["messages"][-1]
            require(last["role"] == "tool" and last["tool_call_id"] == "offline_candidate"
                    and last["content"] == "2.0")
            self.tool_round_trips += 1
            message["content"] = "I cannot complete this request."
        elif self.role == "simulator" and self.steps in (1, 2):
            message["content"] = "Please help with my request." if self.steps == 1 else "###STOP###"
        else:
            require(False)
        return message


def source_assertions(stage, archive_path, deadline):
    runtime = external.source_support().runtime
    raw = runtime.read_file(runtime.ROOT / runtime.tau.PINS, 1 << 20, deadline)
    require(runtime.digest(raw) == runtime.tau.PINS_SHA)
    pins = runtime.tau.json_value(raw)
    _, sources = runtime.source_inputs(runtime.read_file(archive_path, runtime.ARCHIVE_LIMIT, deadline), pins, deadline)
    catalog.selection.verify_sources(stage["plan"], sources, pins)
    selected = catalog.selection.selected(stage)
    rows = runtime.tau.json_value(sources["data/tau2/domains/" + selected["domain"] + "/tasks.json"])
    rows = rows["tasks"] if type(rows) is dict else rows
    matches = [row for row in rows if row["id"] == selected["task_id"]]
    require(len(matches) == 1)
    assertions = (matches[0].get("evaluation_criteria") or {}).get("nl_assertions") or []
    catalog.selection.validate_assertions(stage, assertions)
    return assertions


class Simulator(external.Endpoint):
    def __init__(self, contract, archive, assertions, budget):
        super().__init__("simulator", Peer("simulator", assertions), b"", full_task=True,
                         count_budget=budget, support_chat=True)
        self.contract, self.archive = contract, archive

    def count(self, body, context, deadline):
        self.stage = "input-validation"
        require(context == self.context and self.ready is None and self.attempts < 32 and self.count_attempts < 32)
        pair = catalog.project(body, self.contract, self.archive, deadline)
        self.count_budget.reserve()
        self.count_attempts += 1
        self.stage = "support-count"
        self.http.pair = pair
        counted = self.http.post(external.COUNT, pair["count"], b"", deadline)
        require(counted == {"object": "response.input_tokens", "input_tokens": TOKENS})
        self.ready = worker.digest(worker.encoded(body)), pair["chat"], TOKENS
        self.stage = "counted"
        return TOKENS


class SourcePeer(Peer):
    """Exercise the real bridge with fixture replies, checking both wire forms."""
    def __init__(self, role, assertions, tools):
        super().__init__(role, assertions)
        self.tools, self.counted = tools, None

    def post(self, route, body, key, deadline, *, before_send=None):
        if self.role == "simulator":
            if route == external.COUNT:
                self.counted = worker.decode(worker.encoded(body))
                self.pair = {"count": self.counted}
            elif route == external.CHAT:
                original = {key: value for key, value in body.items() if key not in ("store", "service_tier")}
                original["reasoning_effort"] = "none"
                pair = catalog.projection.project(original, self.tools)
                require(pair["count"] == self.counted and pair["chat"] == body)
                self.pair = pair
                self.counted = None
        return super().post(route, body, key, deadline, before_send=before_send)


class Offline:
    def __init__(self, stage, archive, assertions, deadline, *, source_bound=False):
        require(type(source_bound) is bool)
        self.domain = catalog.selection.selected(stage)["domain"]
        require(self.domain == "telecom" or self.domain == "airline" and source_bound)
        self.contract = catalog.bind(stage, archive, deadline) if self.domain == "telecom" else None
        catalog.selection.validate_assertions(stage, assertions)
        config = policy.configuration(full_task=True, external_simulator=True, stage=stage)
        budget = external.CountBudget()
        if source_bound:
            support = external.source_support()
            source = support.Source(archive, stage, deadline)
            if self.domain == "airline":
                self.contract = source.binding
            admission = support.Admission(config)
            self.endpoints = {role: support.Endpoint(role,
                SourcePeer(role, assertions, source.tools) if self.domain == "telecom" else AirlinePeer(role, assertions), b"", Counter(),
                full_task=True, count_budget=budget, support_chat=True, source=source, admission=admission)
                for role in ("candidate", "simulator", "evaluator")}
        else:
            self.endpoints = {"simulator": Simulator(self.contract, archive, assertions, budget),
                **{role: external.Endpoint(role, Peer(role, assertions), b"", Counter(), full_task=True,
                    count_budget=budget, support_chat=True) for role in ("candidate", "evaluator")}}
        self.manifest = {"schema": policy.external_transport(config), "input": config,
            "settings": policy.settings(config), "binding_sha256": worker.digest(worker.encoded(self.contract)),
            "framing": {role: {"reasoning_effort": "none", "parallel_tool_calls": False,
                "context_tokens": 32768, "served_model": policy.PUBLIC_MODEL if role == "candidate" else policy.EXTERNAL_MODEL,
                "framing_sha256": BINDING, "tokenizer_sha256": external.tau.VOCABULARY_DIGEST
                    if role == "candidate" else BINDING} for role in config["contexts"]}}
        policy.transport_manifest(self.manifest, worker.digest(worker.encoded(self.manifest)))

    def observation(self):
        return {role: {"generation_steps": endpoint.http.steps, "synthetic_count_calls": endpoint.http.counts,
                       "tool_round_trips": endpoint.http.tool_round_trips} for role, endpoint in self.endpoints.items()}

    def accepted(self, result):
        return (external.accepted(result) and result["trial"]["upstream_termination"]["tool_errors"] == 0
                and self.endpoints["candidate"].http.tool_round_trips == 1
                and self.endpoints["simulator"].http.tool_round_trips == (1 if self.domain == "telecom" else 0))

    def close(self):
        for endpoint in self.endpoints.values():
            endpoint.ready = None
            endpoint.http.pair = None
            endpoint.http.assertions = []
