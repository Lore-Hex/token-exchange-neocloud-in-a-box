# SPDX-License-Identifier: Apache-2.0
"""Offline capacity-policy validation; no collector, owner or runnable profile.

vLLM advertises concurrency at max_model_len, not its scheduler's sequence cap.
One 128K request and 32 short requests per rank have different requirements.
This parser does not establish graph capture, rank placement or runtime safety.
"""

import hashlib

import selected_combined_plan as plan
import selected_service as service
import stream_capacity as capacity

require = plan.require
SCHEMA = "nib.selected-combined-capacity/v1"
CONTEXT, SEQUENCES, SHORT_CONTEXT = 131072, 32, 4096
# vLLM 5df63a7203cdb9328d824a27b682e77c7b9928be:
# get_kv_cache_capacity returns int(concurrency * max_model_len); metrics_info
# preserves the floating-point value with str(), rather than the log's %.2f.
SOURCES = {
    "vllm/v1/core/kv_cache_utils.py": "af4164bca819f1cdc416077522a7019936fd38a523a482283eb91b08dae0e57a",
    "vllm/config/cache.py": "d37db21e7a6a1b477a580b54d90011d8708c4e7f6e3903221432d9a39830559b",
}
HEADER = {"schema": SCHEMA, "execution_profile": plan.PROFILE,
          "model": service.MODEL, "context_tokens": CONTEXT,
          "engine_max_sequences": SEQUENCES, "public_max_inflight": 1,
          "runtime_qualified": False, "confidentiality_verified": False}


def verify_sources(read, root):
    capacity.verify_sources(read, root)
    for name, expected in SOURCES.items():
        require(hashlib.sha256(read(root + "/" + name, 256 << 10)).hexdigest() == expected)


def _ranks(rows):
    capacity._stable(rows, rows, minimum_concurrency=1)
    for row in rows:
        require(row["kv_capacity_tokens"] == int(row["kv_max_concurrency"] * CONTEXT)
                and row["kv_capacity_tokens"] >= max(CONTEXT, SEQUENCES * SHORT_CONTEXT)
                and row["running"] == row["waiting"] == 0)


def parse(raw, *, execution_profile, model):
    plan.profile(execution_profile)
    require(type(model) is str and model == service.MODEL)
    rows = capacity._parse(raw, model, minimum_concurrency=1)
    _ranks(rows)
    return {**HEADER, "ranks": rows}


def _snapshot(value):
    require(type(value) is dict and set(value) == {*HEADER, "ranks"})
    for name, expected in HEADER.items():
        require(type(value[name]) is type(expected) and value[name] == expected)
    _ranks(value["ranks"])


def stable(before, after):
    _snapshot(before)
    _snapshot(after)
    capacity._stable(before["ranks"], after["ranks"], minimum_concurrency=1)
