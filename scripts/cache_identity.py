# SPDX-License-Identifier: Apache-2.0
"""Closed cache candidates and official workload evidence; no admission grant."""

import re

import quality_candidate
import selected_long_qualification as workload
import stream_transport
import tau_external_tokenizer as tokenizer

require, worker = tokenizer.require, tokenizer.worker
SHAPES = {2048: 512, 32768: 1024, 131072: 1024}
PREPARATION_FLAGS = {"inference_executed", "runtime_parity_verified",
                     "billing_reconciled", "tariff_change_justified"}


def fields(candidate="selected"):
    require(type(candidate) is str and candidate in ("selected", "original"))
    return quality_candidate.original_fields() if candidate == "original" else {}


def model(candidate="selected"):
    return fields(candidate).get("model", stream_transport.SELECTED_MODEL)


def schema(kind, candidate="selected"):
    fields(candidate)
    return "nib." + kind + ("/v2" if candidate == "original" else "/v1")


def validate_original(row):
    expected = quality_candidate.validate_original(row)
    # Canonical JSON also distinguishes integer profile totals from floats/bools.
    require(worker.encoded({key: row[key] for key in expected}) == worker.encoded(expected))


def source_bindings():
    return {worker.digest(worker.encoded({"sources": tokenizer.SOURCES,
                "model_files": tokenizer.parity.MODEL_FILES, "wheel_sha256": sha}))
            for _, sha in tokenizer.WHEELS.values()}


def tokenizer_fields(source_binding):
    require(type(source_binding) is str and source_binding in source_bindings())
    source = {"tokenizer_source_binding_sha256": source_binding}
    return {**source, "tokenizer_binding_sha256":
            worker.digest(worker.encoded({**fields("original"), **source}))}


def validate_preparation(row):
    require(type(row) is dict and set(row) == set(fields("original")) | PREPARATION_FLAGS |
            {"schema", "tokenizer_source_binding_sha256", "tokenizer_binding_sha256", "cases"}
            and row["schema"] == schema("cache-workload", "original"))
    validate_original(row)
    expected = tokenizer_fields(row["tokenizer_source_binding_sha256"])
    require(all(row[key] == value for key, value in expected.items())
            and all(row[key] is False for key in PREPARATION_FLAGS)
            and type(row["cases"]) is list and len(row["cases"]) == len(SHAPES))
    for case, (prompt, output) in zip(row["cases"], SHAPES.items()):
        require(type(case) is dict and set(case) == {"padding", "prompt_tokens",
                "max_output_tokens", "tokens_sha256", "messages_sha256"}
                and type(case["padding"]) is int and 0 <= case["padding"] <= workload.MAX_PADDING
                and type(case["prompt_tokens"]) is int and case["prompt_tokens"] == prompt
                and type(case["max_output_tokens"]) is int and case["max_output_tokens"] == output
                and type(case["tokens_sha256"]) is str
                and re.fullmatch("[0-9a-f]{64}", case["tokens_sha256"])
                and case["messages_sha256"] == workload.digest(workload.messages(3, case["padding"])))
    return row


def prepared_case(preparation, prompt_tokens):
    validate_preparation(preparation)
    require(type(prompt_tokens) is int and prompt_tokens in SHAPES)
    return next(row for row in preparation["cases"] if row["prompt_tokens"] == prompt_tokens)


def evidence(preparation, prompt_tokens):
    case = prepared_case(preparation, prompt_tokens)
    return {**fields("original"), "workload_sha256": workload.digest(preparation),
            **{key: preparation[key] for key in (
                "tokenizer_source_binding_sha256", "tokenizer_binding_sha256")},
            "tokens_sha256": case["tokens_sha256"]}
