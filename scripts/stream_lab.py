# SPDX-License-Identifier: Apache-2.0
"""Opt-in fixed stream cohort inside the existing, source-bound lab owner."""

import builtins
import hashlib
import json
import os
import re
import time
import types

NAMES = ("tau_preflight", "tau_trial_worker", "tau_transport", "tau_local_vllm",
         "tau_local_llama", "engine_tokenizer_parity", "tau_llama_identity", "tau_live_identity",
         "stream_measurement", "stream_transport", "stream_cohort")
SCHEMA = "nib.owned-stream-input/v1"
CHILD_SCHEMA = "nib.owned-stream-tokenizer/v1"
RESULT_SCHEMA = "nib.owned-stream-result/v1"
EXTENDED_PROFILE = "chat-2k-512-c1-600s"
MAX_SECONDS = 4800


def require(value):
    if not value:
        raise ValueError("owned stream cohort rejected")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def sha(value):
    require(type(value) is str and re.fullmatch("[0-9a-f]{64}", value))
    return value


def enabled(contract):
    require(contract.get("stream_cohort") in ({"schema": SCHEMA},
            {"schema": SCHEMA, "profile": EXTENDED_PROFILE})
            and contract.get("operation") == "local-correctness"
            and (contract.get("gpu_family") == "B300" or
                 (contract.get("gpu_family") == "B200" and contract.get("data_parallel_size") == 2
                  and type(contract.get("model_profile")) is dict
                  and contract["model_profile"].get("name") == "uncensored"))
            and (contract.get("gpu_security", "confidential") == "confidential" or
                 (contract.get("gpu_security") == "nonconfidential" and contract.get("gpu_family") == "B300"
                  and contract.get("data_parallel_size") == 2 and type(contract.get("model_profile")) is dict
                  and contract["model_profile"].get("name") == "uncensored"))
            and type(contract.get("data_parallel_size", 1)) is int and contract.get("data_parallel_size", 1) in (1, 2)
            and contract.get("tokenizer_parity") is True and "tokenizer_materialization" in contract
            and contract.get("runtime_observation") is True and "provider_conformance" not in contract)


def cohort_seconds(contract, cohort):
    enabled(contract)
    return (cohort.EXTENDED_COHORT_SECONDS if "profile" in contract["stream_cohort"]
            else cohort.COHORT_SECONDS)


def load(sources, pins):
    """Compile only the fixed, authenticated source closure, not ambient modules."""
    modules = {}
    def source_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in NAMES:
            require(level == 0 and name in modules)
            return modules[name]
        return builtins.__import__(name, globals, locals, fromlist, level)
    for name in NAMES:
        path = "stream/" + name + ".py"
        raw = sources[path]
        require(type(raw) is bytes and 0 < len(raw) <= 2 << 20
                and hashlib.sha256(raw).hexdigest() == sha(pins[path]))
        module = types.ModuleType(name)
        module.__file__ = "/opt/nib/" + path
        module.__dict__["__builtins__"] = {**vars(builtins), "__import__": source_import}
        exec(compile(raw, module.__file__, "exec", dont_inherit=True), module.__dict__)
        modules[name] = module
    return modules["stream_cohort"]


def workload(value, cohort):
    require(type(value) is dict and set(value) == {"schema", "cases", "binding_sha256"}
            and value["schema"] == "nib.chat-stream-workload/v1" and type(value["cases"]) is list
            and len(value["cases"]) == cohort.TOTAL_REQUESTS)
    cases = []
    for index, row in enumerate(value["cases"]):
        require(type(row) is dict and set(row) == {"index", "padding", "prompt_tokens", "messages_sha256", "token_ids_sha256"}
                and type(row["index"]) is int and row["index"] == index
                and type(row["prompt_tokens"]) is int and row["prompt_tokens"] == cohort.PROMPT_TOKENS)
        cases.append((row["padding"], row["token_ids_sha256"]))
    result = cohort.Workload(tuple(cases))
    require(value == result.metadata())
    return result


def child(tokenizer, parity, contract_sha, cohort, deadline):
    return {"schema": CHILD_SCHEMA, "contract_sha256": sha(contract_sha), "parity": parity,
            "workload": cohort.prepare(tokenizer, deadline=deadline).metadata()}


