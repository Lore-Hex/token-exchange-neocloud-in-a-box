# SPDX-License-Identifier: Apache-2.0
"""One bounded c8 burst; private metadata pipes, never an arbitrary endpoint."""

import os
import select
import signal
import subprocess
import sys
import threading
import time

import stream_cohort as cohort
import stream_lab as binding

stream, meter, require = cohort.stream, cohort.meter, cohort.require
SCHEMA = "nib.chat-stream-cohort/v2"
READY, GO = b"R\n", b"G"
PACKET_BYTES, START_SECONDS, CLEANUP_SECONDS = 4096, 10, 3


def cancelled(*_):
    raise KeyboardInterrupt


def child(model):
    """Only the authenticated lab wrapper calls this, with its selected model."""
    require(threading.current_thread() is threading.main_thread())
    signal.signal(signal.SIGINT, cancelled)
    signal.signal(signal.SIGTERM, cancelled)
    raw = sys.stdin.buffer.readline(PACKET_BYTES + 1)
    require(len(raw) <= PACKET_BYTES and raw.endswith(b"\n"))
    value = stream.worker.decode(raw)
    require(type(value) is dict and set(value) == {"process", "index", "padding", "deadline", "epoch"})
    process = value["process"]
    stream.identity.keys(process, stream.identity.PROCESS)
    for key in ("pid", "start_ticks", "netns_inode", "listener_inode"):
        require(type(process[key]) is int and 0 < process[key] < 2**63)
    for key in ("boot_id_sha256", "exe_sha256", "argv_sha256"):
        binding.sha(process[key])
    require(type(value["index"]) is int and 1 <= value["index"] <= cohort.MEASURED_REQUESTS
            and meter.number(value["epoch"]) and meter.number(value["deadline"])
            and value["epoch"] <= time.monotonic() < value["deadline"]
            and value["deadline"] - value["epoch"] <= cohort.EXTENDED_COHORT_SECONDS)
    messages = cohort.messages(value["index"], value["padding"])
    endpoint = stream.identity.Endpoint(stream.PORT, {"process": process})
    client = stream.OwnedStreamHTTP(endpoint, model=model)
    require(os.write(1, READY) == len(READY))
    require(sys.stdin.buffer.read(1) == GO and sys.stdin.buffer.read(1) == b"")
    started = time.monotonic()
    sample = client.collect(messages, prompt_tokens=cohort.PROMPT_TOKENS,
                            max_output_tokens=cohort.OUTPUT_TOKENS, deadline=value["deadline"])
    packet = {"start": started - value["epoch"], "end": time.monotonic() - value["epoch"],
              "sample": sample.metadata()}
    raw = stream.worker.bounded_json(packet, PACKET_BYTES)
    require(os.write(1, raw) == len(raw))


def spawn():
    # No owner pipes, sockets or diagnostic FDs cross this exec boundary.
    return subprocess.Popen(["/opt/nib/python", "stream-collector"], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)


def start_collector(workers, index):
    # Popen can raise KeyboardInterrupt after fork without returning its child.
    # Defer signals only through fixed exec/registration, never through imports
    # or readiness. The child's exec resets these caught signal dispositions.
    pending = {}
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM)}
    try:
        for sig in handlers:
            signal.signal(sig, lambda sig, frame: pending.setdefault(sig, frame))
        process = spawn()
        row = {"process": process, "index": index, "raw": bytearray(), "ready": False,
               "eof": False, "bad": False, "dispatch": None, "packet": None}
        workers.append(row)
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    for sig, frame in pending.items():
        handlers[sig](sig, frame)
    return row


def close(pipe):
    try:
        pipe.close()
        return True
    except OSError:
        return False


def packet(raw, lower, upper):
    value = stream.worker.decode(raw)
    require(type(value) is dict and set(value) == {"start", "end", "sample"}
            and meter.number(value["start"]) and meter.number(value["end"])
            and lower <= value["start"] <= value["end"] <= upper)
    binding.validate_sample(value["sample"], cohort)
    require(value["sample"]["elapsed_seconds"] <= value["end"] - value["start"] + 1e-6)
    return value


