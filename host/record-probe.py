#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Record committed, read-only Linux inventory; never admit hardware or a runtime."""

import sys

if __name__ == "__main__" and not sys.flags.isolated:
    sys.exit("Run with python3 -I host/record-probe.py")

from contextlib import ExitStack, contextmanager
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import types

ROOT = Path(__file__).resolve().parent.parent
RECORDER = "host/record-probe.py"
PROBE = "host/probe.py"
BOOTSTRAP = Path(__file__).read_bytes()
LIMIT = 65536
GIT_ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_NO_REPLACE_OBJECTS": "1",
           "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": "", "GIT_TERMINAL_PROMPT": "0"}
CHECKS = ("gpus", "host_memory_kib", "cpu_vendor", "attestation_interfaces_present")
INTERFACES = {"amd_sev_guest", "intel_tdx_guest", "linux_tsm_report"}
FLAGS = {"confidential_qualified", "hardware_attestation_verified", "runtime_ready"}
MARKER = b"\nNIB_PROBE_EXIT="
# Preserve both 0 and 1 reports while reusing probe.capture's output/deadline/
# process-group bounds. Raw stderr is counted and discarded by that helper.
LAUNCHER = """
import subprocess, sys
code = subprocess.call([sys.executable, '-I', '-B', sys.argv[1]], stdin=subprocess.DEVNULL)
print('\\nNIB_PROBE_EXIT=' + str(code), flush=True)
"""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git(root, *args):
    result = subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(root), *args],
                            stdin=subprocess.DEVNULL, env=GIT_ENV, capture_output=True, timeout=10)
    if result.returncode or len(result.stdout) > LIMIT or len(result.stderr) > LIMIT:
        raise ValueError("committed-source-unavailable")
    return result.stdout


def sources(root):
    revision = git(root, "rev-parse", "--verify", "HEAD^{commit}").decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or revision == "0" * 40:
        raise ValueError("invalid-source-revision")
    result = {}
    for name in (RECORDER, PROBE):
        entry = git(root, "ls-tree", revision, "--", name).decode("ascii").strip().split()
        if len(entry) != 4 or entry[0] not in ("100644", "100755") or entry[1] != "blob" or entry[3] != name:
            raise ValueError("regular-committed-source-required")
        oid = entry[2]
        if not re.fullmatch(r"[0-9a-f]{40}", oid) or not 0 < int(git(root, "cat-file", "-s", oid)) <= LIMIT:
            raise ValueError("invalid-source-size")
        result[name] = git(root, "cat-file", "blob", oid)
    if result[RECORDER] != BOOTSTRAP:
        raise ValueError("recorder-source-mismatch")
    return revision, result


def load_probe(data, filename):
    # No imports from the checkout or ignored timestamp-valid bytecode.
    module = types.ModuleType("committed_hardware_probe")
    module.__file__ = str(filename)
    exec(compile(data, str(filename), "exec"), module.__dict__)
    return module


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def utc_text(value):
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|\+00:00)", value):
        raise ValueError("invalid-probe-time")
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate-report-key")
        result[key] = value
    return result


def validate_output(data, source_digest, started, ended):
    if not isinstance(data, bytes) or len(data) > LIMIT:
        raise ValueError("invalid-probe-output")
    raw, marker, status = data.rpartition(MARKER)
    if not marker or status not in (b"0\n", b"1\n"):
        raise ValueError("invalid-probe-status")
    report = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    keys = {"schema", "scope", "checks", "errors", "eight_b200_reported", "result",
            "started_utc", "ended_utc", "script_sha256"} | FLAGS
    if (not isinstance(report, dict) or set(report) != keys
            or report["schema"] != "nib.hardware-inventory/v1" or report["scope"] != "read-only-inventory"
            or any(report[key] is not False for key in FLAGS) or report["script_sha256"] != source_digest):
        raise ValueError("invalid-probe-contract")
    first, last = timestamp(report["started_utc"]), timestamp(report["ended_utc"])
    if not started <= first <= last <= ended:
        raise ValueError("inconsistent-probe-time")
    checks, errors = report["checks"], report["errors"]
    if (not isinstance(checks, dict) or not set(checks) <= set(CHECKS) or not isinstance(errors, list)
            or errors != [name + "-unavailable" for name in CHECKS if name not in checks]):
        raise ValueError("invalid-probe-checks")
    gpus = checks.get("gpus", [])
    if "gpus" in checks:
        if not isinstance(gpus, list) or not 1 <= len(gpus) <= 8:
            raise ValueError("invalid-gpu-count")
        for gpu in gpus:
            if (not isinstance(gpu, dict) or set(gpu) != {"family", "memory_mib", "driver"}
                    or gpu["family"] not in ("B200", "B300", "H100", "H200", "unrecognized")
                    or type(gpu["memory_mib"]) is not int or not 1 <= gpu["memory_mib"] <= 1048576
                    or not isinstance(gpu["driver"], str)
                    or not re.fullmatch(r"[0-9]{3}\.[0-9]{1,3}(?:\.[0-9]{1,3})?", gpu["driver"])):
                raise ValueError("invalid-gpu-values")
    if "host_memory_kib" in checks and (type(checks["host_memory_kib"]) is not int
                                       or not 0 < checks["host_memory_kib"] < 2**50):
        raise ValueError("invalid-memory-value")
    if "cpu_vendor" in checks and checks["cpu_vendor"] not in ("amd", "intel", "unrecognized"):
        raise ValueError("invalid-cpu-value")
    if "attestation_interfaces_present" in checks:
        interfaces = checks["attestation_interfaces_present"]
        if (not isinstance(interfaces, dict) or set(interfaces) != INTERFACES
                or any(type(value) is not bool for value in interfaces.values())):
            raise ValueError("invalid-interface-values")
    eight = len(gpus) == 8 and all(gpu["family"] == "B200" for gpu in gpus)
    passed = eight and not errors
    if (report["eight_b200_reported"] is not eight or report["result"] != ("pass" if passed else "fail")
            or status != (b"0\n" if passed else b"1\n")):
        raise ValueError("inconsistent-probe-result")
    report["started_utc"], report["ended_utc"] = utc_text(first), utc_text(last)
    return report


