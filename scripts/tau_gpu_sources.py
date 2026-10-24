# SPDX-License-Identifier: Apache-2.0
"""Exact source closure for the selected GPU owner, never ambient imports."""

import builtins
import hashlib
import types

NAMES = ("tau_preflight", "tau_trial_worker", "tau_transport", "tau_local_vllm",
         "tau_local_llama", "engine_tokenizer_parity", "tau_llama_identity", "tau_live_identity",
         "tau_cpu_parity", "tau_owner_channel", "tau_support_probe", "deepseek_model_profile",
         "tau_selected_policy", "engine_local_correctness", "engine_runtime_observation", "tau_gpu_owner")
PREFIX = "tau-owner/"


def load(sources, pins):
    """The caller authenticates this loader and the containing contract first."""
    modules = {}
    def source_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in NAMES:
            if level != 0 or name not in modules:
                raise ValueError("owner source order rejected")
            return modules[name]
        return builtins.__import__(name, globals, locals, fromlist, level)
    for name in NAMES:
        path = PREFIX + name + ".py"
        raw = sources[path]
        if not (type(raw) is bytes and 0 < len(raw) <= 2 << 20
                and hashlib.sha256(raw).hexdigest() == pins[path]):
            raise ValueError("owner source digest rejected")
        module = types.ModuleType(name)
        module.__file__ = "/opt/nib/" + path
        module.__dict__["__builtins__"] = {**vars(builtins), "__import__": source_import}
        exec(compile(raw, module.__file__, "exec", dont_inherit=True), module.__dict__)
        modules[name] = module
    return modules["tau_gpu_owner"]
