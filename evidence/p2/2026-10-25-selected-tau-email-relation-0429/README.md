# Selected tau: prior-user-text relation, failed task

Original producer `32771fb4ca03cc25ab3177b70c431813cd1a2bf0`,
2026-10-25 04:29:40-04:49:13 UTC. All twelve original files are unchanged.
[Regression tests](../../../scripts/test_selected_tau_email_relation_evidence.py)
pin their sizes/hashes, producer ancestry, 37-file source closure, original
readiness and CPU receipts, failure handshake and joined cleanup reports.

The [controller](evidence/output.log) and [scorer](scorer/output.log) record
**FAIL** / `protocol_error`, `too_many_errors`, one known assistant tool
`ValueError`, and zero unattributed errors. The exact origin is retail tools
SHA256 `34f4fe9702d36f0dd4697700fbcd63d0cf5386e6eb8e7499aaa336243bad8750`,
line 330. The [pinned upstream method](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/domains/retail/tools.py#L314-L330)
identifies the email lookup rejecting an email absent from its customer database.
This source interpretation does not rely on retained exception text.

The new bounded observation is `prior_user_text_contains_argument=true`:
the submitted email argument occurred as a literal case-insensitive substring
in earlier simulator-user text. It is **not** a comparison with the task's
intended customer identity or correct email. It does not establish user intent,
full identity, simulator correctness, or which model caused the failure.
Negation, quoted text and other context are not represented by this boolean.
No email, conversation, tool argument, prompt, response or exception message
was retained in the diagnostic. The [earlier failure](../2026-10-25-selected-tau-email-rejection-0333/README.md)
did not contain this field and cannot be given the same relation retroactively.

Retail task 2, trial 0, seed 42: 25,040 ms of trial time; six requests, three
agent, three simulator, zero evaluator. Actual usage is 16,092 prompt +225
completion =16,317 total tokens. Reward and cost remain null. The one-tool-error
stop, zero retries, 8192 candidate context and 256-token output ceiling remain
unchanged. Qwen3-0.6B simulator and Qwen3-1.7B evaluator are unqualified support
models; this is not a full tau score, model comparison or quality acceptance.

The selected abliterated checkpoint remains revision
`c19ca5ca4eaeb79d29fdf08aea8327da95b1116a`, ordinary eight B300s, DP2/TP4,
`tau8k-eager`, driver 580.178.04, CUDA driver API 13.0 and CC disabled.
Startup reached readiness in 840,203 ms; four tokenizer-parity cases passed.
Both owners acknowledged `protocol_error` with three completed requests and
exited 1. GPU engine stop, owner cleanup, scorer cleanup and controller cleanup
were confirmed in the originals. The operator also reported an empty Docker
inventory and idle GPUs after termination; that separate inspection is not an
additional artifact here. Startup and cleanup do not convert the task to PASS.
All quality, live-admission, confidentiality and deployment claims remain false.
