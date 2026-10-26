# SPDX-License-Identifier: Apache-2.0
"""Fixed repeated C8 waves on the existing source-bound owned lab endpoint."""

import signal
import threading
import time

import stream_c8 as c8
import stream_cohort as cohort
import stream_lab as binding

stream, meter, require = cohort.stream, cohort.meter, cohort.require
SCHEMA = "nib.chat-stream-sustained/v1"
WAVES, MIN_MEASURED_SECONDS = 32, 120
SECONDS = cohort.EXTENDED_COHORT_SECONDS
MEASURED_REQUESTS = WAVES * cohort.MEASURED_REQUESTS
TOTAL_REQUESTS = cohort.WARMUP_REQUESTS + MEASURED_REQUESTS
RESERVED_TOKENS = TOTAL_REQUESTS * (cohort.PROMPT_TOKENS + cohort.OUTPUT_TOKENS)
STOPS = ("complete", "deadline", "cancelled", "warmup-failed", "wave-failed",
         "identity-failed", "insufficient-duration", "internal")


def empty(model, workload):
    return {"schema": SCHEMA, "model": model, "workload": workload.metadata(), "concurrency": 8,
        "prompt_tokens_per_request": cohort.PROMPT_TOKENS, "max_output_tokens_per_request": cohort.OUTPUT_TOKENS,
        "reserved_waves": WAVES, "reserved_requests": TOTAL_REQUESTS, "reserved_total_tokens": RESERVED_TOKENS,
        "duration_limit_seconds": SECONDS, "minimum_measured_seconds": MIN_MEASURED_SECONDS,
        "warmup": None, "waves": [], "measurement_start_seconds": None, "elapsed_seconds": 0.0,
        "started_requests": 0, "dispatch_unknown_requests": 0, "not_run_requests": TOTAL_REQUESTS,
        "known_prompt_tokens": 0, "known_output_tokens": 0, "requests_with_unknown_usage": 0,
        "measured": None, "collectors_reaped": True, "completed": False, "stop_reason": "deadline",
        "cache_state": "uncontrolled-repeated-fixture", "rank_identity": "unknown",
        "pricing_measured": False, "quality_accepted": False, "confidential": False,
        "sweep_accepted": False, "saturation_demonstrated": False}


def summarize(row):
    samples = [sample for wave in row["waves"] if wave["cohort"] is not None
               for sample in wave["cohort"]["samples"]]
    all_samples = ([row["warmup"]] if row["warmup"] is not None else []) + samples
    unknown = sum(wave["cohort"] is None for wave in row["waves"]) * cohort.MEASURED_REQUESTS
    row.update(started_requests=len(all_samples), dispatch_unknown_requests=unknown,
        not_run_requests=TOTAL_REQUESTS - len(all_samples) - unknown,
        known_prompt_tokens=sum(sample["prompt_tokens"] or 0 for sample in all_samples),
        known_output_tokens=sum(sample["completion_tokens"] or 0 for sample in all_samples),
        requests_with_unknown_usage=unknown + sum(sample["completion_tokens"] is None for sample in all_samples),
        collectors_reaped=all(wave["cohort"] is not None and wave["cohort"]["collectors_reaped"] for wave in row["waves"]))
    start = row["measurement_start_seconds"]
    row["measured"] = (meter._summarize_rows(samples, scheduled=MEASURED_REQUESTS,
        wall_seconds=row["elapsed_seconds"] - start, gpu_count=8) if start is not None else None)
    if row["measured"] is not None:
        row["measured"]["dispatch_unknown_requests"] = unknown
        row["measured"]["not_run"] -= unknown
        row["measured"]["requests_with_unknown_usage"] += unknown
        if unknown:
            row["measured"]["started_error_rate"] = None


