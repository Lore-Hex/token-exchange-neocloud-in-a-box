# P3.T1: Bounded Single-H100 Appraisal Collector

Status: software-only opt-in diagnostic. No vendor request, GPU appraisal, boot,
readiness transition or confidential service has been validated by this change.
The production GPU gate and release verifier remain fail-closed. The executable
is excluded from default builds and default images. An explicit, non-activating
image composition path is now implemented and tested offline; no native image
containing this collector has been built or booted by this change.

## Existing Inputs and Source

The collector uses the installed CLI already authenticated by the
[guest appraiser probe](p3-t1-appraisal-cli.md), not a new SDK binding or verifier.
The existing [Ubuntu 24.04 guest lock](../../image/inputs/guest-runtime-ubuntu2404.json)
pins `nvattest` and `libnvat` to `2.0.0.1790976895-1`; the driver is `595.91.07`.
Runtime admission checks the exact CLI/library sizes and SHA-256 values from
`scripts/mkosi_appraisal_probe.py`, their fixed library alias and read-only EROFS
paths. A mandatory parity test also compares the existing lab appraiser's
security predicates and its four serialization defaults.

The authenticated Debian members are root-owned: `nvattest` is mode `0755`,
`libnvat.so.2.0.0` is `0644`, and `libnvat.so.2` is the exact relative symlink.
These modes were read from the locked Ubuntu 24.04 archives, not inferred from
the filename. The archive SHA-256 values are respectively
`77eaec7644b6a221147245a1c0643bac7317265815e3483ef12f283fc91badb0`
and `d6ae20d7f0d90c62c5e99bcac29cb66f3d9e18c7ee2c4de3aee40134edd70353`.
Unexpected executable bits on the library also reject.

