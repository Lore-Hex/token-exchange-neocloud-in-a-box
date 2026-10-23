# Image Skeleton Directory Modes

Status: source-only defect reproduced and narrow composition fix implemented;
no corrected native execution or boot result. The
[original metadata-only failure](../../evidence/p3/2026-10-23-native-guest-network-rejection/README.md)
records `guest-network/rejected`, not its precise check or filesystem modes.
The defect below is consistent with that observation, not a uniquely established
remote root cause.

## Source-Bound Cause

The private input publisher intentionally emits directories as `0700` and files
as `0600`, and its verifier requires those modes. Previously the worker pointed
mkosi's skeleton directly at this private transport tree. Its `etc` directory
therefore had mode `0700`.

Pinned mkosi `ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b` installs
[skeleton trees before distribution packages](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/__init__.py),
in `install_skeleton_trees` and `build_image`. Its
[`copy_tree` implementation](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/tree.py)
uses `--preserve=mode,links` even when `preserve=False`. The surrounding
`preserve_target_directories_stat` restores only already-existing destination
directories; a new logical `/etc` keeps the skeleton mode. The inspected archive
SHA-256 is `3d38e848f9b1108ec76d873f2c378dfc7d4b536ad89deffe8c97504f7a8dff43`,
matching the [repository pin](../../image/inputs/mkosi-27.1.json).

Package directory modes must not be assumed to repair this. Pinned mkosi's
[`extract_tar`](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/archive.py)
uses `--no-overwrite-dir`. Exact Ubuntu dpkg `1.22.6ubuntu6.6`
[`src/main/archives.c`](https://snapshot.ubuntu.com/ubuntu/20261020T000000Z/pool/main/d/dpkg/dpkg_1.22.6ubuntu6.6.tar.xz)
marks an existing directory at lines 803-807 and returns before extracting or
changing its mode at lines 959-960. Source archive SHA-256
`7048ee31e30066ae123cdcb891cfa1ad55b2f42a4e04fca2c5db41607ae25196` and length
5,548,716 match the official
[source descriptor](https://snapshot.ubuntu.com/ubuntu/20261020T000000Z/pool/main/d/dpkg/dpkg_1.22.6ubuntu6.6.dsc).
This was a source inspection; no claim of independently verified descriptor
signature or executed package hooks is made.

The exact pinned systemd `255.4-1ubuntu8.18` archive contains `0755` directories
for `/etc`, `/etc/systemd`, and `/etc/systemd/system`. Its SHA-256
`ba37e7dc1e33690d94a1063543d27f9b17c013a20791c3315b2423fbf9e7ae7b`
matches the [runtime package lock](../../image/inputs/guest-runtime-ubuntu2404.json).
Those archive modes do not override the existing-directory behavior above.

A source-only local GNU cp reproduction using the exact pinned mkosi copy
arguments retained `etc=0700` and the unchanged guest-network `parent()` gate
rejected it. No packages or hooks were run. Portable unit regressions model the
same mode-preserving copy and show the corrected skeleton passes under umasks
`022`, `027`, and `077`. Private outer build/staging directories are not logical
guest parents: `parent()` starts inside the guest root descriptor and checks
the logical children, not their host-side ancestors.

## Narrow Fix

Before installation, the worker materializes only the existing three fixed
DKMS controls into a fresh configuration-owned `mkosi-skeleton`. The outer
configuration and skeleton directories remain `0700`; only newly created
`etc`, `etc/dkms`, and `etc/dkms/framework.conf.d` become `0755`. Control files
remain `0600`, single-link, and byte-identical. The original private payload and
its strict verification are untouched. The repo and tools-tree aliases are
unchanged.

Directory traversal is descriptor-anchored and no-follow. Source and private
destination directories must be owned and `0700`. The existing bounded,
stable regular-file reader gains an optional directory descriptor so the
three source reads and destination readbacks stay anchored. Each source is
at most 16 KiB. Destination creation is exclusive; no existing directory is
chmodded and no unlisted file is copied. Writes are flushed, synced and read
back. All descriptors close on failure; the existing configuration-stage
failure path prevents installation/export, and the enclosing private workspace
and payload cleanup remain responsible for discarding any partial skeleton.

The strict guest `0755`, ownership, no-follow and traversal checks are unchanged.
No recursive permission normalization, host umask change, mode exception,
timeout/output-cap adjustment, service start, network/device access or new
workflow flag is added.

## Checks And Next Gate

```sh
python -B -m unittest discover -s scripts -p 'test_mkosi_skeleton.py'
python -B -m unittest discover -s scripts -p 'test_native_guest_network_rejection_evidence.py'
```

Focused coverage includes original failure bytes/source closure, private-source
preservation, exact control bytes, unsafe parents/leaves, size/link bounds,
destination collisions, readback failure, and configuration rejection before
any package installation or artifact export. Full checks and independent final
head review precede integration. A new exact merged-source proposal and explicit
authorization are required before any actual corrected CPU debug-boot attempt.
