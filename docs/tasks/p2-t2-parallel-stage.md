# Bounded Parallel Weight Staging

`nib model stage --download` accepts `--workers 1..4`. The default remains
one worker, with the same proof shape and pinned manifest verification.
`host/record-stage.py run` accepts the same option and records nondefault
worker counts in the evidence and command.

Each file has one request attempt and a body limit of its manifest byte size
plus one byte. The total of these disjoint limits is no larger than the
already required global byte budget. No retry loop or range-download behavior
is added. Existing complete files are hashed before reuse; every new file
must pass its size/hash checks before atomic no-overwrite publication.

A shared cancellation context stops sibling downloads on failure. Every
worker and its partial-file cleanup finishes before the staging lock is
released. Each worker has its own hash and copy buffer. Four workers is a
ceiling, not a measured optimal setting.

Focused race tests cover actual overlapping requests, the concurrency bound,
identical serial/parallel proofs, verified reuse, failure cancellation/join,
partial cleanup, invalid counts, and oversized/corrupt response bodies.
These are local fake-transport tests, not download-throughput measurements.
The original single-stream hardware evidence is retained unchanged.
