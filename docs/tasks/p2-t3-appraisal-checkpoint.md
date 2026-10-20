# P2.T3: GPU Appraisal Checkpoint

This preserves unsuccessful setup checks and subsequent source-bound GPU
appraisal/readiness observations on October 20, 2026, after the
[verified weight download](p2-t3-verified-weights-checkpoint.md). The later
eight-GPU appraisal passed; no inference result, full confidential qualification
or phase exit follows from it.

## Preserved Reports

Both JSON files below are unchanged output from the committed helper, not
reconstructed results. Neither original invocation used a source-bound
`run.json` recorder. Exact execution timestamps and a complete canonical
evidence envelope are therefore unavailable; do not invent them or use these
reports to satisfy a hardware acceptance criterion. A later recorder must
capture those fields at execution time.

| Operation | Committed helper source | Original report | Result |
|---|---|---|---|
| Install two exact local NVIDIA archives, then inspect | `49387f339e1ae06136cc49367d6ca9cdd4cf51c2` | [installation report](../../evidence/p2/2026-10-20-gpu-appraisal-blocked/nvidia-install-49387f3.json) | Installation completed; subsequent pinned-file inspection failed. No appraisal ran. |
| Fresh audit-only `--attest` | `a7a54c28ef3a0df78ceba26d7a96ac4db5b52e22` | [appraisal report](../../evidence/p2/2026-10-20-gpu-appraisal-blocked/nvidia-appraisal-a7a54c2.json) | CLI contract passed; eight persistence-enabled GPUs and readiness zero observed; failed at `claims-policy`; NVML shutdown completed. |

The source-relative invocation forms were:

```sh
python3 -I -S -B host/attest-lab-gpus.py --install-packages PRIVATE_PACKAGE_DIRECTORY
python3 -I -S -B host/attest-lab-gpus.py --attest
```

`PRIVATE_PACKAGE_DIRECTORY` is a placeholder, not an exact command record.
Operator paths, access details and credentials are intentionally absent.

## Interpretation

The installation did not change the driver, confidential-computing mode or
readiness. The follow-up source correction in
[PR #57](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/pull/57)
excluded symlink access time from identity comparisons and recognized the
pinned CLI's help formatting. It did not relax cryptographic claims.

The audit reached `claims-policy` only after both the remote attestation and
signed-token verification commands returned successfully. That is not
equivalent to the helper accepting the claims. The original report does not
identify the rejected field, so the precise cause remains unproven here.
Readiness was not enabled, and no engine or model ran.

NVIDIA's pinned SDK may add schema fields when serializing authenticated
claims. That is a candidate compatibility issue, not a diagnosis of this
run: see the
[GPU serializer](https://github.com/NVIDIA/attestation-sdk/blob/11b2bfeb42a1158aa08744603ecb239ccb3d2265/nv-attestation-sdk-cpp/src/gpu/claims.cpp)
and [claims deserializer](https://github.com/NVIDIA/attestation-sdk/blob/11b2bfeb42a1158aa08744603ecb239ccb3d2265/nv-attestation-sdk-cpp/src/claims.cpp).
Those reports predate the source-bound diagnostic below. They are not
retrospectively rewritten or assigned its exact cause.

## Source-bound Appraisal and Readiness

The following directories are unchanged recorder output, including each original
`run.json` and `output.log`. Their four source-file hashes were checked against
the recorded Git commit; regression tests reconstruct the corresponding original
report validator. No raw JWT, nonce, device identifier, host address or access key
is included.

| UTC interval on 2026-10-20 | Committed source | Record | Observation |
|---|---|---|---|
| 12:02:01.396352 to 12:02:05.314939 | `a23e66b49acf62a8f8547a276f05fd9fec096297` | [run](../../evidence/p2/2026-10-20-gpu-appraisal-120201396352/run.json), [output](../../evidence/p2/2026-10-20-gpu-appraisal-120201396352/output.log) | Audit-only FAIL: `claims-policy`, `claim-missing`, signed-token field `x-nvidia-device-type`. Eight persistence flags enabled, readiness before zero, no setter requested, NVML shutdown succeeded. |
| 12:14:47.763215 to 12:15:00.191921 | `12914b4d7f576e0c0abd66d4915763dbbb68f0b5` | [run](../../evidence/p2/2026-10-20-gpu-appraisal-121447763215/run.json), [output](../../evidence/p2/2026-10-20-gpu-appraisal-121447763215/output.log) | PASS: fresh appraisal accepted eight distinct locally bound GPU identities; all recorded nonce, measurement, certificate/revocation, secure-boot and debug-disabled checks passed. Existing persistence count eight; readiness changed 0 to 1 with verified readback; NVML shutdown succeeded. |

The later source contains the independently reviewed
[raw-token serialization compatibility](p2-t3-nvidia-attestation.md#raw-token-serialization-compatibility):
four documented derived/empty fields may be absent from an already authenticated
raw GPU payload. It does not default security assertions or accept conflicting
present fields. The failed record remains a failure.

The successful report proves only the helper's eight-GPU appraisal and
point-in-time readiness transition contract. It does not prove readiness after
shutdown indefinitely, GPU memory coherence, engine execution, inference,
correctness, performance, CPU or fabric attestation. Both records intentionally
retain `execution:software`, `hardware_validated:false`, `runtime_ready:false`,
`confidential_qualified:false` and `deployable:false`. The driver/CUDA fields
remain null rather than being invented.

No completed bounded local correctness result is recorded in this PR. That
operation's outcome must be preserved separately before any model-working or
quality claim.

## Remaining Gates

- Future readiness transitions still require fresh eight-GPU appraisal; this
  point-in-time success is not reusable authorization or a saved-token bypass.
- Obtain completed bounded engine and correctness evidence after runtime admission.
- Measure actual inference and quality before asserting model compatibility.
  The local fixed-case suite is not tau and cannot substitute for it.
- The current AMD SEV-SNP lab does not satisfy the plan's Intel TDX,
  reproducible image, NVSwitch, attested TLS, production verifier or
  marketplace qualification gates.
- New hardware, paid reference calls and publication remain separately gated.
  No complete-phase or production-confidential claim follows from this record.
