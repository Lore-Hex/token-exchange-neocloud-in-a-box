# Private Storage Metadata Preflight

`host/inspect-storage-metadata.py` observes one operator-selected NFS export
through a temporary private read-only mount. This is a narrow software helper,
not storage admission, a copy operation, or confidential-computing evidence.
The existing full-pack inspector and all its default receipt semantics are
unchanged. No existing helper source or transitive pin is modified.
The pinned lifecycle comes from the [inspector](../../host/inspect-public-weight-cache.py),
[cache helper](../../host/cache-public-weights.py),
[resource helper](../../host/probe-build-resources.py) and
[capture supervisor](../../host/probe.py).

## Scope and Bounds

- Linux root and Python `-I -S -B` are required. The operator supplies the exact
  committed producer, export, fresh attempt ID, empty private mount target,
  private output parent, and fresh ownership-directory path. No names are
  discovered or adopted. Paths are absolute, disjoint and nonsymlinked by the
  pinned directory walker; the mount target already exists with mode `0700`.
- The worker uses `unshare --mount --propagation private`, checks that its mount
  namespace differs from the supervisor, and checks private propagation before
  attaching anything. It never remounts an existing host mount. Mounting still
  contacts NFS and changes the worker's mount namespace: execution requires its
  own operator approval and admission; this is not a network-free probe.
- Ownership is published before work using the unchanged inspector's
  supervisor identity and cgroup device/inode records. The existing resource
  helper applies its unchanged ceilings: two CPU cores, 8 GiB memory, no swap,
  and 256 PIDs. These are ceilings, not allocated capacity or predicted usage.
- The captured worker has a fixed 120-second bound and a 60-second cleanup
  reserve. Existing mount commands retain their 45-second command limits.
  Findmnt readbacks are accepted only below 8 KiB (propagation below 4 KiB),
  with one exact row, bounded options, and a worker acknowledgment below 4 KiB.
  Commands remain inside the owned resource boundary. No arbitrary commands,
  profiles, resource overrides, timeout extensions or retry mode are exposed.
- Reads are limited to mount metadata and the mounted root directory's
  descriptor identity, ownership/mode and filesystem availability counters.
  No model inventory, file hashes, content reads, copy, write-capability probe,
  quota request, Docker operation or service action is performed.

The private output contains the supplied export/target and observed mount
options, which can identify infrastructure. It is created exclusively with
mode `0600`, fsynced with its parent before ownership/launch, and retains a
failed record if later work fails. Do not publish this receipt as public
acceptance evidence. Exceptions expose fixed categories, not raw output.

## Receipt Meaning

Schema `nib.storage-metadata-observation/v1` binds the producer, executing source
hash, helper hashes and attempt. A pass means the bounded metadata observation
and explicit private-mount cleanup completed, followed by the pinned cgroup
cleanup checks. It is not permission to stage, load an image or start a model.

The observation binds the open descriptor's mount ID to the exact NFS
source/target, `ro,nodev,nosuid,noexec` options, directory device/inode/UID/GID/mode,
and `f_bavail * f_frsize` / `f_favail` counters. Descriptor, pathname and mount
readbacks are compared again. Distinct mount IDs or devices do not establish
distinct server backing. Available bytes/inodes are one observation, not a
reservation, effective quota, writeability test or guarantee against a later
consumer. Some filesystems cannot report usable inode availability; invalid
or negative counters fail rather than becoming a successful capacity claim.

`quota`, `backing` and `all_client_aliases` remain `unknown` on every result;
`write_capability` remains `unverified`. Admission, pack verification, runtime
readiness and confidentiality remain false. `host_mount_mutation_requested`
describes the requested operation, not an independent host-wide audit.
`process_group_absence_verified` remains false, distinct from the explicit
owned-cgroup cleanup fields. Other clients, mount namespaces and descriptors
are not enumerated by this helper.

Cleanup only unmounts the bound identity after exact readback and checks
unmounted state. It never uses force/lazy unmount. A failed mount/readback
without a bound identity, replacement mount, busy unmount, lost acknowledgment
or timeout cannot imply cleanup or absence. Uninterruptible NFS work can
outlive a client deadline. An independently reviewed controller must continue
accounting for the exact supervisor, capture helper, descendants and cgroup
until owned absence is observed, including after parent exit. Existing
inspection/staging controllers do not automatically recognize this new CLI.
Never cancel/adopt a process or group by name alone, or retry an uncertain run.

## Operator Integration

After separate review and authorization, the invocation shape is:

```sh
python3 -I -S -B host/inspect-storage-metadata.py \
  --export "$APPROVED_EXPORT" --mount "$FRESH_PRIVATE_TARGET" \
  --output "$FRESH_PRIVATE_RECEIPT" --ownership-dir "$FRESH_OWNERSHIP_DIR" \
  --attempt-id "$FRESH_ATTEMPT_UUID_HEX" --producer "$EXACT_COMMITTED_PRODUCER"
```

Before copying, the operator still needs authoritative volume/export/backing
and quota joins, current authenticated visibility/ownership for every permitted
client, writable-alias exclusion, sufficient bytes/inodes plus the copy's
reserve, a fresh no-overlap/pressure admission, and an approved duration/cost.
An inability to inspect another retained client remains unknown. This helper
neither recovers SSH credentials nor changes provider access or billing.

## Verification Status

The focused fake suite is `scripts/test_storage_metadata_inspection.py`.
It mocks mount commands, namespace/process identity, filesystem counters and
the bounded worker; only temporary local fixture files are used.

On 2026-10-29 the parent reported 22 focused tests PASS in 0.175 seconds at
`2f3a99d5eccc1a2ef584e581445f0d51c0248acd`, using the pinned Go 1.27.2 test
image with networking disabled, a read-only source mount, UID 501, two CPUs
and 1 GiB memory. The parent also reported a CLEAN static review limited to
the metadata-only software scope. These results are not full CI, a review of
subsequent integration changes, or permission for a live operation. Final-head
review, documentation checks and full CI remain pending. No host tests, real
mounts, remote probes or hardware validation were performed for this change.
The suite can be selected in the repository's approved offline test container:

```sh
python3 -B -m unittest discover -s scripts -p test_storage_metadata_inspection.py
```
