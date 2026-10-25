# SPDX-License-Identifier: Apache-2.0
"""Offline pinned V4.1 rendering and native BPE. No model or network import."""

import ast
import copy
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import platform
import tempfile
import types
import zipfile

import engine_tokenizer_parity as parity
import tau_trial_worker as worker

require = worker.require
ENCODER = "vllm/tokenizers/deepseek_v41_encoding.py"
WRAPPER = "vllm/tokenizers/deepseek_v41.py"
SOURCES = {ENCODER: "477f22ac250b43c12bd19bb3c93f934d6637b06410b8cb7ccba0862d1049dee8",
           WRAPPER: "fff82623f5676021bd93766e8ec7106ff0384181b83ab4f2aa3a492ffa0b3f1f"}
# Official PyPI 0.23.3 JSON, checked 2026-10-25. Input wheels are downloaded
# separately; this module never installs packages or consults site-packages.
# https://pypi.org/pypi/tokenizers/0.23.3/json
WHEELS = {
    ("Darwin", "arm64"): (3101381, "68649e97d5b43c44c031d8d848874a6eecae8f8fe40ea989aa777a5a83aca716"),
    ("Linux", "x86_64"): (3396193, "376851d22bcf9d650a5c3090bb83e6cf9e895fbf0595369fa4cd43c1f69b5f87"),
}
# Byte-identical token IDs from the four previously committed runtime-parity
# observations, replayed offline; not a new live or full prompt parity claim.
PARITY = (
    (12, "9d6bde6f28006680e5d37e06d816356d782004f1e8dbabf46d42e81b088afc16"),
    (16, "c7b86dad075d1d51e4bf9aec664d9ce392e5d04a63babe717ab9354ec0b56032"),
    (275, "6ddfa5e6dbf2f45b8efc1e2ebda65f1f79c39b1a4ec8a7fce8e77fc5a0add7d5"),
    (332, "4e5eb88296779609b1ef81a335894e3ef5c284a94a8ceb1607421414c43c9cbe"),
)


def verified(path, size, sha):
    raw = worker.read(Path(path), size)
    require(len(raw) == size and hashlib.sha256(raw).hexdigest() == sha)
    return raw


class Counter:
    def __init__(self, source_root, model_root, wheel):
        self.temporary = None
        self.native = self.tokenizer = None
        size, sha = WHEELS[(platform.system(), platform.machine())]
        raw = verified(wheel, size, sha)
        models = {name: verified(Path(model_root) / name, size, digest)
                  for name, (size, digest) in parity.MODEL_FILES.items()}
        sources = {}
        for name, digest in SOURCES.items():
            data = worker.read(Path(source_root) / name, 128 << 10)
            require(hashlib.sha256(data).hexdigest() == digest)
            sources[name] = data
        encoding = types.ModuleType("pinned_v41_encoding")
        exec(compile(sources[ENCODER], ENCODER, "exec"), encoding.__dict__)
        selected = [node for node in ast.parse(sources[WRAPPER]).body
                    if isinstance(node, ast.FunctionDef) and node.name in
                    ("_normalize_messages", "get_deepseek_v41_tokenizer")]
        require(len(selected) == 2)
        namespace = {**encoding.__dict__, "copy": copy, "HfTokenizer": object,
                     "ChatCompletionMessageParam": dict}
        exec(compile(ast.Module(body=selected, type_ignores=[]), WRAPPER, "exec"), namespace)
        try:
            self.temporary = tempfile.TemporaryDirectory(prefix="nib-tau-bpe-")
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                members = archive.infolist()
                require(len(members) <= 256)
                native = [row for row in members if row.filename == "tokenizers/tokenizers.abi3.so"]
                require(len(native) == 1 and 0 < native[0].file_size <= 32 << 20)
                library = Path(self.temporary.name) / "tokenizers.abi3.so"
                descriptor = os.open(library, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o500)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(archive.read(native[0]))
            spec = importlib.util.spec_from_file_location("tokenizers", library)
            self.native = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.native)
            backend = self.native.Tokenizer.from_str(models["tokenizer.json"].decode("utf-8"))
            backend.no_truncation()
            backend.no_padding()
            # TokenizersBackend applies the pinned config's three special
            # AddedToken declarations before encode(add_special_tokens=False).
            config = worker.decode(models["tokenizer_config.json"])
            for name in ("bos_token", "eos_token", "pad_token"):
                token = dict(config[name])
                require(token.pop("__type") == "AddedToken")
                backend.add_special_tokens([self.native.AddedToken(**token, special=True)])
            class Backend:
                def get_added_vocab(self):
                    return backend.get_vocab(with_added_tokens=True)

                def encode(self, prompt, *, add_special_tokens):
                    require(add_special_tokens is False)
                    return backend.encode(prompt, add_special_tokens=False).ids
            self.tokenizer = namespace["get_deepseek_v41_tokenizer"](Backend())
            self.binding = worker.digest(worker.encoded({"sources": SOURCES, "model_files": parity.MODEL_FILES,
                                                        "wheel_sha256": sha}))
            for probe, (count, digest) in zip(parity.probes(), PARITY):
                require(parity.tokens(self.tokens(probe)) == {"count": count, "tokens_sha256": digest})
        except BaseException:
            self.close()
            raise

    def tokens(self, body):
        require(self.tokenizer is not None)
        # Pinned vLLM FunctionDefinition.model_dump field order/defaults. This
        # is rendering only: never rewrite the actual wire tools or arguments.
        tools = None if body.get("tools") is None else [
            {"type": "function", "function": {"name": item["function"]["name"],
             "description": item["function"].get("description"),
             "parameters": item["function"]["parameters"]}} for item in body["tools"]]
        ids = self.tokenizer.apply_chat_template(body["messages"], tools=tools,
            tokenize=True, add_generation_prompt=True, continue_final_message=False,
            reasoning_effort="none", enable_thinking=False)
        require(type(ids) is list and 0 < len(ids) <= 8192
                and all(type(value) is int and 0 <= value < 2**31 for value in ids))
        return ids

    def count(self, body):
        return len(self.tokens(body))

    def close(self):
        self.tokenizer = self.native = None
        if self.temporary is not None:
            self.temporary.cleanup()
            self.temporary = None
