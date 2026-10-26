# SPDX-License-Identifier: Apache-2.0
"""Closed, source-backed experiments; never a production performance claim."""

import hashlib
import json

BATCH_NAMES = ("batch8-eager", "batch8-graphs", "batch8-dspark")
SCALING_PROFILE = "batch32-graphs"
LONG_PROFILE = "long128k-eager"
LONG_GRAPHS_PROFILE = "long128k-graphs"
LONG_PROFILES = (LONG_PROFILE, LONG_GRAPHS_PROFILE)
EAGER_CONTEXTS = {"tau8k-eager": 8192, "quality32k-eager": 32768, LONG_PROFILE: 131072}
CONTEXTS = {**EAGER_CONTEXTS, LONG_GRAPHS_PROFILE: 131072}
NAMES = (*BATCH_NAMES, *CONTEXTS, SCALING_PROFILE)
REVISION = "5df63a7203cdb9328d824a27b682e77c7b9928be"
# https://github.com/vllm-project/vllm/tree/5df63a7203cdb9328d824a27b682e77c7b9928be
# Configuration and graph-candidate implementation used by these profiles.
SOURCES = {
    "vllm/config/compilation.py": "d8a8086166e3f344b75d98d8350a77592f7b6c145319b3a9f0b9c8cb36bc39cb",
    "vllm/config/scheduler.py": "2fb86b1572a4925be59f304d13b4f6ec03d496b67a7c4abbb976ecda2232839a",
    "vllm/config/speculative.py": "ce135651a7a0343ff526f72e9545932617378161abd03135d67eda6de89c617c",
    "vllm/v1/worker/gpu/cudagraph_utils.py": "ef2aae2eb3ec5ea97d4732f65c886b7a4688247721aacf23e536485e0ffc05ab",
    "vllm/config/cache.py": "d37db21e7a6a1b477a580b54d90011d8708c4e7f6e3903221432d9a39830559b",
    "vllm/entrypoints/launchers/cli_args.py": "76221e2c10a0c5cffb7820a25c176a436777eb2008737cc5b1effaa014ebad28",
    "vllm/engine/arg_utils.py": "c10281277fd9a90dc8b22670db4f1af53708c5caa2aefee7aa861fa579329219",
    "vllm/v1/core/kv_cache_utils.py": "af4164bca819f1cdc416077522a7019936fd38a523a482283eb91b08dae0e57a",
    "vllm/entrypoints/openai/chat_completion/serving.py": "5e619c0a6866758a247c76e7b8c472f6e008ddbc720f7efe47cc6776cb986d4f",
    "vllm/entrypoints/openai/chat_completion/protocol.py": "8744d5e2004b2d376e34cf17de51c919bca6d81b0cc8392da138db1ef05a7c61",
    "vllm/entrypoints/generate/base/serving.py": "74948d88c9e5edfed6993f749644f3da41719b8a670bb675fd837753b40b89fe",
}


def require(value):
    if not value:
        raise ValueError("DeepSeek execution profile rejected")


def select(name):
    require(type(name) is str and name in NAMES)
    return name


def enabled(contract):
    """No implicit opt-in, original-weight substitution or confidential crossover."""
    if "execution_profile" not in contract:
        require("tuning.py" not in contract.get("sources", {}))
        return None
    name = select(contract["execution_profile"])
    require(contract.get("operation") == "local-correctness"
            and contract.get("gpu_family") == "B300"
            and contract.get("gpu_security") == "nonconfidential"
            and type(contract.get("data_parallel_size")) is int
            and contract["data_parallel_size"] == 2
            and type(contract.get("model_profile")) is dict
            and contract["model_profile"].get("name") == "uncensored"
            and "tuning.py" in contract.get("sources", {})
            and "provider_conformance" not in contract)
    measurement_keys = ("tokenizer_parity", "tokenizer_materialization", "runtime_observation", "stream_cohort")
    if name in CONTEXTS:
        require(contract.get("tokenizer_parity") is True and contract.get("runtime_observation") is True
                and type(contract.get("tokenizer_materialization")) is dict
                and "stream_cohort" not in contract)
    elif any(key in contract for key in measurement_keys):
        require(contract.get("tokenizer_parity") is True and contract.get("runtime_observation") is True
                and type(contract.get("tokenizer_materialization")) is dict
                and type(contract.get("stream_cohort")) is dict)
    if name == SCALING_PROFILE:
        require(contract.get("stream_cohort") == {"schema": "nib.owned-stream-input/v1",
                "profile": "chat-2k-512-c16-32-64-isolated-900s"})
    return name


