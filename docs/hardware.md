<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 Lore Hex Corp -->

# Hardware and operating gates

Status: **source-backed requirements; no system validated**. Joseph authorized
correction of the former B300 blocker: use the confidential SKU's documented
**270 GB**, not 288 GB, for planning. This does not select B300 or approve
procurement. Sources reviewed: 2026-10-19 UTC. Exact system qualification is
P3/P5 work, not a prerequisite to finishing this design document.

## GPU mode matrix

Source snapshot: NVIDIA
[R595 TRD1 RN-12817-001_v02, April 2026, pages 5-8](https://docs.nvidia.com/595trd1-trusted-computing-solutions-release-notes.pdf#page=5).
Vendor-listed combinations are not our tested support matrix. GB labels are
budgeting inputs, not measured free memory; see [sizing](sizing.md).

| Candidate | Mode | Memory/GPU | Project role and boundary |
|---|---|---:|---|
| HGX B200, eight GPUs, air cooled | Blackwell MPT CC; up to eight GPUs/CVM, encrypted NVLink | 180 GB | Preferred P5/P7, one TD owns node. |
| HGX B300, eight GPUs, air cooled | Blackwell MPT CC | 270 GB | Alternative requiring separate qualification and approval. |
| HGX H200, eight GPUs, air cooled | Hopper Protected PCIe (PPCIe); NVLink not encrypted | 141 GB | Approved fallback only, different custody boundary. |
| H100 single GPU | Single GPU passthrough CC | Profile-specific | P3 pipeline lab, not a large-model production fit. |

R595 pairs driver **595.58.03** with CUDA **13.2**. Its Intel component tables
list Ubuntu **25.10 / kernel 6.17.0+** hosts and **24.04** guests. These are
reference versions, not a complete operational lock. PPCIe lacks key rotation
and has an IV-exhaustion limitation; fallback operation needs a tested restart
policy. Source: [Overview, component tables and Known Issues](https://docs.nvidia.com/595trd1-trusted-computing-solutions-release-notes.pdf#page=4).

Do not infer support from an architecture name. GB200/GB300 racks, another
cooling variant, a smaller partition or another firmware branch need their own
documented combination. Recheck the exact SKU, VBIOS, driver, mode and RIM
status in NVIDIA's [Secure AI compatibility matrix](https://www.nvidia.com/en-us/data-center/solutions/confidential-computing/secure-ai-compatibility-matrix/)
before P5. This review did not establish a live matrix row for an actual
server. A wildcard firmware suffix is not an acceptable release pin.

## CPU and BIOS requirements

NVIDIA's [deployment guide DU-12302-001_v7.1, April 2026, pages 7-9](https://docs.nvidia.com/cc-deployment-guide-tdx-snp.pdf#page=7)
names TDX-capable Emerald Rapids and Granite Rapids CPUs and respective TDX
module families 1.x and 2.x. Canonical's
[hardware table](https://github.com/canonical/tdx#3-supported-hardware)
lists Emerald Rapids 1.5.x and Granite Rapids 2.0.x. Neither family name proves
that the ordered machine has usable TDX enabled.

The NVIDIA guide's Intel BIOS example enables TME, TME-MT, TDX, SEAM loader
and SGX, assigns a nonzero TDX key split, and disables the 46-bit
physical-address limit. It also lists platform-specific memory-integrity and
bypass settings. This is an example, not commands for an unidentified board.

P5 must record server/CPU part numbers, BIOS revision and OEM menu mapping,
verify VT-d/IOMMU and passthrough prerequisites, and review the complete vendor
configuration before changing anything. Collect non-sensitive observations and
resulting TDX/device checks, not only provider assurances. Do not blindly apply
a guide's security-sensitive toggle to another motherboard.

## Host, guest and fabric ownership

Our intended host is Ubuntu 26.04 LTS with Canonical's TDX stack and an explicit
QEMU launch profile. [Canonical's TDX guide](https://ubuntu.com/server/docs/how-to/virtualisation/intel-tdx/)
documents Ubuntu TDX setup. This does not establish NVIDIA validation of our
26.04 combination; its R595 table above names 25.10. P5 must qualify and pin
the exact kernel/QEMU/OVMF/TDX module/driver/firmware combination. The guest
target is Ubuntu 24.04. One image working across lab and bare metal remains
a test requirement, not an established fact.

Fabric ownership is mode-specific: the guide installs Blackwell Fabric
Manager on the host ([page 14](https://docs.nvidia.com/cc-deployment-guide-tdx-snp.pdf#page=14)),
but starts Hopper PPCIe Fabric Manager in the guest
([page 29](https://docs.nvidia.com/cc-deployment-guide-tdx-snp.pdf#page=29)).
The host manager is not trusted with request plaintext. P5 must record GPU
and switch assignment, topology, secure sessions and mode-specific appraisal.
Host status is not a substitute for guest and relying-party verification.

Our confidential profiles forbid Engram offload, UVA-backed lookup paths,
NVLS/multicast collectives and unverified host-memory paths. Ordinary engine
recipes are not CC qualification. The [released Tinfoil reference](https://github.com/tinfoilsh/confidential-deepseek-v4-1-flash/blob/29bc3ea355129b1dda97282ce80ce972530ee4fc/tinfoil-config.yml)
is a four-GPU configuration with UVA and Engram CPU offload disabled, not proof
of our eight-GPU profile. P2/P5 must verify the actual binary's compatibility
and performance; do not generalize vendor feature names into working flags.

## Lab versus production

Google lists `a3-highgpu-1g` with TDX and NVIDIA support in its
[Confidential VM configurations](https://docs.cloud.google.com/confidential-computing/confidential-vm/docs/supported-configurations).
Its [GPU creation guide, Enable confidential computing mode](https://docs.cloud.google.com/confidential-computing/confidential-vm/docs/create-a-confidential-vm-instance-with-gpu)
recommends open kernel-module driver 580 or later, LKCA and persistence setup.
P3 must pin an exact compatible build, verify CPU/GPU evidence and record
custom-image support. If only Google's image works, report a pipeline test,
not validation of our UKI or predicted bare-metal measurements. Check current
quota, zones, image support and price at the run gate.

P2's plain B200 lab has no confidentiality claim. A managed fallback requires
approval and a distinct platform measurement policy. Neither replaces P5's
own-image/bare-metal criteria; a single-GPU pass proves no multi-GPU fabric.

## Procurement and questionnaire

Project targets, **not measured minima**: at least 2 TB RAM, 15 TB NVMe and
one 100 GbE interface, plus BIOS/out-of-band access or an agreed
provider-operated configuration. See [estimates](sizing.md#host-storage-and-cost-estimates).
Obtain actual power/cooling/rack requirements from the selected server's OEM.

Joseph reports having emailed HydraHost about an eight-B200 server. Hardware,
price, delivery and CC enablement are **pending**. No machine is qualified,
reserved, rented or approved by that outreach. No further contact is authorized
here. Questions for Joseph's existing correspondence:

1. Exact GPU board/cooling SKU, Xeon part number, RAM and NVMe configuration?
2. Will the required TDX BIOS settings be enabled; who controls BIOS/BMC access?
3. Are CC mode changes and compatible GPU firmware updates permitted?
4. May we control host OS, QEMU/OVMF and complete GPU/fabric assignment?
5. What custody, maintenance, facility audit and incident-reporting controls apply?
6. Availability, minimum term, total price, power/cooling limits and teardown terms?
7. Who provides NVIDIA commercial entitlement and attestation-service access?

Answers are procurement inputs, not hardware acceptance evidence.

## Licenses and action gates

All new repository code/docs are [Apache-2.0](../LICENSE). Models, dependencies,
CUDA, drivers, firmware and services retain their licenses. Preserve upstream
copyright, license and NOTICE material when permitted Apache material is
reused; identify modifications. Do not copy AGPL material. Referencing a stack
does not make the whole stack Apache-licensed.

| Material | Source and treatment |
|---|---|
| DeepSeek selected revision | [MIT at 1bae0e10](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017/blob/1bae0e1088993b9b82de08f59de5be5a34e42170/LICENSE); preserve its notices. |
| GLM selected revision | [GLM-5.3 License at ba32f8c1](https://huggingface.co/zai-org/GLM-5.3/blob/ba32f8c19c8738e2c57c71798c601d477a7bf844/LICENSE), not Apache or unqualified MIT. Its model-as-a-service security-review clause considers affiliate revenue above USD 10 billion over any consecutive twelve months. Owner/legal review determines applicability. |
| NVIDIA CC | [Product terms A-C](https://www.nvidia.com/en-us/agreements/enterprise-software/product-specific-terms-for-confidential-computing/): commercial Enterprise Product license required; free development does not authorize offering/distributing the product. Driver/CUDA terms are separate. |
| Attestation tools | [nvtrust deprecation notice](https://github.com/NVIDIA/nvtrust#guest-tools) directs new integrations to C++ SDK/CLI. Confirm the chosen artifact's license and entitlement; a repo license does not waive service terms. |

NVIDIA's terms page shows September 4, 2026 as modification date and July 9,
2026 as version date. Caching, disclosure, telemetry and audit conditions also
need review before operations. No price, entitlement or legal approval is
inferred. [G-NVIDIA-LICENSE and other gates](gates.md) remain pending.

The [SDK release tagged 2026.10.02](https://github.com/NVIDIA/attestation-sdk/releases/tag/2026.10.02)
lists `nvattest` 2.0.0. Its source at
`11b2bfeb42a1158aa08744603ecb239ccb3d2265` has an
[Apache-2.0 license](https://github.com/NVIDIA/attestation-sdk/blob/11b2bfeb42a1158aa08744603ecb239ccb3d2265/LICENSE).
This is a source/tooling reference, not a tested GPU profile. Do not describe
the SDK source as proprietary or conflate that license with production CC
service/driver terms. P1/P3 must pin the actual built/downloaded artifact too.

The [CLI reference, reviewed October 19](https://docs.nvidia.com/attestation/nv-attestation-sdk-cpp/latest/sdk-cli/command-reference.html)
retains legacy `collect-evidence`/`attest`; the release labels CoRIM v2
experimental. Use a pinned legacy v1 evidence/appraisal contract initially;
CoRIM adoption requires separate review. A JSON document or exit code zero is
not enough: check each expected GPU's authenticated result, nonce, mode,
identity, RIM/revocation status and any required NVSwitch/fabric appraisal.

G-HW-RENT/G-RUN cover procurement and each billable run; G-INTEL-PCS/G-NGC
cover registration/credentials; G-GCP-PROJECT/G-GCP-QUOTA cover lab setup.
Production, credits, DNS, releases, settings and further third-party contact
retain their gates. General permission to build/merge software grants none of
those actions.

## Per-system verification record

Commit the actual profile, script and scrubbed evidence before calling a
system validated: OEM/CPU/board, BIOS/TDX version, exact host/guest/driver/CUDA/
firmware, device inventory, fabric checks, fresh CPU/GPU appraisals, predicted
versus observed measurements, negative mutations, verified storage, no debug
or logging exposure, and applicable reboot/load/benchmark/soak results.

P3 proves only its single-GPU system. B200, B300, H200 PPCIe and a managed
fallback each need their own record. All are currently **unvalidated**.
