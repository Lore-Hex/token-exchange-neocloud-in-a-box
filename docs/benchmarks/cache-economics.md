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

## Bounded Cache Sequence

`scripts/cache_probe.py` drives that transport for one prepared synthetic
workload: 2,048 input / 512 maximum output tokens, or 32,768 or 131,072 input /
1,024 maximum output tokens. It reuses the existing synthetic long-output
fixture. The caller must first measure its exact padding with the pinned
tokenizer; counts are not estimated by this sequence.

Each sequence has three cold/warm/different-namespace triplets, nine requests
maximum, concurrency one, no retries and a 1,200-second overall cap. Each pair
of namespaces is fresh; only the cold/warm pair shares one. It checks the
caller's runtime predicate before every request and emits a manifest, pre-send
intents, numerical results and a summary through a required durable writer.
Receipt failures propagate and prohibit further requests. Unmatched intents
remain possibly transmitted with unknown usage; never resume that run. The
transport is consumed even if a sequence fails before sending.

Transport failure, missing cache counts or unexpected cold/negative cache reuse
stops the sequence. A warm miss is retained rather than excluded or retried.
Known usage survives a late failure; missing usage remains explicitly counted.
Reported totals cover attempted failures too, and time includes preparation,
preflight and receipt work before the terminal write. A five-second bounded
receipt-only allowance does not admit inference after the run deadline.

This is not a standalone admission controller: the operator still needs a
reviewed source/endpoint/tokenizer-bound caller, an exclusive fsynced journal,
fresh runtime/quiescence checks, aggregate cost authorization and exact billing
reconciliation. The two namespaces may map to different replicas, so a zero-hit
negative sample does not prove same-replica isolation. No cache-isolation,
quality, sustained-load, privacy, invoice or tariff qualification is claimed.

`scripts/test_cache_stream_integration.py` exercises the real sequence, HTTP/SSE
collector, measurements and interrupted-prefix auditor together over synthetic
in-memory connections. It covers namespace ordering, late protocol failures,
missing cache counts, partial sends, overload, failed receipt callbacks and
nested deadline cleanup. The socket/TLS connection and receipt storage are
test doubles; these checks do not validate live TLS, fsync durability, tokenizer
parity, runtime identity, network performance or hardware.

### Offline Input Preparation

Prepare all three fixed synthetic input sizes using already downloaded, pinned
tokenizer sources, model tokenizer files and the approved native wheel:

```sh
python3 -I -S -B scripts/cache_workload.py \
  --vllm-source "$VLLM_SOURCE" --tokenizer "$TOKENIZER" \
  --tokenizer-wheel "$TOKENIZER_WHEEL"
```

This command makes no network calls, loads no model weights and reads no API
credentials. It reuses the hash-verified V4.1 renderer/native BPE and its four
historical parity fixtures. A separate, explicit 262,144-token offline search
bound permits candidates around 131,072 input tokens; existing 8K/32K callers
keep their limits. Each shape has at most 67 candidate measurements and the
whole preparation has a 180-second deadline. Padding estimates only choose
candidates: an exact measured count is required, with token/message hashes
recorded. It never guesses, truncates or rounds a prompt to fit.

The numeric JSON contains no prompt text, credential or input path. Save it
operator-privately with the reviewed source revision and original files; it
does not authenticate its own provenance. This is preparation, not execution
approval or current runtime parity, cache savings, model quality, billing or
price qualification. The live caller must still verify this exact source and
workload, the current owner, endpoint, tokenizer and aggregate budget. It must
never start inference merely because offline preparation succeeded.

### Explicit Official Candidate

The cache preparation CLI accepts `--candidate original`; omission retains the
historical selected-model v1 format and wire model. This opt-in uses only
`quality_candidate.original_fields()`: the official model ID, pinned original
checkpoint/manifest and `original-long1m-graphs` profile. No arbitrary model,
revision, serving profile or tokenizer-source override is accepted.

For an already admitted caller, construct `stream_https.SelectedHTTPS` with
`candidate="original"` and call `cache_probe.run` with the same candidate and
`preparation=` set to the unmodified v2 preparation object. The selected shape's
padding must match. Request validation and every streamed response event enforce
that client's model identity. The transport's TLS, timeout, request/token budgets
and no-retry rules are unchanged. This is software support, not a live caller or
permission to launch inference.

V2 preparation binds the existing hash-verified tokenizer sources, model files
and native wheel to the official identity. Both checkpoint manifests pin the
same tokenizer bytes; that does not make their model identities interchangeable.
The v2 manifest embeds the complete preparation. Every intent, result and summary
binds its canonical workload hash, tokenizer source/identity hashes, selected
token-ID hash and full official identity. The interrupted and completed auditors
emit v2 reports with those bindings plus the exact input journal's hash and size.
Mixed v1/v2 records, profiles, models or preparation bindings are rejected, not
upgraded. Legacy v1 records remain unchanged and cannot be relabelled official.

Audits establish internal consistency, not source authenticity, current tokenizer
parity, endpoint ownership or billing reconciliation. Preserve the original
preparation and source evidence; independently verify them before any separately
approved request. Tests use synthetic tokenizer counts and in-memory HTTP/SSE;
no real tokenizer execution, external inference or GPU validation is claimed.

### Completed Journals

Reconcile a single recorded successful nine-request sequence offline:

```sh
python3 -I -S -B scripts/cache_complete.py < private-cache-journal.jsonl
```

This reuses the interrupted-prefix validator, then requires one final successful
summary whose request, token and warm-hit counts exactly match every receipt.
All nine ordered attempts must have known usage; cold and different-namespace
results must report zero cached tokens, including the last request. Warm misses
are valid results and are not dropped. Every successful request must finish
before the collector's 119-second deadline. Serial request durations must fit
the reported total window, which must be below the sequence's 1,200-second cap.
Duplicate keys, extra fields, mixed runs, missing receipts, malformed tails,
overstated qualification flags and failed terminal summaries are rejected.

`recorded_sequence_completed` means only that these supplied records are
consistent. It does not authenticate their source, prove current runtime or
tokenizer identity, attest storage durability, release accounting holds, verify
customer billing, or qualify cache isolation, quality, throughput or a price.
No input is changed and no inference is sent. Keep original failed/interrupted
files unchanged; this command does not repair them or strip terminal records.

### Interrupted Journals

Audit an intact, newline-terminated prefix of a single interrupted sequence:

```sh
python3 -I -S -B scripts/cache_journal.py < private-cache-journal.jsonl
```

The offline auditor requires the manifest and ordered intent/result records,
rejects duplicate keys, mixed runs, malformed/truncated tails and terminal
summaries, and makes no network calls or changes to the input. An orphaned
intent remains one possibly transmitted request with unknown usage. A saved
intent followed by a pre-send failure also remains conservatively uncertain.
Known usage after a late failure is retained, even when the upstream prompt count
disagrees with the prepared tokenizer count. Missing cache counts stay unknown.
Requests without send evidence are not a statement that nothing was billed.

This validates only the supplied prefix, not its provenance or completeness.
Preserve the original file and all unresolved holds; never reconstruct missing
receipts from transcript totals, concatenate runs, resume or manufacture a
terminal summary. A rejected audit cannot release accounting holds. Even nine
completed result records without a terminal record do not qualify the run,
cache isolation, model quality, billing, confidential computing or a tariff.

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
