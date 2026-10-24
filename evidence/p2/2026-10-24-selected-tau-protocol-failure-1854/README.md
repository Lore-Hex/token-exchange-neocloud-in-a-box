# Selected Tau: First Completed Operational Attempt

Original failed run, 2026-10-24 18:54:24-19:13:45 UTC, from committed producer
`7d40f8916c8b76dbb6db93f020795d108d428c55`. This is the selected abliterated
checkpoint, not the original-revision full quality gate.

- [GPU report](gpu/output.log): eight selected B300s, driver `580.178.04`,
  CUDA driver API `13.0`, CC disabled. Startup reached READY after **821,161 ms**.
  All four fixed 8K tokenizer-parity cases passed (eight parity requests,
  separate from the trial). The owner then completed five agent requests.
- [Scorer report](scorer/output.log): retail task 2, trial 0, seed 42;
  **10 requests** (five agent, five simulator, zero evaluator), **28.809 s**,
  **27,355 input + 296 output = 27,651 total tokens**. The trial ended
  `protocol_error`, reward null, pricing not measured. Scorer exit 0 and its
  completed protocol mean it reported the failure, not that the trial passed.
- [CPU report](cpu/output.log): both support models passed their fixed parity
  checks and the owner completed five simulator requests. Its four original
  [runtime/tokenizer receipts](cpu/evaluator-runtime.json) are retained; their
  byte hashes match the report's receipt map.
- [Controller report](evidence/output.log): `result: fail`, stage `trial`,
  owner exits candidate/support both 1, `owner_reports: null`, cleanup
  **unconfirmed**. Both READY hashes and the original scorer report are retained.
  GPU and CPU reports independently record cleanup **confirmed**, and the GPU
  report records `engine_stopped: true`. These do not rewrite the controller's
  incomplete final join. The producer's
  [final join](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/blob/7d40f8916c8b76dbb6db93f020795d108d428c55/scripts/record-tau-selected.py#L203)
  required passing scorer/CPU results and therefore rejected this failed trial.

**Diagnosis, not an original measured cause:** the operator's source-based
diagnosis is a high-confidence inference of upstream `too_many_errors` under
`max_errors=1`. The pinned
[worker mapping](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/blob/7d40f8916c8b76dbb6db93f020795d108d428c55/scripts/tau_trial_worker.py#L393)
maps that reason, `agent_error`, and `user_error` to `protocol_error`, with the
same default for other unrecognized reasons. The originals retain neither the
exact upstream reason nor the tool/exception. They cannot prove which tool
failed or distinguish model arguments from a code defect.

The operator separately reported a later empty-container check and zero GPU
use. That later observation is not part of these originals and is not a
retroactive cleanup receipt. No quality score, full tau acceptance, matched
original-revision comparison, confidential-computing result, or deployment
qualification is established. All original FAIL/BLOCKED results and false
qualification, live-admission and confidentiality flags are unchanged.

The four original envelopes are [GPU](gpu/run.json), [CPU](cpu/run.json),
[scorer](scorer/run.json), and [controller](evidence/run.json).
[Regression tests](../../../scripts/test_selected_tau_trial_evidence.py) pin
all twelve original byte hashes, exact producer ancestry and source closures,
READY/finish/count and receipt joins, source-defined limits, and negative
qualification/privacy controls. Only these eight run/log files and four CPU
receipts are retained; no scorer payload or private deployment identifiers are
included. The GPU bundle/contract and frozen scorer inputs are not included,
so their recorded hashes alone are not a full input replay or fresh live
ownership check.
