# SPDX-License-Identifier: Apache-2.0
"""Bounded text SSE collection from an already admitted, owned local engine.

No engine launch, admission receipt creation, benchmark scheduler or qualification.
The lab owner must verify source/model/tokenizer parity and retain its lifecycle.
"""

import http.client
import re
import socket
import threading
import time

import stream_measurement as measurement
import tau_live_identity as identity
import tau_local_vllm as local

worker = local.worker
require = measurement.require
HOST, PORT = "127.0.0.1", 8000
MODEL = "deepseek-v4.1-flash-1017"
# Existing engine_lab context and engine_local_correctness request bounds.
CONTEXT, MAX_OUTPUT, REQUEST_SECONDS = 4096, 512, 60
PATH = "/v1/chat/completions"


class StreamResponse(http.client.HTTPResponse):
    """CPython 3.12.3/3.12.13, restricted to chunks without extensions/trailers.

    Its default decoder discards unchecked chunk CRLF and accepts missing trailer
    termination. Keep read1(), but reject these ambiguous EOF/framing cases.
    """
    tail = None

    def __init__(self, *args, profile=None, **kwargs):
        self.max_stream_bytes = measurement.stream_limit(profile)
        super().__init__(*args, **kwargs)

    def _close_conn(self):
        # Preserve any bytes buffered past HTTP termination for the EOF check.
        if self.fp is not None:
            self.tail, self.fp = self.fp, None

    def close(self):
        try:
            super().close()
        finally:
            if self.tail is not None:
                self.tail.close()
                self.tail = None

    def _read_next_chunk_size(self):
        line = self.fp.readline(16)
        require(re.fullmatch(rb"[0-9a-fA-F]{1,8}\r\n", line))
        size = int(line, 16)
        require(size <= self.max_stream_bytes)
        return size

    def _get_chunk_left(self):
        if self.chunk_left == 0:
            require(self._safe_read(2) == b"\r\n")
            self.chunk_left = None
        return super()._get_chunk_left()

    def _read_and_discard_trailer(self):
        require(self.fp.readline(3) == b"\r\n")


def response_length(response, *, profile=None):
    maximum = measurement.stream_limit(profile)
    headers = response.getheaders()
    require(not response.headers.defects and len(headers) <= 32
            and sum(len(key) + len(value) for key, value in headers) <= 65536
            and all("\r" not in value and "\n" not in value for _, value in headers))
    for name in ("content-type", "content-length", "transfer-encoding", "content-encoding"):
        require(sum(key.lower() == name for key, _ in headers) <= 1)
    require(response.status == 200 and response.version in (10, 11)
            and response.getheader("Content-Encoding", "identity").lower() == "identity")
    media = [part.strip().lower() for part in response.getheader("Content-Type", "").split(";")]
    require(media in (["text/event-stream"], ["text/event-stream", "charset=utf-8"]))
    length, encoding = response.getheader("Content-Length"), response.getheader("Transfer-Encoding")
    require(not (length is not None and encoding is not None)
            and (encoding is None or encoding.lower() == "chunked"))
    if length is not None:
        require(length.isascii() and length.isdecimal() and len(length) <= len(str(maximum))
                and 0 < int(length) <= maximum)
        return int(length)
    return None


def request_body(messages, prompt_tokens, max_output_tokens, *, model=MODEL, cache_salt=None):
    require(type(model) is str and re.fullmatch(r"[a-z0-9_.-]{1,128}", model))
    require(type(max_output_tokens) is int and 0 < max_output_tokens <= MAX_OUTPUT
            and type(prompt_tokens) is int and 0 < prompt_tokens <= CONTEXT - max_output_tokens
            and type(messages) is list and 1 <= len(messages) <= 2
            and all(type(row) is dict and set(row) == {"role", "content"}
                    and type(row["content"]) is str and 0 < len(row["content"]) <= worker.MAX_REQUEST
                    for row in messages)
            and [row["role"] for row in messages] in (["user"], ["system", "user"]))
    require(cache_salt is None or type(cache_salt) is str and re.fullmatch("[0-9a-f]{64}", cache_salt))
    body = {"model": model, "messages": messages,
        "stream": True, "stream_options": {"include_usage": True}, "n": 1, "temperature": 0,
        "max_completion_tokens": max_output_tokens, "reasoning_effort": "none",
        "add_generation_prompt": True, "continue_final_message": False, "add_special_tokens": False,
        "chat_template_kwargs": {"reasoning_effort": "none", "enable_thinking": False}}
    if cache_salt is not None:
        body["cache_salt"] = cache_salt
    return worker.bounded_json(body, worker.MAX_REQUEST)


