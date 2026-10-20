# P3.T1 Ubuntu kernel candidate inspection

Status: **authenticated package-content inspection only**. Observed 2026-10-20.
This narrows the kernel input gap in the
[guest-image prerequisite audit](p3-t1-guest-image-feasibility.md); it is not a
complete image lock, installation recipe, accepted measurement or hardware
result. No package script, package executable or kernel module was executed.
No image was built or booted. [PLAN](../../PLAN.md) and [gates](../gates.md) are
unchanged.

## Authentication and candidate

The candidate is Ubuntu 24.04's GA `6.8.0-147.147`, with release string
`6.8.0-147-generic`. It is not a selection of a floating `linux-generic` package,
an HWE substitution, or a claim that this version is perpetually security-current.

Canonical's [archive-key documentation](https://ubuntu.com/chisel/docs/v1.3.0/reference/security/)
identifies the Ubuntu Archive Automatic Signing Key (2018), full fingerprint
`F6ECB3762474EDA9D21B7022871920D1991BC93C`. An isolated `gpgv` verification with
that key accepted the saved
[noble-updates InRelease](https://archive.ubuntu.com/ubuntu/dists/noble-updates/InRelease).
The authenticated Release identifies Ubuntu / noble-updates / noble, amd64,
main, with Date `2026-10-20 12:44:43 UTC`; its signature is dated
`2026-10-20 12:45:37 UTC`. No `Valid-Until` field was present. These are dated
repository observations, not a general freshness or vulnerability assessment.

| Authenticated object | SHA-256 |
| --- | --- |
| InRelease | `4fd8f772d483cb011bf899a9e1a20c38c7dec90fbc21bf5083d4ada6b7d9120b` |
| Verified Release plaintext | `d9f7b934e18de77f3c2a1a53a0946a3e2811939ab350d510bcc8fe6630fdf9bc` |
| main/binary-amd64/Packages.xz, 1397104 bytes | `a398e4efccd241c15ea24a92e1aedfbb853f9e9100e73de9f6bc6f1d5b2581fa` |
| main/source/Sources.xz, 568824 bytes | `56956fdc9dbdf980e52b8c02fb211cced4bf01ffc3cd4773779d2dd2b27783e2` |

The [binary index](https://archive.ubuntu.com/ubuntu/dists/noble-updates/main/binary-amd64/by-hash/SHA256/a398e4efccd241c15ea24a92e1aedfbb853f9e9100e73de9f6bc6f1d5b2581fa)
and [source index](https://archive.ubuntu.com/ubuntu/dists/noble-updates/main/source/by-hash/SHA256/56956fdc9dbdf980e52b8c02fb211cced4bf01ffc3cd4773779d2dd2b27783e2)
matched their signed size/hash entries. All five downloaded package archives
matched the binary index before non-executing inspection, totaling 185166394
bytes. Version is `6.8.0-147.147` throughout; architecture is amd64 except the
common headers (`all`).

| Package | Bytes | SHA-256 |
| --- | ---: | --- |
| [linux-image-6.8.0-147-generic](https://archive.ubuntu.com/ubuntu/pool/main/l/linux-signed/linux-image-6.8.0-147-generic_6.8.0-147.147_amd64.deb) | 14845068 | `08432502e565a8a3b4ab13bd8b01788a0a83c9398c8a1e9350f1225991bc4fbb` |
| [linux-modules-6.8.0-147-generic](https://archive.ubuntu.com/ubuntu/pool/main/l/linux/linux-modules-6.8.0-147-generic_6.8.0-147.147_amd64.deb) | 39250112 | `3b9188304d600fc35a4f4d1f99751f7ea485bb7f9e0cb87831d4ebf2d3423d9d` |
| [linux-modules-extra-6.8.0-147-generic](https://archive.ubuntu.com/ubuntu/pool/main/l/linux/linux-modules-extra-6.8.0-147-generic_6.8.0-147.147_amd64.deb) | 113500352 | `b3f16d16b54796497884b27810b0942243bdc6ef26da1ce821e948ece39a9492` |
| [linux-headers-6.8.0-147](https://archive.ubuntu.com/ubuntu/pool/main/l/linux/linux-headers-6.8.0-147_6.8.0-147.147_all.deb) | 13677052 | `16c59831600aa76bbf2ce56000a7874a5c7b52f9507ae3deb0e633f1d71078bf` |
| [linux-headers-6.8.0-147-generic](https://archive.ubuntu.com/ubuntu/pool/main/l/linux/linux-headers-6.8.0-147-generic_6.8.0-147.147_amd64.deb) | 3893810 | `8acc8adee7f81b66adc95dfad5989c03a154bcc20dd91e52efd7f1e63db82109` |

The image is from source package `linux-signed`; the other four are from
`linux`. The signed source index supplies the corresponding `.dsc` and source
archive hashes. Those source archives were not downloaded or built; archive
authentication is not proof of reproducible source-to-binary correspondence.

## Configuration and module contents

The base modules package's `boot/config-6.8.0-147-generic` and generic headers'
`usr/src/linux-headers-6.8.0-147-generic/.config` are byte-identical: 287601 bytes,
SHA-256 `cf8454bc2e67effab87a5bff3c39042f2d8d106b6a777c4302e52f50d23b7400`.
Both header release fields (`include/config/kernel.release` and
`include/generated/utsrelease.h`) identify `6.8.0-147-generic`.

| Config symbols, with the `CONFIG_` prefix | Actual values |
| --- | --- |
| `INTEL_TDX_GUEST`, `CONFIGFS_FS` | `y` |
| `TDX_GUEST_DRIVER`, `TSM_REPORTS` | `m` |
| `CRYPTO_ECC`, `CRYPTO_ECDH`, `CRYPTO_ECDSA` | `m` |
| `CRYPTO_AKCIPHER`, `CRYPTO_KPP`, `CRYPTO_RNG_DEFAULT`, `ASN1` | `y` |
| `BLK_DEV_DM`, `EFI_STUB` | `y` |
| `DM_VERITY`, `VSOCKETS`, `VIRTIO_VSOCKETS` | `m` |
| `MODULES`, `MODULE_SIG`, `MODULE_SIG_ALL`, `MODULE_SIG_SHA512` | `y` |
| `MODULE_SIG_FORCE` | **not set** |

In particular, **including only the image and base modules misses the packaged
TDX/TSM modules**. Their exact locations below are relative to
`lib/modules/6.8.0-147-generic/`. Every inspected object is x86-64 relocatable
ELF with vermagic `6.8.0-147-generic SMP preempt mod_unload modversions `
(including the final space). Static `.modinfo` supplies the dependencies below.
Matching metadata is not a module-load, ABI, attestation or driver test.

| Archive | Member below the release directory | Declared module dependency |
| --- | --- | --- |
| extra modules | `kernel/drivers/virt/coco/tdx-guest/tdx-guest.ko.zst` | `tsm` |
| extra modules | `kernel/drivers/virt/coco/tsm.ko.zst` | none |
| base modules | `kernel/crypto/ecc.ko.zst` | none |
| base modules | `kernel/crypto/ecdh_generic.ko.zst` | `ecc` |
| base modules | `kernel/crypto/ecdsa_generic.ko.zst` | `ecc` |

The base archive's `modules.builtin` also lists
`kernel/fs/configfs/configfs.ko`; this agrees with its built-in configfs setting.
The module member hashes below bind compressed payload bytes, not installed or
accepted guest measurements:

| Member basename | Bytes | SHA-256 |
| --- | ---: | --- |
| `tdx-guest.ko.zst` | 4552 | `5b11e366bb6908872844215900f2e5033d65f19f96b7242dcf95c7061609f555` |
| `tsm.ko.zst` | 6630 | `48923f1b9091deecaccd5683c89f3176b46911f22a7f11c1c5827802d91e17de` |
| `ecc.ko.zst` | 19131 | `97a82a7011c93b6acc9ceb33f80907ba510988375ba50df9eef69038bab12d02` |
| `ecdh_generic.ko.zst` | 4748 | `a7afa413d7aa6caaf477de2a34c04e851889341a1a3775a1349df6528ea14243` |
| `ecdsa_generic.ko.zst` | 5584 | `e80680795815722e159c78ac1dd929ff3212a407008d2681fe7bc539f5cc6d5e` |

The source-level requirements are consistent with the pinned
[Linux v6.8 TDX guest Kconfig](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/virt/coco/tdx-guest/Kconfig)
and [TSM Kconfig](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/virt/coco/Kconfig).
Those upstream files alone would not prove the Ubuntu binary configuration.

Google's [confidential GPU guide, LKCA/persistence steps](https://docs.cloud.google.com/confidential-computing/confidential-vm/docs/create-a-confidential-vm-instance-with-gpu#enable_confidential_computing_mode_on_the_gpu)
requires attention to crypto-module initialization before NVIDIA and initrd
contents. The observed crypto payloads are prerequisites, not proof that R595
uses LKCA correctly on this kernel. Module names here describe archive contents;
this document deliberately supplies no untested load command.

`MODULE_SIG_FORCE` being unset means this config alone does not force rejection
of unsigned modules. The actual boot policy, signature enforcement and trust
keys still require review and testing. Likewise, modular verity support does
not prove it is available early enough to authenticate/mount the root.

## Remaining closure and next step

Actual control records agree with the index for package identity, dependencies,
pre-dependencies, recommendations and suggestions. Direct dependencies outside
the five-package set remain unresolved:

`coreutils`, `libc6 (>= 2.38)`, `libelf1t64 (>= 0.144)`,
`libgcc-s1 (>= 4.2)`, `libssl3t64 (>= 3.0.0)`,
`zlib1g (>= 1:1.2.3.3)`, `kmod`, `linux-base (>= 4.5ubuntu1~16.04.1)`,
and `wireless-regdb`.

The image recommends bootloader alternatives and
`initramfs-tools | linux-initramfs-tool`. A UKI recipe must choose its boot/initrd
tools deliberately, not inherit these recommendations or a host default. The
generic headers' config reports GCC 13.3.0 (Ubuntu
`13.3.0-6ubuntu2~24.04.1`); that observation is not a pinned build-tool closure or
proof of a compatible R595 module build.

The next bounded source task is to authenticate the intended R595 open-driver
package set and inspect its kernel-build/LKCA requirements against this
candidate, then resolve the remaining Ubuntu dependency and build-tool closure.
Stop that component if the exact artifacts or required compatibility facts are
unknown. Do not invent a version pairing or add a permanently blocked wrapper.
Initrd composition/load order, appraisal-gated readiness, UKI/root-hash binding,
release/debug separation and two-clean-build reproducibility remain unresolved.

This is a factual note about externally published package contents. The
preserved private package inspection is not a committed hardware run or P3
acceptance evidence; no package payloads or upstream implementation are vendored.
