# Nonconfidential DeepSeek Baseline

An explicit `--prepare-nonconfidential-baseline` option prepares the existing
bounded local-correctness experiment for four selected B200 GPUs whose NVML
settings affirmatively report CC disabled. This is part of P2's ordinary-GPU
functional testing, not a confidential fallback or a production profile.
The separate `--prepare-nonconfidential-baseline-b300` option selects four B300
devices with a 200 GiB per-device memory floor. Both the family and security
profile are hash-bound in the prepared contract and checked in the container
and host result validator; neither is inferred or downgraded automatically.

The default B200 and B300 CC profiles are unchanged. They still reject
disabled CC, unknown modes, debug mode and unready devices. The new source-bound
`gpu_security` field is allowed only for B200/B300 local correctness. Missing means
the original confidential profile; unknown values reject. No auto-selection,
retry into a weaker mode, GPU setter or readiness mutation is added.

The baseline requires NVML to return feature disabled, environment unavailable,
development tools off and no protected multi-GPU mode. A missing/unsupported
query is a failure, not proof of disabled CC. It reads ordinary device-memory
capacity rather than falsely labeling it protected memory. The existing
image constraints, exact weight hashes, five-stage experimental application,
isolated Python bootstrap, GPU-resident Engram, conservative collective settings,
synthetic correctness cases, no-network container, resource limits, watchdog
and cleanup remain the same. It is not an optimized stock-engine benchmark.

The ABI comes from NVIDIA's pinned
[NVML header](https://github.com/NVIDIA/go-nvml/blob/1a1e86da474d340d7cf58274588f4a4afc8772e7/pkg/nvml/nvml.h):
`nvmlMemory_t` v1 and `nvmlDeviceGetMemoryInfo` report bytes; the CC definitions
give the disabled/unavailable/none constants. The selected GPU family and
memory floor remain checked at each observation. No provider's advertised
GPU name or CUDA image label substitutes for these runtime checks.

```sh
python3 -I -S host/record-deepseek-lab.py \
  --prepare-nonconfidential-baseline /absolute/private/baseline-bundle

# Use only for an explicitly approved B300 ordinary-GPU experiment.
python3 -I -S host/record-deepseek-lab.py \
  --prepare-nonconfidential-baseline-b300 /absolute/private/b300-baseline-bundle
```

Execute the prepared bundle through the same documented recorder and private
paths in the [lab guide](p2-t3-deepseek-lab.md). A future result must retain
`confidential:false`, `deployable:false`, `attestation_verified:false` and the
explicit CC-disabled observations. This preparation does not establish an
actual inference result, a tau score, TDX support or P2 completion.
