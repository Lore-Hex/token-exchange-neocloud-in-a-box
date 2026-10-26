# Owned long-context and cache qualification

Software-only implementation. No long-context, cache-hit, long-output, GPU-rank,
quality, confidential-service or pricing result is claimed here. Independent
final-head review, full gates and a separately approved finite run are required.
The existing [C8 measurements](../../evidence/p2/2026-10-24-selected-c8-stream/README.md)
remain separate evidence; this C1 harness does not repeat that experiment.

## Runnable integration

The explicit `--long-qualification` option on
[record-selected-service.py](../../host/record-selected-service.py) reuses the
existing [finite owner](p2-t4-selected-finite-owner.md), private inputs,
network/watchdog controls, preparation/image/source joins and cleanup. It only
accepts `--lifetime-profile ordinary-24h --execution-profile long128k-eager`.
It is mutually exclusive with all four smoke modes. Omitting it preserves
ordinary serving and network-none smoke commands and receipts.

After the engine, guard and TLS identities are ready, the existing supervisor
calls [the client](../../scripts/selected_long_qualification.py) once. The client
only connects inside the container to the owned TLS listener at
`127.0.0.1:8443`. It reuses the peer/inode/PID/start/argv proof, certificate trust,
hostname and private API-key snapshot. No arbitrary URL, prompt, model, caller
rank header, redirect, public client or retry is accepted. The owner retires
after the qualification stage, including on failure; it does not idle until the
ordinary-day cutoff. The same reviewed image may later be started as ordinary
service by omitting the option, under a separate operational approval.

Prepare a **new** non-owner B300 DP2/TP4 `long128k-eager` lab bundle with tokenizer
parity, tokenizer materialization and runtime observation enabled. Build a fresh
selected-service image from this exact source and the reviewed TLS-grace pin,
following the [image preparation](p2-t4-selected-service-image.md) and
[long-profile](p2-t3-long-context-cache.md) prerequisites. Historical payloads,
source receipts and images must not be overwritten. The complete prepared
Python source inventory binds both new helpers; admission also compares their
current bytes and the shared streaming/identity helpers before starting owners.

Use the existing finite-owner command and **all** its exact preparation/build,
daemon, public weight-view, private input, host topology and state arguments.
The only qualification-mode additions are:

```sh
--lifetime-profile ordinary-24h \
--execution-profile long128k-eager \
--long-qualification
```

First use `--inspect` against an explicitly prepared empty root-owned `0700`
state directory. This is read-only admission, not a runtime qualification.
Only after independent review and explicit run approval invoke the same exact
inputs once with `--run`, preserving stdout as the original terminal receipt
and stderr as the existing closed progress stream. Do not substitute an
external observer or issue additional requests while this exclusive C1 stage
runs. This document does not authorize network publication or paid execution.

The original absolute cutoff is never renewed. Host preflight requires **more
than 7,540 seconds remaining**: 1,800 startup + 3,600 qualification + 1,960
in-container cleanup + 180 host cleanup. The ordinary profile still caps the
cutoff at 24 hours. Tokenizer preparation (at most 180 seconds) is inside the
3,600-second stage, not additional time. Each request is clipped to the
contract's 900 seconds and the original work deadline; cleanup uses the
contract's 930-second frontend drains and four 25-second reap allocations.
There is no fallback to the old global 120/375-second constants.

## Fixed synthetic workload

The [bounded CPU child](../../scripts/selected_long_tokenizer.py) uses the exact
prepared runtime interpreter (CPython 3.12.3), sealed lab loader and locally
verified model tokenizer. It loads no model weights and downloads nothing.
The inherited application environment and source/model hashes are rechecked.
Library stdout/stderr are discarded; the private pipe carries only bounded
token counts and hashes. The same pinned `parity.TEMPLATE` reasoning-none
settings are used for offline counting and the selected native request path.
See the pinned [DeepSeek renderer](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/renderers/deepseek_v4.py)
and [shared parity contract](../../scripts/engine_tokenizer_parity.py).

Ten sequential requests, no retry:

