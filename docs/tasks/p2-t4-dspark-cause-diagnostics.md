# P2.T4: Bounded Worker Exception Chains

Implemented and synthetic-tested diagnostics, not a DSpark fix or a successful
hardware run. The original selected DSpark receipt located a failure at pinned
vLLM `5df63a7203cdb9328d824a27b682e77c7b9928be`
[`cudagraph_utils.py:1020`](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/v1/worker/gpu/cudagraph_utils.py#L1020):
accelerator synchronization during temporary graph-profiling teardown. That
cleanup can mask an earlier exception. The old packet did not retain general
cause/context links, so it cannot establish the underlying GPU error. No OOM,
kernel defect, graph-setting fix, or working inference is inferred from it.

## Wire And Privacy Limits

Only the [worker diagnostic](p2-t3-engine-worker-diagnostic.md) changes. Its fresh
private cache file now contains a fixed 1175-byte, version-2 packet. The legacy
291-byte CPU/entry packet and its built-in classifications remain unchanged.
Historical worker JSON remains accepted by its pinned legacy validator; new
worker JSON uses the explicit `nib.engine-worker-exception/v2` schema. Unknown
fields, categories, links, versions, padding and packet sizes fail closed.

- At most four distinct exception objects, starting with the observed failure.
  Both explicit cause and implicit context links are recorded as numeric indices.
  Cause is visited before context. Shared objects and cycles are not revisited.
  Context is retained even for `raise ... from None`; suppression only controls
  Python's formatted traceback, which this observer never requests.
- At most 64 traceback frames are scanned across all four objects, including
  existing bounded Pydantic validation causes. Each object retains at most its
  last eight eligible source-hash/line pairs. Exhausted frame or object budgets
  are marked truncated; a missing link in a truncated graph need not mean no
  underlying exception existed.
- The existing five-root allowlist, 2 MiB per-source read limit and line range
  1 through 1,000,000 remain unchanged. There are at most 64 source reads across
  the packet. Unavailable source and observer failure remain explicit flags.
- Categories are the existing exact built-in types plus `accelerator` and
  `accelerator-oom`, only when the exception's exact class is the already-loaded
  `torch._C.AcceleratorError` or `torch._C.OutOfMemoryError`. The native module
  must be the same object referenced by loaded `torch._C`; no import occurs.
  Import/native provenance remains the sealed bootstrap's responsibility, not
  this classifier's. Missing types, subclasses and all other types stay `other`.
- No exception text, arguments, class names, notes, filenames, source text,
  frame locals, prompts, completions or environment values are serialized.
  BaseException's native descriptors bypass custom exception attribute hooks.
  No stringification, equality hooks or traceback formatting is used.

The first private-file writer still wins. No retries, exception suppression,
engine flags, graph sizes, source admission, network access, ownership checks,
deadlines or cleanup behavior change. Only after engine exit does the parent
read the exact-size packet through the existing no-follow regular-file reader.
An observer failure cannot change the original target exception or its outcome.

## Verification And Remaining Work

Focused tests cover cleanup masking, explicit causes, suppressed contexts,
cycles, shared links, chain/frame exhaustion, hostile exception hooks, fixed
native classifications, missing source, malformed binary and JSON envelopes,
historical packet bytes, source-pinned host validation, and unchanged
first-writer/link/size guards. All kernel/model behavior in these tests is
synthetic. Full checks and independent final-head review remain required.

This does not identify the original DSpark run's root cause retroactively.
A separately approved fresh run would be needed to collect its exception chain.
Even then, an accelerator category identifies the exception family, not a
particular CUDA error or kernel. No message-based diagnosis or automatic retry
is added.
