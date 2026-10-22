# P3.T1 Immutable Root Build Artifacts

Two actual builds of `ad1ab45e6b96a06a5972bedfcb5d4b5f5dcf5c9c` in
[workflow 39839973107](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39839973107)
succeeded individually but produced different EROFS/verity/UKI identities.
The fixed tree diagnostics agree for `etc`, `usr`, `boot`, `var/lib/dpkg`
and other entries. Only `var/log`, `var/lib/dkms` and remaining `var` differ.
The selected DKMS make log differs too. These original diagnostics remain under
[evidence](../../evidence/p3/2026-10-22-native-root-tree-mismatch/README.md).
The original earlier mismatch remains
under [evidence](../../evidence/p3/2026-10-22-native-repro-mismatch/README.md).

## Narrow Finalization

After installer/driver children exit, before generating EROFS, discard only:

- `var/lib/dkms/nvidia/595.91.07/6.8.0-147-generic/x86_64/log/make.log`.
  The authenticated DKMS 3.4.3 script writes wall time at line 1581 and elapsed
  time at line 199, then moves the log to this path at line 1708. The existing
  driver recorder already preserves its original private diagnostic copy.
  Archive and source hashes are in the [review](p3-t1-dkms-build-controls.md).
- `var/log/alternatives.log`. This is the package operation log, distinct from
  `/var/lib/dpkg/alternatives` state and `/etc/alternatives` links, as documented
  in the [upstream manual](https://manpages.debian.org/bookworm/dpkg/update-alternatives.1.en.html).
- `var/cache/ldconfig/aux-cache`. The glibc 2.39
  [source](https://github.com/bminor/glibc/blob/glibc-2.39/elf/cache.c#L804-L884)
  (`elf/cache.c` Git blob `8a618e11fa414abb05966125c70bd02bd3116560`)
  describes this build accelerator and binds entries to inode, ctime, size and
  device. It is not the runtime loader's `/etc/ld.so.cache`, which stays intact.

The second and third paths are source-backed nondeterminism candidates, not yet
individually proven causes in the actual pair. A fresh repeat-build comparison
is still required; matching diagnostics alone cannot prove image identity.

No recursive deletion, arbitrary glob, package database cleanup, source-pin
change or verification relaxation is introduced. Only these three fixed leaf
names are considered. Missing files are reported as null. Present files must
be regular, singly linked, at most 2 MiB, and stable across reads. Parent and
leaf opens reject symlinks. Every candidate is validated before any removal;
failures abort the image build. Fixed-name byte counts and hashes are retained
in `discarded_build_artifacts`, never contents or dynamic paths. The remaining
tree is fingerprinted after removal. This is an isolated build-root operation,
not an operation on a running workload.

Synthetic tests cover the exact deletion scope, preservation of runtime state,
empty/missing files, size limits, FIFOs, directories, symlink parents/leaves,
hard links, identity changes, removal failures and content-free receipts.
Full tests, review and two fresh native builds are required before any claim
that this fixes reproducibility. No boot, signing or confidential qualification
is granted by finalization.
