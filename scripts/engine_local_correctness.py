# SPDX-License-Identifier: Apache-2.0
"""Eight fixed synthetic cases; no tools, network or engine execution here."""

import hashlib
import json
import re
import time

REVISION = "5df63a7203cdb9328d824a27b682e77c7b9928be"
# Public source audit, not an assertion that an uninspected wheel matches it.
# https://github.com/vllm-project/vllm/tree/5df63a7203cdb9328d824a27b682e77c7b9928be
SOURCE_SHA256 = {
    "vllm/entrypoints/openai/chat_completion/protocol.py": "8744d5e2004b2d376e34cf17de51c919bca6d81b0cc8392da138db1ef05a7c61",
    "vllm/entrypoints/openai/chat_completion/serving.py": "5e619c0a6866758a247c76e7b8c472f6e008ddbc720f7efe47cc6776cb986d4f",
    "vllm/renderers/chat_utils.py": "9a5d1db66e15285303987073f0c69c3ef3b4745e8c4ab71b9b063f1579fb2061",
    "vllm/tool_parsers/deepseekv41_engine_tool_parser.py": "fa6ee39fd90feae5f7466afdda2c745173737b824c6d65c56f828304dc2114ee",
    "vllm/parser/deepseek_v41.py": "9b5293f5f72b38f9544f06fed2845e877e4dde7b303488d629cb1934b04cdac7",
    "vllm/parser/deepseek_v4.py": "709f328fe3b0711c98cc76f572bee14c6342efaaf2160f010517d3fd54794cee",
    "vllm/tokenizers/deepseek_v41.py": "fff82623f5676021bd93766e8ec7106ff0384181b83ab4f2aa3a492ffa0b3f1f",
    "vllm/tokenizers/deepseek_v41_encoding.py": "477f22ac250b43c12bd19bb3c93f934d6637b06410b8cb7ccba0862d1049dee8",
    "vllm/tool_parsers/abstract_tool_parser.py": "6c55c28eaa73742391045cdef614cf2eb5fbcf789fdc12769db518fdf9545d2e",
    "vllm/tool_parsers/structural_tag_registry.py": "89cba76c81d5976a7b3fa7403817bf369b8bb244cc5c424d04ba6da6ef36e47f",
}
MAX_OUTPUT = 512
REQUEST_SECONDS = 60
TOTAL_SECONDS = 600
TEARDOWN_RESERVE = 90  # Existing hardware recheck (15s), TERM/KILL (60+10s), margin.
MAX_REQUESTS = 10
CASES = (
    {"id": "arithmetic", "kind": "exact", "prompt": "Compute 1 + 1. Reply with the single digit only.", "answer": "2"},
    {"id": "ordering", "kind": "exact", "prompt": "Sort these integers ascending: 9, 2, 5. Reply only as comma-separated digits, with no spaces.", "answer": "2,5,9"},
    {"id": "logic", "kind": "exact", "prompt": "All daxes are wugs. Item A is a dax. Is item A a wug? Reply only yes or no.", "answer": "yes"},
    {"id": "extraction", "kind": "exact", "prompt": "Record: item=box; code=K7; count=4. Extract only the code, with no other text.", "answer": "K7"},
    {"id": "json-count", "kind": "json", "prompt": 'Reply only with a JSON object containing exactly count as the integer 3 and ok as the boolean true. No Markdown.', "answer": {"count": 3, "ok": True}},
    {"id": "json-record", "kind": "json", "prompt": 'Reply only with a JSON object containing exactly label as the string "north" and rank as the integer 2. No Markdown.', "answer": {"label": "north", "rank": 2}},
    {"id": "tool-blue", "kind": "tool", "prompt": "Look up the key blue using lookup_value. After the tool result, reply only with its value.", "key": "blue", "answer": "CEDAR"},
    {"id": "tool-gold", "kind": "tool", "prompt": "Look up the key gold using lookup_value. After the tool result, reply only with its value.", "key": "gold", "answer": "MAPLE"},
)
TOOL = {"type": "function", "function": {
    "name": "lookup_value", "description": "Read one value from a fixed synthetic table.",
    "parameters": {"type": "object", "properties": {"key": {"type": "string", "enum": ["blue", "gold"]}},
                   "required": ["key"], "additionalProperties": False},
}}
FAILURES = ("none", "protocol", "answer", "json", "tool", "timeout", "transport", "cancelled", "budget")


class Rejected(ValueError):
    def __init__(self, category):
        super().__init__("local correctness rejected")
        self.category = category


def require(value, category="protocol"):
    if not value:
        raise Rejected(category)


def strict_json(raw):
    require(type(raw) in (str, bytes) and len(raw) <= 1 << 20)
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result
    def constant(_):
        raise Rejected("protocol")
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise Rejected("protocol") from None


