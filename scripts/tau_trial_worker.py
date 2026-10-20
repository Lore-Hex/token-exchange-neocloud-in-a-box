#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""One scripted tau trial; third-party code runs only in the reviewed container."""

import sys
import contextlib
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import stat
import time
import types

MAX_REQUEST = 1 << 20
MAX_RESPONSE = 256 << 10
MAX_FILES = 11000
MAX_PAYLOAD = 512 << 20
SECONDS = 180
MODEL_ROLES = {"nib-scripted-agent": "agent", "nib-scripted-simulator": "simulator",
               "nib-scripted-evaluator": "evaluator"}
STATUSES = ("evaluated", "infrastructure_error", "timeout", "truncated",
            "protocol_error", "budget_abort", "cancelled")


def require(value):
    if not value:
        raise ValueError("tau trial rejected")


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def unique(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value)
        value[key] = item
    return value


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: require(False))


def bounded_json(value, limit):
    # Bound traversal before serialization and reject arbitrary objects/descriptors.
    remaining = [20000, limit]
    def visit(item, depth):
        remaining[0] -= 1
        require(remaining[0] >= 0 and depth <= 24)
        if item is None or type(item) in (bool, int):
            require(type(item) is not int or abs(item) <= 10**15)
            remaining[1] -= 24
        elif type(item) is str:
            remaining[1] -= len(item)
        elif type(item) in (list, dict):
            for key in item:
                visit(key, depth + 1)
                if type(item) is dict:
                    require(type(key) is str)
                    visit(item[key], depth + 1)
        elif type(item) is float:
            require(math.isfinite(item))
            remaining[1] -= 24
        else:
            require(False)
        require(remaining[1] >= 0)
    visit(value, 0)
    raw = encoded(value)
    require(len(raw) <= limit)
    return raw


def safe_name(name):
    require(type(name) is str and 0 < len(name) <= 512 and not name.startswith("/")
            and all(p not in ("", ".", "..") for p in name.split("/"))
            and not any(ord(c) < 32 or ord(c) == 127 or c == "\\" for c in name))


def read(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit)
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        identity = lambda row: (row.st_dev, row.st_ino, row.st_mode, row.st_nlink,
                                row.st_size, row.st_mtime_ns, row.st_ctime_ns)
        require(len(raw) == before.st_size and identity(before) == identity(after))
        return raw


def verify_tree(root, index, deadline):
    require(type(index) is dict and 0 < len(index) <= MAX_FILES)
    require(stat.S_ISDIR(os.lstat(root).st_mode))
    for name in index:
        safe_name(name)
    seen, total = set(), 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            require(stat.S_ISDIR(os.lstat(Path(directory) / name).st_mode))
        for name in files:
            relative = (Path(directory) / name).relative_to(root).as_posix()
            if relative == "trial.json":
                continue
            require(time.monotonic() < deadline and relative in index)
            row = index[relative]
            require(type(row) is dict and set(row) == {"bytes", "sha256"}
                    and type(row["bytes"]) is int and 0 <= row["bytes"] <= 128 << 20)
            raw = read(Path(directory) / name, row["bytes"])
            total += len(raw)
            require(total <= MAX_PAYLOAD and digest(raw) == row["sha256"])
            seen.add(relative)
    require(seen == set(index))


def load(root, name):
    path = root / name
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(read(path, 1 << 20), str(path), "exec", dont_inherit=True), module.__dict__)
    return module


class Abort(BaseException):
    """Not caught by upstream retry/error recovery; contains only a fixed status."""
    def __init__(self, status):
        require(status in STATUSES and status != "evaluated")
        self.status = status


@contextlib.contextmanager
def alarm(deadline, clock=time.monotonic):
    previous = signal.getsignal(signal.SIGALRM)
    timer = signal.getitimer(signal.ITIMER_REAL)
    require(timer[1] == 0.0)
    start = time.monotonic()
    if timer[0]:
        deadline = min(deadline, clock() + timer[0])
    def expired(signum, frame):
        raise Abort("timeout")
    try:
        signal.signal(signal.SIGALRM, expired)
        if deadline <= clock():
            raise Abort("timeout")
        signal.setitimer(signal.ITIMER_REAL, deadline - clock())
        yield
        if clock() >= deadline:
            raise Abort("timeout")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if timer[0]:
            signal.setitimer(signal.ITIMER_REAL, max(0.000001, timer[0] - (time.monotonic() - start)))


