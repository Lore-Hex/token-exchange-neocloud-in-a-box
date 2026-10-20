# P2.T3: GPU Appraisal Checkpoint

This records unsuccessful GPU setup checks on October 20, 2026, after the
[verified weight download](p2-t3-verified-weights-checkpoint.md).
It is not a successful attestation, inference result or phase exit.

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
The next diagnostic must name only fixed rejection categories, never emit
tokens, nonces or device identifiers, and preserve fail-closed behavior.

## Remaining Gates

- Authenticate and bind all eight GPU results with a fresh nonce before any
  readiness transition; never import a previous success or bypass appraisal.
- Start the bounded local engine and correctness suite only after admission.
- Measure actual inference and quality before asserting model compatibility.
  The local fixed-case suite is not tau and cannot substitute for it.
- The current AMD SEV-SNP lab does not satisfy the plan's Intel TDX,
  reproducible image, NVSwitch, attested TLS, production verifier or
  marketplace qualification gates.
- New hardware, paid reference calls and publication remain separately gated.
  No complete-phase or production-confidential claim follows from this record.
