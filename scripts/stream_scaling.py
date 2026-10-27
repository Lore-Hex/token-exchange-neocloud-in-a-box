# SPDX-License-Identifier: Apache-2.0
"""Finite fresh-salt C16/C32/C64 waves under the existing owned endpoint."""

import hashlib
import os
import signal
import subprocess
import sys
import threading
import time

import stream_c8 as c8
import stream_capacity as capacity
import stream_cohort as cohort
import stream_lab as binding

stream, meter, require = cohort.stream, cohort.meter, cohort.require
SCHEMA = "nib.chat-stream-scaling/v1"
CONCURRENCIES, CONDITIONS = (16, 32, 64), ("isolated",)
SECONDS, ADMISSION_SECONDS, MIN_MEASURED_SECONDS = 900, 150, 120
MAX_REQUESTS = 4096
TOTAL_REQUESTS = 1 + len(CONCURRENCIES) * MAX_REQUESTS
RESERVED_TOKENS = TOTAL_REQUESTS * (cohort.PROMPT_TOKENS + cohort.OUTPUT_TOKENS)
PLATEAU_RESERVE = ADMISSION_SECONDS + c8.START_SECONDS + stream.REQUEST_SECONDS + c8.CLEANUP_SECONDS + 2 * capacity.SECONDS
STOPS = ("complete", "deadline", "cancelled", "warmup-failed", "collector-failed", "request-failed",
         "capacity-failed", "identity-failed", "insufficient-duration", "request-cap", "cache-mismatch")


def spawn(prepared_sha256=None):
    command = ["/opt/nib/python", "scaling-collector"]
    if prepared_sha256 is not None:
        binding.sha(prepared_sha256)
        command = ["/usr/bin/python3.12", "-I", "-S", "-B",
                   "/opt/nib-service/scripts/selected_combined_worker.py", prepared_sha256, "collect"]
    return subprocess.Popen(command, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)


def salt(nonce, condition, request, index):
    binding.sha(nonce)
    require(condition in CONDITIONS and type(request) is int and 0 <= request <= TOTAL_REQUESTS
            and type(index) is int and 0 <= index < cohort.TOTAL_REQUESTS)
    return hashlib.sha256((nonce + ":" + condition + ":" + str(request)).encode()).hexdigest()


def child(model, *, selected_model=False):
    require(threading.current_thread() is threading.main_thread())
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, c8.cancelled)
    raw = sys.stdin.buffer.readline(c8.PACKET_BYTES + 1)
    require(len(raw) <= c8.PACKET_BYTES and raw.endswith(b"\n"))
    value = stream.worker.decode(raw)
    require(type(value) is dict and set(value) == {"process", "index", "padding", "deadline", "epoch", "salt"})
    process = value["process"]
    stream.identity.keys(process, stream.identity.PROCESS)
    for key in ("pid", "start_ticks", "netns_inode", "listener_inode"):
        require(type(process[key]) is int and 0 < process[key] < 2**63)
    for key in ("boot_id_sha256", "exe_sha256", "argv_sha256"):
        binding.sha(process[key])
    binding.sha(value["salt"])
    require(type(value["index"]) is int and 1 <= value["index"] <= cohort.MEASURED_REQUESTS
            and meter.number(value["epoch"]) and meter.number(value["deadline"])
            and value["epoch"] <= time.monotonic() < value["deadline"]
            and value["deadline"] - value["epoch"] <= SECONDS)
    messages = cohort.messages(value["index"], value["padding"])
    client = stream.OwnedStreamHTTP(stream.identity.Endpoint(stream.PORT, {"process": process}),
                                  model=model, selected_model=selected_model)
    require(os.write(1, c8.READY) == len(c8.READY))
    require(sys.stdin.buffer.read(1) == c8.GO and sys.stdin.buffer.read(1) == b"")
    started = time.monotonic()
    sample = client.collect(messages, prompt_tokens=cohort.PROMPT_TOKENS, max_output_tokens=cohort.OUTPUT_TOKENS,
                            deadline=value["deadline"], cache_salt=value["salt"])
    raw = stream.worker.bounded_json({"start": started - value["epoch"], "end": time.monotonic() - value["epoch"],
                                     "sample": sample.metadata()}, c8.PACKET_BYTES)
    require(os.write(1, raw) == len(raw))


