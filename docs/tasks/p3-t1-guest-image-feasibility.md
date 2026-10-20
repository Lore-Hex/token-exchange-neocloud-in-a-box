# P3.T1 guest-image prerequisite audit

Status: **source research only; build implementation blocked on exact inputs**.
Reviewed 2026-10-20. No guest image was built, booted, signed, imported or
published. No software was installed and no vendor setup script was executed.
This document changes neither [PLAN](../../PLAN.md) nor the
[human gates](../gates.md). It does not satisfy P3.T1's two-clean-build acceptance
criterion or establish the H100 quota required to start P3.

The [image directory](../../image/README.md) has no build configuration or image
lock. The existing [measured-image decision](../decisions/0004-measured-guest-image.md)
remains the contract: authenticated UKI/root selection, verity-protected content,
distinct release/debug measurements, and no mutable executable overlay.

## Verified source facts

These are external source observations, not our hardware measurements or an
approved combination of components.

| Component | Exact reference inspected | Meaning and limit |
| --- | --- | --- |
| mkosi | v27.1, commit `ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b` | Suitable configuration primitives exist; no build-tool tree or resulting image is pinned. |
| Canonical TDX example | commit `a599cf54510ec81667cedfb183e4da9f96be536d` | Documents guest preparation, but uses mutable package resolution and development defaults. Not our release recipe. |
| NVIDIA trusted-computing reference | R595 TRD1, RN-12817-001_v02, April 2026 | Driver 595.58.03 and CUDA 13.2 are a published reference pairing, not our complete package closure. |
| NVIDIA appraisal source | SDK release 2026.10.02, commit `11b2bfeb42a1158aa08744603ecb239ccb3d2265` | Existing source review remains useful; a source revision is not proof of package-to-source reproducibility. |
| NVIDIA Ubuntu guest packages | Ubuntu 24.04 amd64 index, observed 2026-10-20 | nvattest/libnvat 2.0.0.1790976895-1 exist, with different archive hashes from the lab's Ubuntu 26.04 packages. |

### mkosi

