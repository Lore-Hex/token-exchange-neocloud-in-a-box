# SPDX-License-Identifier: Apache-2.0
"""Fixed CPU-only request/parser/normalizer diagnostic; no BPE or serving."""

import asyncio
import copy
import hashlib
import types

MODE = "tokenizer-continuation"
SOURCES = {
    "vllm/renderers/chat_utils.py": "9a5d1db66e15285303987073f0c69c3ef3b4745e8c4ab71b9b063f1579fb2061",
    "vllm/tokenizers/deepseek_v41.py": "fff82623f5676021bd93766e8ec7106ff0384181b83ab4f2aa3a492ffa0b3f1f",
    "vllm/tokenizers/deepseek_v41_encoding.py": "477f22ac250b43c12bd19bb3c93f934d6637b06410b8cb7ccba0862d1049dee8",
    "vllm/entrypoints/serve/tokenize/protocol.py": "e887a771afbc7ae3c709691d31e34da8f3d63a627eab55cc3d97d05625901c07",
    "vllm/entrypoints/openai/chat_completion/protocol.py": "8744d5e2004b2d376e34cf17de51c919bca6d81b0cc8392da138db1ef05a7c61",
    "vllm/renderers/deepseek_v4.py": "f7175f9980550e5ccfcc94fcebfdeee113eee66efc7cd1ec539d5b2af1f0d8cd",
}


def require(value):
    if not value:
        raise ValueError("tokenizer diagnostic rejected")


def verify_sources(read_source, selected):
    for path, expected in SOURCES.items():
        require(hashlib.sha256(read_source(selected + "/" + path)).hexdigest() == expected)


def tokenize_payloads(parity, model):
    return tuple({"model": model, **probe, "add_generation_prompt": True, "continue_final_message": False,
                  "add_special_tokens": False, "chat_template_kwargs": dict(parity.TEMPLATE),
                  "return_token_strs": False} for probe in parity.probes())


def exercise(probes, request_type, normalize, encode, parse, parse_async, checkpoints, check, chat_request_type):
    """Unmodified requests first; controls never replace the final failing input."""
    # Text-only parser accesses this one field. No model, media, device or
    # engine configuration is constructed. Unexpected accesses fail closed.
    config = types.SimpleNamespace(enable_prompt_embeds=False)
    requests = [request_type(**copy.deepcopy(probe)) for probe in probes]
    checkpoints.emit(12)
    for request in requests[:3]:
        normalize(request.messages)
    raw = copy.deepcopy(probes[3]["messages"])
    plain = normalize(raw)
    dictionary = copy.deepcopy(plain)
    dictionary[1]["tool_calls"][0]["function"]["arguments"] = {"key": "blue"}
    require(encode(plain, thinking_mode="chat") == encode(dictionary, thinking_mode="chat"))
    chat = copy.deepcopy(probes[3])
    chat.pop("return_token_strs", None)
    chat.update(max_completion_tokens=1, temperature=0, reasoning_effort="none", stream=False, n=1,
                tool_choice="none", parallel_tool_calls=False)
    chat_request = chat_request_type(**chat)
    require(type(chat_request.messages[1]["tool_calls"]) is list
            and len(chat_request.messages[1]["tool_calls"]) == 1)
    normalize(chat_request.messages)
    calls = requests[3].messages[1]["tool_calls"]
    # Observe, do not materialize: consuming this iterator would change the
    # original input. The parsed conversation is checked separately below.
    require(type(calls) is not list and iter(calls) is calls)
    checkpoints.emit(13)
    for probe in probes:
        sync_request = request_type(**copy.deepcopy(probe))
        async_request = request_type(**copy.deepcopy(probe))
        conversation, data, ids = parse(sync_request.messages, config, content_format="openai")
        other, async_data, async_ids = asyncio.run(
            parse_async(async_request.messages, config, content_format="openai"))
        require(data is ids is async_data is async_ids is None)
        require(normalize(conversation) == normalize(other))
        if probe is probes[3]:
            require(type(conversation[1]["tool_calls"]) is list
                    and len(conversation[1]["tool_calls"]) == 1
                    and conversation[1]["tool_calls"][0]["function"]["arguments"] == {"key": "blue"})
    checkpoints.emit(14)
    # This is the same async generic parser used by DeepseekV4Renderer.
    # It consumes the tool iterator into a *different* conversation; the
    # renderer passes the original request.messages to the tokenizer instead.
    original = requests[3].messages
    conversation, data, ids = asyncio.run(parse_async(original, config, content_format="openai"))
    require(data is ids is None and len(conversation[1]["tool_calls"]) == 1)
    check()
    checkpoints.emit(15)
    # Preserve the real exception and traceback for the existing fixed packet.
    # No exception text, request values, rendered strings or token IDs escape.
    normalize(original)
    checkpoints.emit(16)


