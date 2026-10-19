#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Container-only synthetic checks. Never print process logs or response bodies."""

import argparse
import base64
from contextlib import ExitStack, closing
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
from threading import Thread
import time


ROOT = Path(__file__).resolve().parent
BIN = ROOT / "bin"
CANARY = "NIB_E2E_PRIVATE_INPUT_81f657"
REQUIRED = {
    "catalog.native-model-discovery", "callability.advertised-models",
    "chat.non-empty", "chat.pong", "chat.usage", "chat.model",
    "chat.finish-reason", "chat.temperature-zero", "chat.forwarded-fields",
    "chat.max-token-spelling", "stream.response-status", "stream.sse-framing",
    "stream.error-signaling", "stream.first-delta", "stream.done", "stream.usage",
    "stream.incremental-delivery",
}
MUTATIONS = (
    "cpu-binding", "cpu-nonce", "cpu-measurement", "cpu-rtmr0", "cpu-rtmr1", "cpu-rtmr2",
    "cpu-debug", "cpu-migratable", "cpu-truncated", "gpu-missing", "gpu-duplicate",
    "gpu-nonce", "gpu-identity", "gpu-kind",
)


def require(condition, check):
    if not condition:
        raise CheckFailure(check)


class CheckFailure(Exception):
    pass


def clean_env(**extra):
    # Allow-list, not a credential-name deny-list. No inherited API/proxy env.
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp",
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", **extra}


def request(port, path, *, body=None, headers=None, tls=None):
    cls = http.client.HTTPSConnection if tls else http.client.HTTPConnection
    kwargs = {"context": tls} if tls else {}
    with closing(cls("127.0.0.1", port, timeout=3, **kwargs)) as conn:
        conn.request("POST" if body is not None else "GET", path, body, headers or {})
        response = conn.getresponse()
        data = response.read((1 << 20) + 1)
        require(len(data) <= 1 << 20, "bounded-response")
        return response.status, dict(response.getheaders()), data


def wait_ready(process, port, path, tls=None):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        require(process.poll() is None, "service-startup")
        try:
            if request(port, path, tls=tls)[0] == 200:
                return
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(0.05)
    raise CheckFailure("service-readiness-timeout")


class Processes:
    def __init__(self, stack):
        self.stack = stack
        self.logs = []
        self.processes = []

    def start(self, args, **env):
        # Anonymous logs remain private and bounded by the fixture run deadline.
        log = self.stack.enter_context(tempfile.TemporaryFile())
        process = subprocess.Popen([str(a) for a in args], env=clean_env(**env),
                                   stdout=log, stderr=log, cwd="/tmp")
        self.logs.append(log)
        self.processes.append(process)
        self.stack.callback(self.stop, process)
        return process

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def privacy(self):
        for log in self.logs:
            log.seek(0)
            data = log.read((1 << 20) + 1)
            require(len(data) <= 1 << 20 and CANARY.encode() not in data,
                    "no-private-input-in-service-logs")


def provider_check(base, *, catalog=None, ca=None, tier=4, reject_catalog=False):
    command = ["trustedrouter-provider-check", "--base-url", base + "/v1",
               "--model", "nibfake-model", "--tier", str(tier), "--json", "-"]
    if catalog:
        command += ["--catalog-url", catalog]
    result = subprocess.run(command, env=clean_env(**({"SSL_CERT_FILE": str(ca)} if ca else {})),
                            capture_output=True, timeout=60)
    require(len(result.stdout) <= 1 << 20, "bounded-provider-report")
    report = json.loads(result.stdout)
    checks = {item["id"]: item["status"] for item in report["checks"]}
    # Persist only IDs/status, never response excerpts or error messages.
    require(all(re.fullmatch(r"[a-z0-9.-]{1,100}", k) and v in
                ("pass", "warn", "skip", "fail") for k, v in checks.items()), "provider-report-shape")
    require(report["suite_version"] == "0.1.8", "provider-version")
    require(report["target"]["authentication"]["authorization_header_sent"] is False,
            "provider-no-authentication")
    if reject_catalog:
        require(result.returncode == 1 and checks.get("catalog.declared-v2") == "fail",
                "invalid-catalog-rejected")
    else:
        require(result.returncode == 0, "provider-exit-zero")
        required = REQUIRED if tier == 4 else {"catalog.native-model-discovery"}
        if catalog:
            required = required | {"catalog.declared-v2"}
        require(all(checks.get(k) == "pass" for k in required), "required-provider-checks-pass")
        require("fail" not in checks.values(), "provider-no-failures")
    if catalog:
        declaration = next(c for c in report["checks"] if c["id"] == "catalog.declared-v2")
        require(declaration["measured"]["schema_source"] == "vendored-offline-fallback",
                "offline-catalog-schema")
    return {"exit_code": result.returncode, "checks": checks}


