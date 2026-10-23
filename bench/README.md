# Benchmarks

P2 synthetic workloads and measurement tooling. The workload sweep and quality
comparison are not complete. Functional/API checks elsewhere in `evidence/`
are not throughput or quality qualification.

The [stream measurement component](../docs/benchmarks/stream-measurement.md)
implements bounded, metadata-only text-stream observations and cohort statistics.
The [owned-engine HTTP collector](../docs/benchmarks/stream-transport.md) exercises
incremental collection against local synthetic HTTP; controller wiring is pending.
It is tested with synthetic streams, not yet connected to a live benchmark
controller. It does not launch models or authorize inference.
