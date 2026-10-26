# SPDX-License-Identifier: Apache-2.0
"""Metadata-only text-stream measurements; no transport, files or model dispatch."""

from dataclasses import dataclass
import json
import math
import re

MAX_EVENT = 64 << 10
MAX_STREAM = 64 << 20
MILLION_PROFILE = "long1m"
FAILURES = frozenset(("protocol-error", "size-limit", "timeout", "transport-error", "cancelled"))


def require(value):
    if not value:
        raise ValueError("stream measurement rejected")


def stream_limit(profile=None):
    require(profile is None or type(profile) is str and profile == MILLION_PROFILE)
    return 512 << 20 if profile == MILLION_PROFILE else MAX_STREAM


def number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def integer(value, maximum):
    return type(value) is int and 0 <= value <= maximum


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


@dataclass(frozen=True)
class Sample:
    status: str
    elapsed_seconds: float
    first_output_seconds: float | None
    first_content_seconds: float | None
    last_output_seconds: float | None
    output_events: int
    prompt_tokens: int | None
    completion_tokens: int | None
    cached_prompt_tokens: int | None
    finish_reason: str | None

    def metadata(self):
        ok = self.status == "completed"
        # Chunks need not contain one token. This is an explicitly named estimate,
        # never instantaneous TPS or measured per-token latency.
        interval = None
        if (ok and self.completion_tokens > 1 and self.output_events > 1
                and self.last_output_seconds > self.first_output_seconds):
            interval = ((self.last_output_seconds - self.first_output_seconds)
                        / (self.completion_tokens - 1))
        return {"status": self.status, "elapsed_seconds": self.elapsed_seconds,
                "ttft_seconds": self.first_output_seconds if ok else None,
                "first_content_seconds": self.first_content_seconds if ok else None,
                "mean_output_token_interval_estimate_seconds": interval,
                "output_events": self.output_events, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "cached_prompt_tokens": self.cached_prompt_tokens,
                "finish_reason": self.finish_reason}


class StreamMeasurement:
    """One n=1 text chat SSE response, with elapsed monotonic arrival timestamps.

    Caller must enforce HTTP status/media type, model/runtime identity, transport
    deadlines and request budgets. This class neither opens nor authorizes a socket.
    It intentionally accepts only the reviewed text benchmark subset, not tools.
    """

    def __init__(self, model, *, timeout_seconds, max_prompt_tokens, max_output_tokens, profile=None):
        self.max_stream_bytes = stream_limit(profile)
        million = profile == MILLION_PROFILE
        require(type(model) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,127}", model)
                and number(timeout_seconds) and 0 < timeout_seconds <= (3540 if million else 1800)
                and integer(max_prompt_tokens, 1048576) and max_prompt_tokens > 0
                and integer(max_output_tokens, 393216 if million else 131072) and max_output_tokens > 0)
        self.model, self.timeout = model, timeout_seconds
        self.max_prompt, self.max_output = max_prompt_tokens, max_output_tokens
        self.pending, self.event = bytearray(), bytearray()
        self.skip_lf = False
        self.bytes = self.events = 0
        self.elapsed = 0.0
        self.first = self.content = self.last = None
        self.usage = self.finish_reason = self.failure = None
        self.done = self.closed = False

    def fail(self, status):
        require(status in FAILURES)
        self.failure = self.failure or status
        self.pending.clear()
        self.event.clear()

    def timestamp(self, elapsed):
        if not number(elapsed) or elapsed < self.elapsed:
            self.fail("protocol-error")
            return False
        self.elapsed = float(elapsed)
        if elapsed > self.timeout:
            self.fail("timeout")
            return False
        return True

    def feed(self, data, elapsed):
        require(not self.closed)
        if self.failure or not self.timestamp(elapsed):
            return
        if type(data) is not bytes or len(data) > MAX_EVENT or self.bytes + len(data) > self.max_stream_bytes:
            self.fail("size-limit")
            return
        self.bytes += len(data)
        try:
            # SSE accepts CR, LF and CRLF, including a CRLF split between reads.
            # A partial event is never dispatched at EOF (WHATWG SSE sections 9.2.5-6).
            for byte in data:
                if self.skip_lf:
                    self.skip_lf = False
                    if byte == 10:
                        continue
                if byte in (10, 13):
                    line = bytes(self.pending)
                    self.pending.clear()
                    self.skip_lf = byte == 13
                    self.line(line)
                else:
                    self.pending.append(byte)
                    if len(self.pending) + len(self.event) > MAX_EVENT:
                        self.fail("size-limit")
                        return
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
            self.fail("protocol-error")

    def line(self, line):
        if not line:
            if self.event:
                payload = bytes(self.event[:-1])
                self.event.clear()
                self.message(payload)
            return
        # Validate UTF-8 even for ignored comments; never retain comment content.
        line.decode("utf-8", errors="strict")
        if line.startswith(b":"):
            return
        field, separator, value = line.partition(b":")
        require(separator and field == b"data")
        if value.startswith(b" "):
            value = value[1:]
        require(len(self.event) + len(value) + 1 <= MAX_EVENT)
        self.event.extend(value + b"\n")

    def message(self, payload):
        require(not self.done)
        if payload == b"[DONE]":
            require(self.finish_reason is not None and self.usage is not None and self.events > 0)
            self.done = True
            return
        require(self.usage is None)
        row = json.loads(payload, object_pairs_hook=unique, parse_constant=lambda _: require(False))
        require(type(row) is dict and "error" not in row and row.get("model") == self.model
                and type(row.get("choices")) is list and len(row["choices"]) <= 1)
        choices = row["choices"]
        if choices:
            require(self.finish_reason is None)
            choice = choices[0]
            require(type(choice) is dict and type(choice.get("index")) is int and choice["index"] == 0
                    and type(choice.get("delta")) is dict)
            delta = choice["delta"]
            require(set(delta) <= {"role", "content", "reasoning_content", "reasoning"}
                    and delta.get("role") in (None, "assistant"))
            output = False
            for name in ("content", "reasoning_content", "reasoning"):
                text = delta.get(name)
                require(text is None or type(text) is str)
                if text:
                    output = True
                    if name == "content" and self.content is None:
                        self.content = self.elapsed
            if output:
                self.events += 1
                self.first = self.elapsed if self.first is None else self.first
                self.last = self.elapsed
            finish = choice.get("finish_reason")
            require(finish in (None, "stop", "length"))
            self.finish_reason = finish
        else:
            require(self.finish_reason is not None and row.get("usage") is not None)
        usage = row.get("usage")
        if usage is not None:
            require(self.finish_reason is not None and type(usage) is dict)
            prompt, output, total = (usage.get(key) for key in
                                     ("prompt_tokens", "completion_tokens", "total_tokens"))
            require(integer(prompt, self.max_prompt) and integer(output, self.max_output) and output > 0
                    and integer(total, self.max_prompt + self.max_output) and total == prompt + output)
            details = usage.get("prompt_tokens_details")
            require(details is None or type(details) is dict)
            cached = None if details is None else details.get("cached_tokens")
            require(cached is None or integer(cached, prompt))
            details = usage.get("completion_tokens_details")
            require(details is None or type(details) is dict)
            reasoning = None if details is None else details.get("reasoning_tokens")
            require(reasoning is None or integer(reasoning, output))
            self.usage = (prompt, output, cached)

    def close(self, elapsed, *, failure=None):
        require(not self.closed and (failure is None or failure in FAILURES))
        self.timestamp(elapsed)
        if failure is not None:
            self.fail(failure)
        if not self.failure and (not self.done or self.pending or self.event):
            self.fail("protocol-error")
        self.closed = True
        prompt, output, cached = self.usage or (None, None, None)
        return Sample(self.failure or "completed", self.elapsed, self.first, self.content,
                      self.last, self.events, prompt, output, cached, self.finish_reason)