def privacy_requests(port, tls=None):
    headers = {"Content-Type": "application/json", "X-Private-Test": CANARY}
    payload = json.dumps({"model": "nibfake-model", "messages": [{"role": "user", "content": CANARY}]}).encode()
    status, _, data = request(port, "/v1/chat/completions", body=payload, headers=headers, tls=tls)
    require(status == 200 and json.loads(data)["choices"][0]["message"]["content"] == "PONG",
            "fixed-pong-no-input-echo")
    request(port, "/v1/" + CANARY + "?private=" + CANARY, headers=headers, tls=tls)
    request(port, "/v1/chat/completions", body=CANARY.encode(), headers=headers, tls=tls)


def upstream_unavailable(port, tls=None):
    body = json.dumps({"model": "nibfake-unavailable", "stream": True,
                       "messages": [{"role": "user", "content": CANARY}]}).encode()
    status, headers, data = request(port, "/v1/chat/completions", body=body,
                                    headers={"Content-Type": "application/json"}, tls=tls)
    require(status == 503 and headers.get("Retry-After") == "7" and b"data:" not in data,
            "upstream-503-retry-after")


def isolation():
    require(os.getuid() == 65532, "nonroot-runtime")
    require({name for _, name in socket.if_nameindex()} == {"lo"}, "network-none-runtime")
    status = Path("/proc/self/status").read_text()
    require(re.search(r"^CapEff:\s+0+$", status, re.M) is not None, "no-capabilities")
    require(re.search(r"^NoNewPrivs:\s+1$", status, re.M) is not None, "no-new-privileges")


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True) + "\n")


def policies(temp):
    release = json.loads((ROOT / "fixtures/schema/release.json").read_bytes())
    profile = json.loads((ROOT / "fixtures/schema/hardware-profile.json").read_bytes())
    profile["pci_devices"].append({"address": "0000:03:00.0", "kind": "nvswitch"})
    write_json(temp / "release.json", release)
    write_json(temp / "profile.json", profile)
    args = []
    for name in ("release", "profile"):
        path = temp / (name + ".json")
        args += ["--" + name, str(path), "--" + name + "-sha256", hashlib.sha256(path.read_bytes()).hexdigest()]
    return args


def catalog_server(stack, temp):
    root = temp / "catalog"
    root.mkdir()
    pack = json.loads((ROOT / "fixtures/schema/model-pack.json").read_bytes())
    pack["tr_model_id"] = "nibfake/nibfake-model"
    pack["served_model_name"] = "nibfake-model"
    pack["engine_profiles"]["b200x2-tdx"]["max_model_len"] = 4096
    pack_dir = root / "models/nibfake-model/1"
    pack_dir.mkdir(parents=True)
    write_json(pack_dir / "pack.yaml", pack)
    shutil.copyfile(ROOT / "fixtures/schema/files.sha256", pack_dir / "files.sha256")
    write_json(root / "pricing.json", {
        "schema": "nib.catalog-pricing/v1", "purpose": "synthetic-local-test",
        "models": [{"pack": "models/nibfake-model/1/pack.yaml", "profile": "b200x2-tdx", "max_output_tokens": 16,
                    "pricing": {"currency": "USD", "unit": "per_1m_tokens", "input": "0.000001",
                                "output": "0.000002", "cached_input": None, "cache_write": None,
                                "minimum_request": "0"}}],
    })
    generated = subprocess.run([str(BIN / "nib-release"), "catalog", "--root", str(root), "--pricing", "pricing.json"],
                               env=clean_env(), capture_output=True, timeout=15)
    require(generated.returncode == 0 and len(generated.stdout) <= 1 << 20, "catalog-generation")
    invalid = json.loads(generated.stdout)
    invalid["data"][0]["pricing"]["input"] = "invalid"
    routes = {"/catalog.v2.json": generated.stdout, "/invalid-catalog.v2.json": json.dumps(invalid).encode()}
    violations = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.headers.get("Authorization"):
                violations.append("authorization")
            body = routes.get(self.path, b"")
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 18002), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop():
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        require(not thread.is_alive() and not violations, "catalog-shutdown-no-auth")

    stack.callback(stop)
    return "http://127.0.0.1:18002"


