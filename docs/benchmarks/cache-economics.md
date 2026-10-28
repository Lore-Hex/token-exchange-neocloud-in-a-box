# Cache Price Sensitivity

`scripts/cache_economics.py` compares hypothetical cache-read tariffs using
numeric request receipts. It makes no network calls and changes no prices.
It is an offline calculation, not a benchmark, invoice, settlement ledger,
budget authorization or automatic pricing recommendation.

## Inputs

Extract counts from a separately validated complete receipt chain. Include
failed requests and unmatched pre-send intents as `failed` or `unresolved`;
only requests proven not sent belong in `not_started_requests`. Never fill
missing usage or cache counts with zero. Keep interrupted runs separate.
The source SHA identifies the original artifact, but this calculator does not
open it, validate its hash or establish that its measurements are authentic.

All prices are exact decimal strings in USD per million tokens. Fleet cost is
the **whole fleet's** declared hourly allocation, including required supporting
resources. The window includes warmup, failures and idle time, not the sum of
successful request times. Do not divide it by GPU count, replicas or speculative
concurrency. Use observed wall time and declare whether the hourly input is an
estimate or reconciled cost alongside the report.

For example, the following is a **synthetic arithmetic example**, not hardware
evidence. The source hash is a placeholder and must be replaced for a real run:

```json
{
  "schema": "nib.cache-economics-input/v1",
  "measurement_source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "window_seconds": "60",
  "fleet_usd_per_hour": "60",
  "input_usd_per_million": "2",
  "output_usd_per_million": "10",
  "cache_rates_usd_per_million": ["2", "0.5"],
  "max_prompt_tokens": 1000,
  "max_output_tokens": 100,
  "planned_requests": 1,
  "not_started_requests": 0,
  "requests": [
    {"status": "completed", "prompt_tokens": 1000,
     "completion_tokens": 100, "cached_prompt_tokens": 800}
  ]
}
```

```sh
python3 scripts/cache_economics.py < private-numeric-input.json
```

The calculator rejects duplicate JSON keys, floats for money, negative or
over-cap usage, more cached tokens than input, duplicate/elevated cache rates,
unknown fields and inconsistent request counts. Maximum input size is 2 MiB;
at most 10,000 requests, eight candidate rates and a seven-day window are
accepted. No prompt, completion, credential, address or customer identifier is
needed. Keep operator inputs and reports outside this public repository.

## Interpretation

Cache tokens are a **subset** of input:

```text
projected charge = ((input - cached) * input_rate
                    + cached * cache_rate + output * output_rate) / 1,000,000
```

The example projects $0.003 at the unchanged cache rate and $0.0018 at the
discounted rate. It does not charge full input and then add cache on top.
The full minute costs $1 of fleet-time allocation in both scenarios.

- Missing cache counts produce a range from all-cached to all-uncached input.
  Missing total usage produces a zero-to-request-cap range, **not free work**.
- Failed and unresolved work has its own tariff-equivalent range; it is never
  added to successful-request gross revenue. It may still consume capacity.
- Full-window allocation is kept even when no request completes. The difference
  from completed gross is an allocation sensitivity, **not profit or marginal
  inference cost**. Unknown usage remains unknown even if both tariff bounds
  coincide. Report bounds are rounded outward to 12 decimal places.
- Exact settlement rounding, refunds, minimum charges, router markup, taxes,
  reserved capacity, payment fees and idle time outside the declared window are
  not modeled. A successful response does not prove it was billed or collected.

Before lowering a live tariff, collect repeated cold/warm/negative-namespace
measurements for representative chat, agent and long-context workloads, including
actual cache counts and decode lengths. Verify account-level cache isolation,
real billing and the full workload mix. A four-token warm retrieval or a faster
first token alone does not establish a sustainable discount. Keep existing
prices until the operator-specific evidence and normal rollout gates pass.

Related: [long-context cache tests](../tasks/p2-t3-long-context-cache.md),
[quality calibration](../tasks/p2-t3-retained-quality.md), and
[published cache-read prices](../operators/trustedrouter.md#cache-read-pricing).

## External Streaming Collector

`scripts/stream_https.py` provides the transport component for a future bounded
public-HTTPS cache experiment. It reuses `bounded_https.HTTPS` for verified TLS
to one explicitly supplied address and `stream_transport.StreamResponse` plus
`stream_measurement` for strict HTTP/SSE termination and numeric measurements.
The existing JSON-only collector and the running service are unchanged.

The closed selected-model text request requires a 64-hex namespace, final usage,
greedy non-reasoning decoding, no tools, at most 131,072 input and 8,192 output
tokens, a 2 MiB request, and a 119-second absolute deadline. There is one POST,
no redirect or retry, and no proxy-environment handling. The caller's mandatory
`before_send` callback runs after TLS verification but before authenticated
bytes; a failed durable intent propagates and prevents sending. A possibly
partial send counts as an attempt even when usage is unknown. Already measured
usage survives late framing/count/cache errors. Missing cache usage stays
unknown. No prompt, completion or credential is returned or logged.

This library is **not an executable experiment or execution approval**. A
separately reviewed caller must verify the source, live owner, endpoint contract
and tokenizer, create the cold/warm/negative namespaces, enforce aggregate
request/token/time/budget caps, and persist/reconcile intents and numerical
receipts. Report unknown work, errors, actual decode length and the entire
observation window. The input count is supplied by that caller, not independently
tokenized by the transport. No cache-isolation, billing, quality, hardware,
confidentiality or sustainable-price qualification follows from a successful
stream. Synthetic in-memory tests are not a live TLS or GPU benchmark.

## Read-Only Cache Counters

`scripts/cache_counters.py` summarizes an existing private DP2 `/metrics`
snapshot without sending inference, reading credentials or contacting a server:

```sh
python3 -I -S -B scripts/cache_counters.py --model "$MODEL" < private-metrics.txt
```

The pinned vLLM revision defines prefix-cache queries and hits as **tokens**,
not requests ([definitions](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/v1/metrics/loggers.py#L661-L678),
[increments](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/v1/metrics/loggers.py#L1167-L1177)).
This parser requires both counters for exactly ranks 0 and 1 of the expected
model, rejects duplicate or malformed series and noninteger/unsafe counts,
and weights the aggregate fraction by queried tokens, not by rank. Zero
queries produce an unknown fraction, not a measured zero hit rate.

Collect only from the separately authenticated, owned engine and verify its
source, process and container identity. This offline parser cannot establish
those facts; a supplied source hash is not authentication. Keep raw metrics
and reports operator-private. Output contains numeric counters and an input
hash, not model names, unrelated labels, prompts or completions. Input is
bounded to 2 MiB. The existing engine metrics transport is not changed.

These are **engine-lifetime cumulative**, non-atomic counters covering all
engine work, not a particular customer's cache-hit rate or invoice. They reset
on restart. Do not compare snapshots across processes or infer a measurement
window from one snapshot. They do not establish KV residency, a causal speedup,
cached-input settlement or a sustainable cache discount. No delta is computed,
no missing value is filled in and no tariff or serving profile is changed.