def percentile(values, fraction):
    """Linear interpolation at (n-1)*p, matching the pinned benchmark's convention."""
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return values[low] + (values[high] - values[low]) * (position - low)


def summarize(samples, *, scheduled, wall_seconds, gpu_count):
    """Closed measured cohort only; warmups must be accounted for separately.

    This consumes in-process samples, not untrusted evidence. Missing scheduled
    requests remain not-run, failures remain in the denominator, and unavailable
    usage is never treated as zero cost or zero work.
    """
    require(type(samples) in (list, tuple) and integer(scheduled, 100000) and scheduled > 0
            and len(samples) <= scheduled and all(type(row) is Sample for row in samples)
            and number(wall_seconds) and wall_seconds > 0
            and integer(gpu_count, 1024) and gpu_count > 0)
    require(all(row.status in FAILURES | {"completed"}
                and number(row.elapsed_seconds) and row.elapsed_seconds <= wall_seconds for row in samples))
    return _summarize_rows([sample.metadata() for sample in samples], scheduled=scheduled,
                           wall_seconds=wall_seconds, gpu_count=gpu_count)


def _summarize_rows(rows, *, scheduled, wall_seconds, gpu_count):
    # Shared arithmetic for in-process samples and the separately validated
    # metadata-only lab receipt. This is not untrusted-input admission.
    good = [row for row in rows if row["status"] == "completed"]
    failed = [row for row in rows if row["status"] != "completed"]
    output = sum(row["completion_tokens"] for row in good)
    result = {"scheduled": scheduled, "started": len(rows), "completed": len(good),
              "failed": len(failed), "not_run": scheduled - len(rows),
              "started_error_rate": len(failed) / len(rows) if rows else None,
              "scheduled_noncompletion_rate": (scheduled - len(good)) / scheduled,
              "wall_seconds": wall_seconds, "gpu_count": gpu_count,
              "completed_output_tokens": output,
              "completed_output_tokens_per_second_per_gpu": output / wall_seconds / gpu_count,
              "reported_failed_output_tokens": sum(row["completion_tokens"] or 0 for row in failed),
              "requests_with_unknown_usage": sum(row["completion_tokens"] is None for row in rows)}
    for metric in ("ttft_seconds", "first_content_seconds", "mean_output_token_interval_estimate_seconds"):
        values = [row[metric] for row in good if row[metric] is not None]
        result[metric] = {"samples": len(values), "p50": percentile(values, 0.5),
                          "p95": percentile(values, 0.95)}
    return result
