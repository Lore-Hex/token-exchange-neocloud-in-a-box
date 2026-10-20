# DeepSeek Correctness Attempt

The source-bound attempt at `26a7240b53ee0590d98655b12c6b57ca4779c2aa`
ran from 2026-10-20 12:15:36 UTC to 12:23:24 UTC after the independently
recorded GPU-readiness transition. The original
[run envelope](../../evidence/p2/2026-10-20-correctness-recorder-failed/run.json)
and [output](../../evidence/p2/2026-10-20-correctness-recorder-failed/output.log)
are preserved byte-for-byte.

The result is **FAIL**, outer stage `experiment`, with failure category
`bounded-lab-stage-failed` and confirmed container cleanup. No validated inner
`experiment` field was retained. Consequently, this artifact cannot establish
engine startup, successful inference, throughput or a correctness score.
No tau benchmark ran. Its execution classification remains `software` and its
host facts remain unobserved because the recorder did not retain a validated
hardware result; these original fields have not been reconstructed afterward.

Independent source review subsequently found that the recorder compared the
application writer's complete-report digest with a files-only inventory
digest. Those are different hash domains. A regression using the real writer
reproduces that defect, but the opaque original output does not prove it was
the first failure encountered by this particular run.

The follow-up adds fixed outer recorder steps, pre-cleanup container-state
observations and content-free startup outcomes, while correcting the invalid
digest comparison separately. Neither a software fix nor successful GPU
appraisal establishes full CPU, fabric, application, model or TLS attestation.
The candidate remains BLOCKED and non-deployable.
