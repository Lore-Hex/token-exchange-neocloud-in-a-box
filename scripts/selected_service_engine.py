# SPDX-License-Identifier: Apache-2.0
"""Selected-only entry appended to the unchanged sealed lab helpers."""

import ast

SERVICE_MODEL = "dealignai/deepseek-v4.1-flash-1017-uncensored-fp8"
EAGER_PROFILE, GRAPHS_PROFILE = "tau8k-eager", "batch8-graphs"
QUALITY_PROFILE = "quality32k-eager"
BOUND_PROFILES = (GRAPHS_PROFILE, QUALITY_PROFILE)


def execution_limits(profile):
    if type(profile) is not str or profile not in (EAGER_PROFILE, *BOUND_PROFILES):
        raise ValueError("selected engine profile rejected")
    if profile == QUALITY_PROFILE:
        return {"profile": profile, "context_tokens": 32768,
                "max_output_tokens": 2048, "max_inflight": 1,
                "guard_profile": "selected-deepseek-quality"}
    graphs = profile == GRAPHS_PROFILE
    return {"profile": profile, "context_tokens": 4096 if graphs else 8192,
            "max_output_tokens": 256, "max_inflight": 8 if graphs else 1,
            "guard_profile": "selected-deepseek-graphs" if graphs else "selected-deepseek"}


def canonical_arguments(contract):
    def check(value):
        if not value:
            raise ValueError("selected engine arguments rejected")
    limits = execution_limits(contract.get("execution_profile"))
    graphs = limits["profile"] == GRAPHS_PROFILE
    check(contract.get("operation") == "local-correctness"
          and contract.get("gpu_family") == "B300"
          and contract.get("gpu_security") == "nonconfidential"
          and type(contract.get("data_parallel_size")) is int
          and contract["data_parallel_size"] == 2
          and contract.get("model_profile", {}).get("name") == "uncensored"
          and not {"provider_conformance", "tau_owner"} & set(contract))
    if graphs:
        # Preserve the authenticated C8 lab metadata. selected_engine_entry
        # calls run_engine only, never the lab's cohort or tokenizer requests.
        check(contract.get("stream_cohort") == {"schema": "nib.owned-stream-input/v1",
              "profile": "chat-2k-512-c8-600s"} and contract.get("tokenizer_parity") is True
              and contract.get("runtime_observation") is True
              and type(contract.get("tokenizer_materialization")) is dict)
    else:
        check("stream_cohort" not in contract)
    if limits["profile"] == QUALITY_PROFILE:
        check(contract.get("tokenizer_parity") is True and contract.get("runtime_observation") is True
              and type(contract.get("tokenizer_materialization")) is dict)
    result = list(contract["arguments"])
    expected = {"--served-model-name": SERVICE_MODEL.split("/", 1)[1],
                "--host": "127.0.0.1", "--port": "8000", "--max-model-len": str(limits["context_tokens"]),
                "--max-num-seqs": str(limits["max_inflight"]),
                "--max-num-batched-tokens": str(limits["context_tokens"]),
                "--data-parallel-size": "2", "--tensor-parallel-size": "4"}
    if graphs:
        check("--enforce-eager" not in result and "--speculative-config" not in result)
        # Exact deepseek_tuning.arguments profile, source-pinned to vLLM 5df63a7.
        expected["--compilation-config"] = ('{"pass_config":{"fuse_allreduce_rms":false},'
            '"cudagraph_mode":"FULL_DECODE_ONLY","cudagraph_capture_sizes":[1,2,4,8]}')
    elif limits["profile"] == QUALITY_PROFILE:
        check(result.count("--enforce-eager") == 1 and "--speculative-config" not in result)
        expected["--compilation-config"] = '{"pass_config":{"fuse_allreduce_rms":false}}'
    for flag, value in expected.items():
        check(result.count(flag) == 1)
        index = result.index(flag) + 1
        check(index < len(result) and result[index] == value)
    # vLLM 5df63a7 ModelConfig.served_model_name: select at launch, never rewrite
    # request/response bodies. All other reviewed lab arguments stay identical.
    result[result.index("--served-model-name") + 1] = SERVICE_MODEL
    return result


def selected_engine_entry():
    # The names below are the source-bound lab helpers in the composed launcher.
    require(len(sys.argv) == 3 and sys.argv[2] == "selected-service-engine")
    contract, _, modules, application, _ = lab_context(sys.argv[1])
    arguments = canonical_arguments(contract)
    recheck_sources(modules[0], application, contract["paths"])
    initialize_cache()

    class QuietCheckpoints:
        def bootstrap(self, stage, index):
            pass

        def emit(self, identifier):
            pass

        def close(self):
            pass

    run_engine({**contract, "arguments": arguments}, QuietCheckpoints())


def compose_launcher(base, adapter):
    """Offline construction only; keep all original definitions/worker dispatch.

    The prepared lab.json must hash the resulting launch.py and recompute its
    python wrapper. The original lab bundle/receipt is never overwritten.
    """
    tree = ast.parse(base)
    last = tree.body[-1]
    expected = ast.parse('if __name__ == "__main__": pass').body[0].test
    if not isinstance(last, ast.If) or ast.dump(last.test) != ast.dump(expected):
        raise ValueError("selected launcher entry rejected")
    prefix = b"".join(base.splitlines(keepends=True)[:last.lineno - 1])
    return prefix + b"\n" + adapter + b'''\n
if __name__ == "__main__":
    try:
        if len(sys.argv) == 3 and sys.argv[2] == "selected-service-engine":
            selected_engine_entry()
        else:
            lab_entry()
    except BaseException:
        sys.exit(1)
'''