class Bridge:
    """Serial all-role accounting. Scripted usage is not a tokenizer measurement."""
    def __init__(self, tau, config, role, transport, clock=time.monotonic):
        require(role in ("candidate", "reference"))
        self.tau, self.role, self.transport, self.clock = tau, role, transport, clock
        self.budget = tau.Budget(config, clock=clock)
        self.budget.begin_trial()
        self.failed = None
        self.usage_known = True
        self.incoming = self.outgoing = 0
        self.counts = {key: 0 for key in ("agent", "simulator", "evaluator")}
        self.assertions = []

    def stop(self, status):
        self.failed = self.failed or status
        if self.budget.pending is not None:
            self.budget.abandon()
            self.usage_known = False
        raise Abort(self.failed)

    def complete(self, *, model, messages, tools=None, **kwargs):
        if self.failed:
            raise Abort(self.failed)
        try:
            require(type(model) is str and model in MODEL_ROLES
                    and type(messages) is list and 0 < len(messages) <= 256)
            require(kwargs.get("num_retries", 0) == 0 and not kwargs.get("stream", False))
            request = {"messages": messages, "tools": tools}
            bounded_json(request, MAX_REQUEST)
            category = MODEL_ROLES[model]
            role = self.role if category == "agent" else category
        except Exception:
            self.stop("protocol_error")
        try:
            bounds = self.budget.reserve(role)
        except Exception:
            self.stop("timeout" if self.clock() >= self.budget.trial_deadline else "budget_abort")
        self.counts[category] += 1
        try:
            with alarm(self.budget.pending[4], self.clock):
                response = self.transport(category, request, bounds)
            bounded_json(response, MAX_RESPONSE)
            require(type(response) is dict and set(response) == {"choices", "usage"}
                    and type(response["choices"]) is list and len(response["choices"]) == 1)
            choice = response["choices"][0]
            require(type(choice) is dict and set(choice) == {"message", "finish_reason"}
                    and choice["finish_reason"] in ("stop", "tool_calls", "length"))
            message = choice["message"]
            require(type(message) is dict and set(message) <= {"role", "content", "tool_calls"}
                    and message.get("role") == "assistant"
                    and (message.get("content") is None or type(message["content"]) is str))
            calls = message.get("tool_calls")
            require(calls is None or type(calls) is list and 0 < len(calls) <= 16)
            if calls:
                ids = set()
                for call in calls:
                    require(type(call) is dict and set(call) == {"id", "type", "function"}
                            and type(call["id"]) is str and 0 < len(call["id"]) <= 128
                            and call["id"] not in ids and call["type"] == "function")
                    ids.add(call["id"])
                    fn = call["function"]
                    require(type(fn) is dict and set(fn) == {"name", "arguments"}
                            and type(fn["name"]) is str and 0 < len(fn["name"]) <= 128
                            and type(fn["arguments"]) is str and len(fn["arguments"]) <= 32768
                            and type(decode(fn["arguments"])) is dict)
                require(choice["finish_reason"] in ("tool_calls", "length"))
            else:
                require(type(message.get("content")) is str and message["content"]
                        and choice["finish_reason"] != "tool_calls")
            if category == "evaluator" and choice["finish_reason"] != "length":
                results = decode(message["content"])
                require(type(results) is dict and set(results) == {"results"}
                        and type(results["results"]) is list
                        and len(results["results"]) == len(self.assertions))
                for result, assertion in zip(results["results"], self.assertions):
                    require(type(result) is dict and set(result) == {"expectedOutcome", "metExpectation", "reasoning"}
                            and result["expectedOutcome"] == assertion
                            and type(result["metExpectation"]) is bool and type(result["reasoning"]) is str)
            self.budget.settle(response["usage"])
            self.incoming += response["usage"]["prompt_tokens"]
            self.outgoing += response["usage"]["completion_tokens"]
            if choice["finish_reason"] == "length":
                self.stop("truncated")
            return response
        except Abort as exc:
            self.stop(exc.status)
        except BaseException as exc:
            self.stop("cancelled" if isinstance(exc, KeyboardInterrupt) else
                      "timeout" if self.budget.pending and self.clock() >= self.budget.pending[4]
                      else "protocol_error")

    def usage(self):
        if not self.usage_known:
            return None
        return {"prompt_tokens": self.incoming, "completion_tokens": self.outgoing,
                "total_tokens": self.incoming + self.outgoing, "cost_usd": format(self.budget.spent, "f")}


