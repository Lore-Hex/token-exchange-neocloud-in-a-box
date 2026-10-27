# SPDX-License-Identifier: Apache-2.0
"""Private, bounded original-byte combined receipts. No legacy cap changes."""

import base64
from contextlib import contextmanager
import datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import time

import selected_combined_capacity as capacity
import selected_long_qualification as long
import stream_scaling as scaling
import tau_runtime

require = capacity.require
SCHEMA = "nib.selected-combined-owner/v1"
LIMITS = {"long": long.MAX_REPORT, "scaling": 30 << 20, "lifecycle": 16384,
          **{"capacity-" + str(i): capacity.capacity.LIMIT for i in range(4)}}
MAX_RECEIPT = 52 << 20
MAX_CAPTURE = MAX_RECEIPT + 65536
STAGES = ("contract", "preparation", "startup", "long", "throughput", "service-cleanup", "terminal")
FAILURES = ("none", "contract", "preparation", "startup", "long", "throughput", "identity",
            "deadline", "cancelled", "cleanup", "publication", "internal")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(row):
    return json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def decode(raw, limit):
    require(type(raw) is bytes and 0 < len(raw) <= limit)
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result
    def constant(_):
        raise ValueError("combined receipt rejected")
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("combined receipt rejected") from None


def component(name, raw):
    require(name in LIMITS and type(raw) is bytes and 0 < len(raw) <= LIMITS[name])
    return {"bytes": len(raw), "sha256": digest(raw), "base64": base64.b64encode(raw).decode("ascii")}


