# SPDX-License-Identifier: Apache-2.0
"""Fixed selected-checkpoint operational trial, separate from original quality."""

import time

import deepseek_model_profile as models
import tau_support_probe as support

tau = support.tau
SCHEMA = "nib.tau-selected-operational-input/v1"
RESOLUTION_SCHEMA = "nib.tau-selected-customer-resolution-input/v1"
EXTERNAL_SCHEMA = "nib.tau-external-customer-resolution-input/v1"
EXTERNAL_TRANSPORT = "nib.tau-external-customer-resolution-transport/v1"
EXTERNAL_MODEL = "gpt-4.1-2025-04-14"
PUBLIC_MODEL = "dealignai/deepseek-v4.1-flash-1017-uncensored-fp8"
RESOLUTION_REPORT = "nib.tau-selected-customer-resolution/v1"
TRANSPORT = "nib.tau-selected-operational-transport/v1"
CONTEXTS = {"candidate": 8192, "simulator": 4096, "evaluator": 4096}
LIMITS = {"concurrency": 1, "retries": 0, "turns": 32, "requests": 32,
          "request_seconds": 120, "trial_seconds": 900, "run_seconds": 960,
          "input_tokens_per_request": 8192, "output_tokens_per_request": 256,
          "total_tokens": 32 * (8192 + 256)}


def is_resolution(value):
    return type(value) is dict and value.get("schema") in (RESOLUTION_SCHEMA, EXTERNAL_SCHEMA)


def is_external(value):
    return type(value) is dict and value.get("schema") == EXTERNAL_SCHEMA


def configuration(*, customer_resolution=False, external_simulator=False):
    tau.require(type(customer_resolution) is bool)
    tau.require(type(external_simulator) is bool and (not external_simulator or customer_resolution))
    value = {"schema": SCHEMA, "upstream_revision": tau.REVISION,
            "selection": dict(support.SELECTION), "model_profile": models.select("uncensored"),
            "support": support.configuration()["support"], "generation": dict(support.GENERATION),
            "limits": dict(LIMITS), "contexts": dict(CONTEXTS), "pricing": "not-measured",
            "quality_eligible": False, "confidential": False, "live_admission": False}
    if customer_resolution:
        value.update(schema=RESOLUTION_SCHEMA, support={"simulator": value["support"]["evaluator"]},
                     owner_roles={"simulator": "evaluator"})
    if external_simulator:
        value.pop("owner_roles")
        value.update(schema=EXTERNAL_SCHEMA, support={"simulator": {"model": EXTERNAL_MODEL}},
                     contexts={"candidate": 8192, "simulator": 8192}, pricing="published-ceiling-not-invoice")
        # Pinned tau config.py DEFAULT_LLM_USER; official API/model/pricing and
        # 2027-01-14 shutdown checked 2026-10-25. Candidate rates are
        # this closed diagnostic's required operator-declared ceilings.
        # https://developers.openai.com/api/docs/pricing#standard-pricing-data
        value["prices"] = {"candidate": ["0.65", "1.45"], "simulator": ["2.00", "8.00"]}
        value["limits"] = {**LIMITS, "cost_usd": "1.00"}
    return value


def validate(value, sha=None):
    tau.require(tau.encoded(value) == tau.encoded(configuration(customer_resolution=is_resolution(value),
                                                              external_simulator=is_external(value))))
    if sha is not None:
        tau.sha(sha)
        tau.require(tau.digest(tau.encoded(value)) == sha)
    return value


def settings(value):
    validate(value)
    roles = ("candidate", "simulator") if is_resolution(value) else CONTEXTS
    result = {"roles": {role: {"generation": dict(support.GENERATION)} for role in roles},
              "limits": dict(value["limits"])}
    if is_external(value):
        for role, prices in value["prices"].items():
            result["roles"][role].update(input_usd_per_million=prices[0], output_usd_per_million=prices[1])
    return result


def transport_manifest(value, sha):
    if type(value) is dict and is_external(value.get("input")):
        validate(value["input"])
        tau.require(set(value) == {"schema", "input", "settings", "framing", "binding_sha256"}
                    and value["schema"] == EXTERNAL_TRANSPORT and tau.digest(tau.encoded(value)) == sha
                    and value["settings"] == settings(value["input"])
                    and set(value["framing"]) == {"candidate", "simulator"})
        tau.sha(value["binding_sha256"])
        for role, row in value["framing"].items():
            tau.require(set(row) == {"reasoning_effort", "parallel_tool_calls", "served_model",
                        "framing_sha256", "tokenizer_sha256", "context_tokens"}
                        and row["reasoning_effort"] == "none" and row["parallel_tool_calls"] is False
                        and type(row["context_tokens"]) is int and row["context_tokens"] == 8192
                        and row["served_model"] == (PUBLIC_MODEL if role == "candidate" else EXTERNAL_MODEL))
            tau.sha(row["framing_sha256"])
            tau.sha(row["tokenizer_sha256"])
        tau.require(value["framing"]["candidate"]["tokenizer_sha256"] == tau.VOCABULARY_DIGEST)
        return value
    resolution = type(value) is dict and is_resolution(value.get("input"))
    tau.require(type(value) is dict and set(value) == {"schema", "input", "settings", "framing", "owners"}
                | ({"simulator_binding"} if resolution else set())
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
            model = support.configuration()["support"][role]["model"] if resolution and name == "support" else framed["served_model"]
            tau.require(row["roles"][role] == {"model": model, "context_tokens": CONTEXTS[role]})
    if resolution:
        support.validate_binding(value["simulator_binding"])
        tau.require(all(value["simulator_binding"][key] == value["framing"]["simulator"][key]
                        for key in ("framing_sha256", "tokenizer_sha256")))
    tau.require(value["owners"]["candidate"]["ready_sha256"] != value["owners"]["support"]["ready_sha256"])
    for role, row in value["framing"].items():
        expected_model = ("disabled-evaluator" if resolution and role == "evaluator" else
                          value["input"]["model_profile"]["served_model_name"] if role == "candidate"
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


def resolution_result(value, *, completed=False):
    if value is None:
        tau.require(not completed)
        return None
    tau.require(type(value) is dict and set(value) == {"schema", "tool", "outcome", "intended_customer_match"}
                and value["schema"] == RESOLUTION_REPORT
                and value["tool"] in ("none", "email", "name_zip")
                and value["outcome"] in ("not_attempted", "resolved", "wrong_customer", "lookup_error",
                                         "unsupported_tool", "multiple_tools"))
    outcome, match = value["outcome"], value["intended_customer_match"]
    tau.require((outcome == "resolved" and match is True or outcome == "wrong_customer" and match is False
                 or outcome not in ("resolved", "wrong_customer") and match is None)
                and ((value["tool"] == "none") == (outcome in ("not_attempted", "unsupported_tool", "multiple_tools")))
                and (not completed or outcome == "resolved"))
    return value


class ResourceBudget(support.ResourceBudget):
    """Reuse sticky resource accounting; zero internal dollars are not free GPUs."""
    def __init__(self, value, *, clock=time.monotonic):
        self.external = is_external(value)
        self._initialize(settings(value), clock)

    def _price(self, role, incoming, outgoing):
        return (tau.Budget._price(self, role, incoming, outgoing) if self.external else
                super()._price(role, incoming, outgoing))

    def _within_cost(self, price):
        return tau.Budget._within_cost(self, price) if self.external else super()._within_cost(price)
