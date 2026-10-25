# Selected tau: known assistant tool rejected, cleanup confirmed

Original producer `76013ab9d7da6a98d35d7bae0a7dc5eead494737`,
2026-10-25 02:45:33-03:04:53 UTC. The twelve original reports are unchanged;
[tests](../../../scripts/test_selected_tau_attribution_evidence.py) pin every
byte and join producer sources, readiness receipts and owner cleanup.

The [controller](evidence/output.log) records **FAIL** at the trial stage.
The [scorer](scorer/output.log) records a known assistant tool raising one
`ValueError` during live dispatch: `too_many_errors`, zero unattributed errors.
This is more specific than the previous diagnostic, but does not establish
which tool, which argument, or which business rule rejected the operation.
No conversation, tool names, arguments or exception messages were recorded.

Retail task 2, trial 0, seed 42 lasted 24,821 ms: six requests, three agent,
three simulator, zero evaluator; 16,093 prompt and 226 completion tokens.
Reward and cost are null. The strict `max_errors=1`, zero retries, 8K context
and 256-token output ceiling were unchanged. The small Qwen3-0.6B simulator
and Qwen3-1.7B evaluator remain unqualified for full quality evaluation. This
failure alone does not establish whether the candidate or simulator supplied
incorrect lookup data.

The selected abliterated checkpoint remains revision
`c19ca5ca4eaeb79d29fdf08aea8327da95b1116a`, with the ordinary eight-B300
`tau8k-eager` profile, driver 580.178.04, CUDA driver API 13.0, CC disabled.
Startup reached readiness in 826,156 ms and the four tokenizer parity cases
passed. Both owners completed the failure handshake and exited 1; the scorer
protocol completed and all components confirmed cleanup. The GPU engine
stopped. Cleanup success is not trial success.

All quality, deployment, live-admission and confidential qualification flags
remain false. The next software diagnostic adds only a hash-pinned source line
for a rejection; it was **not present in this run**, and no exact rejection
site may be inferred retrospectively.
