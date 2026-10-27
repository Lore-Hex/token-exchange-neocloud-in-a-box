# SPDX-License-Identifier: Apache-2.0
"""Owned-engine metrics: rank labels, shared advertised capacity, not HBM proof."""

import hashlib
import http.client
import json
import re
import socket
import time

import stream_transport as stream

require = stream.require
LIMIT, SECONDS = 2 << 20, 5
# vLLM 5df63a7203cdb9328d824a27b682e77c7b9928be: cache config info,
# per-engine scheduler counters, and graph-memory subtraction before KV sizing.
SOURCES = {
    "vllm/v1/metrics/loggers.py": "a17291f044251f1586457b6bd1542f98660724dd07a426d61ce852186b95b9e1",
    "vllm/v1/worker/gpu_worker.py": "e970aa29a738aa15cd963d9f9202ca6d3d504e98fd344959fd998e940472a5a0",
    "vllm/v1/engine/core_client.py": "386b528a69029507f80a2df4d6df2a4fa6ba2ba811501dc3449ceea58b5cd90a",
    "vllm/entrypoints/serve/instrumentator/metrics.py": "d511dd081071daeaf993a653e8716f73de9afbbece4c6eeacfe3a8c948a5bc74",
}
METRICS = {"vllm:num_requests_running": "running", "vllm:num_requests_waiting": "waiting",
           "vllm:kv_cache_usage_perc": "kv_fraction", "vllm:num_preemptions_total": "preemptions"}
LABEL = re.compile(r'([a-zA-Z_][a-zA-Z_0-9]*)=("(?:[^"\\\n]|\\[\\"n])*")(?:,|$)')
LINE = re.compile(r'([a-zA-Z_:][a-zA-Z_0-9:]*)(?:\{(.*)\})? ([^ ]+)')


def verify_sources(read, root):
    for name, expected in SOURCES.items():
        require(hashlib.sha256(read(root + "/" + name, 256 << 10)).hexdigest() == expected)


def labels(raw):
    result, position = {}, 0
    require(len(raw) <= 16384)
    while position < len(raw):
        match = LABEL.match(raw, position)
        require(match is not None and match[1] not in result and len(result) < 128)
        result[match[1]] = json.loads(match[2])
        position = match.end()
    return result


def parse(raw, model):
    return _parse(raw, model, minimum_concurrency=32)


def _parse(raw, model, *, minimum_concurrency):
    # Closed callers select the 4K short-only or 128K combined policy. The
    # latter uses a different receipt and is not registered as a live profile.
    require(type(minimum_concurrency) is int and minimum_concurrency in (1, 32))
    require(type(raw) is bytes and 0 < len(raw) <= LIMIT)
    result = [{"rank": rank, "graph_memory_bytes": None, "graph_capture_observed": False} for rank in (0, 1)]
    seen = set()
    for line in raw.decode("utf-8").splitlines():
        if not any(line.startswith(name + "{") for name in (*METRICS, "vllm:cache_config_info")):
            continue
        match = LINE.fullmatch(line)
        require(match is not None)
        name, values = match[1], labels(match[2])
        require(values.get("engine") in ("0", "1") and (name, values["engine"]) not in seen)
        seen.add((name, values["engine"]))
        row = result[int(values["engine"])]
        if name == "vllm:cache_config_info":
            require(match[3] in ("1", "1.0") and values.get("cache_dtype") == "fp8"
                    and values.get("enable_prefix_caching") == "True"
                    and values.get("kv_offloading_size") == "None")
            tokens = values.get("kv_cache_size_tokens", "")
            require(tokens.isascii() and tokens.isdecimal() and len(tokens) <= 16)
            concurrency = float(values.get("kv_cache_max_concurrency", "nan"))
            require(stream.measurement.number(concurrency) and concurrency >= minimum_concurrency
                    and 32 * 4096 <= int(tokens) < 2**53)
            row.update(kv_capacity_tokens=int(tokens), kv_max_concurrency=concurrency)
        else:
            require(set(values) == {"engine", "model_name"} and values["model_name"] == model)
            value = float(match[3])
            require(stream.measurement.number(value) and value < 2**53)
            if METRICS[name] == "kv_fraction":
                require(value <= 1)
            else:
                require(value.is_integer())
                value = int(value)
            row[METRICS[name]] = value
    require(len(seen) == 10)
    return result