| Stage | Exact prompt tokens | Output ceiling | Requests |
| --- | ---: | ---: | ---: |
| 32K | 32,512 | 256 | cold, warm, different namespace |
| 64K | 65,280 | 256 | cold, warm, different namespace |
| Near 128K | 130,816 | 256 | cold, warm, different namespace |
| Paired maximum context/output | 114,688 | 16,384 | one fresh namespace |

Totals reserved: 800,512 prompt tokens and 18,688 output tokens. Prompt padding
is estimated only to locate candidates; exact tokenization must confirm every
selected count. The server's actual prompt usage must match that count. Each
context probe transiently checks a fixed synthetic retrieval answer. Raw prompts,
token IDs, responses, reasoning, errors, credentials and salts are never logged.

Cold/warm pairs use one fresh namespace; the negative control uses another.
The guard's reviewed namespace affinity selects a DP2 replica. The report still
records observed rank as **unknown**: configured routing is not worker-rank
observation. Cold/negative cached-token counts must be present and zero; all
three warm requests must report positive actual cached tokens for `checks_pass`.
No cache flush, retry-until-hit or fabricated zero is used. A warm miss is
retained as a failed check, not attributed to capacity or the model. Different
namespaces can map to different replicas, so these controls do not establish
cross-tenant isolation or replay correctness on both ranks.

The final probe requires 114,688 actual prompt tokens plus 16,384 actual
completion tokens and `length` finish, testing the paired 131,072-token budget
in one request rather than measuring the two ceilings separately. A pass proves
one C1 request on this configuration, not both replicas or a latency SLA. Early
`stop`, timeout, missing usage or incomplete framing cannot pass. Extra prefill
does not extend any deadline. No 1M-context or sustained-capacity claim follows
from these probes.

## Receipts and failure

The bounded report is embedded in lifecycle v8, inside the existing execution
v7 envelope, then joined by the existing host owner. Progress plus terminal
output retains the existing 16 KiB attachment limit. `result: completed` in the
inner report means the ten-response schedule completed, not that all checks
passed. `checks_pass` is recomputed by the validator; a miss, short output,
incomplete schedule or identity/cleanup failure makes the lifecycle fail.

Known integer usage survives later framing, deadline, identity and finalization
failures. A send attempt with no valid usage remains `usage_unknown_requests`.
The report retains incomplete attempts and never treats lost responses as free.
Cleanup certainty and workload success remain separate. Unknown response-reader
closure cannot become confirmed cleanup. Keep failed original receipts; do not
rewrite them after a later run. Broad quality, capacity, cache-isolation,
confidential and pricing flags remain false even when the narrow checks pass.

TTFT, first visible content, actual cached prompt tokens and finish reason are
reported. Decode tokens/second is explicitly an estimate derived from actual
completion count and output-event arrival span, not per-token timestamps.
Completed-output throughput uses the entire stage wall time, including tokenizer
preparation and connection checks. Neither metric is fleet throughput.

## Cost inputs and remaining gates

For an honest cost estimate collect total hourly deployment cost, sustained
aggregate completed output tokens/second across the whole node at the agreed
latency/error SLO, prompt/output mix, and the billable busy fraction. Include
storage, CPU, networking and idle/startup costs. With busy-window throughput
`T`, total hourly cost `H`, and utilization `u`, infrastructure dollars per
million output tokens is `H * 1,000,000 / (3,600 * T * u)`. If throughput already
includes idle wall time, set `u = 1` instead of charging idle twice. Input-token
revenue/cost allocation and utilization are separate measured inputs, not
inferred from C1 decode speed. The arithmetic helper makes no pricing claim.

[Client tests](../../scripts/test_selected_long_qualification.py) cover exact
count preparation, usage retention, no-retry scheduling, cache controls, transport
framing/finalization and content-free reports. [Hook tests](../../scripts/test_selected_long_hook.py)
cover CLI dispatch, source joins, incompatible modes, budgets, original cutoff,
cleanup, strict terminal replay and bounded attachment size. Real pinned-runtime
tokenizer preparation, GPU memory fit, cache behavior, long generation and
end-to-end cleanup still require the one reviewed hardware run. No full-suite
or hardware success is implied by the focused synthetic tests.
