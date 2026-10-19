#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Long-running, standard-TLS Qwen pilot. Never a confidential release profile."""

import argparse
from contextlib import ExitStack
import http.client
import json
import os
from pathlib import Path
import re
import signal
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types

ROOT = Path(__file__).resolve().parents[2]
path = ROOT / "scripts/pilot/run.py"
pilot = types.ModuleType("pilot_source")
pilot.__file__ = str(path)
exec(compile(path.read_bytes(), str(path), "exec"), pilot.__dict__)


def private_input(source, target, *, secret, limit):
    """Snapshot deployment files; reject symlinks, special files and public secrets."""
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        pilot.require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= limit,
                      "deployment-file")
        if secret:
            pilot.require(info.st_mode & 0o077 == 0, "deployment-file-permissions")
        data = stream.read(limit + 1)
        pilot.require(0 < len(data) <= limit, "deployment-file-size")
    with target.open("xb") as output:
        os.chmod(target, 0o600)
        output.write(data)
    return data


def validate_api_key(value):
    # One key, no alternate authentication syntax or whitespace-delimited extras.
    pilot.require(re.fullmatch(rb"[A-Za-z0-9_-]{32,256}\n?", value) is not None,
                  "api-key-format")


def tls_health(port, hostname, context):
    # Verify the public certificate name while connecting only over loopback.
    with socket.create_connection(("127.0.0.1", port), timeout=2) as raw:
        with context.wrap_socket(raw, server_hostname=hostname) as connection:
            connection.sendall(b"GET /healthz HTTP/1.1\r\nHost: " + hostname.encode("ascii")
                               + b"\r\nConnection: close\r\n\r\n")
            response = http.client.HTTPResponse(connection)
            response.begin()
            response.close()
            return response.status == 200


def wait_ready(process, check, shutdown, *, timeout=90):
    deadline = time.monotonic() + timeout
    while not shutdown.is_set() and time.monotonic() < deadline:
        pilot.require(process.poll() is None, "service-exited")
        try:
            if check():
                return
        except (OSError, http.client.HTTPException):
            pass
        shutdown.wait(0.1)
    pilot.require(shutdown.is_set(), "readiness-timeout")


def supervise(processes, shutdown, *, max_seconds, health_check):
    deadline = time.monotonic() + max_seconds
    next_health = time.monotonic()
    while not shutdown.is_set():
        pilot.require(all(process.poll() is None for process in processes), "service-exited")
        pilot.require(time.monotonic() < deadline, "run-duration-exceeded")
        if time.monotonic() >= next_health:
            try:
                healthy = health_check()
            except (OSError, http.client.HTTPException):
                healthy = False
            pilot.require(healthy, "service-unhealthy")
            next_health = time.monotonic() + 10
        shutdown.wait(0.1)


def serve(args, shutdown):
    pilot.require(re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", args.hostname) is not None,
                  "tls-hostname")
    pilot.require(1024 <= args.port <= 65535 and args.port not in {8000, 8001}, "listen-port")
    pilot.require(1 <= args.max_seconds <= 86400, "run-duration")
    pins = json.loads((ROOT / "scripts/pilot/pins.json").read_text())
    engine, front, weights, _ = pilot.prepared_inputs(args.prepared, pins)
    bases = json.loads((ROOT / "scripts/e2e/pins.json").read_text())
    pilot.require(pilot.file_digest(front) == bases["trcs"]["binary_sha256"], "shim-digest")
    # Image construction verifies the pinned engine source and model. Deployment
    # must run the reviewed image digest, read-only, with no executable host mounts.
    with tempfile.TemporaryDirectory(prefix="nib-qwen-service-") as directory:
        work = Path(directory)
        cert, key, api_key = work / "cert.pem", work / "key.pem", work / "api-key"
        private_input(args.cert, cert, secret=False, limit=65536)
        private_input(args.key, key, secret=True, limit=16384)
        validate_api_key(private_input(args.api_key_file, api_key, secret=True, limit=257))
        # Reject broken/mismatched keypairs before starting either process.
        certificate = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        certificate.load_cert_chain(cert, key)
        trust = ssl.create_default_context(cafile=str(cert))
        env = pilot.controlled_env(work / "control")
        with ExitStack() as stack:
            def start(command):
                process = subprocess.Popen(command, cwd=work, env=env,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                stack.callback(pilot.stop, process)
                return process

            server = start(pilot.engine_command(engine, weights, pins["model_id"], 8000, api_key))
            wait_ready(server, lambda: pilot.request(8000, "/health")[0] == 200, shutdown)
            if shutdown.is_set():
                return
            api = start([str(args.prepared / "bin/nib-pilot-api"), "--api-key-file", str(api_key),
                         "--input-price", args.input_price, "--output-price", args.output_price])
            wait_ready(api, lambda: pilot.request(8001, "/health")[0] == 200, shutdown)
            if shutdown.is_set():
                return
            shim = start(pilot.shim_command(front, args.port, 8001, cert, key,
                                            host="0.0.0.0" if args.public else "127.0.0.1"))
            check = lambda: tls_health(args.port, args.hostname, trust)
            wait_ready(shim, check, shutdown)
            if shutdown.is_set():
                return
            print(json.dumps({"event": "ready", "confidential": False,
                              "model": pins["model_id"], "context_length": 4096,
                              "max_inflight": 2}), flush=True)
            supervise([server, api, shim], shutdown, max_seconds=args.max_seconds, health_check=check)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, default=Path("/opt/nib-pilot"))
    parser.add_argument("--cert", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--api-key-file", type=Path, required=True)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--input-price", required=True, help="Approved USD per million input tokens")
    parser.add_argument("--output-price", required=True, help="Approved USD per million output tokens")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--public", action="store_true", help="Bind standard TLS to all IPv4 interfaces")
    parser.add_argument("--max-seconds", type=int, default=86400,
                        help="Bound this pilot run; cloud resource lifetime is separately enforced")
    args = parser.parse_args()
    shutdown = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: shutdown.set())
    try:
        serve(args, shutdown)
    except (pilot.PilotError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        # Neither credentials nor request/provider error content enters diagnostics.
        print('{"event":"stopped","result":"fail"}', flush=True)
        return 1
    print('{"event":"stopped","result":"pass"}', flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
