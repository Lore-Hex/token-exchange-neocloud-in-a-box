# SPDX-License-Identifier: Apache-2.0
"""Offline-only tool/count projection; deliberately not wired into live owners."""

import tau_external as external

worker, require = external.worker, external.require
SCHEMA = "nib.tau-telecom-count-projection/v1"


def project(body, expected_tools):
    """Return private in-memory payloads, not a log or publishable receipt.

    The caller must authenticate expected_tools from the pinned upstream runtime.
    This helper compares that catalog but does not itself authenticate its source.
    """
    wire = external._chat_request(body, full_task=True, model=external.policy.EXTERNAL_MODEL, max_tools=30)
    require(set(wire) <= {"model", "messages", "tools", "tool_choice", "parallel_tool_calls",
                         "max_completion_tokens", "temperature", "stream", "n", "seed", "reasoning_effort"}
            and type(wire.get("tools")) is list and len(wire["tools"]) == 30
            and wire["tools"] == worker.decode(worker.bounded_json(expected_tools, 65536))
            and wire.get("tool_choice") == "auto" and wire.get("parallel_tool_calls") is False
            and wire.get("stream") is False and type(wire.get("n")) is int and wire["n"] == 1
            and type(wire.get("temperature")) in (int, float) and wire["temperature"] == 0)
    if "seed" in wire:
        require(type(wire["seed"]) is int and 0 <= wire["seed"] < 2**32)
    items, pending = [], []
    for message in wire["messages"]:
        if message["role"] == "tool":
            require(pending and message["tool_call_id"] == pending.pop(0))
            items.append({"type": "function_call_output", "call_id": message["tool_call_id"],
                          "output": message["content"]})
            continue
        require(not pending)
        if message["content"] is not None:
            items.append({"role": message["role"], "content": message["content"]})
        calls = message.get("tool_calls", [])
        require(type(calls) is list and len(calls) <= worker.MAX_TOOL_CALLS)
        for call in calls:
            pending.append(call["id"])
            items.append({"type": "function_call", "call_id": call["id"],
                          "name": call["function"]["name"], "arguments": call["function"]["arguments"]})
    require(not pending)
    # Responses defaults can normalize omitted strictness. Explicit false keeps
    # the original Chat schemas non-strict without rewriting their properties.
    # https://developers.openai.com/api/docs/guides/function-calling#strict-mode
    count = {"model": wire["model"], "input": items, "truncation": "disabled",
             "tools": [{"type": "function", **tool["function"], "strict": False} for tool in wire["tools"]],
             "tool_choice": wire["tool_choice"], "parallel_tool_calls": False}
    chat = {key: value for key, value in wire.items() if key != "reasoning_effort"}
    chat.update(store=False, service_tier="default")
    worker.bounded_json(chat, 65536)
    worker.bounded_json(count, 65536)
    return {"schema": SCHEMA, "chat": chat, "count": count,
            "request_sha256": worker.digest(worker.encoded(body)),
            "tool_catalog_sha256": worker.digest(worker.encoded(wire["tools"])),
            "source_authenticated": False, "token_count_parity_verified": False,
            "live_admission": False, "quality_eligible": False}
