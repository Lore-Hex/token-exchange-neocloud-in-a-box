<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 Lore Hex Corp -->

# Sizing and estimates

Status: **planning estimates, no measured fit or throughput**. These expand
[PLAN section 3](../PLAN.md#3-sizing-how-big-each-node-must-be), not permission
to procure hardware or select engine flags. Source review: 2026-10-19 UTC.

## Units and source bytes

GB = 1,000,000,000 bytes; GiB = 1,073,741,824 bytes. Vendor capacities are
budgeting inputs, not allocatable-memory measurements. Calculations assume
even sharding and an **estimated 15 GB/GPU** for activations, graphs and
workspace. Replicated tensors, loader peaks, quantization metadata and runtime
reserves can invalidate that assumption.

| Model revision | Weight shard bytes | Other bytes | Complete-pack `total_bytes` |
|---|---:|---:|---:|
| DeepSeek-V4.1-Flash-1017, `1bae0e1088993b9b82de08f59de5be5a34e42170` | 510,296,708,312 | 14,336,983 | **510,311,045,295** |
| GLM-5.3, `ba32f8c19c8738e2c57c71798c601d477a7bf844` | 755,632,050,320 | 31,638,982 | **755,663,689,302** |

Primary sources: recursive pinned
[DeepSeek tree](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash-1017/tree/1bae0e1088993b9b82de08f59de5be5a34e42170?recursive=true&limit=1000)
and [GLM tree](https://huggingface.co/api/models/zai-org/GLM-5.3/tree/ba32f8c19c8738e2c57c71798c601d477a7bf844?recursive=true&limit=1000).
P0.T4's source audit counts 83 files/48 weight shards and 156 files/141 weight
shards respectively. The correction adds non-weight files; it does not drop
configuration, tokenizers, licenses or code to force a weights-only total.
Metadata sums are not downloaded-file integrity verification. P0.T4 owns
hashing and deployable packs.

Arithmetic: `510296708312 + 14336983 = 510311045295`;
`755632050320 + 31638982 = 755663689302`. Disk bytes are not exact runtime GPU
allocation. Use weight subtotals only for the approximate memory budget, and
all-file totals for the complete manifest.

## Weight and workspace budget

Inputs: [documented CC capacities](hardware.md#gpu-mode-matrix), shard bytes
above and the estimated workspace reserve. For `T` tensor ranks:

```text
estimated weights per rank = weight_bytes / T / 1e9
estimated remainder per rank = nominal_GPU_GB - weights_per_rank - 15
```

| Scenario | Nominal aggregate GB | Estimated weights GB/rank | Estimated remainder GB/rank | Interpretation |
|---|---:|---:|---:|---|
| DeepSeek, 4 x B200 TP4 | 720 | 127.574 | 37.426 | Candidate GPU-resident replica. |
| DeepSeek, 8 x B200 DP2 x TP4 | 1,440 | 127.574 | 37.426 | Two complete replicas; weights duplicated, not sharded over eight. |
| DeepSeek, 4 x H200 TP4 | 564 | 127.574 | -1.574 | No fit under this reserve assumption, before KV. |
| DeepSeek, 8 x H200 TP8 | 1,128 | 63.787 | 62.213 | Memory only; kernels/CC performance unvalidated. |
| GLM FP8, 8 x H200 TP8 | 1,128 | 94.454 | 31.546 | Context-limited candidate. |
| GLM FP8, 8 x B200 TP8 | 1,440 | 94.454 | 70.546 | Preferred 1M-context candidate. |
| GLM FP8, 8 x B300 TP8 | 2,160 | 94.454 | 160.546 | Corrected 270 GB input; not selected. |
| GLM FP8, 8 x H100, assumed 80 GB/GPU | 640 | 94.454 | -29.454 | Does not fit this scenario. |

Remainder is not measured KV. The [vLLM GLM recipe at 2338e9ed](https://github.com/vllm-project/recipes/blob/2338e9edec1d5df5f823abf3c1f19e7ba0b2ed0e/models/zai-org/GLM-5.3.yaml)
(`variants.default.hardware_overrides.h200`) reports approximately 23 GiB
for its H200 KV budget and caps context at 262,144. That differs from the simple
31.546 GB remainder; do not silently interchange the two. An upstream recipe
is not our hardware evidence.

## KV sensitivity and concurrency

The pinned [DeepSeek config](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017/blob/1bae0e1088993b9b82de08f59de5be5a34e42170/config.json)
and [GLM config](https://huggingface.co/zai-org/GLM-5.3/blob/ba32f8c19c8738e2c57c71798c601d477a7bf844/config.json)
declare 1,048,576 maximum positions. This is a model limit, not an engine's
allocation guarantee at the requested concurrency.

**DeepSeek estimate:** PLAN's approximately 890 B/token remains a provisional
compressed-cache heuristic, not a verified layout or accepted profile.
`890 * 1048576 / 1e9 = 0.933 GB`; doubling the assumed rate gives 1.866 GB.
Window state, indexers, drafter state, cache replication and rounding add
uncertainty. The [released Tinfoil configuration](https://github.com/tinfoilsh/confidential-deepseek-v4-1-flash/blob/29bc3ea355129b1dda97282ce80ce972530ee4fc/tinfoil-config.yml)
selects `fp8_ds_mla`; it does not validate PLAN's FP4 shorthand or a universal
890 B/token rate. Small estimated KV does not mean unlimited concurrency.
Confidential profiles must keep Engram on GPU.

**GLM estimate:** an illustrative packed-MLA calculation uses the config's 78
layers, latent width 512, RoPE width 64 and 21 full indexers of width 128.
Assume one-byte latent/index values, two-byte RoPE values, 16 scale bytes per
MLA entry and four per index entry:

```text
78 * (512 + 64*2 + 16) + 21 * (128 + 4) = 53,940 B/token
53,940 * 262,144   / 1e9 = 14.140 GB
53,940 * 1,048,576 / 1e9 = 56.560 GB
```

Byte widths and scale packing are **assumptions**, not verified properties of
every backend. For a TP backend that replicates this cache, apply the cost per
rank; do not divide by TP8 or add all ranks' free memory as different tokens.
The 23 GiB example gives `23 * 2^30 / 53940`, approximately 457,843 cached
tokens per replicated rank before other overhead. DP-attention changes cache
ownership and needs a separate measured profile, not an automatic eightfold
capacity claim for TP.

Live tokens include input, generated tokens, retained prefixes and speculative
state, with engine-specific sharing/rounding. One full-window request fitting
does not prove that many do. P2 must record cache layout, rank allocations,
prefill peaks, maximum sequences and admission limits. No quality equivalence,
throughput multiplier or CC overhead percentage is claimed here. NVFP4 is an
optional P2 comparison with separate revision/license/byte accounting and
quality checks, not a selected alternative pack.

## Host storage and cost estimates

Both full packs total `510311045295 + 755663689302 = 1265974734597` bytes,
about 1.266 TB. Raw downloads plus an uncompressed volume for each are about
2.532 TB before verity trees, images, staging and prior releases. The 15 TB
NVMe target provides planning headroom, not a measured minimum. Compression
and retention remain unknown. The 2 TB RAM/100 GbE targets also need load-time
and working-set measurements. Host RAM is not an Engram offload escape hatch.

**Illustrative estimate, not a vendor quote:** retain PLAN's USD 415,000
purchase assumption, 36-month amortization, 14.3 kW power-budget assumption,
USD 230/kW-month colocation and 730 hours/month:

```text
415000 / 36 = 11527.78 USD/month
14.3 * 230 = 3289.00 USD/month
total = 14816.78 USD/month
14816.78 / (8 * 730) = about 2.54 USD/GPU-hour
```

Power here is an assumption, not HydraHost's unknown server specification.
Taxes, financing, energy treatment, networking, support, NVIDIA licensing,
spares, labor and idle capacity change cost. At an **assumed** rental range of
USD 4-9/GPU-hour, `8 * 730 * rate` is USD 23,360-52,560/month. Obtain a current
complete quote before G-HW-RENT/G-RUN; these are not current market prices.

Serving both models simultaneously means two planned nodes. GLM uses the
whole node; two DeepSeek replicas still serve one model in one TD. Revenue,
cost per token and ordering decisions wait for measured utilization/throughput.

## Required measurement

P2 records exact bytes loaded, dtype, per-rank steady/peak HBM, workspace, cache
format, context/concurrency sweep, latency/error/throughput and quality checks.
P5 repeats equal workloads under CC and records load time, overhead, two
reboots and soak behavior. Include engine/firmware pins, flags, workload lengths,
prefix reuse and synthetic-data provenance. Never relabel a published benchmark
as our run. All fit/KV/reserve/cost calculations here remain **estimates**;
all deployment profiles remain **unvalidated** until that evidence exists.
