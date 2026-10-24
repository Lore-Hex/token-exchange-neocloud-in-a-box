# SPDX-License-Identifier: Apache-2.0
"""One owned TLS inference smoke; library only until the explicit owner hook."""

from contextlib import ExitStack, contextmanager
import http.client
import json
import re
import signal
import socket
import threading
import time

import selected_service_process as process

service = process.service
CASES = ("missing-key", "wrong-key", "authenticated")
SECONDS, NEGATIVE_SECONDS, REQUEST_SECONDS = 140, 5, 120
FAILURES = frozenset(("none", "contract", "already-used", "not-ready", "cancelled", "deadline",
                     "identity", "authentication", "transport", "protocol", "completion", "internal"))
CLAIMS = ("hardware_validated", "deployable", "provider_conformance", "qualification", "streaming",
          "tools", "reasoning", "zdr", "confidential", "e2ee")
SCHEMA = "nib.selected-service-inference/v1"


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def request_bytes():
    return encoded({"model": service.MODEL, "messages": [{"role": "user", "content": "Reply with a short greeting."}],
                    "max_tokens": service.OUTPUT, "temperature": 0, "stream": False})


def contract():
    return {"schema": "nib.selected-service-inference-contract/v1", "model": service.MODEL,
        "request_sha256": process.digest(request_bytes()), "max_output_tokens": service.OUTPUT,
        "cases": list(CASES), "authenticated_requests": 1, "retries": 0,
        "seconds": SECONDS, "negative_seconds": NEGATIVE_SECONDS, "request_seconds": REQUEST_SECONDS,
        "auth_nonforwarding_basis": "reviewed-guard-source-and-unit-tests"}


class Rejected(ValueError):
    def __init__(self, category):
        self.category = category if type(category) is str and category in FAILURES else "internal"
        super().__init__("selected inference rejected")


def require(condition, category):
    if not condition:
        raise Rejected(category)


@contextmanager
def cancellation(shutdown):
    handlers = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    restoring, cancelled = False, False
    def stopped(*_):
        nonlocal cancelled
        if cancelled or shutdown.is_set():
            return
        cancelled = True  # Latch before entering the event's lock.
        shutdown.set()
        if not restoring:
            raise Rejected("cancelled")
    try:
        for number in handlers:
            signal.signal(number, stopped)
        yield
    finally:
        restoring = True
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, set(handlers))
        try:
            for number, handler in handlers.items():
                signal.signal(number, handler)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)
    # A first signal during restoration still fails success, but cannot replace
    # an exception already unwinding or leave the other helper handler installed.
    if shutdown.is_set():
        raise Rejected("cancelled")


def checkpoint(supervisor, identities, deadline):
    supervisor.checkpoint(deadline)
    owner = supervisor.owner
    for role in service.ROLES:
        require(owner.bound(role, supervisor.children[role], deadline) is not None
                and owner.identities[role] == identities[role], "identity")
    require(supervisor.engine_identity == identities["engine"], "identity")
    supervisor.checkpoint(deadline)
    return owner.identities["tls"]


def exchange(supervisor, identities, case, key, deadline, report):
    owner = supervisor.owner
    deadline = min(deadline, supervisor.cutoff() - service.CLEANUP_RESERVE,
                   time.monotonic() + (REQUEST_SECONDS if case == "authenticated" else NEGATIVE_SECONDS))
    with service.identity.worker.alarm(deadline), ExitStack() as stack:
        bound = checkpoint(supervisor, identities, deadline)
        raw = stack.enter_context(socket.create_connection(("127.0.0.1", process.TLS_PORT),
            timeout=min(NEGATIVE_SECONDS, max(.001, deadline - time.monotonic()))))
        owner.peer("tls", bound, raw, deadline)  # Prove the owned peer before any credential is sent.
        connection = stack.enter_context(owner.trust.wrap_socket(raw, server_hostname=owner.hostname))
        connection.settimeout(max(.001, deadline - time.monotonic()))
        checkpoint(supervisor, identities, deadline)
        body = request_bytes()
        header = ("POST /v1/chat/completions HTTP/1.1\r\nHost: " + owner.hostname
                  + "\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: "
                  + str(len(body)) + "\r\n").encode("ascii")
        if case != "missing-key":
            credential = key if case == "authenticated" else b"invalid-synthetic-key"
            header += b"Authorization: Bearer " + credential + b"\r\n"
        report["attempts"][case] = True
        if case == "authenticated":
            report["usage_unknown"] = True
        connection.sendall(header + b"\r\n" + body)
        response = stack.enter_context(http.client.HTTPResponse(connection))
        response.begin()
        limit = (1 << 20) if case == "authenticated" else 4096
        raw = response.read(limit + 1)
        require(len(raw) <= limit, "protocol")
        if case != "authenticated":
            require(response.status == 401 and service.correctness.strict_json(raw) ==
                {"error": {"message": "invalid_api_key", "type": "invalid_api_key"}}, "authentication")
        else:
            require(response.status == 200, "completion")
            message, finish, usage = service.correctness.decode_completion(raw, service.MODEL, service.OUTPUT)
            # Settlement precedes all content, identity and resource-finalization
            # checks. A later failure must not erase a valid measured response.
            report.update(usage=usage, usage_unknown=False)
            require(type(message.get("content")) is str and bool(message["content"].strip())
                    and message.get("tool_calls") in (None, []) and finish in ("stop", "length"), "completion")


