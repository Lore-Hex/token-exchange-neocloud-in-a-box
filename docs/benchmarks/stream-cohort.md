# Bounded Chat Cohort

Implementation: [stream_cohort.py](../../scripts/stream_cohort.py).
Tests: [test_stream_cohort.py](../../scripts/test_stream_cohort.py).
This is a controller component tested with synthetic tokenizers and local HTTP,
not a live-model result or the complete P2 benchmark sweep.

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

## Remaining Integration

The lab must still wire preparation into the sealed tokenizer child, bind the
workload to the current runtime, invoke the cohort from the owning controller,
and preserve the actual run and cleanup evidence. No host launch or guest
admission changes are made here. The current 4,096-context profile does not
support the planned 32K/128K workloads or concurrency-256 sweep; those require
separately reviewed profiles and live tests.
