#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Import exact reviewed APT reports without repeating their executions."""
import sys

if __name__ == "__main__" and not sys.flags.isolated:
    sys.exit("Use python3 -I scripts/record_apt_report.py")

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import types

ROOT = Path(__file__).absolute().parent.parent
SCRIPT = "scripts/record_apt_report.py"
PRODUCER = "03d52b4b078c8102b296e03603ec34c00c0c3401"
REPORT_SHA256 = "37801abac400cd6cda8be1986591c8b7f1cae8c01de1f42e401246af98c2dd4f"
DIAGNOSTIC_PRODUCER = "a3d2657ead1a375d9fa57082dca7e5f23bf6e3d4"
DIAGNOSTIC_SHA256 = "2a31d0e293c05c09b29b9636d3ffcae41e15d1761e297a6ff1cfb211fa467f9b"
RESOLVED_PRODUCER = "87f7d15c6d3a02f3faa83b0ac584315501e1fe9b"
RESOLVED_SHA256 = "980e1a5e9599a3e39d98e58c46dc8da707b92cfc5965a8360b5df16329be0fa0"
REPORTS = {
    REPORT_SHA256: (PRODUCER, 1, "fail", "Resolve failure cause is unknown."),
    DIAGNOSTIC_SHA256: (
        DIAGNOSTIC_PRODUCER, 2, "fail",
        "Resolve exited zero but the strict stderr gate rejected the exact "
        "missing-preferences-directory warning (92 bytes). This is the diagnostic "
        "run's observation, not a reconstruction of the earlier v1 stderr."),
    RESOLVED_SHA256: (
        RESOLVED_PRODUCER, 2, "pass",
        "Offline resolution passed with explicit private empty preferences: "
        "145 packages planned for install/configuration and zero removals. "
        "This is a simulated package plan, not an installed or bootable guest."),
}


def bootstrap():
    path = ROOT / "scripts/prepare_guest_packages.py"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= 65536:
            raise ValueError("report-import-rejected")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(65537)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_nlink,
                              s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if len(data) != before.st_size or identity(before) != identity(os.fstat(fd)):
            raise ValueError("report-import-rejected")
    finally:
        os.close(fd)
    module = types.ModuleType("apt_evidence_files")
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec", dont_inherit=True), module.__dict__)
    return module, data


base, BASE_SOURCE = bootstrap()
BOOTSTRAP = base.read(ROOT / SCRIPT, 65536)


def committed():
    revision, sources = base.committed_inputs()
    value = base.processes.execute_process_group(
        ["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(ROOT),
         "show", revision + ":" + SCRIPT],
        env=base.GIT_ENV, timeout=30, max_output_bytes=65536, stdin=subprocess.DEVNULL)
    value.check_returncode()
    base.require(value.stdout == BOOTSTRAP == base.read(ROOT / SCRIPT, 65536)
                 and sources[base.SOURCE_FILES[0]] == BASE_SOURCE)
    sources[SCRIPT] = BOOTSTRAP
    return revision, sources


def evidence(raw, revision, sources):
    # Deliberately not a general import API: never publish arbitrary input JSON.
    base.require(type(raw) is bytes and len(raw) <= 16384
                 and base.digest(raw) in REPORTS
                 and re.fullmatch("[0-9a-f]{40}", revision) and revision != "0" * 40)
    report_sha256 = base.digest(raw)
    producer, version, result, observation = REPORTS[report_sha256]
    report = json.loads(raw)
    base.require(report["git_sha"] == producer and report["result"] == result
                 and report["schema"] == "nib.apt-container-run/v" + str(version)
                 and report["stage"] == "complete" and report["cleanup"] == "confirmed")
    simulation = report["simulation"]
    base.require(simulation["result"] == result
                 and not any(simulation[key] for key in
                             ("installable_verified", "package_installation_executed",
                              "maintainer_scripts_executed", "bootable", "deployable")))
    if result == "pass":
        base.require(simulation["stage"] == "complete" and simulation["diagnostic"] is None
                     and simulation["target_packages"] == 145
                     and simulation["plan"] == {
                         "installed": 145, "configured": 145, "removed": 0,
                         "plan_sha256": "8bccb4aa2b6c004f7081f817a7244c931cc003280c166dc8c930ecbc915efe4c"})
    else:
        base.require(simulation["stage"] == "resolve" and simulation["plan"] is None)
    run = {
        "schema": "nib.evidence/v1", "phase": "P3", "task": "T1", "git_sha": producer,
        "command": "python3 -I host/simulate-guest-apt.py --run --metadata <private-metadata> "
                   "--archives <private-archives> --bundle <private-bundle> --output <private-output>",
        "started_utc": report["started_at"], "ended_utc": report["finished_at"],
        "host": {"provider": "local-docker", "machine": "linux-amd64",
                 "gpus": "none", "driver": None, "cuda": None},
        "result": result, "execution": "software", "hardware_validated": False,
        "notes": "Historical content-free report import, not a new execution or independent "
                 "container verification. Original producer and reported execution timestamps "
                 "retained. Command uses the producer's fixed CLI with private paths normalized; "
                 "literal path values were not recorded. " + observation + " "
                 "No package installation, scripts, guest build or hardware validation. "
                 "output.log is byte-identical to original report.json; SHA256=" + report_sha256
                 + ". Importer git_sha=" + revision + "; importer/helper SHA256="
                 + json.dumps({n: base.digest(b) for n, b in sorted(sources.items())}, sort_keys=True),
    }
    base.require(len(run["notes"]) <= 4096)
    return {"run.json": base.encoded(run), "output.log": raw}


def main():
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            self.exit(2, "Invalid historical report import arguments.\n")
    parser = Parser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base.require(args.report.is_absolute() and args.output.is_absolute()
                 and args.output != args.report and args.output not in args.report.parents
                 and args.report not in args.output.parents)
    revision, sources = committed()
    raw = base.read(args.report, 16384)
    files = evidence(raw, revision, sources)
    base.publish(args.output, files)
    base.verify(args.output, files)
    print(json.dumps({"recorded": True, "original_result": json.loads(raw)["result"], "rerun": False,
                      "report_sha256": base.digest(raw)}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        sys.exit("Historical APT report import rejected; no APT execution occurred.")
