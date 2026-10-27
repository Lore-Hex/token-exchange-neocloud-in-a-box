#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Finite, serial ordinary-TLS observations; not continuous readiness or qualification."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        sys.exit("Use python3 -I -S -B scripts/soak-selected-service.py")
    sys.path.insert(0, str(ROOT / "scripts"))

from datetime import datetime, timezone
import hashlib
import importlib.util
import math
import os
import signal
import stat
import threading
import time
import types

_spec = importlib.util.spec_from_file_location("ordinary_soak_probe", ROOT / "scripts/probe-selected-service.py")
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)
inference, worker = probe.inference, probe.service.identity.worker
require, encoded = probe.require, probe.encoded
SCRIPT = "scripts/soak-selected-service.py"
SECONDS, MIN_INTERVAL, MAX_INTERVAL, MAX_SAMPLES = 86400, 300, 3600, 288
RECEIPT_LIMIT, FINALIZE_SECONDS = 32768, 5
CONTINUE_FAILURES = frozenset(("not-ready", "transport", "completion"))
CLAIMS = ("continued_readiness", "provider_conformance", "quality_eligible",
          "deployment_eligible", "confidential", "e2ee", "phase_complete",
          "peak_load_soak", "server_ownership_verified")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def utc(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


class Window:
    def __init__(self, duration, authorized):
        self.started = time.monotonic()
        self.wall_started = time.time()
        self.original = self.started + duration
        self.wall = self.wall_started + duration
        require(math.isfinite(self.wall_started) and authorized >= self.wall, "deadline")
        self.hard = self.original

    def cutoff(self):
        # Wall jumps may only shorten the original monotonic window, never renew it.
        self.hard = min(self.hard, time.monotonic() + self.wall - time.time())
        return self.hard

    def wait(self, wake):
        while True:
            left = min(wake, self.cutoff()) - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(1, left))


class Receipts:
    """New private directory, exclusive fixed-name files and a durable hash chain.

    Partial writes remain evidence; a failed write stops requests. No resume,
    overwrite, rename, truncation, or deletion of prior observations is allowed.
    """
    def __init__(self, path):
        path = Path(path)
        require(path.is_absolute() and ".." not in path.parts and path.name not in ("", "."), "contract")
        self.fd = None
        parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in path.parts[1:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                os.close(parent)
                parent = child
            os.mkdir(path.name, mode=0o700, dir_fd=parent)
            self.fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            info = os.fstat(self.fd)
            require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700, "contract")
            os.fsync(parent)
        except BaseException:
            if self.fd is not None:
                os.close(self.fd)
            raise
        finally:
            os.close(parent)
        self.previous = None
        self.count = 0

    def write(self, name, payload):
        require(name in ("run.json", "terminal.json") or
                probe.re.fullmatch(r"[0-9]{4}-(?:intent|sample)\.json", name), "contract")
        raw = encoded({"schema": "nib.ordinary-soak-receipt/v1", "previous_sha256": self.previous,
                       "ordinal": self.count, "payload": payload}) + b"\n"
        require(len(raw) <= RECEIPT_LIMIT, "protocol")
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self.fd)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(self.fd)
        self.previous = digest(raw)
        self.count += 1
        return self.previous

    def close(self):
        os.close(self.fd)


