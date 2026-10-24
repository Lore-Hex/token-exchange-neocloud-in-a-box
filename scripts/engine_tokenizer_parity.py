# SPDX-License-Identifier: Apache-2.0
"""Fixed runtime tokenizer prerequisite, not a tokenizer or quality verifier."""

import copy
import hashlib
import json
import re
import time

# vLLM 5df63a7203cdb9328d824a27b682e77c7b9928be. See the task document
# for the exact renderer, tokenize API and generated-usage source chain.
SOURCES = {
    "vllm/tokenizers/hf.py": "4c2712de92e8fec91b9d7f3b5b162bd1f0683e7fc21c16b20188a505fc6c6fea",
    "vllm/entrypoints/serve/tokenize/protocol.py": "e887a771afbc7ae3c709691d31e34da8f3d63a627eab55cc3d97d05625901c07",
    "vllm/entrypoints/serve/tokenize/serving.py": "194f12a7f986aac45d23f8252eefcbb8a10d976710000b36614b08cf3359903c",
    "vllm/renderers/deepseek_v4.py": "f7175f9980550e5ccfcc94fcebfdeee113eee66efc7cd1ec539d5b2af1f0d8cd",
    "vllm/renderers/online_renderer.py": "a8f90b4d1d41b2194557992a12105f65bf720d743384e18ee01c2870a75a7c72",
    "vllm/renderers/params.py": "5113461cd308266727d9ced82900cbe65ae50245a2f3cb16224f6689574570a9",
    "vllm/renderers/registry.py": "5ea03ecad81f8911c72112656f2456c05e0e57122be99af05c27464466596844",
    "vllm/entrypoints/generate/base/protocol.py": "0c07e689fdbbb59929fc8599f714b8041eec0dcd7fb16d87bb59bc80c5874803",
}
MODEL_FILES = {
    "tokenizer.json": (6367257, "c90dfa01249db1be4245780a052ede752e1361c612ac6d08e2bdada7d599476b"),
    "tokenizer_config.json": (801, "6ac8c8dc065ed118161d02dd532749ae3f52c243deac27872134fae2f50d8547"),
}
IDS = ("text", "system", "tools", "tool-continuation")
TEMPLATE = {"reasoning_effort": "none", "enable_thinking": False}
LOCAL_SECONDS = 60
REQUEST_SECONDS = 30
TOTAL_SECONDS = 300
MAX_TOKENS = 1024
FAILURES = ("none", "not-run", "protocol", "token-ids", "usage", "timeout", "transport", "cancelled")


def require(value):
    if not value:
        raise ValueError("tokenizer parity rejected")


def enabled(contract):
    require(contract.get("tokenizer_parity") is True and contract.get("operation") == "local-correctness"
            and (contract.get("gpu_family") == "B300" or
                 (contract.get("gpu_family") == "B200" and type(contract.get("data_parallel_size")) is int
                  and contract["data_parallel_size"] == 2 and type(contract.get("model_profile")) is dict
                  and contract["model_profile"].get("name") == "uncensored"))
            and (contract.get("gpu_security", "confidential") == "confidential" or
                 (contract.get("gpu_security") == "nonconfidential" and contract.get("gpu_family") == "B300"
                  and type(contract.get("data_parallel_size")) is int and contract["data_parallel_size"] == 2
                  and type(contract.get("model_profile")) is dict
                  and contract["model_profile"].get("name") == "uncensored"
                  and "provider_conformance" not in contract)))
    return True


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def probes():
    text = {"role": "user", "content": "Reply with the single digit 2."}
    tool = {"type": "function", "function": {"name": "parity_lookup", "description": "Read a synthetic value.",
            "parameters": {"type": "object", "properties": {"key": {"type": "string"}},
                           "required": ["key"], "additionalProperties": False}}}
    request = {"messages": [{"role": "user", "content": "Look up the key blue."}], "tools": [tool]}
    continuation = copy.deepcopy(request)
    continuation["messages"] += [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "parity-call", "type": "function",
            "function": {"name": "parity_lookup", "arguments": '{"key":"blue"}'}}]},
        {"role": "tool", "tool_call_id": "parity-call", "content": '{"value":"CEDAR"}'},
    ]
    return ({"messages": [text]}, {"messages": [{"role": "system", "content": "Answer briefly."}, copy.deepcopy(text)]},
            request, continuation)


