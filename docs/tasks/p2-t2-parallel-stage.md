# Bounded Parallel Weight Staging

`nib model stage --download` accepts `--workers 1..8`. The default remains
one worker, with the same proof shape and pinned manifest verification.
`host/record-stage.py run` accepts the same option and records nondefault
worker counts in the evidence and command.

Parallel workers take missing files in descending manifest-byte size, keeping
manifest order for ties. Large shards start early instead of waiting behind
small files and becoming a long tail. Only the pending work queue is reordered:
the manifest, its digest, final proof and serial/default order are unchanged.
Existing complete files still pass verification before any download starts.
This scheduling change has fake-transport ordering/reuse tests, not a measured
wall-clock improvement. It does not alter an already running recorder bundle.

Each file has one request attempt and a body limit of its manifest byte size
plus one byte. The total of these disjoint limits is no larger than the
already required global byte budget. No retry loop or range-download behavior
is added. Existing complete files are hashed before reuse; every new file
must pass its size/hash checks before atomic no-overwrite publication.

A shared cancellation context stops sibling downloads on failure. Every
worker and its partial-file cleanup finishes before the staging lock is
released. Each worker has its own hash and 256 KiB copy buffer. Eight workers
use 2 MiB of copy buffers, excluding TLS/HTTP/runtime overhead. The ceiling
was raised from four to eight to permit better utilization of large-node
download links; this is not a claim of measured optimal throughput. Total
body budget, one-attempt policy, exact hashes and time bounds are unchanged.

Focused race tests cover actual overlapping requests, the concurrency bound,
identical serial/parallel proofs, verified reuse, failure cancellation/join,
partial cleanup, invalid counts, and oversized/corrupt response bodies.
These are local fake-transport tests, not download-throughput measurements.
The original single-stream hardware evidence is retained unchanged.
An additional nine-file race test requires eight overlapping requests, joins
all workers, checks the same global body budget and serial proof, and verifies
that resuming with eight workers makes no requests for complete files.