def scripted(bridge, category, request, bounds):
    # Fixed minimal dialogue, not a simulated competent agent or quality result.
    if category == "agent":
        content = "I cannot complete this request."
    elif category == "simulator":
        content = "Please help with my request." if bridge.counts[category] == 1 else "###STOP###"
    else:
        content = json.dumps({"results": [{"expectedOutcome": item, "metExpectation": False,
                                          "reasoning": "Scripted integration fixture."}
                                         for item in bridge.assertions]})
    return {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


def deny_external(event, args):
    if (event.startswith("socket.") and event not in ("socket.__new__",)
            or event in ("subprocess.Popen", "os.system", "os.posix_spawn", "os.fork", "os.forkpty", "pty.spawn")):
        raise Abort("protocol_error")


def run_upstream(root, manifest, selection, bridge):
    # Called only by the container entry after verification. No upstream import at
    # module scope: unit tests exercise the bridge without importing dependencies.
    import logging
    logging.disable(logging.CRITICAL)
    from loguru import logger
    logger.remove()
    import litellm
    litellm.telemetry = False
    for name in ("callbacks", "input_callback", "success_callback", "failure_callback",
                 "_async_success_callback", "_async_failure_callback", "service_callback"):
        setattr(litellm, name, [])
    from tau2.utils import llm_utils
    from tau2.runner.batch import run_single_task
    from tau2.runner.helpers import get_tasks
    from tau2.data_model.simulation import TextRunConfig
    from tau2.evaluator import evaluator_nl_assertions
    from tau2.evaluator.evaluator import EvaluationType
    from litellm import ModelResponse
    llm_utils.set_llm_log_dir(None)
    llm_utils._write_llm_log = lambda *args, **kwargs: None
    def complete(**kwargs):
        value = bridge.complete(**kwargs)
        # Tau parses this field before its cost callback; keep the validated role alias.
        return ModelResponse(model=kwargs["model"], **value)
    llm_utils.completion = complete
    llm_utils.completion_cost = lambda *args, **kwargs: 0.0
    evaluator_nl_assertions.DEFAULT_LLM_NL_ASSERTIONS = "nib-scripted-evaluator"
    evaluator_nl_assertions.DEFAULT_LLM_NL_ASSERTIONS_ARGS = {"temperature": 0, "num_retries": 0}
    task = get_tasks(selection["domain"], task_split_name="base",
                     task_ids=[selection["task_id"]], num_tasks=1)[0]
    bridge.assertions = list(task.evaluation_criteria.nl_assertions or []) if task.evaluation_criteria else []
    args = {"temperature": 0, "num_retries": 0}
    config = TextRunConfig(
        domain=selection["domain"], task_set_name=selection["domain"], task_split_name="base",
        task_ids=[selection["task_id"]], num_tasks=1, llm_agent="nib-scripted-agent",
        llm_args_agent=args, llm_user="nib-scripted-simulator", llm_args_user=args,
        agent="llm_agent", user="user_simulator", num_trials=1, max_concurrency=1,
        max_steps=manifest["settings"]["limits"]["turns"], max_errors=1,
        timeout=min(SECONDS, manifest["settings"]["limits"]["trial_seconds"]),
        max_retries=0, hallucination_retries=0, auto_review=False, auto_resume=False,
        verbose_logs=False, seed=selection["seed"], save_to=None)
    try:
        simulation = run_single_task(config, task, seed=selection["seed"], save_dir=None,
                                     verbose_logs=False, audio_debug=False, audio_taps=False, auto_review=False,
                                     evaluation_type=EvaluationType.ALL_WITH_NL_ASSERTIONS)
        reason = simulation.termination_reason.value
        if reason not in ("user_stop", "agent_stop"):
            raise Abort({"max_steps": "truncated", "context_window_exceeded": "truncated",
                         "timeout": "timeout", "too_many_errors": "protocol_error",
                         "agent_error": "protocol_error", "user_error": "protocol_error",
                         "infrastructure_error": "infrastructure_error",
                         "unexpected_error": "infrastructure_error"}.get(reason, "protocol_error"))
        require(simulation.reward_info is not None)
        return float(simulation.reward_info.reward)
    finally:
        llm_utils.set_llm_log_dir(None)


def evaluate(tau, aggregate, manifest, selection, producer, execute=run_upstream, root=None, clock=time.monotonic):
    start = clock()
    bridge = Bridge(tau, manifest["settings"], selection["role"], None, clock)
    bridge.transport = lambda category, request, bounds: scripted(bridge, category, request, bounds)
    status, reward = "infrastructure_error", None
    try:
        with alarm(min(bridge.budget.trial_deadline, start + SECONDS), clock):
            value = execute(root, manifest, selection, bridge)
            require(type(value) is float and math.isfinite(value) and -1000 <= value <= 1000)
            if bridge.failed:
                raise Abort(bridge.failed)
            require(bridge.counts["agent"] > 0 and bridge.counts["simulator"] > 0
                    and (not bridge.assertions or bridge.counts["evaluator"] > 0))
            bridge.budget.end_trial()
            reward, status = format(Decimal(str(value)), "f"), "evaluated"
    except Abort as exc:
        status = exc.status
    except KeyboardInterrupt:
        status = "cancelled"
    except BaseException:
        status = "infrastructure_error"
    if status != "evaluated":
        reward = None
    if bridge.budget.pending is not None:
        bridge.budget.abandon()
        bridge.usage_known = False
    row = {key: selection[key] for key in ("domain", "task_id", "trial", "seed")}
    row.update(status=status, reward=reward, elapsed_ms=min(86400000, int((clock() - start) * 1000)),
               requests=bridge.budget.requests, usage=bridge.usage())
    manifest_sha = tau.digest(tau.encoded(manifest))
    report = {"schema": "nib.tau-trials/v1", "manifest_sha256": manifest_sha, "role": selection["role"],
              "kind": "synthetic-fixture", "producer": producer,
              "binding": aggregate.expected_binding(manifest, selection["role"]), "trials": [row]}
    aggregate.validate_report(report, manifest, manifest_sha, selection["role"])
    return report, {"role_requests": bridge.counts, "reserved_tokens": bridge.budget.tokens,
                    "reserved_cost_usd": format(bridge.budget.spent, "f"),
                    "usage_known": bridge.usage_known}


def main():
    # Save only a private, non-inheritable report descriptor. Native writes and
    # upstream exceptions on stdout/stderr go to /dev/null, never Docker logs.
    report_fd = os.dup(1)
    os.set_inheritable(report_fd, False)
    with open(os.devnull, "r+b", buffering=0) as sink:
        for fd in (0, 1, 2):
            os.dup2(sink.fileno(), fd)
    result = {"schema": "nib.tau-scripted-trial/v1", "stage": "verification", "completed": False,
              "report": None, "accounting": None, "quality_accepted": False, "confidential": False,
              "live_transport": False, "token_usage": "synthetic-not-tokenized", "upstream_executed": False}
    try:
        with alarm(time.monotonic() + 240):
            require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
                    and sys.version_info[:3] == (3, 12, 13) and sys.platform == "linux"
                    and os.uname().machine == "x86_64" and os.confstr("CS_GNU_LIBC_VERSION") == "glibc 2.36"
                    and len(sys.argv) == 2 and len(sys.argv[1]) == 64)
            root = Path("/opt/nib")
            raw = read(root / "trial.json", 4 << 20)
            require(digest(raw) == sys.argv[1])
            contract = decode(raw)
            verify_tree(root, contract["files"], time.monotonic() + 60)
            tau = load(root, "scripts/tau_preflight.py")
            aggregate = load(root, "scripts/tau_aggregate.py")
            manifest = decode(read(root / "manifest.json", 1 << 20))
            pins = decode(read(root / tau.PINS, 1 << 20))
            aggregate.validate_manifest(manifest, contract["manifest_sha256"], pins)
            sys.path[:] = [p for p in sys.path if p and "site-packages" not in p]
            sys.path.extend([str(root / "upstream/src"), str(root / "packages")])
            sys.addaudithook(deny_external)
            result["stage"], result["upstream_executed"] = "trial", True
            report, accounting = evaluate(tau, aggregate, manifest, contract["selection"],
                                          contract["producer"], root=root)
        result.update(stage="completed", completed=True, report=report, accounting=accounting)
    except BaseException:
        pass
    try:
        raw = encoded(result)
        require(len(raw) <= 16384)
        os.write(report_fd, raw)
    finally:
        os.close(report_fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
