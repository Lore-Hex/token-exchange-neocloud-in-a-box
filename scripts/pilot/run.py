#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Real local CPU inference through trcs; never evidence of confidential compute."""

import argparse
from contextlib import ExitStack, closing
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import types

ROOT = Path(__file__).resolve().parents[2]


def source_module(name):
    path = ROOT / "scripts/e2e" / (name + ".py")
    module = types.ModuleType("pilot_" + name)
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


controlled_env = source_module("snapshot").controlled_env
REQUIRED = source_module("exercise").REQUIRED


class PilotError(Exception):
    pass


def require(condition, stage):
    if not condition:
        raise PilotError(stage)


def checked_copy(source, target, expected, limit):
    """Copy regular inputs privately, then verify the bytes actually used."""
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream, target.open("xb") as output:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= limit, "input-file")
        digest, size = hashlib.sha256(), 0
        while chunk := stream.read(1 << 20):
            size += len(chunk)
            require(size <= limit, "input-size")
            digest.update(chunk)
            output.write(chunk)
        require(digest.hexdigest() == expected, "input-digest")


def unpack(archive, destination):
    destination.mkdir()
    with tarfile.open(archive) as source:
        members = source.getmembers()
        require(sum(item.size for item in members) <= 512 << 20, "archive-size")
        require(all(item.isfile() or item.isdir() for item in members), "archive-type")
        source.extractall(destination, filter="data")
    children = list(destination.iterdir())
    require(len(children) == 1 and children[0].is_dir(), "archive-root")
    return children[0]


def command(args, env, *, cwd=None, timeout=180):
    result = subprocess.run([str(arg) for arg in args], cwd=cwd, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=timeout)
    require(result.returncode == 0, "command-failed")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def engine_command(binary, weights, model_id, port, keyfile):
    # All flags documented at the exact pinned tools/server/README.md revision.
    return [str(binary), "--model", str(weights), "--alias", model_id,
            "--host", "127.0.0.1", "--port", str(port), "--threads", "4",
            "--ctx-size", "4096", "--parallel", "1", "--n-predict", "256",
            "--device", "none", "--gpu-layers", "0", "--offline",
            "--no-webui", "--no-slots", "--log-disable", "--jinja",
            "--chat-template-kwargs", '{"enable_thinking":false}',
            "--api-key-file", str(keyfile)]


def shim_command(binary, port, upstream_port, cert, key, *, host="127.0.0.1"):
    require(host in {"127.0.0.1", "0.0.0.0"}, "listen-host")
    return [str(binary), "shim", "--listen", host + ":" + str(port),
            "--upstream", "http://127.0.0.1:" + str(upstream_port),
            "--tee", "none", "--evidence-source", "none", "--max-inflight", "2",
            "--max-request-bytes", "65536",
            "--tls-cert", str(cert), "--tls-key", str(key)]


