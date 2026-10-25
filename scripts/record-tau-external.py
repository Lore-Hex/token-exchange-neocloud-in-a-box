#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare offline, or explicitly run ONE bounded natural lookup diagnostic."""

import sys

if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        sys.exit("Use python3 -I -S -B scripts/record-tau-external.py")

import argparse
import datetime
import os
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
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--source-archive", type=Path, required=True)
    prepare.add_argument("--wheelhouse", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("run")
    for name in ("payload", "docker-socket", "contract", "candidate-key", "simulator-key", "vllm-source",
                 "tokenizer", "tokenizer-wheel", "output"):
        run.add_argument("--" + name, type=Path, required=True)
    for name in ("plan-sha256", "contract-sha256", "deadline-utc"):
        run.add_argument("--" + name, required=True)
    args = parser.parse_args()
    cancelled = False
    def cancel(signum, frame):
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            raise KeyboardInterrupt
    prior = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in prior:
        signal.signal(sig, cancel)
    try:
        if args.command == "prepare":
            files, plan = trial.prepare(args.source_archive, args.wheelhouse, None, None, None,
                time.monotonic() + 600, parent_pipe=True, selected_operational=True,
                customer_resolution=True, external_simulator=True)
            trial.publish(args.output, files)
            print(trial.tau.encoded({"plan_sha256": trial.tau.digest(trial.tau.encoded(plan)),
                                    "source_git_sha": plan["producer"]["git_sha"]}).decode(), end="")
            return 0
        trial.require(not args.output.exists())
        deadline_utc = datetime.datetime.strptime(args.deadline_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        remaining = deadline_utc.timestamp() - time.time()
        trial.require(60 < remaining <= 960)
        # Freeze one monotonic deadline; preparation and cleanup consume it.
        deadline = time.monotonic() + remaining
        record = trial.run(args.payload, args.plan_sha256, args.output, args.docker_socket,
            parent_pipe=True, external_inputs={"deadline": deadline, "arguments": (
                args.contract, args.contract_sha256, args.candidate_key, args.simulator_key,
                args.vllm_source, args.tokenizer, args.tokenizer_wheel)})
        print(trial.tau.encoded({"result": record["result"], "cleanup": record["cleanup"]}).decode(), end="")
        return 0 if record["result"] == "pass" else 1
    except BaseException:
        if args.command == "run" and not args.output.exists():
            trial.publish_evidence(args.output, {"observation.json": trial.tau.encoded({
                "schema": "nib.tau-external-preflight/v1", "result": "fail", "cleanup": "unconfirmed",
                "quality_eligible": False, "confidential": False, "live_admission": False})})
        print("External tau diagnostic failed; no automatic retry.", file=sys.stderr)
        return 1
    finally:
        cancelled = True
        for sig, handler in prior.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
