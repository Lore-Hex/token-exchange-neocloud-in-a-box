#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run an explicitly approved frozen sequential plan; never resumes or retries."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B scripts/record-tau-sequential.py")

import argparse
import datetime
import os
from pathlib import Path
import signal
import stat
import tempfile
import time
import types


def source_trial():
    path = Path(__file__).absolute().with_name("tau_trial.py")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not (stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= 1 << 20):
            raise ValueError("sequential source rejected")
        raw = stream.read((1 << 20) + 1)
        after = os.fstat(stream.fileno())
        if len(raw) != before.st_size or (before.st_dev, before.st_ino, before.st_mode, before.st_nlink,
                before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
                after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("sequential source rejected")
    module = types.ModuleType("sequential_tau_trial")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module


trial = source_trial()
PREPARATION, TRIAL, FINAL_RESERVE = 600, 960, 30


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Sequential tau arguments rejected.\n")


def execute(args, progress):
    trial.require(all(path.is_absolute() for path in (args.selection_plan, args.source_archive, args.wheelhouse,
        args.docker_socket, args.contract, args.candidate_key, args.simulator_key, args.vllm_source,
        args.tokenizer, args.tokenizer_wheel, args.output))
        and not args.output.exists() and not args.output.is_symlink())
    cutoff = datetime.datetime.strptime(args.deadline_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    left = cutoff.timestamp() - time.time()
    trial.require(PREPARATION + TRIAL + FINAL_RESERVE < left <= 86400)
    deadline = time.monotonic() + left
    trial.tau.sha(args.selection_plan_sha256)
    raw = trial.runtime.read_file(args.selection_plan, 1 << 20, deadline)
    plan = trial.tau.json_value(raw)
    trial.require(trial.tau.digest(raw) == args.selection_plan_sha256 and trial.tau.encoded(plan) == raw)
    progress["stage"] = "source-verification"
    owner = trial.AggregateOwner(args.output, plan, args.source_archive, args.contract_sha256, deadline)
    reason = "failed"
    progress["stage"] = "reservation"
    try:
        with trial.worker.alarm(deadline - FINAL_RESERVE):
            for index in range(len(owner.journal.plan["selections"])):
                progress["stage"] = "reservation"
                if time.monotonic() + PREPARATION + TRIAL + FINAL_RESERVE > deadline:
                    reason = "deadline"
                    break
                stage = owner.reserve()
                if stage is None:
                    reason = "cap-exhausted"
                    break
                trial.require(stage["index"] == index)
                progress["stage"] = "preparation"
                with tempfile.TemporaryDirectory(prefix="nib-sequential-payload-") as directory:
                    files, _ = trial.prepare(args.source_archive, args.wheelhouse, None, None, None,
                        min(deadline - TRIAL - FINAL_RESERVE, time.monotonic() + PREPARATION),
                        parent_pipe=True, selected_operational=True, external_simulator=True, full_task=True, stage=stage)
                    sha = trial.tau.digest(files["trial.json"])
                    payload = Path(directory) / "prepared"
                    trial.publish(payload, files)
                    progress["stage"] = "trial"
                    destination = args.output / f"trial-{index:02d}"
                    record = trial.run(payload / "payload", sha, destination, args.docker_socket, parent_pipe=True,
                        aggregate_owner=owner, external_inputs={"deadline": min(deadline - FINAL_RESERVE, time.monotonic() + TRIAL),
                            "arguments": (args.contract, args.contract_sha256, args.candidate_key, args.simulator_key,
                                          args.vllm_source, args.tokenizer, args.tokenizer_wheel),
                            "full_task": True, "stage_binding": owner.driver.policy.staged().binding(stage),
                            "support_source": args.source_archive})
                    diagnostic = record.get("trial") or {}
                    progress["cancelled"] = (progress.get("cancelled", False) or record.get("cancelled") is True
                        or record.get("stage") == "cancelled" or diagnostic.get("trial", {}).get("status") == "cancelled")
                    if progress["cancelled"]:
                        raise KeyboardInterrupt
                    progress["stage"] = "settlement"
                    owner.settle(record, destination)
            else:
                progress["stage"], reason = "completed", "complete"
    except BaseException as exc:
        reason = ("cancelled" if progress.get("cancelled") or isinstance(exc, KeyboardInterrupt) else
                  "deadline" if isinstance(exc, trial.worker.Abort) and exc.status == "timeout" else "failed")
    finally:
        try:
            with trial.worker.alarm(deadline):
                result = owner.finish(reason, stage=progress["stage"])
        finally:
            owner.journal.close()
    return result


def main():
    parser = Parser(description=__doc__)
    for name in ("selection-plan", "source-archive", "wheelhouse", "docker-socket", "contract", "candidate-key",
                 "simulator-key", "vllm-source", "tokenizer", "tokenizer-wheel", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("selection-plan-sha256", "contract-sha256", "deadline-utc"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    progress = {"stage": "input-verification", "cancelled": False}
    started = trial.now()
    def cancel(signum, frame):
        if not progress["cancelled"]:
            progress["cancelled"] = True
            raise KeyboardInterrupt
    prior = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in prior:
        signal.signal(sig, cancel)
    try:
        result = execute(args, progress)
        print(trial.tau.encoded(result).decode(), end="")
        return 0 if result["result"] == "pass" else 1
    except BaseException:
        if not args.output.exists():
            record = {"result": "fail", "stage": progress["stage"], "cancelled": progress["cancelled"],
                      "started_utc": started, "ended_utc": trial.now(), "live_admission": False,
                      "quality_eligible": False, "source_authenticated": False}
            try:
                trial.publish_evidence(args.output, trial.external_evidence_files(record))
            except BaseException:
                pass  # Preserve any partial/existing evidence; never retry or overwrite.
        print("Sequential tau incomplete; retained reservations must be reconciled. No retry.", file=sys.stderr)
        return 1
    finally:
        progress["cancelled"] = True
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, set(prior))
        try:
            for sig, handler in prior.items():
                signal.signal(sig, handler)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)


if __name__ == "__main__":
    sys.exit(main())