def wave(endpoint, workload, record, *, concurrency, deadline, admission_end, epoch, model, nonce, condition, offset,
         prepared_sha256=None):
    """The legacy C8 pipe framing/reap protocol; no inherited owner descriptors."""
    workers, stop, reaped = [], "collector-failed", False
    start = time.monotonic()
    request_deadline = min(deadline - c8.CLEANUP_SECONDS, admission_end + stream.REQUEST_SECONDS)
    try:
        with stream.worker.alarm(request_deadline):
            setup = min(admission_end, time.monotonic() + c8.START_SECONDS)
            for slot in range(concurrency):
                require(time.monotonic() < setup)
                factory = spawn if prepared_sha256 is None else lambda: spawn(prepared_sha256)
                row = c8.start_collector(workers, slot, factory=factory)
                process = row["process"]
                os.set_blocking(process.stdout.fileno(), False)
                os.set_blocking(process.stdin.fileno(), False)
                index = slot % cohort.MEASURED_REQUESTS + 1
                raw = stream.worker.bounded_json({"process": endpoint.expected, "index": index,
                    "padding": workload.cases[index][0], "deadline": request_deadline, "epoch": epoch,
                    "salt": salt(nonce, condition, offset + slot + 1, index)}, c8.PACKET_BYTES)
                require(os.write(process.stdin.fileno(), raw) == len(raw))
            while not all(row["ready"] for row in workers):
                require(time.monotonic() < setup and not c8.failed(workers))
                c8.drain(workers, epoch, min(.02, setup - time.monotonic()))
            for row in workers:
                require(not c8.failed(workers))
                if time.monotonic() >= admission_end:
                    break
                row["dispatch"] = time.monotonic() - epoch
                require(os.write(row["process"].stdin.fileno(), c8.GO) == len(c8.GO))
                row["process"].stdin.close()
                c8.drain(workers, epoch)
            stop = "request-failed"
            released = [row for row in workers if row["dispatch"] is not None]
            while not all(row["eof"] for row in released) and not c8.failed(released):
                if time.monotonic() >= request_deadline:
                    stop = "deadline"
                    break
                c8.drain(workers, epoch, min(.02, request_deadline - time.monotonic()))
            if not c8.failed(released) and all(row["packet"] is not None for row in released):
                stop = "complete"
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            stop = "cancelled"
        elif isinstance(exc, TimeoutError) or isinstance(exc, stream.worker.Abort) and exc.status == "timeout":
            stop = "deadline"
        elif time.monotonic() >= admission_end and not any(row["dispatch"] is not None for row in workers):
            stop = "complete"
    finally:
        handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM)}
        for sig in handlers:
            signal.signal(sig, signal.SIG_IGN)
        try:
            reaped = c8.cleanup(workers, epoch, deadline)
            ended = time.monotonic() - epoch
            samples, intervals = [], []
            for row in workers:
                if row["dispatch"] is None:
                    continue
                packet = row["packet"]
                if packet is None:
                    status = "timeout" if stop == "deadline" else "cancelled" if stop == "cancelled" else "transport-error"
                    sample = meter.Sample(status, ended - row["dispatch"], None, None, None, 0, None, None, None, None).metadata()
                else:
                    sample = packet["sample"]
                samples.append(sample)
                intervals.append({"index": row["index"], "start": packet["start"] if packet else row["dispatch"],
                    "dispatch": row["dispatch"], "end": packet["end"] if packet else ended,
                    "reported": packet is not None, "exit_code": row["process"].returncode})
            if stop == "complete" and (not reaped or any(i["exit_code"] != 0 for i in intervals)):
                stop = "collector-failed"
            if stop == "complete" and any(s["status"] != "completed" for s in samples):
                stop = "request-failed"
            record.update(start=start - epoch, end=ended, samples=samples, intervals=intervals,
                          collectors_reaped=reaped, stop_reason=stop, peak_in_flight=c8.peak(intervals),
                          dispatch_unknown_requests=0)
        finally:
            c8.restore_handlers(handlers)


