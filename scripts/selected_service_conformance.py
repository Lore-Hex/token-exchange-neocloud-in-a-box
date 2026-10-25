# SPDX-License-Identifier: Apache-2.0
"""A closed catalog/Tier-4 subset on the existing owned TLS service, not a full gate."""

import asyncio
from contextlib import asynccontextmanager, contextmanager, redirect_stdout, redirect_stderr
import http.client
import os
import sys
import threading
import time
from types import SimpleNamespace

import provider_conformance_worker as pinned
import selected_service_inference as inference
import stream_measurement as measurement
import stream_transport as transport

process, service = inference.process, inference.service
require, encoded = inference.require, inference.encoded
SCHEMA = "nib.selected-service-conformance/v1"
SECONDS, LIMIT = 160, 1 << 20
CASES = ("missing-key", "wrong-key", "catalog", "stream")
STREAM_CHECKS = tuple(sorted(name for name in pinned.MANDATORY if name.startswith("stream.")))


def contract():
    return {"schema": "nib.selected-service-conformance-contract/v1", "model": service.MODEL,
        "checker_wheel_sha256": pinned.CHECKER_SHA, "requirements_sha256": pinned.LOCK_SHA,
        "upstream_contract": pinned.CONTRACT, "catalog_schema": "pinned-vendored-offline",
        "stream_checks": list(STREAM_CHECKS), "cases": list(CASES), "seconds": SECONDS,
        "request_seconds": inference.REQUEST_SECONDS, "max_output_tokens": service.OUTPUT,
        "response_bytes": LIMIT, "authenticated_requests": 1, "retries": 0,
        "full_provider_gate": False}


def payload_files(root, deadline):
    """Reuse the lab payload verifier; the prepared receipt binds every byte."""
    with service.identity.worker.alarm(deadline):
        raw = process.bootstrap.read_source(str(root / "trial.json"))
        plan = pinned.verify(root, process.digest(raw))
        require(plan.get("model_profile") == service.models.select("uncensored"), "identity")
        return set(plan["files"]) | {"trial.json"}


@contextmanager
def checker_modules(root, deadline):
    # The isolated owner has no site-packages. Only the offline, locked payload
    # enters its import path. In particular, never use the engine's dependencies.
    payload_files(root, deadline)
    packages = root / "packages"
    names = {path.name.split(".")[0] for path in packages.iterdir() if ".dist-info" not in path.name}
    require(not any(name in sys.modules for name in names), "identity")
    original = list(sys.path)
    sys.path.append(str(packages))
    try:
        import httpx
        import tr_provider_check
        from tr_provider_check import contract as rules
        from tr_provider_check import http as client
        from tr_provider_check.checks import callability, catalog, chat, streaming
        from tr_provider_check.snapshot import contract_version
        require(tr_provider_check.__version__ == "0.1.8" and contract_version == pinned.CONTRACT, "identity")
        yield SimpleNamespace(httpx=httpx, rules=rules, client=client, catalog=catalog, streaming=streaming,
                              callability=callability, chat=chat)
    finally:
        sys.path[:] = original
        for name in list(sys.modules):
            if name.split(".")[0] in names:
                del sys.modules[name]


@contextmanager
def quiet_checker():
    """Never release upstream diagnostics, including native writes, to the owner log."""
    saved = {}
    try:
        for number in (1, 2):
            saved[number] = os.dup(number)
        null = os.open(os.devnull, os.O_WRONLY)
        try:
            for number in saved:
                os.dup2(null, number)
        finally:
            os.close(null)
        with open(os.devnull, "w") as output, redirect_stdout(output), redirect_stderr(output):
            yield
    finally:
        for number, descriptor in saved.items():
            try:
                os.dup2(descriptor, number)
            finally:
                os.close(descriptor)


@contextmanager
def response(supervisor, identities, deadline, case, key, body, report):
    require(case in (*CASES, "json", "drain") and report["attempts"][case] is False, "already-used")
    authenticated = case in ("stream", "json", "drain")
    deadline = min(deadline, time.monotonic() +
        (inference.REQUEST_SECONDS if authenticated else inference.NEGATIVE_SECONDS))
    # The explicit drain phase outlives the main-thread hook alarm. Its reader
    # uses the same absolute socket deadline; ordinary exchanges keep the alarm.
    connect = inference.owned_tls if case == "drain" else inference.tls_connection
    with connect(supervisor, identities, deadline) as connection:
        catalog = case == "catalog"
        method, path = ("GET", "/v1/models") if catalog else ("POST", "/v1/chat/completions")
        header = (method + " " + path + " HTTP/1.1\r\nHost: " + supervisor.owner.hostname
            + "\r\nConnection: close\r\nAccept: " + ("application/json" if catalog or case == "json" else "text/event-stream")
            + "\r\nAccept-Encoding: identity\r\nContent-Type: application/json\r\n"
            + "Content-Length: " + str(len(body)) + "\r\n").encode("ascii")
        if case == "wrong-key" or authenticated:
            header += b"Authorization: Bearer " + (key if authenticated else b"invalid-synthetic-key") + b"\r\n"
        report["attempts"][case] = True
        if authenticated:
            report["usage_unknown"] = True
        connection.sendall(header + b"\r\n" + body)
        with transport.StreamResponse(connection) as observed:
            observed.begin()
            yield observed, connection, deadline
        if case != "drain":
            inference.checkpoint(supervisor, identities, deadline)


