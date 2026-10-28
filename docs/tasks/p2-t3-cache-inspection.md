# P2.T3 Read-Only Official Cache Inspection

Status: implemented with synthetic unit tests. No live cache availability,
hardware, throughput or serving result is established by this change.

The official checkpoint can be checked without allocating another full local
copy. `host/inspect-public-weight-cache.py` reuses the hash-pinned public cache
helper's manifest, exact inventory, stable file hashing, mount readback and
bounded process supervisor. It only accepts the complete pinned original
83-file pack. It does not mount, unmount, restore, copy, delete, change
permissions or start inference.

An operator must separately arrange and approve a **read-only NFS mount** of
the public cache on a suitable host. The kernel readback must match the exact
export and mountpoint, report `ro` without `rw`, and include
`nodev,nosuid,noexec`. The pack directory is `deepseek-v4.1-flash-1017`
immediately below that mount. No source URI, host address or filesystem path
is included in the receipt.

```sh
python3 -I -S -B host/inspect-public-weight-cache.py \
  --export "$APPROVED_PUBLIC_NFS_EXPORT" \
  --mount "$APPROVED_READONLY_MOUNT" \
  --output "$PRIVATE_RECEIPT_DIRECTORY/inspection.json" \
  --producer "$EXACT_REVIEWED_GIT_SHA" --timeout 3600
```

Run on Linux as root, from the reviewed source tree, with a private existing
receipt directory. This command is an example, not authorization for a live
run. Hashing the whole pack is substantial I/O: approve its host, window and
budget without disrupting the serving node. The limit is 61-7200 seconds;
60 seconds is reserved outside the worker budget. The exclusive, fsynced
receipt is written before the worker starts. Existing receipts are never
replaced. Failed or interrupted attempts remain failures, with zero claimed
verified bytes. Timeout and unproven cleanup are not retried automatically.

A successful `nib.public-weight-cache-inspection/v1` receipt binds the full
model profile, manifest, producer and helper hashes to the completed check.
It means all expected bytes were read and matched during that inspection.
It is not a trusted immutable snapshot: an ordinary host or storage operator
can subsequently change the files. The receipt explicitly leaves immutability,
runtime readiness, confidentiality and process-group absence unverified.
Restore, independent local rehashing, image admission, drained model-ID cutover,
fresh conformance/quality/cache tests and rollback gates remain separate.

## Verification

`python3 -m unittest discover -s scripts -p test_public_weight_cache_inspection.py`
uses only synthetic files and mocked kernel mount readback/supervision. Tests
cover correct hashes, corrupt or unexpected files, symlinks, hardlinks, FIFOs,
mutation during checking, wrong/read-write mounts, timeout, failed cleanup,
cancellation, mixed checkpoint acknowledgments, duplicate JSON keys, exact
worker source, durable intent and receipt collisions. This is not a real NFS
hash run or a complete failure-injection test of the existing supervisor.