def bootstrap_test_cert(process, temp):
    # Only the disposable, network-none fixture can use this trust bootstrap.
    # It is not production authentication. nib verify independently checks its
    # own connection's key binding and does not consume this certificate file.
    untrusted = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    untrusted.check_hostname = False
    untrusted.verify_mode = ssl.CERT_NONE
    wait_ready(process, 18443, "/healthz", untrusted)
    with socket.create_connection(("127.0.0.1", 18443), timeout=3) as raw:
        with untrusted.wrap_socket(raw, server_hostname="127.0.0.1") as conn:
            cert = ssl.DER_cert_to_PEM_cert(conn.getpeercert(binary_form=True))
    ca = temp / "fixture-ca.pem"
    ca.write_text(cert)
    return ca, ssl.create_default_context(cafile=str(ca))


def verify(args, *, success=False, release=False):
    binary = BIN / ("nib-release" if release else "nib-fake")
    result = subprocess.run([str(binary), "verify", "--endpoint", "https://127.0.0.1:18443", *args],
                            env=clean_env(), capture_output=True, timeout=35)
    require(len(result.stdout) <= 8192, "bounded-verifier-result")
    # A negative test must reach appraisal, not pass because the process failed
    # to start, parse policy or connect. Fetch errors legitimately have no JSON
    # only for malformed GPU helper output, which the shim rejects itself.
    require(bool(result.stdout), "verifier-reached-appraisal")
    document = json.loads(result.stdout)
    require(all(document.get(field) is False for field in ("cpu_verified", "gpu_verified", "confidential")),
            "no-hardware-claims")
    require((result.returncode == 0) == success, "verifier-exit")
    require(document.get("fake_checks_passed") is success, "verifier-fake-result")
    require(document.get("status") == ("fake_pass" if success else "fail"), "verifier-status")
    return {"exit_code": result.returncode, "status": document["status"], "confidential": False}


def admission(tls):
    # Hold a request body open, so release of the sole admission slot cannot
    # race with the overload probe. No timing assumption about SSE is needed.
    with closing(http.client.HTTPSConnection("127.0.0.1", 18443, context=tls, timeout=3)) as held:
        held.putrequest("POST", "/v1/chat/completions")
        held.putheader("Content-Length", "65536")
        held.putheader("Content-Type", "application/json")
        held.endheaders()
        held.send(b"{")
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            status, headers, _ = request(18443, "/v1/models", tls=tls)
            if status == 429:
                require(headers.get("Retry-After") == "1" and bool(headers.get("X-Request-Id")),
                        "admission-headers")
                with socket.create_connection(("127.0.0.1", 18443), timeout=1) as raw:
                    with tls.wrap_socket(raw, server_hostname="127.0.0.1") as rejected:
                        slow_rejection(rejected)
                return
            time.sleep(0.01)
        raise CheckFailure("admission-429")


def slow_rejection(conn):
    # Withhold the entire declared body. Assert actual EOF as well as the
    # Connection header, which alone did not catch the upstream drain hang.
    conn.sendall(b"POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 1\r\n\r\n")
    with http.client.HTTPResponse(conn) as response:
        response.begin()
        require(response.status == 429 and response.getheader("Retry-After") == "1",
                "slow-admission-429")
        require(response.getheader("Connection", "").lower() == "close", "slow-admission-close-header")
        require(re.fullmatch(r"[0-9a-f]{32}", response.getheader("X-Request-Id", "")) is not None,
                "slow-admission-request-id")
        require(len(response.read(4097)) <= 4096, "bounded-admission-response")
        require(conn.recv(1) == b"", "slow-admission-closed-socket")


