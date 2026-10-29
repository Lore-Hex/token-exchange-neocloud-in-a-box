#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""One externally observed, system-root TLS checker run; no owner or deployment proof."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        sys.exit("Use python3 -I -S -B scripts/check-selected-service.py")
    sys.path.insert(0, str(ROOT / "scripts"))

import asyncio
from contextlib import ExitStack, asynccontextmanager, contextmanager
from datetime import datetime, timezone
import http.client
import importlib.util
import re
import ssl
import threading
import time
from types import SimpleNamespace

import selected_service_protocol as protocol
import external_checker_payload

_spec = importlib.util.spec_from_file_location("external_conformance_probe", ROOT / "scripts/probe-selected-service.py")
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)
subset, service, inference = protocol.subset, protocol.service, protocol.inference
require, encoded, digest = inference.require, inference.encoded, protocol.process.digest
SCHEMA, INPUT = "nib.selected-external-conformance/v1", "nib.selected-external-conformance-input/v1"
ORIGINAL_SCHEMA, ORIGINAL_INPUT = "nib.selected-external-conformance/v2", "nib.selected-external-conformance-input/v2"
CASES = ("health", "missing-key", "wrong-key", "catalog", *protocol.JSON_CASES, "stream")
GENERATIONS = (*protocol.JSON_CASES, "stream")
SECONDS, JSON_LIMIT = 480, 65536


def sha(value, size=64):
    require(type(value) is str and re.fullmatch("[0-9a-f]{" + str(size) + "}", value), "contract")
    return value


def timestamp(value):
    require(type(value) is str, "contract")
    result = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    require(result.strftime("%Y-%m-%dT%H:%M:%SZ") == value, "contract")
    return result.timestamp()