def summarize(plateau):
    samples = [sample for wave in plateau["waves"] for sample in wave["samples"]]
    unknown = sum(w["dispatch_unknown_requests"] for w in plateau["waves"])
    plateau["started_requests"] = len(samples)
    plateau["dispatch_unknown_requests"] = unknown
    plateau["not_run_requests"] = MAX_REQUESTS - len(samples) - unknown
    plateau["collectors_reaped"] = all(w["collectors_reaped"] for w in plateau["waves"])
    plateau["peak_in_flight"] = max((w["peak_in_flight"] for w in plateau["waves"]), default=0)
    plateau["measured"] = (meter._summarize_rows(samples, scheduled=MAX_REQUESTS,
        wall_seconds=plateau["end"] - plateau["start"], gpu_count=8)
        if (samples or unknown) and plateau["end"] > plateau["start"] else None)
    if plateau["measured"] is not None:
        plateau["measured"].update(dispatch_unknown_requests=unknown)
        plateau["measured"]["not_run"] -= unknown
        plateau["measured"]["requests_with_unknown_usage"] += unknown
        if unknown:
            plateau["measured"]["started_error_rate"] = None
    plateau["known_cached_prompt_tokens"] = sum(s["cached_prompt_tokens"] or 0 for s in samples)
    plateau["cached_usage_unknown_requests"] = unknown + sum(s["cached_prompt_tokens"] is None for s in samples)


def empty(model, workload, condition):
    return {"schema": SCHEMA, "model": model, "workload": workload.metadata(), "condition": condition,
        "concurrencies": list(CONCURRENCIES), "duration_limit_seconds": SECONDS,
        "admission_seconds": ADMISSION_SECONDS, "minimum_measured_seconds": MIN_MEASURED_SECONDS,
        "max_requests_per_plateau": MAX_REQUESTS, "reserved_requests": TOTAL_REQUESTS,
        "reserved_total_tokens": RESERVED_TOKENS, "warmup": None, "capacity": None, "plateaus": [],
        "elapsed_seconds": 0.0, "completed": False, "stop_reason": "deadline",
        "known_prompt_tokens": 0, "known_output_tokens": 0, "requests_with_unknown_usage": 0,
        "started_requests": 0, "dispatch_unknown_requests": 0, "not_run_requests": TOTAL_REQUESTS, "collectors_reaped": True,
        "rank_identity": "unknown", "capacity_scope": "api-shared-cache-config",
        "graph_capture_observed": False, "saturation_demonstrated": False,
        "pricing_measured": False, "quality_accepted": False, "confidential": False}


def totals(row):
    samples = ([row["warmup"]] if row["warmup"] is not None else []) + [s for p in row["plateaus"] for w in p["waves"] for s in w["samples"]]
    unknown = sum(p["dispatch_unknown_requests"] for p in row["plateaus"])
    row.update(started_requests=len(samples), dispatch_unknown_requests=unknown,
        not_run_requests=TOTAL_REQUESTS - len(samples) - unknown,
        known_prompt_tokens=sum(s["prompt_tokens"] or 0 for s in samples),
        known_output_tokens=sum(s["completion_tokens"] or 0 for s in samples),
        requests_with_unknown_usage=unknown + sum(s["completion_tokens"] is None for s in samples),
        collectors_reaped=all(p["collectors_reaped"] for p in row["plateaus"]))


