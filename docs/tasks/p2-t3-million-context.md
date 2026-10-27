# P2.T3: 1M context and 384K output candidate

Status: **finite C1 hardware trial passed on ordinary eight-B300**, including
655,360 input plus 393,216 actual output tokens and all nine retrieval/cache
checks through 1,048,320 input tokens. See the
[original October 26 results](../../evidence/p2/2026-10-26-selected-million-pass/README.md).
This is **not sustained capacity, quality qualification, confidential inference,
or a public serving guarantee**. The existing
[128K/16K graph trial](../../evidence/p2/2026-10-26-selected-long-graphs/README.md)
and all older execution profiles and receipts are unchanged.

## Source and limits

The selected abliterated checkpoint's immutable
[config.json](https://huggingface.co/dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8/blob/c19ca5ca4eaeb79d29fdf08aea8327da95b1116a/config.json)
sets `text_config.max_position_embeddings=1048576` and `use_cache=true`.
Its SHA-256 is
`8be45ce0476004a3f529fd896115a4a2e800a129ad2d3ec05b16050f52e21879`,
already in the [selected manifest](../../models/deepseek-v4.1-flash-1017-uncensored-fp8/1/files.sha256).
[DeepSeek's published API table](https://api-docs.deepseek.com/quick_start/pricing/)
lists 1M context and 384K maximum output (read October 26, 2026). The output
ceiling was the parity target; the separate finite hardware trial now records
that many actual output tokens, without making a general completion guarantee.

| Bound | Explicit candidate |
|---|---|
| Context, input plus output | 1,048,576 tokens |
| Maximum requested output | 393,216 tokens |
| Default output if omitted | 2,048 tokens |
| Total admitted requests | 1 |
| Chunked prefill budget | 4,096 tokens per iteration |
| Request body | 16 MiB |
| Request deadline | 3,540 seconds (59 minutes) |
| Guard and TLS shutdown grace | 3,550 seconds |
| Owner drain per frontend | 3,570 seconds |
| Reserved cleanup | 7,240 seconds |
| Measurement stream / event caps | 512 MiB / 64 KiB |
| Qualification stage, including tokenizer | 7,200 seconds |
| Tokenizer preparation within stage | 600 seconds |

The selected engine remains pinned to
[`5df63a7203cdb9328d824a27b682e77c7b9928be`](https://github.com/vllm-project/vllm/tree/5df63a7203cdb9328d824a27b682e77c7b9928be).
Only `--max-model-len` changes from the 128K graph engine arguments. It retains
TP4 x DP2 on ordinary eight-B300 hardware, FP8 KV, GPU-resident Engram,
512 GiB host-memory bound, zero swap, chunked prefill, explicit prefix caching,
actual cached-token usage and decode-only graph shape `[1]`. See the
[source-backed settings](p2-t3-long-context-cache.md). The pinned
[KV capacity check](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/v1/core/kv_cache_utils.py#L1009-L1039)
must still pass; model configuration alone does not prove that memory fits.
No automatic context reduction, offload, DSpark, batching increase or
confidential hardware relabeling is allowed.

The pinned TLS shim
[`ad6dce916b2fb6754196f32fd778d1bc6e971db2`](https://github.com/Lore-Hex/trustedrouter-confidential-stack/blob/ad6dce916b2fb6754196f32fd778d1bc6e971db2/cmd/trcs/main.go)
caps shutdown grace at one hour. The 59-minute request fits that cap and leaves
bounded frontend drains. Cleanup is `2 * 3570 + 4 * 25 = 7240` seconds. Host
admission requires **more than 16,420 seconds** remaining: startup 1,800 + stage
7,200 + cleanup 7,240 + host reserve 180. Tokenizer time is inside the stage,
not an extra allowance. Absolute retirement can shorten, never extend, this.

The byte cap is separate from token capacity: not every 1M-token encoding fits
16 MiB. Neither it nor the context ceiling promises completion before the
deadline. No caller gets a larger default output or unbounded generation.

## Qualification path

Use the existing selected-service prepare/build/run path with
`--execution-profile long1m-graphs`,
`--tokenizer-parity --tokenizer-materialization --runtime-observation`, and the
owned `--long-qualification` hook. Build from reviewed committed source. Drain
an existing owner before startup; never launch a second engine on occupied GPUs.

The same ten-request C1 schedule is parameterized, not copied:

1. Exact pinned-chat-template prompts of 261,888, 524,032 and 1,048,320 tokens,
   each reserving 256 output. Each has cold, warm and different-namespace runs.
2. A 655,360-token prompt with 393,216 requested output, totaling 1,048,576.
   Actual final `completion_tokens=393216` and `finish_reason=length` are required.
   Early EOS, missing final usage, truncation and timeouts cannot pass. No
   `ignore_eos`, retries or output-token estimates substitute for actual usage.
3. Reserve 10 requests, 6,158,080 prompt tokens and 395,520 output tokens. A
   shared two-hour stage clips each individual deadline to the remaining time.
4. Retain content-free measurements, source/engine identity and cleanup receipts.
   New v2 workload/qualification envelopes bind the 1M profile; old 128K v1
   receipts replay unchanged and cannot be relabeled as 1M evidence.

HTTP framing and measurement limits both opt into 512 MiB; legacy users retain
64 MiB. Duplicate headers, invalid chunk delimiters/trailers, oversized bodies,
unknown usage and cancellation remain fail-closed. The collector does not retain
prompts, output text or secrets.

Cache routing retains normalized per-workspace namespaces and DP affinity.
Missing namespaces stay fresh per request. Actual warm counters are necessary,
but cross-replica controls do not establish same-replica isolation. Rank identity
and broad cache isolation remain unqualified. Public cache discounts, first-token
and idle-time promises and sustained concurrency need separate qualification.
The million profile now declares prompt-cache support based on the measured
warm counters, but cached pricing remains null, not an invented discount.
Subsequent [retail tau2](../../evidence/p2/2026-10-26-tau-external-retail-pass/README.md)
and [calibration](p2-t3-retained-quality.md) results are separate bounded evidence,
not full-suite quality certification.

## Marketplace integration gate

The original candidate deliberately returned `data: []`: the marketplace v2.0
contract and provider checker 0.1.8 allowed at most 900 seconds for completion,
not the 3,540 seconds needed by this profile. The live
[marketplace schema](https://trustedrouter.com/providers/marketplace/catalog.v2.schema.json)
confirmed this ceiling on October 26, 2026.

The coordinated v2.1 contract now permits an explicit completion deadline up to
3600 seconds, while v2.0 still rejects anything above 900. This profile emits one
model with its actual 3540-second deadline and qualified capabilities. The
[schema provenance](../../catalog/schema-provenance.json) identifies the exact
control-plane source revision; it does not claim that revision is deployed.
Legacy catalogs remain byte-identical and replay through the unchanged 0.1.8
checker. That historical checker still rejects the new v2.1 declaration, as it
should: old evidence cannot become a new qualification by relabeling it.

Public listing requires deployed upstream contract/checker support, verified
router deadlines, then fresh endpoint conformance. Until that happens, catalog
support alone is not a public serving or uptime guarantee.

## Tests and remaining gates

`scripts/test_selected_million.py` exercises source-bound arguments, exact token
budgets, deadlines, tokenizer child, report validation and full lifecycle joins.
`scripts/test_stream_transport.py` and `scripts/test_stream_measurement.py` cover
bounded HTTP/SSE framing; `internal/pilotapi/million_profile_test.go` verifies
catalog/output/body/deadline/admission limits. Shared Go tests exercise namespace
isolation and rank affinity for both long profiles. Historical argv and receipt
replays remain regression gates. `scripts/test_pilot_catalog.py` replays generated
catalogs through the pinned checker; it is mandatory in `check_checker_tests.py`.

Real GPU startup, all ten qualification requests, full output, cache counters
and cleanup passed in the committed finite trial. The offline
`scripts/test_selected_million_evidence.py` replays its original producer's
validators and rejects inflated usage, cache, privacy and capacity claims.
The marketplace integration gate above, sustained load, general quality and
P3/P4/P5 confidential-image, verifier and full confidential-node exits remain
separate unfulfilled gates.
