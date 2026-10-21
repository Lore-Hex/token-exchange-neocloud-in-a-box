# P2.T3: Original worker RPC failure diagnostics

The [sixth B300 trial](../../evidence/p2/2026-10-21-b300-memory-profile/run.json)
at `97d906790f205e0d1cf57e2f7b051a2c69e4033c` passed the complete weight and
application checks, then exited during startup after 262102 ms. The engine
stopped and container cleanup was confirmed; Docker reported no OOM kill.
This does not rule out a GPU allocation failure. No inference succeeded.

Its [original diagnostic](../../evidence/p2/2026-10-21-b300-memory-profile/output.log)
matches pinned vLLM `v1/engine/core.py:315`, `v1/executor/abstract.py:163`, and
`v1/executor/multiproc_executor.py:444`: the `determine_available_memory` RPC
failed. The last location is the parent's generic worker-error wrapper, not
the underlying worker exception. The exact cause remains unproven.

The source-pinned log observer previously recognized only the worker startup
catch at line 953. Add the other three explicit `logger.exception` catches at
959 (busy loop), 1000 (async output), and 1063 (RPC) in the same
[vLLM source revision](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/v1/executor/multiproc_executor.py).
The source hash, module, origin, bounded first-failure packet and factory
restoration remain required. No logging formatter, exception message, arguments
or locals are inspected. Exception handling and engine results are unchanged.

Verification: focused `scripts.test_deepseek_lab`, full `make check`, all hosted
checks, then a new bounded exact-source B300 trial. No production, attestation,
benchmark or model-quality qualification is claimed.
