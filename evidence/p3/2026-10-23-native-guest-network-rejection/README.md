# Native Guest-Network Composition Rejected

Original [workflow run 39891331417](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39891331417)
used exact committed producer `942dba9d38b8a19bf6c3ab6b9a3a7de44fcb3b0f`, with
`debug_boot=true` and `guest_network=true`. This was one authorized dispatch
after the exact main full/catalog checks and fresh head/active-run checks passed.
No retry was made. The job ran 2026-10-23T02:45:45Z through 02:51:45Z.

Authenticated public input fetch passed. Fresh native export failed during
02:48:03Z-02:51:41Z; debug image assembly and QEMU were skipped. The unused-export
cleanup step and its fixed metadata upload passed. This is a failed image build,
not a boot result.

## Original Bytes

Artifact `12475450719`, `unused-debug-export-39891331417-1`, is a 3,999-byte ZIP
with exactly one regular mode-0600 member, `validation.json` (8,938 bytes).
Its SHA-256 matches the original GitHub artifact digest:
`53d6b1127b5e5529f6489a4e207f31e87047bd06feb264ac96449cadda1d84c3`.
The private original archive remains outside the repository at
`/private/tmp/nib-debug-network-39891331417-original.zip`.

- `validation.json`: original extracted bytes; SHA-256
  `9bb47760989c04bb3a0c855d52dd254b45fa480d518a7af4fe329872072fb6f5`.
- `artifact-metadata.json`: original structured GitHub artifact API response;
  SHA-256 `2ce804cebfbd306f79ee5b1ad2dc8859496bbcbcd2a27123e8507f810a88c8bc`.
- `workflow-run.json`: original structured `gh run view --json` response;
  SHA-256 `acda954f3b609ebd6e8a6a652202b92793256725cfaa9cc88fca92763f717e75`.
- `output.log`: byte-identical duplicate of `validation.json`, not a console
  transcript. No raw installer, serial, journal, network or exception text.

This workflow branch did not upload a standard native `run.json`; none is
fabricated. The source-bound committed validator emitted the original JSON,
including 68 native source hashes and two validator/workflow hashes. Tests
recompute all of them from the exact reachable producer above.

## Observations And Limits

The fixed projection records installation `process-exit`, worker
`guest-network/rejected`, 212 configured packages and installer exit status 0.
The child exited 1. Resource readback succeeded, peak memory was 5,024,845,824
bytes, OOM kills were 0, and the populated count before cleanup was 0. Payload
removal, process cleanup and private-image deletion are confirmed. Overall
result remains `fail`; artifact, boot, confidentiality, reproducibility and
signing qualifications remain false.

The original installation report was not uploaded. Its recorded identity is
9,138 bytes, SHA-256
`6d61e30ed180a77a5cd55f0e2bc963cee38a5180f2d1f23422b926efd8ea18f2`.
The projection does not retain failure frames, actual guest-directory modes,
or the precise rejected check. OOM0 is not a diagnosis of that check.

The subsequent [source-only skeleton-mode reproduction](../../../docs/tasks/p3-t1-image-skeleton-modes.md)
establishes a composition defect consistent with this rejection. It does not
establish the unique remote cause or retroactively add observations to these
originals. No corrected native run, boot, TDX/GPU qualification, GCP support,
full guest-service readiness or reproducibility result is claimed.
