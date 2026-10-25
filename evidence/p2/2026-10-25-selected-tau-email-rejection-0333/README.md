# Selected tau: email lookup rejected, cleanup confirmed

Original producer `71d28f9fa97bab1cbfd176d1559963f80541c663`,
2026-10-25 03:33:48-03:53:54 UTC. All twelve original files are unchanged.
[Regression tests](../../../scripts/test_selected_tau_origin_evidence.py)
pin their bytes, producer ancestry, source closure, original readiness receipts,
failure handshake and independently joined cleanup reports.

The [controller](evidence/output.log) and [scorer](scorer/output.log) record
**FAIL**. A known assistant tool raised one `ValueError`; the content-free
origin is retail tools SHA256
`34f4fe9702d36f0dd4697700fbcd63d0cf5386e6eb8e7499aaa336243bad8750`, line 330.
The [exact upstream source](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/domains/retail/tools.py#L314-L330)
identifies `find_user_id_by_email` exhausting the customer database and raising
`User not found`. The upstream archive and this file were checked against the
pinned hashes locally; this interpretation is not based on exception text
captured from the run.

This establishes the rejected operation, not who supplied the wrong email.
The diagnostic retained no conversation, tool argument or exception message.
It cannot distinguish a simulator-provided incorrect email from an assistant
transcription or invention. Do not retroactively assign this exact origin to
earlier runs, which did not record it.

Retail task 2, trial 0, seed 42 ran for 26,470 ms: six requests, three agent,
three simulator, zero evaluator; 16,081 prompt and 225 completion tokens.
Reward and cost remain null. The unchanged policy stops at one tool-error
batch, with zero retries, 8192 context and a 256-token output ceiling. The
Qwen3-0.6B simulator and Qwen3-1.7B evaluator remain unqualified support models.
This single operational task is not a full tau score or quality comparison.

The selected abliterated checkpoint remains revision
`c19ca5ca4eaeb79d29fdf08aea8327da95b1116a`, ordinary eight B300s, DP2/TP4,
`tau8k-eager`, driver 580.178.04 and CUDA driver API 13.0, CC disabled.
Startup reached readiness in 870,158 ms; all four tokenizer-parity cases passed.
Both owners acknowledged `protocol_error`, three requests each, and exited 1.
Scorer protocol completion and cleanup passed; both owner cleanup reports and
the final controller join confirmed teardown, including a stopped GPU engine.
Successful startup and cleanup do not turn the task into a pass. No production,
confidentiality, live-admission or quality qualification is claimed.
