# SPDX-License-Identifier: Apache-2.0
"""Closed full-checker observation followed by retirement with an open stream."""

import asyncio
from contextlib import ExitStack
import http.client
import socket
import threading
import time

import selected_service_conformance as subset

inference, process, service = subset.inference, subset.process, subset.service
require, encoded = inference.require, inference.encoded
SCHEMA = "nib.selected-service-protocol/v1"
SECONDS = 480
JSON_CASES = ("callability", "pong", "temperature", "top-k", "thinking", "reasoning", "reasoning-effort", "max-token")
CASES = ("catalog", *JSON_CASES, "drain")
EXTRAS = (None, None, None, {"top_k": 40}, {"thinking": {"type": "disabled"}},
          {"reasoning": {"effort": "low"}}, {"reasoning_effort": "low"}, None)
DRAIN_PROMPT = "Write a long numbered list of 100 useful facts about mathematics. Explain each item in a full sentence."


def contract():
    return {"schema": "nib.selected-service-protocol-contract/v2", "subset": subset.contract(),
        "cases": list(CASES), "checks": subset.pinned.MANDATORY, "seconds": SECONDS,
        "request_seconds": inference.REQUEST_SECONDS, "retries": 0,
        "drain_prompt_sha256": process.digest(DRAIN_PROMPT.encode()),
        "drain_reasoning_effort": "none",
        "max_output_tokens": service.OUTPUT, "checker_statuses_unchanged": True}


def usage(raw, maximum):
    try:
        _, _, value = service.correctness.decode_completion(raw, service.MODEL, maximum)
        return value
    except (ValueError, TypeError, KeyError):
        return None


def checker_result(checks):
    if checks is None:
        return "incomplete"
    return next((status for status in ("fail", "warn", "skip") if status in checks.values()), "pass")


async def collect(checker, supervisor, identities, deadline, key, report):
    class Client:
        provider = None
        chat_body = checker.client.GatewayClient.chat_body
        index = 0

        async def models(self):
            require(not report["attempts"]["catalog"], "already-used")
            attempt = {"attempts": {"catalog": False}}
            try:
                with subset.response(supervisor, identities, deadline, "catalog", key, b"", attempt) as state:
                    observed, _, _ = state
                    raw = observed.read(65537)
                    require(len(raw) <= 65536 and observed.status == 200, "protocol")
                    subset.validate_catalog(checker, raw, supervisor, deadline)
                    return checker.httpx.Response(observed.status, content=raw,
                        request=checker.httpx.Request("GET", "https://127.0.0.1:8443/v1/models"))
            finally:
                report["attempts"]["catalog"] = attempt["attempts"]["catalog"]

        async def chat(self, *, model, prompt, temperature=0, max_tokens=None, max_tokens_key=None, extra=None):
            index = self.index
            require(index < len(JSON_CASES), "already-used")
            maximum = 4 if index == 0 else 16 if index == 7 else None
            expected_key = checker.client.max_token_parameter(checker.client.provider_for_model(model), model) \
                if index == 7 else None
            require(model == service.MODEL and prompt == checker.rules.PONG_PROMPT
                and temperature == (0 if index == 2 else None) and max_tokens == maximum
                and max_tokens_key == expected_key and extra == EXTRAS[index], "contract")
            case = JSON_CASES[index]
            self.index += 1
            body = encoded(self.chat_body(model=model, prompt=prompt, stream=False, temperature=temperature,
                max_tokens=max_tokens, max_tokens_key=max_tokens_key, extra=extra))
            attempt = {"attempts": {"json": False}}
            try:
                with subset.response(supervisor, identities, deadline, "json", key, body, attempt) as state:
                    observed, _, _ = state
                    raw = observed.read(subset.LIMIT + 1)
                    require(len(raw) <= subset.LIMIT, "protocol")
                    if observed.status == 200:
                        report["usage"][case] = usage(raw, maximum or service.OUTPUT)
                    return checker.httpx.Response(observed.status, content=raw,
                        request=checker.httpx.Request("POST", "https://127.0.0.1:8443/v1/chat/completions"))
            finally:
                report["attempts"][case] = attempt["attempts"]["json"]

    client = Client()
    evidence = checker.catalog.CatalogEvidence()
    catalog_rows, models = await checker.catalog.run_catalog_checks(client, catalog_url=None, evidence=evidence)
    require(models == [service.MODEL], "identity")
    call_rows, _ = await checker.callability.run_callability_checks(client, models, max_models=1,
        declared_chat_models=[service.MODEL])
    chat_rows = await checker.chat.run_chat_checks(client, service.MODEL)
    require(client.index == len(JSON_CASES), "protocol")
    checks = dict(report["subset"]["stream_checks"])
    for row in [*catalog_rows, *call_rows, *chat_rows]:
        if row.id == "catalog.declared-v2":
            require(row.status == "skip", "protocol")
            continue  # The separate subset already validated the owned catalog offline.
        require(type(row.id) is str and row.id in subset.pinned.MANDATORY and row.id not in checks
            and type(row.tier) is int and row.tier == subset.pinned.MANDATORY[row.id]
            and type(row.status) is str and row.status in ("pass", "fail", "warn", "skip"), "protocol")
        checks[row.id] = row.status
    require(set(checks) == set(subset.pinned.MANDATORY), "protocol")
    report["checks"] = checks
    report["all_17_pass"] = all(value == "pass" for value in checks.values())
    report["checker_result"] = checker_result(checks)


