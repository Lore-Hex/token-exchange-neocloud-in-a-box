# Benchmarks

P2 synthetic workloads and measurement tooling. The workload sweep and quality
comparison are not complete. Functional/API checks elsewhere in `evidence/`
are not throughput or quality qualification.

The [frozen quality protocol](../docs/benchmarks/quality-protocol.md) verifies
pinned lm-eval task files and dataset bytes and selects the same 200 questions
for each side of a future comparison. It does not run or score inference.

The [stream measurement component](../docs/benchmarks/stream-measurement.md)
implements bounded, metadata-only text-stream observations and cohort statistics.
The [owned-engine HTTP collector](../docs/benchmarks/stream-transport.md) exercises
incremental collection, and the lab controller now wires it to the owned engine.
The [bounded chat cohort](../docs/benchmarks/stream-cohort.md) provides exact-length
synthetic workload preparation and sequential warmup/measurement accounting.

The original [selected graph-stream report](../evidence/p2/2026-10-24-selected-graphs-stream/output.log)
records eight measured concurrency-one requests after one excluded warmup:
143.36 output tok/s and median TTFT 98.61 ms. Its
[run envelope](../evidence/p2/2026-10-24-selected-graphs-stream/run.json)
still says `fail`; the lab's outer qualification remains `BLOCKED`. This narrow
selected abliterated-checkpoint measurement is not the full P2 workload sweep
or a quality score for DeepSeek's original revision.

The [selected 8K parity report](../evidence/p2/2026-10-24-selected-tau8k-parity/output.log)
and [CPU-only retry](../evidence/p2/2026-10-24-cpu-http-threads-pass/output.log)
are prerequisites, not tau evaluations. The
[selected controller attempt](../evidence/p2/2026-10-24-selected-tau-readiness-failure-1800/evidence/output.log)
failed before READY/scoring with cleanup unconfirmed. Full workload/concurrency
coverage, the [original-revision quality comparison](../docs/benchmarks/tau-validation.md),
and confidential validation remain unproven. None of these artifacts authorizes
another hardware run.
