# P2.T3: Initial Spheron Checkpoint

These are measured inventory and failed-run records, not working inference or
confidential qualification. The original recorder artifacts are unchanged.

## Results

| Record | Result | Meaning |
| --- | --- | --- |
| [Inventory](../../evidence/p2/2026-10-20-spheron-inventory/run.json) | Pass | The host reported eight B200 GPUs and AMD SEV-SNP interfaces, not Intel TDX. Read-only inventory is not attestation or runtime admission. |
| [Initial staging](../../evidence/p2/2026-10-20-weight-stage-initial-cancelled/run.json) | Fail | Operator-cancelled to revise the bounded transfer window. Process-group cleanup confirmed. |
| [Serial continuation](../../evidence/p2/2026-10-20-weight-stage-serial-cancelled/run.json) | Fail | Operator-cancelled before changing to the reviewed four-worker downloader. Process-group cleanup confirmed. |
| [Parallel continuation](../../evidence/p2/2026-10-20-weight-stage-parallel-cancelled/run.json) | Fail | Operator-cancelled to use the newly approved longer runtime. Process-group cleanup confirmed. |
| [Empty-weight readiness](../../evidence/p2/2026-10-20-deepseek-readiness-rejected/run.json) | Fail | Pinned image binding passed, but the experiment rejected at `hardware`, before weight validation or engine startup. Container cleanup confirmed. |

The staging checkpoints retain complete files and discard incomplete parts.
Their byte counts are unverified progress metadata, not a completed `StageProof`.
The latest cancelled run observed 194,493,774,735 complete-file bytes; the model
manifest requires 510,311,045,295 bytes across 83 files.

The readiness run used an intentionally empty model directory, separate from
the active download. It was meant to test hardware readiness without loading
the model, but it rejected earlier than the intended empty-weight check.
The report does not distinguish an NVML/bootstrap defect from unsupported
hardware state. A new source-bound diagnostic is required; repeating this run
unchanged or relaxing admission would not establish the cause.

## Verification And Limits

Each directory contains its generating commit, sanitized command, timestamps
and original `output.log`. The local copies were compared byte-for-byte with
the retrieved originals before submission.

No successful model answer, throughput benchmark, tau result, GPU attestation
or confidential production deployment is established here. The running
download and any later diagnostic or successful run require separate evidence.
The rental lifecycle is operator-private; this document does not authorize
additional spend or publish machine access details.
