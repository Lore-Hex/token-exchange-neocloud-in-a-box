# Eight-B300 Lab Appraisal

On October 21, 2026, the committed recorder at
`6e864bebbb123a79f063ca562bcd686b8ab5365c` appraised all eight GPUs on the
approved replacement test node. The original records below are copied without
rewriting their contents. They contain no host address, device identifier, key,
nonce, token or inference content.

| UTC start | Record | Actual result |
| --- | --- | --- |
| 15:20:11 | [Inventory](../../evidence/p2/2026-10-21-hardware-probe-152011207366/output.log), [run](../../evidence/p2/2026-10-21-hardware-probe-152011207366/run.json) | Eight B300 GPUs, each 275040 MiB, driver 595.91.07. CPU is AMD; SEV guest and TSM interfaces are present, Intel TDX is absent. The existing B200-only inventory gate correctly reports failure. |
| 15:20:44 | [Installation](../../evidence/p2/2026-10-21-gpu-appraisal-152044945944/output.log), [run](../../evidence/p2/2026-10-21-gpu-appraisal-152044945944/run.json) | Two exact NVIDIA appraisal packages installed; CLI inspected. Overall result remains failed/unappraised because no attestation was requested. |
| 15:21:39 | [Fresh appraisal](../../evidence/p2/2026-10-21-gpu-appraisal-152139222557/output.log), [run](../../evidence/p2/2026-10-21-gpu-appraisal-152139222557/run.json) | PASS: eight distinct locally bound GPU identities; nonce, measurements, certificate/revocation, secure-boot and debug-disabled checks accepted. Readiness changed from 0 to 1 with readback verification; persistence count eight and NVML shutdown successful. |

These are real inventory and GPU-appraisal observations, not inference or a
complete confidential-service qualification. The recorder deliberately retains
`hardware_validated:false`, `runtime_ready:false`, `confidential_qualified:false`
and `deployable:false`. Null CUDA/driver fields in the appraisal record have not
been backfilled from the separate inventory.

The source-bound appraisal test verifies the original record hashes, rebuilds
its validator from the recorded Git revision and checks all eight GPU claims.
It also prevents the AMD/B300 inventory from being interpreted as Intel TDX or
eight B200s. Earlier failed runs remain in the repository.

No completed engine load, correctness suite, tau benchmark, CPU/fabric
verification, measured image, attested TLS or marketplace qualification is
claimed by this checkpoint. Readiness must be freshly appraised for subsequent
operations; the stored report is not reusable authorization.

See [appraisal semantics](p2-t3-appraisal-checkpoint.md) and
[the measured guest contract](../decisions/0004-measured-guest-image.md).
