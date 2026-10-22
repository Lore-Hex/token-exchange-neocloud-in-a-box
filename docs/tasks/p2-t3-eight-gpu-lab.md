# P2.T3: Explicit eight-GPU correctness lab

Status: implemented for local tests; no eight-GPU runtime result yet. This is
not a release profile, attestation result, tau benchmark, or serving deployment.

## Source-backed topology

The pinned vLLM revision is
`5df63a7203cdb9328d824a27b682e77c7b9928be`. Its
[data-parallel deployment guide](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/docs/serving/data_parallel_deployment.md)
(introduction and Internal Load Balancing) documents combining data and tensor
parallelism. TP4 with DP2 selects eight GPUs. For an MoE model, the expert layers
form a DP-times-TP group; forward passes synchronize even when a rank is idle.
These are not independent replicas. `max-num-seqs` applies per DP rank.

The lab keeps the reviewed all-off CC controls, eager execution, TP4, PP1,
4,096-token context, one sequence per rank, and one API server. Only the explicit
new profile changes DP1 to DP2. It does not edit the production model pack or
the historical launch plan. Whether this topology works in the selected image
and hardware remains a question for the real experiment.

## Bounded experiment

`host/record-deepseek-lab.py --prepare-local-correctness-b300-dp2 <private-bundle>`
prepares a committed-source bundle for the already retained eight-B300 lab.
This is an offline preparation action, not execution. The existing runner's
explicit execute approval, receipt hash and private weights path are still
required.

The profile is restricted to B300, CC observations, and local correctness.
There is no DP2 nonconfidential fallback. Both the host and payload validate
the profile. Docker injection and read-only NVML observations must identify
exactly eight selected GPUs; every device must satisfy the same CC checks.
NVML observations are not cryptographic attestation.

The host-memory bound is 1,024 GiB, twice the existing 512-GiB lab limit. This is
a conservative experiment limit, not a measured requirement. The existing
80-minute outer deadline, watchdog, container cleanup, no network/ports,
read-only weights, source hashes, and synthetic-only correctness cases remain.
Existing DP1 profiles retain their four-GPU selection, 512-GiB limit, arguments
and absent optional `data_parallel_size` contract field.

At the retained machine's recorded compute rate of $53.5188/hour, the maximum
80-minute experiment costs **an estimated $71.36 of already-running compute**.
The retained public-weight volume is billed separately. No extra GPU node or
reservation is needed. Verify current spend and the approved weekly ceiling
before execution; keep instance lifecycle decisions outside this reusable repo.

## Validation and remaining work

Tests cover explicit CLI selection, profile rejection, unchanged default
contracts, all eight device observations, mismatched counts and family, Docker
device/memory inspection, result validation and source-bound arguments.

Before claiming a hardware pass, preserve the original output from this exact
committed runner under `evidence/p2/`, including failures and cleanup. A pass
would establish only bounded local correctness; sustained serving, performance,
full tau testing and measured confidential-guest verification remain separate.
