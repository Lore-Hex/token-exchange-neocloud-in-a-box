# SPDX-License-Identifier: Apache-2.0
"""Closed external roles and ordinary candidate; explicit lookup or full task."""

from decimal import Decimal
import os
import re
import signal
import subprocess
import time

import bounded_https
import tau_external_tokenizer
import tau_live_identity as private
import tau_local_vllm as local
import tau_pipe_driver as pipe
import tau_selected_policy as policy

worker, tau, require = local.worker, local.tau, local.worker.require
RESERVE = 60
CHAT = "/v1/chat/completions"
RESPONSES = "/v1/responses"
COUNT = "/v1/responses/input_tokens"
COUNT_BINDING = worker.digest(b"OpenAI Responses input_tokens; gpt-4.1-2025-04-14; text-only; no truncation; v1")
COUNT_RESERVATION = Decimal("2.095152")  # Entire published 1,047,576-token context at $2/M.


def projected(body, *, full_task=False):
    """Only two upstream optional aliases are removed; all values are retained."""
    result = worker.decode(worker.bounded_json(body, 65536))
    require(type(full_task) is bool and 0 < len(result["messages"]) <= 128
            and result["max_completion_tokens"] == (2048 if full_task else 256)
            and result["reasoning_effort"] == "none" and result["parallel_tool_calls"] is False)
    for message in result["messages"]:
        if "tool_calls" in message and message["tool_calls"] is None:
            del message["tool_calls"]
        for call in message.get("tool_calls", []):
            if "name" in call:
                require(call["name"] == call["function"]["name"])
                del call["name"]
    return result


