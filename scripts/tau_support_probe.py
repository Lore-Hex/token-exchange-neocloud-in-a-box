# SPDX-License-Identifier: Apache-2.0
"""Fixed resource-only CPU probe policy, never a comparison or price manifest."""
import time

import tau_preflight as tau

SCHEMA = "nib.tau-cpu-support-probe-input/v1"
IDENTITY_SCHEMA = "nib.tau-cpu-identity-probe-input/v1"
IDENTITY_17_SCHEMA = "nib.tau-cpu-identity-17b-probe-input/v1"
IDENTITY_17_REPORT = "nib.tau-cpu-identity-17b-probe/v1"
PINS = "scripts/tau_cpu_support_models.json"
MODELS = ("qwen/qwen3-0.6b", "qwen/qwen3-1.7b")
SELECTION = {"domain": "retail", "task_id": "2", "trial": 0, "seed": 42, "role": "candidate"}
GENERATION = {"temperature": "0", "max_tokens": 256, "num_retries": 0}
LIMITS = {"concurrency": 1, "retries": 0, "turns": 3, "requests": 3,
          "request_seconds": 120, "trial_seconds": 180, "run_seconds": 300,
          "input_tokens_per_request": 4096, "output_tokens_per_request": 256, "total_tokens": 13056}


def is_identity(value):
    return type(value) is dict and value.get("schema") in (IDENTITY_SCHEMA, IDENTITY_17_SCHEMA)


def report_schema(value):
    return (IDENTITY_17_REPORT if value.get("schema") == IDENTITY_17_SCHEMA else
            "nib.tau-cpu-identity-probe/v1" if is_identity(value) else "nib.tau-cpu-probe/v1")


def configuration(*, identity_followup=False, identity_followup_17b=False):
    tau.require(type(identity_followup) is bool and type(identity_followup_17b) is bool
                and not (identity_followup and identity_followup_17b))
    pins = tau.json_value(tau.regular(tau.ROOT / PINS, 16384, time.monotonic() + 5))
    tau.require(set(pins["models"]) == set(MODELS))
    result = {"schema": IDENTITY_SCHEMA if identity_followup else SCHEMA, "upstream_revision": tau.REVISION, "selection": dict(SELECTION),
            "support": {role: {"model": model, "revision": pins["models"][model]["revision"]}
                        for role, model in zip(("simulator", "evaluator"), MODELS)},
            "generation": dict(GENERATION), "limits": dict(LIMITS), "target": "scripted",
            "pricing": "not-measured", "quality_eligible": False, "confidential": False, "live_admission": False}
    if identity_followup_17b:
        # The fixed CPU pair retains its original receipt roles. Bind this
        # diagnostic explicitly to the 1.7B process, with no evaluator client.
        result.update(schema=IDENTITY_17_SCHEMA, support={"simulator": result["support"]["evaluator"]},
                      owner_roles={"simulator": "evaluator"})
    return result


def validate(value, sha=None):
    tau.require(type(value) is dict and tau.encoded(value) == tau.encoded(
        configuration(identity_followup=value.get("schema") == IDENTITY_SCHEMA,
                      identity_followup_17b=value.get("schema") == IDENTITY_17_SCHEMA)))
    if sha is not None:
        tau.sha(sha)
        tau.require(tau.digest(tau.encoded(value)) == sha)
    return value


def runtime_binding(runtime):
    pinned = configuration(identity_followup_17b=True)["support"]["simulator"]
    tau.require(runtime["role"] == "evaluator" and runtime["model"] == pinned["model"]
                and runtime["revision"] == pinned["revision"])
    return validate_binding({"role": "simulator", "owner_role": "evaluator", **pinned,
        "runtime_sha256": tau.digest(tau.encoded(runtime)), "process_sha256": tau.digest(tau.encoded(runtime["process"])),
        "model_files_sha256": runtime["model_files_sha256"],
        **{key: runtime["serving"][key] for key in ("tokenizer_sha256", "framing_sha256")}})


def validate_binding(value):
    pinned = configuration(identity_followup_17b=True)["support"]["simulator"]
    hashes = ("runtime_sha256", "process_sha256", "model_files_sha256", "tokenizer_sha256", "framing_sha256")
    tau.require(type(value) is dict and set(value) == {"role", "owner_role", "model", "revision", *hashes}
                and value["role"] == "simulator" and value["owner_role"] == "evaluator"
                and all(value[key] == expected for key, expected in pinned.items()))
    for key in hashes:
        tau.sha(value[key])
    return value


def source_selection(sources):
    prefix = "data/tau2/domains/retail/"
    ids = tau.task_ids(sources[prefix + "tasks.json"], sources[prefix + "split_tasks.json"])
    tau.require("2" in ids)
    rows = tau.json_value(sources[prefix + "tasks.json"])
    chosen = [row for row in rows if row["id"] == "2"]
    tau.require(len(chosen) == 1 and chosen[0].get("initial_state") is None and chosen[0].get("user_tools") is None)
    assertions = chosen[0]["evaluation_criteria"]["nl_assertions"]
    tau.require(type(assertions) is list and len(assertions) == 1
                and type(assertions[0]) is str and 0 < len(assertions[0]) <= 16384)
    return dict(SELECTION)


def settings(value):
    validate(value)
    return {"roles": {role: {"generation": dict(GENERATION)} for role in ("candidate", "simulator", "evaluator")},
            "limits": dict(LIMITS)}


def transport_manifest(value, sha):
    tau.require(type(value) is dict and set(value) == {"schema", "input", "settings", "framing"}
                and value["schema"] == "nib.tau-cpu-support-transport/v1"
                and tau.digest(tau.encoded(value)) == sha
                and value["settings"] == settings(value["input"])
                and type(value["framing"]) is dict and set(value["framing"]) == {"candidate", "simulator", "evaluator"})
    for role, row in value["framing"].items():
        tau.require(type(row) is dict and set(row) == {"reasoning_effort", "parallel_tool_calls", "served_model",
                    "framing_sha256", "tokenizer_sha256", "context_tokens"}
                    and row["reasoning_effort"] == "none" and row["parallel_tool_calls"] is False
                    and type(row["context_tokens"]) is int and row["context_tokens"] == 4096
                    and row["served_model"] == ("scripted-candidate" if role == "candidate" else
                        "disabled-evaluator" if role == "evaluator" and value["input"]["schema"] == IDENTITY_17_SCHEMA
                        else value["input"]["support"][role]["model"]))
        tau.sha(row["framing_sha256"])
        tau.sha(row["tokenizer_sha256"])
    return value


class ResourceBudget(tau.Budget):
    """Identical token/time/sticky accounting; dollar accounting is not measured."""
    def __init__(self, value, *, clock=time.monotonic):
        self._initialize(settings(value), clock)

    def _initial_spend(self):
        return tau.amount("0")

    def _price(self, role, incoming, outgoing):
        tau.require(role in ("candidate", "simulator", "evaluator"))
        return tau.amount("0")

    def _within_cost(self, price):
        return self.spent == price == 0
