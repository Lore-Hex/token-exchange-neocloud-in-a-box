# SPDX-License-Identifier: Apache-2.0
"""Closed, source-backed experiments; never a production performance claim."""

import hashlib
import json

NAMES = ("batch8-eager", "batch8-graphs", "batch8-dspark")
REVISION = "5df63a7203cdb9328d824a27b682e77c7b9928be"
# https://github.com/vllm-project/vllm/tree/5df63a7203cdb9328d824a27b682e77c7b9928be
# CompilationConfig, SchedulerConfig and SpeculativeConfig, respectively.
SOURCES = {
    "vllm/config/compilation.py": "d8a8086166e3f344b75d98d8350a77592f7b6c145319b3a9f0b9c8cb36bc39cb",
    "vllm/config/scheduler.py": "2fb86b1572a4925be59f304d13b4f6ec03d496b67a7c4abbb976ecda2232839a",
    "vllm/config/speculative.py": "ce135651a7a0343ff526f72e9545932617378161abd03135d67eda6de89c617c",
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
            and not any(key in contract for key in ("tokenizer_parity", "tokenizer_materialization",
                        "runtime_observation", "stream_cohort", "provider_conformance")))
    return name


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
    replace("--max-num-seqs", "1", "8")
    if name != "batch8-eager":
        result.remove("--enforce-eager")
        # Full decode graphs isolate graph replay from prefill compilation.
        # 8 requests * (1 target + 5 draft tokens) = 48 captured rows for DSpark.
        sizes = [6, 12, 24, 48] if name == "batch8-dspark" else [1, 2, 4, 8]
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
