# P2.T3: pinned lab GPU appraisal

`host/attest-lab-gpus.py` is an operator-invoked, bounded
helper for the approved eight-GPU lab. Its default only inspects already
installed files and CLI metadata. An explicit, separate offline install mode
accepts only the two pinned local packages. Audit-only `--attest` never sets
readiness. A separate, explicitly authorized
[gated transition](p2-t3-gated-readiness.md) requires fresh authenticated appraisal
before its sole readiness setter. No mode changes persistence, driver or CC mode.
The initial audit implementation and this transition's software tests are not
evidence of a successful real appraisal or transition.
Remote appraisal requires separate operator authorization and contacts NVIDIA.
The author does not operate the VM.

This is not the production GPU verifier. A successful appraisal is not CPU,
NVSwitch/fabric, application, storage, TLS or confidential-service qualification.
The AMD lab does not fulfill the Intel TDX/GCP gates. Every output retains
`runtime_ready:false`, `confidential_qualified:false` and `deployable:false`.
The production `internal/verify` incomplete-GPU-appraisal rejection is unchanged.

## Exact artifacts and source

Public metadata and both package byte hashes were independently checked on
2026-10-20 against the
[NVIDIA Ubuntu 26.04 amd64 index](https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2604/x86_64/Packages).
No package was installed or executed during author inspection.

| Package, version `2.0.0.1790976895-1` | Bytes | SHA256 |
| --- | ---: | --- |
| `nvattest` | 321056 | `f717e8749cf51921b06871ec54084f56c03a6e93d5daeff967f3694f3c1155e1` |
| `libnvat` | 6152144 | `cca9409160a7bf793323cc0aafcdc1f569ecc99f346fc6abc6961c850a153b22` |

Exact package members (read as data without installation/execution):

| Installed file | Bytes | SHA256 |
| --- | ---: | --- |
| `/usr/bin/nvattest` | 1128696 | `9c269a22c3f4710cdb115ce21d72c1c3b8e9c9e2db9cf4a391b510ed1a1dea42` |
| `/usr/lib/x86_64-linux-gnu/libnvat.so.2.0.0` | 26794312 | `e757a59f8241830553da3f99f717ddd44d47973ca756a4fa3feac78012dce896` |

The library alias must be exactly `libnvat.so.2 -> libnvat.so.2.0.0`.
These packages contain no service unit or maintainer script (the control
archives contain only `control` and `md5sums`). An independently installed provider
agent is neither assumed present nor claimed absent.

