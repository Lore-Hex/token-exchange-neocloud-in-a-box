<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 Lore Hex Corp -->

# Architecture

Status: P0.T2 design contract, not a running confidential service. This expands
[PLAN section 5](../PLAN.md#5-architecture). Requirements below are normative
for later phases, not claims that the current skeleton enforces them. See the
[threat model](threat-model.md), [sizing](sizing.md), [hardware](hardware.md), and
[gates](gates.md). Source review date: 2026-10-19 UTC.

## Node and trust boundaries

### Commercial boundary

The node is an inference supplier, not a retail billing platform. TrustedRouter
owns customer authentication, credit enforcement, accounting, billing, payments
and provider payouts under the onboarding agreement. The node accepts the
dedicated inference credential, advertises its catalog and agreed rates, and
returns the engine's actual token usage in ordinary and streaming responses.
Never estimate usage from character counts or equate aggregate logs with an
invoice. TrustedRouter performs settlement; this repo has no customer ledger,
payment SDK, account database or payment webhook.

Keep operator identities, contracts, bank details, secrets and cloud resources
outside this source repo. Runtime provider metadata is configuration, not a
hardcoded identity or attestation result. See the
[operator guide](operators/trustedrouter.md) for the handoff and minimal telemetry.

### Confidential boundary

One physical server, one Intel TDX trust domain (TD), one model pack. The
preferred profile assigns eight B200 GPUs to that TD. DeepSeek may have two
TP4 replicas inside it; GLM uses TP8. Replicas are neither separate tenants nor
separate security domains. These are planned profiles requiring P2/P5 tests,
not configurations proven by another operator's four-GPU deployment.

```text
Buyer -> TrustedRouter gateway / independent relying party
                     | TLS and fresh channel-bound evidence
    untrusted host --|-- TCP 443 -> guest 8443, L4 forwarding only
                     v
  +---------------- Intel TDX confidential VM ----------------+
  | trcs :8443       TLS key generated here; attestation API   |
  |     |            listener audit and admission control      |
  |     v                                                      |
  | 127.0.0.1:8000    pinned inference engine                   |
  |     |                                                      |
  | nib-gpu-gate -> all assigned GPUs in approved CC mode       |
  | nib-weights  -> read-only model volume via dm-verity        |
  | read-only verified rootfs; measured egress policy           |
  +-----------------------|------------------------------------+
                          | authenticated encrypted CPU/GPU I/O
                     GPU protected memory and compute
  host storage: public weight cache; encrypted ephemeral scratch
```

The confidentiality boundary includes guest software and the approved GPU
mode, not the host OS, QEMU process, host storage, host firewall, operator, or
provider control plane. Host software can deny service and manipulate inputs
to the TD; it is not entrusted with request plaintext. The relying party,
guest kernel, driver, engine, model code, shim, verification code, vendor
hardware roots and accepted firmware are trusted. Measured code can still
contain exploitable bugs; attestation is not a proof of program correctness.

NVIDIA distinguishes encrypted Blackwell multi-GPU NVLink from Hopper PPCIe,
whose intra-node GPU links are not encrypted. These profiles must not inherit
identical physical-security claims. See [the mode matrix](hardware.md#gpu-mode-matrix).
The intended node-to-gateway boundary does not hide content from the buyer or
the gateway that handles it. It is not buyer-to-model encryption through an
untrusted gateway.

## Build and boot contract

1. Build an image for exactly one `(pack, engine, hardware profile, variant)`.
   Pin the base, kernel, initrd, firmware, driver, runtime, engine image,
   patches, model revision, tokenizer/config/code files, launch arguments and
   dependencies. Remote-code loading and runtime downloads cannot bypass pins.
2. Build a UKI and read-only root filesystem. The measured boot path must
   authenticate the rootfs verity root and prohibit unmeasured command-line,
   initrd, configuration, or executable overlays. A UKI alone does not prove
   that this condition holds; test the actual firmware/boot-loader path.
3. Put the model volume's verity root in that protected rootfs. Mount the volume
   through dm-verity before engine startup; never serve from a host cache path
   that bypasses the verified device. Public weights need integrity, not secrecy.
   Release rootfs and weight mounts must forbid `check_at_most_once` and
   `ignore_corruption`: those options respectively permit online tampering or
   allow corrupted reads. Exercise post-mount corruption after a prior read,
   forcing a new device read so a cached page cannot mask the test.
4. In the guest, attest every assigned GPU and the mode-required fabric devices
   before enabling readiness or starting the engine. Reject missing, duplicate,
   unexpected, debug, or failed devices. Shut down on an irrecoverable gate failure.
5. Start the engine only on loopback, then enable the front door after readiness,
   listener and storage checks pass. Ongoing failures remove readiness; requests
   must not continue through a failed gate or fall back to non-CC service.

The mechanisms, not deployment commands, are based on
[systemd-stub's UKI and measurement documentation](https://www.freedesktop.org/software/systemd/man/latest/systemd-stub.html)
and [Linux dm-verity](https://docs.kernel.org/admin-guide/device-mapper/verity.html).
dm-verity verifies blocks against a trusted root; it does not choose that root,
encrypt content, or reject an old otherwise-valid release. Our build and
relying-party policy must supply those missing trust decisions.

## Measurement contract

The intended mapping below is a profile-specific acceptance requirement, not a
universal promise about all TDX firmware. P3/P5 must reconstruct the actual
event log and compare predicted registers with hardware quotes. Canonical's
[TDX event-log documentation](https://github.com/canonical/tdx#11-inspect-event-log-and-measurements)
is a reference for inspecting that boot chain.

| Value | Required interpretation | Acceptance evidence |
|---|---|---|
| MRTD | Initial TD construction, including the selected TDVF/OVMF firmware | Bare metal: reproduce from the exact firmware and launch profile. GCP: verify an applicable signed endorsement; an observed value is explicitly weaker, not an independent prediction. |
| RTMR0 | Firmware/platform configuration measured by that firmware | Fix VM shape, device topology and launch inputs; replay the actual events and reject mismatches. |
| RTMR1/RTMR2 | Measured boot path covering the UKI and enforced rootfs root | Prove the kernel, initrd and effective command line are bound; tampering each must fail. Never assume matching a UKI file hash alone covers all inputs. |
| RTMR3 | Reserved, unused by this v1 image design | Do not represent a runtime extension as workload enforcement. A managed-platform compose-hash policy is a different profile. |
| Model root | The rootfs contains the accepted weight-volume root | Bit flips after startup must fail reads; a different root requires a different accepted release. |

One source image does not mean one measurement on GCP and bare metal. A host
update, new firmware, different VM shape, changed engine flags, or debug image
requires recomputation and explicit policy review. Event logs are untrusted
explanations until replay matches authenticated registers. P8 reproducibility
and release signing are requirements, not completed work.

## Evidence and relying-party verification

Reuse the draft
[tr-attestation/v0 protocol at 05787fad](https://github.com/Lore-Hex/trustedrouter-confidential-stack/blob/05787fadca260dbe27f6750f51f5e0ffbdadf8f3/docs/attestation-protocol.md),
sections 2-5 and its golden vectors. The protocol defines these bindings:

```text
report_data = SHA256(DER SubjectPublicKeyInfo)
           || SHA256("tr-attestation/v0" || 0x00 || fresh_32_byte_nonce)
gpu_nonce   = SHA256("tr-attestation/v0/gpu" || 0x00 || report_data)
```

The key is generated inside the TD and never exported or supplied by the host.
Fetch evidence on the TLS connection being authenticated, before sending
inference content. Recompute bindings from that connection's certificate and
the verifier's own nonce, not from JSON assertions. This project requires
verification on each new connection and renewal within ten minutes on a
persistent connection (P4 policy); expiry or key rotation requires rechecking.

| Layer | Mandatory checks before a confidential verdict |
|---|---|
| Transport | Expected protocol, fresh challenge, same-connection SPKI, complete CPU report-data match; no TLS termination outside the TD. |
| Intel | Quote parsing/signature, vendor trust chain, PCK CRLs, signed TCB/QE collateral and their validity, accepted TCB status, debug disabled, workload measurements and any required platform identity. |
| NVIDIA | Every expected device's vendor-signed report, trust chain, signed RIM comparison, revocation status, approved mode/firmware, nonce inside authenticated vendor evidence, and mode-specific fabric verification. |
| Workload | Accepted release/profile and variant, measured configuration, model-root binding, no fake evidence implementation, and complete device inventory. |
| Policy | Verifier-controlled time, expiry, revocations, rollback floor and custody policy. Unavailable/expired required collateral fails closed; any bounded cache must preserve signature and expiry checks. |

The [go-tdx-guest README at 23b3eac9, verify/Options](https://github.com/google/go-tdx-guest/blob/23b3eac9db39ba4e1df1426bacb3bfa3a7018e97/README.md#verify)
documents that collateral and CRL verification default to **off**. Integration
must explicitly enable **both `GetCollateral` and `CheckRevocations`**, retain
TCB-status checks, and test those settings. Use a bounded allowlisted collateral
fetcher, not evidence-controlled arbitrary URLs. Signature-only verification
is insufficient. The operator verifier can use this dependency; guest Go code
remains standard-library-only under the repository rule.

Its [validation options at the same revision](https://github.com/google/go-tdx-guest/blob/23b3eac9db39ba4e1df1426bacb3bfa3a7018e97/validate/validate.go)
also require explicit `EnableTdDebugCheck` and `EnableTdMigratableCheck`;
their zero values skip those checks. Call field/policy validation as well as
signature verification, with nonempty expected report data and measurements.
Reject unsupported quote formats; the pinned source handles QuoteV4/QuoteV5.
Its [context-aware verification API](https://github.com/google/go-tdx-guest/blob/23b3eac9db39ba4e1df1426bacb3bfa3a7018e97/verify/verify.go)
supports a context-aware getter: propagate deadlines/cancellation through every
collateral request. A context on the outer call alone is not enough.

A GPU envelope's nonce field is not vendor cryptographic verification. A
correct nonce attached to unsigned, forged, stale, or substituted device
reports must fail. Freshness alone also does not prove that a remote GPU served
the request: the accepted measured guest must enumerate its assigned devices,
establish secure device sessions, and use only those devices for inference.
Multi-GPU/NVSwitch collection and binding require P1.T2/P4.T1 protocol work;
opaque bundles and `confidential: true` are not acceptance decisions.

Until the P3 vendor appraiser exists, production `nib verify` must return a
failed/partial result with GPU verification incomplete, never confidential
success. Positive fake verification is compile-time restricted to `nibfake`;
do not expose a production API accepting arbitrary permissive verifiers.
Fake TSM/attestation-agent sources must be explicitly paired with that fake
verifier and excluded from release artifacts.

Reuse `trcs` as a **pinned, hash-checked binary built from upstream source**.
The inspected stack places its reusable implementation under Go `internal/`
([source tree](https://github.com/Lore-Hex/trustedrouter-confidential-stack/tree/05787fadca260dbe27f6750f51f5e0ffbdadf8f3/internal));
there is no assumed public library API to import. Required changes and any
build-tagged fake backend go upstream with tests; do not copy the internal
implementation here. Record source commit, build inputs and executable hash.

## Request, network and data lifecycle

Only `trcs` may listen outside loopback, including IPv6 and auxiliary engine
workers. A host firewall is defense in depth, not enforcement against host
root. The measured guest policy denies unexpected ingress and egress; the
listener audit must fail closed on violations or unreadable state. Egress is
limited to specifically required attestation services with bounded requests.
Do not make a general HTTP proxy, arbitrary model fetcher, or diagnostic shell
reachable through the shim. Admission limits and bounded queues return 429
with `Retry-After`; upstream unavailability returns an appropriate 503.

Request content, keys and KV cache stay in private CPU/GPU memory within this
boundary. Scratch, if necessary, is encrypted using a boot-ephemeral guest
key and is never relied on for rollback protection. Disable plaintext swap,
hibernation, crash dumps and core dumps. Release images have no SSH, serial
shell or debug tools; debug variants have different measurements and are
excluded from release acceptance. Benchmarks use synthetic data only.

No logging of prompts, completions, bodies, headers, queries, or user-controlled
URL paths, including error/debug paths. Use fixed route classes and bounded
status/timing counters, not request-derived labels. Upstream
[trcs accessLog at 05787fad](https://github.com/Lore-Hex/trustedrouter-confidential-stack/blob/05787fadca260dbe27f6750f51f5e0ffbdadf8f3/internal/shim/server.go#L284)
currently logs `r.URL.Path`: P1.T2 must fix this upstream and add adversarial-path
log tests before this project claims no request-data logging. Evidence bundles
can disclose device identity; publish only reviewed, scrubbed artifacts.

## Phase verification boundaries

| System/phase | What must be demonstrated | What it cannot establish |
|---|---|---|
| P1 fake node | Real shim transport, loopback/listener policy, binding and mutation tests; fixed-route logging; no fake code in release | Hardware trust, vendor GPU cryptography, real CC, model fit, or a confidential verdict |
| P2 plain B200 | Exact model hashes, load and quality checks, measured per-rank memory and repeatable benchmarks | TDX isolation, CC overhead, or a confidential route |
| P3 GCP H100 | Real CPU and GPU verification with negative tests, measured boot and image limitations explicitly recorded | Multi-GPU fabric behavior, either large model's fit, or bare-metal measurements |
| P5 B200 TD | Every device/fabric gate, full verity tamper tests, predicted measurements, two reboots, load/benchmark/soak evidence | Other board/firmware combinations or physical-attack resistance |
| H200 fallback | Separate PPCIe profile, whole-fabric verification and restart policy | Encrypted Hopper NVLink or automatic equivalence to B200 |
| Managed fallback | Approved platform-specific OS/compose policy and fresh hardware evidence | Reproducibility or validation of our own bare-metal image |

Each hardware result needs the committed script, scrubbed log, exact versions,
timestamps and run manifest required by [AGENTS.md](../AGENTS.md). A partial P1
pass remains **tested against fakes**, never **confidential** or **validated on
hardware**. P1's dependency on merged P0.T1-T3 remains unchanged.

## Licensing and reuse

Original documentation is Apache-2.0 under [LICENSE](../LICENSE). The protocol
description is adapted from trustedrouter-confidential-stack, Copyright 2026
Lore Hex Corp, Apache-2.0, at the pinned revision above. Changes specify this
node's stricter no-fallback policy, device completeness, storage enforcement,
phase tests and known integration gaps. Models and vendor software retain
[separate licensing requirements](hardware.md#licenses-and-action-gates).
