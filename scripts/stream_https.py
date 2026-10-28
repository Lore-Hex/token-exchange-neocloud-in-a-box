# SPDX-License-Identifier: Apache-2.0
"""One bounded selected-candidate HTTPS stream; no scheduler or admission grant."""

import re
import threading

import bounded_https
import stream_measurement as measurement
import stream_transport as transport

worker = bounded_https.worker
require = measurement.require
MAX_REQUEST = 2 << 20
MAX_PROMPT, MAX_OUTPUT, REQUEST_SECONDS = 131072, 8192, 119


def request_bytes(body, prompt_tokens):
    """Closed synthetic text subset; the caller must verify tokenizer parity."""
    require(type(body) is dict and set(body) == {
        "model", "messages", "max_tokens", "temperature", "stream",
        "stream_options", "reasoning_effort", "cache_salt"})
    require(body["model"] == transport.SELECTED_MODEL and body["stream"] is True
            and type(body["stream_options"]) is dict
            and set(body["stream_options"]) == {"include_usage"}
            and body["stream_options"]["include_usage"] is True
            and type(body["temperature"]) in (int, float) and body["temperature"] == 0
            and body["reasoning_effort"] == "none"
            and type(body["max_tokens"]) is int and 0 < body["max_tokens"] <= MAX_OUTPUT
            and type(prompt_tokens) is int and 0 < prompt_tokens <= MAX_PROMPT
            and type(body["cache_salt"]) is str and re.fullmatch("[0-9a-f]{64}", body["cache_salt"]))
    messages = body["messages"]
    require(type(messages) is list and 1 <= len(messages) <= 2
            and all(type(row) is dict and set(row) == {"role", "content"}
                    and type(row["content"]) is str and 0 < len(row["content"]) <= MAX_REQUEST
                    for row in messages)
            and [row["role"] for row in messages] in (["user"], ["system", "user"]))
    return worker.bounded_json(body, MAX_REQUEST)


class SelectedHTTPS(bounded_https.HTTPS):
    """Explicit owned address, unchanged TLS verification, no redirects/retries.

    A reviewed caller owns run budgets, namespace generation, endpoint/runtime
    identity, durable receipts and their reconciliation. This transport has no
    CLI, credential discovery, process launch or price-changing behavior.
    """

    def __init__(self, hostname, *, address, **options):
        require(address is not None)
        super().__init__(hostname, (transport.PATH,), address=address, **options)
        self.last_attempt_started = False

    def collect(self, body, credential, *, prompt_tokens, deadline, before_send):
        require(threading.current_thread() is threading.main_thread() and not self.used
                and callable(before_send) and measurement.number(deadline)
                and type(credential) is bytes and 16 <= len(credential) <= 4096
                and all(33 <= byte <= 126 for byte in credential))
        payload = request_bytes(body, prompt_tokens)
        started = self.clock()
        deadline = min(deadline, started + REQUEST_SECONDS)
        require(deadline > started)
        meter = measurement.StreamMeasurement(transport.SELECTED_MODEL,
            timeout_seconds=REQUEST_SECONDS, max_prompt_tokens=MAX_PROMPT,
            max_output_tokens=body["max_tokens"])
        self.used, self.last_status, self.last_attempt_started = True, None, False
        failure, callback_failed = None, False
        try:
            with worker.alarm(deadline, self.clock), self.connection(deadline) as connection:
                authority = self.hostname + (":" + str(self.port) if self.port != 443 else "")
                header = ("POST " + transport.PATH + " HTTP/1.1\r\nHost: " + authority
                    + "\r\nConnection: close\r\nAccept: text/event-stream\r\nAccept-Encoding: identity"
                    + "\r\nContent-Type: application/json\r\nContent-Length: " + str(len(payload))
                    + "\r\nAuthorization: Bearer ").encode("ascii") + credential + b"\r\n\r\n"
                # Persist intent before any authenticated bytes. Receipt failures
                # propagate, rather than becoming a recoverable request failure.
                callback_failed = True
                before_send()
                callback_failed = False
                self.last_attempt_started = True
                self.attempts[transport.PATH] += 1
                connection.sendall(header + payload)
                payload = header = credential = None
                with transport.StreamResponse(connection) as response:
                    response.begin()
                    self.last_status = response.status
                    if response.status == 429 or 500 <= response.status <= 599:
                        raise OSError
                    length = transport.response_length(response)
                    count = 0
                    while True:
                        connection.settimeout(self.remaining(deadline))
                        block = response.read1(measurement.MAX_EVENT)
                        elapsed = self.clock() - started
                        self.remaining(deadline)
                        if not block:
                            break
                        count += len(block)
                        meter.feed(block, elapsed)
                        if meter.failure:
                            break
                    if not meter.failure:
                        require(length is None or count == length)
                        connection.settimeout(self.remaining(deadline))
                        require(response.tail is not None and response.tail.read1(1) == b"")
                        require(meter.usage is not None and meter.usage[0] == prompt_tokens)
                        self.remaining(deadline)
        except BaseException as exc:
            if callback_failed:
                raise
            failure = ("timeout" if isinstance(exc, TimeoutError) or
                       isinstance(exc, worker.Abort) and exc.status == "timeout" else
                       "cancelled" if isinstance(exc, (KeyboardInterrupt, SystemExit, InterruptedError)) else
                       "transport-error" if isinstance(exc, OSError) else "protocol-error")
        finally:
            self.used = False
        ended = self.clock()
        if ended >= deadline:
            failure = failure or "timeout"
        return meter.close(ended - started, failure=failure)
