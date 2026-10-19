# Plan: Token Exchange Neocloud-in-a-Box

Status: **draft v1, 2026-10-18**. Owner: Joseph Perla. Executors: coding agents
(read [AGENTS.md](AGENTS.md) first).

**Source-corrected documentation, 2026-10-19.** P0.T2 adds
[architecture](docs/architecture.md), [sizing](docs/sizing.md),
[hardware](docs/hardware.md) and [threat-model](docs/threat-model.md) contracts.
B300 planning uses the R595 CC SKU's 270 GB, not the former 288 GB. Complete
model manifests count all files, not just weight shards (P0.T4 below).
These are documented facts/design requirements and labelled estimates, not
hardware validation. Legacy market/benchmark snapshots below are not run-cost
approvals or our measurements; recheck before acting.

Joseph authorized continued software work and self-merge only after CI is green
and independent review of the final head is clean. Spending,
production, releases, settings and contact gates remain. Joseph reports existing
HydraHost outreach for eight B200 GPUs; hardware is pending, not procured.
See [gates](docs/gates.md). Phase dependencies are unchanged.

Every fact below that comes from outside this repo has a source in
[§12](#12-sources). Facts about models, engines, drivers and clouds change
weekly. Re-check the source before acting on one, and stop if it no longer
holds.

## 0. Summary

We are building, in the open, all the software a small operator needs to run a
**confidential GPU inference cloud**. The first operator is us, and the tokens
are sold through TrustedRouter (TR) and the NY Token Exchange (NYTE).

**What a node is.** A node is one 8-GPU server whose GPUs, model and inference
engine all run inside one Intel TDX confidential VM, with the GPUs in NVIDIA
confidential-computing (CC) mode. The node serves one open-weight model behind
an OpenAI-compatible HTTPS endpoint. Its TLS key is generated inside the VM and
bound to hardware attestation (`tr-attestation/v0`), so the TR gateway can check
on every connection exactly which software it is talking to before it sends a
prompt.

**First two nodes:**

| | DeepSeek-V4.1-Flash-1017 — **first** | GLM-5.3 — **second** |
|---|---|---|
| Hugging Face repo | `deepseek-ai/DeepSeek-V4.1-Flash-1017` (2026-10-17) | `zai-org/GLM-5.3` (FP8) |
| Size | 763B logical: 552B backbone + 196B Engram tables + DSpark drafter; 8B/16B active (prefill/decode) | 753B total, 40B active |
| Weights on disk | **510.3 GB** (MXFP4 experts, FP8 Engram tables) | **755.6 GB** (FP8) |
| KV cache per token | ~890 B (FP4) | ~53.9 KB (FP8, vLLM format) |
| Smallest confidential fit | **4×B200** (one replica, Engram tables in GPU memory) | **8×H200** (≤262K context) |
| Recommended | **8×B200 = 2 replicas** | **8×B200** (full 1M context, ~2× H200 throughput) |
| Confidential competition on TR's radar | Tinfoil (experimental) at $0.65/$1.45 per M in/out | Phala $0.84/$2.64, Tinfoil $1.80/$5.75, Privatemode €1.55/€5.74 |
| License | MIT | GLM-5.3 License (MIT-style; a security-review clause applies only to sellers with >$10B revenue) |

**One hardware SKU runs both:** an HGX **8×B200** server with an Intel Xeon that
supports TDX. Blackwell's multi-GPU confidential mode encrypts NVLink and has
been generally available for HGX B200 since NVIDIA's R590 driver (December
2025). See [§3](#3-sizing-how-big-each-node-must-be) for the arithmetic,
[§3.4](#34-where-to-get-the-hardware) for where to get it, and
[§4](#4-which-first-and-why) for the order.

**Start now (human critical path, in parallel with P0–P3).** These have lead
times measured in weeks:
1. **Source the confidential server (G-HW-RENT).** Joseph has contacted
   HydraHost; availability and exact TDX/CC configuration remain pending.
   The questionnaire in [§3.4](#34-where-to-get-the-hardware) is preparation,
   not authorization for agents to contact additional providers.
2. **Request a quote for NVIDIA's Enterprise license (G-NVIDIA-LICENSE).**
   NVIDIA's terms require one for commercial use of confidential computing,
   including its attestation services. Development and proof-of-concept use is
   free.
3. **Create the GCP lab project and request quota** (G-GCP-PROJECT,
   G-GCP-QUOTA): 8× B200, and 1× H100 Spot for confidential VMs.
4. **Register for an Intel PCS API key (G-INTEL-PCS)** and an **NGC API key**
   for NVIDIA's reference-measurement and revocation services.

**Phases** (each has exit criteria; details in [§8](#8-phases)):

| Phase | What | Hardware | Human gate |
|---|---|---|---|
| P0 | Repo foundations, schemas, model packs | none | — |
| P1 | GPU-free end-to-end node in CI | none | — |
| P2 | Bring up both models, non-confidential, and benchmark | GCP 8×B200 | lab project, quota, run cost |
| P3 | Confidential pipeline end to end with a small model | GCP 1×H100 TDX VM | quota, run cost |
| P4 | TR side: generic `tr-attestation` verifier, a new provider kind | TR staging | TR production changes |
| P5 | First bare-metal confidential node: DeepSeek-V4.1-Flash-1017 | rented 8×B200 TDX server | rental, Intel key, NVIDIA license |
| P6 | List DeepSeek-V4.1-Flash-1017 on TR | P5 node | pricing, production |
| P7 | GLM-5.3 node and listing | second 8×B200 server | rental, production |
| P8 | Reproducible releases, signing, transparency page | CI | publishing |
| P9 | Fleet operations and "in a box" for other operators | — | — |

## 1. Goal, definition of done, non-goals

**Goal.** A GPU owner with a TDX-capable 8-GPU server can use only this repo to:
1. boot an attested confidential inference node serving one of our model packs;
2. let anyone verify it independently; and
3. list it on TrustedRouter as a verified confidential provider.

**v1 is done when:**
- both nodes are live on TR as confidential routes (`deepseek/deepseek-v4.1-flash-1017`
  and `z-ai/glm-5.3`) whose TR receipts read `tee-verified`;
- the TR gateway checks them with the generic `tr-attestation/v0` verifier (P4)
  against measurements we published;
- provider-check Tiers 1–4 pass against each node;
- a node can be rebuilt from a tagged release and reproduces the published
  measurements; and
- a stranger can follow `docs/` to do the same on their own hardware.

**Non-goals for v1:**
- inference across several machines (confidential multi-node is impractical:
  GPUDirect RDMA and NVLink multicast are unavailable in CC mode);
- training or fine-tuning;
- renting raw VMs to tenants;
- Kubernetes;
- AMD GPUs and consumer GPUs;
- our own billing (TR meters and bills);
- custom CUDA kernels (use upstream engines; carry patches only when CC requires
  them); and
- any production fallback to non-confidential mode.

## 2. What already exists (reuse, do not rebuild)

| Thing | Where | Use it for | State |
|---|---|---|---|
| `trcs` shim + `tr-attestation/v0` protocol + golden vectors | [trustedrouter-confidential-stack](https://github.com/Lore-Hex/trustedrouter-confidential-stack) (`cmd/trcs`, `internal/attest`, `docs/attestation-protocol.md`) | TLS termination inside the TEE, the evidence endpoint, a listener audit | alpha; **never run on CC hardware**; standard library only |
| Provider checker | [trustedrouter-provider-check](https://github.com/Lore-Hex/trustedrouter-provider-check) (`uvx trustedrouter-provider-check`, PyPI 0.1.8) | conformance gate (Tiers 1–4) | live; no attestation checks |
| TR Catalog v2 contract | quill-router `src/trusted_router/provider_contract.py`; schema at https://trustedrouter.com/providers/marketplace/catalog.v2.schema.json | public price/model declaration (`--catalog-url`) | live |
| TDX + NVIDIA verification code | quill-cloud-proxy `sidecar/near_ai_verify.go` (TDX report data, RTMR3, NVIDIA checks) | starting point for the generic verifier (P4) | live for NEAR |
| CC patch for SGLang | [confidential-dot-ai/confidential-inference](https://github.com/confidential-dot-ai/confidential-inference) `images/sglang/patches/cc-optimizations-v0518.patch` (Apache-2.0) | detects CC through NVML; moves device-to-host copies off the hot path; all-reduce without NVLink multicast | production at confidential.ai |
| Weight-manifest gate | same repo, `images/sglang/wait_for_model.py` | design reference for weight integrity | production |
| Confidential DeepSeek V4.1 config | [tinfoilsh/confidential-deepseek-v4-1-flash](https://github.com/tinfoilsh/confidential-deepseek-v4-1-flash) | proven settings: 4×B200/B300 on TDX, vLLM 0.31.0 TP4, Engram tables kept in GPU memory, `VLLM_DISABLE_UVA=1` | production ("experimental") |

**Do not use:**
- C8s (confidential.ai's platform). It is **AGPL-3.0**: read it for design
  ideas only, never copy code.
- `Coral-Bricks-AI/vllm`. It is upstream vLLM from 2026-07-25 plus one media
  SSRF fix. Use upstream vLLM, which already contains the KV block-size fixes
  #49845 and #58457 that DeepSeek and GLM sparse attention need.
- `trymirai/uzu` (Apple-only).

## 3. Sizing: how big each node must be

All memory numbers are **estimates** from published sizes. P2 measures the real
values. NVIDIA's R595 CC SKU table labels H200 141 GB, B200 180 GB and
**B300 270 GB**, not the former 288 GB planning input
([R595 v02, pages 5-7](https://docs.nvidia.com/595trd1-trusted-computing-solutions-release-notes.pdf#page=5)).
These are nominal vendor capacities, not measured usable HBM. The 15 GB/GPU
activation/graph/workspace reserve is an **estimate**. Unit-consistent
arithmetic and runtime limitations are in [docs/sizing.md](docs/sizing.md).

### 3.1 DeepSeek-V4.1-Flash-1017

- **Weights: 510.3 GB.** That is 288.8 GB of MXFP4 routed experts, 202.8 GB of
  FP8 Engram tables, 7.9 GB of DSpark drafter, about 6.9 GB of FP8
  attention/dense layers, 2.65 GB of BF16 embeddings and head, and 0.97 GB of
  vision encoder.
- **Engram tables must stay in GPU memory on a confidential node.** vLLM's
  default puts them in pinned host memory read over UVA. In CC mode
  `cudaHostRegister` (pinning) is unsupported, and every transfer between the
  GPU and host memory uses shared bounce-buffer pages carrying encrypted
  payloads, not plaintext ([NVIDIA whitepaper, Bounce Buffers](https://docs.nvidia.com/nvidia-secure-ai-with-blackwell-and-hopper-gpus-whitepaper.pdf#page=12)).
  Input-dependent lookup patterns remain a threat even with encrypted payloads;
  no prompt-recovery rate is claimed here. Tinfoil keeps the tables on the GPU
  (`VLLM_DISABLE_UVA=1`). **Engram offload is forbidden in every confidential
  profile.**
- **KV estimate:** the original 890 B/token heuristic gives
  `890 × 1,048,576 / 10^9 = 0.933 GB`. This is not a verified cache layout or
  unlimited-concurrency guarantee. The released Tinfoil configuration uses
  `fp8_ds_mla`; see the [source and sensitivity analysis](docs/sizing.md#kv-sensitivity-and-concurrency).
- **Fit:**
  - **4×B200 (720 GB):** ~128 GB per GPU of weights, ~37 GB per GPU left. One
    replica. This is Tinfoil's confidential configuration.
  - **8×B200 (1.44 TB):** two TP4 replicas behind one endpoint (vLLM
    data-parallel 2 × tensor-parallel 4). **Recommended.**
  - **8×H200 (1.13 TB):** fits at TP8, but published throughput per GPU is
    roughly 7–10× lower than Blackwell. The FP4 experts and FP4 KV kernels are
    Blackwell-only, and FlashMLA dropped Hopper on 2026-09-30. Fallback only.
  - **4×H200:** does not fit with the Engram tables in GPU memory.
- **Published throughput** (September weights, agent-trace workloads with huge
  inputs, InferenceX):
  - 4×B200 with SGLang at concurrency 256: 2,166 output tok/s per GPU.
  - 4×B200 with vLLM DEP4 at concurrency 128: 1,362 output tok/s per GPU.
  - 8×H200 with vLLM TP8 at concurrency 64: 187 output tok/s per GPU; it
    collapses at concurrency 128.
  - Lambda, 4×B200 vLLM TP4, 8K in / 1K out: 4,859 total output tok/s at
    concurrency 256.

### 3.2 GLM-5.3

- **Weights: 755.6 GB FP8** (141 shards). BF16 is 1,506.7 GB, which is not
  useful on one node.
- **KV cache:** 53,940 B per token (vLLM `fp8_ds_mla` plus indexer). The MLA
  cache is **copied to every tensor-parallel rank, not split**.
  - At TP8 on 8×H200, vLLM has ~23 GiB per GPU, about 457K tokens for the whole
    node. That is why vLLM's recipe caps H200 at `--max-model-len 262144`.
  - SGLang's DP-attention mode (`--attn-dp-size 8`) gives each rank its own
    requests, so node capacity is ~8× larger. That mode is where its throughput
    numbers come from.
- **Fit:**
  - **8×H200:** FP8 TP8, context ≤262K, KV cache in BF16 on Hopper in SGLang.
    Works, but depends on Hopper kernels that upstream FlashMLA no longer
    maintains.
  - **8×B200:** FP8 TP8 with FP8 KV, ~70 GB per GPU for KV, **full 1M context**.
    **Recommended.**
  - **8×B200 + NVFP4** (`nvidia/GLM-5.3-NVFP4`, 464.1 GB, Blackwell-only, NVIDIA
    reports evals ≈ FP8): about twice the KV room. Adopt only if P2's quality
    smoke matches FP8.
  - **8×H100:** FP8 does not fit (640 GB).
- **Published throughput** (SGLang, FP8, 8K in / 1K out, input+output tok/s per
  GPU, simulated MTP acceptance): 8×H200 2,558 and 8×B200 5,025 at concurrency
  256 with DP-attention.

### 3.3 The node SKU and software stack

| Component | Requirement | Source / why |
|---|---|---|
| GPUs | HGX 8×B200, air-cooled; B300 is a separately qualified alternative | [Mode matrix](docs/hardware.md#gpu-mode-matrix); do not infer support for other cooling variants or GB200/GB300 racks |
| GPU CC mode | Blackwell multi-GPU passthrough CC: up to 8 GPUs per VM, NVLink encrypted, CPU↔GPU via encrypted bounce buffers | GA for HGX B200 in R590 (driver 590.48.01); R595 adds B300 |
| CPU | Intel Xeon with TDX: 5th Gen Emerald Rapids (TDX module 1.5.x) or 6th Gen Granite Rapids (2.0.x) | NVIDIA CC deployment guide v7.1; canonical/tdx |
| BIOS | TME, TME-MT, TDX, SEAM loader and SGX enabled; non-zero TDX key split; 46-bit physical-address limit off | NVIDIA CC deployment guide v7.1 |
| Host OS | Ubuntu 26.04 LTS is our intended host; exact kernel/QEMU/OVMF combination must be pinned and qualified | Canonical TDX setup is not NVIDIA validation of this combination; R595 lists Intel Ubuntu 25.10 / kernel 6.17.0+ |
| Driver and firmware | R595 reference: driver 595.58.03, CUDA 13.2; exact board firmware still needs selection | [Hardware sources](docs/hardware.md#host-guest-and-fabric-ownership); Blackwell Fabric Manager is on host in the guide, Hopper PPCIe's is in guest |
| Guest OS | Ubuntu 24.04 (NVIDIA-validated guest) | NVIDIA R595 release notes |
| RAM | ≥ 2 TB DDR5 | TDX VM memory, weight staging |
| Disk | ≥ 15 TB NVMe | weight caches for both models plus verity trees |
| Network | ≥ 1×100 GbE | weight downloads; inference traffic is small |
| Out of band | BMC with Redfish/IPMI, plus BIOS access or a provider that sets the BIOS profile above for us | TDX settings, power control |
| Power | 14.3 kW is an illustrative budget assumption, not the chosen server's specification | Obtain OEM power/cooling requirements; [estimate](docs/sizing.md#host-storage-and-cost-estimates) |

What CC mode forbids or changes, and the engines must respect:
- no GPUDirect RDMA, MPS, MIG or `cudaHostRegister`;
- NVLink SHARP multicast (NVLS) is off;
- all host↔GPU copies go through bounce buffers (one 8×H200 study measured
  pinned host-to-device bandwidth at 0.18× normal; one B300 study measured
  model loading 34× slower).

Tuned stacks lose 1–8% of throughput under CC; stock stacks lose 13–40%. CC
tuning is real work (P5.T4).

**Two models running at once means two servers.** GLM-5.3 needs all 8 GPUs, and
one CC VM owns the whole node.

### 3.4 Where to get the hardware

**Lab scope:** use only the documented configuration selected for each phase;
the following survey is not proof of universal cloud availability or absence.
- GCP has `a3-highgpu-1g` (1×H100, TDX) and `g4-standard-48` (1× RTX PRO 6000,
  SEV).
- Azure has NCCadsH100v5 (1×H100, SEV-SNP).
- AWS has no GPU TEE.

**Big neoclouds** (CoreWeave, Lambda, Nebius, Crusoe, Hyperstack) rent 8×B200
machines but advertise no confidential computing or BIOS access.

| Option | 8×B200 (per GPU-hour) | Confidential computing | Use for |
|---|---|---|---|
| GCP `a4-highgpu-8g` | our existing GCP account | no | P2 bring-up and benchmarks |
| Modal | $6.25 (per second, no commitment) | no | P2 fallback if GCP quota is slow |
| Sesterce / Hyperstack / Lambda / CoreWeave | $4.11 / $6.00 ($5.10 reserved) / $6.69 / $8.60 | not advertised; ask | P5 if they enable TDX + CC |
| Hydra Host | not published | TDX bare metal (beta), their BIOS profile enables TDX/TME/SGX, full GPU passthrough | **P5 first candidate** |
| ServerMO, Spheron | not published | "CC-ready" reserved bare metal; their staff set BIOS and VBIOS | P5 candidates |
| Phala Cloud | 8×H200 $4.03–5.53; 8×B300 $7.80–8.20 | managed TDX + GPU CC VMs (dstack); no BIOS; their OS image | P5 fallback platform (see P5 note) |
| **Buy** an HGX B200 | ~$360–470K new; ~14.3 kW; colocation $190–265 per kW-month (≈ $2.8–3.8K/month) | full control | after 30 days of P6 utilization data |

**Estimate:** buying is about $415K / 36 months = $11.5K/month, plus
~$3.3K/month of colocation (14.3 kW × ~$230/kW-month), ≈ $14.8K/month, or
≈ $2.5 per GPU-hour (÷ 8 GPUs × 730 h). Renting CC-capable B200 capacity is
likely $4–9 per GPU-hour, i.e. $23–53K/month (× 8 × 730).

**Questionnaire for providers** (G-HW-RENT: Joseph sends it; Codex drafts it in
`docs/hardware.md`):
1. HGX B200 (or B300) 8-GPU, air-cooled, with which Xeon (Emerald or Granite
   Rapids)?
2. Will you enable TDX (TME, TME-MT, TDX, SEAM loader, SGX, key split) in BIOS,
   or give us BIOS/BMC access?
3. May we put the GPUs into NVIDIA multi-GPU CC mode and update GPU firmware to
   NVIDIA's R595 trusted-computing release?
4. Is the host OS ours (Ubuntu 26.04)?
5. What are the physical custody controls (ISO 27001 / SOC 2) and the location?
6. Monthly price, minimum term, and how soon it can be delivered.

## 4. Which first, and why

**DeepSeek-V4.1-Flash-1017 first, GLM-5.3 second, both on 8×B200.**

1. **It is easier to fit.** 510 GB with a tiny KV cache, versus 756 GB with a
   KV cache that is copied to every GPU. One 8×B200 node runs two replicas.
2. **A known-good confidential configuration exists.** Tinfoil runs it on
   TDX + B200/B300 with vLLM 0.31.0 TP4 and Engram tables in GPU memory.
3. **Market.** Today TR routes it only to non-confidential providers (arcee,
   baseten, deepinfra, engy, featherless, morph). GLM-5.3 already has three
   confidential sellers, one of them at $0.84/$2.64.
4. **Simpler license:** MIT.
5. **It needs Blackwell anyway**, which is the SKU GLM-5.3 also prefers.

**Fallback:** if no 8×B200 TDX server with working multi-GPU CC can be had
within three weeks of starting P5, take an 8×H200 TDX server and do **GLM-5.3
first**. Hopper's Protected PCIe multi-GPU CC mode is the most proven
confidential setup, and DeepSeek-V4.1-Flash is slow on Hopper.

## 5. Architecture

```
            TrustedRouter attested gateway (relying party)
                        │  TLS, verified per connection: TDX quote + GPU evidence,
                        │  report_data binds the node's TLS key (tr-attestation/v0)
════════════════════════╪═════════ host (untrusted: operator, provider, cloud) ═══
  bare-metal host       │ L4 port-forward 443 → 8443 only (never terminates TLS)
  ┌─────────────────────▼──────────────────────────────────────────────────┐
  │ Intel TDX trust domain (confidential VM)  — measured: MRTD, RTMR0-3    │
  │                                                                         │
  │  trcs shim :8443 ─ TLS key born here; /.well-known/trustedrouter/       │
  │     │              attestation; listener audit; 429 + Retry-After       │
  │     ▼ 127.0.0.1:8000                                                    │
  │  inference engine (vLLM, pinned digest)  ── 8 GPUs in CC mode           │
  │     ▲                                     (Blackwell: encrypted NVLink) │
  │  nib-gpu-gate: GPU + NVSwitch attestation at boot; fails closed         │
  │  nib-weights: dm-verity over model weights (root hash pinned in image)  │
  │  nftables: egress allowlist only; no SSH, no serial in release images   │
  └─────────────────────────────────────────────────────────────────────────┘
  host disks: weight cache (integrity-checked on every read), scratch (encrypted
  with a key born in the VM, lost on reboot)
```

**What is measured, and how a relying party knows what runs:**

| Register | Covers | Who computes the expected value |
|---|---|---|
| MRTD | initial TD construction including virtual firmware (TDVF/OVMF); profile-specific | bare metal: `nib measure`; GCP: verified applicable endorsement. An observed value is an observation-only lab pin, not an independently predicted or endorsed measurement |
| RTMR0 | firmware configuration and ACPI tables. Depends on the VM shape (vCPUs, memory, devices) and the QEMU version | `nib measure` per hardware profile (fixed QEMU command line) |
| RTMR1, RTMR2 | the boot path: our Unified Kernel Image (kernel, initrd, command line). The command line carries the dm-verity root hash of the read-only root filesystem | `nib measure` from the built image |
| RTMR3 | runtime events (reserved; not used in v1) | — |

This is the intended mapping: P3/P5 must replay the actual event log and prove
the mapping for each firmware/launch profile. An observed quote must not be
used as its own independent prediction. Observation-only lab results do not
satisfy prediction/reproducibility criteria and cannot silently become release
acceptance policy. See [measurement contract](docs/architecture.md#measurement-contract).

The **root filesystem is read-only and dm-verity protected**, and its root hash
is in the measured kernel command line. That one value therefore pins
everything in the image: the engine container digest, the model pack (Hugging
Face revision, per-file SHA-256, weight-volume verity root hash), engine flags,
the egress allowlist and `trcs`.

**A release is one image per (model pack, engine version, hardware
profile).** Changing any of them produces a new measurement, which TR pins
explicitly, the same way it pins PCR0 for its AWS enclaves.

**Data required to remain within the node's private CPU/GPU boundary** (the
buyer and trusted gateway necessarily also handle request content):
- prompts and completions;
- the TLS private key;
- KV cache and any engine scratch.

Model weights are public. We protect their **integrity**, not their secrecy:
dm-verity verifies weight blocks on device reads. Release rootfs and weight
mounts must forbid `check_at_most_once` and `ignore_corruption`
([kernel documentation](https://docs.kernel.org/admin-guide/device-mapper/verity.html)).
Post-mount bit-flip tests must force a new device read, including after a prior
successful read; no unverified cache path may bypass the mapping. Verity does
not itself prevent rollback to an older accepted release.

**GPU attestation.** Two layers, both required:
1. At boot, `nib-gpu-gate` collects evidence for every GPU (and NVSwitch where
   the mode requires it) and verifies it inside the VM against NVIDIA's
   reference measurements (RIM) and certificate revocation (OCSP). Only then
   does it set the GPUs' ready state. On failure it powers the VM off.
2. On every attestation request, `trcs` adds fresh GPU evidence bound to the
   relying party's nonce (`gpu_nonce` per the protocol), so TR can re-verify
   itself.

The verifier must authenticate every expected device's vendor report, nonce,
identity, accepted mode/firmware, RIM and revocation status, plus required
fabric evidence. A JSON envelope nonce, claimed success or CLI exit code zero
is not GPU verification. Until the real P3 appraiser exists, production
verification returns fail/partial with GPU verification incomplete; positive
fake results exist only in `nibfake` builds and never imply confidentiality.
No runtime bypass or unsafe injectable verifier may turn a release verdict green.

Tooling: NVIDIA's C++ Attestation SDK and its `nvattest` CLI, pinned. nvtrust's
Python SDK and local verifier are deprecated. RIM and OCSP fetches go to NVIDIA
services, and an NGC API key is advised. Commercial use of these services needs
NVIDIA's Enterprise license (G-NVIDIA-LICENSE).
The C++ SDK source itself is Apache-2.0; source licensing is distinct from
commercial CC services/driver conditions. The reviewed release `2026.10.02`
lists `nvattest` 2.0.0; use a pinned legacy v1 evidence contract initially,
not experimental CoRIM v2 without review. See
[tooling sources and licensing](docs/hardware.md#licenses-and-action-gates).

## 6. Repository layout (created in P0)

```
cmd/nib/                   operator CLI (Go; nib = neocloud-in-a-box): image, measure, host,
                           model, verify, catalog, evidence
internal/                  shared Go packages (no network code inside guest packages)
guest/                     everything that runs inside the TD: systemd units, nib-gpu-gate,
                           nib-weights, nftables policy, engine launch wrapper
                           (Go: standard library ONLY, enforced by test like trcs)
image/                     mkosi configuration: Ubuntu 24.04 TD guest → UKI + dm-verity rootfs;
                           `release` and `debug` variants (debug never accepted)
host/                      bare-metal host: BIOS checklists per vendor, TDX host setup,
                           QGS/PCCS, GPU CC mode, VFIO, QEMU launch per hardware profile
platforms/gcp/terraform/   GCP lab: network, firewall, service account, instance templates
models/<model>/<pack>/     model packs: pack.yaml + files.sha256 (+ engine profiles)
profiles/                  hardware profiles: b200x8-tdx.yaml, h200x8-tdx.yaml, h100x1-gcp-tdx.yaml,
                           b200x8-gcp-plain.yaml (P2 only, non-confidential)
bench/                     benchmark harness (Python + uv) and scenarios
catalog/                   pricing config → generated catalog.v2.json (public declaration)
third_party/               vendored patches with their licenses (e.g. SGLang CC patch)
evidence/                  committed run evidence (see AGENTS.md)
docs/                      architecture, threat model, sizing, hardware, runbooks,
                           decisions/ (ADRs), gates.md
```

## 7. Formats (frozen in P0; change only through an ADR)

**Model pack: `models/<model>/<pack-id>/pack.yaml`**

```yaml
schema: nib.model-pack/v1
id: deepseek-v4.1-flash-1017@1              # <model>@<pack revision>
hf_repo: deepseek-ai/DeepSeek-V4.1-Flash-1017
hf_revision: <40-hex commit sha>             # never a branch name
license: MIT
served_model_name: deepseek-v4.1-flash-1017   # native id on /v1/models
tr_model_id: deepseek/deepseek-v4.1-flash-1017
files: files.sha256                          # "<sha256>  <bytes>  <path>" per file, sorted
total_bytes: 510311045295                     # all files, not only weight shards
weights_volume:                              # deterministic erofs image + dm-verity (P5)
  format: erofs
  verity_root_hash: <hex>                    # filled in P5; empty until then
engine_profiles:
  b200x8-tdx:
    engine: vllm
    image: vllm/vllm-openai@sha256:<digest>
    args: [...]                              # every flag cites its source in a comment
    env: {VLLM_DISABLE_UVA: "1"}
    max_model_len: 1048576
    replicas: 2                              # data-parallel replicas behind one endpoint
```

**Hardware profile: `profiles/<id>.yaml`**: GPU model and count, CC mode
(`blackwell-mgpu-cc` | `hopper-ppcie` | `single-gpu-cc` | `none`), CPU
generation, the TD's vCPUs and memory, the exact QEMU and OVMF versions and the
QEMU argument template, and the PCI devices passed through (GPUs, NVSwitches).

**Release manifest: `release.json`**, signed with Sigstore cosign keyless from
CI in P8. It lists the image digests, UKI SHA-384, rootfs verity root hash, the
model pack id and verity root hash, and for each hardware profile the expected
MRTD and RTMR0–2. The TR verifier accepts measurements only from a release
manifest it has pinned.

**Evidence run: `evidence/.../run.json`** (see AGENTS.md):

```json
{"schema":"nib.evidence/v1","phase":"P2","task":"T3","git_sha":"…","command":"…",
 "started_utc":"…","ended_utc":"…","host":{"provider":"gcp","machine":"a4-highgpu-8g",
 "gpus":"8xB200","driver":"…","cuda":"…"},"result":"pass","notes":"…"}
```

## 8. Phases

Conventions: **G-x** is a human gate (see [docs/gates.md](docs/gates.md)), and
**AC** is an acceptance criterion. Every hardware AC needs an `evidence/` artifact.

**Dependencies.** Phases run in parallel when their dependencies are met. When
a task is blocked on a gate, move to the next unblocked task.

| Phase | Can start when |
|---|---|
| P0 | now |
| P1 | P0.T1–T3 merged |
| P2 | P0.T4 merged, G-GCP-PROJECT and G-GCP-QUOTA granted |
| P3 | P1.T2 and P1.T4 merged, H100 quota granted |
| P4 | P3 exit |
| P5 | P3 exit, P4.T2 merged, G-HW-RENT, G-INTEL-PCS, G-NGC |
| P6 | P4 and P5 exits |
| P7 | P2.T6 and P6 exits, a second server |
| P8 | T1–T3 after P3 exit; release signing before P6 |
| P9 | P6 exit |

### P0: Foundations (no hardware)

- **T1. Skeleton and CI.**
  - Repo layout per §6; a Makefile with `check`, `test`, `test-e2e`, `lint`.
  - GitHub Actions: gofmt/vet/staticcheck, `go test ./...`, shellcheck,
    yamllint, a markdown link check, gitleaks, and a test that guest Go packages
    import only the standard library.
  - CODEOWNERS = @jperla.
  - AC: CI green on an empty skeleton, and gitleaks fails on a planted fake
    key (positive control, then removed).
- **T2. Docs.**
  - `docs/architecture.md` (expanded §5); `docs/sizing.md` (§3 with the
    arithmetic); `docs/hardware.md` (BIOS, CPU, GPU, CC-mode matrix with
    sources).
  - `docs/threat-model.md`: start from the confidential stack's threat model and
    add the host as an adversary for weights (dm-verity), the Engram/UVA
    access-pattern leak, debug images, and physical attacks (TEE.fail, DDRop)
    stated as out of scope with the custody mitigation.
  - `docs/decisions/0001-0007` recording D1–D7 from §9.
  - AC: every external claim links a source.
- **T3. Schemas and validators.**
  - JSON Schemas for the model pack, hardware profile, release manifest and
    evidence run.
  - `nib lint` validates every such file in the repo and runs in CI.
  - AC: unit tests with valid and invalid fixtures, one invalid fixture per
    rule.
- **T4. Model packs for both models.**
  - `models/deepseek-v4.1-flash-1017/1/` and `models/glm-5.3/1/`, with the
    pinned `hf_revision` and the `files.sha256` built from the Hugging Face API
    (`/api/models/<repo>/tree/<rev>` lists every file's size, and for LFS files
    the SHA-256 as `lfs.oid`; verify the field names against the live API).
    Small non-LFS files (config, tokenizer, code) are downloaded at the pinned
    revision and hashed.
  - `nib model check-remote <pack>` re-fetches and compares.
  - Engine profiles copy the upstream recipes (vLLM recipes repo, SGLang
    cookbook), with a source comment per flag.
  - AC: `nib model check-remote` passes for both, and complete-manifest
    `total_bytes` equals **510,311,045,295** and **755,663,689,302** at revisions
    `1bae0e1088993b9b82de08f59de5be5a34e42170` and
    `ba32f8c19c8738e2c57c71798c601d477a7bf844`, respectively. The old values
    510,296,708,312 and 755,632,050,320 are **weight shard subtotals**, not pack
    totals. Preserve non-weight files and hash them; metadata arithmetic alone
    is not pack validation. Primary pinned trees and arithmetic are in
    [docs/sizing.md](docs/sizing.md#units-and-source-bytes), based on P0.T4's
    source audit. This correction does not itself mark P0.T4 accepted.
- **T5. Evidence tooling.**
  - `nib evidence new <phase> <slug>` scaffolds `run.json`.
  - `scripts/scrub-secrets.sh` redacts tokens, keys and IPs listed in a local
    deny file.
  - AC: tests show a planted token is scrubbed.

**Exit P0:** T1–T5 merged and CI green.

### P1: GPU-free end-to-end node (CI only)

- **T1. Guest services, fake mode.**
  - `guest/` systemd units and Go helpers: `nib-gpu-gate`, `nib-weights`, the
    engine wrapper, nftables policy.
  - Each has a `fake` backend compiled only under build tag `nibfake`. Release
    builds must not contain it; CI checks the binary's symbols.
  - AC: unit tests; the release build contains no `nibfake` symbols.
- **T2. trcs integration.**
  - Build `trcs` as a pinned, hash-checked binary dependency from
    trustedrouter-confidential-stack. Its implementation is internal Go
    packages, not an assumed public library API; do not copy them here.
  - The changes trcs needs are pull requests **to that repo**, one feature
    each, with tests:
    - (a) an `X-Request-Id` response header;
    - (b) admission control: at most N in-flight requests, then 429 with
      `Retry-After`, and pass the engine's 503 through with `Retry-After`;
    - (c) evidence for N GPUs plus NVSwitches as separate tagged bundles;
    - (d) a health endpoint that reveals nothing about requests.
    - (e) fixed route-class logging instead of user-controlled raw URL paths,
      including error/debug paths, with adversarial-path privacy tests.
    - (f) any fake TSM/agent backend must be explicitly fake and compile-tagged
      out of releases, paired only with a fake verifier. Do not export unsafe
      verifier injection as a production API.
  - AC: each trcs PR merged with tests; this repo pins the merge commit.
- **T3. `make test-e2e`.**
  - Runs in a container: fake engine (an OpenAI-compatible stub with streaming,
    usage and errors), explicit fake evidence sources, fake GPU bundles, real
    pinned `trcs`, and a `nibfake` verifier.
  - Runs `trustedrouter-provider-check --tier 4` against it, then `nib verify`.
  - AC: provider-check exits 0; the `nibfake` verifier passes; CI runs it on
    every PR. This is fake-mode integration only. The release verifier must
    fail/return partial until all real appraisal requirements are implemented.
- **T4. `nib verify` v0.**
  - Fetch evidence on the same TLS connection with a fresh nonce.
  - Check `report_data` = SHA-256(SPKI) ‖ SHA-256("tr-attestation/v0"‖0x00‖nonce)
    using the protocol's golden vectors.
  - Parse the TDX quote and verify its signature chain against Intel PCS
    collateral with `github.com/google/go-tdx-guest`.
    Enable `GetCollateral` and `CheckRevocations` explicitly (both default off),
    retain TCB-status checking, and enable `EnableTdDebugCheck` and
    `EnableTdMigratableCheck` in policy validation. Use nonempty expected report
    data/measurement policy, supported quote-format checks and bounded,
    context-aware collateral retrieval. Source: [verification contract](docs/architecture.md#evidence-and-relying-party-verification).
  - Compare MRTD/RTMR0–2 to a release manifest.
  - GPU bundles: parse and check binding to `gpu_nonce`; real signature
    verification arrives in P3. This structural check never grants a production
    GPU-verified/confidential verdict; positive fake testing is `nibfake` only.
  - AC: mutation tests, one per field, each of which must fail verification:
    wrong SPKI, stale nonce, wrong MRTD, each wrong RTMR, a truncated quote and
    a wrong GPU nonce.
- **T5. Catalog declaration.**
  - `catalog/pricing.yaml` → `nib catalog` → `catalog.v2.json`, validated
    against TR's pinned `catalog.v2.schema.json`.
  - Publishing it is gate G-PUBLISH (GitHub Pages of this repo is the default
    location).
  - AC: the provider checker's Tier 1 with `--catalog-url` pointing at a local
    file server passes in e2e.

**Exit P1:** `make test-e2e` green in CI with provider-check Tier 4 and
`nibfake` verification, and all mutation tests failing as designed. No P1 fake
or partial result claims confidentiality or hardware validation; the release
verifier remains fail-closed while GPU cryptographic appraisal is incomplete.

### P2: Model bring-up on GCP, non-confidential

Gates:
- **G-GCP-PROJECT**: create the lab project.
- **G-GCP-QUOTA**: 8× B200 in one region with A4 capacity (Spot or flex-start).
- **G-RUN**: approve each run's hours and cost.

- **T1. Terraform `platforms/gcp/terraform`.**
  - VPC, a firewall allowing only SSH from the operator's IP (443 closed), a
    service account, and an instance template for `a4-highgpu-8g` (8×B200) with
    local SSD, Spot or flex-start, and a `count` variable (0 or 1).
  - Apply means start; `count=0` means stop.
  - AC: `terraform plan` output is attached to the PR; nothing is applied
    without G-RUN.
  - If B200 quota is not granted within a week, the fallback is the same
    scripts on Modal (`gpu="B200:8"`, $6.25 per GPU-hour, cash). That needs
    G-RUN with a cash estimate.
- **T2. Plain host bootstrap** (`platforms/gcp/bootstrap.sh`).
  - NVIDIA driver and CUDA per the pack, a container runtime with the NVIDIA
    toolkit, and weights downloaded to local SSD at the pinned revision and
    checked against `files.sha256`.
  - AC: evidence shows every file hash matches.
- **T3. DeepSeek-V4.1-Flash-1017 serve and correctness.**
  - vLLM per the recipe: DP2 × TP4, **Engram on GPU (no UVA)**, which is the
    confidential setting. Also run Engram offload once, for the record only.
  - provider-check Tiers 1–4 direct to the node.
  - A quality smoke: the same 200-question GSM8K and MMLU-Pro subsets, greedy
    decoding, run with `lm-eval` against our node and against the vendor's
    first-party API as the reference (a few dollars; G-RUN). Within ±2 points.
  - AC: provider-check exits 0, and the smoke result is within tolerance or
    explained.
- **T4. Benchmarks** (`bench/`).
  - Three scenarios: chat 2K/512; agent 32K/1K with 80% prefix reuse;
    long context 128K/1K.
  - Concurrency 1, 8, 32, 64, 128, 256.
  - Record TTFT p50/p95, TPOT p50/p95, output tok/s per GPU and error rate.
  - Use vLLM, plus the SGLang preview image where it runs.
  - AC: raw JSON plus a generated `docs/benchmarks/p2.md`.
- **T5. GLM-5.3 on the same machine.**
  - FP8 TP8 on vLLM ≥0.29 and SGLang ≥0.5.20 (DP-attention mode), 1M context
    with FP8 KV.
  - Then `nvidia/GLM-5.3-NVFP4` with the same quality smoke and benchmarks.
  - AC as T3 and T4.
- **T6. Decisions.**
  - ADRs for: the engine per model; parallelism; KV dtype; speculative
    decoding on or off; NVFP4 or FP8 for GLM-5.3; the served context length.
  - Engine profiles in the packs are finalized and pinned by digest.
  - **The default engine is vLLM for both** (one engine in the trusted
    computing base). SGLang only if it wins by >25% at equal latency in T4/T5.
- **T7. Teardown.** `count=0`, and evidence of zero running instances and the
  hours used.

**Exit P2:** both models served on 8×B200 with provider-check green, quality
smokes in tolerance, benchmarks committed, packs final.

### P3: Confidential pipeline on GCP (1×H100, TDX, small model)

**2026-10-19 scope update:** Joseph requested a smaller Qwen first and complete
project separation from TrustedRouter. [P3.T7](docs/operators/qwen-pilot.md) is a
local real-model API-plumbing pilot that can run before cloud/GPU gates. It uses
no fake engine and makes no confidential claim; see [D8](docs/decisions/0008-small-qwen-pilot.md).
Subsequent cloud work must use a dedicated Hixtube project, never production
`quill-cloud-proxy`. The confidentiality and listing gates below remain intact.

GCP's confidential GPU VMs have one GPU (`a3-highgpu-1g`: 1×H100 80GB on
TDX; Spot or flex-start; zones us-central1-a, us-east5-a, europe-west4-c). Our
two models do not fit, so this phase proves **the pipeline**: our image, real
TDX quotes, real GPU CC evidence, `trcs`, `nib verify` and TR's verifier
(P4). It uses a small pinned model chosen in T3.

Gates: G-GCP-QUOTA (1× H100 Spot), G-NGC, G-RUN.

- **T7. Local real-model pilot (run first).** Pinned Qwen3-0.6B Q8_0, CPU inference through
  standard-TLS `trcs`, provider-check Tiers 1-4, API-key rejection, token usage,
  streaming, upstream failure, process cleanup, committed-source evidence.
  This is not a GPU, image measurement, attestation or billing test. It changes
  neither the release verifier nor the production engine.

- **T8. Private offline image artifact (ungated local preparation).** Optional
  [image export](docs/operators/qwen-export.md) from T7's existing committed-source
  Linux proof, using the exact tested image ID before cleanup. Bound save/load,
  validate archive identity/layers, retain source/context/model/base pins and
  original license/notice files, and publish a new owner-only bundle atomically
  only after proof, load verification and cleanup pass. Negative tests cover
  stale input, timeout, corruption, collision, symlink and cleanup failures.
  This is not a public image, deploy driver, cloud run or confidential claim.
  Implementation/unit validation is distinct from the pending real combined-head
  Docker proof/export; no success evidence is fabricated. Public redistribution
  and deployment remain separately gated.

- **T1. Guest image v0** (`image/`, mkosi).
  - Ubuntu 24.04 TDX guest kernel with configfs-tsm.
  - NVIDIA open kernel driver from the R595 trusted-computing release (GCP's
    guide requires ≥580) with the Linux Kernel Crypto API (LKCA) enabled and
    `uvm-persistence-mode`, per GCP's guide. One guest image serves GCP and
    bare metal.
  - NVIDIA's `nvattest` CLI / C++ Attestation SDK (pinned; not the deprecated
    Python SDK) for `nib-gpu-gate` and per-request GPU evidence; `trcs`;
    podman.
  - Output: a UKI plus a dm-verity root filesystem, in `release` and `debug`
    variants.
  - CI job: two clean builds must produce an identical UKI hash and verity root
    hash (reproducibility).
  - AC: the reproducibility job is green.
- **T2. Boot on GCP.**
  - Import the image with `TDX_CAPABLE,UEFI_COMPATIBLE,GVNIC` guest OS
    features, and add a Terraform template for an `a3-highgpu-1g`
    `--confidential-compute-type=TDX` Spot VM.
  - **If GCP refuses a custom image for GPU confidential VMs, stop and report.**
    The documented fallback is GCP's Ubuntu image plus our packages. That proves
    the GPU and attestation plumbing but not our measurements, and must be
    labelled that way.
  - AC: serial log and `run.json` show a TDX guest with the GPU in CC mode
    (`nvidia-smi conf-compute -f` reports CC on, and the ready state is set by
    the gate).
- **T3. Serve and attest.**
  - Pick a small model that fits 80 GB in FP8 (record the choice in an ADR; it
    is for testing and is never listed).
  - `trcs` serves `tr-attestation/v0` with a real TDX quote and real GPU
    evidence.
  - AC: the evidence bundle is committed (it contains no secrets).
- **T4. `nib verify` v1, real hardware.**
  - Verify the TDX quote signature against Intel PCS collateral, MRTD against
    Google's firmware endorsement (or the recorded observed value, flagged as
    such), RTMR1/2 against `nib measure`, and GPU evidence (certificate chain
    to NVIDIA's root, RIM measurements, OCSP).
    Observation-only MRTD pins need explicit lab-policy acceptance, never count
    as independent prediction, and cannot silently become production policy.
    Require the enabled Intel collateral/CRL/debug/non-migratable policy checks
    from P1.T4 and authenticated vendor results for every assigned GPU. Unknown,
    missing or failed appraisal stays fail/partial, not confidential success.
  - AC: passes against the live node. Each mutation from P1.T4, replayed
    against real evidence, fails.
- **T5. `nib measure`.**
  - Compute the expected RTMR1/RTMR2 for our UKI as GCP's firmware measures it.
  - AC: equal to the observed values from T3.
- **T6. Through the front door.**
  - provider-check Tiers 1–4 against the attested endpoint.
  - The same small model on a non-CC `a3-highgpu-1g` for a CC-overhead number.
  - AC: provider-check exits 0; overhead recorded.

**Exit P3:** a real TDX + H100 CC node that `nib verify` accepts, every tamper
case rejected, and evidence committed. Tear down after the run.

### P4: TrustedRouter side, a new provider kind (`tr-attestation` providers)

Today TR has no generic verifier. Each confidential provider (Tinfoil, NEAR,
Chutes, Privatemode) has a hand-written one in quill-cloud-proxy. This phase
adds one verifier for **any** operator running this stack. That is the "new
kind of provider".

Work happens in quill-cloud-proxy and quill-router (public repos) through
their normal PR process. **Merging to quill-cloud-proxy main deploys the
production enclave, which is gate G-TR-PROD.**

- **T1. Protocol freeze.**
  - Fold P3's lessons into `tr-attestation/v0`, or define v1 in
    trustedrouter-confidential-stack: multi-GPU bundle layout, NVSwitch
    evidence, measurement-manifest reference.
  - AC: spec PR merged with updated golden vectors.
- **T2. Generic verifier in quill-cloud-proxy.**
  - A sidecar following `sidecar/near_ai_verify.go`, reusing its TDX and NVIDIA
    code: the same-connection nonce fetch, `report_data` binding, quote
    signature, measurement policy and GPU evidence.
  - Produces `{policy, verified_at, expires_at}` so receipts say `tee-verified`.
  - Re-attests at least every 10 minutes and fails closed.
  - Policy is per provider: the release manifests TR has pinned (widen, then
    narrow on upgrade, like the AWS PCR0 pins).
  - AC: unit tests use P3's real evidence as fixtures; mutation tests mirror
    P1.T4.
- **T3. quill-router support.**
  - A `Provider` entry type for `tr-attestation` operators (flags: TEE + E2EE +
    ZDR when TLS ends in the VM and the verifier passes).
  - The Catalog v2 refresher reads our declaration, plus a PONG canary.
  - A marketplace section on `/providers/marketplace`: "Run your own
    confidential node", linking this repo.
  - AC: tests per the repo's conventions; `scripts/update_model_changes.py`
    run if the catalog changes.
- **T4. Internal-only route.**
  - Point an `internal_only` model at the P3 node (as with `fortanix-test`) and
    send canary traffic through the gateway.
  - AC: PONG through the gateway, with the receipt showing `tee-verified`.
    Spending TR credits needs G-TR-CREDITS.

**Exit P4:** TR verifies a `tr-attestation` node generically, and an internal
route is green.

### P5: First bare-metal confidential node, DeepSeek-V4.1-Flash-1017 on 8×B200

Gates:
- **G-HW-RENT**: an 8×B200 TDX server; provider per `docs/hardware.md`.
- **G-INTEL-PCS**: Intel PCS API key for PCCS.
- **G-NVIDIA-LICENSE**: confirm NVIDIA's terms for production CC.
- G-NGC, G-RUN.

- **T1. Host preparation** (`host/`). Follow NVIDIA's *Deployment Guide for
  Intel TDX and AMD SEV-SNP* (v7.1 or newer) and the R595 trusted-computing
  release notes, step by step, citing the section for every command.
  - A BIOS checklist for the actual server vendor: TME, TME-MT, TDX, SEAM
    loader, SGX, a non-zero TDX key split, the 46-bit physical-address limit
    off, VT-d/IOMMU on.
  - Qualify the intended Ubuntu 26.04 host and pin its exact kernel/QEMU/OVMF
    against the actual hardware; do not label it NVIDIA-validated solely from
    R595's Ubuntu 25.10 reference. Set up Intel QGS and
    PCCS (quote generation; needs G-INTEL-PCS).
  - Driver and GPU firmware at the R595 trusted-computing versions. Use NVIDIA's
    tool to put **all 8 GPUs** into Blackwell multi-GPU CC mode (on the H200
    fallback SKU: PPCIe mode). Fabric Manager goes where the guide says.
  - Bind the GPUs (and NVSwitches if the mode passes them through) to VFIO; set
    up the NVMe weight-cache volume.
  - `nib host check` verifies each item and writes evidence.
  - AC: `nib host check` all green on the real server.
- **T2. TD launch.**
  - `host/launch-td.sh <profile>` runs the exact QEMU command line from the
    hardware profile with our pinned OVMF and the image from P3, extended for
    multi-GPU.
  - The host forwards TCP 443 to the TD and nothing else.
  - AC: the TD boots with 8 GPUs in CC mode, and `nib-gpu-gate` passes for all
    GPUs (and NVSwitches where applicable). Model load time is recorded,
    because CC bounce buffers make loading much slower.
- **T3. Weight volume with dm-verity.**
  - `nib model build-volume <pack>` downloads at the pinned revision, checks
    the SHA-256 of every file, builds a deterministic erofs image (fixed
    timestamps, UUID and ordering) and its verity tree, and prints the root
    hash.
  - Run it twice on different machines; both root hashes must be equal. Pin
    the value in `pack.yaml`.
  - In the TD, open the cached volume with the pinned root hash; a host-side
    bit flip must cause a read error, never wrong weights.
  - AC: reproducibility proven; bit-flip test evidence.
- **T4. Serve and tune for CC.**
  - vLLM DP2 × TP4 per the P2 profile, with CC settings: `VLLM_DISABLE_UVA=1`,
    Engram on GPU, no NVLS or multicast all-reduce, no `cudaHostRegister` paths,
    and device-to-host copies off the hot path.
  - Learn from the confidential.ai SGLang patch, Tinfoil's vLLM config and
    Tinfoil's CC-overhead write-up. Carry patches in `third_party/` only with
    their license, and upstream them.
  - AC: provider-check Tiers 1–4 through the attested endpoint, and throughput
    within **10%** of P2 at the same configuration (tuned stacks lose 1–8%).
    If it is worse, stop and report the profile before moving on.
- **T5. Verify, measure, benchmark, soak.**
  - `nib measure` predicts MRTD and RTMR0–2 for this profile, and they equal
    the observed values.
  - `nib verify` passes.
  - Rerun the P2 benchmarks under CC and report the overhead.
  - 24-hour soak at 50% of measured peak: error rate below 0.5%, no memory
    growth.
  - Reboot twice: measurements stay identical and the weight cache is reused.
  - AC: evidence for each.
- **T6. Release manifest v1** for profile `b200x8-tdx`, built by CI and signed
  (P8.T2 if ready; otherwise attached to the PR unsigned and labelled as such).
  Publishing is gate G-PUBLISH.

**Exit P5:** an attested, soaked node serving DeepSeek-V4.1-Flash-1017
confidentially, with predicted measurements matching reality.

**P5 fallback platform: managed TDX (Phala Cloud, dstack).** Use it only if
G-HW-RENT has produced nothing in three weeks *and* Joseph approves.
- Run the same engine container and `trcs` as a dstack app on Phala's 8×B300
  GPU-TEE VMs.
- `trcs` already has a `dstack` evidence source (experimental).
- Measurements are then dstack's OS release plus our compose hash (RTMR3), not
  our image, and TR's verifier policy must say so.
- This gets a confidential route live sooner; it does not prove the box.
  P5 proper still has to happen.

### P6: List DeepSeek-V4.1-Flash-1017 on TrustedRouter

Gates: G-PRICE (Joseph sets prices; default proposal: match Tinfoil's
$0.65 in / $1.45 out per million tokens), G-TR-PROD, G-DNS.

- **T1. Application package** (what `/providers/marketplace` asks for):
  - the provider-check JSON report (Tiers 1–4);
  - attestation answers: verifier = `tr-attestation`, measurements = release
    manifest URL, key binding = SPKI in `report_data`;
  - a ZDR statement (nothing logged; point to the code);
  - regions, contacts, prices, the catalog URL and a scoped, revocable API key.
- **T2. TR configuration PRs:** the P4 verifier policy pins the P5 release; the
  `Provider` entry; a refresher on our catalog; a canary. Ship `internal_only`
  first, then public.
- **T3. Watch 72 hours:** availability ≥ 99%, gateway error rate < 1%, TTFT p95
  within 20% of the P5 benchmark at the same load.

**Exit P6:** the confidential route is public on TR and the marketplace lists
the provider.

### P7: GLM-5.3 node

The same steps as P5 and P6 on a second 8×B200 server (G-HW-RENT): the pack from
P2.T6 (FP8 or NVFP4), TP8 over the whole node, 1M context, release, soak,
listing (G-PRICE: proposal between Phala's $0.84/$2.64 and Tinfoil's
$1.80/$5.75).

**Exit P7:** a confidential `z-ai/glm-5.3` route is public on TR.

### P8: Releases, reproducibility, transparency (can start after P3)

- **T1. Two-builder reproducibility.** Image, UKI and weight volumes rebuilt by
  CI and by a second machine with byte-identical results. Packages come from
  pinned snapshots (snapshot.ubuntu.com).
- **T2. Signing.** cosign keyless signing in GitHub Actions; Rekor entries for
  every release manifest. `nib verify` checks the signature and the transparency
  log inclusion.
- **T3. Trust page** (GitHub Pages, G-PUBLISH): releases, measurements per
  profile, how to verify, and what is not claimed.
- **T4. Upgrade runbook:** build → publish → TR widens its pins → roll nodes one
  at a time → TR narrows its pins. Mirror quill-cloud-proxy's AWS enclave
  release runbook. Add a CVE watch for the kernel, driver, engine and firmware,
  and a monthly rebuild.

### P9: Fleet and "in a box" for other operators

- Node inventory and an attest-by-IP reconciler that publishes DNS only for
  nodes that verify. TR's enclave DNS reconciler is the design reference.
- Health and alerting without request data; capacity reporting.
- An installer and operator guide: hardware bill of materials, provider list,
  step-by-step from a bare server to a TR listing, and a support matrix.
- NYTE hooks: publish capacity and price to the exchange (designed with NYTE
  when v1 is live).

## 9. Decisions (record each as an ADR in P0.T2)

| # | Decision | Why | Revisit when |
|---|---|---|---|
| [D1](docs/decisions/0001-one-node-one-trust-domain.md) | One node = one TDX VM = one model, single machine | deliberate project scope, not a universal whole-server CC requirement | reviewed multi-node confidential design |
| [D2](docs/decisions/0002-hardware-profile.md) | Hardware target 8×B200 + TDX Xeon; separately qualified 8×H200 fallback | candidate fit for both models; Hopper needs its own kernels/fabric tests | B300 price/availability |
| [D3](docs/decisions/0003-bare-metal-launch.md) | Canonical TDX host stack + explicit QEMU launch, no default dstack/Kubernetes/CoCo | fewer configuration paths to audit; image portability remains unvalidated; no external KMS | fleet scale (P9) |
| [D4](docs/decisions/0004-measured-guest-image.md) | mkosi, Ubuntu 24.04, UKI + verified rootfs per (pack, engine, profile, variant) | bind effective executable/configuration inputs through measured boot | image design changes |
| [D5](docs/decisions/0005-verified-model-volume.md) | deterministic erofs + dm-verity; model root in measured image | verified device reads; no unverified alternate path or permissive verity options | equivalent integrity design |
| [D6](docs/decisions/0006-inference-engine.md) | vLLM default; SGLang only by ADR with >25% measured win at equal latency/quality/security | one default engine to review | P2 results |
| [D7](docs/decisions/0007-attestation-protocol.md) | pinned trcs binary; boot GPU gate plus fresh evidence per attestation request | reuse protocol; full CPU/GPU/fabric cryptography required for confidential acceptance | P4.T1 |

## 10. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| No provider rents bare metal with CC enabled in the BIOS | P5 cannot start | start G-HW-RENT now with three or more candidates (§3.4); Phala fallback platform; buy |
| Multi-GPU CC on Blackwell has driver bugs or immature tooling | P5 slips | GA since R590; Tinfoil runs 4×B200 TDX in production; fallback D2 (H200 PPCIe + GLM first) |
| Hopper PPCIe has no key rotation: when the encryption IV space runs out the app crashes and the VM must restart | outages on the H200 fallback | scheduled rolling restarts; Blackwell first |
| Model loading is far slower under CC (bounce buffers) | long boots and upgrades | measure in P5.T2; large aligned parallel copies; keep nodes up and roll one at a time |
| Engines are slow or break under CC (UVA, NVLS, device-to-host stalls) | lower throughput | confidential.ai patch, Tinfoil's settings; measure overhead in P5.T5 |
| GCP refuses custom images on GPU confidential VMs | P3 proves less | documented fallback, labelled; real measurements proven on bare metal in P5 |
| RTMR0 drifts with the QEMU, OVMF or VM shape | verifier rejects nodes after host updates | pin QEMU/OVMF per profile; `nib measure` in CI; upgrade = new release |
| Hopper kernels unmaintained (FlashMLA dropped Hopper 2026-09-30) | H200 fallback degrades | B200 first; pin the engine version on Hopper |
| Physical attacks on TEEs (TEE.fail, DDRop) | a hostile datacenter can break TEEs | out of scope in the threat model; providers with audited custody; hardware-ID allowlist (confidential stack) |
| NVIDIA CC licensing: "Commercial use of NVIDIA Confidential Computing requires a commercial Enterprise Product license" (terms 2026-07-09); no public price | legal or cost | quote requested now (G-NVIDIA-LICENSE); development and PoC use is free |
| Price competition (Tinfoil $0.65/$1.45 on DeepSeek) | thin margin | P2 benchmarks set our cost per token before G-PRICE |
| TR-side changes collide with other TR deploys | delays | normal TR deploy coordination; P4 changes are additive and fail closed |

## 11. Open questions for Joseph

1. **Resolved 2026-10-19:** provider **Hixtube**, slug `hixtube`, as named by
   Joseph. The reusable project remains Token Exchange Neocloud-in-a-Box.
2. Rent or buy for P5 (proposal: rent month to month from a CC-capable
   provider; decide on buying an HGX B200 after 30 days of P6 utilization).
   Which providers to contact first (proposal: Hydra Host, ServerMO, Spheron,
   Sesterce).
3. Prices for both routes (proposals in P6 and P7).
4. **Resolved 2026-10-19:** `hixtube.com`; Joseph reports delegating it to Google
   Cloud DNS. Planned first-node origin: `api.hixtube.com`. See the
   [Hixtube launch checklist](docs/operators/hixtube.md); delegation alone does
   not establish a serving endpoint or satisfy the hardware launch gates.
5. Does NYTE need anything from v1 beyond the TR listing? (Proposal: no; P9.)

## 12. Sources

Models
- DeepSeek-V4.1-Flash-1017: https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017 ;
  sizes from https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash-1017/tree/main ;
  release note https://api-docs.deepseek.com/news/news261017 ;
  tech report https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/blob/main/DeepSeek_V41_Tech_Report.pdf
- vLLM recipe: https://github.com/vllm-project/recipes/blob/main/models/deepseek-ai/DeepSeek-V4.1-Flash.yaml ;
  SGLang cookbook: https://github.com/sgl-project/sglang/blob/main/docs/cookbook/autoregressive/DeepSeek/DeepSeek-V4_1.mdx
- FlashMLA (Hopper dropped 2026-09-30): https://github.com/deepseek-ai/FlashMLA
- Throughput: https://inferencex.semianalysis.com ; https://lambda.ai/inference-models/deepseek-ai/deepseek-v4.1-flash
- Tinfoil's confidential config: https://github.com/tinfoilsh/confidential-deepseek-v4-1-flash
- GLM-5.3: https://huggingface.co/zai-org/GLM-5.3 (config.json, LICENSE) ; https://docs.z.ai/guides/llm/glm-5.3 ;
  vLLM recipe https://github.com/vllm-project/recipes/blob/main/models/zai-org/GLM-5.3.yaml ;
  SGLang cookbook https://github.com/sgl-project/sglang/blob/main/docs/cookbook/autoregressive/GLM/GLM-5.3.mdx ;
  NVFP4 https://huggingface.co/nvidia/GLM-5.3-NVFP4
- Provider prices: https://openrouter.ai/api/v1/models/deepseek/deepseek-v4.1-flash-1017/endpoints ;
  https://openrouter.ai/api/v1/models/z-ai/glm-5.3/endpoints ; https://inference.tinfoil.sh/v1/models ;
  https://www.privatemode.ai/pricing

Confidential computing
- GCP confidential VMs with GPUs: https://docs.cloud.google.com/confidential-computing/confidential-vm/docs/supported-configurations ;
  https://docs.cloud.google.com/confidential-computing/confidential-vm/docs/create-a-confidential-vm-instance-with-gpu
- confidential.ai: https://github.com/confidential-dot-ai/confidential-inference (docs/verification.md, SGLang CC patch)
- Our stack: https://github.com/Lore-Hex/trustedrouter-confidential-stack (docs/attestation-protocol.md, docs/hardware.md, docs/threat-model.md)
- Hydra Host TDX bare metal: https://hydrahost.com/post/confidential-ai-bare-metal-intel-tdx/
- NVIDIA R595 trusted-computing release notes: https://docs.nvidia.com/595trd1-trusted-computing-solutions-release-notes.pdf ;
  R590 (Blackwell multi-GPU CC GA): https://docs.nvidia.com/590trd1-trusted-computing-solutions-release-notes.pdf
- NVIDIA CC deployment guide for TDX and SEV-SNP (v7.1): https://docs.nvidia.com/cc-deployment-guide-tdx-snp.pdf
- NVIDIA secure AI whitepaper: https://docs.nvidia.com/nvidia-secure-ai-with-blackwell-and-hopper-gpus-whitepaper.pdf
- NVIDIA CC license terms: https://www.nvidia.com/en-us/agreements/enterprise-software/product-specific-terms-for-confidential-computing/
- nvtrust (`nvattest`, C++ SDK): https://github.com/NVIDIA/nvtrust ; NRAS: https://docs.nvidia.com/attestation/cloud-services/latest/nras/nras_introduction.html
- Intel Trust Authority GPU attestation: https://docs.trustauthority.intel.com/main/articles/concept-gpu-attestation.html ;
  PCCS: https://cc-enabling.trustedservices.intel.com/intel-sgx-tdx-pccs/02/overview/
- Canonical TDX: https://github.com/canonical/tdx
- CC overhead on 8×B200 (NVIDIA, 2026-09-22): https://developer.nvidia.com/blog/enabling-private-high-performance-production-ai-inference-with-nvidia-confidential-computing/ ;
  Tinfoil overhead study: https://tinfoil.sh/blog/2026-06-23-confidential-computing-overhead
- Rental prices: https://cloud.phala.com/about/pricing ; https://lambda.ai/pricing ; https://www.hyperstack.cloud/gpu-pricing ;
  https://www.coreweave.com/pricing ; https://modal.com/pricing ; https://getdeploying.com/reference/cloud-gpu/nvidia-b200
- CC-ready bare metal: https://www.servermo.com/blogs/confidential-ai-bare-metal/ ;
  https://www.spheron.network/blog/confidential-gpu-computing-nvidia-tee-encrypted-vram/
- Hardware purchase and colocation: https://www.amcompute.com/appraisals/b200 ;
  https://www.cbre.com/insights/reports/global-data-center-trends-2026

Serving and performance
- Modal, trillion-parameter serving: https://modal.com/blog/trillion-tokens-trillion-parameters
- GPU performance reading list: https://github.com/wafer-ai/gpu-perf-engineering-resources

TrustedRouter
- Marketplace: https://trustedrouter.com/providers/marketplace ; provider checker: https://github.com/Lore-Hex/trustedrouter-provider-check
