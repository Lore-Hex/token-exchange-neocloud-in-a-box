#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""One scripted tau trial; third-party code runs only in the reviewed container."""

import sys
import contextlib
from decimal import Decimal
import functools
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import stat
import threading
import time
import types

MAX_REQUEST = 1 << 20
MAX_RESPONSE = 256 << 10
MAX_TOOL_CALLS = 16
MAX_FILES = 11000
MAX_PAYLOAD = 512 << 20
SECONDS = 180
SELECTED_TRIAL_SECONDS = 900
SELECTED_RUN_SECONDS = 960
MODEL_ROLES = {"nib-scripted-agent": "agent", "nib-scripted-simulator": "simulator",
               "nib-scripted-evaluator": "evaluator"}
STATUSES = ("evaluated", "infrastructure_error", "timeout", "truncated",
            "protocol_error", "budget_abort", "cancelled")
# Pinned tau2 TerminationReason and the existing terminal-status mapping.
# https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/data_model/simulation.py#L1234-L1245
UPSTREAM_STATUSES = {"user_stop": "evaluated", "agent_stop": "evaluated",
                     "max_steps": "truncated", "context_window_exceeded": "truncated",
                     "timeout": "timeout", "too_many_errors": "protocol_error",
                     "agent_error": "protocol_error", "user_error": "protocol_error",
                     "infrastructure_error": "infrastructure_error", "unexpected_error": "infrastructure_error"}
TOOL_ERROR_CATEGORIES = {ValueError: "value_error", TypeError: "type_error", KeyError: "key_error",
                         IndexError: "index_error", AssertionError: "assertion_error",
                         RuntimeError: "runtime_error"}
TOOL_ERROR_REQUESTORS = ("assistant", "user", "other")
# Exact fc0055dc source identities already sealed by the tau runtime lock.
TOOL_ERROR_SOURCES = {
    "tau2.domains.retail.tools": (29202, "34f4fe9702d36f0dd4697700fbcd63d0cf5386e6eb8e7499aaa336243bad8750", 752),
    "tau2.environment.environment": (18486, "b5357f11e2914c11ce0372af919cc1d726191b91cd97f776b8fb388ae0615350", 490),
    "tau2.environment.toolkit": (11313, "3e897891e8878c3399aec72e42eb0eb5546642420c0c902b225421fc936b924a", 323),
}
EMAIL_LOOKUP_ORIGIN = (TOOL_ERROR_SOURCES["tau2.domains.retail.tools"][1], 330)


def prior_user_text_contains_argument(value, messages, user_type):
    """Transient comparison only; neither argument nor message content escapes."""
    try:
        require(type(value) is str and 3 <= len(value) <= 320 and value.count("@") == 1
                and not any(char.isspace() for char in value)
                and type(messages) is list and len(messages) <= 256)
        texts, total = [], 0
        for message in messages:
            if type(message) is not user_type:
                continue
            text = vars(message).get("content")
            if text is None:
                continue
            require(type(text) is str)
            total += len(text)
            require(total <= MAX_REQUEST)
            texts.append(text)
        require(texts)
        # Match upstream's lower(), not a guessed email normalization. This is
        # literal containment, not a claim of correct identity or user intent.
        return any(value.lower() in text.lower() for text in texts)
    except Exception:
        return None


def tool_error_sources():
    """Observe only already-loaded, hash-pinned modules; never import a tool."""
    sources = {}
    for name, (size, digest, lines) in TOOL_ERROR_SOURCES.items():
        module = sys.modules.get(name)
        if type(module) is not types.ModuleType:
            continue
        path = vars(module).get("__file__")
        try:
            require(type(path) is str and Path(path).is_absolute())
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
                info = os.fstat(stream.fileno())
                require(stat.S_ISREG(info.st_mode) and info.st_size == size)
                raw = stream.read(size + 1)
                after = os.fstat(stream.fileno())
            fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
            require(all(getattr(info, key) == getattr(after, key) for key in fields)
                    and len(raw) == size and len(raw.splitlines()) == lines
                    and hashlib.sha256(raw).hexdigest() == digest)
            sources[path] = (digest, lines)
        except Exception:
            continue
    return sources


