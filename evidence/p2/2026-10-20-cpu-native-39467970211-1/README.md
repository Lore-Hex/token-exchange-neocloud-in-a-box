# Native Botocore Finder Observation

Original [run.json](run.json) and [output.log](output.log) from
[Actions run 39467970211, attempt 1](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39467970211)
are preserved byte-identically. Producer:
`d764993463b6072681227185f4b9a82967178755`.
The CPU-only diagnostic ran October 20, 2026, **17:44:21-17:56:25 UTC**.
No GPUs, model weights, inference, credentials or container network were used.

## Result

The read-only payload was accessible after the native permission fix. All five
application stages completed with the expected before/after inventory hashes.
Application preflight took 17,269 ms, application 671,086 ms and entry 17,557 ms.
These are diagnostic phase timings, not inference latency or throughput.

Engine entry exited 1 at checkpoint 11. The sole state mismatch was `finders`.
The finite diagnostic identified the actual finder sequence as:

```text
builtin, frozen, sealed, botocore-vendored-six
```

All six source checks passed, including the exact Botocore vendored-six source.
The scan was complete with no comparison failure, missing expected finder,
duplicate or changed expected ordering. The pattern adapter was still pending.
This identifies the additional finder in this run; it is not an unrecorded
exception traceback or proof that every earlier GPU failure had the same cause.

The recorder completed, confirmed owned-container cleanup and retained
`entry_pass: false`. The workflow correctly failed its final success gate.
The result supports reviewing an exact-source compatibility adapter, not
allowing arbitrary finders or claiming a working inference service.
Candidate, ABI, GPU, hardware, deployment and confidentiality acceptance remain
blocked. No unchanged retry is implied.

## Original Bindings

| Object | SHA-256 |
| --- | --- |
| `run.json` | `6fd052106f05a170242d0907edf75b38b987a9be2b6984b9c4c6088d1e20684c` |
| `output.log` | `ff86eaa6fa6fa655ef16213abafd14e1d8ef777f612803d9fd6cd246afb5185a` |
| Producer recorder source | `8f73310e082ddd0f7e779f5b9df6ae63b50c143b94f748ad5c6c8877211a8bb5` |
| Reconstructed payload | `030ca2bfda9231eac67c6666e35a6bedeaa95c53b8313888e0f6dd4bf9bed2c8` |

The [offline regression](../../../scripts/test_native_botocore_evidence.py)
reconstructs the producer's historical helpers and payload, checks raw OCI and
validator bindings, and uses that producer's authoritative validator. The
record is valid failure evidence, not a passing engine entry. Fabricated
entry/confidentiality, altered payload and unknown cleanup are rejected.