def context_limit(contract):
    return CONTEXTS.get(enabled(contract), 4096)


def arguments(baseline, name):
    """Transform a canonical lab argv; never append duplicate last-wins flags."""
    select(name)
    result = list(baseline)

    def replace(flag, expected, value):
        require(result.count(flag) == 1)
        index = result.index(flag) + 1
        require(index < len(result) and result[index] == expected)
        result[index] = value

    require("--speculative-config" not in result and result.count("--enforce-eager") == 1)
    if name in CONTEXTS:
        # Pinned SchedulerConfig.verify_max_model_len requires a batch token
        # budget >= context when chunked prefill is disabled. Keep one sequence.
        replace("--max-num-seqs", "1", "1")
        replace("--max-model-len", "4096", str(CONTEXTS[name]))
        replace("--max-num-batched-tokens", "4096", "4096" if name in LONG_PROFILES else str(CONTEXTS[name]))
        if name in LONG_PROFILES:
            # Pinned arg_utils, CacheConfig and FrontendArgs: bounded chunks,
            # explicit APC and real cached-token usage, not an estimated hit.
            for flag in ("--enable-chunked-prefill", "--enable-prefix-caching", "--enable-prompt-tokens-details"):
                require(flag not in result and flag.replace("--enable-", "--no-enable-") not in result)
                result.append(flag)
        if name != LONG_GRAPHS_PROFILE:
            return result
    else:
        replace("--max-num-seqs", "1", "32" if name == SCALING_PROFILE else "8")
        if name == SCALING_PROFILE:
            require("--enable-prompt-tokens-details" not in result)
            result.append("--enable-prompt-tokens-details")
    if name != "batch8-eager":
        result.remove("--enforce-eager")
        # Capture decode-only graphs; prefill runs outside CUDA graphs.
        # Removing eager also restores pinned compiler/JIT defaults.
        # DSpark shares this list: target needs 8*6 rows, drafter needs 8*5.
        # Without 40, drafter rounds 48 to 50 and drops it above its 40-row cap.
        sizes = ([1, 2, 4, 8, 16, 32] if name == SCALING_PROFILE else [1] if name == LONG_GRAPHS_PROFILE else
                 [6, 12, 24, 40, 48] if name == "batch8-dspark" else [1, 2, 4, 8])
        compilation = {"pass_config": {"fuse_allreduce_rms": False},
                       "cudagraph_mode": "FULL_DECODE_ONLY", "cudagraph_capture_sizes": sizes}
        replace("--compilation-config", '{"pass_config":{"fuse_allreduce_rms":false}}',
                json.dumps(compilation, separators=(",", ":")))
    if name == "batch8-dspark":
        # Same-checkpoint draft weights; real block rejection, never synthetic
        # acceptance. Recipe: e28104e0, features.spec_decoding (NVIDIA).
        speculative = {"method": "dspark", "num_speculative_tokens": 5,
                       "draft_sample_method": "probabilistic", "rejection_sample_method": "block",
                       "enable_adaptive_verification": True}
        result += ["--speculative-config", json.dumps(speculative, separators=(",", ":"))]
    return result


def verify_sources(read_source, root):
    for path, expected in SOURCES.items():
        require(hashlib.sha256(read_source(root + "/" + path, 256 << 10)).hexdigest() == expected)