def validate_catalog(checker, raw, supervisor, deadline):
    payload = service.correctness.strict_json(raw)
    catalog = checker.catalog
    try:
        catalog.Draft202012Validator(catalog._vendored_schema(),
            format_checker=catalog.FormatChecker()).validate(payload)
    except catalog.ValidationError:
        raise inference.Rejected("protocol") from None
    catalog._validate_vendored_rules(payload)
    require(len(payload["data"]) == 1, "protocol")
    row = payload["data"][0]
    provider = service.correctness.strict_json(service.identity.private_read(
        supervisor.inputs.root / "provider.json", deadline))
    require(all(payload["provider"].get(name) == value for name, value in provider.items())
        and row["id"] == service.MODEL and row["context_length"] == supervisor.contract.context_tokens
        and row["max_output_tokens"] == service.OUTPUT
        and row["capabilities"] == {"streaming": True, "tools": False, "structured_output": False,
                                    "reasoning": False, "prompt_caching": False}
        and row["pricing"]["input"] == supervisor.contract.input_price
        and row["pricing"]["output"] == supervisor.contract.output_price
        and row["lifecycle"]["retirement_at"] == supervisor.contract.admission_stop_at, "protocol")
    evidence = catalog.CatalogEvidence()
    evidence.declared_models.append(row)
    return evidence


async def collect(checker, supervisor, identities, deadline, key, report):
    # Source-verified 0.1.8 accepts this narrow client interface. Do not invoke
    # GatewayClient transport/retries or run_catalog_checks' independent client.
    class Client:
        provider = None
        chat_body = checker.client.GatewayClient.chat_body

        @asynccontextmanager
        async def stream_chat(self, *, model, prompt, temperature):
            require(model == service.MODEL and prompt == checker.rules.PONG_PROMPT
                    and temperature is None, "contract")
            started = time.monotonic()
            meter = measurement.StreamMeasurement(service.MODEL, timeout_seconds=inference.REQUEST_SECONDS,
                max_prompt_tokens=4096, max_output_tokens=service.OUTPUT)
            complete = False
            class Blocks(checker.httpx.AsyncByteStream):
                async def __aiter__(self):
                    nonlocal complete
                    count = 0
                    while True:
                        connection.settimeout(max(.001, request_deadline - time.monotonic()))
                        block = observed.read1(measurement.MAX_EVENT)
                        if not block:
                            break
                        count += len(block)
                        require(count <= LIMIT, "protocol")
                        meter.feed(block, time.monotonic() - started)
                        # Preserve known usage even if framing, identity, close,
                        # upstream checks or subsequent cleanup later fails.
                        if meter.usage is not None:
                            report.update(usage={"prompt_tokens": meter.usage[0],
                                "completion_tokens": meter.usage[1]}, usage_unknown=False)
                        require(meter.failure is None, "deadline" if meter.failure == "timeout" else "protocol")
                        inference.checkpoint(supervisor, identities, request_deadline)
                        yield block
                    require(length is None or count == length, "protocol")
                    require(observed.tail is not None and observed.tail.read1(1) == b"", "protocol")
                    sample = meter.close(time.monotonic() - started)
                    require(sample.status == "completed" and sample.first_content_seconds is not None
                            and sample.prompt_tokens > 0 and sample.completion_tokens > 0, "protocol")
                    complete = True
            with response(supervisor, identities, deadline, "stream", key, body, report) as state:
                observed, connection, request_deadline = state
                length = transport.response_length(observed)
                value = checker.httpx.Response(200, headers=observed.getheaders(), stream=Blocks(),
                    request=checker.httpx.Request("POST", "https://127.0.0.1:8443/v1/chat/completions"))
                try:
                    yield value
                    require(complete, "protocol")
                finally:
                    await value.aclose()

    client = Client()
    body = encoded(client.chat_body(model=service.MODEL, prompt=checker.rules.PONG_PROMPT,
                                   temperature=None, max_tokens=service.OUTPUT))
    for case in CASES[:2]:
        with response(supervisor, identities, deadline, case, key, body, report) as (observed, _, _):
            raw = observed.read(4097)
            require(len(raw) <= 4096 and observed.status == 401
                and service.correctness.strict_json(raw) == {"error": {
                    "message": "invalid_api_key", "type": "invalid_api_key"}}, "authentication")
        report["checks"][case] = True
    with response(supervisor, identities, deadline, "catalog", key, b"", report) as (observed, _, _):
        raw = observed.read(65537)
        require(observed.status == 200 and len(raw) <= 65536, "protocol")
        evidence = validate_catalog(checker, raw, supervisor, deadline)
    report["checks"]["catalog"] = True
    rows = await checker.streaming.run_streaming_checks(client, service.MODEL, evidence=evidence, clock=time.monotonic)
    require(type(rows) is list and len(rows) == len(STREAM_CHECKS), "protocol")
    projected = {}
    for row in rows:
        require(type(row.id) is str and row.id in STREAM_CHECKS and row.id not in projected
            and type(row.tier) is int and row.tier == 4
            and type(row.status) is str and row.status in ("pass", "fail", "warn", "skip"), "protocol")
        projected[row.id] = row.status
    report["stream_checks"] = projected
    require(all(value == "pass" for value in projected.values()), "completion")
    report["checks"]["stream"] = True


