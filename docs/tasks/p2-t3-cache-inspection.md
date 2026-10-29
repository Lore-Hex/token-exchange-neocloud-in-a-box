# P2.T3 Read-Only Official Cache Inspection

Status: implemented with synthetic unit tests. No live cache availability,
hardware qualification, throughput or serving result is established. An
[actual failed attempt](../../evidence/p2/2026-10-29-original-cache-inspection-failure/README.md)
is preserved without upgrading its unproven cleanup or claiming a root cause.
The [later cancelled attempt](../../evidence/p2/2026-10-29-original-cache-inspection-cancelled/README.md)
also preserves its original unproven cleanup; external operator observations
do not replace either worker receipt.

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

## Bounded Resource Mode

Add `--bounded-resources` to place the worker, mount helper and all descendants
in a fresh cgroup before any mount or model read. This composes with private
mount mode and emits a v3 receipt; v1/v2 defaults remain unchanged. It reuses
the hash-pinned `host/probe-build-resources.py` lifecycle with an 8 GiB memory
cap, zero swap, two CPU equivalents and 256 tasks. These are process/cgroup
limits, not a network-throughput or host-kernel memory limit. See the Linux
[cgroup v2 memory and CPU interfaces](https://docs.kernel.org/6.17/admin-guide/cgroup-v2.html).
The existing root controllers must already be available; the checker never
enables controllers, moves service processes or adopts an existing group.

The child verifies the exact new directory identity, joins the group and reads
back all limits before executing the existing inspector. Cleanup targets that
owned group, including descendants with different sessions/process groups.
Success requires verified empty membership, group removal and zero OOM kills,
as well as the original complete hash/mount acknowledgment. Cleanup or limit
failures cannot preserve a pack pass. The receipt records the pinned resource
helper, observed limits, memory peak and cleanup state, without operator paths.
SIGTERM to the inspector uses its existing cancellation path; the resource
lifecycle then cleans up its group. Unproven cleanup is not permission to
delete, recover or kill any unrelated resource.

No resource limit authorizes a live run by itself. Operators must still check
disk space, health, incremental transfer charges, time limits and competing
work before launching. Bound and monitor host/network pressure separately.

## Durable Ownership Handshake

Bounded mode optionally accepts `--ownership-dir "$NEW_PRIVATE_DIRECTORY"`
and `--attempt-id "$UNIQUE_32_HEX_ATTEMPT"` together. The directory must not
exist and its parent must already be private. The inspector publishes a
complete, fsynced `supervisor.json` before any worker, and `group.json` with
the exact cgroup device/inode and limits before launching a child. Both bind
the attempt, producer, script hash, supervisor PID/start ticks and host boot ID.
Publication uses an exclusive hard link to a completed temporary file, never
replacement or adoption of an existing record. See [link(2)](https://man7.org/linux/man-pages/man2/link.2.html).
A publication failure prevents worker launch and retains group cleanup.

These are private operator recovery records, not benchmark results. Keep them
outside public evidence; the ordinary inspection receipt remains unchanged.
They let an external monitor recover exact ownership after connection loss or
parent exit, including a group that disappeared before its first sample.
The monitor must validate attempt/source/boot identity, use a pidfd for any
supervisor signal, and keep checking the recorded group until cleanup is
proven. Missing records do not authorize name-based adoption, retry or a
success claim. This optional handshake does not supply that external monitor
or establish a completed live inspection.

## Failure Diagnostics

The pinned process capture intentionally discards stderr and raises on a
nonzero exit before returning stdout. Previously, an escaping worker exception
therefore became only `worker-failed`, with no stage. The capture, cache,
profile and cgroup helper pins and lifecycle remain unchanged.

Caught worker exceptions now produce a bounded
`nib.public-weight-cache-inspection-worker-failure/v1` acknowledgment instead
of a traceback. This internal failure acknowledgment exits zero solely to
transport diagnostics through the existing capture. The supervisor validates
the exact keys and closed label sets and still exits nonzero with a failed
inspection receipt. Only the existing exact success acknowledgment can pass;
successful v1/v2/v3 receipts remain unchanged.

On an accepted failure acknowledgment, the receipt adds `worker_failure` with
only `stage`, `error` and nullable `cleanup_error`. Stages distinguish worker
preflight, pinned helper/manifest loading, namespace, target and propagation
checks, private mount, mount readback/identity, pack opening, inventory before
and after hashing, hashing, final mount readback, deadline and private cleanup.
An otherwise unclassified worker exception uses `worker`. Errors are only
`not-found`, `permission-denied`, `os-error`, `command-failed`, `command-timeout`,
`rejected` or `unexpected-error`. These are exception categories, not proven
root causes. No raw exception, command, return body, file name, export, path,
host identifier or traceback is retained, including on error paths.

If private cleanup also fails, the original stage/error is retained and
`cleanup_error` records only the cleanup error category. A private-cleanup-only
failure uses stage `private-cleanup`. Validated worker metadata also survives
a later cgroup cleanup/limit rejection, OOM result or supervisor cancellation;
it never overrides those failures. Every failure still claims zero verified
files/bytes, no pack pass, no worker-exit confirmation and `cleanup: unproven`.
The existing private mount and process-absence flags remain conservative.

No acknowledgment is guaranteed: interpreter/bootstrap, cgroup join, exec or
unshare failures before the worker, timeout, abrupt exit, output-limit or
capture-cleanup failure may leave no worker metadata. Absence is **unknown**,
not evidence that a particular stage succeeded. There is no retry, fallback,
new mount behavior or new remote diagnostic command.

## Verification

`python3 -m unittest discover -s scripts -p test_public_weight_cache_inspection.py`
uses only synthetic files and mocked kernel mount readback/supervision. Tests
cover correct hashes, corrupt or unexpected files, symlinks, hardlinks, FIFOs,
mutation during checking, wrong/read-write/nested mounts, timeout, failed cleanup,
late cancellation, private CLI errors, mixed checkpoint acknowledgments, duplicate JSON keys, exact
worker source, durable intent and receipt collisions. This is not a real NFS
hash run or a complete failure-injection test of the existing supervisor.
Additional cases cover closed failure acknowledgments, private primary plus
cleanup failure, cgroup cleanup/OOM/cancellation precedence, privacy canaries,
unchanged original evidence bytes, and a real subprocess failure acknowledgment
through the pinned capture with invalid worker preflight and no mount or read.
