# P2.T3: Selected checkpoint provider conformance

The selected `dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8`
checkpoint passed direct provider-check Tiers 1-4 on an ordinary eight-B300
node on 2026-10-24. The committed recorder was
`68ab473fda5b29bf85be07099494702b63aeba69`.

[Original receipts](../../evidence/p2/2026-10-24-selected-provider-conformance/)
bind the exact selected manifest, runtime input, source closure, image,
process identity, tokenizer parity and isolated checker. No original bytes
or historical status were rewritten. Regression tests pin those bytes and
reject modified check results, runtime identity, acknowledgement and cleanup.

- 17 required checks passed; the optional declared v2 catalog check was skipped.
- Native discovery, chat content, actual usage, model identity, finish reason,
  optional fields and streaming semantics passed.
- All four tokenizer parity cases passed, using eight endpoint requests.
- Same owned API process and listener before and after the checker; engine and
  checker cleanup confirmed, exit zero, no OOM.
- Engine readiness wait: 835,146 ms. This is startup, not TTFT or throughput.
- Fixed eager TP4 x DP2, 4K context, 512 GiB process memory limit and zero swap.

This is **API conformance**, not a tau score, measured throughput, a public
production endpoint or confidential qualification. CC was disabled. The
original outer `result: fail`, `status: BLOCKED`, `deployable: false` and
`confidential: false` remain intact because broader qualification gates are
still open. The selected [8K prerequisite](p2-t4-selected-tau-session.md) and live
operational tau trial are separate work.