The [v27.1 release](https://github.com/systemd/mkosi/releases/tag/v27.1)
resolves through annotated tag object
`9bb5d145c8d8df700c0455c234fa8d51c1fab1ea` to the commit above. Pin the commit,
not merely the tag. The
[manual at that commit](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/resources/man/mkosi.1.md)
documents:

- `Bootable=yes` fails if required boot components are absent; `auto` need not.
- Explicit `UnifiedKernelImages` modes can require a UKI rather than silently
  falling back to a different boot entry.
- `ToolsTree` can fix the tools used during the build; its automatic defaults
  do not select an immutable tool tree for us.
- `SourceDateEpoch` supports reproducibility but does not by itself prove it.
- `CacheOnly=always` restricts the package manager to cached inputs. This is not
  a general network sandbox for build scripts.
- `Verity` has output-type-specific behavior. A disk/rootfs partition definition,
  authenticated root-hash selection and initrd policy still need review; setting
  one option is not proof that the booted root is protected.

These are capability checks, **not a proposed runnable configuration**. The
Linux build-tool versions, package cache, signing policy and partition layout
are not selected. In particular, signing or automatic key enrollment must not
be enabled by copying defaults from an example.

### Ubuntu TDX and GPU initialization

Canonical's
[guest setup source](https://github.com/canonical/tdx/blob/a599cf54510ec81667cedfb183e4da9f96be536d/setup-tdx-guest.sh)
selects an unversioned generic kernel package by default, supports an optional
Intel kernel, and resolves matching extra modules through apt. Its
[configuration](https://github.com/canonical/tdx/blob/a599cf54510ec81667cedfb183e4da9f96be536d/setup-tdx-config)
does not supply our exact kernel release or package hashes. It also supplies
development login defaults, which must not enter a release image.

Do not run or copy that setup script as the image recipe:

- The optional H100 branch installs driver 570 and CUDA 12.8, not PLAN's R595
  reference stack.
- It installs a service that sets GPU readiness without first appraising
  evidence. This conflicts with our attestation-gated readiness contract.
- Repository addition and system upgrade are mutable operations, not an
  immutable package closure.

Google's
[confidential GPU guest guide](https://docs.cloud.google.com/confidential-computing/confidential-vm/docs/create-a-confidential-vm-instance-with-gpu)
recommends open kernel-module drivers 580 or later, LKCA initialization and UVM
persistence. PLAN describes the guide's version floor as a requirement; the
current guide phrases it as a recommendation. Retaining the intended R595
stack does not prove compatibility. Google's LKCA example loads the ECDSA/ECDH
modules before NVIDIA and updates the initramfs. We still need the selected
kernel's actual configuration/modules, correct initialization order, and a
reviewed persistence service.

The [R595 release notes](https://docs.nvidia.com/595trd1-trusted-computing-solutions-release-notes.pdf#page=4)
give the reference driver/CUDA pairing. They do not establish that our future
UKI, kernel, driver userspace and initrd form a validated guest. See the existing
[hardware source matrix](../hardware.md) for the host/guest distinctions.
Neither a vendor setup example nor a previous SNP lab run proves one image
works on both GCP TDX and bare-metal TDX.

### nvattest guest artifacts

The existing [lab appraisal helper](../../host/attest-lab-gpus.py) intentionally
pins Ubuntu 26.04 artifacts. Do not change that historical contract or substitute
its package hashes into a 24.04 image lock.

The public
[NVIDIA Ubuntu 24.04 amd64 index](https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/Packages)
advertised these distinct artifacts:

| Package, version 2.0.0.1790976895-1 | Bytes | Published SHA-256 |
| --- | ---: | --- |
| `nvattest_2.0.0.1790976895-1_amd64.deb` | 321042 | `77eaec7644b6a221147245a1c0643bac7317265815e3483ef12f283fc91badb0` |
| `libnvat_2.0.0.1790976895-1_amd64.deb` | 6152132 | `d6ae20d7f0d90c62c5e99bcac29cb66f3d9e18c7ee2c4de3aee40134edd70353` |

The index declares a minimum libnvat dependency for nvattest and no `Depends`
field for this libnvat version. Absence of that field is not proof of a
self-contained binary or guest ABI compatibility.

This audit fetched only the public index over HTTPS, not those package
archives. It did not verify apt repository signatures, inspect package payloads,
run either binary or demonstrate correspondence to
[SDK source](https://github.com/NVIDIA/attestation-sdk/tree/11b2bfeb42a1158aa08744603ecb239ccb3d2265).
The table is therefore **candidate metadata, not an authenticated build lock**.
The current lab's install/appraisal/readiness results remain separate from guest
image preparation and do not authorize a boot-time readiness shortcut.

## Bounded next implementation

Do not add a build wrapper that substitutes host defaults or treats partial pins
as success. First obtain an exact, authenticated input set for:

1. The Ubuntu 24.04 kernel, configuration, headers, modules and TDX/configfs-tsm
   support, plus the matching initrd contents.
2. R595 open driver, userspace/firmware and LKCA dependencies for that kernel;
   review the persistence/appraisal ordering without a blind readiness setter.
3. The Ubuntu base and complete dependency closure, including podman, systemd,
   UKI/verity tools and the 24.04 appraisal packages. Include repository
   authentication and payload hashes, not only top-level package versions.
4. The Linux build-tool tree and mkosi source, plus a fixed build timestamp and
   explicit offline build boundary.
5. Exact guest application binaries and configuration, release/debug contents,
   root partitions, UKI/root-hash binding and signing inputs. Existing component
   proofs are not a completed guest-image integration.

With those inputs selected, a useful next software increment is a bounded,
read-only offline input verifier followed by an explicit mkosi configuration:
reject missing, extra, changed or ambiguous inputs; never resolve packages or
fall back to host tools during verification. Test the verifier with corrupted,
missing and unsafe-path fixtures. Only then add two clean, isolated builds and
compare UKI/verity outputs. Boot, TDX measurements, GPU appraisal, GCP custom-image
support and publication remain separate gated work.

No executable preflight is added in this audit because the required input set
is not yet known. This avoids an always-blocked scaffold or a misleading
partial-success result.

## Source-byte observations

These hashes identify the public bytes read for this source audit, not build
artifacts or accepted guest measurements. No upstream implementation is vendored.
The mutable package index may change; its digest records this observation only.

| Source | SHA-256 |
| --- | --- |
| mkosi manual at the commit above | `6344028a599ad9dc354711a5bfd328abb1d0d0a81fe8e0defa248527da537d0c` |
| Canonical `setup-tdx-config` at the commit above | `e19dffac8aa64ca23727183aabd66c8c715a60cd416ad1476e617f7a188aeb30` |
| Canonical `setup-tdx-guest.sh` at the commit above | `ff5b3fed0942e3e959a152ff5fb8dea63f55ee5f287d0780d38f9ab8a95883e7` |
| NVIDIA Ubuntu 24.04 index, 8045582 bytes | `4454ee49760311c655999beec127aeea30a2619c86ce4369e3dd14e01295499a` |
