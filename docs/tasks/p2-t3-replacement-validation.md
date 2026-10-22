# P2.T3: Replacement GPU and Final Parent-Pipe Checks

These are two separate observations, not an end-to-end confidential result.
The original committed-script artifacts are copied without edits and protected
by exact-hash regression tests.

## Eight-GPU Appraisal

The [replacement-host appraisal](../../evidence/p2/2026-10-22-replacement-gpu-appraisal/run.json)
ran on 2026-10-22 at 07:42 UTC using source `319201ea33a63266755118b1305cd696314c8a83`.
The [numeric result](../../evidence/p2/2026-10-22-replacement-gpu-appraisal/output.log)
reports eight authenticated, distinct GPUs, matching local identities and nonce,
valid certificate/revocation checks, secure boot, debug disabled and matching
measurements. Readiness was enabled and read back as 1.

This verifies only that appraisal scope at that time. It does not prove the
CPU trust domain, guest image, model weights, TLS binding, fabric, runtime or
service. The original receipt deliberately remains non-deployable and not
confidential-qualified. No raw device identities, nonce, tokens, credentials or
private endpoint details are published.

## Final Reviewed Parent-Pipe Trial

The [final-head trial](../../evidence/p2/2026-10-22-tau-pipe-final-head/run.json)
ran on 2026-10-22 from 12:08:14 to 12:08:23 UTC using reviewed source
`55e5ba6dce65ad3260e7747d10f4494341052b8e` and a newly source-bound payload.
It passed in the real isolated Linux/amd64 container, with upstream tau
orchestration and four scripted role calls: one agent, two simulator and one
evaluator. The [result](../../evidence/p2/2026-10-22-tau-pipe-final-head/output.log)
records known synthetic accounting, successful completion and confirmed cleanup.

It is not a DeepSeek inference run, tokenization result, quality score or live
transport acceptance. The scripted reward is a fixture result only. Existing
failed and earlier successful trials remain unchanged. The next quality step
still requires real candidate/reference models, independent simulator/evaluator
roles, runtime-bound tokenization/accounting and the prescribed benchmark set.

Verification:

```
uv run --project tools --frozen python -m unittest discover -s scripts -p 'test_replacement_validation_evidence.py'
make check
```
