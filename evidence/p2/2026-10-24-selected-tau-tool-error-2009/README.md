# Selected operational trial: upstream tool error, cleanup confirmed

Original reports from producer `bc8585c183a3ca7721a7a7dfc11f7fa0c715e54b`,
2026-10-24 20:09:53-20:29:19 UTC. All 12 files are preserved byte-for-byte;
the [regression test](../../../scripts/test_selected_tau_diagnostic_evidence.py)
pins their sizes and SHA-256 hashes and verifies committed producer sources.

The [controller](evidence/output.log) records **FAIL**, `protocol_error`, and
confirmed cleanup. Its owner-report hashes join the original
[GPU report](gpu/output.log) and [CPU report](cpu/output.log); its embedded
scorer report matches the [standalone scorer](scorer/output.log).
Both owners completed the explicit finish handshake with three requests each
and exited 1 for the failed trial. Scorer protocol completion and successful
cleanup are not a successful quality result.

- Retail task 2, trial 0, seed 42: 24,489 ms, six requests (three agent, three
  simulator, zero evaluator), 16,057 prompt and 205 completion tokens, 16,262
  total. Reward is null; pricing was not measured.
- The original bounded diagnostic explicitly records `too_many_errors` and
  `tool_errors: 1`. It contains no tool identity, arguments, exception text or
  conversation. The exact cause and model-versus-CPU-simulator attribution
  remain unknown. This is more specific than the
  [earlier 1854 report](../2026-10-24-selected-tau-protocol-failure-1854/README.md),
  whose originals and inference limits are unchanged.
- GPU startup reached readiness in 830,098 ms on eight B300s, driver
  `580.178.04`, CUDA driver API `13.0`, CC disabled. Four 8K tokenizer parity
  cases passed (eight requests). Both GPU and CPU owners, scorer and controller
  report confirmed cleanup; the GPU report records the engine stopped.
- The selected checkpoint is
  `dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8`, revision
  `c19ca5ca4eaeb79d29fdf08aea8327da95b1116a`, using `tau8k-eager` and the
  original bare served name. This is not the original-revision quality gate.

The [GPU](gpu/run.json), [CPU](cpu/run.json), [scorer](scorer/run.json) and
[controller](evidence/run.json) envelopes remain failed and unvalidated.
The GPU remains `BLOCKED`; all qualification, deployment, live-admission and
confidential flags remain false. No full tau score, production serving,
quality acceptance or confidential-computing claim is established.

The four CPU readiness receipts remain alongside the CPU report and are
joined by their original byte hashes. Tests reuse the historical CPU loader,
check all 36 scorer source hashes, retain the original bounded policy, and
reject diagnostic content, malformed counts and qualification promotion.
Only scanner-observed public hash fields receive exact path/field/value
exceptions; wrong paths, keys, values and same-line secrets remain detected.
No private payload or deployment identity is included.