def probe(endpoint, model, deadline):
    deadline = min(deadline, time.monotonic() + SECONDS)
    with stream.worker.alarm(deadline):
        return parse(_probe_raw(endpoint, model, deadline), model)


def _probe_raw(endpoint, model, deadline, *, selected_model=False):
    require(type(endpoint) is stream.identity.Endpoint and endpoint.port == stream.PORT)
    deadline = min(deadline, time.monotonic() + SECONDS)
    client = stream.OwnedStreamHTTP(endpoint, model=model, selected_model=selected_model)
    sock = response = connection = None
    try:
        with stream.worker.alarm(deadline):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.set_inheritable(False)
            sock.settimeout(client.remaining(deadline))
            client.connect(sock, deadline)
            connection = http.client.HTTPConnection(stream.HOST, stream.PORT)
            connection.response_class = stream.StreamResponse
            connection.sock = sock
            connection.request("GET", "/metrics", headers={"Accept": "text/plain; version=0.0.4",
                               "Accept-Encoding": "identity", "Connection": "close"})
            response = connection.getresponse()
            headers = response.getheaders()
            require(response.status == 200 and not response.headers.defects and len(headers) <= 32
                    and response.getheader("Content-Encoding", "identity") == "identity"
                    and response.getheader("Content-Type", "").split(";", 1)[0] == "text/plain"
                    and sum(len(k) + len(v) for k, v in headers) <= 65536
                    and all("\r" not in v and "\n" not in v for _, v in headers)
                    and sum(k.lower() == "content-length" for k, _ in headers) <= 1
                    and sum(k.lower() == "transfer-encoding" for k, _ in headers) <= 1
                    and sum(k.lower() == "content-type" for k, _ in headers) == 1
                    and sum(k.lower() == "content-encoding" for k, _ in headers) <= 1)
            length, encoding = response.getheader("Content-Length"), response.getheader("Transfer-Encoding")
            require((length is None and encoding == "chunked") or
                    (encoding is None and type(length) is str and length.isascii() and length.isdecimal()
                     and len(length) <= 8 and 0 < int(length) <= LIMIT))
            raw = bytearray()
            while True:
                block = response.read1(min(65536, LIMIT + 1 - len(raw)))
                client.remaining(deadline)
                if not block:
                    break
                raw.extend(block)
                require(len(raw) <= LIMIT)
            require(length is None or len(raw) == int(length))
            require(response.tail is not None and response.tail.read1(1) == b"")
            client.remaining(deadline)
            endpoint.check(deadline)
            return bytes(raw)
    finally:
        for resource in (response, connection, sock):
            if resource is not None:
                resource.close()


def stable(before, after):
    return _stable(before, after, minimum_concurrency=32)


def _stable(before, after, *, minimum_concurrency):
    require(type(minimum_concurrency) is int and minimum_concurrency in (1, 32))
    require(type(before) is list and type(after) is list and len(before) == len(after) == 2)
    for rank, (old, new) in enumerate(zip(before, after)):
        require(type(old) is dict and type(new) is dict
                and set(old) == set(new) == {"rank", "graph_memory_bytes", "graph_capture_observed",
                "kv_capacity_tokens", "kv_max_concurrency", "running", "waiting", "kv_fraction", "preemptions"})
        require(type(old["rank"]) is int and type(new["rank"]) is int and old["rank"] == new["rank"] == rank
                and old["graph_memory_bytes"] is new["graph_memory_bytes"] is None
                and old["graph_capture_observed"] is new["graph_capture_observed"] is False
                and type(new["kv_capacity_tokens"]) is int and 32 * 4096 <= new["kv_capacity_tokens"] < 2**53
                and stream.measurement.number(new["kv_max_concurrency"])
                and new["kv_max_concurrency"] >= minimum_concurrency
                and all(old[k] == new[k] for k in ("kv_capacity_tokens", "kv_max_concurrency", "preemptions"))
                and all(type(new[k]) is int and 0 <= new[k] < 2**53 for k in ("running", "waiting", "preemptions"))
                and stream.measurement.number(new["kv_fraction"]) and new["kv_fraction"] <= 1)
