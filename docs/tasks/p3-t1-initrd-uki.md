# P3.T1 private initrd and unsigned UKI increment

Status: implemented and fixture-tested, **not natively executed or booted**.
This is a generation/inspection path using existing authenticated tools, not a
completed P3.T1 image, release output or confidential proof. No workflow, GPU,
firmware, signing service or cloud build was invoked for it.

The separate [private artifact export](p3-t1-private-boot-export.md) adds an
explicit local-only opt-in. It retains the verified GPT through UKI verification
and copies both exact images before cleanup; the no-export behavior below is
unchanged. Export does not establish a boot or signing result.

## Entry point and source binding

The existing main-only manual
[`mkosi-native-build.yml`](../../.github/workflows/mkosi-native-build.yml) adds
`boot_uki`, default `false`. It requires `verity_root`, which already requires
`guest_runtime`, `compile_driver` and `install_driver`. Both host entry points
and the isolated worker reject missing prerequisites. Without this flag the
existing command arguments and generated payload are unchanged.

Order: 212-package configured inventory, driver compile/decompress/install,
offline startup policy, appraiser CLI probe, EROFS/verity generation and
userspace verification, then private initrd/UKI assembly. An earlier failure
cannot be bypassed. The [verity stage](p3-t1-verity-root.md) continues to delete
its images before returning; this stage consumes its verified root hash, not
an exported disk. Only in this new mode, repart's reported data/hash GPT UUIDs
must equal the first/last 128 bits of that root hash, as required by the pinned
systemd initrd verity device discovery.

[`prepare_mkosi_boot.py`](../../scripts/prepare_mkosi_boot.py) is compiled from
the committed source map, not imported from an ambient or mutable module path.
It verifies full archive sizes/hashes and derives a fixed member inventory from
39 packages already in the existing 212-package lock. No lock, dependency,
resolver, package hook or package count changes. The 32 library package names
are the fixed dependency closure of the selected systemd/udev/kmod/mount tools.
The remaining packages are those utilities plus dmsetup, base-files and the
exact kernel's base modules. No NVIDIA module is put in the initrd; the driver
remains in the verified installed root.