def original(name, row):
    require(name in LIMITS and type(row) is dict and set(row) == {"bytes", "sha256", "base64"}
            and type(row["bytes"]) is int and 0 < row["bytes"] <= LIMITS[name]
            and type(row["base64"]) is str and len(row["base64"]) <= 4 * ((LIMITS[name] + 2) // 3))
    try:
        raw = base64.b64decode(row["base64"], validate=True)
    except ValueError:
        raise ValueError("combined receipt rejected") from None
    require(len(raw) == row["bytes"] and digest(raw) == row["sha256"]
            and base64.b64encode(raw).decode("ascii") == row["base64"])
    return raw


def empty(prepared_sha256):
    scaling.binding.sha(prepared_sha256)
    return {"schema": SCHEMA, "execution_profile": capacity.plan.PROFILE,
        "prepared_sha256": prepared_sha256, "stage": "contract", "failure": "contract",
        "result": "fail", "cancelled": False, "cleanup_confirmed": False,
        "terminal_identity_verified": False, "engine_identity_sha256": None,
        "deadline_plan": None, "components": {}, "known_prompt_tokens": 0,
        "known_output_tokens": 0, "requests_with_unknown_usage": 0,
        "context_tokens": 131072, "public_max_inflight": 1,
        "hardware_qualified": False, "public_admission_changed": False,
        "confidential": False, "pricing_measured": False}


def totals(row, long_row, scaling_row):
    row["known_prompt_tokens"] = sum(r["known_prompt_tokens"] for r in (long_row, scaling_row) if r)
    row["known_output_tokens"] = sum(r["known_output_tokens"] for r in (long_row, scaling_row) if r)
    row["requests_with_unknown_usage"] = ((long_row["usage_unknown_requests"] if long_row else 0)
        + (scaling_row["requests_with_unknown_usage"] if scaling_row else 0))


def validate(raw, expected=None):
    row = decode(raw, MAX_RECEIPT)
    require(type(row) is dict)
    template = empty(row.get("prepared_sha256"))
    require(set(row) == set(template) and (expected is None or row["prepared_sha256"] == expected))
    variable = {"prepared_sha256", "stage", "failure", "result", "cancelled", "cleanup_confirmed",
        "terminal_identity_verified", "engine_identity_sha256", "deadline_plan", "components",
        "known_prompt_tokens", "known_output_tokens", "requests_with_unknown_usage"}
    require(all(type(row[k]) is type(v) and row[k] == v for k, v in template.items() if k not in variable))
    require(row["stage"] in STAGES and row["failure"] in FAILURES and row["result"] in ("pass", "fail")
            and all(type(row[k]) is bool for k in ("cancelled", "cleanup_confirmed", "terminal_identity_verified"))
            and type(row["components"]) is dict and set(row["components"]) <= set(LIMITS))
    if row["engine_identity_sha256"] is not None:
        scaling.binding.sha(row["engine_identity_sha256"])
    if row["deadline_plan"] is not None:
        clocks = row["deadline_plan"]
        require(type(clocks) is dict and set(clocks) == {"preparation_seconds", "now_wall", "now_monotonic",
                                                       "hard_wall", "hard_monotonic"})
        capacity.plan.DeadlinePlan.begin(execution_profile=capacity.plan.PROFILE, **clocks)
    data = {name: original(name, value) for name, value in row["components"].items()}
    l = long.validate_combined_report(data["long"], row["prepared_sha256"]) if "long" in data else None
    s = decode(data["scaling"], LIMITS["scaling"]) if "scaling" in data else None
    if s is not None:
        scaling.validate_combined(s)
        require(l is not None and l["checks_pass"] and s["prepared_sha256"] == row["prepared_sha256"]
                and s["engine_identity_sha256"] == row["engine_identity_sha256"])
    if l is not None and l["samples"]:
        require(l["engine_identity_before_sha256"] == row["engine_identity_sha256"])
    if l is not None and l["checks_pass"]:
        require(l["engine_identity_after_sha256"] == row["engine_identity_sha256"])
    observed = ([] if s is None else [s["capacity"], *[p["capacity_after"] for p in s["plateaus"]]])
    names = ["capacity-" + str(i) for i in range(4) if "capacity-" + str(i) in data]
    require(names == ["capacity-" + str(i) for i in range(len(names))])
    for index, observation in enumerate(observed):
        if observation is not None:
            require(index < len(names))
            parsed = capacity.parse(data[names[index]], execution_profile=capacity.plan.PROFILE, model=long.service.MODEL)
            require(parsed == observation)
    lifecycle = None
    if "lifecycle" in data:
        lifecycle = long.service.correctness.strict_json(data["lifecycle"])
        require(type(lifecycle) is dict and lifecycle.get("schema") == long.service.EXECUTION_SCHEMA
                and lifecycle.get("lifecycle_schema") == long.service.ORDINARY_SCHEMA
                and lifecycle.get("execution") == long.process.engine_entry.execution_limits(long.process.engine_entry.LONG_GRAPHS_PROFILE)
                and lifecycle.get("prepared_sha256") == row["prepared_sha256"]
                and lifecycle.get("execution_prepared_sha256") == row["prepared_sha256"]
                and type(lifecycle.get("cleanup_confirmed")) is bool)
        require(set(lifecycle) == {"schema", "lifecycle_schema", "execution", "execution_prepared_sha256",
            "reason", "status", "ready", "cleanup_confirmed", "cleanup", "deployable", "hardware_validated",
            "tools", "reasoning", "zdr", "confidential", "e2ee", "lifetime", "prepared_sha256"}
            and all(lifecycle[k] is False for k in ("deployable", "hardware_validated", "tools", "reasoning",
                                                    "zdr", "confidential", "e2ee"))
            and type(lifecycle["ready"]) is bool and lifecycle["status"] in ("retired", "cancelled", "failed")
            and lifecycle["reason"] in long.service.FAILURES | {"retired"}
            and type(lifecycle["cleanup"]) is list and len(lifecycle["cleanup"]) <= 3)
        roles = []
        for item in lifecycle["cleanup"]:
            require(type(item) is dict and set(item) == {"role", "drained", "reaped"}
                    and item["role"] in long.service.ROLES
                    and type(item["drained"]) is bool and type(item["reaped"]) is bool)
            roles.append(item["role"])
        require(roles == list(reversed(long.service.ROLES[:len(roles)])))
        lifetime = lifecycle["lifetime"]
        require(type(lifetime) is dict and set(lifetime) == {"profile", "hard_stop_at", "admission_stop_at"}
                and lifetime["profile"] == long.service.ORDINARY_PROFILE and row["deadline_plan"] is not None)
        stamps = []
        for name in ("hard_stop_at", "admission_stop_at"):
            require(type(lifetime[name]) is str and len(lifetime[name]) == 20 and lifetime[name].endswith("Z"))
            stamps.append(datetime.datetime.strptime(lifetime[name], "%Y-%m-%dT%H:%M:%SZ")
                          .replace(tzinfo=datetime.timezone.utc).timestamp())
        clocks = row["deadline_plan"]
        maximum = min(clocks["hard_wall"], clocks["now_wall"] + clocks["preparation_seconds"] + capacity.plan.BASE_SECONDS)
        require(stamps[0] <= maximum - 180 and stamps[0] - stamps[1] == 1960)
        require(row["cleanup_confirmed"] == lifecycle["cleanup_confirmed"])
    derived = dict(row)
    totals(derived, l, s)
    require(all(type(row[k]) is int and row[k] == derived[k] for k in
                ("known_prompt_tokens", "known_output_tokens", "requests_with_unknown_usage")))
    if row["result"] == "pass":
        require(row["failure"] == "none" and row["stage"] == "terminal" and not row["cancelled"]
                and row["cleanup_confirmed"] and row["terminal_identity_verified"]
                and row["deadline_plan"] is not None and row["engine_identity_sha256"] is not None
                and l is not None and l["checks_pass"] and s is not None and s["completed"]
                and not row["requests_with_unknown_usage"] and len(names) == 4
                and lifecycle is not None and lifecycle.get("status") == "retired"
                and lifecycle.get("ready") is True and lifecycle.get("reason") == "retired"
                and lifecycle.get("cleanup") == [{"role": role, "drained": True, "reaped": True}
                                                for role in reversed(long.service.ROLES)])
    else:
        require(row["failure"] != "none")
    return row


def capture(raw, expected):
    require(type(raw) is bytes and len(raw) <= MAX_CAPTURE)
    lines = raw.splitlines()
    require(1 <= len(lines) <= 16 and sum(map(len, lines[:-1])) <= 65536)
    retained_long = None
    for line in lines[:-1]:
        progress = decode(line, 32768)
        if type(progress) is dict and progress.get("schema") == "nib.selected-combined-progress/v1":
            require(set(progress) == {"schema", "prepared_sha256", "long"}
                    and progress["prepared_sha256"] == expected and retained_long is None)
            retained_long = original("long", progress["long"])
            long.validate_combined_report(retained_long, expected)
            continue
        require(type(progress) is dict and progress.get("schema") == "nib.selected-service-progress/v2"
                and progress.get("prepared_sha256") == expected and progress.get("phase") in long.service.PROGRESS_PHASES
                and progress.get("execution") == long.process.engine_entry.execution_limits(long.process.engine_entry.LONG_GRAPHS_PROFILE))
    row = validate(lines[-1], expected)
    if retained_long is not None:
        require("long" in row["components"] and original("long", row["components"]["long"]) == retained_long)
    return row


@contextmanager
def sink(path):
    """Keep even interrupted output; never replace an original or follow links."""
    path = Path(path)
    with tau_runtime.directory(path.parent) as parent:
        info = os.fstat(parent)
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, "wb") as stream:
            try:
                yield stream
            finally:
                stream.flush()
                os.fsync(stream.fileno())
                os.fsync(parent)


def retain(path, raw, limit):
    """Exclusive private original-byte publication, including failed captures."""
    require(type(raw) is bytes and len(raw) <= limit)
    with sink(path) as stream:
        require(stream.write(raw) == len(raw))


def replay(path, expected):
    raw = tau_runtime.read_file(Path(path), MAX_CAPTURE, time.monotonic() + 30)
    return capture(raw, expected)


def replay_directory(directory, expected):
    """Join the original child bytes to host/watchdog and input cleanup.

    A child PASS is never whole-owner PASS. Missing or failed host publication
    leaves the originals intact and cannot release an unknown reservation.
    """
    scaling.binding.sha(expected)
    directory = Path(directory)
    with tau_runtime.directory(directory) as fd:
        info = os.fstat(fd)
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
        for name in ("intent.json", "capture.jsonl", "host.json"):
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1)
    deadline = time.monotonic() + 30
    intent = decode(tau_runtime.read_file(directory / "intent.json", 4096, deadline), 4096)
    require(type(intent) is dict and set(intent) == {"schema", "preparation_sha256", "build_sha256",
            "execution_profile", "run_requested"}
            and intent["schema"] == "nib.selected-combined-intent/v1"
            and intent["execution_profile"] == capacity.plan.PROFILE and intent["run_requested"] is True)
    for key in ("preparation_sha256", "build_sha256"):
        scaling.binding.sha(intent[key])
    raw = tau_runtime.read_file(directory / "capture.jsonl", MAX_CAPTURE, deadline)
    row = capture(raw, expected)
    host = decode(tau_runtime.read_file(directory / "host.json", MAX_CAPTURE + 16384, deadline),
                  MAX_CAPTURE + 16384)
    require(type(host) is dict and host.get("schema") == "nib.selected-combined-host/v1"
            and host.get("execution_profile") == capacity.plan.PROFILE
            and host.get("prepared_sha256") == expected and host.get("capture_sha256") == digest(raw)
            and host.get("deadline_plan") == row["deadline_plan"]
            and all(host.get(k) == intent[k] for k in ("preparation_sha256", "build_sha256"))
            and host.get("lifecycle") == row)
    passed = (row["result"] == "pass" and host.get("result") == "pass" and host.get("status") == "PASS"
        and host.get("reason") == "completed" and host.get("cleanup") == "confirmed"
        and host.get("capture_complete") is True and host.get("runtime_started") is True
        and host.get("preflight_pass") is True and host.get("container_created") is True
        and host.get("input_cleanup_confirmed") is True and host.get("cancelled", False) is False
        and all(host.get(k) is False for k in ("hardware_validated", "deployable", "published", "tools",
                                              "reasoning", "zdr", "confidential", "e2ee")))
    return {"schema": "nib.selected-combined-replay/v1", "result": "pass" if passed else "fail",
        "capture_sha256": digest(raw), "cleanup_confirmed": passed,
        "hardware_qualified": False, "public_admission_changed": False,
        "known_prompt_tokens": row["known_prompt_tokens"], "known_output_tokens": row["known_output_tokens"],
        "requests_with_unknown_usage": row["requests_with_unknown_usage"]}