def request_ids(tls):
    ids = set()
    for path in ("/v1/models", "/healthz?private=" + CANARY):
        status, headers, data = request(18443, path, headers={"X-Request-Id": CANARY}, tls=tls)
        value = headers.get("X-Request-Id", "")
        require(status == 200 and re.fullmatch(r"[0-9a-f]{32}", value) is not None and value not in ids,
                "proxy-owned-request-ids")
        ids.add(value)
        if path.startswith("/healthz"):
            require(data == b"", "health-reveals-no-request-data")


def full_node(stack, temp, processes, summary):
    state = temp / "collector-state"
    state.write_text("valid")
    aa = processes.start([BIN / "fake-agent", "aa", "--listen", "127.0.0.1:18001", "--state", state])
    query = base64.urlsafe_b64encode(bytes(64)).decode().rstrip("=")
    wait_ready(aa, 18001, "/aa/evidence?encoding=base64&runtime_data=" + query)
    shim = processes.start([
        BIN / "trcs", "shim", "--listen", "127.0.0.1:18443", "--upstream", "http://127.0.0.1:18000",
        "--tee", "tdx", "--evidence-source", "coco-aa", "--coco-aa-url", "http://127.0.0.1:18001",
        "--listener-audit", "enforce", "--require-tee", "--tls-san", "127.0.0.1",
        "--attestation-rate", "100", "--max-inflight", "1",
        "--gpu-evidence-cmd", str(BIN / "fake-agent") + " gpu --state " + str(state),
    ])
    ca, tls = bootstrap_test_cert(shim, temp)
    catalog = catalog_server(stack, temp)
    summary["provider"] = provider_check("https://127.0.0.1:18443", catalog=catalog + "/catalog.v2.json", ca=ca)
    summary["catalog_negative"] = provider_check("https://127.0.0.1:18443", catalog=catalog + "/invalid-catalog.v2.json",
                                                  ca=ca, tier=1, reject_catalog=True)
    args = policies(temp)
    summary["fake_verifier"] = verify(args, success=True)
    summary["release_verifier"] = verify(args, release=True)
    mutations = {}
    for mode in MUTATIONS:
        state.write_text(mode)
        mutations[mode] = verify(args)
    state.write_text("valid")
    # Recovery proves negatives were not a stopped service or exhausted limiter.
    verify(args, success=True)
    summary["mutations"] = mutations
    privacy_requests(18443, tls)
    upstream_unavailable(18443, tls)
    request_ids(tls)
    admission(tls)
    summary["admission"] = "pass"
    return tls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-only", action="store_true")
    args = parser.parse_args()
    summary = {"execution": "fake", "hardware_validated": False, "confidential": False,
               "scope": "engine-only" if args.engine_only else "full-fake-node", "result": "fail"}
    try:
        isolation()
        with ExitStack() as stack:
            temp = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            fixture = temp / "guest"
            shutil.copytree(ROOT / "fixtures/guest", fixture)
            processes = Processes(stack)
            engine = processes.start([BIN / "nib-engine", "--config", fixture / "guest.json"])
            wait_ready(engine, 18000, "/health")
            if args.engine_only:
                catalog = catalog_server(stack, temp)
                summary["provider"] = provider_check("http://127.0.0.1:18000", catalog=catalog + "/catalog.v2.json")
                summary["catalog_negative"] = provider_check("http://127.0.0.1:18000", catalog=catalog + "/invalid-catalog.v2.json",
                                                             tier=1, reject_catalog=True)
                privacy_requests(18000)
                upstream_unavailable(18000)
            else:
                tls = full_node(stack, temp, processes, summary)
            (fixture / "weights.txt").write_bytes(b"corrupted!")
            engine.wait(timeout=5)
            require(engine.returncode != 0, "weight-gate-fail-closed")
            if not args.engine_only:
                require(request(18443, "/healthz", tls=tls)[0] == 503, "unhealthy-node-not-ready")
            for process in reversed(processes.processes):
                processes.stop(process)
            processes.privacy()
            summary["privacy"] = "pass"
            summary["weight_gate"] = "pass"
        summary["result"] = "pass"
    except CheckFailure as error:
        # These identifiers are code constants, never user-supplied text.
        summary["failed_check"] = str(error)
    except Exception as error:
        summary["failed_check"] = "runner-exception"
        summary["exception_type"] = type(error).__name__
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
