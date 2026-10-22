# SPDX-License-Identifier: Apache-2.0
"""Explicit llama.cpp CPU-support transport; not a live tau admission path."""

import time

from tau_local_vllm import LocalChatHTTP, require_nonthinking_auto, require, worker

REVISION = "620d2225cc436ab0b001e3200e590e3b286ef9ab"
MODELS = frozenset(("qwen/qwen3-0.6b", "qwen/qwen3-1.7b"))


class LocalLlamaCPP(LocalChatHTTP):
    """Text-only pilot models, with caller-declared context and engine counts.

    The caller still needs measured runtime, model, full framing and tokenizer
    identity. These HTTP calls do not prove any of those or independent parity.
    No public API, credentials, launch operation or receipt issuer is provided.
    """
    PATHS = ("/apply-template", "/tokenize", "/v1/chat/completions")

    def __init__(self, port, model, context_tokens, *, clock=time.monotonic):
        super().__init__(port, clock=clock)
        require(type(model) is str and model in MODELS
                and type(context_tokens) is int and context_tokens == 4096)
        self.model, self.context_tokens = model, context_tokens

    def generation_body(self, body):
        require(type(body) is dict
                and {"model", "messages", "stream", "n", "temperature",
                     "max_completion_tokens", "reasoning_effort", "parallel_tool_calls"}
                <= set(body)
                <= {"model", "messages", "stream", "n", "temperature",
                    "max_completion_tokens", "reasoning_effort", "parallel_tool_calls",
                    "seed", "tools", "tool_choice"}
                and body["model"] == self.model
                and body["stream"] is False and type(body["n"]) is int and body["n"] == 1
                and type(body["max_completion_tokens"]) is int
                and 0 < body["max_completion_tokens"] <= 256)
        require_nonthinking_auto(body)
        require(type(body["messages"]) is list and 0 < len(body["messages"]) <= 512)
        for message in body["messages"]:
            require(type(message) is dict and "role" in message
                    and set(message) <= {"role", "content", "tool_calls", "tool_call_id"}
                    and message["role"] in ("system", "user", "assistant", "tool")
                    and (message.get("content") is None or type(message["content"]) is str))
        # The parent has already validated the text messages/tools. Both render
        # and generation receive identical fields through the same upstream parser.
        return {**body, "chat_template_kwargs": {"enable_thinking": False}}

    def count(self, body, context_tokens, deadline):
        require(type(context_tokens) is int and context_tokens == self.context_tokens)
        rendered = self.post("/apply-template", self.generation_body(body), deadline)
        require(type(rendered) is dict and set(rendered) == {"prompt"}
                and type(rendered["prompt"]) is str and 0 < len(rendered["prompt"]) <= worker.MAX_REQUEST)
        # server-context.cpp tokenizes chat generation with add_special=true and
        # parse_special=true. /tokenize defaults add_special=false: set both.
        row = self.post("/tokenize", {"content": rendered["prompt"], "add_special": True,
                                     "parse_special": True, "with_pieces": False}, deadline)
        require(type(row) is dict and set(row) == {"tokens"} and type(row["tokens"]) is list
                and 0 < len(row["tokens"]) <= context_tokens
                and all(type(token) is int and 0 <= token < 2**31 for token in row["tokens"]))
        return len(row["tokens"])

    def complete(self, body, prompt_tokens, deadline):
        require(type(prompt_tokens) is int and prompt_tokens > 0)
        prepared = self.generation_body(body)
        require(prompt_tokens + prepared["max_completion_tokens"] <= self.context_tokens)
        return super().complete(prepared, prompt_tokens, deadline)


if __name__ == "__main__":
    raise SystemExit("Library only. CPU support transport is not tau admission or quality proof.")
