# SPDX-License-Identifier: Apache-2.0
"""Fixed sequential chat smoke on an already admitted owned DeepSeek engine.

No launch, source admission, untrusted receipt parsing or qualification. The
owner must measure the workload with its source-verified exact tokenizer first.
"""

from dataclasses import dataclass
import hashlib
import json
import re
import threading
import time

import stream_transport as stream

meter, require = stream.measurement, stream.require
PROMPT_TOKENS, OUTPUT_TOKENS = 2048, 512
MEASURED_REQUESTS, WARMUP_REQUESTS = 8, 1
TOTAL_REQUESTS = MEASURED_REQUESTS + WARMUP_REQUESTS
RESERVED_TOKENS = TOTAL_REQUESTS * (PROMPT_TOKENS + OUTPUT_TOKENS)
PREPARE_SECONDS, COHORT_SECONDS = 60, 180
MAX_PADDING, SEARCH_RADIUS = 4096, 16


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def messages(index, padding):
    require(type(index) is int and 0 <= index < TOTAL_REQUESTS
            and type(padding) is int and 0 <= padding <= MAX_PADDING)
    # Different leading request IDs avoid deliberately replaying the warmup.
    # They do not prove a cold cache; only reported cache usage is measured.
    content = (f"Request {index:02d}. Read the filler and then follow the instruction.\n"
               + " x" * padding
               + "\nList the integers from 1 to 200, one per line. Do not explain.")
    return [{"role": "user", "content": content}]


@dataclass(frozen=True)
class Workload:
    # Only padding counts and token digests. No prompt or token-ID retention.
    cases: tuple

    def validate(self):
        require(type(self.cases) is tuple and len(self.cases) == TOTAL_REQUESTS)
        for row in self.cases:
            require(type(row) is tuple and len(row) == 2
                    and type(row[0]) is int and 0 <= row[0] <= MAX_PADDING
                    and type(row[1]) is str and re.fullmatch("[0-9a-f]{64}", row[1]))

    def metadata(self):
        self.validate()
        rows = [{"index": index, "padding": padding, "prompt_tokens": PROMPT_TOKENS,
                 "messages_sha256": digest(messages(index, padding)), "token_ids_sha256": token_hash}
                for index, (padding, token_hash) in enumerate(self.cases)]
        return {"schema": "nib.chat-stream-workload/v1", "cases": rows,
                "binding_sha256": digest(rows)}


def prepare(tokenizer, *, deadline):
    """Call only in the owner's bounded, source-verified exact-tokenizer child.

    Counter identity is deliberately not inferred from a Python object's name.
    The search never assumes BPE counts are monotonic or pads an approximate
    count. If the fixed synthetic fixture cannot reach exactly 2K, fail closed.
    """
    require(threading.current_thread() is threading.main_thread() and meter.number(deadline))
    deadline = min(deadline, time.monotonic() + PREPARE_SECONDS)
    rows = []
    with stream.worker.alarm(deadline):
        for index in range(TOTAL_REQUESTS):
            def count(padding):
                require(time.monotonic() < deadline)
                ids = tokenizer.apply_chat_template(messages(index, padding), tools=None, tokenize=True,
                    add_generation_prompt=True, continue_final_message=False,
                    reasoning_effort="none", enable_thinking=False)
                require(type(ids) is list and 0 < len(ids) <= 16384
                        and all(type(value) is int and 0 <= value < 2**31 for value in ids)
                        and time.monotonic() < deadline)
                return len(ids), digest(ids)

            # Empty padding can merge the surrounding text differently. Estimate
            # from two nonempty prefixes, then still require an exact measured fit.
            base, _ = count(64)
            probe, _ = count(128)
            require(base < PROMPT_TOKENS and probe > base)
            center = 64 + (PROMPT_TOKENS - base) * 64 // (probe - base)
            candidates = [center] + [center + delta * sign
                                    for delta in range(1, SEARCH_RADIUS + 1) for sign in (-1, 1)]
            for padding in candidates:
                if 0 <= padding <= MAX_PADDING:
                    size, token_hash = count(padding)
                    if size == PROMPT_TOKENS:
                        rows.append((padding, token_hash))
                        break
            else:
                raise ValueError("stream workload rejected")
    result = Workload(tuple(rows))
    result.validate()
    return result