def request(port, path, *, tls=None, body=None, key=None):
    connection = (http.client.HTTPSConnection("127.0.0.1", port, context=tls, timeout=30)
                  if tls else http.client.HTTPConnection("127.0.0.1", port, timeout=30))
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    with closing(connection):
        connection.request("POST" if body is not None else "GET", path,
                           json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        data = response.read((1 << 20) + 1)
        require(len(data) <= 1 << 20, "response-size")
        return response.status, data


def ready(process, port, path, tls=None):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        require(process.poll() is None, "service-exited")
        try:
            if request(port, path, tls=tls)[0] == 200:
                return
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(0.1)
    raise PilotError("readiness-timeout")


def stop(process):
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def provider_result(report, returncode):
    require(report.get("suite_version") == "0.1.8", "checker-version")
    require(report["target"]["authentication"]["authorization_header_sent"] is True,
            "checker-authentication")
    checks = {item["id"]: item["status"] for item in report["checks"]}
    # Persist only known check IDs and fixed statuses, not free-form excerpts.
    allowed = REQUIRED | {"catalog.declared-v2"}
    require(set(checks) <= allowed and all(value in {"pass", "fail", "warn", "skip"}
                                         for value in checks.values()), "checker-shape")
    passed = returncode == 0 and all(checks.get(name) == "pass" for name in REQUIRED)
    return {"result": "pass" if passed else "fail", "checks": checks}


def check_provider(checker, port, model, cert, api_key, env):
    result = subprocess.run([str(checker),
                             "--base-url", "https://127.0.0.1:" + str(port) + "/v1",
                             "--model", model, "--tier", "4", "--json", "-"],
                            env={**env, "SSL_CERT_FILE": str(cert), "TR_PROVIDER_API_KEY": api_key},
                            capture_output=True, timeout=180)
    require(len(result.stdout) <= 1 << 20, "checker-report-size")
    return provider_result(json.loads(result.stdout), result.returncode)


def prepare_native(inputs, work, env, pins, trcs, summary):
    for filename, digest, limit in (
        ("model.gguf", pins["model"]["sha256"], pins["model"]["bytes"]),
        ("llama.tar.gz", pins["engine"]["archive_sha256"], 64 << 20),
        ("trcs.tar.gz", trcs["archive_sha256"], 16 << 20),
    ):
        checked_copy(inputs / filename, work / filename, digest, limit)
    engine = unpack(work / "llama.tar.gz", work / "engine-source")
    front = unpack(work / "trcs.tar.gz", work / "front-source")
    summary["stage"] = "build-engine"
    command(["cmake", "-S", engine, "-B", work / "engine-build", "-DCMAKE_BUILD_TYPE=Release",
             "-DGGML_METAL=OFF", "-DGGML_CUDA=OFF", "-DGGML_OPENMP=OFF",
             "-DLLAMA_BUILD_TESTS=OFF", "-DLLAMA_BUILD_EXAMPLES=OFF", "-DLLAMA_BUILD_APP=OFF",
             "-DLLAMA_BUILD_UI=OFF", "-DLLAMA_USE_PREBUILT_UI=OFF", "-DLLAMA_OPENSSL=OFF",
             "-DLLAMA_SUBPROCESS=OFF", "-DBUILD_SHARED_LIBS=OFF"], env)
    command(["cmake", "--build", work / "engine-build", "--target", "llama-server", "-j", "4"],
            env, timeout=900)
    summary["stage"] = "build-front-door"
    command(["go", "mod", "verify"], env, cwd=front)
    command(["go", "build", "-trimpath", "-buildvcs=false", "-o", work / "trcs", "./cmd/trcs"],
            env, cwd=front, timeout=300)
    summary["stage"] = "install-checker"
    command([sys.executable, "-m", "venv", work / "venv"], env)
    command(["uv", "pip", "install", "--python", work / "venv/bin/python", "--require-hashes",
             "-r", ROOT / "scripts/e2e/requirements.lock"], env)
    return (work / "engine-build/bin/llama-server", work / "trcs", work / "model.gguf",
            work / "venv/bin/trustedrouter-provider-check")


def file_digest(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "prepared-file")
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepared_inputs(prepared, pins):
    # Only the container harness uses this path: its rootfs and model are read-only.
    weights = prepared / "model.gguf"
    require(weights.stat().st_size == pins["model"]["bytes"]
            and file_digest(weights) == pins["model"]["sha256"], "prepared-model-digest")
    checker = shutil.which("trustedrouter-provider-check")
    require(checker is not None, "prepared-checker")
    return prepared / "bin/llama-server", prepared / "bin/trcs", weights, Path(checker)


def run(inputs, summary, *, prepared=None):
    pins = json.loads((ROOT / "scripts/pilot/pins.json").read_text())
    trcs = json.loads((ROOT / "scripts/e2e/pins.json").read_text())["trcs"]
    summary["pins"] = {**pins, "trcs": {name: trcs[name] for name in ("commit", "archive_sha256")}}
    processes = []
    with tempfile.TemporaryDirectory(prefix="nib-qwen-runtime-") as directory:
        work = Path(directory)
        env = controlled_env(work / "control")
        engine, front, weights, checker = (prepared_inputs(prepared, pins) if prepared is not None else
                                          prepare_native(inputs, work, env, pins, trcs, summary))
        summary["built_binary_sha256"] = {
            "llama-server": file_digest(engine), "trcs": file_digest(front),
        }
        cert, certkey, apikeyfile = work / "cert.pem", work / "key.pem", work / "api-key"
        command(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                 "-nodes", "-keyout", certkey, "-out", cert, "-days", "1", "-subj", "/CN=localhost",
                 "-addext", "subjectAltName=IP:127.0.0.1"], env)
        certkey.chmod(0o600)
        apikey = secrets.token_hex(32)
        apikeyfile.write_text(apikey + "\n")
        apikeyfile.chmod(0o600)
        tls = ssl.create_default_context(cafile=str(cert))
        upstream, public = free_port(), free_port()
        require(upstream != public, "port-collision")
        with ExitStack() as stack:
            def start(args):
                process = subprocess.Popen(args, env=env, cwd=work,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                processes.append(process)
                stack.callback(stop, process)
                return process

            summary["stage"] = "start-real-model"
            server = start(engine_command(engine, weights,
                                          pins["model_id"], upstream, apikeyfile))
            ready(server, upstream, "/health")
            shim = start(shim_command(front, public, upstream, cert, certkey))
            ready(shim, public, "/healthz", tls)
            summary["stage"] = "authenticated-inference"
            payload = {"model": pins["model_id"], "messages": [{"role": "user",
                       "content": "What is the capital of France? Answer in one word."}],
                       "max_tokens": 64, "temperature": 0}
            require(request(public, "/v1/chat/completions", tls=tls, body=payload)[0] == 401, "missing-key")
            require(request(public, "/v1/chat/completions", tls=tls, body=payload,
                            key="invalid-test-key")[0] == 401, "wrong-key")
            status, body = request(public, "/v1/chat/completions", tls=tls, body=payload, key=apikey)
            require(status == 200, "chat-status")
            response = json.loads(body)
            require("paris" in response["choices"][0]["message"]["content"].lower(), "known-answer")
            usage = response["usage"]
            require(all(type(usage.get(name)) is int and usage[name] > 0
                        for name in ("prompt_tokens", "completion_tokens", "total_tokens")), "usage-integers")
            require(usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"], "usage-sum")
            summary["inference"] = {"known_answer": "pass", "unauthorized_rejected": True,
                                    "usage": {name: usage[name] for name in
                                              ("prompt_tokens", "completion_tokens", "total_tokens")}}
            summary["stage"] = "provider-conformance"
            summary["provider"] = check_provider(checker, public, pins["model_id"], cert, apikey, env)
            require(summary["provider"]["result"] == "pass", "provider-conformance")
            # Deliberately kill inference and ensure the front door does not return an empty 200.
            stop(server)
            require(request(public, "/v1/chat/completions", tls=tls, body=payload, key=apikey)[0] == 502,
                    "backend-failure")
            summary["backend_failure_rejected"] = True
            if prepared is not None:
                stop(shim)
                summary["stage"] = "persistent-service"
                service = start([sys.executable, "-I", str(ROOT / "scripts/pilot/serve.py"),
                                 "--prepared", str(prepared), "--cert", str(cert), "--key", str(certkey),
                                 "--api-key-file", str(apikeyfile), "--hostname", "127.0.0.1",
                                 "--port", str(public), "--max-seconds", "300",
                                 "--input-price", "0.000001", "--output-price", "0.000002"])
                ready(service, public, "/healthz", tls)
                status, catalog = request(public, "/v1/models", tls=tls, key=apikey)
                require(status == 200 and json.loads(catalog).get("contract_version") == "2.0",
                        "service-catalog")
                require(request(public, "/v1/chat/completions", tls=tls, key=apikey,
                                body={**payload, "max_tokens": 257})[0] == 400, "service-output-limit")
                summary["service_provider"] = check_provider(checker, public, pins["model_id"], cert, apikey, env)
                require(summary["service_provider"]["result"] == "pass", "service-conformance")
                stop(service)
                require(service.returncode == 0, "service-graceful-shutdown")
                for port in (8000, 8001, public):
                    with socket.socket() as probe:
                        require(probe.connect_ex(("127.0.0.1", port)) != 0, "service-port-cleanup")
                summary["service"] = {"catalog_v2": "pass", "output_limit": "pass",
                                      "graceful_shutdown": "pass", "listeners_closed": "pass"}
        require(all(process.poll() is not None for process in processes), "process-cleanup")
        summary["cleanup"] = "processes-stopped"
    require(not work.exists(), "temporary-cleanup")
    summary.update(result="pass", stage="complete", cleanup="processes-stopped-and-temporary-files-removed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inputs", type=Path)
    mode.add_argument("--prepared", type=Path, help="Read-only container bundle; no runtime installation")
    args = parser.parse_args()
    summary = {"result": "fail", "stage": "verify-inputs", "execution": "real-cpu-model",
               "confidential": False, "hardware_validated": False, "public_endpoint": False,
               "billing_integrated": False, "cloud_resources_created": False,
               "runtime_platform": {"system": platform.system(), "machine": platform.machine()}}
    try:
        run(args.inputs, summary, prepared=args.prepared)
    except PilotError as error:
        summary["failure_code"] = str(error)  # Fixed internal check name, never exception details.
    except (OSError, ValueError, KeyError, TypeError, IndexError,
            subprocess.SubprocessError, tarfile.TarError, http.client.HTTPException):
        # Never relay request bodies, command lines, credentials or raw process errors.
        summary["failure_code"] = "execution-failed"
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["result"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
