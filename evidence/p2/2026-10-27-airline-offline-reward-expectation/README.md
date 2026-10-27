# Offline Airline Replay: Wrong Expected Rewards

Original producer: `6a8a4f5f18163612d89bce367912994c37c83792`.
The network-disabled original worker completed all five fixed airline tasks
`0, 1, 10, 11, 12`, with original ordered assertion counts `1, 1, 1, 3, 2`.
All five owned scorer cleanups were confirmed. Each task exercised an agent
`calculate` tool round trip, two text-only simulator calls, and one original
multi-assertion evaluator call. The peers and token counts were synthetic.

The outer recorder **failed** because its author's fixture incorrectly
expected every reward to be zero. The unchanged upstream rewards were
**1, 1, 1, 0, 0**. These tasks use `DB` and `COMMUNICATE`, not `NL_ASSERTION`,
as their reward basis. The first three require no database write or communicated
value. The final two require database changes the fixed peer did not perform.
`ALL_WITH_NL_ASSERTIONS` runs the all-false language judge but the original
[evaluator](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/evaluator/evaluator.py)
multiplies only components in the task's original basis. Neither tasks nor
scoring were changed to repair the offline recorder's expectation.

The controller retained all five original receipts and settled $31.4310645
in conservative synthetic reservations, including $31.427280 for 15 synthetic
count attempts. This is not actual billing or available credit. The outer FAIL
and original bytes remain preserved, despite successful inner lifecycle checks.
No retry substituted tasks, rewards or original assertions.

No live model, paid request, hardware validation, model-quality result, repeated
tau score, matched-reference comparison, confidential inference, or production
change is established by this artifact.