def binding(correctness):
    return digest({"source_revision": correctness.REVISION,
                   "sources": {**correctness.SOURCE_SHA256, **SOURCES}, "model_files": MODEL_FILES,
                   "probes": probes(), "template": TEMPLATE, "max_completion_tokens": 1})


def verify_sources(read_source, selected_root, correctness):
    correctness.verify_sources(read_source, selected_root)
    for name, sha in SOURCES.items():
        require(hashlib.sha256(read_source(selected_root + "/" + name, 128 << 10)).hexdigest() == sha)


def verify_model(read_source):
    for name, (size, sha) in MODEL_FILES.items():
        raw = read_source("/model/" + name, size)
        require(len(raw) == size and hashlib.sha256(raw).hexdigest() == sha)


def tokens(value):
    require(type(value) is list and 0 < len(value) <= MAX_TOKENS
            and all(type(item) is int and 0 <= item < 2**31 for item in value))
    return {"count": len(value), "tokens_sha256": digest(value)}


def local_measure(tokenizer, tool_type, correctness):
    rows = []
    for name, probe in zip(IDS, probes()):
        # Both API paths model_dump the pinned tool type before rendering.
        tools = [tool_type(**item).model_dump() for item in probe.get("tools", [])] or None
        ids = tokenizer.apply_chat_template(probe["messages"], tools=tools, tokenize=True,
                    add_generation_prompt=True, continue_final_message=False, **TEMPLATE)
        rows.append({"id": name, **tokens(ids)})
    return {"schema": "nib.tokenizer-parity-local/v1", "binding_sha256": binding(correctness), "cases": rows}


