# Selected DeepSeek Graph-Mode Correctness

**Measured:** 8/8 fixed correctness cases and 10/10 requests passed on eight
ordinary B300 GPUs, October 24, 2026, 10:23:07-10:50:04 UTC. The cases cover
arithmetic, ordering, logic, extraction, JSON and two tool-call round trips.

This run uses the selected `dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8`
checkpoint at `c19ca5ca4eaeb79d29fdf08aea8327da95b1116a`, unchanged from the
[eager correctness run](../2026-10-24-ordinary-selected-correctness/README.md).
Producer: `5b4e2f54f2b9c14ff46fb9c1efcfbcdbf37b49f1`.

The explicit `batch8-graphs` profile uses TP4 x DP2, 4096 context, maximum eight
sequences per rank, and `FULL_DECODE_ONLY` capture sizes 1/2/4/8. The request
phase took 13,454 ms; startup took 1,237,458 ms. These short cases do **not**
establish tokens/second, sustained load or concurrency-eight throughput.

Engine shutdown and owned-container cleanup were confirmed. The container
exited zero without OOM. GPU confidential mode was disabled and no attestation
was verified. The outer `result=fail`, `status=BLOCKED`, `deployable=false`, and
`hardware_validated=false` remain unchanged; the narrow correctness experiment
has `functional_pass=true`. This is not tau, quality or production acceptance.

The original [run.json](run.json), [output.log](output.log),
[input-contract.json](input-contract.json) and [receipt.json](receipt.json)
are copied without rewriting. They contain fixed cases, usage and timings,
not prompts, completions, credentials or private deployment addresses.

[Regression checks](../../../scripts/test_selected_graphs_evidence.py) reuse
the eager evidence checks, bind all original hashes and producer sources, and
verify that the launch is exactly the committed graph profile applied to the
baseline. Missing usage, missing tool turns and inflated quality claims remain
rejected. Offline validation is not a second hardware run.
