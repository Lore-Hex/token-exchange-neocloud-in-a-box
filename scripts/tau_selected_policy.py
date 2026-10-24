# SPDX-License-Identifier: Apache-2.0
"""Fixed selected-checkpoint operational trial, separate from original quality."""

import time

import deepseek_model_profile as models
import tau_support_probe as support

tau = support.tau
SCHEMA = "nib.tau-selected-operational-input/v1"
TRANSPORT = "nib.tau-selected-operational-transport/v1"
CONTEXTS = {"candidate": 8192, "simulator": 4096, "evaluator": 4096}
LIMITS = {"concurrency": 1, "retries": 0, "turns": 32, "requests": 32,
          "request_seconds": 120, "trial_seconds": 900, "run_seconds": 960,
          "input_tokens_per_request": 8192, "output_tokens_per_request": 256,
          "total_tokens": 32 * (8192 + 256)}


def configuration():
    return {"schema": SCHEMA, "upstream_revision": tau.REVISION,
            "selection": dict(support.SELECTION), "model_profile": models.select("uncensored"),
            "support": support.configuration()["support"], "generation": dict(support.GENERATION),
            "limits": dict(LIMITS), "contexts": dict(CONTEXTS), "pricing": "not-measured",
            "quality_eligible": False, "confidential": False, "live_admission": False}


def validate(value, sha=None):
    tau.require(tau.encoded(value) == tau.encoded(configuration()))
    if sha is not None:
        tau.sha(sha)
        tau.require(tau.digest(tau.encoded(value)) == sha)
    return value


def settings(value):
    validate(value)
    return {"roles": {role: {"generation": dict(support.GENERATION)} for role in CONTEXTS},
            "limits": dict(LIMITS)}


def transport_manifest(value, sha):
    tau.require(type(value) is dict and set(value) == {"schema", "input", "settings", "framing", "owners"}
                and value["schema"] == TRANSPORT and tau.digest(tau.encoded(value)) == sha
                and value["settings"] == settings(value["input"])
                and type(value["framing"]) is dict and set(value["framing"]) == set(CONTEXTS)
                and type(value["owners"]) is dict and set(value["owners"]) == {"candidate", "support"})
    from tau_owner_channel import binding
    for name, roles in (("candidate", {"candidate"}), ("support", {"simulator", "evaluator"})):
        row = binding(value["owners"][name])
        tau.require(set(row["roles"]) == roles)
        for role in roles:
            framed = value["framing"][role]
            tau.require(row["roles"][role] == {"model": framed["served_model"], "context_tokens": CONTEXTS[role]})
    tau.require(value["owners"]["candidate"]["ready_sha256"] != value["owners"]["support"]["ready_sha256"])
    for role, row in value["framing"].items():
        expected_model = (value["input"]["model_profile"]["served_model_name"] if role == "candidate"
                          else value["input"]["support"][role]["model"])
        tau.require(type(row) is dict and set(row) == {"reasoning_effort", "parallel_tool_calls", "served_model",
                    "framing_sha256", "tokenizer_sha256", "context_tokens"}
                    and row["reasoning_effort"] == "none" and row["parallel_tool_calls"] is False
                    and type(row["context_tokens"]) is int and row["context_tokens"] == CONTEXTS[role]
                    and row["served_model"] == expected_model)
        tau.sha(row["framing_sha256"])
        tau.sha(row["tokenizer_sha256"])
        if role == "candidate":
            tau.require(row["tokenizer_sha256"] == tau.VOCABULARY_DIGEST)
    return value


class ResourceBudget(support.ResourceBudget):
    """Reuse sticky resource accounting; zero internal dollars are not free GPUs."""
    def __init__(self, value, *, clock=time.monotonic):
        self._initialize(settings(value), clock)
