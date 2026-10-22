# P2.T3 fixed tokenizer parity on eight B300 GPUs

The original [run envelope](../../evidence/p2/2026-10-22-tokenizer-parity-fixed-pass/run.json)
and [content-free output](../../evidence/p2/2026-10-22-tokenizer-parity-fixed-pass/output.log)
record a real run of producer `8e2236c251e8fde607de4506d0550fd72b1e7775`
from 17:12:13 to 17:38:24 UTC on 2026-10-22. It used the same pinned DeepSeek
V4.1 Flash 1017 weights, immutable engine image and TP4 x DP2 profile as the
[previous failing parity run](p2-t3-replacement-parity-evidence.md), with the
explicit [tokenizer request materialization correction](p2-t3-tokenizer-materialization.md).

## Measured result

All four unchanged cases passed. Independent local token IDs match the server
token IDs, and the generation path reports the same input-token count.

| Fixed case | Input tokens | Token IDs | Generated input usage |
| --- | ---: | --- | --- |
| Text | 12 | Match | Match |
| System message | 16 | Match | Match |
| Tools | 275 | Match | Match |
| Tool continuation | 332 | Match | Match |

The engine became ready after 1,201,371 ms. The eight-request comparison took
12,386 ms, with one output token per generation. These timings are startup and
test-phase durations, **not TTFT, throughput or a quality benchmark**. The
container exited 0 without OOM; the engine stopped and owned-container cleanup
was confirmed. The separately approved VM, cache and access key remain retained.

The original prepared bundle was rehashed locally, and its own committed
`bundle_inputs` and `validate_result` functions accepted the original receipt.
No status or measurement in either evidence file was rewritten. Regression tests
bind both files by SHA-256, preserve the earlier failed result, and check that
the probe binding and all four independent token measurements are unchanged.

```sh
python3 -B -m unittest discover -s scripts -p test_fixed_parity_evidence.py
```

## Remaining boundaries

This establishes the fixed four-case runtime tokenizer prerequisite, not
equivalence for every prompt or framing. `functional_pass=true` is deliberately
separate from the original outer `result=fail`, `status=BLOCKED`,
`deployable=false` and `confidential=false`. Do not relabel the overall
qualification as passed.

There is no live-model tau score, GSM8K/MMLU result, complete provider-conformance
run, native/JIT provenance, boot validation or end-to-end attested service in
this evidence. The host uses AMD SEV-SNP, not the plan's Intel TDX platform.
The hardware observations in this run are not fresh cryptographic GPU appraisal.
The adapter field binds configuration, not an independent runtime activation
proof; its separate CPU control and this successful HTTP parity test retain
their distinct scopes. The original failing GPU run did not retain an exception
body, so its exact historical exception is not retrospectively established.