def drain(workers, epoch, wait=0):
    pipes = {}
    for row in workers:
        if not row["eof"]:
            try:
                pipes[row["process"].stdout.fileno()] = row
            except (OSError, ValueError):
                row["bad"] = row["eof"] = True
                close(row["process"].stdout)
    if not pipes:
        if wait:
            time.sleep(min(wait, 0.01))
        return
    try:
        readable, _, _ = select.select(list(pipes), [], [], max(0, wait))
    except (OSError, ValueError):
        for row in pipes.values():
            row["bad"] = row["eof"] = True
            close(row["process"].stdout)
        return
    for fd in readable:
        row = pipes[fd]
        try:
            raw = os.read(fd, PACKET_BYTES + len(READY) + 1 - len(row["raw"]))
            if raw:
                row["raw"].extend(raw)
                require(len(row["raw"]) <= PACKET_BYTES + len(READY)
                        and READY.startswith(row["raw"][:len(READY)]))
                row["ready"] = len(row["raw"]) >= len(READY)
                require(row["dispatch"] is not None or len(row["raw"]) <= len(READY))
                continue
            row["eof"] = True
            close(row["process"].stdout)
            require(row["dispatch"] is not None and row["ready"])
            row["packet"] = packet(bytes(row["raw"][len(READY):]), row["dispatch"], time.monotonic() - epoch)
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            row["bad"] = row["eof"] = True
            close(row["process"].stdout)
        if row["eof"]:
            row["raw"].clear()


def failed(workers):
    return any(row["bad"] or row["packet"] is not None
               and row["packet"]["sample"]["status"] != "completed" for row in workers)


def cleanup(workers, epoch, deadline):
    """Drain before closing; signal denial cannot replace a successful wait."""
    until = min(deadline, time.monotonic() + CLEANUP_SECONDS)
    confirmed = True
    for row in workers:
        confirmed = close(row["process"].stdin) and confirmed
    # Request alarms may be returning known-usage failures at the same cutoff.
    # Give those tiny metadata packets bounded flush time before interrupting.
    for sig, seconds in ((None, 0.1), (signal.SIGINT, 1), (signal.SIGKILL, CLEANUP_SECONDS)):
        for row in workers:
            process = row["process"]
            if sig is not None:
                try:
                    os.killpg(process.pid, sig)
                except OSError:
                    pass
        cutoff = min(until, time.monotonic() + seconds)
        while time.monotonic() < cutoff:
            drain(workers, epoch, min(0.02, cutoff - time.monotonic()))
            if all(row["process"].poll() is not None and row["eof"] for row in workers):
                break
    for row in workers:
        process = row["process"]
        try:
            process.wait(timeout=0)
        except (subprocess.TimeoutExpired, OSError):
            confirmed = False
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            pass
        except OSError:
            confirmed = False
        else:
            confirmed = False
        confirmed = close(process.stdout) and confirmed
        row["eof"] = True
    return confirmed


def peak(intervals):
    events = sorted((point, delta) for row in intervals
                    for point, delta in ((row["start"], 1), (row["end"], -1))
                    if row["reported"] and row["end"] > row["start"])
    active = maximum = 0
    for _, delta in events:
        active += delta
        maximum = max(maximum, active)
    return maximum


