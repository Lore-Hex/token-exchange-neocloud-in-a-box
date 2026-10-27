# 1M Context and 384K Output: Finite Trial Passed

The selected abliterated DeepSeek checkpoint completed the ten-request C1
qualification on ordinary eight-B300 hardware on October 26, 2026. This is
**non-confidential** hardware. The service retired and confirmed cleanup after
the test; this is not an endpoint still serving, a marketplace listing, a tau
quality result or sustained-capacity qualification.

## Measurements

| Input tokens | Cold first content | Warm first content | Warm cached tokens |
| --- | --- | --- | --- |
| 261,888 | 10.07 s | 3.53 s | 261,760 |
| 524,032 | 11.07 s | 0.87 s | 523,904 |
| 1,048,320 | 23.64 s | 1.66 s | 1,048,192 |

Each level had cold, warm and different-namespace requests. All nine retrieval
checks passed with four output tokens each. Every cold and different-namespace
request reported zero cached tokens. These are observations for these requests,
not a general isolation guarantee: cache state is replica-local and the exact
request-to-replica assignment was not observed. Four-token streams are not useful
decode-throughput benchmarks.

The final request used **655,360 input + 393,216 actual output tokens**, filling
the **1,048,576-token combined context**. It finished with `length` in
**2,741.33 seconds (45.69 minutes)**, with **14.07-second first content**, zero
cached tokens and 393,202 output events. Events are not tokens. Whole-request
output throughput was **143.44 tokens/second** (`393216 / 2741.332059995`);
the recorded decode-rate **estimate** was 144.18 tokens/second, derived from
the mean output-token interval. This one serial request is not node-wide
sustained throughput or a cost-competitiveness result.

The complete qualification took 2,857.32 seconds, including tokenizer preparation:
6,158,080 prompt tokens, 393,252 output tokens, **zero retries and zero unknown
usage**. Original engine identity hashes matched before and after. No automatic
context reduction, output-count substitution, `ignore_eos` or offload was used.

## Original Records and Reproduction

- [owner.json](owner.json): unchanged terminal owner/lifecycle/qualification
  record. `result=pass`, `checks_pass=true`; all TLS, guard and engine drains
  and reaps passed. Host network/container removal, quarantine, watchdog and
  private-input removal are confirmed.
- [owner-progress.jsonl](owner-progress.jsonl): unchanged phase records.
  Preparation began at 21:08:24 UTC, readiness at 21:24:09, draining at 22:11:47.
  Phase timestamps are not a separate final cleanup timestamp.
- [input-contract.json](input-contract.json): unchanged source-bound engine and
  model inputs, not operator addresses or credentials.
- [Preparation checkpoint](../2026-10-26-million-prerequisites/README.md): the
  existing verified staging, read-only view, isolation, build and preparation
  receipts are referenced, not duplicated or relabelled.

The producer was committed source `41058d99a2b2dee790cdd9679bdcf5dabaa325be`,
using `host/record-selected-service.py --run --long-qualification`,
`--execution-profile long1m-graphs` and `--lifetime-profile ordinary-24h`.
The operator-specific paths, address and disposable test credentials stay private.
The original owner cutoff was `2026-10-27T18:00:00Z`; it was not extended.
Follow the [profile runbook](../../../docs/tasks/p2-t3-million-context.md) with
reviewed operator inputs for another authorized run.

Model: `dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8`, revision
`c19ca5ca4eaeb79d29fdf08aea8327da95b1116a`, 79 files / 510,311,056,155 bytes.
Image: `sha256:c17040e00711efd40d3aaacd46bfc8846e151d2e37b5d26fabfea6173acdc865`.
The contract uses TP4 x DP2, FP8 KV, GPU-resident Engram, 4,096-token chunked
prefill, prefix caching and decode graph shape `[1]`. Public admission stays C1.

The original broad `hardware_validated`, `deployable`, `published`, privacy,
quality, pricing and capacity flags remain false. A finite hardware pass does
not promote those broader claims. The model catalog remains empty because the
59-minute request limit exceeds the pinned marketplace contract's 15-minute
ceiling. This change does not modify that gate, API behavior or customer pricing.

## Offline Replay

`python3.12 -m unittest discover -s scripts -p test_selected_million_evidence.py`
checks exact artifact hashes, source/preparation/image joins, token counts,
cache controls and cleanup. It executes the historical validation code offline
and tests mutated usage and unsupported claims. No network, engine or GPU is used.
