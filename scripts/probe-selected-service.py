#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""One external TLS smoke with closed trust modes; never an attestation or quality gate."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        sys.exit("Use python3 -I -S -B scripts/probe-selected-service.py")
    sys.path.insert(0, str(ROOT / "scripts"))

import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
import http.client
import ipaddress
import os
import re
import socket
import ssl
import subprocess
import threading
import time

import selected_service_inference as inference
import stream_measurement as measurement
import stream_transport as transport

service = inference.service
require, encoded = inference.require, inference.encoded
CASES = ("health", "missing-key", "wrong-key", "stream")
SECONDS, CONNECT_SECONDS, LIMIT = 150, 5, 1 << 20
SOURCES = ("scripts/probe-selected-service.py", "scripts/selected_service_inference.py",
           "scripts/stream_measurement.py", "scripts/stream_transport.py", "scripts/selected_service.py",
           "scripts/tau_live_identity.py", "scripts/tau_trial_worker.py", "scripts/pilot/serve.py")


def now():
    return datetime.now(timezone.utc).isoformat()


def body():
    return encoded({"model": service.MODEL,
        "messages": [{"role": "user", "content": "Reply with a short greeting."}],
        "max_tokens": service.OUTPUT, "temperature": 0, "stream": True,
        "stream_options": {"include_usage": True}})


def remaining(deadline):
    left = deadline - time.monotonic()
    require(left > 0, "deadline")
    return left


def source_identity(deadline):
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": "/nonexistent",
        "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": ""}
    def git(*args):
        result = subprocess.run(["git", "-C", str(ROOT), *args], env=env, check=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=min(5, remaining(deadline)))
        require(len(result.stdout) <= LIMIT, "identity")
        return result.stdout
    revision = git("rev-parse", "HEAD").decode().strip()
    require(re.fullmatch("[0-9a-f]{40}", revision), "identity")
    hashes = {}
    for name in SOURCES:
        committed = git("show", revision + ":" + name)
        require(committed == (ROOT / name).read_bytes(), "identity")
        hashes[name] = hashlib.sha256(committed).hexdigest()
    return revision, hashes


