# IFEval calibration: 200 selected prompts

**174/200 strict prompt passes (87.0%)**, **181/200 loose prompt passes (90.5%)**.
At the individual-instruction level, strict scoring passed 291/318 checks
(91.5%); loose scoring passed 298/318 (93.7%). All 200 requests completed with
HTTP 200, known integer usage and no retries, truncations or missing cases.
Median request latency was 2.920 seconds. Total usage was 10,069 input and
77,385 output tokens.

The pinned TrustedRouter benchmark's vendored IFEval prompt loader and strict/
loose graders are unchanged. This selects the first 200 of 541 prompts, not
the full benchmark. Requests were serial, temperature zero, reasoning disabled,
with an 8,192-token output cap. The standard receipt's pass means every selected
case was evaluated, not that every instruction was followed.

This measures the abliterated checkpoint on the retained ordinary-TLS node.
It is not a matched-reference comparison, confidential qualification, or a
production availability claim. No prompts or responses were retained. The
summarizer reverified the immutable benchmark source tree and reconciled every
request, score, token count and transmission marker before publication.

See [run.json](run.json), [numeric results](output.log), and the
[reproduction guide](../../../docs/tasks/p2-t3-retained-quality.md).
