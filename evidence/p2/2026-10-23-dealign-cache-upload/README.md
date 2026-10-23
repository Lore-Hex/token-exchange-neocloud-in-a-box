# Original Complete Cache Upload Receipt

[receipt.json](receipt.json) is the unchanged public upload receipt from
`host/cache-public-weights.py` at exact producer
`4578a14bf90574d88515076a625acec813d6712b`. Its 1,566 bytes have SHA-256
`70aa251d3158d4302fcb934532031bcab269f3b572f7aa2cd9a3c0775e0c121f`.
No `run.json`, console output or other execution artifact is reconstructed.
Private exports, endpoints, host access details and local model paths are not
included.

The receipt records a successful full upload from 2026-10-23 11:04:25.312425
to 12:17:27.349410 UTC: **4,382.036985 seconds** within the configured
7,200-second timeout. All **79 files / 510,311,056,155 bytes** match the fixed
`dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8` profile at revision
`c19ca5ca4eaeb79d29fdf08aea8327da95b1116a`, manifest SHA-256
`5c6891a9649702b1ac0327b945600acb40c571a069942517cf14ba661a3cfcbc`.
Both source and target manifest fields bind that same profile. Remaining file
and byte counts are zero, and `pack_complete=true`.

The [pinned helper](../../../host/cache-public-weights.py) hashes the complete
source before copying, hashes every copied stream, and re-reads and hashes the
destination. Its successful worker result follows inventory/identity checks,
syncs and mount readback. The receipt records `publication=confirmed` with
**`exclusive-directory-copy`**, not atomic rename; the publication-helper hash
is null. The destination is visible while incomplete. Its existence alone
would not establish a complete upload.

`cleanup=worker-completed` and `worker_exit_confirmed=true` are preserved.
`process_group_absence_verified=false` and `source_removed=false` remain
unchanged; no unmount, source deletion or process-group disappearance is
inferred. `runtime_ready=false` and `confidential=false` also remain unchanged.

This receipt records the completed cache copy at that time. It does not prove
power-loss durability, continued cache availability, a later restore to a GPU
host, immutable storage, engine execution, tokenizer parity, model quality,
public-service readiness or confidential qualification. Fresh verification is
still required for any later restore or use. The earlier
[partial seed](../2026-10-23-dealign-partial-seed/README.md) and
[complete local stage](../../p0/2026-10-23-dealign-stage-complete/README.md)
remain separate immutable evidence with their original claim boundaries.

The [focused tests](../../../scripts/test_dealign_upload_evidence.py) pin the
original bytes and all four active source/manifest files from the reachable
producer, check the complete-stage identity, and replay the exact historical
parent's metadata checks with synthetic worker output and failure controls.
Those tests perform no NFS mount, model read/copy, child-worker execution or
hardware operation. They do not substitute for the original measured upload.
See the [cache-copy contract](../../../docs/tasks/p2-t4-public-cache-seed.md).
