# P3.T1 R595 package inputs

This is an **offline dependency-coverage lock**, not an installable guest image,
driver build, boot result, attestation result or permission to rent hardware.
The [P3.T1 gates](../gates.md) and [kernel audit](p3-t1-kernel-package-audit.md)
remain unchanged. The checker never downloads, installs, imports or executes
package contents.

## Candidate and security correction

PLAN's `595.58.03` is the April 2026 trusted-computing reference, not a
security-current image pin. NVIDIA's [May bulletin 5821](https://nvidia.custhelp.com/app/answers/detail/a_id/5821)
identifies affected Linux R595 versions before `595.71.05`. The later
[September bulletin 5861, updated October 13, 2026](https://nvidia.custhelp.com/app/answers/detail/a_id/5861)
identifies the Linux R595 update as **595.91.07**. This audit therefore selects
`595.91.07-1ubuntu1`, present in the authenticated Ubuntu 24.04 NVIDIA index.
It does not assert that these bulletins are a perpetual security clearance.

The [595.91.07 release notes](https://docs.nvidia.com/datacenter/tesla/tesla-release-notes-595-91-07/index.html)
list Ubuntu 24.04 support and an August 3, 2026 release. They also warn that
Hopper subrevision 3 needs VBIOS `96.00.68.00.xx` or newer. This is an additional
hardware preflight requirement, **not** evidence about an available machine.
AMD SEV-SNP capacity does not satisfy the Intel TDX gate.

## Exact input graph

[r595-ubuntu2404.json](../../image/inputs/r595-ubuntu2404.json) contains 145 exact
binary-package records totaling **463,466,682 archive bytes**. Each record pins
the source index, architecture, version, archive path, size and SHA-256, plus
the dependency/conflict fields used by the check.

The explicit roots are:

- The five `6.8.0-147.147` image/module/header packages inspected in the kernel
  audit, including `linux-modules-extra` for TDX/TSM.
- `nvidia-headless-open=595.91.07-1ubuntu1`; every selected R595 component is
  pinned to that version even where the metapackage uses unversioned Depends.
- `gcc-13=13.3.0-6ubuntu2~24.04.1`, matching the kernel's recorded compiler.
- Ubuntu 24.04 `nvattest` and `libnvat=2.0.0.1790976895-1`.
- All 23 Essential package names observed in the authenticated amd64/all
  indexes, and the transitive Depends/Pre-Depends of these roots.

The selected DKMS is `1:3.4.3-1ubuntu1` from NVIDIA's repository; its epoch is
part of the pin. This includes declared build tools such as make, binutils,
patch and pahole. It is not a claim that all undocumented ELF or build-time
dependencies have been discovered.

The graph covers Depends and Pre-Depends, versioned alternatives/Provides,
Conflicts and Breaks, and rejects unreachable extra packages. Only amd64/all
packages and the explicitly supported relationship syntax are accepted.
The version and relationship rules follow
[Debian Policy 5.6.12](https://www.debian.org/doc/debian-policy/ch-controlfields.html#version)
and [chapter 7](https://www.debian.org/doc/debian-policy/ch-relationships.html).
`:any` requires `Multi-Arch: allowed`; no cross-architecture installation is
attempted. Recommends/Suggests, maintainer-script execution/order, pre-dependency
configuration order and file collisions are **not** checked. Consequently the
result explicitly keeps `installable_verified:false` and `deployable:false`.
This is not a replacement apt solver and is not the complete guest rootfs or
mkosi build-tool closure.

## Repository authentication

Public metadata was obtained on October 20, 2026 without credentials. Each
InRelease was checked with `gpgv` against a dedicated single-vendor keyring;
the exact `VALIDSIG` fingerprint was checked before using its SHA-256 table:

| Repository | Signing fingerprint | Release date |
| --- | --- | --- |
| Ubuntu noble main/universe | `F6ECB3762474EDA9D21B7022871920D1991BC93C` | April 25, 2024 15:10:33 UTC |
| Ubuntu noble-updates main/universe | same Ubuntu key | October 20, 2026 12:44:43 UTC |
| Ubuntu noble-security main/universe | same Ubuntu key | October 20, 2026 14:45:24 UTC |
| NVIDIA Ubuntu 24.04 x86_64 | `EB693B3035CD5710E231E123A4B469963BF863CC` | October 19, 2026 08:03:00 UTC |

The Ubuntu fingerprint is documented in
[Canonical's archive-key reference](https://ubuntu.com/chisel/docs/v1.3.0/reference/security/).
NVIDIA's [repository management source](https://github.com/NVIDIA/cuda-repo-management)
identifies the Ubuntu 24.04 `3bf863cc` key;
[the public key](https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/3bf863cc.pub)
was inspected for the complete fingerprint above. The lock records each
InRelease hash, verified Release hash, index hash/size and official URL.
Ubuntu indexes were fetched through their hash-addressed `by-hash/SHA256/`
locations. The NVIDIA Packages bytes matched the signed SHA-256 table.
The NVIDIA Release had no Valid-Until field: this is a dated observation, not
a freshness guarantee.

The offline checker trusts the committed, reviewed index hashes; it does not
repeat OpenPGP verification or discover new versions. Updating a hash requires
fresh authenticated metadata and review, not accepting a changed download.

## Nonexecuting source and package inspection

Official tag `595.91.07` resolves through annotated tag
`26d82922dc1e444f274294505f73f1c0b8ca682d` to commit
`9f087a6d4e86d85acc0ce1d354d6276fe5047b29`. This is an official repository
reference and byte correspondence, not an independently verified Git tag
signature. The authenticated package archives for source-open, dkms-open,
kernel-common and persistenced were downloaded and hash-checked before
read-only ar/tar inspection; no maintainer scripts or binaries were executed.

Two package files match the immutable upstream source byte for byte:

| Source at that commit | Bytes | SHA-256 |
| --- | ---: | --- |
| [kernel-open/nvidia/internal_crypt_lib.h](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/9f087a6d4e86d85acc0ce1d354d6276fe5047b29/kernel-open/nvidia/internal_crypt_lib.h) | 7,467 | `f877669b7ffa91518beca9c9bd666f331c5e1ab406818ec2bb173dddd6680e0f` |
| [kernel-open/conftest.sh](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/9f087a6d4e86d85acc0ce1d354d6276fe5047b29/kernel-open/conftest.sh) | 207,249 | `98b43f5a513c8f738ba99531f1d07fa914c8eaad3e57dede1cb0c269824de0d0` |

The crypto header's nine configuration prerequisites are present in the
already-audited kernel config: AEAD, AKCIPHER, SKCIPHER, HASH, HMAC, RSA and
X509_CERTIFICATE_PARSER are `y`; ECDH and ECDSA are `m`. The header additionally
requires successful API conftests: `NV_CRYPTO_PRESENT`, and either the older
akcipher-verify API or both the crypto-sig header and ECC byte conversion API.
The upstream conftest itself notes that its crypto test is incomplete.
Configuration presence alone therefore does not prove `USE_LKCA` or a working
driver. No conftest was executed.

Read-only inspection also found the old `crypto_akcipher_verify` inline API in
the audited common headers' `include/crypto/akcipher.h` (15,576 bytes,
SHA-256 `f043c81b9c8fff052aa95fd11b874d1ed25a77dd98e32ac34202cd0696455803`).
The generic headers' `Module.symvers` contains the GPL export for
`crypto_alloc_akcipher` (the complete file is 2,214,587 bytes, SHA-256
`8e7eb5798789b9610c324b3d9b13373d88c30ae17bb9e41e622efd05e0277a87`).
These are source/symbol observations, not a successful compile or module load.

Three inspected packaged files affect the eventual recipe:

| Package member | Bytes | SHA-256 |
| --- | ---: | --- |
| `usr/src/nvidia-595.91.07/dkms.conf` | 702 | `ecc14df50b16604c446e9e5b8609dd60c5551dc5937d711aa782aea87b6ff217` |
| `usr/share/initramfs-tools/hooks/framebuffer-nvidia` | 906 | `81b01f63c4d6adc02531905c33fc9a32dce97a0c168f18a2f4b73ee803b91ca5` |
| `usr/lib/systemd/system/nvidia-persistenced.service` | 1,501 | `b64683f07b5c273de57503215a0a2d86bb887b5966f7781559a533c6f9a5130a` |

The DKMS file enables autoinstall, chooses host CPU-count parallelism and sets
`IGNORE_CC_MISMATCH=1`. The
[upstream README](https://github.com/NVIDIA/open-gpu-kernel-modules/blob/9f087a6d4e86d85acc0ce1d354d6276fe5047b29/README.md)
instead requires the kernel-interface toolchain to match the kernel's.
The future build must explicitly bound jobs and verify the compiler, not treat
that ignore setting as compatibility evidence. The initramfs hook includes
NVIDIA modules but does not establish the required LKCA preload ordering.
The sample persistence unit has no explicit UVM-persistence argument; it is
not proof of the CC-required persistence state.

The two Ubuntu 24.04 attestation archives were separately hash-checked against
the same signed index. Static ELF inspection, without loading either binary,
found:

| Package file | Bytes | SHA-256 |
| --- | ---: | --- |
| `usr/bin/nvattest` | 1,128,696 | `9c269a22c3f4710cdb115ce21d72c1c3b8e9c9e2db9cf4a391b510ed1a1dea42` |
| `usr/lib/x86_64-linux-gnu/libnvat.so.2.0.0` | 26,794,312 | `e757a59f8241830553da3f99f717ddd44d47973ca756a4fa3feac78012dce896` |

Their DT_NEEDED entries include `libnvat.so.2`, C++/GCC runtimes, zlib and glibc
libraries/loader. The index's missing libnvat Depends field is not evidence
that the library is self-contained. Do not remove runtime libraries merely
because they arrived through build-tool dependencies. No dynamic linking,
symbol-version compatibility or appraisal was tested. These Ubuntu 24.04
bytes are distinct from the earlier Ubuntu 26.04 lab packages.

## Reproduction and next build boundary

Place the seven exact public index files under the `indexes[].file` names in
a private local directory. Then run:

```sh
python3 -I scripts/guest_package_inputs.py --metadata "$NIB_PACKAGE_METADATA"
python3 -I scripts/test_guest_package_inputs.py
```

The first command reads only the committed lock and local files. It verifies
index hashes before parsing, compares every selected record and returns a
content-free count/byte summary plus lock hash. It rejects symlinks, hardlinks,
special files, changed reads and mismatches. Limits are eight indexes, 32 MiB
per compressed input, 128 MiB per decompressed index, four XZ streams, 200,000
records per index and 512 selected packages. The
[XZ 1.2.1 format](https://tukaani.org/xz/xz-file-format-1.2.1.txt)
allows the second empty stream present in Ubuntu's base indexes; arbitrary
trailing data is rejected.

The next executable build step must use these exact inputs in a separately
pinned Linux/mkosi tools tree, verify every downloaded archive, and obtain a
matching offline apt installation plan. It must add and pin the chosen
initramfs/UKI/verity, systemd and guest-service packages rather than silently
calling this driver subgraph a complete image. Review the maintainer scripts
and service activation policy before installation. Then compile against the
audited headers with the matching toolchain and inspect the actual conftest
results/module dependencies before an image boot. None of those operations,
GSP/board firmware qualification, two-clean-build reproducibility, Intel TDX
attestation or GPU runtime qualification happened in this task.