The original archive-only inventory had 2,321 entries and 81,138,441 regular-file
bytes, pinned to `9e58a03558396b6cf3370b44405fad56acc18c7962e6b80661eb8942ce46d540`.
That omitted a deterministic installed-file change: the recipe's fixed
`ImageId=nib-r595-installability-candidate` makes pinned mkosi's
[configure_os_release](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/__init__.py#L373)
append `IMAGE_ID="nib-r595-installability-candidate"` to `usr/lib/os-release`.
Preparation now applies that exact 45-byte line to the authenticated package
member before hashing the inventory, using the same image-ID constant as the
recipe. It rejects an existing IMAGE_ID, unsupported image ID or malformed input;
there is no generic permission to accept generated or modified members.

The corrected package-derived inventory has 2,321 entries, 81,138,486 regular-file
bytes and the same 471,091-byte JSON size. Its SHA-256 is
`b10eb64d60d8cfeba7de15b8533e2f7f8b38f56594bfa4abe90b4ff55ba6971b`.
The only changed member is `usr/lib/os-release`: 400 to 445 bytes, with installed
SHA-256 `27294876c0c569b1f50960119e09e149bc2436aaff672528931a81d4ffc79b8b`.
The [original failed native run](../../evidence/p3/2026-10-22-boot-member-rejected/README.md)
did not identify the failing file. This source-backed correction still requires
a native retry; it does not retroactively prove that run's precise failure.
These are input inventory measurements, **not generated image sizes**. Native
assembly copies installed regular files only when their bytes match this
inventory; vendor links are inert inventory data, never followed for copying.
The package selection includes both `dm-verity.ko.zst` and `erofs.ko.zst` and
builtin module metadata, but does not prove their loading or hardware support.

The source allowance rises from 30 to 32 extra paths solely for the inventory
preparer and [`mkosi_boot.py`](../../scripts/mkosi_boot.py): 40 unique source
paths including the eight base inputs. The native helper and generated inventory
are read-only payload inputs. Python executes the exact pinned ukify, pefile and
ordlookup source bytes, never ambient site packages or cached vendor bytecode.

## Fixed operations

1. Assemble a fresh private tree from the fixed member inventory. Use systemd
   PID1, the initrd marker and the vendor `initrd.target` as the default. Retain
   vendor units, rules and libraries. Add fixed masks for emergency/rescue/debug
   shells, gettys and network services; no network configuration or host `/etc`
   is copied. Mask the vendor `syslog.conf` basename and disable journal storage
   and forwarding. These settings are not a complete boot/release policy audit.
2. Execute pinned kmod with compatibility `argv[0]=depmod`, an explicitly empty
   config, the private basedir and exact kernel version. Require all existing
   tree members unchanged and exactly the ten expected new index files. No
   module is loaded. Normalize mtimes to `1792454400`.
3. Generate an uncompressed `newc` archive with pinned GNU cpio using mkosi's
   reproducible/numeric-ownership interface. Independently check every entry's
   name, kind, mode, root ownership, timestamp, link target or file hash, plus
   exact membership and trailer. The parser verifies an archive; it does not
   generate a substitute production archive or cryptographic proof.
4. Run pinned ukify with an explicit empty config, x64 stub, exact kernel,
   archive, os-release, uname, command line and output. No signing key, PCR
   measurement or kernel re-signing is requested. The fixed command line is
   `root=/dev/mapper/root rootfstype=erofs ro roothash=HEX`, where `HEX` is exactly
   the verified 64-lowercase-hex root hash. No corruption-ignore option exists.
5. Parse the generated outer PE with pinned pefile, require AMD64/PE32+/EFI
   application, no parser warnings, no certificate directory, no duplicate or
   extra sections, and aligned, non-overlapping raw and virtual section ranges
   outside headers and within the image. Bind unchanged DOS/COFF/optional-header
   fields, all data directories, entry point, stub section metadata and bytes to
   the pinned stub. Permit only ukify's explicit header growth, alignment and
   exact stub/kernel SBAT merge. Require the five embedded
   payload sections (`.linux`, `.initrd`, `.cmdline`, `.osrel`, `.uname`) to match
   the exact inputs. The other six sections are the pinned stub's section names,
   including ukify's vendor SBAT handling. Payload permissions are fixed. This is
   not signature verification.
6. Recheck archive, tree, image and tool identities. Delete the entire private
   workspace before success; return only bounded diagnostics and content-free
   identities. Initial collisions are errors, never overwritten or removed.
   A child failure, timeout, overflow, identity mismatch or failed cleanup cannot
   produce a successful helper receipt.

## Pins and primary sources

All executable, kernel and stub member sizes/SHA-256 values are fixed in the
helper and rechecked before native execution. They were read from full-hash-
verified archives already in the tools/runtime locks, not downloaded as new
dependencies. The existing kernel is `6.8.0-147-generic`; systemd/ukify/stub are
Ubuntu `255.4-1ubuntu8.18`, cpio `2.15+dfsg-1ubuntu2.1`, and kmod
`31+20240202-2ubuntu7.2`.

| Input | Bytes | SHA-256 |
| --- | ---: | --- |
| GNU cpio | 140928 | `d342b752fcab2d8d6af40a548bcd6ba096ab34c52c53b608618f55950734ffe5` |
| kmod | 174328 | `5abf732e561c8be9ccd1794940b5d43025b041d4bed1db34782d979e0accc36f` |
| ukify source | 56179 | `62b033782cf535e8633d82e4b6f4792352578da86a8bf0984beeaffd68b3c382` |
| Kernel | 15083912 | `41ba36d860aa0b0cd995f4ca8856d80249b244577e704929b55be13fbd1ebd2f` |
| x64 EFI stub | 68608 | `799551ebda600e8358612a1d15805df5311210243a6ee69178aa27ff0a195d4e` |

Interfaces were checked against the authenticated Ubuntu payload sources and:

- [mkosi ecf7f570 archive.py](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/archive.py),
  `make_cpio`; [initrd/UKI implementation](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/__init__.py),
  `configure_initrd` and `build_uki`.
- [kmod v31 dispatch](https://github.com/kmod-project/kmod/blob/v31/tools/kmod.c),
  compatibility command dispatch by basename of argv[0];
  [depmod](https://github.com/kmod-project/kmod/blob/v31/tools/depmod.c),
  `--all`, `--config`, `--basedir` and index outputs. `kmod depmod` is not used.
- [Linux v6.8 initramfs format](https://github.com/torvalds/linux/blob/v6.8/Documentation/driver-api/early-userspace/buffer-format.rst),
  newc fields, alignment, trailer and hard-link handling.
- [systemd-stable v255.4 ukify](https://github.com/systemd/systemd-stable/blob/v255.4/src/ukify/ukify.py),
  explicit configuration, `create_parser`, `make_uki`, `pe_add_sections` and
  `merge_sbat`. The actual pinned Ubuntu Python parser accepted the helper's
  fixed options in a non-building local source audit. Pinned pefile also parsed
  the existing stub/kernel without warnings. This is not UKI execution proof.
- [verity generator](https://github.com/systemd/systemd-stable/blob/v255.4/src/veritysetup/veritysetup-generator.c),
  `determine_device`; [repart](https://github.com/systemd/systemd-stable/blob/v255.4/src/repart/repart.c),
  root-hash-derived partition UUIDs; [fstab generator](https://github.com/systemd/systemd-stable/blob/v255.4/src/fstab-generator/fstab-generator.c),
  `parse_proc_cmdline_item` and `add_sysroot_mount` for root device/type/read-only.

## Bounds and remaining gates

Build namespaces, synthetic `/dev`, loopback-only network, read-only tools and
guest, NNP, seccomp and the existing eight-capability ceiling are unchanged.
Only the private generation workspace and private scratch tmpfs are writable;
inspection mounts the workspace read-only. No host service, real device,
network request or new capability is used. The cgroup stays at 8 GiB, no swap,
256 PIDs and two CPUs. Outer supervisor/root-launcher limits remain 1900/2500
seconds: no per-stage timeout extends the available outer time.

Inventory/tree limits are 8,192 entries and 192 MiB of regular data; each image
is limited to 256 MiB. Children disable core dumps and enforce hard file-size
limits (zero for inspection). Depmod gets 60 seconds, cpio and ukify 120 seconds
each, inspection 60 seconds. Eight allowlisted diagnostic streams are bounded
to 64 KiB each. Reads require descriptor-anchored, no-follow, stable, regular,
single-link files. Writes are exclusive/no-follow. The existing worker receipt
and artifact bounds remain in force. Practical native resource fit is unproven.

The resulting outer UKI is unsigned; the kernel payload is preserved unchanged.
Embedding a verity hash in an unsigned artifact does **not** authenticate root
selection. There is no ESP/boot disk export, Secure Boot or TPM measurement,
early-userspace loader/module/generator execution proof, root switch, mounted
verity enforcement or post-mount tamper test. Runtime writable-state mounts,
device/firmware support and complete release hardening remain separate work.
Neither release/debug measurement separation nor two-clean-build reproducibility
is proven by a fixed epoch or an input inventory. No LKCA, attestation, quality,
confidentiality or production claim follows. P3.T1 acceptance remains open.

Focused checks from `scripts/` with the existing Python 3.12 environment:

```sh
python -B -m unittest test_mkosi_boot test_mkosi_build test_mkosi_verity \
  test_mkosi_dkms test_prepare_mkosi_inputs test_build_resources \
  test_mkosi_runtime_policy test_mkosi_appraisal_probe test_mkosi_confinement
```

Tests explicitly use synthetic CPIO/PE data and supervisors. A later separately
authorized main-only native run must retain the original receipts and independent
resource-cleanup evidence before claiming real initrd/UKI generation. Export,
boot and reproducibility each require their own evidence and review.

Independent source-level review found missing executable-stub and PE-layout
checks in the first draft. Regression tests now reject stub-code mutations,
entry-point redirection, altered data directories/SBAT/permissions, overlapping
raw or virtual ranges, misalignment and header/image-bound violations. Review
also checked the actual authenticated ukify, parser, stub and kernel bytes in
memory: the baseline passed and all 29 mutation controls were rejected. That
was a parsing/inspection check, not native image generation or boot evidence.