def run(endpoint, workload, *, deadline, gpu_count, cohort_seconds, model, condition, combined=None):
    require(threading.current_thread() is threading.main_thread() and type(endpoint) is stream.identity.Endpoint
            and type(workload) is cohort.Workload and type(gpu_count) is int and gpu_count == 8
            and type(cohort_seconds) is int and cohort_seconds == SECONDS
            and condition in CONDITIONS and meter.number(deadline))
    workload.validate()
    policy = capacity
    prepared_sha256 = None
    if combined is not None:
        import selected_combined_owner
        require(type(combined) is selected_combined_owner.Run and model == stream.SELECTED_MODEL
                and combined.supervisor is not None and combined.used
                and endpoint.expected == combined.identities["engine"])
        policy, prepared_sha256 = combined, combined.prepared_sha256
    row, epoch = empty(model, workload, condition), time.monotonic()
    if combined is not None:
        row.update(schema="nib.selected-combined-scaling/v1", prepared_sha256=prepared_sha256,
                   engine_identity_sha256=binding.digest(endpoint.expected))
        combined.scaling_row = row
    deadline = min(deadline, epoch + SECONDS)
    nonce = os.urandom(32).hex()
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    warmup_start = None
    try:
        for sig in handlers:
            signal.signal(sig, c8.cancelled)
        require(deadline - time.monotonic() > stream.REQUEST_SECONDS + PLATEAU_RESERVE)
        row["stop_reason"] = "capacity-failed"
        row["capacity"] = policy.probe(endpoint, model, deadline - c8.CLEANUP_SECONDS)
        policy.stable(row["capacity"], row["capacity"])
        row["stop_reason"] = "warmup-failed"
        warmup_start = time.monotonic()
        row["warmup"] = stream.OwnedStreamHTTP(endpoint, model=model, **({"selected_model": True} if combined else {})).collect(
            cohort.messages(0, workload.cases[0][0]), prompt_tokens=cohort.PROMPT_TOKENS,
            max_output_tokens=cohort.OUTPUT_TOKENS, deadline=deadline - c8.CLEANUP_SECONDS,
            cache_salt=salt(nonce, condition, 0, 0)).metadata()
        require(row["warmup"]["status"] == "completed")
        row["stop_reason"] = "cache-mismatch"
        require(row["warmup"]["cached_prompt_tokens"] == 0)
        for concurrency in CONCURRENCIES:
            row["stop_reason"] = "deadline"
            require(deadline - time.monotonic() > PLATEAU_RESERVE)
            row["stop_reason"] = "identity-failed"
            endpoint.check(deadline - c8.CLEANUP_SECONDS)
            start = time.monotonic()
            p = {"concurrency": concurrency, "start": start - epoch, "end": start - epoch,
                 "admission_end": start - epoch + ADMISSION_SECONDS, "waves": [], "capacity_after": None,
                 "stop_reason": "collector-failed"}
            row["plateaus"].append(p)
            try:
                offset = (len(row["plateaus"]) - 1) * MAX_REQUESTS
                while time.monotonic() < start + ADMISSION_SECONDS:
                    row["stop_reason"] = "request-cap"
                    require(sum(len(w["samples"]) for w in p["waves"]) + concurrency <= MAX_REQUESTS)
                    row["stop_reason"] = "identity-failed"
                    endpoint.check(deadline - c8.CLEANUP_SECONDS)
                    w = {"start": time.monotonic() - epoch, "end": time.monotonic() - epoch,
                         "samples": [], "intervals": [], "collectors_reaped": False,
                         "stop_reason": "collector-failed", "peak_in_flight": 0,
                         "dispatch_unknown_requests": concurrency}
                    p["waves"].append(w)
                    row["stop_reason"] = "collector-failed"
                    options = {"prepared_sha256": prepared_sha256} if combined else {}
                    wave(endpoint, workload, w, concurrency=concurrency, deadline=deadline,
                         admission_end=start + ADMISSION_SECONDS, epoch=epoch, model=model,
                         nonce=nonce, condition=condition, offset=offset, **options)
                    offset += concurrency
                    row["stop_reason"] = w["stop_reason"]
                    require(w["stop_reason"] == "complete")
                    row["stop_reason"] = "cache-mismatch"
                    require(all(s["cached_prompt_tokens"] == 0 for s in w["samples"]))
                    row["stop_reason"] = "collector-failed"
                row["stop_reason"] = "capacity-failed"
                p["capacity_after"] = policy.probe(endpoint, model, deadline - c8.CLEANUP_SECONDS)
                policy.stable(row["capacity"], p["capacity_after"])
                p["end"] = time.monotonic() - epoch
                summarize(p)
                row["stop_reason"] = "insufficient-duration"
                require(p["measured"] is not None and p["measured"]["wall_seconds"] >= MIN_MEASURED_SECONDS
                        and p["peak_in_flight"] == concurrency)
                row["stop_reason"] = "cache-mismatch"
                require(p["known_cached_prompt_tokens"] == 0 and p["cached_usage_unknown_requests"] == 0)
                p["stop_reason"] = "complete"
            finally:
                p["end"] = time.monotonic() - epoch
                if p["stop_reason"] != "complete":
                    p["stop_reason"] = row["stop_reason"]
                summarize(p)
        row.update(completed=True, stop_reason="complete")
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            row.update(completed=False, stop_reason="cancelled")
        elif isinstance(exc, TimeoutError) or isinstance(exc, stream.worker.Abort) and exc.status == "timeout":
            row.update(completed=False, stop_reason="deadline")
    finally:
        for sig in handlers:
            signal.signal(sig, signal.SIG_IGN)
        try:
            row["elapsed_seconds"] = time.monotonic() - epoch
            if warmup_start is not None and row["warmup"] is None:
                row["warmup"] = meter.Sample("transport-error", time.monotonic() - warmup_start,
                    None, None, None, 0, None, None, None, None).metadata()
            totals(row)
            if row["completed"] and (time.monotonic() >= deadline or not row["collectors_reaped"]):
                row.update(completed=False, stop_reason="deadline")
            if not row["completed"] and row["stop_reason"] == "complete":
                row["stop_reason"] = "collector-failed"
        finally:
            try:
                c8.restore_handlers(handlers)
            except (KeyboardInterrupt, SystemExit):
                row.update(completed=False, stop_reason="cancelled")
    return row


