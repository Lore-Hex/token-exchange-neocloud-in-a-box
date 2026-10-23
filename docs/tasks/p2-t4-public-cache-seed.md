# P2.T4 fixed checkpoint cache seed

The existing [public cache helper](p2-t3-public-weight-cache.md) has an explicit
`--model-profile uncensored` path for full upload, full restore, and one fixed
seed direction: original DeepSeek V4.1 Flash 1017 to the pinned dealign variant.
Omitting the profile retains the original upload/restore/unmount behavior and
manifest pins. No staging downloader, deadline, byte budget or capacity rule is
changed. There is no direct HTTP staging onto NFS.

## Historical cache and current verification

The original cache upload receipt from producer
`9778738405e184ac277b2f5d78e5a7820a71cb0a` recorded 83 files and
510,311,045,295 bytes on October 21. The committed
[October 22 restore receipt](../../evidence/p2/2026-10-22-replacement-cache-restore/receipt.json)
from `a6c9d95a8c1286c0751d974003bddd33a0d1650b` records successful source hashing,
copying and destination hashing. Its narrower cleanup claims remain unchanged.
Neither receipt proves the cache is still present or correct today.

Both [original manifest](../../models/deepseek-v4.1-flash-1017/1/files.sha256)
and [target manifest](../../models/deepseek-v4.1-flash-1017-uncensored-fp8/1/files.sha256)
are exact SHA-256 pins selected by the unchanged
[shared profile helper](../../scripts/deepseek_model_profile.py).
The intersection requires the same relative path, byte length and SHA-256:

| Subset | Files | Bytes |
| --- | ---: | ---: |
| Identical weight shards | 21 | 310,723,700,832 |
| Identical non-weight files | 27 | 14,289,565 |
| Complete seed | 48 | 310,737,990,397 |
| Remaining target files | 31 | 199,573,065,758 |
| Complete target pack | 79 | 510,311,056,155 |

These are manifest arithmetic, not a new copy measurement. The remaining files
are 27 changed shards and four non-weight files. No filename-only matching,
cross-path deduplication, hardlinks, symlinks or caller-selected subset is allowed.

## Copy and publication boundary

The old cache mounts read-only for seeding. Its complete inventory must match
the old 83-file manifest; all entries must be regular single-link files with
exact sizes. Only the 48 selected files are freshly hashed, each against its
**target** manifest entry. The seed makes no fresh integrity claim for the
unselected original bytes. Every copied stream is hashed again, and every
destination is independently re-read and hashed. Source identities and the
verified destination identities are checked before publication.

The helper reuses its existing copy loop, no-follow traversal and hash-bound
`host/probe.py` supervisor. Local seed/restore copies go to an exclusive private sibling directory;
files are 0600 and directories 0700. After all checks and directory syncs, the
existing pinned `scripts/pilot/export.py` exclusive-rename primitive publishes
the directory without replacing even an empty existing destination. A tiny
owned-directory preflight checks both success and conflict behavior of that
primitive on the destination filesystem before copying. Unsupported filesystem
behavior fails closed, with no ordinary-rename or hardlink fallback.

