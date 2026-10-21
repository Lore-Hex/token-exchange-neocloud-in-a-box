# Complete Checkpoint and B300 Startup Failure

On 2026-10-21 the resumed stager verified all 83 files of the pinned DeepSeek
V4.1 Flash 1017 pack: 510,311,045,295 bytes. This is a complete hash proof, not
an inference claim. The [successful receipt](../../evidence/p0/2026-10-21-deepseek-stage-complete/run.json)
and [output](../../evidence/p0/2026-10-21-deepseek-stage-complete/output.log)
bind the exact model revision and manifest. The earlier
[client-timeout failure](../../evidence/p0/2026-10-21-deepseek-stage-client-timeout/run.json)
is preserved; its partial progress is not a verified pack.

A fresh [read-only GPU appraisal](../../evidence/p2/2026-10-21-gpu-appraisal-184213652419/run.json)
passed for all eight B300 devices without changing readiness. This proves the
recorded GPU claims only, not the CPU, complete fabric, image provenance or
production confidential-inference boundary.

The subsequent [full-model lab trial](../../evidence/p2/2026-10-21-b300-model-config-rejected/run.json)
**failed before inference**. The original
[output](../../evidence/p2/2026-10-21-b300-model-config-rejected/output.log)
records all five application stages completing, then the main server exiting
with code 1 after 25 seconds. Checkpoint 17 was reached. The content-free
trace maps to the pinned vLLM
[`ModelConfig` construction](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/engine/arg_utils.py#L1955)
through Pydantic. The underlying validator reason is not established by this
receipt. No completion or correctness result exists; cleanup was confirmed.

The exact committed producer was `0beb6fa56d63ee4ed80e3103c7851dabed69bca0`,
with the full local suite passing before the lab trial. Hosted checks were
queued at trial time; this was not permission to merge without green CI.
The B300/SNP lab is not the required B200/TDX production deployment. No
production eligibility, benchmark result or phase completion is claimed.

Regression tests bind the original artifact hashes, verify the completed
stage proof, preserve the prior null proof, check read-only appraisal
semantics and prevent the failed startup from being reported as success.
