# P2.T3: Explicit B200 Uncensored Laboratory Profile

This is a source-backed, software-tested candidate, not a B200 hardware result.
The replacement provider's GPU, CC, driver, OS, CPU and RAM labels are not
verified observations. No provisioning, weight transfer or GPU execution is
authorized by this change.

## Fixed Selection

The additional preparation flag requires the existing uncensored identity:

```sh
python3 -I -S -B host/record-deepseek-lab.py \
  --prepare-local-correctness-b200-dp2 "$NEW_PRIVATE_BUNDLE" \
  --model-profile uncensored --tokenizer-parity --tokenizer-materialization \
  --runtime-observation --stream-cohort --stream-profile chat-2k-512-c1-600s
```

Preparation is offline and source-bound. This command is not a launch command.
The B200 flag rejects absent/original model selection, DP1, nonconfidential
mode and provider-conformance preparation. It does not generally enable B200
for previously B300-only options. Existing original-model defaults and B300
selection retain their prior payload encoding and command settings for the same
source inputs. Updated source hashes necessarily produce new bundle receipts.

The unchanged `deepseek_model_profile.py` binds the exact 79-file checkpoint,
revision `c19ca5ca4eaeb79d29fdf08aea8327da95b1116a`, byte count
510,311,056,155 and manifest. Full identity, manifest, helper-source and served
name validation remains mandatory at the host/runtime boundaries. Optional
parity, observation and stream eligibility checks do not authenticate a model
identity by themselves. No launch-time family or model override is accepted.

## Source And Limits

The historical [Tinfoil B200 release configuration at
29bc3ea355129b1dda97282ce80ce972530ee4fc](https://github.com/tinfoilsh/confidential-deepseek-v4-1-flash/blob/29bc3ea355129b1dda97282ce80ce972530ee4fc/tinfoil-config.yml)
selects four GPUs, TP4 and `cpu_offload:false`. It uses a different patched
image and the original checkpoint, not this fork or our DP2 combination. It is
a feasibility reference only; no Tinfoil implementation is copied. The
[pinned vLLM recipe at
2338e9edec1d5df5f823abf3c1f19e7ba0b2ed0e](https://github.com/vllm-project/recipes/blob/2338e9edec1d5df5f823abf3c1f19e7ba0b2ed0e/models/deepseek-ai/DeepSeek-V4.1-Flash.yaml)
documents Blackwell attention options, but its B200 TP2/Engram-host-offload
defaults are not this CC laboratory profile.

[NVIDIA R595 RN-12817-001_v02, April 2026, pages 7-8](https://docs.nvidia.com/595trd1-trusted-computing-solutions-release-notes.pdf#page=7)
lists HGX B200 180 GB and up to eight devices for MPT CC. Nominal capacity is
not measured usable HBM. As an **estimate**, 510.311 GB divided over four GPUs
is 127.578 GB per GPU, before actual partitioning and runtime overhead. Two
replicas use two TP4 groups, not TP8 sharding of one model. Fit and startup under
the unchanged deadlines still require a separately approved measured run.
The release's OS/version table does not qualify a provider's Ubuntu 26.04 label.

The new profile retains the reviewed image, all source patches, GPU-resident
Engram, context 4096, max-sequences 1, eager execution, eight selected GPUs,
64 container CPUs and 1024 PIDs. The existing helper sets and inspects a
**512 GiB total** container ceiling with MemorySwap equal to Memory, hence no
additional swap. Runtime observation checks the corresponding cgroup limit.
Original DP2 remains 1024 GiB, original DP1 512 GiB and pre-serve 8 GiB.
OOM or startup failure does not authorize a retry, cap increase or fallback.

Fresh launch admission still requires at least 640 GiB MemAvailable after
weight staging: 512 GiB container cap plus 128 GiB host reserve, zero swap and
no competing workload. This is not a reservation or a fit measurement. Cache
location and durable public-weight staging are separate work; this change
does not modify NFS, staging, file checks or mount integrity.

Actual observations must identify eight B200 devices and preserve existing
CC, readiness, devtools, protected-memory and image/source gates. The existing
120 GiB protected-memory floor is a rejection threshold, not proof of free
memory or model fit. The probe does not perform cryptographic attestation.
All result qualifications remain `BLOCKED`, `deployable:false` and
`confidential:false`, regardless of functional progress.

## Software Verification

`scripts/test_deepseek_model_profile.py` exercises the real preparation CLI,
payload publisher, bundle validation and runtime identity/failure validation
with synthetic Git/build/OCI inputs, never a GPU or actual engine. It checks
legacy payload/command equivalence against frozen `f9885c6`, B300 compatibility,
all eight synthetic device observations, wrong/mixed/missing identity, source
tampering and actual-inspect memory, swap and other resource negatives.

The existing probe, tokenizer parity, runtime observation, stream and lab
suites remain required. Independent final-head review, full checks and ordinary
CI must pass before a real offline bundle is prepared. Hardware/run approval
and fresh preflight remain separate; this document is not either approval.