Full upload deliberately uses a different, non-atomic publication mode:
`exclusive-directory-copy`. Linux v6.8
[`nfs_rename`](https://github.com/torvalds/linux/blob/v6.8/fs/nfs/dir.c#L2485-L2496)
rejects nonzero rename flags, so no-replace rename is not assumed on NFS.
The existing copy helper creates the final directory exclusively (0700), opens
every new file with `O_EXCL` (0600), and never replaces an existing directory or
file. Only full hash/copy/hash, identity/inventory checks, syncs and mount
readback can produce a successful complete-copy receipt. The directory is
visible while incomplete; existence alone is never completion. No rename,
hardlink or weaker atomicity claim is used for upload. A failure leaves the
destination and a failed receipt; it is not automatically resumed or removed.

Unknown files, partials, changed identities, corrupt bytes and existing
destinations are rejected. Failure may leave an owned private temporary tree
or probe; no recursive cleanup, automatic adoption or overwrite is performed.
Failure after publication or sync still cannot produce a successful receipt.

All new seed/upload/restore filesystem and NFS work, including mount/readback,
capacity, hash, copy, sync and publication, runs inside the supervised child.
The parent stays on local storage and reserves a 0600 failure receipt before
starting that child. Its deadline is at most 7200 seconds, with the existing
60-second reserve. The existing supervisor owns process-group termination and
bounded reaping. A timeout or kernel-stuck worker remains failed with cleanup
unproven; process-group absence is never inferred. No NFS soft-mount option,
dependency installation, settings change or new download is introduced.

## Approved operation sequence

Run only after explicit approval, from the reviewed source-bound public package
on local storage. Keep the package and receipt outside both mounted caches and
the local model directory. Mount directories and copy destinations must be
fresh; the receipt's parent must be owner-only (0700). NFS client tools must
already exist. These commands do not authorize resource or host changes.
`PACKAGE` and all path-valued variables below must be canonical absolute paths;
`PACKAGE` is the verified public helper package root on local storage.

```sh
python3 -I -S -B "$PACKAGE/host/cache-public-weights.py" \
  --model-profile uncensored --export "$OLD_APPROVED_EXPORT" \
  --mount "$FRESH_OLD_CACHE_MOUNT" --seed-to "$FRESH_LOCAL_PACK" \
  --manifest "$PACKAGE/models/deepseek-v4.1-flash-1017-uncensored-fp8/1/files.sha256" \
  --seed-manifest "$PACKAGE/models/deepseek-v4.1-flash-1017/1/files.sha256" \
  --producer "$REVIEWED_COMMIT" --timeout 7200 --output "$NEW_SEED_RECEIPT"
```

A successful seed receipt has `pack_complete=false`, 48 verified files,
310,737,990,397 verified bytes and 31 remaining files. It is not accepted as a
complete pack by the existing stage/runtime verifiers. The separately approved
unchanged `record-stage.py` bundle then rehashes those complete files and
downloads only the missing target entries into **local** storage. It retains
its full 561,342,161,771-byte free-space check and 510,311,056,234-byte HTTP-body
budget; the seed does not bypass either gate. No partial-file resumption is added.

After full staging succeeds, use the same helper with `--model-profile
uncensored --source "$COMPLETE_LOCAL_PACK"`, the new approved export, a fresh
mount directory, the target manifest, producer, timeout and a new local receipt.
It verifies all 79 files and records the completed, non-atomic exclusive copy at the fixed
`deepseek-v4.1-flash-1017-uncensored-fp8` directory on the new cache.
Later, `--restore-to "$FRESH_LOCAL_PACK"` instead of `--source` mounts that cache
read-only and performs full verified regular copying. Neither full operation
accepts `--seed-manifest`. Existing explicit unmount remains separate; the helper
does not detach/delete a volume or remove source files.

The public helper package needs only the committed paths
`host/cache-public-weights.py`, `host/probe.py`,
`scripts/deepseek_model_profile.py`, `scripts/pilot/export.py`, and both pinned
`files.sha256` manifests, preserving their repository-relative layout. Package
preparation and verification follow final independent review and all test/CI
gates. No credentials, exports, local paths or private model content belong in it.

## Claims and checks

New receipts bind the exact script and helper hashes, producer, both manifest
identities, operation, publication mode, counts and publication status. Successful full copies can
say `pack_complete=true`; seed receipts cannot. All remain `runtime_ready=false`,
`confidential=false`, `source_removed=false` and
`process_group_absence_verified=false`. This is not power-loss durability,
immutable storage, model quality, runtime readiness or confidential qualification.

Focused tests use tiny synthetic files, pinned public metadata, real local
exclusive rename and real bounded child timeout. Worker mount routing is tested
with fakes, not a measured NFS copy:

```sh
python3 -I -S -B -m unittest discover -s scripts -p 'test*cache*.py'
```

## Actual Partial Seed

The [original October 23 receipt](../../evidence/p2/2026-10-23-dealign-partial-seed/receipt.json)
from exact producer `4578a14bf90574d88515076a625acec813d6712b` records a
successful 48-file, 310,737,990,397-byte seed from 08:43:23.196273 to
09:23:43.984062 UTC. Publication was confirmed with the local atomic
no-replace mode; the worker completed and its exit was confirmed.
`process_group_absence_verified=false` and `source_removed=false` remain unchanged.

This result leaves 31 target files / 199,573,065,758 bytes missing.
`pack_complete=false`, `runtime_ready=false` and `confidential=false` are
preserved. It is not a complete-pack, later HTTP-stage, new-cache upload,
restore or engine/GPU result. The [evidence notes](../../evidence/p2/2026-10-23-dealign-partial-seed/README.md)
separate the original receipt from offline source-binding and metadata-replay
tests; no new copy is performed by those tests.