def source_identity(deadline):
    # Pilot's module factory does not register these three imports in sys.modules.
    names = {"scripts/check-selected-service.py", "scripts/pilot/run.py",
             "scripts/e2e/snapshot.py", "scripts/e2e/exercise.py"}
    for module in tuple(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if type(path) is str and Path(path).is_relative_to(ROOT / "scripts"):
            require(Path(path).suffix == ".py", "identity")
            names.add(Path(path).relative_to(ROOT).as_posix())
    require(len(names) <= 128, "identity")
    return probe.source_identity(deadline, extra_files=tuple(sorted(names)))


def candidate_for(value):
    require(type(value) is dict and value.get("schema") in (INPUT, ORIGINAL_INPUT), "contract")
    return "original" if value["schema"] == ORIGINAL_INPUT else "selected"


def admit(value):
    fields = external_checker_payload.candidate_fields(candidate_for(value))
    require(type(value) is dict and set(value) == {"schema", "runner_git_sha", "service_git_sha",
        "preparation_sha256", "checker_payload_sha256", "target", "catalog", "deadline", "admission_stop_at", *fields}
        and encoded({key: value[key] for key in fields}) == encoded(fields), "contract")
    for field, length in (("runner_git_sha", 40), ("service_git_sha", 40),
                          ("preparation_sha256", 64), ("checker_payload_sha256", 64)):
        sha(value[field], length)
    require(type(value["target"]) is dict and set(value["target"]) == {"address", "hostname", "port"}, "contract")
    target = SimpleNamespace(**value["target"], tls_mode="system-roots", certificate=None, certificate_sha256=None)
    probe.validate_target(target)
    wall, admission = timestamp(value["deadline"]), timestamp(value["admission_stop_at"])
    require(time.time() < wall <= admission and wall - time.time() <= SECONDS, "deadline")
    return target, wall


def catalog(checker, raw, expected, *, candidate="selected"):
    model = protocol.checker_model(candidate)
    if candidate == "original":
        payload = service.correctness.strict_json(raw)
        schema_raw = external_checker_payload.runtime.read_file(
            ROOT / external_checker_payload.CATALOG_SOURCE, 1 << 20, time.monotonic() + 5)
        require(digest(schema_raw) == external_checker_payload.CATALOG_SHA, "identity")
        checker.catalog.Draft202012Validator(service.correctness.strict_json(schema_raw),
            format_checker=checker.catalog.FormatChecker()).validate(payload)
        checker.catalog._validate_vendored_rules(payload)
    else:
        payload = subset.catalog_payload(checker, raw)
    require(encoded(payload) == encoded(expected), "identity")
    require(len(payload["data"]) == 1, "identity")
    row = payload["data"][0]
    limits = service.engine_entry.execution_limits(service.engine_entry.ORIGINAL_MILLION_PROFILE
        if candidate == "original" else service.engine_entry.EAGER_PROFILE)
    require(row["id"] == model and row["context_length"] == limits["context_tokens"]
        and row["max_output_tokens"] == limits["max_output_tokens"] and row["lifecycle"]["status"] == "active"
        and row["capabilities"] == {"streaming": True, "tools": candidate == "original", "structured_output": False,
                                   "reasoning": False, "prompt_caching": candidate == "original"}, "contract")
    if candidate == "original":
        require(type(row["context_length"]) is int and type(row["max_output_tokens"]) is int
            and payload["contract_version"] == "2.1" and row["owned_by"] == model.split("/", 1)[0]
            and row["reliability"] == {"first_token_timeout_seconds": 30,
                "completion_timeout_seconds": service.request_seconds(service.engine_entry.ORIGINAL_MILLION_PROFILE),
                "stream_idle_timeout_seconds": 30, "capacity_scope": "model"}, "contract")
    evidence = checker.catalog.CatalogEvidence()
    evidence.declared_models.append(row)
    return evidence


class Boundary(BaseException):
    """Fatal transport/admission failures must bypass upstream exception recovery."""
    def __init__(self, error):
        self.category = (error.category if isinstance(error, (Boundary, inference.Rejected)) else
            "deadline" if isinstance(error, service.identity.worker.Abort) else
            "cancelled" if isinstance(error, KeyboardInterrupt) else
            "transport" if isinstance(error, (ssl.SSLError, OSError, http.client.HTTPException)) else "protocol")
        super().__init__("external checker boundary rejected")


def empty_result(candidate="selected"):
    fields = external_checker_payload.candidate_fields(candidate)
    if candidate == "original":
        fields["catalog_schema_sha256"] = external_checker_payload.CATALOG_SHA
    return {"schema": ORIGINAL_SCHEMA if candidate == "original" else SCHEMA, **fields,
        "result": "fail", "failure": "contract", "started_at": probe.now(), "ended_at": None,
        "git_sha": None, "source_sha256": {}, "input_sha256": None, "target_sha256": None,
        "expected_service_git_sha": None, "expected_preparation_sha256": None, "checker_payload_sha256": None,
        "catalog_sha256": None, "provider_sha256": None, "deadline": None, "admission_stop_at": None,
        "tls_mode": "system-roots", "checker_wheel_sha256": subset.pinned.CHECKER_SHA,
        "requirements_sha256": subset.pinned.LOCK_SHA, "upstream_contract": subset.pinned.CONTRACT,
        "attempts": dict.fromkeys(CASES, False), "tls_checks": dict.fromkeys(CASES, False),
        "prechecks": dict.fromkeys(CASES[:4], False), "checks": None, "checker_result": "incomplete",
        "all_17_pass": False, "declared_v2_upstream_status": None,
        "usage": {case: {"usage": None, "usage_unknown": False} for case in GENERATIONS},
        "max_requests": 13, "max_authenticated_requests": 9, "max_output_tokens": service.OUTPUT,
        "max_seconds": SECONDS, "retries": 0, "redirects": 0, "client_connections_closed": True,
        **dict.fromkeys(("ownership_verified", "runtime_identity_verified", "continued_readiness",
            "auth_upstream_calls_measured", "quality_eligible", "deployment_eligible", "confidential", "e2ee"), False)}


class Client:
    provider = None

    def __init__(self, checker, target, trust, value, args, cutoff, report):
        self.checker, self.target, self.trust, self.value = checker, target, trust, value
        self.args, self.cutoff, self.report, self.index = args, cutoff, report, 0
        self.candidate = candidate_for(value)
        self.model = protocol.checker_model(self.candidate)
        self.evidence = None

    def checkpoint(self, deadline):
        deadline = min(deadline, self.cutoff())
        require(time.monotonic() < deadline, "deadline")
        return deadline

    def request(self, method, path):
        return self.checker.httpx.Request(method, "https://" + self.target.hostname + ":" + str(self.target.port) + path)

    @contextmanager
    def response(self, case, body=b""):
        report, target = self.report, self.target
        stack, entered = ExitStack(), False
        try:
            require(case in CASES and not report["attempts"][case]
                    and sum(report["attempts"].values()) < 13, "already-used")
            deadline = min(self.cutoff(), time.monotonic() +
                (inference.REQUEST_SECONDS if case in GENERATIONS else probe.CONNECT_SECONDS))
            stack.enter_context(service.identity.worker.alarm(deadline))
            report["client_connections_closed"] = False
            connection = stack.enter_context(probe.pinned_tls(target, self.trust, None, deadline))
            entered = True
            report["tls_checks"][case] = True
            key = b""
            if case in GENERATIONS:
                key = service.identity.private_read(self.args.api_key_file, self.checkpoint(deadline))
                service.pilot.validate_api_key(key)
                key = key.removesuffix(b"\n")
            connection.settimeout(probe.remaining(self.checkpoint(deadline)))
            if case == "health":
                probe.health(connection, target, deadline, report)
                yield None, connection, deadline
                return
            method, path = ("GET", "/v1/models") if case == "catalog" else ("POST", "/v1/chat/completions")
            header = (method + " " + path + " HTTP/1.1\r\nHost: " + target.hostname + ":" + str(target.port)
                + "\r\nConnection: close\r\nAccept-Encoding: identity\r\nContent-Type: application/json"
                + "\r\nAccept: " + ("text/event-stream" if case == "stream" else "application/json")
                + "\r\nContent-Length: " + str(len(body)) + "\r\n").encode("ascii")
            if key or case == "wrong-key":
                header += b"Authorization: Bearer " + (key or b"invalid-synthetic-key") + b"\r\n"
            report["attempts"][case] = True
            if case in GENERATIONS:
                report["usage"][case]["usage_unknown"] = True
            connection.sendall(header + b"\r\n" + body)
            observed = stack.enter_context(probe.transport.StreamResponse(connection))
            observed.begin()
            require(not 300 <= observed.status < 400, "protocol")
            yield observed, connection, deadline
            self.checkpoint(deadline)
        except BaseException as error:
            raise Boundary(error) from None
        finally:
            try:
                stack.close()
                if entered:
                    report["client_connections_closed"] = True
            except BaseException as error:
                report["client_connections_closed"] = False
                raise Boundary(error) from None

    def prechecks(self):
        body = probe.body()
        if self.candidate == "original":
            body = encoded({**service.correctness.strict_json(body), "model": self.model})
        for case in CASES[:3]:
            with self.response(case, body if case != "health" else b"") as (observed, _, _):
                if case != "health":
                    raw = probe.fixed_body(observed, status=401, limit=4096, category="authentication", media="application/json")
                    require(service.correctness.strict_json(raw) == {"error": {
                        "message": "invalid_api_key", "type": "invalid_api_key"}}, "authentication")
                self.report["prechecks"][case] = True

    async def models(self):
        with self.response("catalog") as (observed, _, _):
            raw = probe.fixed_body(observed, status=200, limit=JSON_LIMIT, category="protocol", media="application/json")
            self.evidence = catalog(self.checker, raw, self.value["catalog"], candidate=self.candidate)
            self.report["prechecks"]["catalog"] = True
            return self.checker.httpx.Response(200, content=raw, request=self.request("GET", "/v1/models"))

    async def chat(self, *, model, prompt, temperature=0, max_tokens=None, max_tokens_key=None, extra=None):
        case, body, maximum = protocol.json_request(self.checker, self, self.index, model=model, prompt=prompt,
            temperature=temperature, max_tokens=max_tokens, max_tokens_key=max_tokens_key, extra=extra,
            candidate=self.candidate)
        self.index += 1
        with self.response(case, body) as (observed, _, _):
            raw = probe.fixed_payload(observed, status=observed.status, limit=JSON_LIMIT,
                category="protocol", media="application/json")
            if observed.status == 200:
                usage = protocol.usage(raw, maximum, candidate=self.candidate)
                if usage is not None:
                    self.report["usage"][case].update(usage=usage, usage_unknown=False)
            probe.end_of_body(observed, "protocol")
            return self.checker.httpx.Response(observed.status, content=raw,
                request=self.request("POST", "/v1/chat/completions"))

    @asynccontextmanager
    async def stream_chat(self, *, model, prompt, temperature):
        require(model == self.model and prompt == self.checker.rules.PONG_PROMPT and temperature is None, "contract")
        body = encoded(self.checker.client.GatewayClient.chat_body(self, model=model, prompt=prompt,
            temperature=None, max_tokens=service.OUTPUT))
        started = time.monotonic()
        with self.response("stream", body) as (observed, connection, deadline):
            async with subset.measured_response(self.checker, observed, connection, deadline,
                    self.report["usage"]["stream"], started, self.checkpoint,
                    prompt_limit=service.CONTEXT - service.OUTPUT,
                    request_url="https://" + self.target.hostname + "/v1/chat/completions",
                    candidate=self.candidate) as response:
                yield response


async def collect(client):
    rows = await protocol.json_checks(client.checker, client, candidate=client.candidate)
    stream_rows = await client.checker.streaming.run_streaming_checks(client, client.model,
        evidence=client.evidence, clock=time.monotonic)
    protocol.project_checks([*rows, *stream_rows], {}, client.report)
    client.report["declared_v2_upstream_status"] = "skip"


def run(args):
    report = empty_result()
    try:
        require(args.run and threading.current_thread() is threading.main_thread(), "contract")
        with inference.cancellation(threading.Event()), service.identity.worker.alarm(time.monotonic() + SECONDS):
            raw = service.identity.private_read(args.input, time.monotonic() + 5)
            require(len(raw) <= JSON_LIMIT and digest(raw) == sha(args.input_sha256), "identity")
            value = service.correctness.strict_json(raw)
            target, wall = admit(value)
            candidate = candidate_for(value)
            report = empty_result(candidate)
            hard = time.monotonic() + wall - time.time()
            def cutoff():
                nonlocal hard
                hard = min(hard, time.monotonic() + wall - time.time())
                require(time.monotonic() < hard, "deadline")
                return hard
            with service.identity.worker.alarm(cutoff()):
                report["git_sha"], report["source_sha256"] = source_identity(cutoff())
                require(report["git_sha"] == value["runner_git_sha"], "identity")
                report.update(input_sha256=digest(raw), target_sha256=digest(encoded(value["target"])),
                    expected_service_git_sha=value["service_git_sha"], expected_preparation_sha256=value["preparation_sha256"],
                    checker_payload_sha256=value["checker_payload_sha256"], catalog_sha256=digest(encoded(value["catalog"])),
                    provider_sha256=digest(encoded(value["catalog"]["provider"])), deadline=value["deadline"],
                    admission_stop_at=value["admission_stop_at"])
                trust, peer_digest = probe.trust_input(target, cutoff())
                require(peer_digest is None, "identity")
                root = Path(args.checker_payload)
                require(root.is_absolute() and ".." not in root.parts, "contract")
                options = {"candidate": candidate} if candidate == "original" else {}
                with subset.quiet_checker(), external_checker_payload.checker_modules(root, cutoff(),
                        value["checker_payload_sha256"], **options) as checker:
                    catalog(checker, encoded(value["catalog"]), value["catalog"], candidate=candidate)
                    require(value["catalog"]["data"][0]["lifecycle"]["retirement_at"] == value["admission_stop_at"], "identity")
                    client = Client(checker, target, trust, value, args, cutoff, report)
                    client.prechecks()
                    asyncio.run(collect(client))
                    cutoff()
                require(report["all_17_pass"] and all(report["attempts"].values())
                    and all(report["tls_checks"].values()) and all(report["prechecks"].values())
                    and report["client_connections_closed"]
                    and all(row["usage"] is not None and not row["usage_unknown"] for row in report["usage"].values()), "completion")
                report.update(result="pass", failure="none")
    except BaseException as error:
        report.update(result="fail", failure=error.category if isinstance(error, Boundary) else Boundary(error).category)
    report["ended_at"] = probe.now()
    return report


def main(argv=None):
    report = empty_result()
    try:
        parser = probe.QuietParser(description=__doc__, allow_abbrev=False)
        parser.add_argument("--run", action="store_true")
        for name in ("input", "input-sha256", "checker-payload", "api-key-file"):
            parser.add_argument("--" + name, required=True)
        report = run(parser.parse_args(argv))
    except BaseException:
        pass
    print(encoded(report).decode())
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
