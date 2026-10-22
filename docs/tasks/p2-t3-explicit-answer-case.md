# P2.T3: Explicit Casing in the Logic Probe

The October 22 diagnostic run reached a healthy DeepSeek engine and completed
ten requests. Seven of eight cases passed, including both two-turn tool cases.
The only failure was `logic`, classified `answer-case`: after existing whitespace
trimming, the response differed from the expected answer only in letter case.
No response content was retained. This rules out an opposite yes/no answer in
that run, but does not establish broad model quality.

Original [run](../../evidence/p2/2026-10-22-b300-answer-case/run.json) and
[output](../../evidence/p2/2026-10-22-b300-answer-case/output.log) remain unchanged
and **failed**. Producer `8cb7a5b91cdcb531ba8d23008ba6ce27fd1104c7` ran from
00:11:24 to 00:29:38 UTC, with 22,265 ms in the request phase. Cleanup passed,
with no OOM kill. The earlier non-diagnostic failure is not retrospectively
reclassified.

## Fixture Correction, Not Relaxed Acceptance

The previous prompt requested "yes or no" but did not explicitly require the
case-sensitive formatting enforced by the comparator. The v3 prompt now asks for
exactly one lowercase word without punctuation or other text. The logical
premises and expected answer are unchanged. No answer normalization, retry,
sampling, token budget, tool protocol or engine configuration changes.

The comparator still rejects `Yes`, `yes.`, `Yes!`, `no` and extra text. Tests
restore the old prompt and assert the original entire case-set hash, proving the
other seven cases, tools and all expected answers remain unchanged. Both the
contract and report move to v3; old reports cannot be validated against the new
case set. The original failed artifacts have separate hash-integrity tests.

The new case set requires a fresh hardware run. This change alone proves neither
functional success, tau acceptance nor a confidential service. Full local
`make check` and all final-head hosted checks are required before merge.
