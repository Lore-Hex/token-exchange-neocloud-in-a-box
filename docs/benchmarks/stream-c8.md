# Explicit C8 Chat Burst

Implementation: [stream_c8.py](../../scripts/stream_c8.py).
Tests: [test_stream_c8.py](../../scripts/test_stream_c8.py).
Status: implemented and tested with synthetic child processes and loopback HTTP.
No c8 hardware throughput or P2.T4 acceptance is claimed.

## Scope And Engine Constraints

Preparation must explicitly select `--stream-cohort` and
`--stream-profile chat-2k-512-c8-600s`, with the existing tokenizer parity,
tokenizer materialization and runtime observation prerequisites. This profile
accepts only the selected uncensored checkpoint, ordinary B300 TP4 x DP2 and
`--execution-profile batch8-graphs`. It excludes tau owners, the separate
provider checker, CC, eager, and DSpark. There is no runtime profile override.
The default c1 collector, its extended window, transport and measurement bytes
remain unchanged; historical c1 receipts retain their original validator.

No engine flags change. The existing [execution profile](../../scripts/deepseek_tuning.py)
retains `--max-num-seqs 8`, 4,096 context, 4,096 batched tokens and full-decode
graphs with capture sizes 1/2/4/8. In pinned vLLM
`5df63a7203cdb9328d824a27b682e77c7b9928be`,
[SchedulerConfig](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/config/scheduler.py#L62-L102)
defines the sequence limit per DP rank, not across both ranks. Eight per rank
does not mean this client dispatches sixteen requests. The
[batch/context checks](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/config/scheduler.py#L314-L342)
remain satisfied without raising the batch token budget to the sum of eight
prompts. Internal
[DP routing](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/v1/engine/core_client.py#L1642-L1701)
uses load and local in-flight counts; this receipt neither observes nor claims
an exact 4/4 rank split. The existing pinned-source checks remain in force.

## Bounded Processes And Accounting

The owner prepares the same exact-tokenizer [nine requests](stream-cohort.md):
one serial warmup, then a single burst of eight measured requests, each exactly
2,048 prompt tokens and at most 512 completion tokens. Reservation remains
23,040 tokens, with no retry or replacement request. Early EOS uses actual
reported usage, not the output cap. This is a finite burst, not sustained c8
load, the complete concurrency sweep, or a quality result.

Each measured collector is a fresh exec through the fixed authenticated lab
wrapper. The source-bound closure adds only the c8 implementation and the
same stream binding validator; it imports no tokenizer or model per request.
The parent bounds startup and imports to ten seconds for the entire cohort.
Private stdin/stdout, null stderr, `close_fds=True` and new process groups prevent
retention of unrelated owner, engine or control descriptors. Input and output
are bounded JSON, never pickle. All children must signal ready before release.

The parent passes its trusted engine PID/start/namespace/executable/listener
identity unchanged. Each child reconstructs the same concrete endpoint and runs
the existing pre-connect, connected-peer-before-HTTP, and EOF identity checks
on its main thread. No alternate endpoint or guard callback is introduced.
The owner still checks terminal identity and performs engine/container cleanup.

The earlier owner deadline wins over the 600-second overall limit. Each HTTP
request retains its 60-second limit. Independently of child request alarms, the
parent stops waiting 60 seconds after the first burst release. Three seconds
inside the overall deadline are reserved for draining, interrupting and
killing/reaping collector groups;
this does not consume the owner's separate engine teardown reserve. A bounded
100 ms flush lets request alarms return known-usage failure packets before
SIGINT, followed by at most one second of interrupt grace and SIGKILL. Cleanup
attempts every child, including partial startup, signal denial and bad output.
Lost reports have unknown usage. Received failed usage remains counted but
does not contribute successful throughput. Unconfirmed cleanup cannot pass.

The v2 cohort receipt adds per-request intervals, reported peak overlap and
collector reap confirmation. Valid child intervals alone contribute to peak
overlap. A lost report has a conservative dispatch-to-cleanup interval marked
`reported: false`, not an invented concurrency observation. Success requires
eight successful overlapping collectors, zero child exit codes, confirmed reap,
the existing terminal identity check and normal owner cleanup. Fewer than eight
overlapping collectors is explicitly unsuccessful even if all requests finish.
Measured wall time is dispatch-through-collector-cleanup: it spans the first
burst release through final collection and reap, including shutdown, polling
and draining. Warmup and collector startup are excluded from throughput but
included in the overall deadline. This is not pure server decode time, and it
must not be compared with c1 as a pure server decode metric. The validator
recomputes budgets, overlap and measurement summaries.
All quality, pricing, confidentiality and full-sweep acceptance flags stay false.
