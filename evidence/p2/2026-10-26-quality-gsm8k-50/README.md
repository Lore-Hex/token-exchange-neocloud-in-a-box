# GSM8K calibration: 50 selected cases

The first 50 GSM8K test cases scored **50/50**, with zero errors, truncations,
missing cases or unknown usage. Median request latency was 2.107 seconds.
Total usage was 4,511 prompt and 9,284 completion tokens.

The live runner used the exact prompt suffix and numeric-answer scorer from
[TrustedRouter benchmarks revision 92c1f4d](https://github.com/Lore-Hex/trustedrouter-benchmarks/tree/92c1f4dea58e32fa772f8ee77b69cd9d7cf67bac).
The dataset and source hashes were fixed before calls. Requests were serial,
temperature zero, reasoning disabled, maximum 8,192 output tokens, with no
retries. Prompts, replies and grading diagnostics were not retained.

This directly exercises the selected abliterated checkpoint through the retained
node's pinned ordinary-TLS endpoint, not TrustedRouter routing or billing.
Fifty selected cases are not the full 1,319-case suite or a matched-reference
quality comparison. A perfect small-sample result does not prove general
correctness. The standard receipt's pass means all selected cases were evaluated.

See [run.json](run.json), [numeric observations](output.log), and the
[reproduction instructions](../../../docs/tasks/p2-t3-retained-quality.md).
