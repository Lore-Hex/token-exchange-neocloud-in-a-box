# SPDX-License-Identifier: Apache-2.0
"""Pinned GGUF identity recipes, not measurements or a receipt producer."""

import engine_tokenizer_parity as probes
import tau_local_llama as llama
import tau_preflight as tau

PINS = "scripts/tau_cpu_support_models.json"
HELPER = "scripts/tau_cpu_parity.cpp"


def identity(model, deadline):
    tau.require(type(model) is str and model in llama.MODELS)
    pins = tau.json_value(tau.regular(tau.ROOT / PINS, 16384, deadline))
    tau.require(pins["schema"] == "nib.tau-cpu-support-candidates/v1"
                and pins["engine"]["revision"] == llama.REVISION
                and set(pins["models"]) == llama.MODELS)
    selected = pins["models"][model]
    gguf = {key: selected[key] for key in ("filename", "bytes", "sha256")}
    inventory = {"schema": "nib.tau-gguf-inventory/v1", "files": [gguf]}
    # These are identities of an entire pinned GGUF and the interpretation used
    # for its embedded metadata, not hashes of nonexistent HF tokenizer files.
    tokenizer = {"schema": "nib.tau-gguf-tokenizer-identity/v1", "gguf_sha256": gguf["sha256"],
                 "engine_revision": llama.REVISION, "engine_archive_sha256": pins["engine"]["archive_sha256"]}
    framing = {"schema": "nib.tau-gguf-framing-identity/v1", "tokenizer": tokenizer,
               "template_source": "embedded-gguf", "template_override": False,
               "use_jinja": True, "add_generation_prompt": True, "continue_final_message": False,
               "tool_choice": "auto", "parallel_tool_calls": False, "reasoning_format": "deepseek",
               "enable_thinking": False, "add_special": True, "parse_special": True,
               "context_tokens": 4096,
               "helper_source_sha256": tau.digest(tau.regular(tau.ROOT / HELPER, 65536, deadline))}
    return {"revision": selected["revision"], "gguf": gguf,
            "model_files_sha256": tau.digest(tau.encoded(inventory)),
            "tokenizer_sha256": tau.digest(tau.encoded(tokenizer)),
            "framing_sha256": tau.digest(tau.encoded(framing))}


def case_hashes():
    return {name: probes.digest(probe) for name, probe in zip(probes.IDS, probes.probes())}