Primary source is NVIDIA SDK commit
[`11b2bfeb42a1158aa08744603ecb239ccb3d2265`](https://github.com/NVIDIA/attestation-sdk/tree/11b2bfeb42a1158aa08744603ecb239ccb3d2265):

- [CLI options](https://github.com/NVIDIA/attestation-sdk/blob/11b2bfeb42a1158aa08744603ecb239ccb3d2265/nv-attestation-cli/src/utils.cpp)
  define collection, file-evidence appraisal, detached-EAT verification, nonce,
  fixed NRAS URL and disabled logging options.
- [Evidence serialization and file-source nonce checking](https://github.com/NVIDIA/attestation-sdk/blob/11b2bfeb42a1158aa08744603ecb239ccb3d2265/nv-attestation-sdk-cpp/src/gpu/evidence.cpp)
  define `arch`, `nonce`, base64 report and certificate chain.
  [Certificate serial extraction](https://github.com/NVIDIA/attestation-sdk/blob/11b2bfeb42a1158aa08744603ecb239ccb3d2265/nv-attestation-sdk-cpp/src/nv_x509.cpp)
  uses the first certificate's decimal serial as `ueid`.
- [Token verification](https://github.com/NVIDIA/attestation-sdk/blob/11b2bfeb42a1158aa08744603ecb239ccb3d2265/nv-attestation-cli/src/verify_token.cpp)
  supplies the expected nonce and default JWT validation to the SDK. Nonzero
  results, including an authenticated negative appraisal, fail the collector.
- [GPU claims serialization](https://github.com/NVIDIA/attestation-sdk/blob/11b2bfeb42a1158aa08744603ecb239ccb3d2265/nv-attestation-sdk-cpp/src/gpu/claims.cpp)
  supplies no missing security-positive predicates. Only the same four absent
  serialization defaults as the existing lab helper are allowed when examining
  the authenticated signed payload.
- [Remote appraisal tests](https://github.com/NVIDIA/attestation-sdk/blob/11b2bfeb42a1158aa08744603ecb239ccb3d2265/nv-attestation-cli/tests/attest_tests.cpp)
  distinguish remote `GH100` from the longer local certificate CN. This collector
  accepts only `HOPPER` evidence and authenticated remote `GH100` claims, not
  arbitrary aliases. That is not independent H100 capacity/SKU qualification.

Inspected source SHA-256 values:

| Source member | SHA-256 |
| --- | --- |
| `nv-attestation-cli/src/collect_evidence.cpp` | `27635bed11b4eaa8904dfb3bf94381cc0b6caf7bca2386c58ebd6aa38f6a17cd` |
| `nv-attestation-cli/src/attest.cpp` | `2db79640f550c66fd290792990e087f69eef3477e4a0f759fa1441be692f2848` |
| `nv-attestation-cli/src/verify_token.cpp` | `f7e0219301531f14339c6adb9351042e07724ddf1ca79b232edf7f56472b639e` |
| `nv-attestation-cli/src/utils.cpp` | `7d089a0210517aba151e74d445b69215e066783bbe1ff076215e6c0aabb08825` |
| `nv-attestation-sdk-cpp/src/gpu/evidence.cpp` | `91a98136be778a75dd3da53f9cd4c38735b16fd09bbf4d5714fa2675bc0e52ac` |
| `nv-attestation-sdk-cpp/src/gpu/claims.cpp` | `122e5c6a4c7aa54c393e5ff7d1b0dae3d7256c051426743f2463990c284fd203` |

## Closed Diagnostic Flow

The explicit `nibh100appraisal` build tag produces the static Linux/amd64
`guest/appraisal/cmd/nib-h100-appraisal` command. Its only accepted arguments are
`--collect-and-appraise --deadline <UTC RFC3339 seconds>`, at most 285 seconds
ahead, reserving five seconds for cleanup. It accepts no executable, endpoint,
input evidence, policy, credentials or unsafe executor override.

1. Require an already unprivileged UID/GID, no supplementary groups, zero
   effective/permitted/inheritable/ambient capabilities, `NoNewPrivs=1`, and
   seccomp filtering. This command refuses root; it does not perform root setup.
2. Generate a fresh 32-byte nonce. Collect all NVML-visible devices once and
   require exactly one evidence row. Save it in exclusive 0600 files under a
   private 0700 directory on the admitted root-owned sticky `/tmp` tmpfs.
3. Appraise those exact saved bytes using fixed NRAS remote verification. Verify
   the exact returned detached EAT with the same nonce. Require agreement of
   appraisal/verification claims and check the original signed device payload.
   Go certificate/JWT decoding is not authentication; the pinned SDK does that.
4. Require all existing positive security predicates, expected driver/device/
   nonce identity, disabled debug, successful measurements, no mismatches, and
   valid, unrevoked, unexpired attestation/driver/VBIOS certificate chains.
5. Recollect once and require the same serial and exact certificate-chain bytes;
   recheck claim expiry. Require scratch removal and absence of every owned
   process group before `cleanup_confirmed=true` or a successful result.

No wrapper retries or new nonces are created after a failed step. The SDK itself
has [HTTP retry defaults](https://github.com/NVIDIA/attestation-sdk/blob/11b2bfeb42a1158aa08744603ecb239ccb3d2265/nv-attestation-sdk-cpp/include/nvat.h.in)
(five retries, 5-second connect and 30-second request timeouts); this is **not** a
zero-HTTP-retry claim. All four CLI invocations share the original monotonic
deadline; a wall-clock forward jump also cancels. A clock rollback cannot extend
the original budget. Owned groups are killed and the direct child reaped on
cancellation, overflow or failure. Linux also sets a parent-death signal while
keeping the creating OS thread alive. Unconfirmed group absence remains failure,
even after a kill request. Kernel-uninterruptible I/O and a killed supervisor
cannot produce a success receipt or a guaranteed cleanup claim.

Each stdout/stderr stream and parsed JSON value is capped at 64 KiB. The existing
strict guest JSON decoder rejects duplicate keys, deep nesting, trailing data and
invalid UTF-8. CLI stdout/stderr, certificate identities, tokens, nonces, paths
and exception text are never forwarded. The report contains only a fixed schema,
stage/result, public SDK source, original deadline, count and booleans. Ambient
credentials, proxies, SDK overrides and loader variables are not inherited.

## Remaining Gates

`runtime_ready`, `boot_verified`, `assigned_inventory_verified`, `confidential`
and `deployable` are always false, including after successful SDK appraisal.
The NVML-visible count is not a trusted assigned-device manifest. No collector
result is bound to a CPU quote, boot measurements, TLS key or production policy.
The read-only path check is not authentication of the EROFS image or its complete
dynamic-loader/NVML dependency closure. No readiness setter is called.

A separately reviewed image/launcher must provide measured dependency closure,
least-privilege device access, bounded network admission and scratch, and an
authenticated expected inventory. The current short production GPU-gate service
is not this launcher. Real H100 evidence, vendor availability, unprivileged NVML
access and complete image integration remain untested. No existing B300 lab
success is promoted into H100 or confidential validation.

The [offline static-network prerequisite](p3-t1-h100-network-prerequisites.md)
now prepares private, source-bound candidate files without activating them.
It records the remaining network, nonroot NVML and memory-control acceptance
tests; it does not make the collector image runnable or change these gates.

Software checks use synthetic certificates/claims and fake SDK output, never
cryptographically valid vendor tokens. They cover every missing/false positive,
revocation/expiry, malformed JSON and signed payloads, count/identity substitution,
cancellation, clock jumps, private cleanup and bounded real child processes.
Cross-build tests require a static opt-in binary and only standard-library/local
guest dependencies. Linux runtime-admission negatives run on Linux CI; an ordinary
host is deliberately rejected. These tests cannot prove real device appraisal.

## Opt-In Image Composition

The existing [native launcher](../../host/run-mkosi-build.py) accepts
`--h100-collector --go ABSOLUTE_COMPILER` only with the complete existing
`--compile-driver --install-driver --guest-runtime --verity-root --boot-uki
--export-boot` chain. This is a separate `h100-collector/v1` profile, mutually
exclusive with `--guest-network`. No workflow input or dispatch is added, and
the default/debug boot consumers do not adopt a profile from an artifact.

The [source/export validator](../../scripts/mkosi_export.py) requires the exact
42-file native export closure plus 12 enumerated source members: the existing
composition helper, Go module files, nftables sources/embed and the Linux
collector sources. Both committed-source audits must share a revision and
identical overlap. Missing, extra, mixed or changed sources reject. The
[shared helper](../../scripts/mkosi_guest_network.py) compiles only the explicit
`nibh100appraisal` tag using the existing Go 1.27.2 offline environment, a private
source/cache tree and one bounded compilation deadline. The dependency allowlist
is standard library plus the collector and nftables packages. ELF inspection
requires static Linux/amd64 bytes with no interpreter or dynamic segment.
The compiler hash/version is **observed, not authenticated**; this is not a
toolchain provenance or reproducibility qualification.

Composition installs only `/usr/libexec/nib-h100-appraisal`, mode `0755`, in the
private guest root before verity generation. Descriptor-relative checks reject
collisions, symlinks, hard links, unsafe parent modes, changed bytes and source
mismatches. Existing image files are not replaced. There is no collector
execution, unit, account, activation link, device/network permission grant,
readiness setter, verifier integration or policy change. A failed or cancelled
build cannot finalize a private export; existing cleanup gates still apply.

The input receipt binds the full source identity, observed compiler identity,
compile-source hashes and exact binary size/SHA-256. The worker validates and
rehashes the installed bytes; the host compares the composition inputs with its
original compilation receipt. The original `result.json`, including that proof,
is hashed in the existing private bundle manifest alongside the unchanged
`run.json` and independent resource receipt. The
[export consumer](../../host/validate-native-boot-export.py) must explicitly
select `--h100-collector`, re-audit the same sources and rehash the private bundle;
the default consumer rejects it. Every execution/readiness/confidentiality claim
in the composition proof remains false.

[Synthetic tests](../../scripts/test_mkosi_h100_collector.py) cover deterministic
staging, default opt-out payload-byte identity, exact closure/tag/dependencies,
mutated compiler/source/output, malformed/static ELF boundaries, unsafe paths,
collisions, cancellation and private-receipt replay. The
[shared worker tests](../../scripts/test_mkosi_guest_network.py) check composition
before verity and no export after failure for both profiles. Default byte checks
are software fixtures, not a repeat native build or a new measured image hash.
Existing native/TCG receipts are unchanged and do not validate this new profile.

```sh
go test -race -count=1 ./guest/appraisal ./guest/nftables
python -m unittest discover -s scripts -p 'test_h100_appraisal_contract.py'
python -m unittest discover -s scripts -p 'test_mkosi_h100_collector.py'
```
