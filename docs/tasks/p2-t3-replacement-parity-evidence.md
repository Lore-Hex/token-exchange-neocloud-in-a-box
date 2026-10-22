# P2.T3 replacement-host tokenizer parity evidence

## Original artifacts

The approved replacement eight-B300 lab retained its read-only public weight
cache and restored all 83 files (510,311,045,295 bytes) into private working
storage. Source and destination hashes matched the pinned manifest. The
[restore receipt](../../evidence/p2/2026-10-22-replacement-cache-restore/run.json)
is the original single JSON emitted by committed producer
`a6c9d95a8c1286c0751d974003bddd33a0d1650b`, also retained as `output.log`.
Its worker completed; its narrower cleanup scope is not full process-group
absence proof. No files were removed from the source cache.

A [fresh GPU appraisal](../../evidence/p2/2026-10-22-pre-parity-gpu-appraisal/run.json)
at 12:33:30 UTC authenticated all eight selected GPUs and their required
claims. Readiness was already 1 and remained 1. This is GPU appraisal only;
it does not attest CPU, image, serving runtime, fabric or the TLS endpoint.

Committed producer `6fd01351158e3a2f0ff66d44abd0a6b2c3379ccc` ran the
[tokenizer parity experiment](p2-t3-tokenizer-parity.md) from 12:53:14 to
13:19:39 UTC on 2026-10-22. Original
[run envelope](../../evidence/p2/2026-10-22-tokenizer-parity-tool-protocol-fail/run.json)
and [output](../../evidence/p2/2026-10-22-tokenizer-parity-tool-protocol-fail/output.log)
are preserved without rewriting their statuses. The engine became ready after
1,220,332 ms. Seven requests then ran in the 8,388 ms comparison phase.

| Fixed case | Local input tokens | Server token IDs and generated usage |
| --- | ---: | --- |
| Text | 12 | Match; one generated token |
| System message | 16 | Match; one generated token |
| Tools | 275 | Match; one generated token |
| Tool continuation | 332 | Protocol failure on `/tokenize`; no server token result |

The last case did not reach generation. This receipt does **not** establish a
token-ID mismatch, invalid model behavior or an exact upstream error: it records
only a protocol failure, without an HTTP status or response body. Its root cause
requires a separate reproduction. Do not weaken the comparison, remove this
case or use these partial results as a benchmark token-accounting approval.

## Cleanup and limits

The engine stopped and the owned container cleanup was confirmed. The approved
VM, public weight cache and dedicated access key were retained for continued
testing. This is not proof of a continuously serving endpoint.

The experiment remains `functional_pass=false`, `BLOCKED`, non-deployable and
non-confidential. No live-model tau trial, quality score, throughput result,
native/JIT provenance or end-to-end attested service is claimed. The host's
confidential CPU platform is AMD SEV-SNP, not the Intel TDX platform required by
the current release design. Passing the GPU appraisal does not close that gap.

The regression test binds all six original artifact files by SHA-256 and
asserts these success, failure and qualification boundaries:

```sh
python3 -B -m unittest discover -s scripts -p test_parity_prerequisite_evidence.py
```
