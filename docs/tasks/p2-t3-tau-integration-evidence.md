# Scripted tau integration evidence

The original [run envelope](../../evidence/p2/2026-10-21-tau-integrated-7afb53b/run.json)
and [output](../../evidence/p2/2026-10-21-tau-integrated-7afb53b/output.log)
record a **passed software integration**, produced by
`7afb53b2b375318ad1ae2e0ef3fdbf918fca5574` at 00:04:21-00:04:30 UTC on
2026-10-21. Both files are preserved byte-for-byte. This follows the earlier
[response-model failure](p2-t3-tau-response-model.md), which remains preserved.

The pinned upstream tau runtime evaluated airline task 0, trial 0, seed 670487,
with one scripted agent response, two scripted simulator responses and one
scripted evaluator response. It returned an evaluated trial record and the
recorder confirmed container cleanup. No live model endpoint was used.

The recorded reward of `1.0` belongs to this **synthetic fixture**, not DeepSeek.
The eight tokens and `$0.00001900` are synthetic accounting values, not measured
tokenization, actual API spending or GPU cost. The artifact explicitly retains
`live_transport=false`, `quality_accepted=false`, `confidential=false` and
`hardware_validated=false`. It cannot establish a benchmark score or satisfy
the frozen tau quality gate.

Regression tests pin the original artifact hashes and verify every recorded
toolkit source hash against its producer commit. They check role counts,
synthetic usage, the unchanged hardware/quality limits, and confirmed cleanup.
No upstream package, model or Docker execution occurs in these regressions.

Still required: connect the isolated worker to the reviewed live transport,
prove runtime and tokenization identity, run independently served simulator and
evaluator roles, and execute the frozen paired-reference benchmark with failure,
uncertainty, latency and cost reporting.
