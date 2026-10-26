# SPDX-License-Identifier: Apache-2.0
"""Synthetic C1 qualification inside the existing authenticated service owner.

No engine launch, public destination, arbitrary prompts, cache flush or retry.
Invoked by the explicit long-only lifecycle hook; never on ordinary startup.
"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time

import engine_tokenizer_parity as parity
import selected_service_inference as inference
import stream_measurement as measurement
import stream_transport as transport

process, service = inference.process, inference.service
encoded = inference.encoded
SCHEMA = "nib.selected-long-qualification/v1"
WORKLOAD_SCHEMA = "nib.selected-long-workload/v1"
CONTEXT, LONG_OUTPUT = 131072, 16384
TARGETS = (32768, 65536, 131072)
PROMPTS = (*[target - 256 for target in TARGETS], CONTEXT - LONG_OUTPUT)
OUTPUTS = (256, 256, 256, LONG_OUTPUT)
CASES = tuple((level, scope) for level in range(3) for scope in ("cold", "warm", "different-namespace")) + ((3, "long-output"),)
PREPARE_SECONDS, RUN_SECONDS, MAX_PADDING = 180, 3600, 262144
MAX_REPORT = 12288
SELF, CHILD = "scripts/selected_long_qualification.py", "scripts/selected_long_tokenizer.py"
FAILURES = frozenset(("none", "contract", "identity", "deadline", "cancelled", "transport", "protocol",
                      "tokenizer", "prompt-count", "retrieval", "cache-usage-missing", "unexpected-cache-hit", "internal"))
CLAIMS = ("quality_qualified", "confidential", "capacity_qualified", "cache_isolation_qualified", "pricing_measured")


class Rejected(ValueError):
    def __init__(self, category):
        self.category = category if type(category) is str and category in FAILURES else "internal"
        super().__init__("selected long qualification rejected")


def require(condition, category):
    if not condition:
        raise Rejected(category)


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def messages(level, padding):
    require(type(level) is int and 0 <= level < 4 and type(padding) is int
            and 0 <= padding <= MAX_PADDING, "contract")
    start = "Synthetic benchmark. The initial code is CEDAR42.\n"
    end = ("\nReply only with the initial code, without explanation." if level < 3 else
           "\nList consecutive integers from 1 through 50000, one per line. Continue without explanation.")
    return [{"role": "user", "content": start + " x" * padding + end}]


def prepare(tokenizer, *, deadline):
    """Called only by the source-verified CPU child, never from an input plugin."""
    rows = []
    deadline = min(deadline, time.monotonic() + PREPARE_SECONDS)
    with service.identity.worker.alarm(deadline):
        for level, target in enumerate(PROMPTS):
            def count(padding):
                require(time.monotonic() < deadline, "deadline")
                ids = tokenizer.apply_chat_template(messages(level, padding), tools=None, tokenize=True,
                    add_generation_prompt=True, continue_final_message=False, **parity.TEMPLATE)
                require(type(ids) is list and 0 < len(ids) <= 2 * CONTEXT
                        and all(type(token) is int and 0 <= token < 2**31 for token in ids), "tokenizer")
                return len(ids), digest(ids)
            small, _ = count(64)
            large, _ = count(128)
            require(small < target and large > small, "tokenizer")
            center = 64 + (target - small) * 64 // (large - small)
            # Estimate only locates candidates. BPE monotonicity is not assumed;
            # every selected prompt must have an exactly measured token count.
            candidates = [center] + [center + sign * delta for delta in range(1, 33) for sign in (-1, 1)]
            for padding in candidates:
                if not 0 <= padding <= MAX_PADDING:
                    continue
                size, sha = count(padding)
                if size == target:
                    rows.append({"level": level, "padding": padding, "prompt_tokens": size,
                        "tokens_sha256": sha, "messages_sha256": digest(messages(level, padding))})
                    break
            else:
                require(False, "tokenizer")
    return validate_workload({"schema": WORKLOAD_SCHEMA, "cases": rows})


def validate_workload(row):
    require(type(row) is dict and set(row) == {"schema", "cases"} and row["schema"] == WORKLOAD_SCHEMA
            and type(row["cases"]) is list and len(row["cases"]) == 4, "tokenizer")
    for level, item in enumerate(row["cases"]):
        require(type(item) is dict and set(item) == {"level", "padding", "prompt_tokens", "tokens_sha256", "messages_sha256"}
                and type(item["level"]) is int and item["level"] == level
                and type(item["padding"]) is int and 0 <= item["padding"] <= MAX_PADDING
                and type(item["prompt_tokens"]) is int and item["prompt_tokens"] == PROMPTS[level]
                and item["prompt_tokens"] + OUTPUTS[level] <= CONTEXT
                and type(item["tokens_sha256"]) is str and re.fullmatch("[0-9a-f]{64}", item["tokens_sha256"])
                and item["messages_sha256"] == digest(messages(level, item["padding"])), "tokenizer")
    return row


def request_bytes(workload, level, namespace):
    validate_workload(workload)
    require(type(namespace) is str and re.fullmatch("[0-9a-f]{64}", namespace), "contract")
    raw = encoded({"model": service.MODEL, "messages": messages(level, workload["cases"][level]["padding"]),
        "max_tokens": OUTPUTS[level], "temperature": 0, "stream": True,
        "stream_options": {"include_usage": True}, "reasoning_effort": "none", "cache_salt": namespace})
    require(len(raw) <= 4 << 20, "contract")
    return raw


class ContentMeter(measurement.StreamMeasurement):
    """Transient bounded synthetic-answer comparison; never return model text."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.answer, self.overflow = "", False

    def message(self, payload):
        super().message(payload)
        if payload == b"[DONE]":
            return
        row = json.loads(payload)
        for choice in row["choices"]:
            value = choice["delta"].get("content") or ""
            if len(self.answer) + len(value) <= 128 and not self.overflow:
                self.answer += value
            else:
                self.overflow = True
                self.answer = ""

    def retrieval(self):
        return not self.overflow and self.answer.strip() == "CEDAR42"


