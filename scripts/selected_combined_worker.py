# SPDX-License-Identifier: Apache-2.0
"""Closed source-bound private tokenizer/collector children; no engine launch."""

import sys
from pathlib import Path

if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        sys.exit("Isolated combined child required.")
    sys.path.insert(0, str(Path(__file__).absolute().parent))

import os
import signal
import time

import selected_combined_capacity as capacity
import selected_long_tokenizer as tokenizer
import stream_scaling as scaling

process, service = tokenizer.process, tokenizer.service
require = capacity.require


def authenticate(expected, deadline):
    scaling.binding.sha(expected)
    require(sys.version_info[:3] == (3, 12, 3)
            and Path(__file__).absolute() == process.ROOT / "scripts/selected_combined_worker.py")
    raw = process.bootstrap.read_source(str(process.ROOT / "prepared.json"))
    require(process.digest(raw) == expected)
    receipt = service.correctness.strict_json(raw)
    require(type(receipt) is dict and set(receipt) == {"schema", "lab_sha256", "files"}
            and receipt["schema"] == "nib.selected-service-prepared/v1")
    # Parent authenticates the whole sealed image once. Each collector checks
    # its actual imported source closure, not two large, unused Go binaries.
    for module in tuple(sys.modules.values()):
        name = getattr(module, "__file__", None)
        if name and Path(name).is_relative_to(process.ROOT):
            relative = Path(name).relative_to(process.ROOT).as_posix()
            require(process.digest(process.bootstrap.read_source(name)) == receipt["files"].get(relative)
                    and time.monotonic() < deadline)
    lab_raw = process.bootstrap.read_source(str(process.PAYLOAD / "lab.json"))
    require(process.digest(lab_raw) == receipt["lab_sha256"])
    lab = service.correctness.strict_json(lab_raw)
    process.engine_entry.canonical_arguments(lab)
    require(lab["execution_profile"] == process.engine_entry.LONG_GRAPHS_PROFILE
            and all(os.statvfs(path).f_flag & os.ST_RDONLY
                    for path in (process.ROOT, process.PAYLOAD, Path("/model"))))
    capacity.verify_sources(process.bootstrap.read_source, lab["paths"][-1])
    require(time.monotonic() < deadline)


def prepare(expected, deadline):
    authenticate(expected, deadline)
    with tokenizer.tokenizer_runtime(expected, deadline) as (counter, contract):
        require(contract["execution_profile"] == process.engine_entry.LONG_GRAPHS_PROFILE)
        workload = scaling.cohort.prepare(counter, deadline=deadline)
    authenticate(expected, deadline)
    return {"prepared_sha256": expected, "workload": workload.metadata()}


def main():
    require(len(sys.argv) == 3 and sys.argv[2] in ("prepare", "collect"))
    if sys.argv[2] == "collect":
        authenticate(sys.argv[1], time.monotonic() + scaling.c8.START_SECONDS)
        scaling.child(service.MODEL, selected_model=True)
        return 0
    report_fd = os.dup(1)
    os.set_inheritable(report_fd, False)
    try:
        with open(os.devnull, "r+b", buffering=0) as sink:
            for number in (0, 1, 2):
                os.dup2(sink.fileno(), number)
        signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
        signal.alarm(scaling.cohort.PREPARE_SECONDS)
        row = prepare(sys.argv[1], time.monotonic() + scaling.cohort.PREPARE_SECONDS)
        raw = scaling.stream.worker.bounded_json(row, 8192)
        require(os.write(report_fd, raw) == len(raw))
        return 0
    finally:
        os.close(report_fd)


if __name__ == "__main__":
    try:
        code = main()
    except BaseException:
        code = 1
    sys.exit(code)
