# First DeepSeek Generations

The bounded B300 lab run from producer
`8885dc941ed27bdcbfb85da4a0667b92ad92af6b` reached a healthy DeepSeek
V4.1 Flash 1017 engine and completed ten synthetic requests. It ran from
23:40:18 to 23:58:34 UTC on October 21, 2026. The original
[run](../../evidence/p2/2026-10-21-b300-first-generations/run.json) and
[result](../../evidence/p2/2026-10-21-b300-first-generations/output.log) remain
byte-identical, including their overall **fail** result.

- Exact weight verification and all five application stages completed.
- Engine startup reached health and exact model identity in 767,272 ms
  (about 12.8 minutes), with no entry or worker exception reported.
- Seven of eight cases passed: arithmetic, ordering, extraction, both JSON
  checks, and both two-turn tool-call continuations.
- The logic case failed exact-answer comparison, with a valid response protocol
  and usage counts. The response content was not retained, so this result alone
  cannot distinguish a wrong logical answer from a case/punctuation mismatch.
- The request phase took 23,009 ms. These are whole-request timings, not TTFT
  or a throughput benchmark. No quality or tau score is inferred.
- Engine shutdown and container cleanup were confirmed; no OOM was reported.

Four B300 GPUs were selected from the eight-GPU lab allocation. The runtime
observed CC enabled, devtools off, production environment and NVLink encryption.
This observation is not end-to-end attestation. The host uses AMD SEV-SNP, not
the plan's TDX deployment; image/native provenance, CPU/fabric/service binding,
quality, reproducibility and production listing are still unqualified.

## Diagnostic Follow-up

The smoke report and contract advance to v2 to distinguish exact-answer failures
as `answer`, `answer-case`, `answer-punctuation`, or `answer-case-punctuation`.
These are finite comparison categories computed in memory, not response content
or response hashes. Every category still fails the original exact-answer check.
Case folding or removing a final period/exclamation mark is diagnostic only;
it is never used to accept an answer.

The eight prompts, expected answers, tool schema, ten-request limit, temperature,
reasoning setting, output limits, deadlines, no-retry policy and case-set digest
are unchanged. No past failure is reclassified. A new run is an explicitly
recorded diagnostic experiment, not an unreported rerun until success. Tests
prove formatting variants still fail, fabricated success is rejected, and
response bodies do not enter evidence.