@contextmanager
def destination(root, started):
    # Same confinement/private/no-overwrite contract as nib evidence new, using
    # Unix dir_fd operations so the remote host needs no Go toolchain or build.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        def keep(fd):
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
                raise ValueError("private-owned-evidence-directory-required")
            return fd
        parent = keep(os.open(root, flags))
        name = started.strftime("%Y-%m-%d-hardware-probe-%H%M%S%f")
        for component in ("evidence", "p2", name):
            try:
                os.mkdir(component, 0o700, dir_fd=parent)
            except FileExistsError:
                if component == name:
                    raise ValueError("evidence-already-exists") from None
            parent = keep(os.open(component, flags, dir_fd=parent))
        yield parent, "evidence/p2/" + name


def write_json(directory, name, value):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    with os.fdopen(fd, "w", encoding="ascii") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def unchanged_snapshot(path, expected):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != len(expected) or stream.read(LIMIT + 1) != expected:
            raise ValueError("executed-source-changed")


def record(root, revision, source, *, now=utc_now, capture=None):
    started = now()
    binding = {"git_sha": revision, "probe_sha256": digest(source[PROBE]), "recorder_sha256": digest(source[RECORDER])}
    report = {"schema": "nib.probe-recording/v1", "source": binding, "result": "fail", "inventory": None,
              "hardware_validated": False, "confidential_qualified": False, "runtime_ready": False}
    with destination(root, started) as (directory, relative):
        try:
            with tempfile.TemporaryDirectory(prefix="nib-probe-source-") as temporary:
                path = Path(temporary) / "probe.py"
                path.write_bytes(source[PROBE])
                path.chmod(0o400)
                probe = load_probe(source[PROBE], path)
                execute = probe.capture if capture is None else capture
                data = execute([sys.executable, "-I", "-B", "-c", LAUNCHER, str(path)], timeout=30)
                unchanged_snapshot(path, source[PROBE])
                report["inventory"] = validate_output(data, binding["probe_sha256"], started, now())
                report["result"] = report["inventory"]["result"]
        except (Exception, KeyboardInterrupt):
            # Untrusted exceptions and stdout/stderr never become evidence.
            report["result"] = "fail"
            report["inventory"] = None
            report["failure_code"] = "probe-execution-or-report-failed"
        ended = now()
        if ended < started:
            report.update(result="fail", inventory=None, failure_code="recorder-clock-regressed")
            ended = started
        run = {"schema": "nib.evidence/v1", "phase": "P2", "task": "T2", "git_sha": revision,
               "command": "python3 -I host/record-probe.py", "started_utc": utc_text(started),
               "ended_utc": utc_text(ended), "result": report["result"], "execution": "software",
               "hardware_validated": False,
               "host": {"provider": "operator-approved-test-host", "machine": "linux",
                        "gpus": "reported inventory only", "driver": None, "cuda": None},
               "notes": "Read-only software inventory, not GPU execution, attestation, qualification or runtime admission. "
                        "Pass means only eight B200s reported and inventory reads succeeded. Host/OS/tools remain trusted. "
                        "Probe SHA-256: " + binding["probe_sha256"] + "; recorder SHA-256: " + binding["recorder_sha256"]}
        write_json(directory, "output.log", report)
        write_json(directory, "run.json", run)  # Never leave a success record without its sanitized output.
        os.fsync(directory)
    return relative, 0 if report["result"] == "pass" else 1


def main():
    if sys.platform != "linux" or len(sys.argv) != 1:
        raise ValueError("approved-Linux-test-host-required")
    revision, source = sources(ROOT)
    relative, code = record(ROOT, revision, source)
    print(relative)
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit("Probe recording failed; no hardware or runtime acceptance may be inferred.")