def contract():
    case_bytes = json.dumps({"cases": CASES, "tool": TOOL}, sort_keys=True, separators=(",", ":")).encode()
    return {"schema": "nib.local-correctness-contract/v1", "case_sha256": hashlib.sha256(case_bytes).hexdigest(),
            "source_revision": REVISION, "source_sha256": SOURCE_SHA256, "cases": 8, "requests": MAX_REQUESTS,
            "max_output_tokens": MAX_OUTPUT, "request_seconds": REQUEST_SECONDS, "total_seconds": TOTAL_SECONDS,
            "teardown_reserve_seconds": TEARDOWN_RESERVE, "temperature": 0, "reasoning_effort": "none",
            "concurrency": 1, "retries": 0, "tau_acceptance": False}


def verify_sources(read_source, selected_root):
    for name, expected in SOURCE_SHA256.items():
        require(hashlib.sha256(read_source(selected_root + "/" + name, 128 << 10)).hexdigest() == expected)


def decode_completion(raw, model, max_output):
    row = strict_json(raw)
    require(type(row) is dict and "error" not in row and row.get("object") == "chat.completion"
            and row.get("model") == model and type(row.get("choices")) is list and len(row["choices"]) == 1)
    choice = row["choices"][0]
    require(type(choice) is dict and type(choice.get("index")) is int and choice["index"] == 0
            and choice.get("finish_reason") in ("stop", "length", "tool_calls"))
    message, usage = choice.get("message"), row.get("usage")
    require(type(message) is dict and message.get("role") == "assistant"
            and message.get("refusal") in (None, "")
            and type(usage) is dict
            and all(type(usage.get(key)) is int for key in ("prompt_tokens", "completion_tokens", "total_tokens"))
            and 0 < usage["prompt_tokens"] <= 4096 and 0 < usage["completion_tokens"] <= max_output
            and usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"])
    return message, choice["finish_reason"], {key: usage[key] for key in ("prompt_tokens", "completion_tokens")}


def final_answer(case, message, finish):
    content = message.get("content")
    require(finish == "stop" and type(content) is str and bool(content.strip())
            and message.get("tool_calls") in (None, []))
    if case["kind"] == "json":
        try:
            value = strict_json(content)
        except Rejected:
            raise Rejected("json") from None
        expected = case["answer"]
        require(type(value) is dict and set(value) == set(expected)
                and all(type(value[k]) is type(v) and value[k] == v for k, v in expected.items()), "json")
    else:
        require(content.strip() == case["answer"], "answer")


def tool_continuation(case, message, finish):
    calls = message.get("tool_calls")
    require(finish == "tool_calls" and type(calls) is list and len(calls) == 1, "tool")
    call = calls[0]
    require(type(call) is dict and call.get("type") == "function"
            and type(call.get("id")) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", call["id"]), "tool")
    function = call.get("function")
    require(type(function) is dict and function.get("name") == TOOL["function"]["name"]
            and type(function.get("arguments")) is str and len(function["arguments"]) <= 4096, "tool")
    try:
        args = strict_json(function["arguments"])
    except Rejected:
        raise Rejected("tool") from None
    require(type(args) is dict and set(args) == {"key"} and type(args["key"]) is str and args["key"] == case["key"], "tool")
    require(message.get("content") is None or type(message["content"]) is str, "tool")
    # Preserve only protocol fields, in memory. No returned name dispatch,
    # arbitrary execution, or model-supplied result is used as the tool value.
    assistant = {"role": "assistant", "content": message.get("content"), "tool_calls": calls}
    if message.get("reasoning") is not None:
        require(type(message["reasoning"]) is str, "tool")
        assistant["reasoning"] = message["reasoning"]
    return [assistant, {"role": "tool", "tool_call_id": call["id"],
                        "content": json.dumps({"value": case["answer"]})}]


def run(request, model, *, clock=time.monotonic, deadline=None):
    started = clock()
    deadline = min(started + TOTAL_SECONDS - TEARDOWN_RESERVE,
                   deadline if deadline is not None else float("inf"))
    rows = [{"id": case["id"], "result": "not_run", "failure": "budget", "requests": []} for case in CASES]
    result = {"schema": "nib.local-correctness-smoke/v1", "case_sha256": contract()["case_sha256"],
              "cases": rows, "requests_started": 0, "all_cases_pass": False, "request_phase_elapsed_ms": 0,
              "tau_acceptance": False}
    cancelled = False
    for case, row in zip(CASES, rows):
        if cancelled:
            row["failure"] = "cancelled"
            continue
        if clock() >= deadline:
            continue
        messages = [{"role": "user", "content": case["prompt"]}]
        turns = 2 if case["kind"] == "tool" else 1
        row.update(result="fail", failure="budget")
        for turn in range(turns):
            if clock() >= deadline:
                break
            payload = {"model": model, "messages": messages, "max_completion_tokens": MAX_OUTPUT,
                       "temperature": 0, "reasoning_effort": "none", "stream": False, "n": 1}
            if case["kind"] == "tool":
                payload.update(tools=[TOOL], tool_choice="required" if turn == 0 else "none", parallel_tool_calls=False)
            before = clock()
            request_row = {"turn": turn + 1, "result": "fail", "failure": "transport", "usage": None, "elapsed_ms": 0}
            row["requests"].append(request_row)
            result["requests_started"] += 1
            try:
                raw = request(payload, min(deadline, before + REQUEST_SECONDS))
                require(clock() < min(deadline, before + REQUEST_SECONDS), "timeout")
                message, finish, request_row["usage"] = decode_completion(raw, model, MAX_OUTPUT)
                if case["kind"] == "tool" and turn == 0:
                    messages += tool_continuation(case, message, finish)
                else:
                    final_answer(case, message, finish)
                request_row.update(result="pass", failure="none")
            except Rejected as error:
                request_row["failure"] = error.category
            except TimeoutError:
                request_row["failure"] = "timeout"
            except InterruptedError:
                request_row["failure"] = "cancelled"
                cancelled = True
            except Exception:
                pass
            request_row["elapsed_ms"] = max(0, int((clock() - before) * 1000))
            row["failure"] = request_row["failure"]
            if request_row["result"] != "pass":
                break
        if len(row["requests"]) == turns and all(r["result"] == "pass" for r in row["requests"]):
            row.update(result="pass", failure="none")
        elif not row["requests"]:
            row.update(result="not_run", failure="budget")
        elif row["requests"] and row["requests"][-1]["result"] == "pass":
            row["failure"] = "budget"
    result["all_cases_pass"] = all(row["result"] == "pass" for row in rows)
    result["request_phase_elapsed_ms"] = max(0, int((clock() - started) * 1000))
    validate(result)
    return result


def validate(row):
    require(type(row) is dict and set(row) == {"schema", "case_sha256", "cases", "requests_started",
                                               "all_cases_pass", "request_phase_elapsed_ms", "tau_acceptance"})
    require(row["schema"] == "nib.local-correctness-smoke/v1" and row["case_sha256"] == contract()["case_sha256"]
            and row["tau_acceptance"] is False and type(row["all_cases_pass"]) is bool
            and type(row["requests_started"]) is int and 0 <= row["requests_started"] <= MAX_REQUESTS
            and type(row["request_phase_elapsed_ms"]) is int and 0 <= row["request_phase_elapsed_ms"] <= TOTAL_SECONDS * 1000
            and type(row["cases"]) is list and len(row["cases"]) == len(CASES))
    count = 0
    for case, item in zip(CASES, row["cases"]):
        turns = 2 if case["kind"] == "tool" else 1
        require(type(item) is dict and set(item) == {"id", "result", "failure", "requests"}
                and item["id"] == case["id"] and item["result"] in ("pass", "fail", "not_run")
                and item["failure"] in FAILURES and type(item["requests"]) is list and len(item["requests"]) <= turns)
        for index, request in enumerate(item["requests"]):
            require(type(request) is dict and set(request) == {"turn", "result", "failure", "usage", "elapsed_ms"}
                    and type(request["turn"]) is int and request["turn"] == index + 1
                    and request["result"] in ("pass", "fail") and request["failure"] in FAILURES
                    and type(request["elapsed_ms"]) is int and 0 <= request["elapsed_ms"] <= TOTAL_SECONDS * 1000
                    and (request["result"] == "pass") == (request["failure"] == "none"))
            usage = request["usage"]
            require(usage is None or type(usage) is dict and set(usage) == {"prompt_tokens", "completion_tokens"}
                    and type(usage["prompt_tokens"]) is int and 0 < usage["prompt_tokens"] <= 4096
                    and type(usage["completion_tokens"]) is int and 0 < usage["completion_tokens"] <= MAX_OUTPUT)
            if request["result"] == "pass":
                require(usage is not None and request["elapsed_ms"] <= REQUEST_SECONDS * 1000)
            if index > 0:
                require(item["requests"][index - 1]["result"] == "pass")
        passed = len(item["requests"]) == turns and all(r["result"] == "pass" for r in item["requests"])
        require((item["result"] == "pass") == passed and (item["failure"] == "none") == passed
                and (item["result"] == "not_run") == (len(item["requests"]) == 0))
        if item["result"] == "not_run":
            require(item["failure"] in ("budget", "cancelled"))
        elif not passed:
            require(item["failure"] == (item["requests"][-1]["failure"]
                                       if item["requests"][-1]["result"] == "fail" else "budget"))
        count += len(item["requests"])
    require(count == row["requests_started"]
            and row["all_cases_pass"] == all(r["result"] == "pass" for r in row["cases"]))
    require(len(json.dumps(row)) <= 16384)
    return row