def tool_error_origin(error, sources):
    # The deepest frame is the raise site. Never substitute an outer frame when
    # the real origin is unpinned or the bounded walk cannot reach it.
    frame = BaseException.__traceback__.__get__(error)
    for _ in range(32):
        if frame is None:
            return None
        if frame.tb_next is None:
            source = sources.get(frame.tb_frame.f_code.co_filename)
            if source is not None and 1 <= frame.tb_lineno <= source[1]:
                return (source[0], frame.tb_lineno)
            return None
        frame = frame.tb_next
    return None


def require(value):
    if not value:
        raise ValueError("tau trial rejected")


def validate_upstream_termination(value, *, evaluated=False):
    """Only completed selected simulations have upstream termination facts."""
    if value is None:
        require(not evaluated)
        return None
    require(type(value) is dict and set(value) in ({"reason", "tool_errors"},
            {"reason", "tool_errors", "tool_error_attribution"}))
    reason, errors = value["reason"], value["tool_errors"]
    require(type(reason) is str and reason in UPSTREAM_STATUSES
            and type(errors) is int and 0 <= errors <= MAX_TOOL_CALLS)
    # max_errors=1 stops after the first failing batch, not its first tool.
    # Timeout may supersede that reason in the same termination check.
    require((reason != "too_many_errors" or errors > 0)
            and (errors == 0 or reason in ("too_many_errors", "timeout"))
            and (not evaluated or UPSTREAM_STATUSES[reason] == "evaluated"))
    result = {"reason": reason, "tool_errors": errors}
    if "tool_error_attribution" in value:
        result["tool_error_attribution"] = validate_tool_error_attribution(value["tool_error_attribution"], errors)
    return result


def validate_tool_error_attribution(value, errors):
    require(type(value) is dict and set(value) == {"path", "counts", "unattributed"}
            and type(value["path"]) is str and value["path"] == "live_make_tool_call"
            and type(value["counts"]) is list and len(value["counts"]) <= MAX_TOOL_CALLS
            and type(value["unattributed"]) is int and 0 <= value["unattributed"] <= errors)
    rows, seen, total = [], set(), value["unattributed"]
    for row in value["counts"]:
        require(type(row) is dict and set(row) in (
                    {"requestor", "known_tool", "category", "count"},
                    {"requestor", "known_tool", "category", "count", "origin"},
                    {"requestor", "known_tool", "category", "count", "origin", "prior_user_text_contains_argument"})
                and type(row["requestor"]) is str and row["requestor"] in TOOL_ERROR_REQUESTORS
                and type(row["known_tool"]) is bool and type(row["category"]) is str
                and row["category"] in (*TOOL_ERROR_CATEGORIES.values(), "other_exception")
                and type(row["count"]) is int and 1 <= row["count"] <= MAX_TOOL_CALLS)
        origin = row.get("origin")
        if "origin" in row:
            require(type(origin) is dict and set(origin) == {"source_sha256", "line"}
                    and type(origin["source_sha256"]) is str
                    and origin["source_sha256"] in {value[1] for value in TOOL_ERROR_SOURCES.values()}
                    and type(origin["line"]) is int and 1 <= origin["line"] <=
                    next(value[2] for value in TOOL_ERROR_SOURCES.values()
                         if value[1] == origin["source_sha256"]))
        prior = row.get("prior_user_text_contains_argument")
        if "prior_user_text_contains_argument" in row:
            require(type(prior) is bool and row["requestor"] == "assistant"
                    and row["known_tool"] is True and row["category"] == "value_error"
                    and (origin["source_sha256"], origin["line"]) == EMAIL_LOOKUP_ORIGIN)
        key = (row["requestor"], row["known_tool"], row["category"],
               (origin["source_sha256"], origin["line"]) if origin is not None else None, prior)
        require(key not in seen)
        seen.add(key)
        total += row["count"]
        rows.append(dict(row))
    require(total == errors)
    return {"path": "live_make_tool_call", "counts": rows, "unattributed": value["unattributed"]}