class Drain:
    """One fixed reader; it cannot open sockets, signal children or restart work."""

    def __init__(self, supervisor, deadline, report):
        self.supervisor, self.report = supervisor, report
        self.deadline = min(deadline, time.monotonic() + inference.REQUEST_SECONDS)
        self.started, self.count = time.monotonic(), 0
        self.stack = ExitStack()
        self.thread = None
        self.usage = None
        self.completed = self.closed = False
        self.finalized = False
        self.released = threading.Event()
        self.connection = None
        self.meter = subset.measurement.StreamMeasurement(service.MODEL,
            timeout_seconds=inference.REQUEST_SECONDS, max_prompt_tokens=4096, max_output_tokens=service.OUTPUT)
        self.attempt = {"attempts": {"drain": False}}

    def open(self, identities, key):
        supervisor, report = self.supervisor, self.report
        try:
            body = encoded({"model": service.MODEL, "stream": True, "stream_options": {"include_usage": True},
                "max_tokens": service.OUTPUT, "temperature": 0, "reasoning_effort": "none",
                "messages": [{"role": "user", "content": DRAIN_PROMPT}]})
            self.observed, self.connection, self.deadline = self.stack.enter_context(subset.response(
                supervisor, identities, self.deadline, "drain", key, body, self.attempt))
            self.length = subset.transport.response_length(self.observed)
            while self.meter.content is None:
                require(self.read(), "protocol")
                inference.checkpoint(supervisor, identities, self.deadline)
            require(self.meter.finish_reason is None and self.meter.usage is None and not self.meter.done,
                    "completion")
            inference.checkpoint(supervisor, identities, self.deadline)
            report["drain"]["first_content"] = True
        except BaseException:
            self.close()
            raise
        finally:
            report["attempts"]["drain"] = self.attempt["attempts"]["drain"]
            self.record_usage()

    def record_usage(self):
        value = self.usage
        if value is not None:
            self.report["usage"]["drain"] = {"prompt_tokens": value[0], "completion_tokens": value[1]}

    def close(self):
        if not self.finalized:
            self.finalized = True
            self.stack.close()
            self.closed = True
        self.report["drain"]["resources_closed"] = self.closed

    def read(self):
        require(time.monotonic() < self.deadline, "deadline")
        self.connection.settimeout(max(.001, self.deadline - time.monotonic()))
        block = self.observed.read1(subset.measurement.MAX_EVENT)
        self.count += len(block)
        require(self.count <= subset.LIMIT, "protocol")
        if block:
            self.meter.feed(block, time.monotonic() - self.started)
            if self.meter.usage is not None:
                self.usage = self.meter.usage
            require(self.meter.failure is None, "protocol")
        return bool(block)

    def consume(self):
        try:
            require(self.released.wait(max(0, self.deadline - time.monotonic()))
                    and self.report["drain"]["signal_sent"], "completion")
            while self.read():
                pass
            require(self.length is None or self.count == self.length, "protocol")
            require(time.monotonic() < self.deadline, "deadline")
            self.connection.settimeout(max(.001, self.deadline - time.monotonic()))
            require(self.observed.tail is not None and self.observed.tail.read1(1) == b"", "protocol")
            sample = self.meter.close(time.monotonic() - self.started)
            require(sample.status == "completed" and sample.prompt_tokens > 0 and sample.completion_tokens > 0,
                    "protocol")
            require(time.monotonic() < self.deadline, "deadline")
            self.completed = True
        except BaseException:
            pass  # No native/Python exception details enter the receipt.

    def start(self):
        require(self.thread is None and self.report["drain"]["first_content"]
                and not self.supervisor.shutdown.is_set(), "cancelled")
        self.thread = threading.Thread(target=self.consume, name="selected-drain-reader", daemon=True)
        self.thread.start()
        self.report["drain"]["reader_started"] = True
        self.report["drain"]["retirement_requested"] = True

    def retiring(self):
        require(self.report["drain"]["retirement_requested"] and not self.released.is_set(), "contract")
        self.report["drain"]["signal_sent"] = True
        self.released.set()

    def stop(self):
        self.released.set()
        if self.connection is not None:
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def join(self):
        if self.thread is not None and self.thread.ident is not None:
            if not self.report["drain"]["signal_sent"]:
                self.released.set()
            self.thread.join(max(0, min(5, self.supervisor.cutoff() - time.monotonic())))
            if self.thread.is_alive():
                self.stop()
                self.thread.join(max(0, min(5, self.supervisor.cutoff() - time.monotonic())))
            self.report["drain"]["reader_joined"] = not self.thread.is_alive()
        self.record_usage()
        self.report["drain"]["completed"] = self.completed and self.report["drain"]["reader_joined"]
        if self.thread is None or not self.thread.is_alive():
            self.close()
        return self.report["drain"]["reader_joined"]