def run_once(supervisor):
    """Called once, in the trusted PID-1 owner/main thread, after all roles ready.

    No CLI, URL, request override, private-input handoff or child process. A
    future explicit hook must fail the lifecycle on a failed result and always
    retain the existing front-first cleanup. This module installs no such hook.
    """
    report = {"schema": SCHEMA, "result": "fail", "failure": "internal",
        "contract_sha256": process.digest(encoded(contract())), "prepared_sha256": None,
        "attempts": dict.fromkeys(CASES, False), "checks": dict.fromkeys(CASES, False),
        "usage": None, "usage_unknown": False, "auth_upstream_calls_measured": False,
        **dict.fromkeys(CLAIMS, False)}
    try:
        require(threading.current_thread() is threading.main_thread()
                and type(supervisor) is service.Supervisor and supervisor.used is True
                and type(supervisor.owner) is process.ProcessOwner, "contract")
        require(not getattr(supervisor, "_selected_inference_used", False), "already-used")
        supervisor._selected_inference_used = True
        owner = supervisor.owner
        require(type(owner.prepared) is process.Prepared and set(supervisor.children) == set(service.ROLES)
                and set(owner.identities) == set(service.ROLES)
                and all(supervisor.children[role] is owner.processes[role] for role in service.ROLES), "not-ready")
        identities = {role: dict(owner.identities[role]) for role in service.ROLES}
        deadline = min(time.monotonic() + SECONDS, supervisor.cutoff() - service.CLEANUP_RESERVE)
        with cancellation(supervisor.shutdown), service.identity.worker.alarm(deadline):
            checkpoint(supervisor, identities, deadline)
            raw = process.bootstrap.read_source(str(process.ROOT / "prepared.json"))
            prepared = service.correctness.strict_json(raw)
            require(prepared["files"] == owner.prepared.files
                    and prepared["lab_sha256"] == owner.prepared.lab_sha
                    and owner.prepared.files["scripts/selected_service_inference.py"] ==
                    process.digest(process.bootstrap.read_source(__file__)), "identity")
            report["prepared_sha256"] = process.digest(raw)
            key = service.identity.private_read(supervisor.inputs.root / "api-key", deadline)
            service.pilot.validate_api_key(key)
            key = key.removesuffix(b"\n")
            for case in CASES:
                exchange(supervisor, identities, case, key, deadline, report)
                report["checks"][case] = True
                checkpoint(supervisor, identities, deadline)
        report.update(result="pass", failure="none")
    except Rejected as error:
        report["failure"] = error.category
    except service.Rejected as error:
        report["failure"] = error.category if error.category in ("deadline", "cancelled") else "identity"
    except service.identity.worker.Abort as error:
        report["failure"] = "deadline" if error.status == "timeout" else "internal"
    except service.correctness.Rejected:
        report["failure"] = "protocol"
    except (OSError, http.client.HTTPException):
        report["failure"] = "transport"
    except KeyboardInterrupt:
        report["failure"] = "cancelled"
    except BaseException:
        report["failure"] = "internal"
    return report


def validate_report(raw, prepared_sha256):
    """Strict content-free projection for the future lifecycle/host receipt join."""
    require(type(raw) is bytes and len(raw) <= 4096, "protocol")
    row = service.correctness.strict_json(raw)
    require(type(prepared_sha256) is str and re.fullmatch("[0-9a-f]{64}", prepared_sha256)
        and type(row) is dict and set(row) == {"schema", "result", "failure", "contract_sha256",
        "prepared_sha256", "attempts", "checks", "usage", "usage_unknown", "auth_upstream_calls_measured", *CLAIMS}
        and row["schema"] == SCHEMA and row["result"] in ("pass", "fail")
        and type(row["failure"]) is str and row["failure"] in FAILURES
        and (row["failure"] == "none") == (row["result"] == "pass")
        and row["contract_sha256"] == process.digest(encoded(contract()))
        and row["prepared_sha256"] == prepared_sha256 and row["auth_upstream_calls_measured"] is False
        and all(row[key] is False for key in CLAIMS) and type(row["usage_unknown"]) is bool, "protocol")
    for name in ("attempts", "checks"):
        require(type(row[name]) is dict and set(row[name]) == set(CASES)
                and all(type(value) is bool for value in row[name].values()), "protocol")
    for index, case in enumerate(CASES):
        require(not row["checks"][case] or row["attempts"][case], "protocol")
        require(not row["attempts"][case] or all(row["checks"][prior] for prior in CASES[:index]), "protocol")
    usage = row["usage"]
    require(usage is None or type(usage) is dict and set(usage) == {"prompt_tokens", "completion_tokens"}
            and type(usage["prompt_tokens"]) is int and 0 < usage["prompt_tokens"] <= 4096
            and type(usage["completion_tokens"]) is int and 0 < usage["completion_tokens"] <= service.OUTPUT,
            "protocol")
    require((usage is None or row["attempts"]["authenticated"])
            and row["usage_unknown"] == (row["attempts"]["authenticated"] and usage is None)
            and (not row["checks"]["authenticated"] or usage is not None)
            and (row["result"] != "pass" or all(row["checks"].values())), "protocol")
    return row


if __name__ == "__main__":
    raise SystemExit("Library only; explicit selected-service validation hook is not installed.")
