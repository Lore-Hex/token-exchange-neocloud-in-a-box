# P2.T3 Read-Only Official Cache Inspection

Status: implemented with synthetic unit tests. No live cache availability,
hardware, throughput or serving result is established by this change.

The official checkpoint can be checked without allocating another full local
copy. `host/inspect-public-weight-cache.py` reuses the hash-pinned public cache
helper's manifest, exact inventory, file identity checks, mount readback and
bounded process supervisor. Each hashed file and its parent must have the
approved open mount's Linux `mnt_id`; nested bind mounts are rejected even
when they contain byte-identical files. See
[proc_pid_fdinfo(5)](https://man7.org/linux/man-pages/man5/proc_pid_fdinfo.5.html).
It only accepts the complete pinned original
83-file pack. Its default mode does not mount or unmount anything. Neither mode
restores, copies, deletes model files, changes permissions or starts inference.

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

## Temporary Private Mount

When no suitable read-only mount already exists, add `--private-mount` and pass
an existing empty, root-owned private directory as `--mount`. The worker uses
`/usr/bin/unshare --mount --propagation private`, verifies that its mount namespace
differs from the supervisor's and that the containing mount has private
propagation, then mounts only the approved export read-only. See
[unshare(1)](https://man7.org/linux/man-pages/man1/unshare.1.html), inspected
2026-10-28. This requires Linux mount-namespace support and root privileges.
Namespace failure is terminal; there is no fallback to a shared host mount.

The same deadline covers mounting, the full hash check and normal unmounting.
Mount or cleanup failures do not produce a successful pack receipt. Cleanup
never uses force or lazy unmount, and refuses a different or writable export.
Mount changes are private to the worker; the server's existing mounts and
running service remain unchanged. The underlying NFS reads still consume host
and network resources, so this does not waive the live-run window/budget gate.
The existing supervisor bounds the process group; uncertain cleanup stays
uncertain and must be investigated before another attempt.

Private mode produces a `nib.public-weight-cache-inspection/v2` receipt with
explicit requested-mode, verified-namespace and completed-unmount fields.
Only an exact successful worker acknowledgment can set the latter two true.
Default mode retains its v1 contract. Neither mode creates a runtime snapshot
or authorizes use of a mounted cloud cache as the live model directory.

## Verification

`python3 -m unittest discover -s scripts -p test_public_weight_cache_inspection.py`
uses only synthetic files and mocked kernel mount readback/supervision. Tests
cover correct hashes, corrupt or unexpected files, symlinks, hardlinks, FIFOs,
mutation during checking, wrong/read-write/nested mounts, timeout, failed cleanup,
late cancellation, private CLI errors, mixed checkpoint acknowledgments, duplicate JSON keys, exact
worker source, durable intent and receipt collisions. This is not a real NFS
hash run or a complete failure-injection test of the existing supervisor.
