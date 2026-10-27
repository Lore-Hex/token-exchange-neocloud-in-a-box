#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Plan/prepare offline or run ONE explicitly selected bounded external trial."""

import sys

if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        sys.exit("Use python3 -I -S -B scripts/record-tau-external.py")

import argparse
import datetime
import os
import re
from pathlib import Path
import signal
import stat
import time
import types


def source_trial():
    path = Path(__file__).absolute().parent / "tau_trial.py"
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not (stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= 1 << 20):
            raise ValueError("external tau source rejected")
        raw = stream.read((1 << 20) + 1)
        after = os.fstat(stream.fileno())
        if len(raw) != before.st_size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("external tau source rejected")
    module = types.ModuleType("external_tau_trial")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module


trial = source_trial()


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "External tau arguments rejected.\n")


def main():
    parser = Parser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    planning = commands.add_parser("plan")
    planning.add_argument("--source-archive", type=Path, required=True)
    planning.add_argument("--output", type=Path, required=True)
    planning.add_argument("--domains", nargs="+", choices=("airline", "retail", "telecom"), required=True)
    planning.add_argument("--trials", type=int, required=True)
    planning.add_argument("--seed", type=int, required=True)
    for name in ("weekly-cap-usd", "gpu-committed-usd", "cpu-disk-reserved-usd", "other-committed-usd"):
        planning.add_argument("--" + name, required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--source-archive", type=Path, required=True)
    prepare.add_argument("--wheelhouse", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("run")
    for command in (prepare, run):
        command.add_argument("--full-task", action="store_true")
        command.add_argument("--selection-plan-sha256")
        command.add_argument("--selection-index", type=int)
    prepare.add_argument("--selection-plan", type=Path)
    for name in ("payload", "docker-socket", "contract", "candidate-key", "simulator-key", "vllm-source",
                 "tokenizer", "tokenizer-wheel", "output"):
        run.add_argument("--" + name, type=Path, required=True)
    for name in ("plan-sha256", "contract-sha256", "deadline-utc"):
        run.add_argument("--" + name, required=True)
    run.add_argument("--support-source-archive", type=Path)
    args = parser.parse_args()
    started = trial.now()
    cancelled = False
    toolkit = None
    def cancel(signum, frame):
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            raise KeyboardInterrupt
    prior = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in prior:
        signal.signal(sig, cancel)
    try:
        if args.command == "plan":
            plan = trial.selection_plan(args.source_archive, args.domains, args.trials, args.seed,
                {name: getattr(args, name) for name in ("weekly_cap_usd", "gpu_committed_usd",
                                                       "cpu_disk_reserved_usd", "other_committed_usd")},
                time.monotonic() + 600)
            digest = trial.tau.digest(trial.tau.encoded(plan))
            envelope = trial.evidence_record(plan["producer_git_sha"], digest, started,
                                             {"result": plan["result"], "ended_utc": trial.now()})
            envelope.update(command="python3 -I -S -B scripts/record-tau-external.py plan --source-archive PRIVATE "
                "--output PRIVATE --domains " + " ".join(plan["domains"]) + " --trials " + str(args.trials)
                + " --seed " + str(args.seed) + " " + " ".join("--" + name.replace("_", "-") + " " + value
                    for name, value in plan["budget"]["inputs"].items()),
                host={"provider": "local", "machine": "offline-source-preflight", "gpus": "none", "driver": None, "cuda": None},
                notes="Source-bound staged selection and declared cost reservations only. No upstream execution, "
                      "endpoint calls, spending authorization or quality claim. output.log retains the complete plan.")
            trial.publish_evidence(args.output, {"run.json": trial.tau.encoded(envelope), "output.log": trial.tau.encoded(plan)})
            print(trial.tau.encoded({"result": plan["result"], "selection_plan_sha256": digest}).decode(), end="")
            return 0 if plan["result"] == "pass" else 1
        staged = args.selection_plan_sha256 is not None or args.selection_index is not None
        trial.require(not staged or args.full_task and args.selection_plan_sha256 is not None
                      and type(args.selection_index) is int and 0 <= args.selection_index < 60)
        if staged:
            trial.tau.sha(args.selection_plan_sha256)
        if args.command == "prepare":
            trial.require((args.selection_plan is not None) == staged)
            stage = trial.read_stage(args.selection_plan, args.selection_plan_sha256, args.selection_index,
                                     time.monotonic() + 60) if staged else None
            files, plan = trial.prepare(args.source_archive, args.wheelhouse, None, None, None,
                time.monotonic() + 600, parent_pipe=True, selected_operational=True,
                customer_resolution=not args.full_task, external_simulator=True,
                **({"full_task": True} if args.full_task else {}), **({"stage": stage} if staged else {}))
            trial.publish(args.output, files)
            print(trial.tau.encoded({"plan_sha256": trial.tau.digest(trial.tau.encoded(plan)),
                                    "source_git_sha": plan["producer"]["git_sha"]}).decode(), end="")
            return 0
        trial.require(not args.output.exists())
        trial.require(args.support_source_archive is None or args.full_task and args.support_source_archive.is_absolute())
        deadline_utc = datetime.datetime.strptime(args.deadline_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        remaining = deadline_utc.timestamp() - time.time()
        trial.require(60 < remaining <= 960)
        # Freeze one monotonic deadline; preparation and cleanup consume it.
        deadline = time.monotonic() + remaining
        toolkit = trial.identity(min(deadline - 60, time.monotonic() + 60), True,
                                 selected_operational=True, external_simulator=True)
        record = trial.run(args.payload, args.plan_sha256, args.output, args.docker_socket,
            parent_pipe=True, external_inputs={"deadline": deadline, "arguments": (
                args.contract, args.contract_sha256, args.candidate_key, args.simulator_key,
                args.vllm_source, args.tokenizer, args.tokenizer_wheel),
                **({"full_task": True} if args.full_task else {}),
                **({"support_source": args.support_source_archive} if args.support_source_archive is not None else {}),
                **({"stage_binding": {"sha256": args.selection_plan_sha256, "index": args.selection_index}} if staged else {})})
        print(trial.tau.encoded({"result": record["result"], "cleanup": record["cleanup"]}).decode(), end="")
        return 0 if record["result"] == "pass" else 1
    except BaseException:
        if args.command == "run" and toolkit is not None and not args.output.exists():
            trial.publish_evidence(args.output, trial.external_evidence_files({
                "schema": "nib.tau-external-full-task-preflight/v1" if args.full_task else "nib.tau-external-preflight/v1",
                "result": "fail", "cleanup": "unconfirmed",
                "toolkit": toolkit,
                **({"plan_sha256": args.plan_sha256} if re.fullmatch("[0-9a-f]{64}", args.plan_sha256) else {}),
                "started_utc": started, "ended_utc": trial.now(),
                "quality_eligible": False, "confidential": False, "live_admission": False}))
        print("External tau diagnostic failed; no automatic retry.", file=sys.stderr)
        return 1
    finally:
        cancelled = True
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, set(prior))
        try:
            for sig, handler in prior.items():
                signal.signal(sig, handler)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)


if __name__ == "__main__":
    raise SystemExit(main())