class ToolErrors:
    """Bounded aggregate only; never retain an exception, name, argument or result."""
    def __init__(self):
        self.counts, self.total, self.overflow = {}, 0, False
        self.sources = None

    def record(self, requestor, known, error, *, prior_user_match=None):
        if self.total == MAX_TOOL_CALLS:
            self.overflow = True
            return
        requestor = requestor if type(requestor) is str and requestor in TOOL_ERROR_REQUESTORS[:2] else "other"
        category = TOOL_ERROR_CATEGORIES.get(type(error), "other_exception")
        try:
            if self.sources is None:
                self.sources = tool_error_sources()
            origin = tool_error_origin(error, self.sources)
        except Exception:
            origin = None
        relation = "unknown"
        if (origin == EMAIL_LOOKUP_ORIGIN and requestor == "assistant" and known is True
                and category == "value_error" and type(prior_user_match) is bool):
            relation = "present" if prior_user_match else "absent"
        key = (requestor, known, category, origin or ("", 0), relation)
        self.counts[key] = self.counts.get(key, 0) + 1
        self.total += 1

    def project(self, errors):
        # Do not invent attribution if observations cannot join the completed
        # simulation. Argument binding and sync_tools errors are not observed.
        counts = {} if self.overflow or self.total > errors else self.counts
        rows = [{"requestor": requestor, "known_tool": known, "category": category, "count": count,
                 **({"origin": {"source_sha256": origin[0], "line": origin[1]}} if origin[0] else {}),
                 **({"prior_user_text_contains_argument": relation == "present"}
                    if relation != "unknown" else {})}
                for (requestor, known, category, origin, relation), count in sorted(counts.items())]
        return validate_tool_error_attribution({"path": "live_make_tool_call", "counts": rows,
            "unattributed": errors - sum(counts.values())}, errors)


@contextlib.contextmanager
def observe_selected_tools():
    # Hash-verified fc0055dc source: get_response catches Exception, including
    # sync_tools; set_state also replays get_response during evaluation. Observe
    # only the synchronous live batch, not initialization or evaluation replay.
    from tau2.environment.environment import Environment
    from tau2.data_model.message import UserMessage
    from tau2.orchestrator.orchestrator import BaseOrchestrator
    observer = ToolErrors()
    execute = BaseOrchestrator._execute_tool_calls

    def batch(orchestrator, tool_calls):
        env = orchestrator.environment
        if type(env) is not Environment or env.solo_mode is not False:
            return execute(orchestrator, tool_calls)
        original = env.make_tool_call
        present = "make_tool_call" in vars(env)
        saved = vars(env).get("make_tool_call")

        @functools.wraps(original)
        def call(*args, **kwargs):
            tool_name = args[0] if args else kwargs.get("tool_name")
            requestor = args[1] if len(args) > 1 else kwargs.get("requestor", "assistant")
            known = None
            try:
                toolkit = (env.tools if type(requestor) is str and requestor == "assistant" else
                           env.user_tools if type(requestor) is str and requestor == "user" else None)
                known = type(tool_name) is str and toolkit is not None and toolkit.has_tool(tool_name)
            except Exception:
                pass  # Unsupported observation must not replace the original outcome.
            try:
                return original(*args, **kwargs)
            except Exception as error:
                if type(known) is bool:
                    prior = None
                    if (type(requestor) is str and requestor == "assistant" and known
                            and type(tool_name) is str and tool_name == "find_user_id_by_email"):
                        try:
                            prior = prior_user_text_contains_argument(kwargs.get("email"),
                                orchestrator.get_messages(), UserMessage)
                        except Exception:
                            pass
                    observer.record(requestor, known, error, prior_user_match=prior)
                raise

        try:
            env.make_tool_call = call
            return execute(orchestrator, tool_calls)
        finally:
            if present:
                env.make_tool_call = saved
            else:
                del env.make_tool_call

    try:
        BaseOrchestrator._execute_tool_calls = batch
        yield observer
    finally:
        BaseOrchestrator._execute_tool_calls = execute


