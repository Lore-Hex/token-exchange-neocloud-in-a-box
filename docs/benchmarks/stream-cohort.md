# Bounded Chat Cohort

Implementation: [stream_cohort.py](../../scripts/stream_cohort.py).
Tests: [test_stream_cohort.py](../../scripts/test_stream_cohort.py).
This is a controller component tested with synthetic tokenizers and local HTTP,
not a live-model result or the complete P2 benchmark sweep.
The separate, explicit [c8 burst profile](stream-c8.md) reuses this workload and
transport without changing the default or extended c1 collector.

## Workload And Limits

The initial workload uses the P2.T4 chat dimensions: exactly 2,048 rendered input
tokens and at most 512 generated tokens. There is one separately accounted
warmup and eight measured requests at concurrency one. Every request has a
different leading synthetic request ID. Repetition is not a cache-hit claim,
nor does a different ID prove a cold cache. Reported cached tokens remain the
only cache observation; missing usage remains unknown.

The owner prepares all nine requests with the exact, source-verified
`DeepseekV41Tokenizer` in its bounded local tokenizer child. Rendering matches
the existing [tokenizer parity](../../scripts/engine_tokenizer_parity.py) options:
no tools, non-thinking, generation prompt enabled, no continuation. A bounded
search measures the entire rendered request on each attempt. It requires exactly
2,048 tokens; it never truncates token IDs, assumes monotonic BPE behavior or
accepts an approximate length. The search has at most 35 tokenizer calls per
request and a 60-second overall deadline. Failure to find an exact fit blocks
inference. Only padding counts and token hashes are retained.

The fixed reservation is nine requests times (2,048 + 512), or **23,040 tokens**.
There is no retry, reconnection, dynamic concurrency or automatic restart.
The whole cohort, including warmup, is bounded to 180 seconds or the caller's
earlier deadline; each transport request remains bounded to 60 seconds. The
first failed request stops dispatch. These are diagnostic bounds, not a claim
that a full 512-token response will finish within the limit. Early EOS is valid;
reports use actual output usage and do not label it a 512-token completion.

An explicit `--stream-profile chat-2k-512-c1-600s` during preparation selects a
600-second window instead. The contract binds that named option; it cannot be
changed when running a prepared bundle. The default contract and 180-second
behavior remain unchanged. The same nine 60-second request limits need at most
540 seconds of transport time, leaving 60 seconds for control overhead within
the longer window. Earlier owner deadlines always win. There is still no retry
or change to request count, token reservation, context, concurrency or cleanup.

## Measurement And Ownership

The concrete [owned HTTP collector](stream-transport.md) retains the same-owner
UID/PID/network namespace and connected-peer checks before sending request
content, and checks identity again at EOF. It closes each connection before
returning. Only the fixed local endpoint and model are accepted. The owner must
still authenticate source, weights, runtime and fresh tokenizer parity, supply
the actual allocated GPU count (four or eight), and perform verified engine
cleanup. `Workload` is an in-process value, not an untrusted receipt format or
an admission credential. The Python tokenizer object's name does not prove its
identity; the owner must verify the pinned source and model files.

Warmup traffic is included in total request/token accounting but excluded from
the measured cohort's time and throughput. Measured wall time spans first
dispatch through final socket drain, including failed attempts. Requests not
started remain in the scheduled denominator. Failed requests with reported
usage retain that usage but contribute no successful throughput or latency
percentiles. Unexpected failures produce fixed error categories, never exception
text; unreported usage remains unknown. No measured request means a null measured
summary, not an invented zero-latency sample.

The output contains workload hashes, usage, timing and fixed dispositions only.
It does not contain prompt text, completion text or token IDs. It explicitly
leaves quality, pricing, confidential service and sweep acceptance false.

## Owned Lab Integration

The existing [lab recorder](../../host/record-deepseek-lab.py) accepts an explicit
`--stream-cohort` preparation option, together with `--tokenizer-parity`,
`--tokenizer-materialization` and `--runtime-observation`, for its existing B300
CC local-correctness profile. It cannot be combined with the separate provider
checker or passed as an override when running an already prepared bundle.
Defaults, engine arguments, container privileges, namespaces and cleanup limits
are unchanged. GPU CC mode alone is not confidential-service qualification.

The [integration](../../scripts/stream_lab.py) binds the complete fixed collector
source closure into the existing authenticated payload. It compiles those exact
bytes, not ambient same-name modules. The existing private tokenizer child
prepares the workload alongside its four independent parity controls and binds
the metadata to the current lab contract. Sources and model files are checked
again before the child returns; prompts, token IDs and library output are never
returned. The existing 60-second child deadline remains in force, including
bootstrap, counting and final checks.

For this opt-in benchmark, local workload preparation runs before engine
startup. Two nonempty padding lengths estimate the search center; every final
count is still measured exactly. The owning controller rechecks model and
tokenizer source files after startup and requires all four endpoint parity
probes to pass before dispatch. It then binds the collector to its current child process, namespace,
executable and listener, preserving per-connection accepted-peer checks. The
before/after runtime observations and normal engine teardown still run. A late
identity or cleanup failure prevents success without discarding already reported
usage. The host independently validates workload hashes, fixed budgets, timings,
arithmetic, stop dispositions and summary recomputation from the bounded receipt.

The [first actual attempt](../../evidence/p2/2026-10-23-stream-preparation-failure/README.md)
reached engine readiness but failed before parity/cohort completion, with
confirmed cleanup. It is not a throughput result. The padding regression and
early-preparation ordering are tested; a successful real rerun remains required.
The [second actual attempt](../../evidence/p2/2026-10-23-stream-window-exhaustion/README.md)
passed all four parity cases and completed a warmup plus three measured
responses, but exhausted its 180-second window during the fourth measured
request. Four requests never started and one has unknown usage. It remains a
failed, partial benchmark with confirmed cleanup. The named longer window is
tested, not yet hardware-validated.
The budget is the
existing eight parity requests (four tokenize, four one-token completions), plus
one warmup and eight measured stream requests. Stream reservation remains 23,040
tokens; the four parity completions have their separate existing accounting.
No credentials, public endpoint, cloud resources or new launch profile are added.

## Remaining Work

Partial live observations and cleanup are preserved above. A complete live
cohort and the full benchmark sweep remain pending; code tests do not prove them.
The current 4,096-context profile does not
support the planned 32K/128K workloads or concurrency-256 sweep; those require
separately reviewed profiles and live tests.