def run(contract, sources, lab, checkpoints, read_source):
    verify_sources(read_source, contract["paths"][-1])
    options = lab.tokenizer_adapter_options(contract, require)
    state = lab.install_lab({"paths": contract["paths"], "stdlib_roots": contract["paths"][:2],
                             "wrapper": "/opt/nib/python", "launcher": "/opt/nib/launch.py", **options},
                            lab, checkpoint=checkpoints.bootstrap)
    checkpoints.emit(11)
    from vllm.entrypoints.serve.tokenize.protocol import TokenizeChatRequest
    from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest
    from vllm.renderers.chat_utils import parse_chat_messages, parse_chat_messages_async
    from vllm.tokenizers.deepseek_v41 import _normalize_messages
    from vllm.tokenizers.deepseek_v41_encoding import encode_messages

    parity = types.ModuleType("diagnostic_parity")
    exec(compile(sources["tokenizer-parity.py"], "/opt/nib/tokenizer-parity.py", "exec"), parity.__dict__)
    probes = tokenize_payloads(parity, lab.MODEL)
    require(len(probes) == 4)
    try:
        if options:
            from pydantic_core import ValidationError
            exercise_fixed(probes, TokenizeChatRequest, _normalize_messages, encode_messages,
                           parse_chat_messages, parse_chat_messages_async, checkpoints,
                           state["check"], ValidationError)
            require(state["tokenizer_adapter_verified"]() == contract["tokenizer_materialization"])
            checkpoints.emit(16)
        else:
            exercise(probes, TokenizeChatRequest, _normalize_messages, encode_messages,
                     parse_chat_messages, parse_chat_messages_async, checkpoints, state["check"], ChatCompletionRequest)
    finally:
        state["check"]()
        verify_sources(read_source, contract["paths"][-1])


def exercise_fixed(probes, request_type, normalize, encode, parse, parse_async, checkpoints, check, validation_error):
    """Same four client payloads; schema/iteration controls execute in real Pydantic."""
    config = types.SimpleNamespace(enable_prompt_embeds=False)
    requests = [request_type(**copy.deepcopy(probe)) for probe in probes]
    checkpoints.emit(12)
    original = copy.deepcopy(probes)
    calls = requests[3].messages[1]["tool_calls"]
    require(type(calls) is list and len(calls) == 1 and requests[3].messages[1]["content"] is None)
    for _ in range(2):
        require(requests[3]._materialize_tool_calls_after() is requests[3]
                and requests[3].messages[1]["tool_calls"] is calls)
    # A later invalid iterator element must raise the real validation error,
    # not be skipped, deferred until parse, or hidden by a guard ValueError.
    visited = []
    def invalid_calls():
        visited.append(1)
        yield copy.deepcopy(probes[3]["messages"][1]["tool_calls"][0])
        visited.append(2)
        yield {"type": "function", "id": "invalid_missing_function"}
    invalid = copy.deepcopy(probes[3])
    invalid["messages"][1]["tool_calls"] = invalid_calls()
    try:
        request_type(**invalid)
    except validation_error:
        require(visited == [1, 2])
    else:
        require(False)
    checkpoints.emit(13)
    for probe, request in zip(probes, requests):
        expected = encode(normalize(copy.deepcopy(probe["messages"])), thinking_mode="chat")
        require(encode(normalize(request.messages), thinking_mode="chat") == expected)
        other = request_type(**copy.deepcopy(probe))
        sync, data, ids = parse(request.messages, config, content_format="openai")
        asynchronous, async_data, async_ids = asyncio.run(parse_async(other.messages, config, content_format="openai"))
        require(data is ids is async_data is async_ids is None and normalize(sync) == normalize(asynchronous))
        for messages in (request.messages, other.messages):
            require(encode(normalize(messages), thinking_mode="chat") == expected)
        if probe is probes[3]:
            require(len(request.messages[1]["tool_calls"]) == len(other.messages[1]["tool_calls"]) == 1
                    and request.messages[1]["content"] is other.messages[1]["content"] is None)
    checkpoints.emit(14)
    require(probes == original)
    check()
    checkpoints.emit(15)