def selected_termination(simulation, turns):
    from tau2.data_model.message import ToolMessage
    messages = simulation.messages
    require(type(messages) is list and len(messages) <= 1 + turns * (MAX_TOOL_CALLS + 1))
    errors = 0
    for message in messages:
        if type(message) is ToolMessage:
            require(type(message.error) is bool)
            errors += int(message.error)
    return validate_upstream_termination({"reason": simulation.termination_reason.value, "tool_errors": errors})


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
    """Own SIGALRM on the main thread; never mutate another thread's timer."""
    require(threading.current_thread() is threading.main_thread())
    start = time.monotonic()
    previous = signal.getsignal(signal.SIGALRM)
    timer = signal.getitimer(signal.ITIMER_REAL)
    require(timer[1] == 0.0)
    if timer[0]:
        deadline = min(deadline, clock() + timer[0] - (time.monotonic() - start))
    restoring, expired_seen = False, False
    def expired(signum, frame):
        nonlocal expired_seen
        expired_seen = True
        if not restoring:
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
        # Another unblocked thread may already have received SIGALRM in C;
        # its deferred Python handler must not interrupt restoration here.
        restoring = True
        # Disarming does not discard a pending signal. Consume it while blocked
        # before restoring SIG_DFL, which would otherwise terminate the process.
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
        try:
            signal.setitimer(signal.ITIMER_REAL, 0)
            pending = signal.SIGALRM in signal.sigpending()
            if pending:
                signal.sigwait({signal.SIGALRM})
            signal.signal(signal.SIGALRM, previous)
            remaining = timer[0] - (time.monotonic() - start)
            if timer[0] and remaining > 0:
                signal.setitimer(signal.ITIMER_REAL, remaining)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        if timer[0] and remaining <= 0:
            # Never rearm an expired outer timer for one microsecond during
            # unwinding. Deliver its original handler (and Abort type) now.
            if callable(previous):
                previous(signal.SIGALRM, None)
            raise Abort("timeout")
        if pending or expired_seen:
            raise Abort("timeout")


class Bridge:
    """Serial all-role accounting. Scripted usage is not a tokenizer measurement."""
    def __init__(self, tau, config, role, transport, clock=time.monotonic, *, support_probe=False,
                 selected_operational=False):
        require(role in ("candidate", "reference"))
        require(type(support_probe) is bool and type(selected_operational) is bool
                and not (support_probe and selected_operational))
        self.tau, self.role, self.transport, self.clock = tau, role, transport, clock
        self.selected_operational = selected_operational
        self.customer_resolution_mode = False
        if support_probe or selected_operational:
            if selected_operational:
                import tau_selected_policy as policy
                self.customer_resolution_mode = policy.is_resolution(config)
            else:
                import tau_support_probe as policy
            require(role == "candidate")
            self.budget = policy.ResourceBudget(config, clock=clock)
        else:
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
            require(not self.customer_resolution_mode or category != "evaluator")
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
            require(calls is None or type(calls) is list and 0 < len(calls) <= MAX_TOOL_CALLS)
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
            # Pinned vLLM can mask native length with tool_calls. At the cap its
            # termination is ambiguous, not proven truncated; retain settled usage.
            # https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/openai/chat_completion/serving.py#L1099-L1119
            if (self.selected_operational and role == "candidate"
                    and choice["finish_reason"] == "tool_calls"
                    and response["usage"]["completion_tokens"] == bounds["max_tokens"]):
                self.stop("protocol_error")
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


def configure_upstream(bridge):
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
    from tau2.evaluator import evaluator_nl_assertions
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


