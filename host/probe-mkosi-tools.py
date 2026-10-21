#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Execute the reverified fixed tools probe in the existing offline Docker sandbox."""
import sys

if __name__ == "__main__" and not sys.flags.isolated:
    sys.exit("Use python3 -I host/probe-mkosi-tools.py")

import argparse
import json
from pathlib import Path
import re
import subprocess
import tarfile
import types

ROOT = Path(__file__).absolute().parent.parent
SCRIPT = "host/probe-mkosi-tools.py"
PREPARER = "scripts/prepare_mkosi_probe.py"
LIFECYCLE = "host/simulate-guest-apt.py"


def load(path):
    # The first helper supplies bounded no-follow reads; all sources are checked
    # against the committed tree before preparation or Docker access.
    import os
    import stat
    fd = os.open(ROOT / path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size < 1 << 20:
            raise ValueError("probe-bootstrap-rejected")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read((1 << 20) + 1)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_nlink,
                              s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if len(raw) != before.st_size or identity(before) != identity(os.fstat(fd)):
            raise ValueError("probe-bootstrap-rejected")
    finally:
        os.close(fd)
    module = types.ModuleType("tools_probe_host_helper")
    module.__file__ = str(ROOT / path)
    exec(compile(raw, module.__file__, "exec", dont_inherit=True), module.__dict__)
    return module, raw


prep, PREPARER_SOURCE = load(PREPARER)
runner, LIFECYCLE_SOURCE = load(LIFECYCLE)
worker, WORKER_SOURCE = load(prep.WORKER)
base, require = prep.base, prep.require


def validate_result(value, receipt):
    keys = {"schema", "git_sha", "tools_receipt_sha256", "result", "stage",
            "checks", "programs_attempted", *worker.FALSE_CLAIMS}
    require(type(value) is dict and set(value) == keys
            and value["schema"] == "nib.mkosi-tools-probe/v1"
            and value["git_sha"] == receipt["git_sha"]
            and value["tools_receipt_sha256"] == receipt["tools_receipt_sha256"]
            and all(value[k] is False for k in worker.FALSE_CLAIMS))
    names = [name for name, _ in worker.COMMANDS]
    checks, attempted = value["checks"], value["programs_attempted"]
    require(type(checks) is list and type(attempted) is int
            and 0 <= len(checks) <= attempted <= len(names))
    for name, row in zip(names, checks):
        require(type(row) is dict and set(row) == {
            "program", "result", "stdout_bytes", "stdout_sha256"}
            and row["program"] == name and row["result"] == "pass"
            and type(row["stdout_bytes"]) is int and 0 < row["stdout_bytes"] <= worker.MAX_OUTPUT
            and type(row["stdout_sha256"]) is str
            and re.fullmatch("[0-9a-f]{64}", row["stdout_sha256"]))
    if value["result"] == "pass":
        require(value["stage"] == "complete" and attempted == len(checks) == len(names))
    else:
        require(value["result"] == "fail" and len(checks) < len(names)
                and value["stage"] == names[len(checks)]
                and attempted in (len(checks), len(checks) + 1))


def execute(files, receipt, call):
    report = {"schema": "nib.mkosi-tools-container-run/v1", "git_sha": receipt["git_sha"],
              "result": "fail", "stage": "preflight", "cleanup": "not-created", "probe": None,
              "image_archive_sha256": receipt["files"]["image.tar"]["sha256"],
              "config_sha256": receipt["config_sha256"], "layer_sha256s": receipt["layer_sha256s"],
              **{name: False for name in worker.FALSE_CLAIMS}}
    spec = {"tag_prefix": "nib-tools-startup-probe", "name_prefix": "nib-tools-probe-",
            "label_key": "org.nib.tools-probe-run", "result_key": "probe",
            "entrypoint": ["/usr/bin/python3.12", "-I", "-S", "-B", "/nib/probe.py"],
            "layers": receipt["layer_sha256s"]}
    return runner.execute_image(files["image.tar"], receipt, call, spec, report,
                                lambda value: validate_result(value, receipt))


def evidence_files(report):
    envelope = {
        "schema": "nib.evidence/v1", "phase": "P3", "task": "T1", "git_sha": report["git_sha"],
        "command": "python3 -I -S -B host/probe-mkosi-tools.py --run --inputs PRIVATE "
                   "--bundle PRIVATE --output PRIVATE",
        "started_utc": report["started_at"], "ended_utc": report["finished_at"],
        "host": {"provider": "local-docker", "machine": "linux-amd64",
                 "gpus": "none", "driver": None, "cuda": None},
        "result": report["result"], "execution": "software", "hardware_validated": False,
        "notes": "Seven fixed tools startup probes only. No package installation, maintainer "
                 "scripts, mkosi build, namespace validation, ABI qualification, boot or attestation. "
                 "Private command paths are normalized; output.log contains fixed classes, counts "
                 "and hashes, never raw process output.",
    }
    return {"run.json": base.encoded(envelope), "output.log": base.encoded(report)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    for name in ("inputs", "bundle", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    prep.tools.separate_output(args.output, (args.inputs, args.bundle))
    require(not args.output.exists() and not args.output.is_symlink())
    revision, sources = prep.committed()
    require(sources[PREPARER] == PREPARER_SOURCE and sources[LIFECYCLE] == LIFECYCLE_SOURCE
            and sources[prep.WORKER] == WORKER_SOURCE
            and sources[prep.HELPER] == prep.HELPER_SOURCE
            and sources[prep.prep.HELPER] == prep.prep.HELPER_SOURCE
            and sources[prep.tools.FILES[0]] == prep.prep.mkosi.TOOLS_SOURCE
            and sources[runner.prep.FILES[0]] == runner.PREPARER_SOURCE
            and sources[runner.prep.FILES[1]] == runner.SIMULATION_SOURCE)
    # Reconstruct, not merely trust hashes written beside a received archive.
    files, receipt = prep.build(args.inputs, revision, sources)
    base.verify(args.bundle, files)
    report = runner.run_local(lambda call: execute(files, receipt, call))
    base.publish(args.output, evidence_files(report))
    print(json.dumps({k: report[k] for k in ("result", "stage", "cleanup", "git_sha", "deployable")},
                     sort_keys=True))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, EOFError, UnicodeError,
            tarfile.TarError, subprocess.SubprocessError):
        sys.exit("Tools probe rejected; no boot or installation success may be inferred.")