def contract(args):
    require(args.run and threading.current_thread() is threading.main_thread(), "contract")
    probe.validate_target(args)
    require(type(args.duration_seconds) is int and MIN_INTERVAL <= args.duration_seconds <= SECONDS
            and type(args.interval_seconds) is int and MIN_INTERVAL <= args.interval_seconds <= MAX_INTERVAL
            and type(args.max_samples) is int and 1 <= args.max_samples <= MAX_SAMPLES, "contract")
    planned = 1 + (args.duration_seconds - probe.SECONDS) // args.interval_seconds
    require(planned <= args.max_samples, "contract")
    # A fresh process owns the run: it cannot inherit or extend another alarm.
    require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "contract")
    wall = datetime.strptime(args.deadline, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    return planned, Window(args.duration_seconds, wall)


def empty_result():
    return {"schema": "nib.ordinary-serving-soak/v1", "result": "fail", "failure": "contract",
        "started_at": None, "ended_at": None, "elapsed_seconds": 0, "window_completed": False,
        "samples_started": 0, "samples_completed": 0, "samples_passed": 0, "samples_failed": 0,
        "http_requests_attempted": 0, "model_posts_attempted": 0, "authenticated_requests_attempted": 0,
        "potential_unrecorded_authenticated_requests": 0, "potential_unrecorded_http_requests": 0,
        "known_prompt_tokens": 0,
        "known_completion_tokens": 0, "unknown_usage_samples": 0,
        "run_sha256": None, "terminal_sha256": None, "receipts_complete": False,
        "client_operations_returned": False, "remote_cancellation_verified": False,
        "server_resources_modified": False, "server_teardown_performed": False,
        "retries": 0, **dict.fromkeys(CLAIMS, False)}


def validate_sample(row, revision, hashes, mode):
    expected = set(probe.empty_result()) | ({"tls_mode"} if mode == "system-roots" else set())
    require(type(row) is dict and set(row) == expected and len(encoded(row)) <= RECEIPT_LIMIT // 2,
            "protocol")
    require(row["schema"] == "nib.selected-external-smoke/v" + ("2" if mode == "system-roots" else "1")
            and (mode != "system-roots" or row["tls_mode"] == mode)
            and row["result"] in ("pass", "fail") and row["failure"] in inference.FAILURES
            and (row["result"] == "pass") == (row["failure"] == "none")
            and row["max_authenticated_requests"] == 1 and row["retries"] == 0
            and all(row[name] is False for name in ("auth_upstream_calls_measured",
                "provider_conformance", "quality_eligible", "deployment_eligible", "confidential", "e2ee")),
            "protocol")
    for name in ("attempts", "checks", "tls_checks"):
        require(type(row[name]) is dict and set(row[name]) == set(probe.CASES)
                and all(type(value) is bool for value in row[name].values()), "protocol")
    require(row["git_sha"] == revision and row["source_sha256"] == {
        name: hashes[name] for name in probe.SOURCES}, "identity")
    require(type(row["usage_unknown"]) is bool, "protocol")
    usage = row["usage"]
    require(usage is None or type(usage) is dict and set(usage) == {"prompt_tokens", "completion_tokens"}
            and all(type(value) is int and value > 0 for value in usage.values())
            and usage["prompt_tokens"] <= probe.service.CONTEXT - probe.service.OUTPUT
            and usage["completion_tokens"] <= probe.service.OUTPUT, "protocol")
    require(row["usage_unknown"] == (row["attempts"]["stream"] and usage is None), "protocol")
    require(row["result"] != "pass" or (all(row["checks"].values())
            and all(row["tls_checks"].values()) and all(row["attempts"].values()) and usage is not None), "protocol")
    return row


def run(args):
    result, journal, window = empty_result(), None, None
    active = False
    try:
        planned, window = contract(args)
        result["started_at"] = utc(window.wall_started)
        require(window.cutoff() - time.monotonic() > probe.SECONDS, "deadline")
        with worker.alarm(min(window.cutoff(), time.monotonic() + probe.SECONDS)):
            revision, hashes = probe.source_identity(window.cutoff(), extra_files=(SCRIPT,))
            journal = Receipts(args.output_directory)
            mode = probe.tls_mode(args)
            target = {"address": args.address, "port": args.port, "hostname": args.hostname, "tls_mode": mode}
            if mode == "pinned":
                target["certificate_sha256"] = args.certificate_sha256
            result["run_sha256"] = journal.write("run.json", {
                "schema": "nib.ordinary-soak-contract/v1", "git_sha": revision, "source_sha256": hashes,
                "started_at": result["started_at"], "original_cutoff_utc": utc(window.wall),
                "authorized_not_after": args.deadline, "duration_seconds": args.duration_seconds,
                "interval_seconds": args.interval_seconds, "planned_samples": planned,
                "max_samples": args.max_samples, "max_authenticated_requests": args.max_samples,
                "max_http_requests": len(probe.CASES) * args.max_samples, "concurrency": 1,
                "max_model_posts": (len(probe.CASES) - 1) * args.max_samples,
                "max_requested_output_tokens_all_posts": (len(probe.CASES) - 1) * args.max_samples * probe.service.OUTPUT,
                "sample_seconds": probe.SECONDS, "authenticated_request_seconds": inference.REQUEST_SECONDS,
                "max_output_tokens_per_request": probe.service.OUTPUT, "max_stream_bytes": probe.LIMIT,
                "request_sha256": digest(probe.body()), "target_sha256": digest(encoded(target)),
                "tls_mode": mode, "retries": 0, "finalize_seconds": FINALIZE_SECONDS,
                **dict.fromkeys(CLAIMS, False)})
        with inference.cancellation(threading.Event()):
            last_start = None
            for index in range(planned):
                due = window.started + index * args.interval_seconds
                if last_start is not None:
                    due = max(due, last_start + args.interval_seconds)
                window.wait(due)
                require(window.cutoff() - time.monotonic() >= probe.SECONDS, "deadline")
                sample_deadline = min(window.cutoff(), time.monotonic() + probe.SECONDS)
                with worker.alarm(sample_deadline):
                    # Reject changed source/HEAD before any request in this sample.
                    current = probe.source_identity(sample_deadline, extra_files=(SCRIPT,))
                    require(current == (revision, hashes), "identity")
                    sample_args = types.SimpleNamespace(**vars(args))
                    # The outer monotonic alarm prevents rollback from extending this UTC budget.
                    sample_args.deadline = datetime.fromtimestamp(
                        min(window.wall, time.time() + sample_deadline - time.monotonic()),
                        timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    last_start = time.monotonic()
                    journal.write(f"{index:04d}-intent.json", {
                        "sample": index, "run_sha256": result["run_sha256"], "started_at": probe.now(),
                        "elapsed_seconds": last_start - window.started,
                        "max_http_requests": len(probe.CASES), "max_authenticated_requests": 1,
                        "sample_deadline": sample_args.deadline})
                    active = True
                    result["samples_started"] += 1
                    observed = probe.run(sample_args)
                    validate_sample(observed, revision, hashes, mode)
                    journal.write(f"{index:04d}-sample.json", {"sample": index,
                        "run_sha256": result["run_sha256"], "elapsed_seconds": time.monotonic() - window.started,
                        "probe": observed})
                    active = False
                    result["samples_completed"] += 1
                    result["samples_passed" if observed["result"] == "pass" else "samples_failed"] += 1
                    result["http_requests_attempted"] += sum(observed["attempts"].values())
                    result["model_posts_attempted"] += sum(observed["attempts"][case] for case in probe.CASES if case != "health")
                    result["authenticated_requests_attempted"] += int(observed["attempts"]["stream"])
                    result["unknown_usage_samples"] += int(observed["usage_unknown"])
                    if observed["usage"]:
                        result["known_prompt_tokens"] += observed["usage"]["prompt_tokens"]
                        result["known_completion_tokens"] += observed["usage"]["completion_tokens"]
                    if observed["usage_unknown"]:
                        result["failure"] = "unknown-usage"
                        break
                    if observed["result"] == "fail" and observed["failure"] not in CONTINUE_FAILURES:
                        result["failure"] = observed["failure"]
                        break
            else:
                window.wait(window.original)
                result["window_completed"] = time.monotonic() >= window.original
                if not result["window_completed"]:
                    result["failure"] = "deadline"
                elif result["samples_failed"]:
                    result["failure"] = "failed-samples"
                else:
                    result.update(result="pass", failure="none")
    except inference.Rejected as error:
        result["failure"] = error.category
    except worker.Abort:
        result["failure"] = "deadline"
    except KeyboardInterrupt:
        result["failure"] = "cancelled"
    except BaseException:
        result["failure"] = "internal"
    finally:
        result["potential_unrecorded_authenticated_requests"] = int(active)
        result["potential_unrecorded_http_requests"] = len(probe.CASES) if active else 0
        result["client_operations_returned"] = not active
        if window is not None:
            result["elapsed_seconds"] = max(0, time.monotonic() - window.started)
        result["ended_at"] = probe.now()
        if result["failure"] != "none":
            result["result"] = "fail"
        if journal is not None:
            try:
                with worker.alarm(time.monotonic() + FINALIZE_SECONDS):
                    result["receipts_complete"] = not active
                    result["terminal_sha256"] = journal.write("terminal.json", dict(result))
            except BaseException:
                result.update(result="fail", failure="receipt-write", receipts_complete=False)
            finally:
                try:
                    journal.close()
                except BaseException:
                    result.update(result="fail", failure="receipt-close", receipts_complete=False)
    return result


def main(argv=None):
    result = empty_result()
    try:
        parser = probe.QuietParser(description=__doc__, allow_abbrev=False)
        parser.add_argument("--run", action="store_true")
        for name in ("address", "hostname", "api-key-file", "deadline", "output-directory"):
            parser.add_argument("--" + name, required=True)
        parser.add_argument("--port", type=int, required=True)
        parser.add_argument("--duration-seconds", type=int, default=SECONDS)
        parser.add_argument("--interval-seconds", type=int, default=MIN_INTERVAL)
        parser.add_argument("--max-samples", type=int, required=True)
        probe.tls_arguments(parser)
        result = run(parser.parse_args(argv))
    except BaseException:
        pass
    print(encoded(result).decode())
    return 0 if result["result"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
