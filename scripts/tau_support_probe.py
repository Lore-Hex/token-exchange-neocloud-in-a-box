# SPDX-License-Identifier: Apache-2.0
"""Fixed resource-only CPU probe policy, never a comparison or price manifest."""
import time

import tau_preflight as tau

SCHEMA = "nib.tau-cpu-support-probe-input/v1"
IDENTITY_SCHEMA = "nib.tau-cpu-identity-probe-input/v1"
PINS = "scripts/tau_cpu_support_models.json"
MODELS = ("qwen/qwen3-0.6b", "qwen/qwen3-1.7b")
SELECTION = {"domain": "retail", "task_id": "2", "trial": 0, "seed": 42, "role": "candidate"}
GENERATION = {"temperature": "0", "max_tokens": 256, "num_retries": 0}
LIMITS = {"concurrency": 1, "retries": 0, "turns": 3, "requests": 3,
          "request_seconds": 120, "trial_seconds": 180, "run_seconds": 300,
          "input_tokens_per_request": 4096, "output_tokens_per_request": 256, "total_tokens": 13056}


def configuration(*, identity_followup=False):
    tau.require(type(identity_followup) is bool)
    pins = tau.json_value(tau.regular(tau.ROOT / PINS, 16384, time.monotonic() + 5))
    tau.require(set(pins["models"]) == set(MODELS))
    return {"schema": IDENTITY_SCHEMA if identity_followup else SCHEMA, "upstream_revision": tau.REVISION, "selection": dict(SELECTION),
            "support": {role: {"model": model, "revision": pins["models"][model]["revision"]}
                        for role, model in zip(("simulator", "evaluator"), MODELS)},
            "generation": dict(GENERATION), "limits": dict(LIMITS), "target": "scripted",
            "pricing": "not-measured", "quality_eligible": False, "confidential": False, "live_admission": False}


def validate(value, sha=None):
    tau.require(type(value) is dict and tau.encoded(value) == tau.encoded(
        configuration(identity_followup=value.get("schema") == IDENTITY_SCHEMA)))
    if sha is not None:
        tau.sha(sha)
        tau.require(tau.digest(tau.encoded(value)) == sha)
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
                    and row["served_model"] == ("scripted-candidate" if role == "candidate" else value["input"]["support"][role]["model"]))
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
