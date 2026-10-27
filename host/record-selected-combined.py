#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Private network-none combined qualification; inspect does not start a GPU."""

import sys
from pathlib import Path

ROOT = Path(__file__).absolute().parents[1]
if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        sys.exit("Use python3 -I -S -B record-selected-combined.py")
    sys.path.insert(0, str(ROOT / "scripts"))

from dataclasses import replace
import os
import signal
import time
import types

import selected_combined_owner as combined

receipt, service, process = combined.receipt, combined.service, combined.process
require = combined.require


def smoke_module():
    path = ROOT / "host/record-selected-service-smoke.py"
    module = types.ModuleType("combined_smoke_owner")
    module.__file__ = str(path)
    exec(compile(process.bootstrap.read_source(str(path)), str(path), "exec"), module.__dict__)
    return module


def execute(args):
    smoke = smoke_module()
    result = smoke.report()
    result.update(schema="nib.selected-combined-host/v1", execution_profile=combined.plan.PROFILE,
                  input_cleanup_confirmed=False, cancelled=False)
    directory = Path(args.receipt_directory)
    with receipt.tau_runtime.directory(directory) as fd:
        with os.scandir(fd) as entries:
            require(next(entries, None) is None)
    # Exclusive intent publication precedes any owned cloud/GPU process. All
    # records, including a rejected original capture, survive failure unchanged.
    receipt.retain(directory / "intent.json", receipt.encoded({"schema": "nib.selected-combined-intent/v1",
        "preparation_sha256": args.preparation_sha256, "build_sha256": args.build_sha256,
        "execution_profile": combined.plan.PROFILE, "run_requested": args.run}), 4096)
    started_wall, started_mono = time.time(), time.monotonic()
    try:
        args.execution_profile = combined.BASE_PROFILE
        args.lifetime_profile = service.ORDINARY_PROFILE
        with smoke.admitted_runtime(args, result, lifetime_profile=service.ORDINARY_PROFILE) as values:
            contract, lab, recorder, env, build, base, image, inputs = values
            run = combined.Run(build["prepared_sha256"], dict(preparation_seconds=args.preparation_seconds,
                now_wall=started_wall, now_monotonic=started_mono,
                hard_wall=contract.hard_wall, hard_monotonic=contract.hard_monotonic))
            result["deadline_plan"] = dict(run.row["deadline_plan"])
            # Do not trust an older image that merely has the same CLI flags.
            _, source = smoke.image_builder.committed_sources(recorder)
            _, files, _ = smoke.image_builder.validated_files(Path(args.preparation), args.preparation_sha256)
            require(all(files.get("context/service/" + name) == raw for name, raw in source.items()
                        if name.startswith("scripts/")))
            require(time.monotonic() < run.deadline("preparation"))
            if args.run:
                # Host watchdog and attach retain the original fixed end, even
                # when the operator authorized a longer maximum rental window.
                end = run.deadline("host-cleanup")
                bounded = replace(contract, hard_monotonic=end,
                    hard_wall=min(contract.hard_wall, started_wall + end - started_mono),
                    startup_deadline=run.deadline("preparation"))
                # The child reconstructs the same full plan, not a new duration.
                smoke.run_container(args, bounded, lab, recorder, env, build, base, image, inputs, result,
                    combined=run, receipt_path=directory / "capture.jsonl")
        result["input_cleanup_confirmed"] = True
    except BaseException as exc:
        result.update(result="fail", status="BLOCKED")
        if isinstance(exc, (KeyboardInterrupt, SystemExit, smoke.Cancelled)):
            result["cancelled"] = True
    finally:
        receipt.retain(directory / "host.json", receipt.encoded(result), receipt.MAX_CAPTURE + 16384)
    return {"schema": result["schema"], "result": result["result"], "cleanup": result["cleanup"],
            "capture_sha256": result.get("capture_sha256"), "hardware_qualified": False}


def main(argv=None):
    parser = combined.QuietParser(description=__doc__, allow_abbrev=False)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--inspect", action="store_true")
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--replay", action="store_true")
    parser.add_argument("--receipt-directory", required=True)
    parser.add_argument("--prepared-sha256")
    parser.add_argument("--preparation-seconds", type=int)
    for name in ("build-report", "build-sha256", "preparation", "preparation-sha256", "docker-host",
                 "provider-config", "api-key-file", "cert", "key", "hostname", "weights",
                 "input-price", "output-price", "hard-stop-at"):
        parser.add_argument("--" + name)
    cancelled = False
    def cancel(*_):
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            raise KeyboardInterrupt()
    handlers = {sig: signal.signal(sig, cancel)
                for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        args = parser.parse_args(argv)
        if args.replay:
            result = receipt.replay_directory(args.receipt_directory, args.prepared_sha256)
        else:
            require(all(value is not None for key, value in vars(args).items()
                        if key not in ("prepared_sha256",)))
            result = execute(args)
        print(receipt.encoded(result).decode("ascii"))
        return 0 if result["result"] == "pass" else 1
    except BaseException:
        print('{"schema":"nib.selected-combined-host/v1","result":"fail","hardware_qualified":false}')
        return 1
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    sys.exit(main())
