#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Unchanged pinned provider checks, with a content-free private result channel."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B worker.py MANIFEST_SHA256")

import asyncio
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import resource
import signal
import time
import types

SUPPORT_SHA = "35e784639e7014f4bcb836f12cd8cc533b4f1d7ba9d9d97f66b93325ad8c73ff"
LOCK_SHA = "d5fbea8926b7410c34730593b8ebd305f01b04a160eb30e9867330c45799ade7"
CHECKER_SHA = "591a11295d3f1e0ef20fbc51c788cabfbedf176e79ce56de8378252bd13adecd"
PROFILE_SHA = "11475b07991fb510704d64143fcd9a0084327e586699604f14f88436960b754a"
CONTRACT = "973e380e5ab7e6303576dd7555c186244649befdc2da5c64017611abe9918769"
MODEL = "deepseek-v4.1-flash-1017"
ENDPOINT = "http://127.0.0.1:8000/v1"
SECONDS = 180
ENVIRONMENT = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/scratch",
               "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TMPDIR": "/scratch"}
MANDATORY = {
    "catalog.native-model-discovery": 1, "callability.advertised-models": 2,
    **{name: 3 for name in ("chat.non-empty", "chat.pong", "chat.usage", "chat.model",
                           "chat.finish-reason", "chat.temperature-zero", "chat.forwarded-fields",
                           "chat.max-token-spelling")},
    **{name: 4 for name in ("stream.response-status", "stream.sse-framing", "stream.error-signaling",
                           "stream.first-delta", "stream.done", "stream.usage",
                           "stream.incremental-delivery")},
}
ALLOWED = {**MANDATORY, "catalog.declared-v2": 1}


def require(value):
    if not value:
        raise ValueError("provider checker boundary rejected")


class BoundaryAbort(BaseException):
    """Bypass upstream recovery without returning request-derived exceptions."""


def audit(event, args):
    if event == "socket.connect":
        if args[1] != ("127.0.0.1", 8000):
            raise BoundaryAbort()
    elif event == "socket.getaddrinfo":
        if args[0] != "127.0.0.1" or args[1] not in (8000, "8000"):
            raise BoundaryAbort()
    elif event in ("socket.bind", "socket.gethostbyname", "socket.gethostbyaddr",
                   "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.fork", "os.forkpty"):
        raise BoundaryAbort()


def project(run, upstream_gate, *, model=MODEL):
    """Keep upstream verdicts, never measured content, assertions, errors or model IDs."""
    require(type(upstream_gate) is bool and type(run.checks) is list
            and 0 < len(run.checks) <= len(ALLOWED))
    rows = {}
    native_matches = False
    for row in run.checks:
        require(type(row.id) is str and row.id in ALLOWED and row.id not in rows
                and type(row.tier) is int and row.tier == ALLOWED[row.id]
                and type(row.status) is str and row.status in ("pass", "fail", "warn", "skip"))
        rows[row.id] = row.status
        if row.id == "catalog.native-model-discovery":
            native_matches = (type(row.measured) is dict and row.measured.get("model_ids") == [model]
                              and row.measured.get("unreadable_row_count") == 0
                              and run.selected_model == model)
    require(upstream_gate == ("fail" not in rows.values()))
    accepted = (upstream_gate and native_matches and set(rows) == set(ALLOWED)
                and rows["catalog.declared-v2"] == "skip"
                and all(rows[name] == "pass" for name in MANDATORY))
    return {"checks": dict(sorted(rows.items())), "upstream_conformance_gate": upstream_gate,
            "mandatory_checks_passed": accepted, "native_identity_matches": native_matches}


def result(stage, projection=None):
    return {"schema": "nib.private-provider-check/v1", "stage": stage,
            "checker_version": "0.1.8", "checker_wheel_sha256": CHECKER_SHA,
            "requirements_sha256": LOCK_SHA, "contract_version": CONTRACT,
            "requested_tier": 4, "catalog_checked": False, "projection": projection,
            "endpoint_identity_verified": False, "confidential": False, "quality_accepted": False}