def _collect(client, workload, index, deadline):
    started = time.monotonic()
    try:
        return client.collect(messages(index, workload.cases[index][0]),
            prompt_tokens=PROMPT_TOKENS, max_output_tokens=OUTPUT_TOKENS, deadline=deadline)
    except BaseException as exc:
        # An unexpected transport failure must not erase an attempted request or
        # include exception text. Ordinary late failures already preserve usage.
        status = ("cancelled" if isinstance(exc, (KeyboardInterrupt, SystemExit, InterruptedError))
                  else "timeout" if isinstance(exc, TimeoutError) or
                  isinstance(exc, stream.worker.Abort) and exc.status == "timeout"
                  else "transport-error" if isinstance(exc, OSError) else "protocol-error")
        return meter.Sample(status, max(0, time.monotonic() - started), None, None, None,
                            0, None, None, None, None)


def run(endpoint, workload, *, deadline, gpu_count):
    """One warmup then eight sequential requests, stopping on the first failure.

    The same-owner Endpoint and exact-tokenizer workload must be supplied by the
    authenticated controller. These in-process values are not evidence admission.
    Caller retains engine ownership and cleanup; there is no automatic restart.
    """
    require(threading.current_thread() is threading.main_thread()
            and type(workload) is Workload and meter.number(deadline)
            and type(gpu_count) is int and gpu_count in (4, 8))
    workload.validate()
    client = stream.OwnedStreamHTTP(endpoint)
    started = time.monotonic()
    deadline = min(deadline, started + COHORT_SECONDS)
    warmup, samples, measured_started = None, [], None
    stop = "deadline"
    if time.monotonic() < deadline:
        warmup = _collect(client, workload, 0, deadline)
        stop = "warmup-failed"
        if warmup.status == "completed":
            stop = "deadline"
            for index in range(1, TOTAL_REQUESTS):
                if time.monotonic() >= deadline:
                    break
                if measured_started is None:
                    measured_started = time.monotonic()
                sample = _collect(client, workload, index, deadline)
                samples.append(sample)
                if sample.status != "completed":
                    stop = "request-failed"
                    break
            if len(samples) == MEASURED_REQUESTS and all(row.status == "completed" for row in samples):
                stop = "complete"
    ended = time.monotonic()
    if ended >= deadline and stop == "complete":
        stop = "deadline"
    all_samples = ([warmup] if warmup is not None else []) + samples
    return {"schema": "nib.chat-stream-cohort/v1", "model": stream.MODEL,
            "workload": workload.metadata(), "concurrency": 1,
            "prompt_tokens_per_request": PROMPT_TOKENS, "max_output_tokens_per_request": OUTPUT_TOKENS,
            "reserved_requests": TOTAL_REQUESTS, "reserved_total_tokens": RESERVED_TOKENS,
            "started_requests": len(all_samples), "not_run_requests": TOTAL_REQUESTS - len(all_samples),
            "known_prompt_tokens": sum(row.prompt_tokens or 0 for row in all_samples),
            "known_output_tokens": sum(row.completion_tokens or 0 for row in all_samples),
            "requests_with_unknown_usage": sum(row.completion_tokens is None for row in all_samples),
            "warmup": warmup.metadata() if warmup is not None else None,
            "samples": [row.metadata() for row in samples],
            "measured": (meter.summarize(samples, scheduled=MEASURED_REQUESTS,
                wall_seconds=ended - measured_started, gpu_count=gpu_count)
                if measured_started is not None else None),
            "elapsed_seconds": ended - started, "stop_reason": stop,
            "completed": stop == "complete", "pricing_measured": False,
            "quality_accepted": False, "confidential": False, "sweep_accepted": False}