class OwnedStreamHTTP(local.LocalChatHTTP):
    """Single main-thread request; no arbitrary endpoint, model or guard callback.

    Endpoint must come from the owner's authenticated admission, not a remote
    observer's PID namespace or an untrusted historical receipt. This collector
    does not grant PID/UID/capability access or claim endpoint attestation.
    """

    def __init__(self, endpoint, *, model=MODEL):
        require(type(endpoint) is identity.Endpoint and endpoint.port == PORT)
        require(type(model) is str and re.fullmatch(r"[a-z0-9_.-]{1,128}", model))
        super().__init__(PORT)
        self.endpoint = endpoint
        self.model = model

    def connect(self, sock, deadline):
        self.endpoint.check(deadline)
        super().connect(sock, deadline)
        self.endpoint.check(deadline, sock)

    def collect(self, messages, *, prompt_tokens, max_output_tokens, deadline, cache_salt=None):
        require(threading.current_thread() is threading.main_thread()
                and measurement.number(deadline))
        raw = request_body(messages, prompt_tokens, max_output_tokens, model=self.model, cache_salt=cache_salt)
        require(self.busy.acquire(blocking=False))
        started = time.monotonic()
        deadline = min(deadline, started + REQUEST_SECONDS)
        meter = measurement.StreamMeasurement(self.model, timeout_seconds=REQUEST_SECONDS,
            max_prompt_tokens=prompt_tokens, max_output_tokens=max_output_tokens)
        connection = response = sock = None
        failure = None
        try:
            with worker.alarm(deadline):
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.set_inheritable(False)
                sock.settimeout(self.remaining(deadline))
                self.connect(sock, deadline)
                connection = http.client.HTTPConnection(HOST, PORT)
                connection.set_debuglevel(0)
                connection.response_class = StreamResponse
                connection.sock = sock
                sock.settimeout(self.remaining(deadline))
                connection.request("POST", PATH, body=raw, headers={
                    "Content-Type": "application/json", "Accept": "text/event-stream",
                    "Accept-Encoding": "identity", "Connection": "close"})
                raw = None
                response = connection.getresponse()
                if response.status == 429 or 500 <= response.status <= 599:
                    raise OSError
                length = response_length(response)
                count = 0
                while True:
                    sock.settimeout(self.remaining(deadline))
                    block = response.read1(measurement.MAX_EVENT)
                    elapsed = time.monotonic() - started
                    self.remaining(deadline)
                    if not block:
                        break
                    count += len(block)
                    meter.feed(block, elapsed)
                    if meter.failure:
                        break
                if not meter.failure:
                    require(length is None or count == length)
                    require(response.tail is not None and response.tail.read1(1) == b"")
                    # DONE alone is insufficient: require HTTP end and the live
                    # owner still matching, preserving known usage on late failure.
                    self.endpoint.check(deadline)
                    require(meter.usage is not None and meter.usage[0] == prompt_tokens)
                    self.remaining(deadline)
        except BaseException as exc:
            failure = ("timeout" if isinstance(exc, TimeoutError) or
                       isinstance(exc, worker.Abort) and exc.status == "timeout" else
                       "cancelled" if isinstance(exc, (KeyboardInterrupt, SystemExit, InterruptedError)) else
                       "transport-error" if isinstance(exc, OSError) else "protocol-error")
        finally:
            for resource in (response, connection, sock):
                if resource is not None:
                    try:
                        resource.close()
                    except OSError:
                        failure = failure or "transport-error"
            self.busy.release()
        ended = time.monotonic()
        if ended >= deadline:
            failure = failure or "timeout"
        return meter.close(ended - started, failure=failure)
