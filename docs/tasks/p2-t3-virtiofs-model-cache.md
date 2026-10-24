# Explicit virtiofs cache admission

Status: implemented with fake mount readbacks and local primitive tests only.
No ES volume, hardware, model contents, persistence across restart, inference,
capacity or confidential qualification has been validated by this change.

## Sources and scope

Read 2026-10-23:

- [Spheron ES mounting guide](https://docs.spheron.ai/connecting/volume-mounting/spheron-es),
  Overview, Mount the filesystem and Find the mount tag: ES uses an attached
  virtiofs device and the exact dashboard tag. No formatting is involved.
  Same-region attachment is required; hot attachment/detachment restarts the VM.
- [mount(8)](https://man7.org/linux/man-pages/man8/mount.8.html),
  filesystem-independent options: `nodev,nosuid,noexec`, plus `ro` for readers.
  NFS's `nconnect=16` is not used for virtiofs.
- [findmnt(8)](https://man7.org/linux/man-pages/man8/findmnt.8.html), Options:
  `--mountpoint` checks the mount root; `--target` resolves a child to its
  containing filesystem. Both use `--json` with explicit `--output` columns.
- [flock(2)](https://man7.org/linux/man-pages/man2/flock.2.html), Description:
  independently opened descriptors contend, including within one process.
  `LOCK_EX|LOCK_NB` must fail with `EWOULDBLOCK` while another open holds the lock.
- [link(2)](https://man7.org/linux/man-pages/man2/link.2.html) and
  [fsync(2)](https://man7.org/linux/man-pages/man2/fsync.2.html): existing link
  destinations are not replaced; directory entries need a separate directory sync.

The existing [cache wrapper](../../host/cache-public-weights.py) defaults to
NFS, with its original commands and receipt fields. `--backend virtiofs` is
explicit and uses `--export` as a mount tag, not a hostname/export path.
It supports the uncensored profile's existing upload/restore/seed copy paths,
unmount, and the new bounded `--mount-only` admission mode. The original
checkpoint's legacy copy routes remain NFS-only. No full-copy logic is duplicated.

## Bounded admission

After separate volume, attachment and host/run authorization, prepare private
operator inputs. The mount target must be empty, unmounted, canonical and owned
by the executing root user with mode `0700`. Its ancestors must belong to root
or the executing user and not be group/world writable. Only root-owned sticky
`/tmp`, `/private/tmp` and `/var/tmp` ancestors are exempt from the writable-mode
rule; symlinks are never followed. The mounted volume root must also be owned
by the executing user and not group/world writable. Permissions are checked,
not automatically changed. Do not use a world-writable volume root.

`STAGING_PARENT` must be one direct child of `MOUNT`. Admission creates it with
mode `0700` or validates the existing private directory without removing its
contents. `MANIFEST` and the new `RECEIPT` must be outside the mount and disjoint;
the receipt's existing parent must be owned and mode `0700`.

```sh
sudo python3 -I -S -B host/cache-public-weights.py \
  --backend virtiofs --export "$MOUNT_TAG" --mount "$MOUNT" \
  --mount-only --staging-parent "$STAGING_PARENT" \
  --model-profile uncensored --manifest "$MANIFEST" \
  --producer "$PRODUCER_SHA" --timeout 120 --output "$RECEIPT"
```

The model helper and manifest are pinned to
`dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8`, revision
`c19ca5ca4eaeb79d29fdf08aea8327da95b1116a`, manifest SHA-256
`5c6891a9649702b1ac0327b945600acb40c571a069942517cf14ba661a3cfcbc`:
79 files, 510,311,056,155 bytes. This identifies the intended pack, not contents
already present on the volume.

Admission reserves a failed receipt before invoking the existing pinned,
process-group-supervised worker. The total requested timeout is 61-300 seconds;
60 seconds are reserved from the worker budget. Potentially blocking mount and
filesystem probes run only in that child. Exact source, target, `virtiofs`, and
`rw,nodev,nosuid,noexec` readback is required. The staging parent must resolve to
that mount, not a nested mount. A unique private probe exercises directory-lock
contention and reacquisition, hard-link `EEXIST` without replacement, successful
hard linking/unlinking, and file/directory `fsync`. Probe failures have no fallback.
Successful probes remove only their own files/directory. Interrupted or failed
probes may remain for explicit inspection; there is no wildcard cleanup.

## Handoff and limits

A pass means only `filesystem_admitted:true` at the time of the probe. The
receipt leaves `pack_complete`, `runtime_ready`, `hardware_validated`,
`immutable`, `confidential` and `capacity_checked` false, with zero verified
model files/bytes. No source tag or operational path is published in receipts.

The mount and private parent remain for the separately approved
[existing stager](p0-t4-host-weight-staging.md). Prepare its bundle explicitly
with `--model-profile uncensored`; use a dedicated child of `STAGING_PARENT` as
the destination and retain the independently obtained builder-receipt digest.
The stager, unchanged here, checks the full-pack free-space reserve
(561,342,161,771 bytes), verifies all file hashes and emits its own evidence.
Its filesystem-type agnosticism is not a guarantee of ES durability or safety.

Keep exclusive access across all attached instances throughout staging and use.
Local lock contention is not a cross-instance lock qualification or a guard
against privileged writers. Recheck admission after mount/host/permission changes;
the receipt is not a lease or an immutable runtime boundary. Prior NFS or RAM
staging receipts do not prove that ES contains the checkpoint.

Failure leaves mount state unproven; it does not automatically detach, force
unmount, delete a volume, restart a VM, or stop billing. Explicit `--unmount-only
--backend virtiofs` verifies the exact mount and uses ordinary unmount, never
force/lazy detach. Keep tags, addresses, paths and raw diagnostics in private
operator state, not committed source or public evidence.

## Focused tests

```sh
PYTHONDONTWRITEBYTECODE=1 python3.13 -B -m unittest discover -s scripts -p 'test_*cache*.py' -v
```

These tests do not mount filesystems, access a GPU/cloud, or read model weights.
Independent final-head review and an approved actual ES run remain required.
