#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Probe the pinned sandbox on a disposable, unprivileged native Linux runner."""
import sys

if __name__ == "__main__" and not (
        sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B host/probe-mkosi-native.py")

import argparse
import json
import os
from pathlib import Path
import platform
import runpy
import subprocess
import tempfile
import time
import types

ROOT = Path(__file__).absolute().parent.parent
HELPER = "scripts/prepare_mkosi_inputs.py"
SCRIPT = "host/probe-mkosi-native.py"
WORKER = "scripts/mkosi_tools_probe.py"
CHECKER = "scripts/mkosi_namespace_check.py"
PYTHON = "/usr/bin/python3.10"
mkosi = types.SimpleNamespace(**runpy.run_path(str(ROOT / HELPER), run_name="mkosi_inputs"))
base, tools, require = mkosi.base, mkosi.tools, mkosi.require
worker, WORKER_SOURCE = base.helper("native_mkosi_worker", WORKER)


def inputs(archive):
    revision, sources = tools.committed((*mkosi.EXTRA, SCRIPT, WORKER, CHECKER))
    require(sources[tools.FILES[0]] == mkosi.TOOLS_SOURCE and sources[WORKER] == WORKER_SOURCE)
    pin = json.loads(sources[mkosi.EXTRA[1]])
    summary, upstream = mkosi.verify_source(base.read(archive, mkosi.MAX_SOURCE), pin)
    files = {"mkosi-sandbox.py": upstream["mkosi/sandbox.py"],
             "namespace-check.py": sources[CHECKER]}
    files.update({name: upstream[name] for name in pin["license_files"]})
    return revision, files, summary


def preflight():
    require(sys.platform == "linux" and platform.machine() == "x86_64"
            and sys.version_info[:2] == (3, 10) and os.geteuid() != 0
            and os.getegid() != 0 and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted")
    release = platform.freedesktop_os_release()
    require(release.get("ID") == "ubuntu" and release.get("VERSION_ID") == "22.04"
            and Path(sys.executable).resolve() == Path(PYTHON).resolve())


def probe(payload, execute):
    report = {"schema": "nib.mkosi-native-probe/v1", "result": "fail",
              "stage": "namespace-lookup", "failure_kind": None,
              "mkosi_executed": False, "namespaces_validated": False,
              "process_cleanup_confirmed": False, "payload_removed": False,
              "host_runtime_pinned": False, "package_installation_executed": False,
              "maintainer_scripts_executed": False, "mkosi_build_executed": False,
              "bootable": False, "deployable": False}
    try:
        argv = worker.sandbox_command(payload, namespace_only_proc=True, python=PYTHON)
        report.update(stage="sandbox", mkosi_executed=True)
        result = execute(argv, env=dict(worker.ENV), timeout=10,
                         max_output_bytes=worker.MAX_OUTPUT, stdin=subprocess.DEVNULL)
        report["process_cleanup_confirmed"] = True
        result.check_returncode()
        require(result.stdout == worker.SANDBOX_SUCCESS and result.stderr == b"")
        report.update(stage="complete", result="pass", namespaces_validated=True)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        kind = "invalid-result-or-cleanup"
        if isinstance(exc, subprocess.TimeoutExpired):
            kind = "timeout"
        elif isinstance(exc, subprocess.CalledProcessError):
            error = exc.stderr if isinstance(exc.stderr, bytes) else b""
            kind = ("permission-denied" if any(s in error for s in
                    (b"Operation not permitted", b"Permission denied", b"forbidden to unshare"))
                    else "process-exit")
        elif isinstance(exc, OSError):
            kind = "startup-error"
        report["failure_kind"] = kind
    return report


def run(files, execute):
    # Only public authenticated code is exposed to the sandbox; not the checkout,
    # runner home, credential files, network or writable host storage.
    with tempfile.TemporaryDirectory(prefix="nib-mkosi-native-", dir="/tmp") as temporary:
        payload = Path(temporary).resolve() / "payload"
        base.publish(payload, files)
        report = probe(payload, execute)
    require(not payload.parent.exists())
    report["payload_removed"] = True
    return report


def evidence(report, revision, started, ended):
    return {"schema": "nib.evidence/v1", "phase": "P3", "task": "T1", "git_sha": revision,
            "command": "/usr/bin/python3.10 -I -S -B host/probe-mkosi-native.py --archive PRIVATE --output PRIVATE",
            "started_utc": started, "ended_utc": ended, "result": report["result"],
            "execution": "software", "hardware_validated": False,
            "host": {"provider": "github-actions", "machine": "ubuntu-22.04-x86_64",
                     "gpus": "none", "driver": None, "cuda": None},
            "notes": "Pinned mkosi sandbox and synthetic checker on an unprivileged ephemeral runner. "
                     "The runner kernel, Python and userspace are not pinned build inputs. "
                     "No package installation, image build, boot or attestation. No host security changes."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    preflight()
    revision, files, summary = inputs(args.archive.absolute())
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report = run(files, base.processes.execute_process_group)
    ended = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report.update(git_sha=revision, source=summary,
                  payload_sha256={name: base.digest(raw) for name, raw in files.items()})
    base.publish(args.output.absolute(), {"run.json": base.encoded(evidence(report, revision, started, ended)),
                                        "result.json": base.encoded(report)})
    print("Native namespace probe: " + report["result"], flush=True)
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        sys.exit("Native namespace probe rejected; no image-build qualification.")