def admit_child(value, contract_sha, cohort, parity, correctness):
    require(type(value) is dict and set(value) == {"schema", "contract_sha256", "parity", "workload"}
            and value["schema"] == CHILD_SCHEMA and value["contract_sha256"] == sha(contract_sha))
    parity.validate_local(value["parity"], correctness)
    return value["parity"], workload(value["workload"], cohort)


def run(engine, contract, contract_sha, prepared, cohort, observer, deadline, *, model=None):
    enabled(contract)
    model = selected_model(contract, cohort, model)
    require(engine.poll() is None and time.monotonic() < deadline)
    # The PID is the owner's current child, not a PID supplied by a receipt.
    before = observer.process(engine.pid, deadline)
    require(before["parent"] == os.getpid())
    inode = observer.listener(engine.pid, deadline)
    identity = cohort.stream.identity
    facts = identity.process_facts(engine.pid)
    require(facts == {"start_ticks": before["start"], "boot_id_sha256": before["boot"],
                     "netns_inode": before["namespaces"]["net"], "argv_sha256": before["argv"]})
    process = {"pid": engine.pid, **facts, "exe_sha256": contract["interpreter"]["sha256"], "listener_inode": inode}
    endpoint = identity.Endpoint(cohort.stream.PORT, {"process": process})
    endpoint.check(deadline)
    require(observer.process(engine.pid, deadline) == before and engine.poll() is None)
    result = {"schema": RESULT_SCHEMA, "contract_sha256": sha(contract_sha),
              "engine_identity_sha256": digest(process), "terminal_identity_verified": False,
              "cohort": cohort.run(endpoint, prepared, deadline=deadline,
                                   gpu_count=4 * contract.get("data_parallel_size", 1),
                                   cohort_seconds=cohort_seconds(contract, cohort), model=model)}
    try:
        endpoint.check(deadline)
        require(engine.poll() is None and observer.process(engine.pid, deadline) == before
                and observer.listener(engine.pid, deadline) == inode)
        result["terminal_identity_verified"] = True
    except Exception:
        # Preserve completed/failed usage even if terminal identity changed.
        pass
    return result


def validate_sample(row, cohort):
    meter = cohort.meter
    require(type(row) is dict and set(row) == {"status", "elapsed_seconds", "ttft_seconds", "first_content_seconds",
        "mean_output_token_interval_estimate_seconds", "output_events", "prompt_tokens", "completion_tokens",
        "cached_prompt_tokens", "finish_reason"} and row["status"] in meter.FAILURES | {"completed"}
        and meter.number(row["elapsed_seconds"]) and row["elapsed_seconds"] <= MAX_SECONDS
        and meter.integer(row["output_events"], meter.MAX_STREAM) and row["finish_reason"] in (None, "stop", "length"))
    for key, limit in (("prompt_tokens", cohort.PROMPT_TOKENS), ("completion_tokens", cohort.OUTPUT_TOKENS)):
        require(row[key] is None or meter.integer(row[key], limit))
    require((row["prompt_tokens"] is None) == (row["completion_tokens"] is None)
            and (row["completion_tokens"] is None or row["completion_tokens"] > 0)
            and (row["cached_prompt_tokens"] is None or row["prompt_tokens"] is not None
                 and meter.integer(row["cached_prompt_tokens"], row["prompt_tokens"])))
    timing = ("ttft_seconds", "first_content_seconds", "mean_output_token_interval_estimate_seconds")
    for key in timing:
        require(row[key] is None or meter.number(row[key]) and row[key] <= row["elapsed_seconds"])
    if row["status"] == "completed":
        require(row["prompt_tokens"] == cohort.PROMPT_TOKENS and row["completion_tokens"] is not None
                and row["output_events"] > 0 and row["ttft_seconds"] is not None
                and row["finish_reason"] is not None and row["elapsed_seconds"] < cohort.stream.REQUEST_SECONDS)
        require(row["first_content_seconds"] is None or row["first_content_seconds"] >= row["ttft_seconds"])
        interval = row["mean_output_token_interval_estimate_seconds"]
        require(interval is None or row["completion_tokens"] > 1 and row["output_events"] > 1
                and interval > 0 and row["ttft_seconds"] + interval * (row["completion_tokens"] - 1)
                    <= row["elapsed_seconds"] + 1e-6)
    else:
        require(all(row[key] is None for key in timing))


