# Original checkpoint NFS staging

Status: software support and synthetic tests only. No NFS copy, host-visible
mount, inference or hardware result is claimed. This is separate from the
[read-only inspection](p2-t3-cache-inspection.md) and the existing
[selected view preparer](p2-t4-selected-weight-view.md); neither is changed.

## Explicit boundary

[The sidecar](../../host/stage-original-weight-view.py) accepts only explicit
`--mode nfs-exclusive-copy`, with the closed `original` profile. It imports
hash-pinned existing helpers for manifest parsing, streaming copy, source
identity checks, read-only view modes, immutable ownership records, subprocess
capture and cgroup cleanup. There is no arbitrary model, manifest, command,
resource limit, backend selection, retry, automatic fallback or adoption.

The source is mounted read-only and the destination read-write, both in a new
private mount namespace with `nodev,nosuid,noexec`. Both targets must already
be empty, private, unmounted directories. Readback binds each open mount
descriptor to the exact export, target, filesystem type and options. Different
export strings are insufficient: mount IDs and filesystem devices must also
differ. The same observed filesystem is rejected; different device numbers
do not prove distinct backing storage across server/export aliases. Independently
prove separate backing storage and exclude writable source aliases before use.

The unchanged inspector/resource helpers establish an exclusive owned cgroup
and publish complete attempt/source-bound supervisor and group records before
capture. The worker joins and reads back limits **before** starting `unshare`
or mounting: two CPU cores, 8 GiB memory, zero swap, 256 tasks, group OOM
handling. Parent controllers and existing processes are never changed. Cleanup
uses the existing capture and cgroup lifecycle, including detached descendants.

Ownership and receipt parents must be on an explicitly local disk filesystem
(`ext4`, `xfs` or `btrfs`); NFS, RAM filesystems and unknown types are rejected.
Before capture, a separate immutable `staging-intent.json` binds the planned
wrapper to the attempt, producer, script, original profile/manifest, digests of
both exact exports and local mount targets, and fixed relative source/model
paths. It uses the existing pinned no-replace publication primitive on local
disk only. No NFS data is read or written to establish this record.

After exclusive wrapper creation and fsync, but before mode probes or copying,
the worker publishes `destination-created.json` on that same local disk. It
binds the intent digest to the new wrapper's device/inode. Failure to publish
stops the copy and preserves the empty wrapper as unknown. A later failed copy
retains this local identity evidence; the record never authorizes deletion,
adoption, or retry. A crash before publication remains an explicit evidence gap.

After exact worker acknowledgment and cgroup cleanup, `completion.json` binds
that intent digest to the verified destination wrapper/model device and inode,
the creation-record digest, final verified inventory digest, worker acknowledgment
and resource evidence. The parent checks the creation record against the final
wrapper identity before accepting completion.
The receipt includes the SHA256 of each canonical immutable record. Publication
failure never produces a successful receipt. An intent proves a planned attempt,
not ownership of a directory that already existed; a completion record alone
also cannot override a failed, missing or cancelled final receipt. Never infer
adoption, deletion permission or retry permission from directory existence.

## Destination admission

The destination export root must already permit the unchanged trusted-directory
policy: operator ownership, no symlinks or group/world-writable ancestors. The
sidecar never relaxes existing permissions. Root squashing, inaccessible private
parents, wrong ownership or missing capabilities fail closed. These remain
deployment blockers until independently observed on the intended existing NFS.

Available capacity must cover the original manifest's 510,311,045,295 bytes
plus the greater of 10 GiB or 10% rounded up: **561,342,149,825 bytes total**.
This is a filesystem admission threshold, not a quota reservation. Concurrent
writers can consume capacity afterward; a later write failure leaves a failed
partial copy, never a retry or an inferred success.

The pinned filesystem probe exercises private creation, exclusive hardlinks,
lock contention and file/directory fsync. A tiny owned mode probe additionally
exercises exact `0444` file and `0555` directory modes before weight copying.
Only successful capability probes are removed. Failed probes remain evidence.

The worker exclusively creates `.nib-original-view-<attempt>` as a `0700`
wrapper directly under the destination export. An existing name is always a
failure, even if empty or apparently complete. Only the wrapper's new `model`
child receives copied model files. Source data, old views and unrelated
destination entries are not chmodded, overwritten, moved or removed.

## Copy and completion

The unchanged `cache.upload_copy` implements explicit exclusive-directory copy,
not atomic rename. This follows that helper's existing NFS contract; unsupported
no-replace rename is not handled by silently switching publication modes.
There is one copy invocation using bounded streaming buffers, with no third
full RAM copy. Existing helper checks hash source bytes before writing the
model, verify bytes during copying, rehash the destination, and check stable
source identities. This entails multiple read passes, not multiple copies.
The approved time/cost envelope must include the full source hash pass, the
copy pass, both complete destination rehashes, admission and cleanup. An estimate
covering only write throughput is insufficient. Hard-NFS uncertainty can outlive
the requested cancellation time; monitoring and any continuing host cost must
remain owned until absence or an explicit unresolved-state handoff.

