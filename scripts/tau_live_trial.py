#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Explicit admitted local live tau smoke; never a quality or confidential gate."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B scripts/tau_live_trial.py")

import argparse
import os
from pathlib import Path
import stat
import time
import types


def runner_module():
    # Bootstrap our source only, not ambient imports or cached bytecode. The
    # full committed closure is checked again before preparing or running.
    path = Path(__file__).absolute().with_name("tau_trial.py")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not (stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= 1 << 20):
            raise ValueError("live bootstrap rejected")
        raw = stream.read((1 << 20) + 1)
        after = os.fstat(stream.fileno())
        def identity(info):
            return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
                    info.st_mtime_ns, info.st_ctime_ns)
        if len(raw) != before.st_size or identity(before) != identity(after):
            raise ValueError("live bootstrap rejected")
    module = types.ModuleType("tau_trial")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module


def main():
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError("live arguments rejected")
    parser = Parser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True, parser_class=Parser)
    prep = sub.add_parser("prepare")
    for name in ("source-archive", "wheelhouse", "manifest", "manifest-sha256", "domain", "task-id", "role"):
        prep.add_argument("--" + name, required=True)
    prep.add_argument("--trial", required=True, type=int)
    execution = sub.add_parser("run")
    for name in ("payload", "plan-sha256", "docker-socket"):
        execution.add_argument("--" + name, required=True)
    for child in (prep, execution):
        for name in ("deployment", "deployment-sha256", "output"):
            child.add_argument("--" + name, required=True)
    args = parser.parse_args()
    runner = runner_module()
    if args.operation == "prepare":
        deadline = time.monotonic() + runner.runtime.SECONDS
        toolkit = runner.identity(deadline, True, live_local=True)
        live = runner.parent_driver(toolkit, live_local=True).live
        manifest = runner.tau.json_value(live.private_read(Path(args.manifest), deadline))
        runner.aggregate.validate_manifest(manifest, args.manifest_sha256,
            runner.tau.json_value(runner.runtime.read_file(runner.ROOT / runner.tau.PINS, 1 << 20, deadline)))
        deployment = live.Deployment(Path(args.deployment), args.deployment_sha256, manifest, deadline)
        deployment.require_target(args.role)
        files, _ = runner.prepare(Path(args.source_archive), Path(args.wheelhouse), Path(args.manifest),
            args.manifest_sha256, {key: getattr(args, key) for key in ("domain", "task_id", "trial", "role")},
            deadline, parent_pipe=True, live_binding=deployment.binding)
        runner.publish(Path(args.output), files)
        print(runner.tau.encoded({"plan_sha256": runner.tau.digest(files["trial.json"]),
                                 "upstream_executed": False, "live_transport": False}).decode(), end="")
        return 0
    return runner.run(Path(args.payload), args.plan_sha256, Path(args.output), Path(args.docker_socket),
        parent_pipe=True, live_deployment=(Path(args.deployment), args.deployment_sha256))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BaseException as exc:
        if isinstance(exc, SystemExit) and type(exc.code) is int:
            raise
        sys.exit("Tau live trial rejected; no qualification claim.")
