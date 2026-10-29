# Original cache inspection PASS

[receipt.json](receipt.json) and [output.log](output.log) preserve the
operator-supplied originals byte-for-byte. The receipt is 1,841 bytes, SHA-256
`8b86373a8c9e29d9e7b71cb979cf988611e0a00a0bc19df65541661ab8251312`;
the log is empty, SHA-256
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
The producer is `f5ded64c3bf1b41a8a38fcda6c83620e65a3bcfc`, reachable through
normal merge ancestry. Its inspector and four helper source hashes match the
receipt. No operator identities, addresses, paths or private journals are
included.

The receipt reports **PASS** for all 83 original-profile files, totaling
510,311,045,295 verified bytes, during
2026-10-29T05:28:22.888435+00:00 to 05:54:15.736950+00:00. Worker exit, private
namespace verification and private mount cleanup are confirmed. The resource
section reports limit readback, empty membership, group removal, zero OOM kills
and an 8,589,934,592-byte memory peak, equal to the configured memory limit.
These observations do not establish spare memory capacity or absence of pressure.

`process_group_absence_verified=false` is preserved unchanged. Separately, the
operator reports later owned process/cgroup absence from a private monitor.
That external observation is not published here or substituted for any original
receipt flag.

[run.json](run.json) is preservation metadata, not a rerun. Its command retains
inspector flags but redacts private values; it is not an executable replay or
the private controller invocation. `execution=software` describes actual
CPU/file-integrity verification of stored bytes, not synthetic data or GPU
hardware qualification. Driver and CUDA versions were not measured.

The receipt keeps `immutable`, `runtime_ready` and `confidential` false. This
interval-specific PASS does not prove continued availability or authorize image
import, inference, deployment or a new attempt. No live action was performed to
preserve this evidence. The [cancelled inspection](../2026-10-29-original-cache-inspection-cancelled/README.md)
and [earlier failure](../2026-10-29-original-cache-inspection-failure/README.md)
remain separate and unchanged.
