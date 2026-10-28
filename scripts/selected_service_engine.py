# SPDX-License-Identifier: Apache-2.0
"""Closed checkpoint-specific entries appended to the sealed lab helpers."""

import ast

SERVICE_MODEL = "dealignai/deepseek-v4.1-flash-1017-uncensored-fp8"
ORIGINAL_MODEL = "deepseek/deepseek-v4.1-flash-1017"
EAGER_PROFILE, GRAPHS_PROFILE = "tau8k-eager", "batch8-graphs"
QUALITY_PROFILE = "quality32k-eager"
LONG_PROFILE = "long128k-eager"
LONG_GRAPHS_PROFILE = "long128k-graphs"
LONG_PROFILES = (LONG_PROFILE, LONG_GRAPHS_PROFILE)
MILLION_PROFILE = "long1m-graphs"
ORIGINAL_MILLION_PROFILE = "original-long1m-graphs"
MILLION_PROFILES = (MILLION_PROFILE, ORIGINAL_MILLION_PROFILE)
SELECTED_LONG_CONTEXT_PROFILES = (*LONG_PROFILES, MILLION_PROFILE)
LONG_CONTEXT_PROFILES = (*SELECTED_LONG_CONTEXT_PROFILES, ORIGINAL_MILLION_PROFILE)
BOUND_PROFILES = (GRAPHS_PROFILE, QUALITY_PROFILE, *LONG_CONTEXT_PROFILES)


def execution_limits(profile):
    if type(profile) is not str or profile not in (EAGER_PROFILE, *BOUND_PROFILES):
        raise ValueError("selected engine profile rejected")
    if profile == QUALITY_PROFILE:
        return {"profile": profile, "context_tokens": 32768,
                "max_output_tokens": 2048, "max_inflight": 1,
                "guard_profile": "selected-deepseek-quality"}
    if profile in LONG_CONTEXT_PROFILES:
        million = profile in MILLION_PROFILES
        return {"profile": profile, "context_tokens": 1048576 if million else 131072,
                "max_output_tokens": 393216 if million else 16384, "max_inflight": 1,
                "guard_profile": "original-deepseek-million" if profile == ORIGINAL_MILLION_PROFILE
                    else "selected-deepseek-million" if million else "selected-deepseek-long"}
    graphs = profile == GRAPHS_PROFILE
    return {"profile": profile, "context_tokens": 4096 if graphs else 8192,
            "max_output_tokens": 256, "max_inflight": 8 if graphs else 1,
            "guard_profile": "selected-deepseek-graphs" if graphs else "selected-deepseek"}


def model_profile_name(profile):
    execution_limits(profile)
    return "original" if profile == ORIGINAL_MILLION_PROFILE else "uncensored"


def service_model(profile):
    return ORIGINAL_MODEL if model_profile_name(profile) == "original" else SERVICE_MODEL


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
          and contract.get("model_profile", {}).get("name") == model_profile_name(limits["profile"])
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
    if limits["profile"] in (QUALITY_PROFILE, *LONG_CONTEXT_PROFILES):
        check(contract.get("tokenizer_parity") is True and contract.get("runtime_observation") is True
              and type(contract.get("tokenizer_materialization")) is dict)
    result = list(contract["arguments"])
    model = service_model(limits["profile"])
    expected = {"--served-model-name": model.split("/", 1)[1],
                "--host": "127.0.0.1", "--port": "8000", "--max-model-len": str(limits["context_tokens"]),
                "--max-num-seqs": str(limits["max_inflight"]),
                "--max-num-batched-tokens": "4096" if limits["profile"] in LONG_CONTEXT_PROFILES else str(limits["context_tokens"]),
                "--data-parallel-size": "2", "--tensor-parallel-size": "4"}
    if graphs or limits["profile"] in (LONG_GRAPHS_PROFILE, *MILLION_PROFILES):
        check("--enforce-eager" not in result and "--speculative-config" not in result)
        # Exact deepseek_tuning.arguments profile, source-pinned to vLLM 5df63a7.
        sizes = "[1,2,4,8]" if graphs else "[1]"
        expected["--compilation-config"] = ('{"pass_config":{"fuse_allreduce_rms":false},'
            '"cudagraph_mode":"FULL_DECODE_ONLY","cudagraph_capture_sizes":' + sizes + '}')
    elif limits["profile"] in (QUALITY_PROFILE, LONG_PROFILE):
        check(result.count("--enforce-eager") == 1 and "--speculative-config" not in result)
        expected["--compilation-config"] = '{"pass_config":{"fuse_allreduce_rms":false}}'
    if limits["profile"] in LONG_CONTEXT_PROFILES:
        for flag in ("--enable-chunked-prefill", "--enable-prefix-caching", "--enable-prompt-tokens-details"):
            check(result.count(flag) == 1 and flag.replace("--enable-", "--no-enable-") not in result)
    for flag, value in expected.items():
        check(result.count(flag) == 1)
        index = result.index(flag) + 1
        check(index < len(result) and result[index] == value)
    # vLLM 5df63a7 ModelConfig.served_model_name: select at launch, never rewrite
    # request/response bodies. All other reviewed lab arguments stay identical.
    result[result.index("--served-model-name") + 1] = model
    return result


def selected_engine_entry():
    # The names below are the source-bound lab helpers in the composed launcher.
    require(len(sys.argv) == 3 and sys.argv[2] in ("selected-service-engine", "selected-combined-engine"))
    contract, _, modules, application, _ = lab_context(sys.argv[1])
    arguments = canonical_arguments(contract)
    if sys.argv[2] == "selected-combined-engine":
        require(contract["execution_profile"] == LONG_GRAPHS_PROFILE)
        tuning = tuning_module(contract)
        tuning.verify_sources(read_source, contract["paths"][-1])
        plan = json.loads(read_source(ROOT + "/launch-plan.json"))
        baseline = lab_args(plan, 2, model=contract["model_profile"]["served_model_name"])
        require(tuning.arguments(baseline, LONG_GRAPHS_PROFILE) == contract["arguments"])
        arguments = tuning.combined_arguments(baseline)
        arguments[arguments.index("--served-model-name") + 1] = SERVICE_MODEL
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
        if len(sys.argv) == 3 and sys.argv[2] in ("selected-service-engine", "selected-combined-engine"):
            selected_engine_entry()
        else:
            lab_entry()
    except BaseException:
        sys.exit(1)
'''
