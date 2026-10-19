<!-- SPDX-License-Identifier: Apache-2.0 -->
# ADR 0002: HGX eight-B200 target with Intel TDX

Status: accepted target; hardware, capacity and commercial use remain gated.
Records PLAN D2.

## Decision

Target an HGX eight-B200 system with an Intel TDX-capable Xeon and the supported
NVIDIA multi-GPU confidential-computing mode. Require the provider to confirm
the actual board, CPU, BIOS access/configuration, firmware, software matrix and
physical custody controls before committing a run.

An eight-H200 PPCIe system is a separately evaluated fallback, not an equivalent
confidentiality or performance claim. B300 is a future candidate requiring its
own supported-SKU, sizing and cost review. Never substitute an NVL rack based
only on a similar GPU family name.

## Rationale and limits

The selected profile is intended to support both initial model packs and their
planned parallelism. Capacity arithmetic is in [sizing](../sizing.md); fitting
weight bytes is not proof of engine fit, KV capacity or usable throughput.
Hopper fallback must be tested against its own kernel/quantization and fabric
constraints rather than assuming a Blackwell recipe carries over.

The [NVIDIA R595 release notes](https://docs.nvidia.com/595trd1-trusted-computing-solutions-release-notes.pdf)
and [hardware mode matrix](../hardware.md#gpu-mode-matrix) govern support. The
source-backed B300 capacity correction is recorded there; headline marketed
capacity must not replace a supported confidential SKU's declared capacity.

## Verification and revisit

P2 measures each rank's memory, model loading, correctness, latency and
throughput. P5 repeats the workload in CC mode, including every GPU/fabric
attestation, measured boot, reboot stability and the 24-hour soak. Hardware
availability does not waive any of those checks.

Joseph has contacted Hydra Host about eight B200 GPUs. That is a pending
hardware inquiry, not evidence of delivery, an approved price or a passed CC
test. [Gates](../gates.md) still control rental and individual paid runs.
Revisit after real availability, price and measured results justify another SKU.
