# P2.T3 persistent public-weight cache

The original modes described here remain unchanged. The separate opt-in
[fixed dealign cache and seed mode](p2-t4-public-cache-seed.md) reuses this helper
and supervisor for verified regular copies, with its own partial/full receipts.

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

Run under the existing detached-job procedure. Keep this script, its sibling
`host/probe.py`, and the private receipt on local storage, outside the cache and
restore destination. The parent verifies the supervisor source against
SHA-256 `dd196679dc4b6d7ee335b3d62900b25484734884bd3da6e74f925260ad7864ff`
and executes the exact restore source bytes recorded in the receipt, without
ambient helper imports or cached bytecode. No additional dependency is installed.

The parent reserves and syncs a failure receipt before launching the child.
Manifest loading, free-space inspection, mount commands, readback and every
hash/copy operation run in that independently supervised child. Its budget is
the requested timeout minus 60 seconds; the existing supervisor kills its
owned process group and bounds its own reap wait to one second. The reserve
leaves time for reporting. This does not depend on a Python alarm interrupting
[hard-NFS RPC waits](https://github.com/torvalds/linux/blob/v6.8/net/sunrpc/sched.c#L918-L922).

Timeout, cancellation, nonzero exit, malformed output or failed supervisor
cleanup cannot produce a pass. Such receipts conservatively say
`cleanup=unproven`: sending SIGKILL or reaping the supervisor does not prove
that a kernel-stuck descendant disappeared. Successful `worker-completed`
means the verified worker exited normally and the supervisor was reaped;
`process_group_absence_verified` remains false. Do not infer VM teardown or
mount absence from either result. A failure may leave a partial destination;
it is never overwritten or silently resumed.

The cache remains mounted after a completed restore; use the documented
explicit unmount operation when appropriate. No automatic teardown, deletion,
engine launch or confidential verdict is introduced. The historical upload
and explicit-unmount modes are unchanged. Tests use tiny synthetic fixtures,
including a real local subprocess that ignores its own alarm, not a measured
NFS outage or recovery of the full model pack.

Tests use tiny synthetic files and fake mount commands. They cover source
preservation, exact bytes, no overwrite, corrupted/extra/link/FIFO rejection,
deadline, nested destinations, pinned manifest and mount readback. Actual
mount/copy success requires its own recorded hardware run.
