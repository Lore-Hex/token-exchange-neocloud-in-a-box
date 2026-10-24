# Nonconfidential DeepSeek Baseline

An explicit `--prepare-nonconfidential-baseline` option prepares the existing
bounded local-correctness experiment for four selected B200 GPUs whose NVML
settings affirmatively report CC disabled. This is part of P2's ordinary-GPU
functional testing, not a confidential fallback or a production profile.
The separate `--prepare-nonconfidential-baseline-b300` option selects four B300
devices with a 200 GiB per-device memory floor. Both the family and security
profile are hash-bound in the prepared contract and checked in the container
and host result validator; neither is inferred or downgraded automatically.

Before a large weight transfer, an approved ordinary eight-B300 host can run
the same read-only NVML observations directly from the reviewed source:

```sh
timeout 30 python3 -I -S -B scripts/engine_lab_probe.py --nonconfidential-b300-eight
```

This explicit preflight requires all eight B300 devices, sufficient ordinary
device memory, CUDA 13 or newer, and affirmative disabled-CC settings. Unsupported
queries reject; nothing retries into another profile. It changes no GPU settings,
starts no engine and verifies no attestation. The no-argument command retains
its original four-B200 confidential-mode observations. Keep the external deadline:
vendor library calls may block. A preflight result never substitutes for the
runner's fresh hardware checks or the complete inference result.

The [October 24 read-only inventory](../../evidence/p2/2026-10-24-hardware-probe-000651870938/run.json)
reported eight B300 GPUs with 275,040 MiB each and driver 580.173.02. Its
[observations](../../evidence/p2/2026-10-24-hardware-probe-000651870938/output.log)
found an Intel CPU and no TDX, SEV-guest or TSM interfaces. The original inventory
criterion is specifically eight B200s, so its retained result is correctly
`fail`. These are host-reported facts, not attestation, a passed ordinary-mode
NVML preflight, or an inference result.

The default B200 and B300 CC profiles are unchanged. They still reject
disabled CC, unknown modes, debug mode and unready devices. The new source-bound
`gpu_security` field is allowed only for B200/B300 local correctness. Missing means
the original confidential profile; unknown values reject. No auto-selection,
retry into a weaker mode, GPU setter or readiness mutation is added.

An additional explicit preparation supports the fixed uncensored checkpoint on
eight ordinary B300 GPUs, using the same TP4 x DP2 layout and 512 GiB memory cap
as its separate confidential-mode experiment:

```sh
python3 -I -S host/record-deepseek-lab.py \
  --prepare-nonconfidential-uncensored-b300-dp2 /absolute/private/ordinary-bundle \
  --model-profile uncensored
```

This selects the existing hash-pinned model manifest, not arbitrary weights.
It checks all eight devices for explicitly disabled CC and ordinary memory
capacity. It keeps the existing 4,096-token context, single sequence per
replica, GPU-resident Engram, no added swap, isolated container, eight bounded
correctness cases and cleanup. It does not enable parity/stream cohorts,
provider conformance, persistent public serving or automatic fallback. Those
require their own reviewed admission paths and actual measurements. This
preparation is unit-tested with synthetic hardware only; no ordinary eight-GPU
run, production serving, billing or confidential claim follows from it.

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