Only newly copied model files/directories become `0444`/`0555`. The complete
destination is rehashed again after mode preparation, with file descriptors
bound to the destination mount. Source inventory/identity, wrapper ownership,
both mount identities, deadlines and final modes are checked again. CPU/memory
caps do **not** impose a network bandwidth limit: separately approved quiet
admission and continuous service-pressure monitoring remain required.

Success requires the exact closed worker acknowledgment, normal owned cleanup
of both private mounts, completed capture, exact resource readback, empty and
removed cgroup, zero OOM kills, and no cancellation. Busy/unknown mounts never
receive forced or lazy unmount. Both cleanups are attempted even if one fails.
The timeout is 61 through 7200 seconds, with 60 seconds reserved from the worker
budget for the inherited cleanup path; it is not a guarantee that blocked NFS
tasks terminate. External monitoring and an explicit unresolved-state handoff
are still required if termination or mount cleanup is uncertain.

Failed or unacknowledged copies remain private and unproven, including a fully
written directory after a lost acknowledgment. The sidecar does not remove,
resume or adopt them. Receipts contain fixed failure stage/error labels rather
than raw exceptions, paths, bodies or mount exports. Known worker diagnostics
survive later resource-cleanup failure. Missing acknowledgments remain unknown.
Ownership records are private operator artifacts, not public telemetry.
Unmounting the worker's private mounts does not clear persistent NFS data:
partial and completed wrappers remain. A later read-only handoff must freshly
reopen the exact independently bound destination and rehash its complete
manifest. Neither old digests nor path existence authorize serving it.

## Operator interface

This interface is documentation, not execution approval. Require an independent
review of the exact producer, all CI green, a successful original read-only
scan, an independently approved storage/time budget, fresh admission and an
external monitor. Never overlap an inspection, copy, model load or serving
pressure. Use a fresh exclusive attempt and evidence paths; preserve failed
attempts and the exact producing Git ancestry.

```sh
python3 -I -S -B host/stage-original-weight-view.py \
  --mode nfs-exclusive-copy \
  --source-export "$APPROVED_ORIGINAL_EXPORT" \
  --destination-export "$APPROVED_DISTINCT_STAGING_EXPORT" \
  --source-mount "$EMPTY_PRIVATE_SOURCE_TARGET" \
  --destination-mount "$EMPTY_PRIVATE_DESTINATION_TARGET" \
  --ownership-dir "$NEW_PRIVATE_OWNERSHIP_DIRECTORY" \
  --attempt "$FRESH_ATTEMPT_HEX32" --producer "$REVIEWED_COMMIT" \
  --output "$NEW_PRIVATE_RECEIPT" --timeout "$APPROVED_SECONDS"
```

The inherited cgroup helper requires a root Linux x86-64 host with writable
cgroup v2 and already-enabled CPU, memory and PID controllers. Required kernel,
NFS, mount-namespace and directory capabilities are gates, not assumed facts.

**Host-visible mount remains a separate blocker.** The temporary destination
mount is gone on success; its private-namespace path is not a Docker bind
source. A later reviewed and authorized operation must expose only the complete
`<destination export>/.nib-original-view-<attempt>/model` read-only at a stable
host path, then independently validate the pinned manifest before serving.
No host-visible mount, service replacement, permission change to old parents,
runtime readiness, immutability, attestation or confidentiality is claimed here.

That later operation needs its own explicit lifecycle, not a process-name or
Docker-daemon-identity shortcut. Bind ownership to the host boot ID, actual
daemon/consumer mount namespace, mount ID, exact source and target; read them
back before use and before ordinary unmount. Prove the **inner model** readable
as UID 65532 in the actual consumer namespace without chmodding the private
wrapper or traversing `/proc/<pid>/root` as an assumed serving contract.
Retain that owned mount until all its consumers are gone; never adopt or detach
an existing foreign mount, or use lazy/forced unmount. These controls are not
implemented by this staging sidecar.

Read-only is not immutable. Exclude every writable alias and concurrent writer
for the full verification/consumer lifetime; ordinary privileged host/storage
administrators remain trusted. Coordinated replacement and rollback require
separate approval, drain and conformance checks. Rollback is valid only while
the old view's original owner/mount lifetime and integrity remain valid. No
existing inspection-specific private controller is implicitly compatible with
this new staging receipt or ownership extension.

## Verification

[Focused tests](../../scripts/test_original_weight_staging.py) use tiny synthetic
files, fake mount/cgroup calls and the real pinned copy/mode helpers. They cover
source and old-view preservation, exclusive creation, permission/capacity and
capability rejection, source corruption, partial preservation, post-mode
corruption, private mount identity/cleanup, exact ownership ordering, missing
or mismatched creation/intent records, resource failures, cancellation and
closed acknowledgments. They perform no privileged
mount, NFS access, model copy, remote call or inference.

Run tests only in an isolated CPU container with read-only source, no network,
bounded memory/PIDs/CPU/time and no Docker socket or GPU. Existing inspection,
view, public-cache, resource and evidence-history suites must remain unchanged.
