# Sealed Model-Registry Reentry

The pinned vLLM model registry inspects model classes in a separate Python
process to avoid initializing CUDA in the API process. Its exact command is
`[sys.executable, "-m", "vllm.model_executor.models.registry"]`.
Sources: [`_SUBPROCESS_COMMAND`](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/model_executor/models/registry.py#L797)
and [`_run_in_subprocess` / `_run`](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/model_executor/models/registry.py#L1550).

Our lab wrapper replaced `sys.executable` but admitted only multiprocessing
spawn/tracker invocations and the separately reviewed Torch ISA probe. Thus
this required model-inspection child was rejected. A supported architecture
can then surface as a wrapped `ModelConfig` validation failure. The earlier
hardware receipt established that outer failure, not the nested cause.

The lab dispatcher now admits only this exact two-argument module invocation.
It first installs and checks the same sealed import roots and no-site/no-bytecode
policy as every other child. Before import it verifies the registry's pinned
source SHA-256. Before calling upstream `_run`, it verifies the loaded module
path, function globals and complete function code against the pinned source.
Arbitrary `-m`, extra arguments and generic Python code remain rejected.

Upstream's private parent-to-child pickle protocol is unchanged. This is a
trusted engine child inside the bounded lab container, not an endpoint or an
untrusted-pickle sandbox. The node must never expose that protocol publicly.
The P0 smoke guard is unchanged. No attestation or production qualification
is granted by this adapter.

Tests exercise the exact child as a real isolated subprocess, proving sealed
state is established before its synthetic unpickle boundary. Tampered source,
extra arguments, alternate modules and arbitrary Python are negative cases.
Actual full-model inference remains pending the next committed-producer trial.
