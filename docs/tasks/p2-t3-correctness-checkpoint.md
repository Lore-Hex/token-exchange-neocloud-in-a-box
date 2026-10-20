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

## Validated Startup Failure

The next source-bound attempt used
`bac13b0fac39096a0990417f3cfcf4e623e911ac`, from 12:46:35 to 12:54:21 UTC.
Its original [run envelope](../../evidence/p2/2026-10-20-correctness-startup-failed/run.json)
and [output](../../evidence/p2/2026-10-20-correctness-startup-failed/output.log)
are preserved unchanged. The corrected recorder retained and validated the
inner result:

- Four selected B200 GPUs reported readiness enabled, production CC,
  devtools off and NVLE.
- Full weight validation and all five exact-image application stages
  completed before startup.
- The engine exited with code 1 after 8,003 milliseconds and eight health
  probes, before becoming ready.
- The outer container exited 0 and Docker reported `OOMKilled=false`.
  Container cleanup was confirmed.
- Completion and correctness results are null; functional pass is false.

This is a genuine engine-startup failure, not a successful model run. The
report narrows the boundary but does not identify the failing import, parser
or internal check. No exception text or engine log was retained. The next
fixed-ID checkpoint diagnostic was not executed on this hardware.

## Rental Teardown

Operator observation through the Spheron UI on 2026-10-20 at approximately
13:04 UTC confirmed the disposable test instance terminated, zero running
or reserved instances, and zero storage volumes. Reported total usage was
$445.53 over 5.62 hours, within the six-hour/$480 authorization. The dedicated
test SSH registration and local key pair were removed after evidence retrieval.

These teardown and billing figures are operator observations from the provider
UI, not measurements from the model recorder or invoice reconciliation. They
do not alter either original evidence envelope. No test resource remains
available for further hardware runs under this authorization.