def run_support_probe(root, manifest, selection, bridge):
    """Two original upstream steps, then its evaluator; never a quality trial."""
    import tau_support_probe
    tau_support_probe.validate(manifest)
    require(selection == tau_support_probe.SELECTION)
    configure_upstream(bridge)
    from tau2.runner.helpers import get_tasks
    from tau2.runner.build import build_text_orchestrator
    from tau2.data_model.simulation import TextRunConfig
    from tau2.evaluator.evaluator_nl_assertions import NLAssertionsEvaluator
    from tau2.utils import llm_utils
    task = get_tasks("retail", task_split_name="base", task_ids=["2"], num_tasks=1)[0]
    require(task.initial_state is None and task.user_tools is None and task.evaluation_criteria is not None)
    bridge.assertions = list(task.evaluation_criteria.nl_assertions or [])
    require(len(bridge.assertions) == 1)
    args = {"temperature": 0, "num_retries": 0}
    config = TextRunConfig(
        domain="retail", task_set_name="retail", task_split_name="base", task_ids=["2"], num_tasks=1,
        llm_agent="nib-scripted-agent", llm_args_agent=args, llm_user="nib-scripted-simulator", llm_args_user=args,
        agent="llm_agent", user="user_simulator", num_trials=1, max_concurrency=1, max_steps=2, max_errors=1,
        timeout=min(SECONDS, manifest["limits"]["trial_seconds"]),
        max_retries=0, hallucination_retries=0, auto_review=False, auto_resume=False,
        verbose_logs=False, seed=selection["seed"], save_to=None)
    try:
        orchestrator = build_text_orchestrator(config, task, seed=selection["seed"])
        if tau_support_probe.is_identity(manifest):
            import tau_cpu_identity
            return tau_cpu_identity.run(orchestrator)
        orchestrator.initialize()
        orchestrator.step()  # Original simulator rendering and parser, actual CPU reply.
        require(not orchestrator.done)
        orchestrator.step()  # The parent supplies only the existing scripted agent reply.
        reward = NLAssertionsEvaluator.calculate_reward(task, orchestrator.get_messages())
        return float(reward.reward)
    finally:
        llm_utils.set_llm_log_dir(None)


def run_selected_upstream(root, manifest, selection, bridge):
    import tau_selected_policy as policy
    if policy.is_resolution(manifest):
        import tau_customer_resolution
        return tau_customer_resolution.run(root, manifest, selection, bridge)
    return run_upstream(root, manifest, selection, bridge, selected_operational=True)


def text_config(selection, settings, seconds):
    from tau2.data_model.simulation import TextRunConfig
    args = {"temperature": 0, "num_retries": 0}
    return TextRunConfig(
        domain=selection["domain"], task_set_name=selection["domain"], task_split_name="base",
        task_ids=[selection["task_id"]], num_tasks=1, llm_agent="nib-scripted-agent",
        llm_args_agent=args, llm_user="nib-scripted-simulator", llm_args_user=args,
        agent="llm_agent", user="user_simulator", num_trials=1, max_concurrency=1,
        max_steps=settings["limits"]["turns"], max_errors=1,
        timeout=min(seconds, settings["limits"]["trial_seconds"]),
        max_retries=0, hallucination_retries=0, auto_review=False, auto_resume=False,
        verbose_logs=False, seed=selection["seed"], save_to=None)


def run_upstream(root, manifest, selection, bridge, *, selected_operational=False):
    require(type(selected_operational) is bool)
    if selected_operational:
        bridge.upstream_termination = None
        import tau_selected_policy as policy
        policy.validate(manifest)
        require(encoded(selection) == encoded(manifest["selection"] if policy.is_staged(manifest) else policy.support.SELECTION)
                and policy.LIMITS["trial_seconds"] == SELECTED_TRIAL_SECONDS
                and policy.LIMITS["run_seconds"] == SELECTED_RUN_SECONDS)
        settings, seconds = policy.settings(manifest), SELECTED_TRIAL_SECONDS
    else:
        settings, seconds = manifest["settings"], SECONDS
    configure_upstream(bridge)
    from tau2.runner.batch import run_single_task
    from tau2.runner.helpers import get_tasks
    from tau2.evaluator.evaluator import EvaluationType
    from tau2.utils import llm_utils
    task = get_tasks(selection["domain"], task_split_name="base",
                     task_ids=[selection["task_id"]], num_tasks=1)[0]
    bridge.assertions = list(task.evaluation_criteria.nl_assertions or []) if task.evaluation_criteria else []
    require(not selected_operational or len(bridge.assertions) == 1)
    config = text_config(selection, settings, seconds)
    try:
        with observe_selected_tools() if selected_operational else contextlib.nullcontext() as observer:
            simulation = run_single_task(config, task, seed=selection["seed"], save_dir=None,
                                         verbose_logs=False, audio_debug=False, audio_taps=False, auto_review=False,
                                         evaluation_type=EvaluationType.ALL_WITH_NL_ASSERTIONS)
        if selected_operational:
            termination = selected_termination(simulation, settings["limits"]["turns"])
            termination["tool_error_attribution"] = observer.project(termination["tool_errors"])
            bridge.upstream_termination = validate_upstream_termination(termination)
        reason = simulation.termination_reason.value
        if reason not in ("user_stop", "agent_stop"):
            raise Abort(UPSTREAM_STATUSES.get(reason, "protocol_error"))
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


