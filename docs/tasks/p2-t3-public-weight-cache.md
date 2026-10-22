# P2.T3 persistent public-weight cache

`host/cache-public-weights.py` is an operator-approved lab utility, not a guest
component or confidential storage implementation. It preserves only the exact
83-file DeepSeek V4.1 Flash 1017 manifest. It never deletes the original pack or
updates fstab. Partial copies remain explicitly failed; existing destinations
are not overwritten or silently resumed.

The operator supplies the approved NFS export, fresh mount directory, local
source, pinned manifest, producer commit, private output receipt and timeout
up to two hours. The [Spheron Verda mounting guide](https://docs.spheron.ai/connecting/volume-mounting/data-crunch)
specifies NFS and `nconnect=16`; the utility additionally requires `nodev`,
`nosuid` and `noexec`, checked through actual mount readback. No package is
installed, no network key is copied, and no public endpoint is opened.

The utility rejects unexpected source files, links, special files, corrupt
content and noncanonical directories. It checks every source hash before copying,
checks copied bytes, syncs files, then independently re-reads every destination
hash. The receipt contains only fixed stages, hashes, timestamps and counts.
No prompts, responses or credentials belong on this volume. This is a public
download cache only: confidential deployment still requires independently
verified model hashes, dm-verity and attestation. A completed copy is not a
power-loss durability, immutable-volume or confidentiality proof.

## Restore After A Lab VM Interruption

The same helper supports `--restore-to NEW_LOCAL_DIRECTORY` instead of
`--source`. It mounts the approved cache read-only, requires kernel readback
of `ro,nodev,nosuid,noexec`, and restores only the fixed pack subdirectory.
It reuses the full hash/copy/hash checks above, requires space for the complete
pack plus overhead, and never overwrites or resumes a partial destination.

```sh
python3 -I -S -B host/cache-public-weights.py \
  --export "$APPROVED_EXPORT" --mount "$NEW_MOUNT_DIRECTORY" \
  --restore-to "$NEW_LOCAL_DIRECTORY" \
  --manifest models/deepseek-v4.1-flash-1017/1/files.sha256 \
  --producer "$REVIEWED_COMMIT" --timeout 7200 --output "$NEW_PRIVATE_RECEIPT"
```

Run under the existing detached-job procedure. The receipt is reserved before
mounting or copying and records failure on deadline, corruption or failed
readback. The cache remains read-only mounted afterward; use the documented
explicit unmount operation when appropriate. No automatic teardown, deletion,
engine launch or confidential verdict is introduced. Tests use tiny synthetic
fixtures, not a measured recovery of the full model pack.

Tests use tiny synthetic files and fake mount commands. They cover source
preservation, exact bytes, no overwrite, corrupted/extra/link/FIFO rejection,
deadline, nested destinations, pinned manifest and mount readback. Actual
mount/copy success requires its own recorded hardware run.
