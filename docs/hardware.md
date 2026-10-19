<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 Lore Hex Corp -->

# Hardware: P0.T2 source-verification blocker

Status: **blocked, incomplete**. This is a source-review record, not a
deployment guide or evidence of hardware validation. P0.T2 is not complete.
The remaining hardware matrix, architecture, sizing, threat model, and seven
ADRs have not been authored.

## Plan discrepancy requiring owner direction

[PLAN.md section 3](../PLAN.md#3-sizing-how-big-each-node-must-be) describes
B300 as having 288 GB of usable HBM per GPU. NVIDIA's
[R595 TRD1 release notes, RN-12817-001_v02, April 2026, page 7](https://docs.nvidia.com/595trd1-trusted-computing-solutions-release-notes.pdf#page=7)
instead list the supported air-cooled HGX B300 multi-GPU confidential-computing
SKU with 270 GB per GPU. The same SKU capacity appears on page 5.

NVIDIA's [DGX B300 specifications](https://www.nvidia.com/en-us/data-center/dgx-b300/)
list 2.1 TB of total GPU memory. This is a different system-level description;
it does not establish that the plan's 288 GB is usable memory for the specified
confidential SKU. Neither source establishes a conversion or reservation rule
that would reconcile these figures. Do not invent one or silently substitute
one capacity for the other.

`TODO(verify): Reconcile PLAN.md's 288 GB usable B300 capacity with NVIDIA's
270 GB CC SKU entry using the exact board's official specification and Secure
AI compatibility matrix; obtain owner direction before continuing P0.T2.`

The [agent instructions](../AGENTS.md#how-to-pick-work) require stopping when
a vendor document contradicts the plan. PLAN.md has therefore been left
unchanged. This draft PR records the finding pending owner direction. No
alternative SKU, memory budget, or software stack has been selected.

## Licensing and gates retained

This original documentation is covered by the repository's
[Apache-2.0 license](../LICENSE) and [notice](../NOTICE). It does not relicense
model weights, vendor software, or vendor documentation. No vendor document,
upstream implementation, or AGPL material has been copied into this file.

NVIDIA's [Confidential Computing terms, sections A-C](https://www.nvidia.com/en-us/agreements/enterprise-software/product-specific-terms-for-confidential-computing/)
require a commercial Enterprise Product license for commercial CC use. The
free development grant is limited to developing a customer product; a license
is required before offering or distributing it. The page displays a last
modification date of September 4, 2026 and a version date of July 9, 2026.
Driver and CUDA terms are separate. This source review is not legal approval.

The existing [human gates](gates.md) remain closed unless Joseph approves the
specific action. In particular, G-HW-RENT, G-NVIDIA-LICENSE, G-INTEL-PCS,
G-NGC, G-GCP-PROJECT, G-GCP-QUOTA, and G-RUN are not granted by this document.
No provider contact, registration, provisioning, spending, production
mutation, settings change, publication, or license acceptance was performed.

## Verification and remaining work

Sources above were opened and read on 2026-10-19 UTC. The release notes were
checked at the named revision and pages; the two web pages are mutable and
must be checked again before action. No operational versions were pinned.

The confidential stack's threat model and attestation protocol were read
locally at commit `05787fadca260dbe27f6750f51f5e0ffbdadf8f3` before this stop.
They have not yet been adapted. Any later adaptation must retain the upstream
Apache-2.0 attribution and explicitly identify the changes.

P0.T2 still needs all four complete documents, ADR0001-0007, primary-source
verification for their external claims, estimate arithmetic, attribution, and
documentation checks. No benchmark, model-fit, BIOS, attestation, or hardware
claim has been validated. The base checkout has no Makefile or documentation
test harness; P0.T1 owns that work.
