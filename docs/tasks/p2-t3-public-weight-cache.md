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

Tests use tiny synthetic files and fake mount commands. They cover source
preservation, exact bytes, no overwrite, corrupted/extra/link/FIFO rejection,
deadline, nested destinations, pinned manifest and mount readback. Actual
mount/copy success requires its own recorded hardware run.
