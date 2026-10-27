# MMLU-Pro calibration: 200 selected cases

**171/200 correct (85.5%)**. All 200 requests returned HTTP 200 with integer
usage. One answer reached the explicit 8,192-output-token calibration cap and
counts as incorrect, not as a completed answer. There were no missing cases or
unknown usage. Median request latency was 2.751 seconds; total usage was 41,733
input and 87,598 output tokens.

The pinned TrustedRouter benchmark loader selected 200 cases by systematic
stride across its 12,032-case dataset, preserving proportional category
coverage. The prompt formatter and deterministic answer matcher are unchanged.
Requests were serial, temperature zero, reasoning disabled, no retries. The
standard receipt reports fail because one case truncated; the answer accuracy
still uses the full 200-case denominator.

This is a selected-case calibration of the abliterated checkpoint on the retained
ordinary-TLS endpoint, not the full MMLU-Pro suite, a matched-reference comparison,
or confidential qualification. No response content was retained.

The report was rebuilt using the stricter summarizer after independent review.
It reverified the immutable benchmark tree against the original manifest hash,
validated the per-case measurements and preserved the original scores. No
inference was repeated or selected away. See [run.json](run.json),
[numeric results](output.log), and the
[reproduction guide](../../../docs/tasks/p2-t3-retained-quality.md).