def measurement(row):
    require(type(row) is dict and set(row) == {"count", "tokens_sha256"}
            and type(row["count"]) is int and 0 < row["count"] <= MAX_TOKENS
            and type(row["tokens_sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", row["tokens_sha256"]))


def validate_local(row, correctness):
    require(type(row) is dict and set(row) == {"schema", "binding_sha256", "cases"}
            and row["schema"] == "nib.tokenizer-parity-local/v1" and row["binding_sha256"] == binding(correctness)
            and type(row["cases"]) is list and len(row["cases"]) == len(IDS))
    for name, item in zip(IDS, row["cases"]):
        require(type(item) is dict and set(item) == {"id", "count", "tokens_sha256"} and item["id"] == name)
        measurement({key: item[key] for key in ("count", "tokens_sha256")})
    return row


def run(request, local, model, correctness, *, deadline, clock=time.monotonic):
    validate_local(local, correctness)
    started = clock()
    deadline = min(deadline, started + TOTAL_SECONDS)
    rows = [{"id": name, "result": "not_run", "failure": "not-run", "requests": 0,
             "local": {k: item[k] for k in ("count", "tokens_sha256")}, "server": None, "usage": None}
            for name, item in zip(IDS, local["cases"])]
    result = {"schema": "nib.tokenizer-parity/v1", "binding_sha256": binding(correctness), "cases": rows,
              "requests_started": 0, "all_cases_pass": False, "elapsed_ms": 0,
              "quality_accepted": False, "confidential": False, "tau_acceptance": False}
    for probe, row in zip(probes(), rows):
        row.update(result="fail", failure="timeout")
        if clock() >= deadline:
            break
        try:
            def call(path, payload):
                end = min(deadline, clock() + REQUEST_SECONDS)
                if clock() >= end:
                    raise TimeoutError
                row["requests"] += 1
                result["requests_started"] += 1
                raw = request(path, payload, end)
                if clock() >= end:
                    raise TimeoutError
                return raw
            common = {"model": model, **probe, "add_generation_prompt": True,
                      "continue_final_message": False, "add_special_tokens": False,
                      "chat_template_kwargs": dict(TEMPLATE)}
            row["failure"] = "protocol"
            response = correctness.strict_json(call("/tokenize", {**common, "return_token_strs": False}))
            require(type(response) is dict and set(response) == {"count", "max_model_len", "tokens", "token_strs"}
                    and response["token_strs"] is None and type(response["max_model_len"]) is int
                    and response["max_model_len"] == 4096)
            row["server"] = tokens(response["tokens"])
            require(type(response["count"]) is int and response["count"] == row["server"]["count"])
            row["failure"] = "token-ids"
            require(row["server"] == row["local"])
            generation = {**common, "max_completion_tokens": 1, "temperature": 0,
                          "reasoning_effort": "none", "stream": False, "n": 1}
            if probe.get("tools"):
                generation.update(tool_choice="none", parallel_tool_calls=False)
            row["failure"] = "protocol"
            _, _, row["usage"] = correctness.decode_completion(call("/v1/chat/completions", generation), model, 1)
            row["failure"] = "usage"
            require(row["usage"]["prompt_tokens"] == row["local"]["count"])
            if clock() >= deadline:
                raise TimeoutError
            row.update(result="pass", failure="none")
        except TimeoutError:
            row["failure"] = "timeout"
        except InterruptedError:
            row["failure"] = "cancelled"
        except (OSError, ConnectionError):
            row["failure"] = "transport"
        except Exception:
            pass
        if row["result"] != "pass":
            break
    result["elapsed_ms"] = max(0, int((clock() - started) * 1000))
    result["all_cases_pass"] = all(row["result"] == "pass" for row in rows)
    return validate(result, correctness)


def validate(row, correctness):
    require(type(row) is dict and set(row) == {"schema", "binding_sha256", "cases", "requests_started",
            "all_cases_pass", "elapsed_ms", "quality_accepted", "confidential", "tau_acceptance"}
            and row["schema"] == "nib.tokenizer-parity/v1" and row["binding_sha256"] == binding(correctness)
            and all(row[k] is False for k in ("quality_accepted", "confidential", "tau_acceptance"))
            and type(row["all_cases_pass"]) is bool and type(row["requests_started"]) is int
            and 0 <= row["requests_started"] <= 8 and type(row["elapsed_ms"]) is int
            and 0 <= row["elapsed_ms"] <= TOTAL_SECONDS * 1000
            and type(row["cases"]) is list and len(row["cases"]) == len(IDS))
    stopped = False
    for name, item in zip(IDS, row["cases"]):
        require(type(item) is dict and set(item) == {"id", "result", "failure", "requests", "local", "server", "usage"}
                and item["id"] == name and item["result"] in ("pass", "fail", "not_run")
                and item["failure"] in FAILURES and type(item["requests"]) is int and 0 <= item["requests"] <= 2
                and (item["result"] == "pass") == (item["failure"] == "none")
                and (item["result"] == "not_run") == (item["failure"] == "not-run"))
        measurement(item["local"])
        if item["server"] is not None:
            measurement(item["server"])
            require(item["requests"] >= 1)
        usage = item["usage"]
        if usage is not None:
            require(type(usage) is dict and set(usage) == {"prompt_tokens", "completion_tokens"}
                    and type(usage["prompt_tokens"]) is int and 0 < usage["prompt_tokens"] <= 4096
                    and type(usage["completion_tokens"]) is int and usage["completion_tokens"] == 1
                    and item["requests"] == 2 and item["server"] == item["local"])
        if item["requests"] == 2:
            require(item["server"] == item["local"])
        if stopped or item["result"] == "not_run":
            require(item["result"] == "not_run" and item["requests"] == 0
                    and item["server"] is None and usage is None)
        if item["result"] == "pass":
            require(item["server"] == item["local"] and usage is not None
                    and usage["prompt_tokens"] == item["local"]["count"])
        else:
            stopped = True
    require(row["requests_started"] == sum(item["requests"] for item in row["cases"])
            and row["all_cases_pass"] == all(item["result"] == "pass" for item in row["cases"])
            and len(encoded(row)) <= 8192)
    return row
