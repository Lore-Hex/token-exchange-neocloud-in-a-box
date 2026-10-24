# SPDX-License-Identifier: Apache-2.0
"""Selected-only entry appended to the unchanged sealed lab helpers."""

import ast

SERVICE_MODEL = "dealignai/deepseek-v4.1-flash-1017-uncensored-fp8"


def canonical_arguments(contract):
    def check(value):
        if not value:
            raise ValueError("selected engine arguments rejected")
    check(contract.get("operation") == "local-correctness"
          and contract.get("gpu_family") == "B300"
          and contract.get("gpu_security") == "nonconfidential"
          and contract.get("execution_profile") == "tau8k-eager"
          and type(contract.get("data_parallel_size")) is int
          and contract["data_parallel_size"] == 2
          and contract.get("model_profile", {}).get("name") == "uncensored"
          and not {"provider_conformance", "stream_cohort", "tau_owner"} & set(contract))
    result = list(contract["arguments"])
    expected = {"--served-model-name": SERVICE_MODEL.split("/", 1)[1],
                "--host": "127.0.0.1", "--port": "8000", "--max-model-len": "8192",
                "--max-num-seqs": "1", "--max-num-batched-tokens": "8192",
                "--data-parallel-size": "2", "--tensor-parallel-size": "4"}
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