def validate(row, contract, loaded_cohort, model, condition):
    return _validate(row, contract, loaded_cohort, model, condition, combined=False)


def validate_combined(row):
    return _validate(row, {}, cohort, stream.SELECTED_MODEL, "isolated", combined=True)


def _validate(row, contract, loaded_cohort, model, condition, *, combined):
    require(type(row) is dict)
    policy = capacity
    if combined:
        import selected_combined_capacity
        policy = selected_combined_capacity
    work = binding.workload(row.get("workload"), loaded_cohort)
    template = empty(model, work, condition)
    if combined:
        for key in ("prepared_sha256", "engine_identity_sha256"):
            binding.sha(row.get(key))
        template.update(schema="nib.selected-combined-scaling/v1",
            prepared_sha256=row["prepared_sha256"], engine_identity_sha256=row["engine_identity_sha256"])
    variable = {"warmup", "capacity", "plateaus", "elapsed_seconds", "completed", "stop_reason",
                "known_prompt_tokens", "known_output_tokens", "requests_with_unknown_usage",
                "started_requests", "dispatch_unknown_requests", "not_run_requests", "collectors_reaped"}
    require(set(row) == set(template) and all(type(row[k]) is type(v) and row[k] == v
            for k, v in template.items() if k not in variable)
            and type(row["plateaus"]) is list and len(row["plateaus"]) <= len(CONCURRENCIES)
            and meter.number(row["elapsed_seconds"]) and row["elapsed_seconds"] <= binding.MAX_SECONDS
            and type(row["completed"]) is bool and type(row["collectors_reaped"]) is bool
            and row["stop_reason"] in STOPS and row["completed"] == (row["stop_reason"] == "complete"))
    if row["capacity"] is not None:
        policy.stable(row["capacity"], row["capacity"])
    if row["warmup"] is not None:
        binding.validate_sample(row["warmup"], loaded_cohort)
        require(row["warmup"]["elapsed_seconds"] <= row["elapsed_seconds"])
    previous = row["warmup"]["elapsed_seconds"] if row["warmup"] is not None else 0
    for index, p in enumerate(row["plateaus"]):
        require(type(p) is dict and set(p) == {"concurrency", "start", "end", "admission_end", "waves",
            "capacity_after", "stop_reason", "started_requests", "dispatch_unknown_requests", "not_run_requests",
            "collectors_reaped", "peak_in_flight", "measured", "known_cached_prompt_tokens", "cached_usage_unknown_requests"}
            and type(p["concurrency"]) is int and p["concurrency"] == CONCURRENCIES[index]
            and all(meter.number(p[k]) for k in ("start", "end", "admission_end"))
            and previous <= p["start"] <= p["end"] <= row["elapsed_seconds"]
            and p["admission_end"] == p["start"] + ADMISSION_SECONDS
            and type(p["waves"]) is list and len(p["waves"]) <= MAX_REQUESTS // p["concurrency"] + 1
            and p["stop_reason"] in STOPS and row["capacity"] is not None and row["warmup"] is not None
            and row["warmup"]["status"] == "completed")
        previous = p["end"]
        end = p["start"]
        for wi, w in enumerate(p["waves"]):
            require(type(w) is dict and set(w) == {"start", "end", "samples", "intervals", "collectors_reaped",
                "stop_reason", "peak_in_flight", "dispatch_unknown_requests"}
                and meter.number(w["start"]) and meter.number(w["end"])
                and end <= w["start"] <= w["end"] <= p["end"]
                and type(w["samples"]) is list and type(w["intervals"]) is list
                and len(w["samples"]) == len(w["intervals"]) <= p["concurrency"]
                and type(w["collectors_reaped"]) is bool and w["stop_reason"] in STOPS
                and type(w["dispatch_unknown_requests"]) is int and w["dispatch_unknown_requests"] in (0, p["concurrency"]))
            end = w["end"]
            if w["dispatch_unknown_requests"]:
                require(not w["samples"] and not w["collectors_reaped"] and w["stop_reason"] == "collector-failed")
            for slot, (s, interval) in enumerate(zip(w["samples"], w["intervals"])):
                binding.validate_sample(s, loaded_cohort)
                require(type(interval) is dict and set(interval) == {"index", "start", "dispatch", "end", "reported", "exit_code"}
                    and type(interval["index"]) is int and interval["index"] == slot
                    and type(interval["reported"]) is bool
                    and all(meter.number(interval[k]) for k in ("start", "dispatch", "end"))
                    and w["start"] <= interval["dispatch"] <= interval["start"] <= interval["end"] <= w["end"]
                    and interval["dispatch"] < p["admission_end"]
                    and s["elapsed_seconds"] <= interval["end"] - interval["start"] + 1e-6
                    and (type(interval["exit_code"]) is int and -255 <= interval["exit_code"] <= 255
                         or interval["exit_code"] is None and not w["collectors_reaped"]))
                require(interval["reported"] or s["status"] != "completed" and s["prompt_tokens"] is None)
            require(type(w["peak_in_flight"]) is int and w["peak_in_flight"] == c8.peak(w["intervals"]) <= p["concurrency"])
            if w["stop_reason"] == "complete":
                require(w["collectors_reaped"] and not w["dispatch_unknown_requests"]
                        and all(s["status"] == "completed" for s in w["samples"])
                        and all(i["exit_code"] == 0 for i in w["intervals"]))
            else:
                require(wi == len(p["waves"]) - 1 and p["stop_reason"] != "complete")
        require(type(p["started_requests"]) is int and type(p["dispatch_unknown_requests"]) is int
                and 0 <= p["started_requests"] + p["dispatch_unknown_requests"] <= MAX_REQUESTS)
        expected = dict(p)
        summarize(expected)
        require(binding.digest(expected) == binding.digest(p))
        if p["capacity_after"] is not None:
            policy.stable(p["capacity_after"], p["capacity_after"])
        if p["stop_reason"] == "complete":
            policy.stable(row["capacity"], p["capacity_after"])
            require(p["capacity_after"] is not None and p["end"] >= p["admission_end"]
                    and p["measured"] is not None and p["measured"]["wall_seconds"] >= MIN_MEASURED_SECONDS
                    and p["peak_in_flight"] == p["concurrency"] and p["collectors_reaped"]
                    and all(w["stop_reason"] == "complete" for w in p["waves"])
                    and p["known_cached_prompt_tokens"] == 0 and p["cached_usage_unknown_requests"] == 0)
        else:
            require(index == len(row["plateaus"]) - 1 and not row["completed"])
    expected = dict(row)
    totals(expected)
    require(binding.digest(expected) == binding.digest(row))
    if row["completed"]:
        require(len(row["plateaus"]) == 3 and row["collectors_reaped"] and row["elapsed_seconds"] < SECONDS
                and all(p["stop_reason"] == "complete" for p in row["plateaus"])
                and row["warmup"]["cached_prompt_tokens"] == 0)
    return row["completed"]
