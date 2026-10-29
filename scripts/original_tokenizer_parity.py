# SPDX-License-Identifier: Apache-2.0
"""External official-runtime comparison, separate from the frozen lab helper."""

import time

from engine_tokenizer_parity import (
    FAILURES, IDS, REQUEST_SECONDS, TEMPLATE, TOTAL_SECONDS,
    binding, digest, encoded, measurement, probes, require, tokens, validate_local,
)

ORIGINAL_PROFILE = "original-long1m-graphs"
ORIGINAL_MODEL = "deepseek/deepseek-v4.1-flash-1017"
ORIGINAL_REVISION = "1bae0e1088993b9b82de08f59de5be5a34e42170"
ORIGINAL_MANIFEST = "b74ae635c35372cf6c350e4b33ef60c90ff01fe6426ad0fa7bc99b1982fe5bed"


def runtime_identity(correctness, max_model_len, *, execution_profile):
    require(type(execution_profile) is str and execution_profile == ORIGINAL_PROFILE
            and type(max_model_len) is int and max_model_len == 1048576)
    identity = {"schema": "nib.tokenizer-parity/v3", "execution_profile": ORIGINAL_PROFILE,
                "model": ORIGINAL_MODEL, "model_revision": ORIGINAL_REVISION,
                "manifest_sha256": ORIGINAL_MANIFEST, "max_model_len": max_model_len}
    return {**identity, "binding_sha256": digest({"parity_binding_sha256": binding(correctness),
                                                 **identity})}


# Keep the frozen lab's 4K/8K comparison and image dependency pins unchanged.
def run(request, local, model, correctness, *, deadline, clock=time.monotonic, max_model_len,
        execution_profile):
    identity = runtime_identity(correctness, max_model_len, execution_profile=execution_profile)
    require(type(model) is str and model == ORIGINAL_MODEL)
    validate_local(local, correctness)
    started = clock()
    deadline = min(deadline, started + TOTAL_SECONDS)
    rows = [{"id": name, "result": "not_run", "failure": "not-run", "requests": 0,
             "local": {k: item[k] for k in ("count", "tokens_sha256")}, "server": None, "usage": None}
            for name, item in zip(IDS, local["cases"])]
    result = {**identity, "cases": rows,
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
                    and response["max_model_len"] == max_model_len)
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
    return validate(result, correctness, max_model_len=max_model_len, execution_profile=execution_profile)


def validate(row, correctness, *, max_model_len, execution_profile):
    identity = runtime_identity(correctness, max_model_len, execution_profile=execution_profile)
    require(type(row) is dict and set(row) == set(identity) | {"cases", "requests_started",
            "all_cases_pass", "elapsed_ms", "quality_accepted", "confidential", "tau_acceptance"}
            and all(type(row[key]) is type(value) and row[key] == value for key, value in identity.items())
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
                    and type(usage["prompt_tokens"]) is int and 0 < usage["prompt_tokens"] <= max_model_len
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
