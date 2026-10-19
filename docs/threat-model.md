<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 Lore Hex Corp -->

# Threat model

This is the security contract for the planned node, not proof of implementation.
When product copy overstates protection, this document governs. It applies to
the [architecture](architecture.md) and each separately verified
[hardware profile](hardware.md). Source review: 2026-10-19 UTC.

Adapted from the Apache-2.0
[trustedrouter-confidential-stack threat model at 05787fad](https://github.com/Lore-Hex/trustedrouter-confidential-stack/blob/05787fadca260dbe27f6750f51f5e0ffbdadf8f3/docs/threat-model.md).
Upstream attribution: **Copyright 2026 Lore Hex Corp**; see its
[LICENSE](https://github.com/Lore-Hex/trustedrouter-confidential-stack/blob/05787fadca260dbe27f6750f51f5e0ffbdadf8f3/LICENSE)
and [NOTICE](https://github.com/Lore-Hex/trustedrouter-confidential-stack/blob/05787fadca260dbe27f6750f51f5e0ffbdadf8f3/NOTICE).
This adaptation adds verified weight storage, Engram/UVA restrictions, debug
separation, explicit verifier prerequisites, system-specific tests and known
integration gaps. It removes claims about other providers' deployed tiers and
does not copy AGPL code or documentation.

## Assets and trusted parties

Protect request/response content, TLS/session keys, KV cache and scratch while
inside the node boundary. Protect the identity and integrity of the model,
tokenizer, executable code and configuration that handle a request. Public
model weights are not confidential; substituting them is a security failure.

| Party or component | Trust assumption |
|---|---|
| Buyer and its software | Responsible for its own endpoint and data handling. |
| Relying party, usually the TR gateway | Sees request plaintext; correctly verifies evidence, enforces pins, time, revocation and policy. Its compromise is outside this node's protection. |
| Provider, operator, host root, hypervisor | Adversaries for confidentiality and integrity; can alter disks, networking, launch inputs, device exposure and scheduling. Host tools and firewalls are not trust anchors. |
| Custodian and maintenance chain | Responsible for physical integrity, inspection and access controls. May be the operator; independence must not be assumed. |
| Intel/NVIDIA roots, accepted platform firmware | Correct isolation and cryptography, authentic endorsement infrastructure, and policy that rejects unacceptable TCBs. |
| Accepted guest software and release process | Correctly enforce measured behavior; no malicious accepted release or successful guest compromise. Reproducibility is not a proof of benign code. |

## Attacks and required outcomes

| Attack | Required defense and residual limit | Verification owner |
|---|---|---|
| Network MITM, TLS-terminating ingress, replay | Verify fresh CPU/GPU evidence against the same connection's key; reject mismatches before sending data. A pass-through relay can still deny service. | P1/P3/P4: wrong key, nonce and stale evidence fail. |
| Host reads guest/GPU content | Accept only approved TDX and GPU modes with complete attestation. No plaintext host buffers, swap, dumps or request logging. | P3 single GPU, P5 all devices; never inferred from plain P2. |
| Host changes boot image, engine flags or egress policy | Measured boot binds the effective rootfs and executable/configuration inputs; allowlist release measurements. | P3/P5: change each input independently; verifier must reject. |
| Host changes weights after a startup checksum | Per-read dm-verity, trusted root in measured image, no writable executable overlay or alternate unverified load path. Corruption stops serving, not repair from an untrusted path. | P5: post-mount bit flip, wrong root, changed tokenizer/code and alternate-volume tests. |
| Host rolls back an old valid image/model | Relying-party release floor and explicit accepted-set expiry; verity alone permits a valid old volume. | P4/P8: withdrawn release fails despite valid signatures. |
| Forged GPU envelope or remote device substitution | Verify authenticated vendor evidence for the complete expected inventory, and rely on measured guest device-session enforcement. JSON flags/nonces are hints, not signatures. | P1 structural mutations; P3/P5 vendor-signature, identity, mode and inventory mutations. |
| Debug guest or GPU devtools accepted as release | Different measurements; reject debug attributes, devtools and fake backends. | P1 build separation; P3/P5 genuine debug-evidence rejection. |
| Malicious client exploits inference/parser/driver | Minimize and pin the guest TCB, bind workers to loopback, restrict ingress/egress, limit payloads/queues, update vulnerable releases. TEEs do not contain a process that already controls the trusted guest. | P1 hostile-request/error-path tests; security review for each release. |
| Credential or content leakage in diagnostics | No request-derived log fields; fixed route classes, no crash dumps, redact reviewed operational evidence. | P1 regression tests, including secrets embedded in URL paths and malformed requests. |
| Host blocks time, network or attestation services | Verifier-owned time and bounded signed-collateral cache; required checks expire closed. Availability is not promised. | P3/P4: expired collateral, failed fetches and revoked chains fail. |

Linux describes [dm-verity's trusted-root and block-verification model](https://docs.kernel.org/admin-guide/device-mapper/verity.html).
Our no-bypass, fail-stop and anti-rollback policies are additional requirements.
It does not encrypt weights or establish which root hash the buyer should trust.
Release rootfs and weight mounts must forbid `check_at_most_once` and
`ignore_corruption`. Test a post-mount bit flip after a previously verified
read and force a fresh device read; cached plaintext must not hide corruption.

## Engram and host-visible access patterns

NVIDIA's [Secure AI whitepaper v1.3, Bounce Buffers](https://docs.nvidia.com/nvidia-secure-ai-with-blackwell-and-hopper-gpus-whitepaper.pdf#page=12)
describes encrypted payloads staged through host-visible shared pages. The
pages are shared; **the payload is not plaintext**. Encryption does not make
the choice, timing or length of transfers secret.

Our threat inference is that input-dependent Engram table lookups serviced from
host-visible memory or transfers can expose input-correlated access patterns.
We do not claim a measured prompt-recovery rate or that every UVA use is itself
a successful attack. Confidential profiles forbid Engram host offload and
UVA-backed lookup paths. The released
[Tinfoil configuration at 29bc3ea3](https://github.com/tinfoilsh/confidential-deepseek-v4-1-flash/blob/29bc3ea355129b1dda97282ce80ce972530ee4fc/tinfoil-config.yml)
disables UVA and Engram CPU offload. That is configuration evidence, not proof
that our eventual engine binary has the same behavior.

P2/P5 must inspect the actual launch/configuration, loaded code and memory
residency, reject contradictory offload options, and test that restart or OOM
never silently enables offload. The non-confidential comparison in P2 may use
offload only as explicitly labelled benchmark data. General timing, traffic
length, shared-cache and power side channels remain out of scope; that does
not excuse a known, avoidable host-visible lookup path.

## Physical attacks and custody

Physical tampering is **out of scope**. The researchers behind
[TEE.fail](https://tee.fail/) report attestation-key extraction through DDR5
interposition. [DDRop's authors](https://ddropattack.eu/) describe active memory
interposition that breaks confidential-VM protections. Intel's
[DDRop announcement](https://www.intel.com/content/www/us/en/security-center/announcement/intel-security-announcement-2026-08-11-001.html)
places such physical attacks outside its TDX/SGX threat model. We do not adopt
upstream secondary-source claims about attack cost, runtime or universal
success rates. Valid signatures cannot prove an intact physical machine after
the relevant trust roots have been compromised.

Required custody mitigation, to be evaluated before a production listing:

1. Identify the actual operator, facility and maintenance chain. Prefer
   separation of logical operation and physical custody, but check contractual
   and practical access; renting does not automatically create independence.
2. Review physical-access and maintenance controls for the actual servers and
   location, incident-reporting duties, inspection records and audit rights.
   A certification logo alone is insufficient evidence of those controls.
3. Where independently authenticated platform identity can be obtained, bind
   accepted platforms to the audited inventory. Record privacy and endorsement
   limitations; never treat an unverified serial number as that binding.
4. Reassess custody after hardware replacement or an incident, and suspend
   acceptance when integrity is uncertain. Identity allowlists do not undo
   stolen attestation keys or prevent custodian/operator collusion.

Hopper PPCIe's unencrypted intra-node GPU links add a custody dependency that
Blackwell encrypted NVLink does not share; neither protects against all
physical CPU-memory attacks. See the [mode matrix](hardware.md#gpu-mode-matrix).
No `attested+custody` deployment status is claimed by this repository.

## Policy and current limitations

Acceptance belongs to the relying party: vendor roots, minimum TCBs, revoked
identities, debug policy, accepted releases, complete device set, evidence age
and custody conditions. Host-reported health and `confidential` flags cannot
widen it. Never accept stale evidence because a collateral service is down,
and never silently downgrade a confidential route to a plain one.

The [verification contract](architecture.md#evidence-and-relying-party-verification)
requires Intel collateral **and** CRL verification explicitly enabled. GPU
envelope binding without vendor cryptography is a test of plumbing only.
Debug and migratable-TD rejection must also be explicitly enabled. Before real
GPU appraisal, production verification is fail/partial; fake positive results
are permitted only in `nibfake` builds and cannot assert confidentiality.
The upstream shim's raw-path access logging is an unresolved P1 integration
prerequisite, documented in the [data lifecycle](architecture.md#request-network-and-data-lifecycle).
We make no current ZDR, production confidentiality or real-hardware claim.

Not protected: availability, billing integrity, secrecy of public weights,
malicious accepted releases, hardware/vendor-root compromise, general side
channels, physical tampering, or a compromised buyer/gateway. Software-only
host attacks are in scope under the stated platform assumptions; a newly
discovered TCB break requires policy withdrawal, not relabeling it as harmless.
Report implementation violations using [SECURITY.md](../SECURITY.md).
