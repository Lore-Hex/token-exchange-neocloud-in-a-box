# Selected Long-Context Qualification

Candidate source `e0be185f06c6ebd484cb8906ef6219b44d53f4cc`, using the selected
`dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8` checkpoint at
`c19ca5ca4eaeb79d29fdf08aea8327da95b1116a`. The operator used the retained
eight-B300 ordinary node; it is not confidential hardware.

The original [preparation receipt](preparation.json) embeds the source revision
and hashes the payload. The [input contract](input-contract.json) is the
unchanged prepared `context/payload/lab.json`, renamed only for publication.
The [build receipt](build.json) binds the image to that preparation. These
records prove configuration/build observations, not a running model or a
successful long-context request. The builder trusts the host's Docker, OS and
interpreter and explicitly does not rehash every local image layer.

## Test Scope

The closed profile is TP4 x DP2, one admitted request, eager execution,
131,072 total context, up to 16,384 output tokens, 4,096-token chunked prefill,
FP8 KV and explicit prefix caching with actual cached-token usage enabled.
Engram stays on the GPUs. The environment's compatibility flags do not turn
ordinary hardware into confidential hardware.

The ten-request qualification reserves 800,512 prompt and 18,688 output tokens.
It checks cold, same-namespace warm and different-namespace requests at 32K,
64K and near 128K total context. The final request pairs 114,688 prompt tokens
with a 16,384-token output allowance. A configured output allowance is not
proof that the model actually generated that many tokens. Missing usage,
unknown cache counts, failed retrieval and failed cleanup cannot become passes.

## Measured Result: Partial, Not Qualified

The original [owner receipt](owner.json) is **FAIL/BLOCKED**, not a successful
qualification. [Progress](progress.jsonl) records ready at 01:34:58 UTC and
draining at 01:50:48 UTC on October 26. These are host observations, not
attested timestamps. The qualification itself elapsed 950.226408461 seconds.

All nine retrieval/cache requests completed with the correct synthetic answer,
exact prompt counts, four actual output tokens each and known cache counts:

| Total context target | Input tokens | Warm cached tokens | Cold TTFT | Warm TTFT | Different namespace cached tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| 32K | 32,512 | 32,384 | 7.031 s | 3.396 s | 0 |
| 64K | 65,280 | 65,152 | 1.633 s | 0.304 s | 0 |
| 128K | 130,816 | 130,688 | 3.014 s | 0.365 s | 0 |

All three cold controls also reported zero cached tokens. These observations
demonstrate real reuse within the tested namespace and zero reported reuse for
the tested different namespaces. They do not establish comprehensive cache
isolation, rank identity, concurrency capacity or production cache pricing.
There is one sample per cell, without a separate engine warmup; startup/JIT
effects and request order prevent a general latency claim.

The tenth request, 114,688 input tokens with a 16,384-output allowance, timed
out after 900.000278897 seconds. It observed 12,634 output events but no final
usage or finish reason. **Events are not tokens.** The generated-token count,
cache usage and retained first-content timing for that request are unknown;
16K actual output was not proven. No retry or deadline extension occurred.
The nine completed requests account for 685,824 input and 36 output tokens;
the tenth request remains explicitly unknown, not free or zero usage. The
report's 36/950.226 aggregate is not an output-capacity benchmark.

The inner lifecycle preserves `cleanup_confirmed=false` because the timed-out
response reader did not confirm closure. It reports all three child processes
drained and reaped. The outer owner separately confirms the container and
network absent, creations resolved, policy removed, quarantine confirmed,
watchdog confirmation and removal of private inputs. Later outer cleanup does
not rewrite the inner failure into a pass. The terminal engine identity is
unknown; the pre-request identity alone does not establish end-to-end stability.

Next experiment: separately reviewed graph-enabled execution with the same
weights, context, output, cache, admission and timeout limits. Do not advertise
16K output, a throughput improvement, a cache discount or confidential status
from this failed eager trial.

## Previous Service

[previous-owner.json](previous-owner.json) preserves the previous owner's
original FAIL/BLOCKED, `reason=lifecycle`, and `runtime_started=null` after
the operator requested cancellation before the trial. It reports confirmed
cleanup, all six owner-cleanup fields true, watchdog confirmation and removed
private inputs. Its [build receipt](previous-build.json) joins the original
image/preparation identities. Cleanup does not establish a completed 24-hour
soak, every stream's graceful finish, or a successful original owner result.

The previous owner is attributed to `75b66500621793d668959a5e71baf3f26760eed1`.
The build and owner receipts do not embed source revisions or observed start/end
times. Replay tests pin the attributed producer's historical source bytes;
they do not fabricate embedded provenance or attestation. Private launch,
process, address, namespace, cancellation and credential records stay private.
No rewritten/redacted record is presented as a byte-original artifact.

[Replay tests](../../../scripts/test_selected_long_evidence.py) check original
hashes, source and preparation joins, selected weights, fixed execution limits,
actual usage, cache controls and the distinction between cleanup, configuration
and qualification. They replay the historical validator from the producer's
source in an isolated subprocess; they do not rerun inference.
