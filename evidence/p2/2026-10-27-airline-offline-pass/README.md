# Original-Worker Airline Replay: Pass

The committed recorder at `a52207581d25abbfb2f2841d814c0caa276aebf2`
completed all five selected airline tasks on October 27, 2026, from 18:10:28
through 18:15:10 UTC. This is an offline software test with fixed peers and
synthetic token counts, **not a live-model benchmark or quality score**.

The authenticated upstream revision is
[`fc0055dc4e0a316c3f83133267fbd6faaa770992`](https://github.com/sierra-research/tau2-bench/tree/fc0055dc4e0a316c3f83133267fbd6faaa770992).
The original worker, tasks, tools, assertion order and reward criteria are
unchanged. Task IDs are `0, 1, 10, 11, 12`; assertion counts are `1, 1, 1, 3, 2`.
Every task exercised one agent `calculate` tool round trip, two text-only
simulator calls and one evaluator call. All five scorer cleanups were confirmed.

Original rewards were `1, 1, 1, 0, 0`. These tasks use database and communication
checks as their reward basis, not the language assertions. The fixed peer made
no database changes; the first three tasks require none, while the final two
require changes. The original language evaluator still ran with its original
ordered assertions. A passing replay means these expected original outcomes
and lifecycle checks matched, not that a model solved all tasks.

The journal contains all five original receipts, 17 hash-linked events and
the final controller result. Its $200 cap and $31.4310645 settled reservations
(including $31.427280 for 15 count attempts) are synthetic accounting fixtures,
not invoices, paid usage or available credit. All trial holds were released.
Both paid-count and paid-generation call counts are zero. Token-count parity
with a real upstream is not asserted.

## Preserved Failures

- The earlier [socket failure](../2026-10-27-airline-offline-socket-failure/README.md)
  used a macOS proxy socket inside Linux. A read-only version check established
  that the daemon-native socket worked before the corrected run.
- The [wrong expected-reward failure](../2026-10-27-airline-offline-reward-expectation/README.md)
  completed the original tasks but failed the recorder's incorrect expectation.
  Only that expectation was corrected; original task scores remain unchanged.
- `preflight-failure/` preserves a later attempt that stopped at provenance
  before creating a journal. Its original receipt has no authenticated producer
  SHA, and none has been filled in. Two separate read-only checks of the same
  mounted source then independently returned the expected commit; their outputs
  are retained. The exact cause of this early failure is unclassified. The
  subsequent bounded replay is a separate run, not a replacement receipt.

This does not establish live endpoint eligibility, a repeated full-domain tau
score, a matched-reference comparison, GPU performance, confidential inference
or a production change.