def record_sample(row, sample):
    row["measurement"] = sample.metadata()
    interval = row["measurement"]["mean_output_token_interval_estimate_seconds"]
    row["decode_tokens_per_second_estimate"] = 1 / interval if interval else None


@dataclass(frozen=True)
class Observation:
    sample: measurement.Sample
    retrieval: bool
    resources_closed: bool


def failure(exc):
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        return "cancelled"
    if isinstance(exc, service.identity.worker.Abort):
        return "deadline" if exc.status == "timeout" else "internal"
    if isinstance(exc, (Rejected, inference.Rejected, service.Rejected)):
        return exc.category if exc.category in FAILURES else "identity"
    if isinstance(exc, TimeoutError):
        return "deadline"
    if isinstance(exc, OSError):
        return "transport"
    return "protocol"


def collect(supervisor, identities, workload, level, namespace, deadline, row):
    """One owned TLS connection and POST; settlement survives late failures."""
    started = time.monotonic()
    seconds = supervisor.contract.request_seconds
    deadline = min(deadline, started + seconds)
    meter = ContentMeter(service.MODEL, timeout_seconds=seconds,
        max_prompt_tokens=CONTEXT, max_output_tokens=OUTPUTS[level])
    category, closed = None, False
    try:
        with service.identity.worker.alarm(deadline):
            with inference.owned_tls(supervisor, identities, deadline) as connection:
                response_closed = True
                try:
                    key = service.identity.private_read(supervisor.inputs.root / "api-key", deadline)
                    service.pilot.validate_api_key(key)
                    body = request_bytes(workload, level, namespace)
                    header = ("POST /v1/chat/completions HTTP/1.1\r\nHost: " + supervisor.owner.hostname
                        + ":8443\r\nConnection: close\r\nAccept-Encoding: identity\r\nAccept: text/event-stream"
                        + "\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)) + "\r\n").encode("ascii")
                    row["attempted"] = True  # Charge unknown usage before a partial send.
                    connection.sendall(header + b"Authorization: Bearer " + key.removesuffix(b"\n") + b"\r\n\r\n" + body)
                    key = body = header = None
                    response_closed = False
                    with transport.StreamResponse(connection) as response:
                        response.begin()
                        length = transport.response_length(response)
                        count = 0
                        while True:
                            require(time.monotonic() < deadline, "deadline")
                            connection.settimeout(max(.001, deadline - time.monotonic()))
                            chunk = response.read1(measurement.MAX_EVENT)
                            if not chunk:
                                break
                            count += len(chunk)
                            meter.feed(chunk, time.monotonic() - started)
                            require(meter.failure is None, "protocol")
                        require(length is None or length == count, "protocol")
                        require(response.tail is not None and response.tail.read1(1) == b"", "protocol")
                        inference.checkpoint(supervisor, identities, deadline)
                    response_closed = True
                except BaseException as exc:
                    category = failure(exc)
                # Exiting the owned TLS context must succeed even when the
                # request failed. A close error remains unconfirmed below.
            closed = response_closed
    except BaseException as exc:
        category = failure(exc)
    elapsed = time.monotonic() - started
    if time.monotonic() >= deadline:
        category = category or "deadline"
    status = {"deadline": "timeout", "cancelled": "cancelled", "transport": "transport-error"}.get(category, "protocol-error")
    sample = meter.close(elapsed, failure=status if category else None)
    record_sample(row, sample)
    return Observation(sample, meter.retrieval(), closed)


def budgets(contract):
    # Deliberately reject the pre-integration contract. No fallback to global
    # 120/375 constants may extend work into the actual owner's cleanup reserve.
    seconds, reserve = contract.request_seconds, contract.cleanup_reserve
    require(type(seconds) is int and 1 <= seconds <= 900 and type(reserve) is int
            and seconds < reserve <= 3600, "contract")
    return seconds, reserve


def prepare_owned(supervisor, deadline):
    owner = supervisor.owner
    timeout = min(PREPARE_SECONDS, deadline - time.monotonic() - service.REAP_SECONDS)
    require(timeout > 0, "deadline")
    command = [owner.prepared.interpreter, "-I", "-S", "-B", str(process.ROOT / CHILD),
               owner.prepared.receipt_sha256]
    result = process.snapshot.execute_process_group(command, env=dict(owner.prepared.lab["environment"]),
        cwd="/", timeout=timeout, stdin=subprocess.DEVNULL, max_output_bytes=8192, max_stderr_bytes=4096)
    require(result.returncode == 0 and not result.stderr, "tokenizer")
    row = service.correctness.strict_json(result.stdout)
    require(type(row) is dict and set(row) == {"prepared_sha256", "workload"}
            and row["prepared_sha256"] == owner.prepared.receipt_sha256, "tokenizer")
    owner.prepared.check(deadline)
    return validate_workload(row["workload"])


def empty_report(execution_profile=process.engine_entry.LONG_PROFILE):
    require(type(execution_profile) is str and execution_profile in process.engine_entry.LONG_PROFILES, "contract")
    return {"schema": SCHEMA, "result": "fail", "failure": "contract", "prepared_sha256": None,
        "model": service.MODEL, "model_revision": service.models.select("uncensored")["hf_revision"],
        "execution_profile": execution_profile, "workload": None,
        "request_seconds": None, "cleanup_reserve_seconds": None, "elapsed_seconds": 0.0,
        "reserved_requests": len(CASES), "reserved_prompt_tokens": sum(PROMPTS[level] for level, _ in CASES),
        "reserved_output_tokens": sum(OUTPUTS[level] for level, _ in CASES), "concurrency": 1, "retries": 0,
        "engine_identity_before_sha256": None, "engine_identity_after_sha256": None,
        "request_rank_identity": "unknown", "cache_scope": "replica-local-routing-unobserved",
        "samples": [], "known_prompt_tokens": 0, "known_output_tokens": 0, "usage_unknown_requests": 0,
        "completed_output_tokens_per_second": None, "context_retrieval_pass": False,
        "cache_reuse_observed": False, "cold_and_negative_zero_observed": False,
        "long_output_limit_reached": False, "checks_pass": False, **dict.fromkeys(CLAIMS, False)}


def summarize(report):
    rows = report["samples"]
    report["known_prompt_tokens"] = sum(row["measurement"]["prompt_tokens"] or 0 for row in rows)
    report["known_output_tokens"] = sum(row["measurement"]["completion_tokens"] or 0 for row in rows)
    report["usage_unknown_requests"] = sum(row["attempted"] and row["measurement"]["completion_tokens"] is None for row in rows)
    good = [row for row in rows if row["accepted"]]
    if report["elapsed_seconds"] > 0 and rows:
        report["completed_output_tokens_per_second"] = sum(row["measurement"]["completion_tokens"] for row in good) / report["elapsed_seconds"]
    else:
        report["completed_output_tokens_per_second"] = None
    probes = [row for row in good if row["level"] < 3]
    report["context_retrieval_pass"] = len(probes) == 9 and all(row["retrieval_match"] for row in probes)
    warm = [row for row in probes if row["scope"] == "warm"]
    controls = [row for row in probes if row["scope"] != "warm"]
    report["cache_reuse_observed"] = bool(warm) and any(row["measurement"]["cached_prompt_tokens"] > 0 for row in warm)
    report["cold_and_negative_zero_observed"] = len(controls) == 6 and all(row["measurement"]["cached_prompt_tokens"] == 0 for row in controls)
    long = [row for row in good if row["level"] == 3]
    report["long_output_limit_reached"] = bool(long) and long[0]["measurement"]["completion_tokens"] == LONG_OUTPUT and long[0]["measurement"]["finish_reason"] == "length"
    report["checks_pass"] = (report["result"] == "completed" and len(good) == len(CASES)
        and report["context_retrieval_pass"] and report["cold_and_negative_zero_observed"]
        and len(warm) == 3 and all(row["measurement"]["cached_prompt_tokens"] > 0 for row in warm)
        and report["long_output_limit_reached"]
        and report["engine_identity_before_sha256"] is not None
        and report["engine_identity_before_sha256"] == report["engine_identity_after_sha256"])


def sequence(report, workload, collector, *, deadline, clock=time.monotonic):
    """Closed C1 schedule shared with unit tests; only run_owned grants a socket."""
    validate_workload(workload)
    namespaces = [(secrets.token_hex(32), secrets.token_hex(32)) for _ in range(4)]
    require(len({value for pair in namespaces for value in pair}) == 8, "internal")
    for level, scope in CASES:
        require(clock() < deadline, "deadline")
        row = {"level": level, "scope": scope, "attempted": False, "accepted": False,
               "requested_output_tokens": OUTPUTS[level], "retrieval_match": False,
               "resources_closed": False, "measurement": None, "decode_tokens_per_second_estimate": None}
        record_sample(row, measurement.Sample("protocol-error", 0.0, None, None, None,
                                              0, None, None, None, None))
        # Register before the collector can send or raise. A lost response is
        # unknown usage, never a missing/free request in the final accounting.
        report["samples"].append(row)
        namespace = namespaces[level][scope == "different-namespace"]
        observed = collector(workload, level, namespace, deadline, row)
        sample = observed.sample
        record_sample(row, sample)
        row.update(retrieval_match=observed.retrieval, resources_closed=observed.resources_closed)
        require(sample.status == "completed", {"timeout": "deadline", "cancelled": "cancelled",
                "transport-error": "transport"}.get(sample.status, "protocol"))
        require(row["attempted"] and observed.resources_closed
                and sample.first_content_seconds is not None, "protocol")
        require(sample.prompt_tokens == PROMPTS[level], "prompt-count")
        require(level == 3 or observed.retrieval, "retrieval")
        require(sample.cached_prompt_tokens is not None, "cache-usage-missing")
        require(scope == "warm" or sample.cached_prompt_tokens == 0, "unexpected-cache-hit")
        require(clock() < deadline, "deadline")
        row["accepted"] = True


def run_owned(supervisor):
    """One opt-in post-ready call; caller retains lifecycle/terminal evidence.

    This is a concrete owned-loopback client, not an alternate owner or a CLI
    that can discover/cancel another owner's processes. Failure must fail the
    lifecycle. This module never starts an engine or changes its configuration.
    """
    report, started = empty_report(), time.monotonic()
    try:
        require(threading.current_thread() is threading.main_thread() and type(supervisor) is service.Supervisor
                and type(supervisor.owner) is process.ProcessOwner and supervisor.used
                and supervisor.phase == "ready"
                and supervisor.contract.execution_profile in process.engine_entry.LONG_PROFILES
                and not getattr(supervisor, "_long_qualification_used", False), "contract")
        supervisor._long_qualification_used = True
        report["execution_profile"] = supervisor.contract.execution_profile
        seconds, reserve = budgets(supervisor.contract)
        report.update(request_seconds=seconds, cleanup_reserve_seconds=reserve,
                      prepared_sha256=supervisor.owner.prepared.receipt_sha256)
        deadline = min(started + RUN_SECONDS, supervisor.cutoff() - reserve)
        require(deadline > started, "deadline")
        identities = {role: dict(supervisor.owner.identities[role]) for role in service.ROLES}
        with inference.cancellation(supervisor.shutdown), service.identity.worker.alarm(deadline):
            require(type(supervisor.owner.prepared) is process.Prepared and
                    all(supervisor.children[role] is supervisor.owner.processes[role] for role in service.ROLES), "identity")
            require(Path(__file__).absolute() == process.ROOT / SELF, "identity")
            for name in (SELF, CHILD):
                raw = process.bootstrap.read_source(str(process.ROOT / name))
                require(process.digest(raw) == supervisor.owner.prepared.files[name], "identity")
            inference.checkpoint(supervisor, identities, deadline)
            report["engine_identity_before_sha256"] = digest(identities["engine"])
            workload = prepare_owned(supervisor, deadline)
            report["workload"] = workload
            sequence(report, workload, lambda *args: collect(supervisor, identities, *args), deadline=deadline)
            inference.checkpoint(supervisor, identities, deadline)
            report["engine_identity_after_sha256"] = digest(supervisor.owner.identities["engine"])
        report.update(result="completed", failure="none")
    except BaseException as exc:
        report["failure"] = failure(exc)
    report["elapsed_seconds"] = time.monotonic() - started
    summarize(report)
    return validate_report(encoded(report))


def validate_report(raw, prepared_sha256=None, *, execution_profile=None):
    """Closed metadata, with derived claims recomputed instead of trusted."""
    require(type(raw) is bytes and len(raw) <= MAX_REPORT, "protocol")
    row = service.correctness.strict_json(raw)
    require(type(row) is dict and type(row.get("execution_profile")) is str
            and row["execution_profile"] in process.engine_entry.LONG_PROFILES
            and (execution_profile is None or row["execution_profile"] == execution_profile), "protocol")
    template = empty_report(row["execution_profile"])
    require(type(row) is dict and set(row) == set(template) and row["schema"] == SCHEMA
            and row["result"] in ("completed", "fail") and row["failure"] in FAILURES
            and (row["result"] == "completed") == (row["failure"] == "none"), "protocol")
    mutable = {"result", "failure", "prepared_sha256", "workload", "request_seconds", "cleanup_reserve_seconds",
        "elapsed_seconds", "engine_identity_before_sha256", "engine_identity_after_sha256", "samples",
        "known_prompt_tokens", "known_output_tokens", "usage_unknown_requests", "completed_output_tokens_per_second",
        "context_retrieval_pass", "cache_reuse_observed", "cold_and_negative_zero_observed", "long_output_limit_reached", "checks_pass"}
    require(all(type(row[key]) is type(value) and row[key] == value for key, value in template.items() if key not in mutable), "protocol")
    for key in ("prepared_sha256", "engine_identity_before_sha256", "engine_identity_after_sha256"):
        require(row[key] is None or type(row[key]) is str and re.fullmatch("[0-9a-f]{64}", row[key]), "protocol")
    require(measurement.number(row["elapsed_seconds"]) and type(row["samples"]) is list
            and len(row["samples"]) <= len(CASES), "protocol")
    seconds, reserve = row["request_seconds"], row["cleanup_reserve_seconds"]
    require((seconds is None and reserve is None) or type(seconds) is int and 1 <= seconds <= 900
            and type(reserve) is int and seconds < reserve <= 3600, "protocol")
    for key in ("known_prompt_tokens", "known_output_tokens", "usage_unknown_requests"):
        require(measurement.integer(row[key], template["reserved_prompt_tokens"] + template["reserved_output_tokens"]), "protocol")
    for key in ("context_retrieval_pass", "cache_reuse_observed", "cold_and_negative_zero_observed", "long_output_limit_reached", "checks_pass"):
        require(type(row[key]) is bool, "protocol")
    require(row["completed_output_tokens_per_second"] is None or
            measurement.number(row["completed_output_tokens_per_second"]), "protocol")
    if row["workload"] is not None:
        validate_workload(row["workload"])
    for index, sample in enumerate(row["samples"]):
        level, scope = CASES[index]
        require(type(sample) is dict and set(sample) == {"level", "scope", "attempted", "accepted", "requested_output_tokens",
            "retrieval_match", "resources_closed", "measurement", "decode_tokens_per_second_estimate"}
            and type(sample["level"]) is int and sample["level"] == level and sample["scope"] == scope
            and type(sample["requested_output_tokens"]) is int and sample["requested_output_tokens"] == OUTPUTS[level]
            and all(type(sample[key]) is bool for key in ("attempted", "accepted", "retrieval_match", "resources_closed"))
            and (index == 0 or row["samples"][index - 1]["accepted"]), "protocol")
        m = sample["measurement"]
        keys = {"status", "elapsed_seconds", "ttft_seconds", "first_content_seconds", "mean_output_token_interval_estimate_seconds",
                "output_events", "prompt_tokens", "completion_tokens", "cached_prompt_tokens", "finish_reason"}
        require(type(m) is dict and set(m) == keys and m["status"] in measurement.FAILURES | {"completed"}
                and measurement.number(m["elapsed_seconds"]) and m["elapsed_seconds"] <= row["elapsed_seconds"]
                and measurement.integer(m["output_events"], measurement.MAX_STREAM), "protocol")
        for key in ("ttft_seconds", "first_content_seconds", "mean_output_token_interval_estimate_seconds"):
            require(m[key] is None or measurement.number(m[key]) and m[key] <= m["elapsed_seconds"], "protocol")
        p, o, c = m["prompt_tokens"], m["completion_tokens"], m["cached_prompt_tokens"]
        require((p is None) == (o is None) and (p is None or measurement.integer(p, CONTEXT)
            and measurement.integer(o, OUTPUTS[level]) and o > 0 and sample["attempted"])
            and (c is None or p is not None and measurement.integer(c, p))
            and m["finish_reason"] in (None, "stop", "length"), "protocol")
        interval = m["mean_output_token_interval_estimate_seconds"]
        rate = sample["decode_tokens_per_second_estimate"]
        require((rate is None or measurement.number(rate)) and rate == (1 / interval if interval else None), "protocol")
        if m["status"] == "completed":
            require(sample["attempted"] and o is not None and m["output_events"] > 0
                    and m["ttft_seconds"] is not None and m["finish_reason"] is not None
                    and seconds is not None and m["elapsed_seconds"] <= seconds, "protocol")
        else:
            require(m["ttft_seconds"] is None and m["first_content_seconds"] is None and interval is None, "protocol")
        if interval is not None:
            require(o is not None and o > 1 and m["output_events"] > 1 and interval > 0
                    and m["ttft_seconds"] + interval * (o - 1) <= m["elapsed_seconds"] + 1e-6, "protocol")
        if m["first_content_seconds"] is not None:
            require(m["ttft_seconds"] is not None and m["first_content_seconds"] >= m["ttft_seconds"], "protocol")
        if not sample["attempted"]:
            require(p is None and m["output_events"] == 0 and m["finish_reason"] is None, "protocol")
        if sample["accepted"]:
            require(sample["attempted"] and sample["resources_closed"] and m["status"] == "completed"
                and p == PROMPTS[level] and c is not None and m["first_content_seconds"] is not None
                and m["finish_reason"] in ("stop", "length") and (level == 3 or sample["retrieval_match"])
                and (scope == "warm" or c == 0), "protocol")
    if row["samples"]:
        require(row["workload"] is not None and row["prepared_sha256"] is not None
                and row["engine_identity_before_sha256"] is not None and seconds is not None, "protocol")
    if prepared_sha256 is not None:
        require(type(prepared_sha256) is str and re.fullmatch("[0-9a-f]{64}", prepared_sha256)
                and (row["prepared_sha256"] == prepared_sha256 or row["prepared_sha256"] is None
                     and row["result"] == "fail" and not row["samples"]), "protocol")
    if row["result"] == "completed":
        require(len(row["samples"]) == len(CASES) and all(item["accepted"] for item in row["samples"])
                and row["engine_identity_before_sha256"] == row["engine_identity_after_sha256"], "protocol")
    copy = dict(row)
    summarize(copy)
    require(copy == row, "protocol")
    return row


def cost_inputs(hourly_total_usd, aggregate_output_tokens_per_second, utilization):
    """Arithmetic only; all inputs must refer to the same measured deployment.

    Not called by the C1 harness. Aggregate busy-window throughput is not the
    per-stream decode estimate, and utilization must not be counted twice.
    """
    require(all(measurement.number(value) and value > 0
                for value in (hourly_total_usd, aggregate_output_tokens_per_second, utilization))
            and utilization <= 1, "contract")
    return hourly_total_usd * 1_000_000 / (3600 * aggregate_output_tokens_per_second * utilization)
