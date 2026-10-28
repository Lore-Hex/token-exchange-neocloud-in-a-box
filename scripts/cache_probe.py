# SPDX-License-Identifier: Apache-2.0
"""Bounded serial cache probes on an already admitted external selected node."""

import re
import secrets
import threading
import time

import selected_long_qualification as workload
import stream_https as stream

meter, require, worker = stream.measurement, stream.require, stream.worker
SHAPES = {2048: 512, 32768: 1024, 131072: 1024}
REPEATS, REQUESTS, RUN_SECONDS = 3, 9, 1200
PHASES = ("cold", "warm", "different-namespace")


def request(padding, prompt_tokens, namespace):
    require(type(prompt_tokens) is int and prompt_tokens in SHAPES)
    body = {"model": stream.transport.SELECTED_MODEL,
            "messages": workload.messages(3, padding),
            "max_tokens": SHAPES[prompt_tokens], "temperature": 0,
            "stream": True, "stream_options": {"include_usage": True},
            "reasoning_effort": "none", "cache_salt": namespace}
    stream.request_bytes(body, prompt_tokens)
    return body


def run(client, credential, *, padding, prompt_tokens, run_id, deadline,
        emit, check_runtime, clock=time.monotonic):
    """Three fresh-namespace cold/warm/negative triplets, C1, no retries.

    The admitted caller must bind exact tokenizer counts, runtime and budgets,
    provide an exclusive fsynced journal, and persist every emitted record.
    Receipt errors and unexpected transport exceptions propagate immediately:
    any unmatched intent remains possibly sent, never free or resumable.
    """
    require(threading.current_thread() is threading.main_thread()
            and type(client) is stream.SelectedHTTPS and not client.used
            and not getattr(client, "cache_probe_used", False)
            and client.attempts == {stream.transport.PATH: 0}
            and type(run_id) is str and re.fullmatch("[0-9a-f]{64}", run_id)
            and callable(emit) and callable(check_runtime) and callable(clock)
            and meter.number(deadline)
            and type(credential) is bytes and 16 <= len(credential) <= 4096
            and all(33 <= byte <= 126 for byte in credential))
    request(padding, prompt_tokens, "0" * 64)
    started = clock()
    deadline = min(deadline, started + RUN_SECONDS)
    require(meter.number(started) and deadline > started)
    namespaces = tuple(secrets.token_hex(32) for _ in range(REPEATS * 2))
    require(len(set(namespaces)) == len(namespaces)
            and all(re.fullmatch("[0-9a-f]{64}", value) for value in namespaces))
    client.cache_probe_used = True

    def record(value):
        # Leave a small bounded receipt-only cleanup allowance, never another
        # inference request after the run deadline. No exception text is stored.
        with worker.alarm(min(deadline + 5, clock() + 5), clock):
            emit({"schema": "nib.cache-probe/v1", "run_id": run_id, **value})

    record({"kind": "manifest", "concurrency": 1, "retries": 0,
            "planned_requests": REQUESTS, "prompt_tokens_per_request": prompt_tokens,
            "max_output_tokens_per_request": SHAPES[prompt_tokens],
            "reserved_total_tokens": REQUESTS * (prompt_tokens + SHAPES[prompt_tokens]),
            "messages_sha256": workload.digest(workload.messages(3, padding)),
            "run_seconds_cap": RUN_SECONDS})
    rows, stop = [], "complete"
    for index in range(REQUESTS):
        if clock() >= deadline:
            stop = "deadline"
            break
        try:
            with worker.alarm(deadline, clock):
                check_runtime()
        except BaseException as exc:
            stop = ("deadline" if isinstance(exc, (worker.Abort, TimeoutError)) else
                    "cancelled" if isinstance(exc, (KeyboardInterrupt, SystemExit, InterruptedError))
                    else "preflight-failed")
            break
        repeat, phase_index = divmod(index, len(PHASES))
        scope = namespaces[repeat * 2 + (phase_index == 2)]
        body = request(padding, prompt_tokens, scope)
        identity = {"index": index, "repeat": repeat, "phase": PHASES[phase_index]}

        def intent():
            require(clock() < deadline)
            record({"kind": "intent", **identity})

        sample = client.collect(body, credential, prompt_tokens=prompt_tokens,
                                deadline=deadline, before_send=intent)
        require(type(sample) is meter.Sample)
        row = {**identity, "attempted": client.last_attempt_started,
               "measurement": sample.metadata()}
        record({"kind": "result", **row})
        rows.append(row)
        if sample.status != "completed":
            stop = "request-failed"
            break
        if sample.cached_prompt_tokens is None:
            stop = "cache-usage-missing"
            break
        if phase_index != 1 and sample.cached_prompt_tokens != 0:
            stop = "unexpected-cache-reuse"
            break
    ended = clock()
    if ended >= deadline:
        stop = "deadline"
    attempted = [row for row in rows if row["attempted"]]
    measurements = [row["measurement"] for row in attempted]
    result = {"kind": "summary", "stop_reason": stop, "completed": stop == "complete",
              "elapsed_seconds": ended - started, "planned_requests": REQUESTS,
              "attempted_requests": len(attempted), "not_started_requests": REQUESTS - len(attempted),
              "requests_with_unknown_usage": sum(row["prompt_tokens"] is None or
                  row["completion_tokens"] is None for row in measurements),
              "requests_with_unknown_cache_usage": sum(row["cached_prompt_tokens"] is None
                                                        for row in measurements),
              "known_prompt_tokens": sum(row["prompt_tokens"] or 0 for row in measurements),
              "known_completion_tokens": sum(row["completion_tokens"] or 0 for row in measurements),
              "known_cached_prompt_tokens": sum(row["cached_prompt_tokens"] or 0 for row in measurements),
              "warm_requests_with_reuse": sum(row["phase"] == "warm" and
                  (row["measurement"]["cached_prompt_tokens"] or 0) > 0 for row in attempted),
              "billing_reconciled": False, "cache_isolation_qualified": False,
              "quality_qualified": False, "confidential": False, "tariff_change_justified": False}
    record(result)
    return result
