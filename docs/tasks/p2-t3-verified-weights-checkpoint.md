# P2.T3: Verified Weights And Readiness Blocker

These are real staging and diagnostic results from the approved compatibility
lab on 2026-10-20. Neither result establishes working inference or confidential
service qualification. Original recorder artifacts are preserved unchanged.

## Verified Model Files

[The completed staging record](../../evidence/p2/2026-10-20-weight-stage-verified/run.json)
reports success at 11:12:58 UTC:

- Model: `deepseek-ai/DeepSeek-V4.1-Flash-1017`.
- Revision: `1bae0e1088993b9b82de08f59de5be5a34e42170`.
- Manifest SHA256: `b74ae635c35372cf6c350e4b33ef60c90ff01fe6426ad0fa7bc99b1982fe5bed`.
- Verified: all 83 files, totaling 510,311,045,295 bytes.
- This continuation reused 60 files and downloaded 315,817,270,560 bytes.
- The four-worker run completed within its 7,200-second bound, with
  process-group cleanup confirmed.

The final `proof` in the [original output](../../evidence/p2/2026-10-20-weight-stage-verified/output.log)
is the integrity result. Its last periodic observation still says
`verified:false`; progress observations are deliberately not integrity proofs.
The complete pack must be revalidated before engine admission. Download success
is not a mounted immutable model volume, model load, or successful answer.

## GPU Readiness

The [corrected diagnostic](../../evidence/p2/2026-10-20-readiness-not-ready/run.json)
completed before the weights finished. It exposed four selected B200 GPUs to the
isolated container and recorded all 19 NVML calls succeeding.

The [original diagnostic output](../../evidence/p2/2026-10-20-readiness-not-ready/output.log)
reports production confidential-computing mode, devtools disabled, NVLE selected,
and 185,975,104 KiB of protected memory with zero unprotected memory on each
selected GPU. The first failed admission check was `readiness-policy`:
the driver returned `ready:0`.

This explains the earlier [undifferentiated hardware rejection](p2-t3-spheron-checkpoint.md).
It is a configuration observation, not cryptographic GPU attestation. No
readiness setter, application launch, model inference or benchmark ran as part
of this diagnostic. Container cleanup was confirmed.

## Limits

The host reports AMD SEV-SNP, not the production plan's Intel TDX. A successful
local answer would still be a compatibility result, not production confidential
qualification. GPU appraisal, readiness transition, actual inference, tau
validation and the final rental teardown require their own evidence.

The failed diagnostic recorder before this corrected run produced no complete
record; its fixed failure log remains preserved privately. This checkpoint does
not fabricate a successful run or replacement evidence for it.
