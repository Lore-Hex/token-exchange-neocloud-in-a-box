#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read-only Linux inventory. This is not cryptographic attestation."""

import sys

if __name__ == "__main__" and not sys.flags.isolated:
    sys.exit("Run with python3 -I host/probe.py")

import csv
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import time

LIMIT = 65536
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}
# Keep the process-group leader alive after the command exits, until the parent
# kills the group. This avoids both PID reuse and macOS zombie-group EPERM.
# A private liveness pipe also kills nested command groups if their caller dies.
SUPERVISOR = """
import os, select, signal, subprocess, sys
fd, life = int(sys.argv[1]), int(sys.argv[2])
try:
    try:
        child = subprocess.Popen(sys.argv[3:], stdin=subprocess.DEVNULL)
    except OSError:
        child, code = None, 127
    while child is not None:
        code = child.poll()
        if code is not None:
            break
        if select.select([life], [], [], 0.1)[0]:
            raise SystemExit(1)
    os.write(fd, str(code).encode('ascii'))
    os.close(fd)
    os.close(1)
    os.close(2)
    select.select([life], [], [])
finally:
    os.killpg(os.getpid(), signal.SIGKILL)
"""


def capture(argv, timeout=15, *, env=None, limit=LIMIT):
    """Bound both streams together; never emit diagnostics from the child."""
    reader, writer = os.pipe()
    try:
        life_reader, life_writer = os.pipe()
    except BaseException:
        os.close(reader)
        os.close(writer)
        raise
    try:
        process = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", SUPERVISOR,
                                    str(writer), str(life_reader), *argv],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=ENV if env is None else env,
                                   start_new_session=True,
                                   pass_fds=(writer, life_reader))
    except BaseException:
        os.close(reader)
        os.close(life_writer)
        raise
    finally:
        os.close(writer)
        os.close(life_reader)
    try:
        output = bytearray()
        status = bytearray()
        count = 0
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            selector.register(reader, selectors.EVENT_READ, "status")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("probe-timeout")
                for key, _ in selector.select(min(remaining, 0.25)):
                    chunk = os.read(key.fd, 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    count += len(chunk)
                    if count > limit:
                        raise ValueError("probe-output-limit")
                    if key.data == "stdout":
                        output.extend(chunk)
                    elif key.data == "status":
                        status.extend(chunk)
        if status != b"0":
            raise ValueError("probe-command-failed")
        return bytes(output)
    finally:
        cleanup_error = False
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            cleanup_error = True
        os.close(life_writer)
        process.stdout.close()
        process.stderr.close()
        os.close(reader)
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            raise ValueError("probe-cleanup-timeout") from None
        if cleanup_error:
            raise ValueError("probe-cleanup-failed") from None


def gpu_inventory(data):
    """Publish only recognized model families, numbers and driver versions."""
    rows = list(csv.reader(io.StringIO(data.decode("ascii"))))
    if not 1 <= len(rows) <= 8:
        raise ValueError("invalid-gpu-count")
    result = []
    for row in rows:
        if len(row) != 3:
            raise ValueError("invalid-gpu-row")
        name, memory, driver = (part.strip() for part in row)
        if (not re.fullmatch(r"[0-9]{1,7}", memory)
                or not 1 <= int(memory) <= 1048576
                or not re.fullmatch(r"[0-9]{3}\.\d{1,3}(?:\.\d{1,3})?", driver)):
            raise ValueError("invalid-gpu-values")
        family = next((value for value in ("B200", "B300", "H100", "H200")
                       if re.search(r"\b" + value + r"\b", name)), "unrecognized")
        result.append({"family": family, "memory_mib": int(memory), "driver": driver})
    return result


def memory_kib(data):
    matches = re.findall(rb"^MemTotal:\s+([0-9]+) kB$", data, re.MULTILINE)
    if len(matches) != 1 or not 0 < int(matches[0]) < 2**50:
        raise ValueError("invalid-host-memory")
    return int(matches[0])


def cpu_vendor(data):
    values = set(re.findall(rb"^vendor_id\s*:\s*(\S+)\s*$", data, re.MULTILINE))
    if values == {b"GenuineIntel"}:
        return "intel"
    if values == {b"AuthenticAMD"}:
        return "amd"
    return "unrecognized"


def bounded_read(path, limit):
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("probe-input-limit")
    return data


def collect(run=capture, read=bounded_read, exists=Path.exists):
    report = {"schema": "nib.hardware-inventory/v1", "scope": "read-only-inventory",
              "confidential_qualified": False, "hardware_attestation_verified": False,
              "runtime_ready": False, "checks": {}, "errors": []}
    checks = report["checks"]
    # NVIDIA documents query fields and units in nvidia-smi's official manual.
    # No GPU UUIDs, serials, PCI addresses, hostname, IP or environment is retained.
    tasks = {
        "gpus": lambda: gpu_inventory(run(["/usr/bin/nvidia-smi",
            "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"])),
        "host_memory_kib": lambda: memory_kib(read(Path("/proc/meminfo"), LIMIT)),
        "cpu_vendor": lambda: cpu_vendor(read(Path("/proc/cpuinfo"), 2 << 20)),
        "attestation_interfaces_present": lambda: {
            "amd_sev_guest": exists(Path("/dev/sev-guest")),
            "intel_tdx_guest": exists(Path("/dev/tdx_guest")),
            "linux_tsm_report": exists(Path("/sys/kernel/config/tsm/report")),
        },
    }
    for name, task in tasks.items():
        try:
            checks[name] = task()
        except (OSError, ValueError, UnicodeError, csv.Error, subprocess.SubprocessError):
            report["errors"].append(name + "-unavailable")
    report["eight_b200_reported"] = (len(checks.get("gpus", [])) == 8
                                      and all(g["family"] == "B200" for g in checks["gpus"]))
    report["result"] = "pass" if not report["errors"] and report["eight_b200_reported"] else "fail"
    return report


def main():
    if sys.platform != "linux" or len(sys.argv) != 1:
        sys.exit("Run without arguments on the approved Linux test node.")
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    report = collect()
    report.update(started_utc=started,
                  ended_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