Source reference:
[release 2026.10.02](https://github.com/NVIDIA/attestation-sdk/releases/tag/2026.10.02),
CLI/SDK 2.0.0, tag resolved to
`11b2bfeb42a1158aa08744603ecb239ccb3d2265`.
This is release/source correspondence, not a reproducible-build attestation
binding those binaries to that commit. The OS, Python, dynamic loader, TLS trust
store and installed driver/NVML remain host prerequisites, not newly qualified
by hashing these two files.

## Readiness discrepancy

[Verda's instructions](https://docs.verda.com/cpu-and-gpu-instances/confidential-computing/#run-nvidia-gpu-attestation)
say successful remote attestation automatically sets readiness. In contrast,
the pinned
[attestation flow](https://github.com/NVIDIA/attestation-sdk/blob/11b2bfeb42a1158aa08744603ecb239ccb3d2265/nv-attestation-sdk-cpp/src/attestation.cpp#L254-L326)
returns appraisal results without setting readiness. Its
[NVML wrapper](https://github.com/NVIDIA/attestation-sdk/blob/11b2bfeb42a1158aa08744603ecb239ccb3d2265/nv-attestation-sdk-cpp/src/gpu/nvml_client.cpp)
loads the setter symbol but never calls it. Do not infer installed behavior from
Verda's sentence or treat the existence of that symbol as a call.

Audit mode checks readiness before/after appraisal and rejects an unexpected
change. It does not infer persistence after its final NVML shutdown. The separate
explicit transition mode retains that appraisal check, then requires all eight
devices' existing persistence flags and rechecks the same live inventory before
setting readiness. There is no manual bypass.

## Invocation and bounds

Copy the committed helper and the exact `host/probe.py` alongside each other.
The latter's SHA256 is checked and its source compiled directly, without cached
bytecode or normal module resolution. Normal entry uses isolated/no-site Python:

```sh
# Read-only installed-artifact inspection, no NVIDIA service request:
python3 -I -S -B host/attest-lab-gpus.py

# Only after separate install authorization; directory holds the exact archives:
python3 -I -S -B host/attest-lab-gpus.py --install-packages /path/to/private/packages

# Only after separate operator approval for remote appraisal:
python3 -I -S -B host/attest-lab-gpus.py --attest
```

Installation and appraisal cannot be combined. Install mode requires root on
Ubuntu 26.04 x86_64, checked through the no-follow `/usr/lib/os-release`. It rejects
existing package registrations/files, so it cannot run an old package's upgrade
scripts. It checks both archive hashes and sizes before copying to private
exclusive files and invoking bounded `dpkg --no-triggers --install` on only those
two copies. There is no resolver, download, driver install or service restart.
[dpkg's documented trigger suppression](https://manpages.debian.org/trixie/dpkg/dpkg.1.en.html#no-triggers)
can leave unrelated trigger activations pending; this helper does not process
them automatically. Installation failure/cancellation may leave partial package
state, which requires operator inspection rather than an automatic retry or
rollback. The installed bytes, package versions and CLI help must subsequently
pass the same checks. Installation alone is unappraised and cannot pass.

The supervisor permits 285 seconds for the entire worker, including NVML calls;
the two remote CLI commands share a 240-second deadline. Both output streams are
bounded (4 MiB combined per CLI invocation); raw stderr is discarded. Parent
death, timeout and overflow kill the supervised group using the existing
`probe.capture` lifecycle. Nested supervisors retain the existing liveness-pipe
behavior. No ambient credentials, proxy, NVAT endpoint/policy/CA overrides or
loader overrides are inherited. Missing service entitlement/authentication
fails; the helper does not discover or borrow credentials.

Before appraisal, a shared 30-second offline preflight requires `dpkg-query` to
report both exact versions, amd64 architecture and installed status. It then
checks the actual pinned binary's root, `attest` and `verify-token` help for the
required options, including short-option aliases. These assertions establish
the installed CLI contract, not source/build correspondence or verified remote
service behavior. They follow the pinned
[CLI option definitions](https://github.com/NVIDIA/attestation-sdk/blob/11b2bfeb42a1158aa08744603ecb239ccb3d2265/nv-attestation-cli/src/utils.cpp).
Without `--attest`, completed inspection still emits `result:fail` and
`stage:inspection-complete-unappraised`: no JWT, nonce or device binding has
been verified. A contract, authentication or claim failure likewise cannot pass.

The fixed remote sequence is `attest --device gpu --verifier remote` with NVML,
a freshly generated 32-byte nonce, JSON/off logging and the fixed NRAS URL,
followed by `verify-token` with that same nonce and URL. No Rego override is
passed. Raw tokens reside temporarily in an owner-private directory/file and
are removed on normal/error return. Forced host/parent death may leave that
private temporary directory; it is not published as evidence.

Acceptance requires:

- Exactly eight accessible local NVML devices in production CC, devtools off,
  with protected-PCIe or NVLE multi-GPU selection.
- Eight unique authenticated GPU claims, all bound to the new nonce; default
  overall acceptance from both CLI calls, not merely parseable JSON or stdout.
- Successful measurements, secure boot/debug-disabled, explicit signature,
  RIM, version, architecture and nonce checks; valid, unexpired certificate
  chains with good OCSP status and no measurement/opaque mismatches.
- Each authenticated UEID equals one local leaf-certificate serial. NVIDIA's
  [GPU evidence source](https://github.com/NVIDIA/attestation-sdk/blob/11b2bfeb42a1158aa08744603ecb239ccb3d2265/nv-attestation-sdk-cpp/src/gpu/evidence.cpp#L308-L324)
  obtains UEID from certificate zero; its
  [X.509 code](https://github.com/NVIDIA/attestation-sdk/blob/11b2bfeb42a1158aa08744603ecb239ccb3d2265/nv-attestation-sdk-cpp/src/nv_x509.cpp#L1091-L1137)
  converts that serial to decimal. The helper only extracts the bounded
  [RFC 5280 certificate prefix](https://www.rfc-editor.org/rfc/rfc5280#section-4.1);
  it does not implement certificate or JWT cryptography.
- An identical local UUID/certificate/persistence inventory on recheck and
  unchanged readiness. Identities/certificates remain private in memory.

The
[pinned remote verifier](https://github.com/NVIDIA/attestation-sdk/blob/11b2bfeb42a1158aa08744603ecb239ccb3d2265/nv-attestation-sdk-cpp/src/gpu/verify.cpp#L578-L685)
authenticates the detached EAT and checks its nonce/overall result. The helper
then uses
[verify-token](https://github.com/NVIDIA/attestation-sdk/blob/11b2bfeb42a1158aa08744603ecb239ccb3d2265/nv-attestation-cli/src/verify_token.cpp#L257-L329)
again and checks the exact authenticated token's fields, not solely the CLI's
reserialized compatibility defaults. No missing OCSP nonce field is represented
as an independently observed OCSP nonce check.

Only fixed stages, booleans, counts, public source/artifact hashes and
readiness values are emitted. No UUID, UEID, nonce value, certificate, token,
claim body, environment, service response, raw exception or host path is emitted.
The JSON report alone is not a `nib.evidence/v1` acceptance record.
[`host/record-gpu-appraisal.py`](../../host/record-gpu-appraisal.py) records the
committed invocation, source hashes, UTC interval and sanitized outcome using
the existing private evidence writer. See the
[recording contract](p2-t3-gated-readiness.md#source-bound-recording).

## Software verification

`scripts/test_lab_attestation.py` exercises the real parser, filesystem checks,
capture lifecycle and fake attestation command flow. These fakes do not verify a
real signature or establish NVIDIA service compatibility. Negative cases cover
duplicate/missing/unbound devices, nonce mismatch, false/malformed claims,
revocation/expiry, output/deadline failure, file substitution, symlink/hardlink/
FIFO rejection, helper tampering, privacy and changed inventory/readiness.
