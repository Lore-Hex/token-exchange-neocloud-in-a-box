# SPDX-License-Identifier: Apache-2.0
"""Exact-runtime static tau prefix count, never a full-request fit proof."""

import hashlib
import json
import types

MODE = "tau-agent-prefix"
SCHEMA = "nib.tau-agent-prefix-count/v1"
CONTEXT = 4096
OUTPUT = 256
MAX_IDS = 1 << 20
MAX_REPORT = 4096


def require(value):
    if not value:
        raise ValueError("agent prefix count rejected")


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def validate(row, parity, correctness, request_sha=None):
    require(type(row) is dict and set(row) == {"schema", "request_sha256", "prefix_tokens", "tokens_sha256",
        "context_tokens", "output_reserve", "result", "controls", "boundary_verified", "generation_performed",
        "full_fit_proven", "quality_eligible", "confidential"} and row["schema"] == SCHEMA
        and type(row["prefix_tokens"]) is int and 0 < row["prefix_tokens"] <= MAX_IDS
        and type(row["context_tokens"]) is int and row["context_tokens"] == CONTEXT
        and type(row["output_reserve"]) is int and row["output_reserve"] == OUTPUT
        and row["result"] == ("context-exceeded" if row["prefix_tokens"] + OUTPUT > CONTEXT else "full-fit-unproven")
        and row["boundary_verified"] is True
        and all(row[key] is False for key in ("generation_performed", "full_fit_proven", "quality_eligible", "confidential")))
    for key in ("request_sha256", "tokens_sha256"):
        require(type(row[key]) is str and len(row[key]) == 64 and all(c in "0123456789abcdef" for c in row[key]))
    require(request_sha is None or row["request_sha256"] == request_sha)
    parity.validate_local(row["controls"], correctness)
    require(len(encoded(row)) <= MAX_REPORT)
    return row


def measure(body, tokenizer, tool_type, parity, correctness, prefix, vocabulary, user_token):
    prefix.validate(body)
    controls = parity.validate_local(parity.local_measure(tokenizer, tool_type, correctness), correctness)
    tools = [tool_type(**tool).model_dump() for tool in body["tools"]]
    args = {"tools": tools, "add_generation_prompt": True, "continue_final_message": False, **parity.TEMPLATE}
    ids = tokenizer.apply_chat_template(body["messages"], tokenize=True, **args)
    require(type(ids) is list and 0 < len(ids) <= MAX_IDS
            and all(type(token) is int and 0 <= token < 2**31 for token in ids))
    rendered = tokenizer.apply_chat_template(body["messages"], tokenize=False, **args)
    require(type(rendered) is str and 0 < len(rendered.encode()) <= prefix.MAX_BYTES)
    # The pinned renderer appends this added token before the actual first
    # user content. Verify its non-stripping boundary and this exact prefix;
    # no synthetic user text or substituted message is introduced.
    matches = [row for row in vocabulary["added_tokens"] if row.get("content") == user_token]
    require(len(matches) == 1)
    marker = matches[0]
    require(type(marker["id"]) is int and marker["special"] is False and marker["normalized"] is True
            and vocabulary["normalizer"] == {"type": "Sequence", "normalizers": []}
            and all(marker[key] is False for key in ("single_word", "lstrip", "rstrip")))
    for added in vocabulary["added_tokens"]:
        content = added["content"]
        require(type(content) is str and content)
        if content != user_token:
            # No longer added token may straddle this marker and arbitrary
            # future user text, including the non-normalized first pass.
            require(user_token not in content
                    and not any(len(content) > len(user_token[index:]) and content.startswith(user_token[index:])
                                for index in range(len(user_token))))
    require(tokenizer.encode(user_token, add_special_tokens=False) == [marker["id"]]
            and tokenizer.encode(rendered, add_special_tokens=False) == ids
            and tokenizer.encode(rendered + user_token, add_special_tokens=False) == ids + [marker["id"]])
    row = {"schema": SCHEMA, "request_sha256": digest(prefix.encoded(body)), "prefix_tokens": len(ids),
           "tokens_sha256": digest(parity.encoded(ids)), "context_tokens": CONTEXT, "output_reserve": OUTPUT,
           "result": "context-exceeded" if len(ids) + OUTPUT > CONTEXT else "full-fit-unproven",
           "controls": controls, "boundary_verified": True, "generation_performed": False,
           "full_fit_proven": False, "quality_eligible": False, "confidential": False}
    return validate(row, parity, correctness)


def modules(sources):
    result = []
    for name in ("tokenizer-parity", "correctness", "agent-prefix"):
        module = types.ModuleType(name)
        exec(compile(sources[name + ".py"], "/opt/nib/" + name + ".py", "exec"), module.__dict__)
        result.append(module)
    return result


def run(contract, sources, lab, checkpoints, read_source):
    parity, correctness, prefix = modules(sources)
    require(contract["model_files"] == {name: {"bytes": size, "sha256": sha}
                                       for name, (size, sha) in parity.MODEL_FILES.items()})
    parity.verify_sources(read_source, contract["paths"][-1], correctness)
    def model_source(path, size):
        require(path in {"/model/" + name for name in parity.MODEL_FILES})
        return read_source("/opt/nib" + path, size)
    parity.verify_model(model_source)
    body = correctness.strict_json(sources["agent-prefix.json"])
    prefix.validate(body)
    state = lab.install_lab({"paths": contract["paths"], "stdlib_roots": contract["paths"][:2],
                            "wrapper": "/opt/nib/python", "launcher": "/opt/nib/launch.py"},
                           lab, checkpoint=checkpoints.bootstrap)
    checkpoints.emit(11)
    try:
        from vllm.tokenizers.deepseek_v41 import DeepseekV41Tokenizer
        from vllm.tokenizers.deepseek_v41_encoding import USER_SP_TOKEN
        from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionToolsParam
        checkpoints.emit(12)
        tokenizer = DeepseekV41Tokenizer.from_pretrained("/opt/nib/model", local_files_only=True, trust_remote_code=False)
        checkpoints.emit(13)
        row = measure(body, tokenizer, ChatCompletionToolsParam, parity, correctness, prefix,
                      json.loads(model_source("/model/tokenizer.json", parity.MODEL_FILES["tokenizer.json"][0])), USER_SP_TOKEN)
        checkpoints.emit(14)
        parity.verify_model(model_source)
        parity.verify_sources(read_source, contract["paths"][-1], correctness)
        checkpoints.emit(15)
        state["check"]()
        checkpoints.emit(16)
        return row
    finally:
        state["check"]()