def run(endpoint, workload, *, deadline, gpu_count, cohort_seconds, model):
    require(threading.current_thread() is threading.main_thread() and type(workload) is cohort.Workload
            and type(endpoint) is stream.identity.Endpoint and endpoint.port == stream.PORT
            and type(gpu_count) is int and gpu_count == 8 and meter.number(deadline)
            and type(cohort_seconds) is int and cohort_seconds == cohort.EXTENDED_COHORT_SECONDS)
    workload.validate()
    client = stream.OwnedStreamHTTP(endpoint, model=model)
    epoch = time.monotonic()
    deadline = min(deadline, epoch + cohort_seconds)
    work_deadline = deadline - CLEANUP_SECONDS
    workers, warmup, warmup_start, measured_start = [], None, None, None
    stop, reaped = "deadline", True
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        for sig in handlers:
            signal.signal(sig, cancelled)
        if time.monotonic() < work_deadline:
            with stream.worker.alarm(work_deadline):
                warmup_start = time.monotonic()
                warmup = cohort._collect(client, workload, 0, work_deadline)
                stop = "warmup-failed"
                if warmup.status == "completed":
                    stop = "collector-failed"
                    setup_deadline = min(work_deadline, time.monotonic() + START_SECONDS)
                    for index in range(1, cohort.TOTAL_REQUESTS):
                        require(time.monotonic() < setup_deadline)
                        row = start_collector(workers, index)
                        process = row["process"]
                        os.set_blocking(process.stdout.fileno(), False)
                        os.set_blocking(process.stdin.fileno(), False)
                        raw = stream.worker.bounded_json({"process": endpoint.expected, "index": index,
                            "padding": workload.cases[index][0], "deadline": work_deadline, "epoch": epoch}, PACKET_BYTES)
                        require(os.write(process.stdin.fileno(), raw) == len(raw))
                    while not all(row["ready"] for row in workers):
                        require(time.monotonic() < setup_deadline and not failed(workers))
                        drain(workers, epoch, min(0.02, setup_deadline - time.monotonic()))
                    require(time.monotonic() < setup_deadline and not failed(workers))
                    measured_start = time.monotonic()
                    request_deadline = min(work_deadline, measured_start + stream.REQUEST_SECONDS)
                    for row in workers:
                        require(time.monotonic() < request_deadline and not failed(workers))
                        # Count a release attempt conservatively if interrupted.
                        row["dispatch"] = time.monotonic() - epoch
                        require(os.write(row["process"].stdin.fileno(), GO) == len(GO))
                        row["process"].stdin.close()
                        drain(workers, epoch)
                    stop = "request-failed"
                    while not all(row["eof"] for row in workers) and not failed(workers):
                        if time.monotonic() >= request_deadline:
                            stop = "deadline"
                            break
                        drain(workers, epoch, min(0.02, request_deadline - time.monotonic()))
                    if not failed(workers) and all(row["packet"] is not None for row in workers):
                        stop = "complete"
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, InterruptedError)):
            stop = "cancelled"
        elif isinstance(exc, TimeoutError) or isinstance(exc, stream.worker.Abort) and exc.status == "timeout":
            stop = "deadline"
    finally:
        for sig in handlers:
            signal.signal(sig, signal.SIG_IGN)
        try:
            reaped = cleanup(workers, epoch, deadline)
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
    ended = time.monotonic()
    if warmup_start is not None and warmup is None:
        warmup = meter.Sample("cancelled" if stop == "cancelled" else "timeout" if stop == "deadline" else "transport-error",
                              ended - warmup_start, None, None, None, 0, None, None, None, None)
    samples, intervals = [], []
    for row in workers:
        if row["dispatch"] is None:
            continue
        result = row["packet"]
        if result is None:
            status = "timeout" if stop == "deadline" else "cancelled" if stop == "cancelled" else "transport-error"
            start, end = row["dispatch"], ended - epoch
            sample = meter.Sample(status, end - start, None, None, None, 0, None, None, None, None).metadata()
        else:
            start, end, sample = result["start"], result["end"], result["sample"]
        samples.append(sample)
        intervals.append({"index": row["index"], "start": start, "end": end,
                          "reported": result is not None, "exit_code": row["process"].returncode})
    concurrency = peak(intervals)
    if stop == "complete":
        if not reaped or any(row["process"].returncode != 0 for row in workers):
            stop = "collector-failed"
        elif ended >= deadline or any(row["packet"]["end"] > request_deadline - epoch for row in workers):
            stop = "deadline"
        elif concurrency != cohort.MEASURED_REQUESTS:
            stop = "insufficient-overlap"
    all_samples = ([warmup.metadata()] if warmup is not None else []) + samples
    return {"schema": SCHEMA, "model": model, "workload": workload.metadata(), "concurrency": 8,
        "prompt_tokens_per_request": cohort.PROMPT_TOKENS, "max_output_tokens_per_request": cohort.OUTPUT_TOKENS,
        "reserved_requests": cohort.TOTAL_REQUESTS, "reserved_total_tokens": cohort.RESERVED_TOKENS,
        "started_requests": len(all_samples), "not_run_requests": cohort.TOTAL_REQUESTS - len(all_samples),
        "known_prompt_tokens": sum(row["prompt_tokens"] or 0 for row in all_samples),
        "known_output_tokens": sum(row["completion_tokens"] or 0 for row in all_samples),
        "requests_with_unknown_usage": sum(row["completion_tokens"] is None for row in all_samples),
        "warmup": warmup.metadata() if warmup is not None else None, "samples": samples,
        "measured": (meter._summarize_rows(samples, scheduled=cohort.MEASURED_REQUESTS,
            wall_seconds=ended - measured_start, gpu_count=gpu_count) if samples else None),
        "intervals": intervals, "peak_in_flight": concurrency, "collectors_reaped": reaped,
        "elapsed_seconds": ended - epoch, "stop_reason": stop, "completed": stop == "complete",
        "pricing_measured": False, "quality_accepted": False, "confidential": False, "sweep_accepted": False}
