#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fixed startup probes inside the read-only pinned tools root, not an installer."""
import json
import os
from pathlib import Path
import runpy
import sys
import types


def main():
    if not (sys.platform == "linux" and sys.version_info[:2] == (3, 12)
            and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode
            and os.geteuid() == os.getegid() == 0):
        raise ValueError("worker-preflight")
    worker = types.SimpleNamespace(**runpy.run_path("/nib/probe.py", run_name="tools_probe"))
    checker = types.SimpleNamespace(**runpy.run_path("/nib/namespace-check.py", run_name="namespace_check"))
    worker.require(len(sys.argv) in (5, 6))
    checker.check(sys.argv[1:5])
    if len(sys.argv) == 6:
        pid = types.SimpleNamespace(**runpy.run_path("/nib/pid-check.py", run_name="pid_check"))
        pid.check(sys.argv[5])
    inputs = json.loads(worker.read(Path("/nib/input.json"), 16384))
    worker.validate_input(inputs)
    raw = worker.read(Path("/nib/snapshot.py"), 1 << 20)
    worker.require(worker.hashlib.sha256(raw).hexdigest() == inputs["snapshot_sha256"])
    processes = types.ModuleType("pinned_tools_processes")
    exec(compile(raw, "/nib/snapshot.py", "exec", dont_inherit=True), processes.__dict__)
    report = worker.probe(inputs, processes.execute_process_group)
    worker.require(report["result"] == "pass" and len(report["checks"]) == len(worker.COMMANDS))
    sys.stdout.buffer.write(b"NIB_NATIVE_TOOLS_OK\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit("Pinned tools startup rejected; no installation performed.")
