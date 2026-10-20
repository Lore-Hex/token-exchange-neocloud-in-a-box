# P3.T1 offline package preparation

Status: **implemented offline preparation, not an installability or image proof**.
The [preparer](../../scripts/prepare_guest_packages.py) turns the authenticated
[145-package input lock](../../image/inputs/r595-ubuntu2404.json) into a private,
deterministic local repository and an explicitly nonbootable mkosi candidate.
It also verifies an existing bundle without writes. It never installs packages,
executes their programs or maintainer scripts, builds an image, or contacts a
repository. P3.T1 acceptance, production admission and all hardware gates remain
unchanged.

This completes the archive/control-input preparation step for the selected
kernel/R595 dependency graph. It does **not** call that graph a complete guest:
systemd/initrd, podman, guest applications, the Linux build tools, UKI/verity
layout and release/debug configuration still need exact integration inputs.

## Executable preparation

Use the committed checkout that will be named in the receipt. Python 3.12 and a
trusted host `zstd` decompressor are required for these archives. These are
preparation tools, not a pinned/hermetic Linux build tools tree. The preparer
does not invoke a decompressor found inside a package.

Supply the seven exact indexes from the
[authenticated input audit](p3-t1-r595-package-inputs.md), and a separate private
directory containing exactly the 145 locked archives named `<SHA256>.deb`.
Every public download URL is the lock's index `base_url` plus the package
`Filename`; obtain the bytes separately and retain the original signature audit.
An updated repository response is rejected unless it matches the existing pin.
This command neither refreshes signatures nor asserts current repository
freshness.

Use absolute input/output paths without symlink ancestors, including for the
checkout itself. Verification reads only the prepared bundle and its producer
checkout; the original download cache is no longer needed.

```sh
python3 -I scripts/prepare_guest_packages.py \
  --metadata "$NIB_PACKAGE_METADATA" \
  --archives "$NIB_PACKAGE_ARCHIVES" \
  --output "$NIB_GUEST_INPUTS"

# Same exact producer checkout; verifies all bytes, modes and the exact file set.
python3 -I scripts/prepare_guest_packages.py --verify "$NIB_GUEST_INPUTS"
```

The output must not already exist. All directories are private (`0700`) and
files are `0600`; no package file is made executable. A rejected/partial output
is not resumable and must not be treated as successful preparation. The receipt
is written last, but publication success also requires successful fsync and a
full re-verification. A receipt left after a later failure is not a success exit.

Output:

- `repo/pool/<Package>_<SHA256>.deb`: exact authenticated archive bytes.
- `repo/Packages`: deterministic full control metadata, including dependency,
  conflict, replacement and installed-size fields; exact archive SHA/size.
- `metadata/`: the seven pinned indexes, for subsequent offline verification.
- `packages.txt`: exact package/version requests, no solver-selected upgrades.
- `package-inputs.json`: unchanged parsed lock, canonically serialized.
- `mkosi.conf.candidate`: version-specific recipe input, **not** an enabled
  `mkosi.conf`, release profile or permission to execute a build.
- `receipt.json`: producer commit and helper hashes, every output file's hash,
  and size/hash inventory of all control members (including maintainer scripts).
  Paths in the receipt are fixed bundle-relative paths, not host paths.

The `pass` result means only the package-repository preparation contract passed.
`apt_simulation_executed`, `installable_verified`, `image_built`,
`boot_validated`, `hardware_validated` and `deployable` remain false.
Verification requires the same producer revision; do not rewrite a receipt to
make a different source checkout appear equivalent.

## What is checked

The existing checker first verifies the exact authenticated index hashes and
declared dependency/conflict coverage. The preparer then checks all 463,466,682
archive bytes against the lock before decompressing control information.
Archive and output ancestors are opened without following symlinks. FIFOs,
devices, hardlinks, changed file identities, missing/extra archives and
inconsistent control metadata are rejected.