def invocation(arguments):
    require(len(arguments) in (2, 3, 4) and type(arguments[1]) is str and len(arguments[1]) == 64
            and all(c in "0123456789abcdef" for c in arguments[1]))
    flags = arguments[2:]
    require(flags in ([], ["--parent-pipe"], ["--parent-pipe", "--selected-operational"]))
    return bool(flags), len(flags) == 2


def validate_dispatch(contract, parent_pipe, selected_operational):
    require(type(parent_pipe) is bool and type(selected_operational) is bool
            and (not selected_operational or parent_pipe))
    require(contract.get("transport") == ("selected-operational-parent-pipe" if selected_operational
            else "scripted-parent-pipe" if parent_pipe else None))
    if selected_operational:
        require(contract.get("schema") == "nib.tau-selected-scorer-plan/v1"
                and contract.get("selected_operational") is True
                and type(contract.get("seconds")) is int and contract["seconds"] == SELECTED_RUN_SECONDS
                and "support_probe" not in contract and "agent_prefix" not in contract
                and "live-binding.json" not in contract["files"])
    else:
        require("selected_operational" not in contract)
    require("support_probe" not in contract or parent_pipe and contract["support_probe"] is True)
    require("agent_prefix" not in contract or contract.get("support_probe") is True
            and contract["agent_prefix"] is True and contract["schema"] == "nib.tau-agent-prefix-plan/v1")