def selected_model(contract, cohort, model):
    if "model_profile" not in contract:
        require(model in (None, cohort.stream.MODEL))
        return cohort.stream.MODEL
    # The owner validates the fixed source-bound identity before calling us.
    # Never infer a new checkpoint from an old-name response or a receipt alone.
    require(type(model) is str and model == contract["model_profile"]["served_model_name"])
    return model


def validate(value, contract, contract_sha, cohort, *, model=None):
    enabled(contract)
    model = selected_model(contract, cohort, model)
    require(type(value) is dict and set(value) == {"schema", "contract_sha256", "engine_identity_sha256",
        "terminal_identity_verified", "cohort"} and value["schema"] == RESULT_SCHEMA
        and value["contract_sha256"] == sha(contract_sha) and type(value["terminal_identity_verified"]) is bool)
    sha(value["engine_identity_sha256"])
    row, meter = value["cohort"], cohort.meter
    fixed = {"schema": "nib.chat-stream-cohort/v1", "model": model, "concurrency": 1,
        "prompt_tokens_per_request": cohort.PROMPT_TOKENS, "max_output_tokens_per_request": cohort.OUTPUT_TOKENS,
        "reserved_requests": cohort.TOTAL_REQUESTS, "reserved_total_tokens": cohort.RESERVED_TOKENS,
        "pricing_measured": False, "quality_accepted": False, "confidential": False, "sweep_accepted": False}
    require(type(row) is dict and set(row) == set(fixed) | {"workload", "started_requests", "not_run_requests",
        "known_prompt_tokens", "known_output_tokens", "requests_with_unknown_usage", "warmup", "samples", "measured",
        "elapsed_seconds", "stop_reason", "completed"})
    require(all(type(row[key]) is type(expected) and row[key] == expected for key, expected in fixed.items()))
    workload(row["workload"], cohort)
    require(type(row["samples"]) is list and len(row["samples"]) <= cohort.MEASURED_REQUESTS
            and type(row["completed"]) is bool and meter.number(row["elapsed_seconds"])
            and row["elapsed_seconds"] <= MAX_SECONDS)
    samples = ([row["warmup"]] if row["warmup"] is not None else []) + row["samples"]
    for sample in samples:
        validate_sample(sample, cohort)
    require(sum(sample["elapsed_seconds"] for sample in samples) <= row["elapsed_seconds"] + 1e-6)
    expected = {"started_requests": len(samples), "not_run_requests": cohort.TOTAL_REQUESTS - len(samples),
        "known_prompt_tokens": sum(sample["prompt_tokens"] or 0 for sample in samples),
        "known_output_tokens": sum(sample["completion_tokens"] or 0 for sample in samples),
        "requests_with_unknown_usage": sum(sample["completion_tokens"] is None for sample in samples)}
    require(all(type(row[key]) is int and row[key] == count for key, count in expected.items()))
    measured = row["samples"]
    if measured:
        require(row["warmup"] is not None and row["warmup"]["status"] == "completed"
                and type(row["measured"]) is dict)
        wall = row["measured"].get("wall_seconds")
        require(meter.number(wall) and wall > 0
                and sum(sample["elapsed_seconds"] for sample in measured) <= wall + 1e-6
                and wall + row["warmup"]["elapsed_seconds"] <= row["elapsed_seconds"] + 1e-6)
        expected_summary = meter._summarize_rows(measured, scheduled=cohort.MEASURED_REQUESTS,
            wall_seconds=wall, gpu_count=4 * contract.get("data_parallel_size", 1))
        # Canonical JSON distinguishes false from zero and disallows extra fields.
        require(digest(row["measured"]) == digest(expected_summary))
    else:
        require(row["measured"] is None)
    stop = row["stop_reason"]
    require(stop in ("complete", "deadline", "warmup-failed", "request-failed") and row["completed"] == (stop == "complete"))
    if stop == "warmup-failed":
        require(row["warmup"] is not None and row["warmup"]["status"] != "completed" and not measured)
    elif stop == "request-failed":
        require(measured and measured[-1]["status"] != "completed"
                and all(sample["status"] == "completed" for sample in measured[:-1]))
    else:
        require(all(sample["status"] == "completed" for sample in samples))
        require(stop != "complete" or len(measured) == cohort.MEASURED_REQUESTS
                and row["elapsed_seconds"] < cohort_seconds(contract, cohort))
    return row["completed"] and value["terminal_identity_verified"]
