# Original Partial Seed Receipt

[receipt.json](receipt.json) is the unchanged public receipt from
`host/cache-public-weights.py` at producer
`4578a14bf90574d88515076a625acec813d6712b`. Its 1,642 bytes have SHA-256
`f0d7eb7839fbbc2e3290219c78baf3f66380df053168fc6a88c55b1e370f0145`.
Only that original artifact was supplied for this evidence update. No console
log or repository `run.json` envelope is reconstructed or invented. Private
paths, mount exports and host access details are not included.

The receipt records 2026-10-23 08:43:23.196273 through 09:23:43.984062 UTC:
2,420.787789 seconds within the configured 7,200-second timeout. The fixed
original-to-uncensored seed completed verification and regular copying of
**48 files / 310,737,990,397 bytes**, including 21 identical weight shards.
The producer checks the original 83-file inventory and hashes only the selected
48 files against the target manifest before copying and rehashing them. It does
not claim a fresh hash of all original-cache bytes.

Publication is `confirmed` with `atomic-no-replace-directory`. The supervised
worker completed and its exit was confirmed. The original values
`process_group_absence_verified=false` and `source_removed=false` are preserved;
neither process-group disappearance nor unmount/removal is inferred.

This is a **partial seed**, not a complete model pack. There are still
**31 files / 199,573,065,758 bytes** absent from the 79-file target manifest.
`pack_complete`, `runtime_ready` and `confidential` remain false. This receipt
does not prove a later HTTP stage, complete-pack validation, new-cache upload,
restore, engine/GPU execution, model quality, public-service readiness or
confidential qualification. Later staging activity is outside this evidence.

The [focused tests](../../../scripts/test_dealign_seed_evidence.py) preserve the
original bytes, compare all six source/manifest pins to the reachable producer,
recompute the subset, and replay the original parent's metadata checks with
synthetic worker output. Those replay tests perform no mount, model copy or
hardware operation. See the [cache-seed contract](../../../docs/tasks/p2-t4-public-cache-seed.md).
