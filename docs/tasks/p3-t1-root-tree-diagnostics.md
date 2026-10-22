# P3.T1 Root Tree Diagnostics

Before EROFS creation, the private offline builder records content-free hashes
for eight fixed directory buckets and eight fixed build-state files. This
localizes differences between repeat builds without publishing file contents,
dynamic filenames, guest secrets, raw logs or filesystem images. No files are
deleted or normalized by the diagnostic. It does not change EROFS/verity/UKI
commands, source pins, export verification or image-deletion policy.

The buckets separate `etc`, `usr`, `boot`, `var/log`, `var/lib/dkms`,
`var/lib/dpkg`, remaining `var` entries and other entries. Each canonical row
hash binds the relative path, file kind, permissions, UID/GID, byte length and
file-content or symlink-target hash. Sorted traversal is independent of directory
enumeration order. Only fixed bucket/file names, counts, lengths and hashes
appear in the report. Missing selected files are explicitly `null`.

All directory traversal is descriptor-relative with no-follow opens. Regular
files are streamed in bounded chunks, and descriptor/path metadata is checked
before and after reading. Symlinks are never followed. Special files, excessive
depth/path lengths, more than 150,000 entries, more than 8 GiB of file bytes or
the 120-second deadline reject the build. Existing outer cgroup/process deadlines
still apply. This runs only in the already-confined, credential-free installation
root, never on a live workload or host root.

This is a diagnostic, **not a filesystem integrity verifier**. It deliberately
does not model xattrs, timestamps, hard-link topology, compression or filesystem
layout. EROFS/verity checks and actual image hashes remain authoritative. A
matching diagnostic does not prove matching image bytes; a differing bucket
identifies where to investigate, not which file is the root cause.

The selected build-state files are leads, not claimed causes. The pinned
[mkosi source](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/installer/__init__.py)
returns early from `clean_package_manager_metadata` for directory output in
automatic mode; the pinned
[APT configuration](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/installer/apt.py)
sets `Dir::Log=/var/log/apt`. The existing driver recorder preserves its known
DKMS make-log paths. These facts motivate observing build metadata rather than
blindly deleting it. No observed root cause or reproducibility success is
claimed by this code change.

Synthetic tests cover repeatability, isolated log changes, content canaries,
symlink targets, mode changes, normalized timestamps, entry/byte/time limits,
special files and selected-file link rejection. Actual repeated builds must
preserve their original receipts before their diagnostic hashes are compared.