def verify(root, expected):
    require(re.fullmatch(r"[0-9a-f]{64}", expected) is not None)
    # This helper is pinned independently of the caller-supplied manifest.
    path = root / "support.py"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        raw = stream.read((1 << 20) + 1)
    require(hashlib.sha256(raw).hexdigest() == SUPPORT_SHA)
    support = types.ModuleType("provider_support")
    support.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), support.__dict__)
    raw = support.read(root / "trial.json", 1 << 20)
    require(hashlib.sha256(raw).hexdigest() == expected)
    plan = support.decode(raw)
    require(type(plan) is dict and set(plan) in (
                {"schema", "requirements_sha256", "checker_wheel_sha256", "files"},
                {"schema", "requirements_sha256", "checker_wheel_sha256", "files", "model_profile"})
            and plan["schema"] == "nib.private-provider-payload/v1"
            and plan["requirements_sha256"] == LOCK_SHA and plan["checker_wheel_sha256"] == CHECKER_SHA
            and type(plan["files"]) is dict and set(plan["files"]) >= {"worker.py", "support.py"}
            and all(name in ("worker.py", "support.py", "model-profile.py") or name.startswith("packages/")
                    for name in plan["files"]))
    support.verify_tree(root, plan["files"], time.monotonic() + 30)
    require(("model-profile.py" in plan["files"]) == ("model_profile" in plan))
    if "model_profile" in plan:
        raw = support.read(root / "model-profile.py", 1 << 20)
        require(hashlib.sha256(raw).hexdigest() == PROFILE_SHA)
        profiles = types.ModuleType("checker_model_profiles")
        exec(compile(raw, "model-profile.py", "exec", dont_inherit=True), profiles.__dict__)
        require(type(plan["model_profile"]) is dict and plan["model_profile"] == profiles.select("uncensored")
                and type(plan["model_profile"].get("total_bytes")) is int)
    return plan


def execute(root, expected):
    require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
            and sys.version_info[:3] == (3, 12, 13) and sys.platform == "linux"
            and platform.machine() == "x86_64" and os.getuid() == 65534
            and dict(os.environ) == ENVIRONMENT)
    require(sys.path == ["/usr/local/lib/python312.zip", "/usr/local/lib/python3.12",
                         "/usr/local/lib/python3.12/lib-dynload"])
    require(sorted(p.name for p in Path("/sys/class/net").iterdir()) == ["lo"])
    for name, expected_limit in (("memory.max", "1073741824"), ("memory.swap.max", "0"), ("pids.max", "32")):
        require((Path("/sys/fs/cgroup") / name).read_text().strip() == expected_limit)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    plan = verify(root, expected)
    model = plan["model_profile"]["served_model_name"] if "model_profile" in plan else MODEL
    sys.path.append(str(root / "packages"))
    sys.addaudithook(audit)
    from tr_provider_check import __version__
    from tr_provider_check.checks import run_checks
    from tr_provider_check.report import report_document
    from tr_provider_check.snapshot import contract_version
    require(__version__ == "0.1.8" and contract_version == CONTRACT)
    run = asyncio.run(run_checks(base_url=ENDPOINT, api_key=None, model=model,
                                 catalog_url=None, tier=4, max_sweep_models=1))
    gate = report_document(run.checks)["summary"]["conformance_gate"]
    return result("completed", project(run, gate, model=model))


def main():
    # Silence Python AND native writes before reading inputs or importing wheels.
    output = os.dup(1)
    null = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(null, fd)
    os.close(null)
    value = result("rejected")
    def expired(signum, frame):
        raise BoundaryAbort()
    signal.signal(signal.SIGALRM, expired)
    signal.signal(signal.SIGTERM, expired)
    signal.setitimer(signal.ITIMER_REAL, SECONDS)
    try:
        require(len(sys.argv) == 2)
        value = execute(Path(__file__).absolute().parent, sys.argv[1])
    except BaseException:
        pass
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    require(len(raw) <= 16384)
    with os.fdopen(output, "wb") as stream:
        stream.write(raw)
    if value["stage"] != "completed":
        return 2
    return 0 if value["projection"]["mandatory_checks_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