def validate_target(args):
    address = ipaddress.IPv4Address(args.address)
    require(str(address) == args.address and address.is_global and not address.is_multicast
            and type(args.port) is int and 1 <= args.port <= 65535, "contract")
    require(type(args.hostname) is str and len(args.hostname) <= 253
            and re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", args.hostname), "contract")
    if tls_mode(args) == "pinned":
        require(type(args.certificate) is str and bool(args.certificate)
                and type(args.certificate_sha256) is str
                and re.fullmatch("[0-9a-f]{64}", args.certificate_sha256), "contract")
    else:
        require(args.certificate is None and args.certificate_sha256 is None, "contract")


def tls_mode(args):
    mode = getattr(args, "tls_mode", "pinned")
    require(mode in ("pinned", "system-roots"), "contract")
    return mode


def tls_arguments(parser):
    parser.add_argument("--tls-mode", choices=("pinned", "system-roots"), default="pinned")
    for name in ("certificate", "certificate-sha256"):
        parser.add_argument("--" + name)


def trust_input(args, deadline):
    if tls_mode(args) == "system-roots":
        require(args.certificate is None and args.certificate_sha256 is None, "contract")
        paths = ssl.get_default_verify_paths()
        # OpenSSL honors these even under Python -I. Do not label an injected
        # CA file/directory as installed-system-root validation.
        require(not any(name in os.environ for name in ("SSL_CERT_FILE", "SSL_CERT_DIR",
                paths.openssl_cafile_env, paths.openssl_capath_env)), "contract")
        trust = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        trust.minimum_version = ssl.TLSVersion.TLSv1_2
        trust.load_default_certs(ssl.Purpose.SERVER_AUTH)
        require(trust.check_hostname and trust.verify_mode == ssl.CERT_REQUIRED, "identity")
        return trust, None
    pem = service.identity.private_read(args.certificate, deadline)
    require(len(pem) <= 65536 and hashlib.sha256(pem).hexdigest() == args.certificate_sha256
            and pem.count(b"-----BEGIN CERTIFICATE-----") == 1
            and pem.count(b"-----END CERTIFICATE-----") == 1, "identity")
    text = pem.decode("ascii")
    der = ssl.PEM_cert_to_DER_cert(text)
    trust = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    trust.minimum_version = ssl.TLSVersion.TLSv1_2
    trust.load_verify_locations(cadata=text)
    require(trust.check_hostname and trust.verify_mode == ssl.CERT_REQUIRED, "identity")
    return trust, hashlib.sha256(der).digest()


@contextmanager
def pinned_tls(args, trust, peer_digest, deadline):
    mode = tls_mode(args)
    require(trust.check_hostname is True and trust.verify_mode == ssl.CERT_REQUIRED
            and trust.minimum_version >= ssl.TLSVersion.TLSv1_2
            and trust.keylog_filename is None, "identity")
    require(peer_digest is None if mode == "system-roots" else
            type(peer_digest) is bytes and len(peer_digest) == 32, "identity")
    with ExitStack() as stack:
        raw = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
        raw.set_inheritable(False)
        raw.settimeout(min(CONNECT_SECONDS, remaining(deadline)))
        # A numeric IPv4 target avoids DNS changes and proxy/environment routing.
        raw.connect((args.address, args.port))
        connection = stack.enter_context(trust.wrap_socket(raw, server_hostname=args.hostname))
        if mode == "pinned":
            require(hashlib.sha256(connection.getpeercert(binary_form=True)).digest() == peer_digest, "identity")
        connection.settimeout(remaining(deadline))
        yield connection


def fixed_body(response, *, status, limit, category, media=None):
    headers = response.getheaders()
    lengths = response.headers.get_all("Content-Length")
    require(not response.headers.defects and len(headers) <= 32
            and sum(len(key) + len(value) for key, value in headers) <= 65536
            and all("\r" not in value and "\n" not in value for _, value in headers)
            and response.version in (10, 11) and response.status == status
            and lengths is not None and len(lengths) == 1
            and re.fullmatch(r"0|[1-9][0-9]{0,3}", lengths[0]) is not None
            and int(lengths[0]) <= limit
            and response.getheader("Transfer-Encoding") is None
            and response.getheader("Content-Encoding") is None, category)
    if media is not None:
        require(response.headers.get_all("Content-Type") == [media], category)
    raw = response.read(limit + 1)
    require(len(raw) == int(lengths[0]) and response.tail is not None
            and response.tail.read1(1) == b"", category)
    return raw


def health(connection, args, deadline, result):
    result["attempts"]["health"] = True
    connection.settimeout(remaining(deadline))
    connection.sendall(("GET /healthz HTTP/1.1\r\nHost: " + args.hostname + ":" + str(args.port)
        + "\r\nConnection: close\r\nAccept-Encoding: identity\r\n\r\n").encode("ascii"))
    with transport.StreamResponse(connection) as response:
        response.begin()
        fixed_body(response, status=200, limit=0, category="not-ready")


def request(connection, args, case, key, deadline, result):
    if case == "health":
        return health(connection, args, deadline, result)
    payload = body()
    header = ("POST /v1/chat/completions HTTP/1.1\r\nHost: " + args.hostname + ":" + str(args.port)
        + "\r\nConnection: close\r\nAccept-Encoding: identity\r\nAccept: text/event-stream"
        + "\r\nContent-Type: application/json\r\nContent-Length: " + str(len(payload)) + "\r\n").encode("ascii")
    if case != "missing-key":
        header += b"Authorization: Bearer " + (key if case == "stream" else b"invalid-synthetic-key") + b"\r\n"
    result["attempts"][case] = True
    if case == "stream":
        result["usage_unknown"] = True
    started = time.monotonic()
    connection.settimeout(remaining(deadline))
    connection.sendall(header + b"\r\n" + payload)
    with transport.StreamResponse(connection) as response:
        response.begin()
        if case != "stream":
            raw = fixed_body(response, status=401, limit=4096, category="authentication", media="application/json")
            require(service.correctness.strict_json(raw) == {"error": {
                    "message": "invalid_api_key", "type": "invalid_api_key"}}, "authentication")
            return
        length = transport.response_length(response)
        meter = measurement.StreamMeasurement(service.MODEL, timeout_seconds=inference.REQUEST_SECONDS,
            max_prompt_tokens=service.CONTEXT - service.OUTPUT, max_output_tokens=service.OUTPUT)
        count = 0
        while True:
            connection.settimeout(remaining(deadline))
            chunk = response.read1(measurement.MAX_EVENT)
            if not chunk:
                break
            count += len(chunk)
            require(count <= LIMIT, "protocol")
            meter.feed(chunk, time.monotonic() - started)
            if meter.usage is not None:
                result.update(usage={"prompt_tokens": meter.usage[0], "completion_tokens": meter.usage[1]},
                              usage_unknown=False)
            require(meter.failure is None, "protocol")
        require(length is None or count == length, "protocol")
        require(response.tail is not None and response.tail.read1(1) == b"", "protocol")
        sample = meter.close(time.monotonic() - started)
        require(sample.status == "completed" and sample.first_content_seconds is not None
                and sample.prompt_tokens > 0 and sample.completion_tokens > 0, "completion")
        result["measurement"] = sample.metadata()


def empty_result():
    return {"schema": "nib.selected-external-smoke/v1", "result": "fail", "failure": "contract",
        "started_at": now(), "ended_at": None, "git_sha": None, "source_sha256": {},
        "attempts": dict.fromkeys(CASES, False), "checks": dict.fromkeys(CASES, False),
        "usage": None, "usage_unknown": False, "measurement": None,
        "max_authenticated_requests": 1, "retries": 0, "tls_peer_verified": False,
        "tls_checks": dict.fromkeys(CASES, False),
        "auth_upstream_calls_measured": False, "provider_conformance": False,
        "quality_eligible": False, "deployment_eligible": False, "confidential": False, "e2ee": False}


def run(args):
    result = empty_result()
    try:
        require(args.run and threading.current_thread() is threading.main_thread(), "contract")
        validate_target(args)
        if tls_mode(args) == "system-roots":
            result.update(schema="nib.selected-external-smoke/v2", tls_mode="system-roots")
        cutoff = datetime.strptime(args.deadline, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        seconds = cutoff - time.time()
        require(0 < seconds <= SECONDS, "deadline")
        deadline = time.monotonic() + seconds
        shutdown = threading.Event()
        with inference.cancellation(shutdown), service.identity.worker.alarm(deadline):
            result["git_sha"], result["source_sha256"] = source_identity(deadline)
            trust, peer_digest = trust_input(args, deadline)
            for case in CASES:
                limit = inference.REQUEST_SECONDS if case == "stream" else CONNECT_SECONDS
                request_deadline = min(deadline, time.monotonic() + limit)
                with service.identity.worker.alarm(request_deadline), pinned_tls(
                        args, trust, peer_digest, request_deadline) as connection:
                    result["tls_checks"][case] = True
                    result["tls_peer_verified"] = all(result["tls_checks"].values())
                    # Never even read the real credential before authenticating this peer.
                    key = b""
                    if case == "stream":
                        key = service.identity.private_read(args.api_key_file, request_deadline)
                        service.pilot.validate_api_key(key)
                        key = key.removesuffix(b"\n")
                    request(connection, args, case, key, request_deadline, result)
                    result["checks"][case] = True
        result.update(result="pass", failure="none")
    except inference.Rejected as error:
        result["failure"] = error.category
    except service.identity.worker.Abort:
        result["failure"] = "deadline"
    except (ssl.SSLError, OSError, http.client.HTTPException):
        result["failure"] = "transport"
    except KeyboardInterrupt:
        result["failure"] = "cancelled"
    except BaseException:
        result["failure"] = "protocol"
    result["ended_at"] = now()
    return result


class QuietParser(argparse.ArgumentParser):
    def error(self, message):
        raise inference.Rejected("contract")


def main(argv=None):
    result = empty_result()
    try:
        parser = QuietParser(description=__doc__, allow_abbrev=False)
        parser.add_argument("--run", action="store_true")
        for name in ("address", "hostname", "api-key-file", "deadline"):
            parser.add_argument("--" + name, required=True)
        tls_arguments(parser)
        parser.add_argument("--port", type=int, required=True)
        result = run(parser.parse_args(argv))
    except BaseException:
        pass
    print(encoded(result).decode())
    return 0 if result["result"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