def run_once(supervisor, prior):
    report = {"schema": SCHEMA, "result": "fail", "failure": "internal",
        "contract_sha256": process.digest(encoded(contract())), "prepared_sha256": None,
        "inference_sha256": process.digest(encoded(prior)), "attempts": dict.fromkeys(CASES, False),
        "checks": dict.fromkeys(CASES, False), "stream_checks": None,
        "usage": None, "usage_unknown": False, **dict.fromkeys(inference.CLAIMS, False)}
    try:
        require(threading.current_thread() is threading.main_thread()
            and type(supervisor) is service.Supervisor and supervisor.used is True
            and type(supervisor.owner) is process.ProcessOwner
            and type(supervisor.owner.prepared) is process.Prepared, "contract")
        require(not getattr(supervisor, "_selected_conformance_used", False), "already-used")
        supervisor._selected_conformance_used = True
        prepared = supervisor.owner.prepared
        inference.validate_report(encoded(prior), prepared.receipt_sha256)
        require(prior["result"] == "pass" and getattr(supervisor, "_selected_inference_used", False)
            and set(supervisor.children) == set(service.ROLES)
            and all(supervisor.children[role] is supervisor.owner.processes[role] for role in service.ROLES),
            "not-ready")
        report["prepared_sha256"] = prepared.receipt_sha256
        identities = {role: dict(supervisor.owner.identities[role]) for role in service.ROLES}
        deadline = min(time.monotonic() + SECONDS, supervisor.cutoff() - service.CLEANUP_RESERVE)
        with inference.cancellation(supervisor.shutdown), service.identity.worker.alarm(deadline):
            inference.checkpoint(supervisor, identities, deadline)
            require("checker/trial.json" in prepared.files
                    and prepared.files["scripts/selected_service_conformance.py"] ==
                    process.digest(process.bootstrap.read_source(__file__)), "identity")
            key = service.identity.private_read(supervisor.inputs.root / "api-key", deadline)
            service.pilot.validate_api_key(key)
            with quiet_checker(), checker_modules(process.ROOT / "checker", deadline) as checker:
                asyncio.run(collect(checker, supervisor, identities, deadline, key.removesuffix(b"\n"), report))
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
        "prepared_sha256", "inference_sha256", "attempts", "checks", "stream_checks", "usage", "usage_unknown",
        *inference.CLAIMS} and row["schema"] == SCHEMA and row["result"] in ("pass", "fail")
        and type(row["failure"]) is str and row["failure"] in inference.FAILURES
        and (row["failure"] == "none") == (row["result"] == "pass")
        and row["contract_sha256"] == process.digest(encoded(contract()))
        and row["prepared_sha256"] == prepared_sha256 and row["inference_sha256"] == process.digest(encoded(prior))
        and all(row[key] is False for key in inference.CLAIMS) and type(row["usage_unknown"]) is bool, "protocol")
    for name in ("attempts", "checks"):
        require(type(row[name]) is dict and set(row[name]) == set(CASES)
            and all(type(value) is bool for value in row[name].values()), "protocol")
    for index, case in enumerate(CASES):
        require(not row["checks"][case] or row["attempts"][case], "protocol")
        require(not row["attempts"][case] or all(row["checks"][prior] for prior in CASES[:index]), "protocol")
    usage = row["usage"]
    require(usage is None or type(usage) is dict and set(usage) == {"prompt_tokens", "completion_tokens"}
        and type(usage["prompt_tokens"]) is int and 0 <= usage["prompt_tokens"] <= 4096
        and type(usage["completion_tokens"]) is int and 0 <= usage["completion_tokens"] <= service.OUTPUT, "protocol")
    require((usage is None or row["attempts"]["stream"])
        and row["usage_unknown"] == (row["attempts"]["stream"] and usage is None), "protocol")
    checks = row["stream_checks"]
    require(checks is None or type(checks) is dict and set(checks) == set(STREAM_CHECKS)
        and row["attempts"]["stream"] and all(type(value) is str and value in ("pass", "fail", "warn", "skip")
                                              for value in checks.values()), "protocol")
    require(not row["checks"]["stream"] or usage is not None and all(value > 0 for value in usage.values())
        and checks is not None
        and all(value == "pass" for value in checks.values()), "protocol")
    require(row["result"] != "pass" or all(row["checks"].values()), "protocol")
    return row