def run(endpoint, workload, *, deadline, gpu_count, cohort_seconds, model):
    require(threading.current_thread() is threading.main_thread() and type(endpoint) is stream.identity.Endpoint
            and endpoint.port == stream.PORT and type(workload) is cohort.Workload
            and type(gpu_count) is int and gpu_count == 8 and type(cohort_seconds) is int
            and cohort_seconds == SECONDS and meter.number(deadline))
    workload.validate()
    row, epoch = empty(model, workload), time.monotonic()
    deadline = min(deadline, epoch + SECONDS)
    warmup_start = None
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        for sig in handlers:
            signal.signal(sig, c8.cancelled)
        if time.monotonic() >= deadline - c8.CLEANUP_SECONDS:
            raise TimeoutError
        with stream.worker.alarm(deadline - c8.CLEANUP_SECONDS):
            row["stop_reason"] = "identity-failed"
            endpoint.check(deadline - c8.CLEANUP_SECONDS)
            client = stream.OwnedStreamHTTP(endpoint, model=model)
            warmup_start = time.monotonic()
            row["stop_reason"] = "warmup-failed"
            row["warmup"] = cohort._collect(client, workload, 0, deadline - c8.CLEANUP_SECONDS).metadata()
        if row["warmup"]["status"] != "completed":
            row["stop_reason"] = "warmup-failed"
        else:
            for index in range(WAVES):
                # Do not knowingly admit a wave without its setup/request/reap
                # reservation. The original stage cutoff is never renewed.
                if deadline - time.monotonic() <= c8.START_SECONDS + stream.REQUEST_SECONDS + c8.CLEANUP_SECONDS:
                    break
                row["stop_reason"] = "identity-failed"
                endpoint.check(deadline - c8.CLEANUP_SECONDS)
                start = time.monotonic() - epoch
                if row["measurement_start_seconds"] is None:
                    row["measurement_start_seconds"] = start
                wave = {"index": index, "start": start, "end": start, "cohort": {}}
                row["waves"].append(wave)
                row["stop_reason"] = "wave-failed"
                try:
                    c8.wave(endpoint, workload, wave["cohort"], deadline=deadline, model=model)
                finally:
                    wave["end"] = time.monotonic() - epoch
                    if not wave["cohort"]:
                        # A lost executor result leaves all eight dispatches
                        # uncertain. Never turn that uncertainty into free work.
                        wave["cohort"] = None
                if not wave["cohort"]["completed"]:
                    break
                row["stop_reason"] = "identity-failed"
                endpoint.check(deadline - c8.CLEANUP_SECONDS)
                row["stop_reason"] = "deadline"
            else:
                row["stop_reason"] = "complete"
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            row["stop_reason"] = "cancelled"
        elif isinstance(exc, TimeoutError) or isinstance(exc, stream.worker.Abort) and exc.status == "timeout":
            row["stop_reason"] = "deadline"
        elif row["stop_reason"] not in ("identity-failed", "wave-failed"):
            row["stop_reason"] = "internal"
    finally:
        # Only bounded metadata finalization runs with cancellation masked.
        for sig in handlers:
            signal.signal(sig, signal.SIG_IGN)
        try:
            row["elapsed_seconds"] = time.monotonic() - epoch
            if warmup_start is not None and row["warmup"] is None:
                row["warmup"] = meter.Sample("cancelled" if row["stop_reason"] == "cancelled" else "transport-error",
                    time.monotonic() - warmup_start, None, None, None, 0, None, None, None, None).metadata()
            summarize(row)
            if row["stop_reason"] == "complete":
                if time.monotonic() >= deadline:
                    row["stop_reason"] = "deadline"
                elif row["measured"]["wall_seconds"] < MIN_MEASURED_SECONDS:
                    row["stop_reason"] = "insufficient-duration"
            row["completed"] = row["stop_reason"] == "complete"
        finally:
            try:
                c8.restore_handlers(handlers)
            except (KeyboardInterrupt, SystemExit):
                row.update(completed=False, stop_reason="cancelled")
    return row


def validate(row, contract, loaded_cohort, model):
    require(type(row) is dict)
    work = binding.workload(row.get("workload"), loaded_cohort)
    template = empty(model, work)
    require(set(row) == set(template))
    variable = {"warmup", "waves", "measurement_start_seconds", "elapsed_seconds", "started_requests",
        "dispatch_unknown_requests", "not_run_requests", "known_prompt_tokens", "known_output_tokens",
        "requests_with_unknown_usage", "measured", "collectors_reaped", "completed", "stop_reason"}
    require(all(type(row[key]) is type(value) and row[key] == value
                for key, value in template.items() if key not in variable))
    require(type(row["waves"]) is list and len(row["waves"]) <= WAVES
            and type(row["completed"]) is bool and type(row["collectors_reaped"]) is bool
            and row["stop_reason"] in STOPS and row["completed"] == (row["stop_reason"] == "complete")
            and meter.number(row["elapsed_seconds"]) and row["elapsed_seconds"] <= binding.MAX_SECONDS)
    if row["warmup"] is not None:
        binding.validate_sample(row["warmup"], loaded_cohort)
        require(row["warmup"]["elapsed_seconds"] <= row["elapsed_seconds"])
    start = row["measurement_start_seconds"]
    require((start is None) == (not row["waves"]))
    previous = 0
    for index, wave in enumerate(row["waves"]):
        require(type(wave) is dict and set(wave) == {"index", "start", "end", "cohort"}
                and type(wave["index"]) is int and wave["index"] == index
                and meter.number(wave["start"]) and meter.number(wave["end"])
                and previous <= wave["start"] <= wave["end"] <= row["elapsed_seconds"]
                and row["warmup"] is not None and row["warmup"]["status"] == "completed"
                and row["warmup"]["elapsed_seconds"] <= wave["start"])
        previous = wave["end"]
        if index == 0:
            require(type(start) in (int, float) and type(start) is not bool and start == wave["start"])
        if wave["cohort"] is None:
            require(index == len(row["waves"]) - 1 and row["stop_reason"] in ("wave-failed", "cancelled", "deadline"))
            continue
        complete = binding.validate_cohort(wave["cohort"], contract, loaded_cohort, model, wave=True)
        require(wave["cohort"]["workload"] == row["workload"]
                and wave["cohort"]["elapsed_seconds"] <= wave["end"] - wave["start"] + 1e-6
                and (complete or index == len(row["waves"]) - 1
                     and row["stop_reason"] in ("wave-failed", "cancelled", "deadline")))
    for key in ("started_requests", "dispatch_unknown_requests", "not_run_requests", "known_prompt_tokens",
                "known_output_tokens", "requests_with_unknown_usage"):
        require(meter.integer(row[key], RESERVED_TOKENS))
    if start is not None:
        require(row["elapsed_seconds"] > start)
    expected = dict(row)
    summarize(expected)
    require(binding.digest(expected) == binding.digest(row))
    stop = row["stop_reason"]
    if stop in ("complete", "insufficient-duration"):
        require(len(row["waves"]) == WAVES and row["collectors_reaped"]
                and all(wave["cohort"] is not None and wave["cohort"]["completed"] for wave in row["waves"])
                and row["elapsed_seconds"] < SECONDS
                and (row["measured"]["wall_seconds"] >= MIN_MEASURED_SECONDS) == (stop == "complete"))
    if stop == "warmup-failed":
        require(row["warmup"] is not None and row["warmup"]["status"] != "completed" and not row["waves"])
    if row["warmup"] is not None and row["warmup"]["status"] != "completed":
        require(not row["waves"] and stop in ("warmup-failed", "cancelled", "deadline", "internal"))
    return row["completed"]
