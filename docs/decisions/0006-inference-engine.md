<!-- SPDX-License-Identifier: Apache-2.0 -->
# ADR 0006: vLLM first, measured alternatives only

Status: accepted default; engine profiles are unvalidated until P2/P5.
Records PLAN D6.

## Decision

Use vLLM as the default engine for both initial model packs. Pin a source-backed
image digest and document every launch option. SGLang requires a separate ADR
and more than a 25% measured performance win at comparable quality, memory,
context, concurrency and security settings before adoption.

Do not copy a floating recipe tag into a measured image. Remote model code and
all executable dependencies must be pinned too. Bind inference and auxiliary
listeners to loopback, disable request logging and telemetry, and enforce the
guest's ingress/egress policy regardless of advertised defaults.

## Rationale and limits

One default engine limits the code and deployment profiles we must review.
The [vLLM recipes](https://github.com/vllm-project/recipes) and
[SGLang cookbook](https://github.com/sgl-project/sgl-cookbook) are recipe sources;
the model packs record the exact revisions reviewed. A published recipe is not
a benchmark or a proof that it works under GPU CC on our hardware.

The [Tinfoil release configuration at 29bc3ea3](https://github.com/tinfoilsh/confidential-deepseek-v4-1-flash/blob/29bc3ea355129b1dda97282ce80ce972530ee4fc/tinfoil-config.yml)
is a reference for DeepSeek's `VLLM_DISABLE_UVA=1` and disabled Engram CPU
offload. Our confidential profile must keep Engram GPU-resident and forbid
contradictory flags, OOM-triggered offload or unreviewed host-memory paths.
See [the threat analysis](../threat-model.md#engram-and-host-visible-access-patterns).

## Verification and revisit

P2 records actual model loading, generated-output correctness, per-rank memory,
TTFT and sustained throughput for the planned workloads. P5 repeats the same
configuration under CC, inspects device/host transfer behavior and applies the
plan's overhead and soak gates. Report failures rather than relaxing security
to achieve the performance target.

Revisit based on those measurements or a security issue requiring replacement.
An engine change creates a new measured release and must pass the same negative
and operational tests, even when its HTTP API is compatible.