Only the control tar is parsed. It is not extracted, and the data payload is
never decompressed. Control links, special files, traversal, duplicates and
ambiguous headers are rejected. The bounded `_gpgbuilder` trailing member found
in the pinned NVIDIA packages is permitted by the
[deb format's trailing-member rule](https://manpages.debian.org/bookworm/dpkg-dev/deb.5.en.html#FORMAT);
it is not evaluated or claimed as an independently verified signature. The
whole archive's already-authenticated SHA remains mandatory.

Bounds: 512 packages; 128 MiB per archive; 512 MiB total prepared file bytes;
16 MiB expanded control tar; 64 control entries and 8 MiB per control file;
600 seconds for the archive loop and separately for bundle verification.
The trusted host zstd process has a 20-second whole-process-group limit and
16 MiB output cap. The existing index checker retains its separate size,
decompression and record bounds. Preparation holds the bounded file set in
memory; these are application bounds, not an OS memory sandbox.

Git source retrieval disables replacement objects, lazy fetching, transports,
interactive prompts, system/global configuration and hooks. Ignored files
cannot substitute for the committed source/helper/lock bytes.

## Exact build mechanism and boundaries

The candidate uses mkosi **27.1**, commit
`ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b`. No mkosi implementation is vendored or
executed. All following claims are external source observations:

1. The [manual](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/resources/man/mkosi.1.md)
   specifies Ubuntu/noble/x86-64, local `PackageDirectories`, exact package
   requests, `Format=directory`, `Bootable=no`, and a required explicit
   `ToolsTree`. The candidate uses `SourceDateEpoch=1792454400` (2026-10-20
   00:00:00 UTC), no SSH/autologin, no incremental state and no recommends.
2. The [APT backend](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/installer/apt.py)
   regenerates a local repository with `apt-ftparchive` and marks that local
   repository trusted. Its trust must therefore come from our prior byte
   verification and a protected build input, not from a new repository signature.
3. The [Debian bootstrap](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/distribution/debian.py)
   derives package names from archive basenames before `_`. The prepared names
   preserve this requirement. It first unpacks Essential packages, then
   configures them; this is not a script-free installer.
4. That APT backend uses `--force-depends` and a service-start suppression script.
   Service suppression does not prohibit arbitrary maintainer-script behavior.
   `CacheOnly=always` and `WithNetwork=no` are not by themselves a complete
   no-network, no-host-mutation boundary for all package scripts.

| Source at that commit | SHA-256 |
| --- | --- |
| `mkosi/installer/apt.py` | `5e41cd4b654ca26ec5316f0b74c728d42eb22a66d80ed7b07c525c369e7e2b87` |
| `mkosi/distribution/debian.py` | `568bf910caf9cba6cef2999fc92efadd225fbfaca6aace49e95a09e4cff0d7d9` |
| `mkosi/distribution/ubuntu.py` | `d8b4b3e171b561d82a6d21f41aee5cde00c068a9005ab330a91dc02c90d8dffa` |
| `mkosi/resources/man/mkosi.1.md` | `6344028a599ad9dc354711a5bfd328abb1d0d0a81fe8e0defa248527da537d0c` |

The receipt binds these source observations, not a built mkosi executable.
The candidate requires `ToolsTree=tools-tree` and does not supply it: no host
tool fallback or automatic tools-tree download is an accepted next step.

## Remaining build gates

Before an authorized installation rehearsal, pin a complete Linux tools tree
including mkosi, Python, apt/apt-ftparchive, dpkg, decompression and sandbox tools
and their runtime libraries. Then check the exact package set with isolated
APT configuration/state and no network, removals or fallback resolutions.
APT simulation is not implemented or claimed here; even a clean simulation
would not prove maintainer scripts, DKMS, file collisions, ELF compatibility or
bootability. Do not use mkosi's forced dpkg configuration as that proof.

Review the hashed maintainer scripts in a bounded, unprivileged build sandbox
before executing them. In particular, pin kernel/DKMS compiler and parallelism,
LKCA/initramfs ordering and UVM persistence without a blind GPU readiness setter.
Only afterward can a complete UKI/verity recipe, two clean-build comparison and
authorized boot/attestation test establish the distinct P3 acceptance claims.
No build, install, package-binary execution, cloud operation or publication was
performed by this task.