def candidate_request(body, *, full_task=False):
    result = projected(body, full_task=full_task)
    require(result["model"] == policy.PUBLIC_MODEL)
    tools = result.get("tools")
    require(tools is None or type(tools) is list and 0 < len(tools) <= 16)
    names, seen, pending = set(), set(), set()
    for tool in tools or []:
        require(set(tool) == {"type", "function"} and tool["type"] == "function")
        function = tool["function"]
        require({"name", "parameters"} <= set(function) <= {"name", "parameters", "description"}
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", function["name"])
                and function["name"] not in names and function["parameters"].get("type") == "object"
                and ("description" not in function or type(function["description"]) is str
                     and len(function["description"].encode()) <= 4096))
        names.add(function["name"])
    require(result.get("tool_choice") in (("auto", "none") if tools else (None, "none")))
    if not tools:
        result.pop("parallel_tool_calls")
    for message in result["messages"]:
        role = message["role"]
        content = message["content"]
        require(content is None or type(content) is str and len(content.encode()) <= 65536)
        if role == "tool":
            require(set(message) == {"role", "content", "tool_call_id"}
                    and type(content) is str and message["tool_call_id"] in pending)
            pending.remove(message["tool_call_id"])
            continue
        require(not pending and role in ("system", "user", "assistant"))
        calls = message.get("tool_calls")
        require(set(message) <= ({"role", "content", "tool_calls"} if role == "assistant" else {"role", "content"})
                and (type(content) is str or role == "assistant" and calls))
        for call in calls or []:
            require(set(call) == {"id", "type", "function"} and call["type"] == "function"
                    and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", call["id"]) and call["id"] not in seen)
            function = call["function"]
            require(set(function) == {"name", "arguments"} and function["name"] in names
                    and type(function["arguments"]) is str and len(function["arguments"].encode()) <= 32768
                    and type(worker.decode(function["arguments"])) is dict)
            seen.add(call["id"])
            pending.add(call["id"])
    require(not pending)
    worker.bounded_json(result, 65536)
    return result


def simulator_request(body, *, full_task=False, support_chat=False):
    result = projected(body, full_task=full_task)
    require(result["model"] == policy.EXTERNAL_MODEL and result.get("tools") is None
            and result.get("tool_choice") in (None, "none") and (support_chat or "seed" not in result))
    for message in result["messages"]:
        require(set(message) == {"role", "content"} and type(message["content"]) is str
                and message["role"] in ("system", "user", "assistant"))
    if support_chat:
        # Pinned tau's LLMConfigMixin.set_seed supplies this for the simulator.
        # Chat Completions supports seed; Responses does not. Never discard it.
        # https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create
        if "seed" in result:
            require(type(result["seed"]) is int and 0 <= result["seed"] < 2**32)
        return {key: result[key] for key in ("model", "messages", "max_completion_tokens", "temperature",
                "stream", "n", "seed") if key in result} | {"store": False, "service_tier": "default"}
    # No previous_response_id/conversation, hidden tools, background work or
    # persistent storage. The original full text history remains in each call.
    return {"model": policy.EXTERNAL_MODEL, "input": result["messages"],
            "truncation": "disabled",
            "max_output_tokens": result["max_completion_tokens"], "temperature": 0, "store": False,
            "stream": False, "background": False, "service_tier": "default"}


class CountBudget:
    """One serial count-call cap shared by simulator and evaluator."""
    def __init__(self):
        self.attempts = 0

    def reserve(self):
        require(self.attempts < 32)
        self.attempts += 1


class Endpoint:
    def __init__(self, role, http, key, counter=None, *, full_task=False, count_budget=None, support_chat=False):
        require(type(full_task) is bool and role in (("candidate", "simulator", "evaluator")
                if full_task else ("candidate", "simulator")))
        require(not full_task or type(count_budget) is CountBudget)
        self.full_task, self.count_budget = full_task, count_budget
        require(type(support_chat) is bool)
        self.support_chat = support_chat
        self.context, self.output = (32768, 2048) if full_task else (8192, 256)
        self.role, self.http, self.key, self.counter = role, http, key, counter
        self.count_attempts = self.attempts = self.unknown = 0
        self.measured = []
        self.ready = None
        self.stage = "idle"

    def count(self, body, context, deadline):
        self.stage = "input-validation"
        require(context == self.context and self.ready is None and self.attempts < 32 and self.count_attempts < 32)
        wire = (candidate_request(body, full_task=self.full_task) if self.role == "candidate" else
                simulator_request(body, full_task=self.full_task, support_chat=self.support_chat))
        if self.role == "candidate":
            self.stage = "candidate-tokenization"
            count = self.counter.count(wire)
        else:
            # This is a separately counted non-generating authenticated call,
            # never an inference retry. Only input-semantic fields are shared.
            if self.full_task:
                self.count_budget.reserve()
            self.count_attempts += 1
            self.stage = "support-count"
            counted = self.http.post(COUNT, {"model": wire["model"],
                "input": wire["messages"] if self.support_chat else wire["input"],
                "truncation": "disabled"}, self.key, deadline)
            require(type(counted) is dict and set(counted) == {"object", "input_tokens"}
                    and counted["object"] == "response.input_tokens")
            count = counted["input_tokens"]
        require(type(count) is int and 0 < count <= context - self.output)
        self.ready = worker.digest(worker.encoded(body)), wire, count
        self.stage = "counted"
        return count

    def complete(self, body, prompt, deadline):
        require(self.ready is not None and self.ready[0] == worker.digest(worker.encoded(body))
                and self.ready[2] == prompt and self.attempts < 32)
        _, wire, _ = self.ready
        self.ready = None
        def sent():
            self.attempts += 1
            self.unknown += 1
        self.stage = "generation"
        chat = self.role == "candidate" or self.support_chat
        raw = self.http.post(CHAT if chat else RESPONSES,
                             wire, self.key, deadline, before_send=sent)
        require(type(raw) is dict and raw.get("model") == wire["model"] and type(raw.get("usage")) is dict)
        usage = raw["usage"]
        self.stage = "usage-validation"
        incoming, outgoing = ((usage.get("prompt_tokens"), usage.get("completion_tokens"))
                              if chat else (usage.get("input_tokens"), usage.get("output_tokens")))
        require(type(incoming) is int and 0 < incoming <= self.context
                and type(outgoing) is int and 0 <= outgoing <= self.output
                and type(usage.get("total_tokens")) is int and usage["total_tokens"] == incoming + outgoing)
        settled = {"prompt_tokens": incoming, "completion_tokens": outgoing, "total_tokens": incoming + outgoing}
        # Preserve known measured usage even if count, finish or content fails.
        self.measured.append(settled)
        self.unknown -= 1
        require(incoming == prompt and incoming + outgoing <= self.context)
        self.stage = "content-validation"
        if chat:
            require(self.role == "candidate" or raw.get("service_tier") == "default")
            require(type(raw.get("choices")) is list and len(raw["choices"]) == 1)
            choice = raw["choices"][0]
            message = choice["message"]
            require(type(message) is dict and set(message) <= {"role", "content", "tool_calls", "reasoning",
                    "reasoning_content", "refusal", "annotations", "audio", "function_call"}
                    and message.get("annotations") in (None, []) and message.get("audio") is None
                    and message.get("function_call") is None and message.get("reasoning") in (None, "")
                    and message.get("reasoning_content") in (None, "") and message.get("refusal") in (None, ""))
            self.stage = "completed"
            return {"usage": settled, "choices": [{"finish_reason": choice["finish_reason"],
                    "message": {key: value for key, value in message.items() if key in ("role", "content", "tool_calls")}}]}
        require(raw.get("service_tier") == "default" and raw.get("status") in ("completed", "incomplete")
                and raw.get("error") is None and raw.get("store") is False
                and usage.get("output_tokens_details", {}).get("reasoning_tokens", 0) == 0
                and type(raw.get("output")) is list and len(raw["output"]) == 1)
        message = raw["output"][0]
        require(message.get("type") == "message" and message.get("role") == "assistant"
                and type(message.get("content")) is list and len(message["content"]) == 1)
        content = message["content"][0]
        require(content.get("type") == "output_text" and type(content.get("text")) is str and content["text"])
        if raw["status"] == "incomplete":
            require(raw.get("incomplete_details") == {"reason": "max_output_tokens"})
        else:
            require(raw.get("incomplete_details") is None and message.get("status") == "completed")
        self.stage = "completed"
        return {"usage": settled, "choices": [{"finish_reason": "stop" if raw["status"] == "completed" else "length",
                 "message": {"role": "assistant", "content": content["text"]}}]}

    def observation(self):
        return {"generation_attempts": self.attempts, "count_attempts": self.count_attempts,
                "generation_api": "chat-completions" if self.role == "candidate" or self.support_chat else "responses",
                "stage": self.stage, "last_http_status": getattr(self.http, "last_status", None),
                # Reserve an entire GPT-4.1 input at standard input price
                # for each counting call; do not assume that endpoint is free.
                "count_cost_reserved_usd": format(COUNT_RESERVATION * self.count_attempts, "f"),
                "unknown_responses": self.unknown, "known_usage": {
                    key: sum(row[key] for row in self.measured)
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens")}}


class Runtime:
    def __init__(self, contract_path, contract_sha, candidate_key, simulator_key, source, tokenizer, wheel, deadline,
                 *, full_task=False):
        require(type(full_task) is bool)
        self.counter, self.endpoints = None, {}
        config = policy.configuration(customer_resolution=not full_task, external_simulator=True, full_task=full_task)
        context, output = config["contexts"]["candidate"], config["generation"]["max_tokens"]
        raw = private.private_read(contract_path, deadline)
        require(worker.digest(raw) == contract_sha)
        contract = worker.decode(raw)
        pinned = type(contract) is dict and contract.get("schema") == "nib.tau-external-trial-endpoint/v2"
        require(not pinned or full_task)
        require(type(contract) is dict and set(contract) == {"schema", "hostname", "address", "port", "model",
                "context_tokens", "output_tokens", "prices", "simulator", "retire_at", "conformance_sha256"}
                    | ({"candidate_profile", "evaluator"} if full_task else set())
                    | ({"certificate", "certificate_sha256", "serving_profile", "serving_context_tokens"} if pinned else set())
                and (pinned or contract["schema"] == ("nib.tau-external-trial-endpoint/v1" if full_task
                                           else "nib.tau-external-endpoint/v1"))
                and (type(contract["port"]) is int and 1 <= contract["port"] <= 65535 if pinned else contract["port"] == 443)
                and contract["model"] == policy.PUBLIC_MODEL and contract["context_tokens"] == context
                and contract["output_tokens"] == output and contract["prices"] == config["prices"]
                and contract["simulator"] == policy.EXTERNAL_MODEL
                and type(contract["retire_at"]) is int and contract["retire_at"] > time.time() + deadline - time.monotonic())
        require(not full_task or contract["candidate_profile"] == config["candidate_profile"]
                and contract["evaluator"] == policy.EXTERNAL_MODEL
                and all(type(contract[key]) is int for key in ("port", "context_tokens", "output_tokens")))
        tau.sha(contract["conformance_sha256"])
        require(not pinned or contract["serving_profile"] == "long1m-graphs"
                and type(contract["serving_context_tokens"]) is int
                and contract["serving_context_tokens"] == 1048576)
        try:
            self.counter = tau_external_tokenizer.Counter(source, tokenizer, wheel,
                **({"context_tokens": context} if full_task else {}))
            tls = {"port": contract["port"],
                   "certificate_pem": private.private_read(contract["certificate"], deadline),
                   "certificate_sha256": contract["certificate_sha256"]} if pinned else {}
            candidate_http = bounded_https.HTTPS(contract["hostname"], (CHAT,), address=contract["address"], **tls)
            keys = [private.private_read(path, deadline).rstrip(b"\n") for path in (candidate_key, simulator_key)]
            require(all(16 <= len(key) <= 4096 and all(33 <= byte <= 126 for byte in key) for key in keys))
            options = {"full_task": True, "count_budget": CountBudget()} if full_task else {}
            self.endpoints = {"candidate": Endpoint("candidate", candidate_http, keys[0], self.counter, **options),
                             **{role: Endpoint(role, bounded_https.HTTPS("api.openai.com", (COUNT, CHAT if pinned else RESPONSES)),
                                               keys[1], support_chat=pinned, **options) for role in config["support"]}}
            self.manifest = {"schema": policy.EXTERNAL_TRIAL_TRANSPORT if full_task else policy.EXTERNAL_TRANSPORT,
                "input": config, "settings": policy.settings(config),
                "binding_sha256": contract_sha, "framing": {role: {
                    "reasoning_effort": "none", "parallel_tool_calls": False, "context_tokens": context,
                    "served_model": policy.PUBLIC_MODEL if role == "candidate" else policy.EXTERNAL_MODEL,
                    "framing_sha256": self.counter.binding if role == "candidate" else COUNT_BINDING,
                    "tokenizer_sha256": tau.VOCABULARY_DIGEST if role == "candidate" else COUNT_BINDING}
                    for role in config["contexts"]}}
            policy.transport_manifest(self.manifest, worker.digest(worker.encoded(self.manifest)))
        except BaseException:
            self.close()
            raise

    def close(self):
        for endpoint in self.endpoints.values():
            endpoint.key, endpoint.ready = b"", None
        if self.counter is not None:
            self.counter.close()


class Session(local.CountedSession):
    def __init__(self, channel, runtime, *, assertions, clock=time.monotonic):
        frozen = runtime.manifest
        super().__init__(channel, frozen, worker.digest(worker.encoded(frozen)), "candidate", [],
                         assertions=assertions, clock=clock, selected_operational=True)
        self.endpoints = dict(runtime.endpoints)

    def accept_result(self, packet, deadline):
        if policy.is_external_trial(self.manifest["input"]):
            require(type(packet) is dict and "upstream_termination" in packet)
            upstream = worker.validate_upstream_termination(packet["upstream_termination"],
                                                            evaluated=packet.get("status") == "evaluated")
            require(upstream is None or upstream["tool_errors"] <= worker.MAX_TOOL_CALLS * (
                self.bridge.counts["agent"] + self.bridge.counts["simulator"]))
            sequence, result = super().accept_result({key: value for key, value in packet.items()
                                                     if key != "upstream_termination"}, deadline)
            result.update(schema="nib.tau-external-retail-trial/v1", upstream_termination=upstream,
                          selection=dict(policy.support.SELECTION))
            return sequence, result
        require(type(packet) is dict and packet.get("upstream_termination", False) is None)
        resolution = policy.resolution_result(packet["customer_resolution"], completed=packet.get("status") == "evaluated")
        sequence, result = super().accept_result({key: value for key, value in packet.items()
            if key not in ("customer_resolution", "upstream_termination")}, deadline)
        result["customer_resolution"] = resolution
        return sequence, result


def accepted(value):
    if value.get("schema") == "nib.tau-external-full-task/v1":
        row = value["trial"]
        # Lifecycle success is independent of reward. Zero remains a scored
        # failure of this task, not a transport failure or invented null reward.
        return (value["scorer_protocol_completed"] is True and value["attach_cleanup"] == "reaped"
                and value["scorer_exit_code"] == 0 and row["status"] == "evaluated"
                and row["reward"] in ("0.0", "1.0") and row.get("usage") is not None
                and row.get("upstream_termination", {}).get("reason") in ("user_stop", "agent_stop")
                and set(value["endpoints"]) == {"candidate", "simulator", "evaluator"}
                and all(item["generation_attempts"] > 0 and item["unknown_responses"] == 0
                        for item in value["endpoints"].values()))
    return (value["scorer_protocol_completed"] is True and value["attach_cleanup"] == "reaped"
            and value["scorer_exit_code"] == 0 and value["trial"]["status"] == "evaluated"
            and value["trial"]["reward"] is None and value["trial"].get("customer_resolution", {}).get("outcome") == "resolved"
            and value["trial"].get("usage") is not None
            and set(value["endpoints"]) == {"candidate", "simulator"}
            and all(row["generation_attempts"] > 0 for row in value["endpoints"].values())
            and all(row["unknown_responses"] == 0 for row in value["endpoints"].values()))


def run_process(command, environment, payload, plan, runtime, deadline):
    require(time.monotonic() + RESERVE < deadline <= time.monotonic() + 960)
    process = channel = session = None
    reader = writer = None
    completed, cleanup, code = False, "unconfirmed", None
    full_task = policy.is_external_trial(runtime.manifest["input"])
    trial = {"status": "protocol_error", "reward": None,
             **({"upstream_termination": None} if full_task else {"customer_resolution": None})}
    try:
        with worker.alarm(deadline - RESERVE):
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=environment, bufsize=0, close_fds=True, start_new_session=True)
            reader = os.dup(process.stdout.fileno())
            writer = os.dup(process.stdin.fileno())
            channel = local.transport.Channel(reader, writer)
            reader = writer = None
            process.stdout.close()
            process.stdin.close()
            session = Session(channel, runtime, assertions=pipe.selected_assertions(payload, plan["selection"]))
            session.bridge.budget.trial_deadline = min(session.bridge.budget.trial_deadline, deadline - RESERVE)
            while not session.serve_once():
                pass
            trial = session.result
            code = process.wait(timeout=max(0.001, min(10, deadline - RESERVE - time.monotonic())))
            require(code == 0 and os.read(channel.read_fd, 1) == b"")
            completed = True
    except BaseException as exc:
        if session is not None:
            # A later attach failure does not erase an already validated result.
            trial = session.result if session.result is not None else pipe.failure_result(session, exc)
    finally:
        prior = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        for sig in prior:
            signal.signal(sig, signal.SIG_IGN)
        try:
            for descriptor in (reader, writer):
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            try:
                if session is not None:
                    session.close()
                elif channel is not None:
                    channel.close()
            except Exception:
                completed = False
            if process is not None:
                for stream in (process.stdin, process.stdout):
                    if stream is not None:
                        try:
                            stream.close()
                        except Exception:
                            completed = False
                try:
                    if process.poll() is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    code = process.wait(timeout=max(0.001, min(10, deadline - time.monotonic())))
                    cleanup = "reaped"
                except (OSError, subprocess.TimeoutExpired):
                    completed = False
        finally:
            try:
                mask = signal.pthread_sigmask(signal.SIG_BLOCK, set(prior))
                try:
                    for sig, handler in prior.items():
                        signal.signal(sig, handler)
                finally:
                    signal.pthread_sigmask(signal.SIG_SETMASK, mask)
            except KeyboardInterrupt:
                completed = False
    cost = runtime.manifest["input"]["limits"]["cost_usd"]
    result = {"schema": "nib.tau-external-full-task/v1" if full_task else "nib.tau-external-lookup/v1",
            "scorer_protocol_completed": completed,
            "attach_cleanup": cleanup, "scorer_exit_code": code, "trial": trial,
            "endpoints": {name: endpoint.observation() for name, endpoint in runtime.endpoints.items()},
            "cost_ceiling_usd": format(COUNT_RESERVATION * 32 + Decimal(cost), "f"), "generation_cost_ceiling_usd": cost,
            "cost_basis": "standard-token-price-reservations-not-invoice",
            "quality_eligible": False, "confidential": False, "live_admission": False}
    if full_task:
        result.update(selection=dict(policy.support.SELECTION),
                      evaluation=dict(runtime.manifest["input"]["evaluation"]), full_benchmark=False)
    return result