def main():
    # Save only a private, non-inheritable report descriptor. Native writes and
    # upstream exceptions on stdout/stderr go to /dev/null, never Docker logs.
    report_fd = os.dup(1)
    os.set_inheritable(report_fd, False)
    # Invalid invocations fail after output suppression, without an exception log.
    parent_pipe = sys.argv[2:3] == ["--parent-pipe"]
    selected_operational = sys.argv[2:] == ["--parent-pipe", "--selected-operational"]
    input_fd = os.dup(0) if parent_pipe else None
    if input_fd is not None:
        os.set_inheritable(input_fd, False)
    with open(os.devnull, "r+b", buffering=0) as sink:
        for fd in (0, 1, 2):
            os.dup2(sink.fileno(), fd)
    result = {"schema": "nib.tau-scripted-trial/v1", "stage": "verification", "completed": False,
              "report": None, "accounting": None, "quality_accepted": False, "confidential": False,
              "live_transport": False, "token_usage": "synthetic-not-tokenized", "upstream_executed": False}
    try:
        operation_deadline = time.monotonic() + (SELECTED_RUN_SECONDS if selected_operational else 240)
        with alarm(operation_deadline):
            require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
                    and sys.version_info[:3] == (3, 12, 13) and sys.platform == "linux"
                    and os.uname().machine == "x86_64" and os.confstr("CS_GNU_LIBC_VERSION") == "glibc 2.36"
                    and invocation(sys.argv) == (parent_pipe, selected_operational))
            root = Path("/opt/nib")
            raw = read(root / "trial.json", 4 << 20)
            require(digest(raw) == sys.argv[1])
            contract = decode(raw)
            validate_dispatch(contract, parent_pipe, selected_operational)
            verify_tree(root, contract["files"], min(operation_deadline, time.monotonic() + 60))
            tau = load(root, "scripts/tau_preflight.py")
            aggregate = load(root, "scripts/tau_aggregate.py")
            manifest = decode(read(root / "manifest.json", 1 << 20))
            pins = decode(read(root / tau.PINS, 1 << 20))
            if contract.get("support_probe") is True or selected_operational:
                sys.modules["tau_preflight"] = tau
                policy = load(root, "scripts/tau_support_probe.py")
                sys.modules["tau_support_probe"] = policy
                if selected_operational:
                    sys.modules["deepseek_model_profile"] = load(root, "scripts/deepseek_model_profile.py")
                    sys.modules["tau_external_selection"] = load(root, "scripts/tau_external_selection.py")
                    policy = load(root, "scripts/tau_selected_policy.py")
                    sys.modules["tau_selected_policy"] = policy
                policy.validate(manifest, contract["manifest_sha256"])
                source_policy = policy.support if selected_operational else policy
                if selected_operational:
                    staged = policy.is_staged(manifest)
                    names = (policy.staged().source_names(manifest["stage"]["plan"]["domains"]) if staged else
                             ["data/tau2/domains/retail/" + name for name in ("tasks.json", "split_tasks.json")])
                    sources = {name: read(root / "upstream" / name, 16 << 20) for name in names}
                    for name, raw in sources.items():
                        require(digest(raw) == pins["files"][name]["sha256"]
                                and len(raw) == pins["files"][name]["bytes"])
                    if staged:
                        require(manifest["stage"]["plan"]["producer_git_sha"] == contract["producer"]["git_sha"])
                        policy.staged().verify_sources(manifest["stage"]["plan"], sources, pins)
                        require(encoded(contract["selection"]) == encoded(manifest["selection"]))
                    else:
                        require(encoded(contract["selection"]) == encoded(source_policy.source_selection(sources)))
                else:
                    require(contract["selection"] == source_policy.SELECTION)
            else:
                aggregate.validate_manifest(manifest, contract["manifest_sha256"], pins)
            sys.path[:] = [p for p in sys.path if p and "site-packages" not in p]
            sys.path.extend([str(root / "upstream/src"), str(root / "packages")])
            sys.addaudithook(deny_external)
            result["stage"], result["upstream_executed"] = "trial", True
            if parent_pipe:
                # These helper bytes were verified above; no ambient module
                # search path or network access is introduced.
                sys.path.insert(0, str(root / "scripts"))
                import tau_transport
                import tau_pipe_trial
                channel = tau_transport.Channel(input_fd, report_fd)
                input_fd = report_fd = None  # Channel now owns both duplicates.
                try:
                    seconds = SELECTED_TRIAL_SECONDS if selected_operational else SECONDS
                    proxy = tau_transport.WorkerProxy(channel, min(operation_deadline, time.monotonic() + seconds),
                        selected_operational=selected_operational,
                        customer_resolution=selected_operational and policy.is_resolution(manifest))
                    if contract.get("agent_prefix") is True:
                        prefix = load(root, "scripts/tau_agent_prefix.py")
                        value = prefix.materialize(sys.modules[__name__], policy, manifest, contract["selection"])
                        prefix.send(channel, value, sys.argv[1], proxy.deadline)
                    elif contract.get("support_probe") is True:
                        tau_pipe_trial.execute(root, manifest, contract["selection"], proxy, run=run_support_probe)
                    elif selected_operational:
                        # Use the same module's Abort class as execute's handler,
                        # not the entry point's separate __main__ class.
                        tau_pipe_trial.execute(root, manifest, contract["selection"], proxy,
                                               run=tau_pipe_trial.worker.run_selected_upstream)
                    else:
                        require("support_probe" not in contract)
                        tau_pipe_trial.execute(root, manifest, contract["selection"], proxy)
                finally:
                    channel.close()
                return 0
            report, accounting = evaluate(tau, aggregate, manifest, contract["selection"],
                                          contract["producer"], root=root)
        result.update(stage="completed", completed=True, report=report, accounting=accounting)
    except BaseException:
        pass
    if parent_pipe:
        for fd in (input_fd, report_fd):
            if fd is not None:
                os.close(fd)
        return 1
    try:
        raw = encoded(result)
        require(len(raw) <= 16384)
        os.write(report_fd, raw)
    finally:
        os.close(report_fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