class Run:
    def __init__(self, prior):
        self.drain = None
        self.report = {"schema": SCHEMA, "result": "fail", "failure": "internal",
            "contract_sha256": process.digest(encoded(contract())), "prepared_sha256": None,
            "inference_sha256": process.digest(encoded(prior)), "subset": None, "checks": None,
            "all_17_pass": False, "checker_result": "incomplete", "attempts": dict.fromkeys(CASES, False),
            "usage": dict.fromkeys((*JSON_CASES, "drain")),
            "usage_unknown": dict.fromkeys((*JSON_CASES, "drain"), False),
            "drain": dict.fromkeys(("first_content", "retirement_requested", "reader_started", "signal_sent",
                                    "completed", "reader_joined", "resources_closed"), False),
            **dict.fromkeys(inference.CLAIMS, False)}

    def finish(self, supervisor):
        try:
            if self.drain is not None:
                require(self.drain.join(), "completion")
                require(all(self.report["drain"].values()) and self.report["all_17_pass"]
                        and all(value is not None for value in self.report["usage"].values()), "completion")
                self.report.update(result="pass", failure="none")
            require(not supervisor.shutdown.is_set(), "cancelled")
        except inference.Rejected as error:
            self.report.update(result="fail", failure=error.category)
        except BaseException:
            self.report.update(result="fail", failure="internal")
        if supervisor.shutdown.is_set():
            self.report.update(result="fail", failure="cancelled")
        self.report["usage_unknown"] = {name: self.report["attempts"][name] and value is None
                                         for name, value in self.report["usage"].items()}
        return self.report


def start(supervisor, prior):
    run = Run(prior)
    report = run.report
    try:
        require(type(supervisor) is service.Supervisor and type(supervisor.owner) is process.ProcessOwner
            and threading.current_thread() is threading.main_thread(), "contract")
        require(not getattr(supervisor, "_selected_protocol_used", False), "already-used")
        supervisor._selected_protocol_used = True
        prepared = supervisor.owner.prepared
        report["prepared_sha256"] = prepared.receipt_sha256
        report["subset"] = subset.validate_report(encoded(subset.run_once(supervisor, prior)),
            prepared.receipt_sha256, prior)
        require(report["subset"]["result"] == "pass", "completion")
        identities = {role: dict(supervisor.owner.identities[role]) for role in service.ROLES}
        deadline = min(time.monotonic() + SECONDS, supervisor.cutoff() - service.CLEANUP_RESERVE)
        with inference.cancellation(supervisor.shutdown), service.identity.worker.alarm(deadline):
            inference.checkpoint(supervisor, identities, deadline)
            require(prepared.files["scripts/selected_service_protocol.py"] ==
                    process.digest(process.bootstrap.read_source(__file__)), "identity")
            key = service.identity.private_read(supervisor.inputs.root / "api-key", deadline)
            service.pilot.validate_api_key(key)
            with subset.quiet_checker(), subset.checker_modules(process.ROOT / "checker", deadline) as checker:
                asyncio.run(collect(checker, supervisor, identities, deadline, key.removesuffix(b"\n"), report))
            run.drain = Drain(supervisor, deadline, report)
            run.drain.open(identities, key.removesuffix(b"\n"))
        run.drain.start()
    except inference.Rejected as error:
        report["failure"] = error.category
    except service.Rejected as error:
        report["failure"] = error.category if error.category in ("deadline", "cancelled") else "identity"
    except service.identity.worker.Abort as error:
        report["failure"] = "deadline" if error.status == "timeout" else "internal"
    except (OSError, http.client.HTTPException):
        report["failure"] = "transport"
    except BaseException:
        report["failure"] = "internal"
    return run


def validate_report(raw, prepared_sha256, prior):
    require(type(raw) is bytes and len(raw) <= 16384, "protocol")
    inference.validate_report(encoded(prior), prepared_sha256)
    require(prior["result"] == "pass", "protocol")
    row = service.correctness.strict_json(raw)
    require(type(row) is dict and set(row) == {"schema", "result", "failure", "contract_sha256",
        "prepared_sha256", "inference_sha256", "subset", "checks", "all_17_pass", "attempts", "usage",
        "usage_unknown", "drain", "checker_result", *inference.CLAIMS} and row["schema"] == SCHEMA
        and row["result"] in ("pass", "fail") and type(row["failure"]) is str
        and row["failure"] in inference.FAILURES and (row["result"] == "pass") == (row["failure"] == "none")
        and row["contract_sha256"] == process.digest(encoded(contract()))
        and row["prepared_sha256"] == prepared_sha256
        and row["inference_sha256"] == process.digest(encoded(prior))
        and all(row[name] is False for name in inference.CLAIMS), "protocol")
    if row["subset"] is not None:
        subset.validate_report(encoded(row["subset"]), prepared_sha256, prior)
    require(type(row["attempts"]) is dict and set(row["attempts"]) == set(CASES)
        and all(type(value) is bool for value in row["attempts"].values()), "protocol")
    for index, case in enumerate(CASES):
        require(not row["attempts"][case] or all(row["attempts"][name] for name in CASES[:index]), "protocol")
    require(not any(row["attempts"].values()) or row["subset"] is not None
            and row["subset"]["result"] == "pass", "protocol")
    checks = row["checks"]
    require(checks is None or type(checks) is dict and set(checks) == set(subset.pinned.MANDATORY)
        and all(type(value) is str and value in ("pass", "fail", "warn", "skip") for value in checks.values())
        and all(row["attempts"][case] for case in CASES[:-1]), "protocol")
    require(type(row["all_17_pass"]) is bool
        and row["all_17_pass"] == (checks is not None and all(value == "pass" for value in checks.values())),
        "protocol")
    require(row["checker_result"] == checker_result(checks), "protocol")
    if checks is not None:
        require(all(checks[name] == row["subset"]["stream_checks"][name] for name in subset.STREAM_CHECKS), "protocol")
    for name in ("usage", "usage_unknown"):
        require(type(row[name]) is dict and set(row[name]) == {*JSON_CASES, "drain"}, "protocol")
    for case, value in row["usage"].items():
        maximum = 4 if case == "callability" else 16 if case == "max-token" else service.OUTPUT
        require(value is None or type(value) is dict and set(value) == {"prompt_tokens", "completion_tokens"}
            and type(value["prompt_tokens"]) is int and 0 <= value["prompt_tokens"] <= 4096
            and type(value["completion_tokens"]) is int and 0 <= value["completion_tokens"] <= maximum
            and row["attempts"][case], "protocol")
        require(type(row["usage_unknown"][case]) is bool
            and row["usage_unknown"][case] == (row["attempts"][case] and value is None), "protocol")
    drain = row["drain"]
    require(type(drain) is dict and set(drain) == {"first_content", "retirement_requested", "reader_started",
        "signal_sent", "completed", "reader_joined", "resources_closed"}
        and all(type(value) is bool for value in drain.values()), "protocol")
    require(not any(value for name, value in drain.items() if name != "resources_closed")
            or row["attempts"]["drain"] and checks is not None, "protocol")
    require(not drain["retirement_requested"] or drain["first_content"] and drain["reader_started"], "protocol")
    require(not drain["signal_sent"] or drain["retirement_requested"], "protocol")
    require(not drain["reader_joined"] or drain["reader_started"], "protocol")
    require(not drain["completed"] or drain["signal_sent"] and row["usage"]["drain"] is not None
        and all(value > 0 for value in row["usage"]["drain"].values()), "protocol")
    require(row["result"] != "pass" or row["all_17_pass"] and all(row["attempts"].values())
        and all(drain.values()) and not any(row["usage_unknown"].values()), "protocol")
    return row
