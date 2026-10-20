# P3.T1 pinned Linux APT simulation

Status: **implemented preparation and fake-tested execution contract**. No
packaged program, maintainer script or container has been executed by this task.
This advances the [prepared guest inputs](p3-t1-offline-package-preparation.md)
to a runnable, narrowly scoped installation-plan rehearsal. It does not install
packages, build the guest with mkosi, compile LKCA, boot a guest, or change any
phase gate.

## Exact inputs

The [tools lock](../../image/inputs/apt-tools-ubuntu2404.json) selects 98 Ubuntu
24.04 amd64/all packages, 34,180,466 archive bytes, from the same six authenticated
Ubuntu indexes as the existing kernel/R595 audit. Every package has an exact
version, size, archive hash and signed-index binding. No floating OCI base image
is selected. The tools filesystem is assembled from those archive bytes.

| Tool | Exact selected package version |
| --- | --- |
| apt, apt-utils, libapt-pkg6.0t64 | `2.8.3` |
| dpkg | `1.22.6ubuntu6.6` |
| python3.12 and its runtime libraries | `3.12.3-1ubuntu0.17` |
| python3 metapackage | `3.12.3-0ubuntu2.1` |
| bubblewrap | `0.9.0-1ubuntu0.3` |
| xz-utils | `5.6.1+really5.4.5-1ubuntu0.3` |
| zstd | `1.5.5+dfsg2-2build1.1` |

The lock covers declared Depends/Pre-Depends, alternatives and Essential
packages. That static coverage is not APT resolution. Snapshot authentication
is the [recorded metadata audit](p3-t1-r595-package-inputs.md), not a claim that
the dated snapshot is the latest available software.

The existing mkosi candidate remains pinned to 27.1, commit
`ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b`. It is **not** installed in this
minimal simulation root. Ubuntu's indexed mkosi 20.2 is not substituted. This
root must not be used as a complete mkosi tools tree: the upstream
[common tools](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/resources/mkosi-tools/mkosi.conf)
and [Debian/Ubuntu tools](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/resources/mkosi-tools/mkosi.conf.d/debian-kali-ubuntu/mkosi.conf)
include additional filesystem, boot and sandbox utilities.

## Nonexecuting preparation

Use absolute, physical paths and a committed producer checkout. Supply the
existing seven metadata files and a private archive cache containing every
tools and guest archive as `<SHA256>.deb`. A shared cache may contain unrelated
files; only exact names from the two committed locks are read. Selected files
must be no-follow regular files with one link and stable identities.

```sh
python3 -I scripts/prepare_guest_tools.py \
  --metadata "$NIB_PACKAGE_METADATA" \
  --archives "$NIB_PACKAGE_ARCHIVES" \
  --output "$NIB_APT_TOOLS_BUNDLE"

# Recompute and compare the complete bundle, without writes.
python3 -I scripts/prepare_guest_tools.py \
  --metadata "$NIB_PACKAGE_METADATA" \
  --archives "$NIB_PACKAGE_ARCHIVES" \
  --verify "$NIB_APT_TOOLS_BUNDLE"
```

The preparer reuses the archive/control verifier and pinned dependency checker.
It reads data payloads as bounded tar members, never with filesystem extraction
or package execution. Merged-/usr paths are resolved in a virtual tree;
traversal, cycles, nonidentical collisions, special files and unknown types
reject. In-archive hard links become independent verified regular bytes.
Ownership becomes root, timestamps are fixed to 2026-10-20 UTC, directories
become 0755 and setuid/setgid/sticky bits are removed from regular files.
These explicit transformations are not a normal package installation.

The three required ELF executables and their declared ELF interpreters must
exist as x86-64 files in that tree. This does not prove dynamic linking or ABI
compatibility. Package-generated files and maintainer behavior have not been
reproduced. Optional dangling data symlinks can remain inert; required executable
and interpreter paths cannot be dangling.

Output is private and no-overwrite:

- `image.tar`: deterministic single-layer Docker archive, not a published image.
- `root-inventory.json`: public package path/type/mode/hash inventory.
- `receipt.json`: producer/helper/lock hashes, archive/config/layer digests,
  executable/interpreter observations and explicit nonqualification fields.

The archive uses `manifest.json` Config/RepoTags/Layers with a raw config and
uncompressed layer DiffID, following the
[immutable Moby loader source](https://github.com/moby/moby/blob/f8215cc266744ef195a50a70d427c345da2acdbb/image/tarexport/load.go)
(Git blob `3713203cd14e1ce484b6b67416f0344698ba8e78`). That is a format reference,
not a pin or execution claim for the operator's Docker binary.
The config, layer and archive hashes are computed from the actual prepared
bytes, not invented here or equated with a Docker daemon's reported `.Id`.

Bounds: 128 tools packages/64 MiB compressed tools archives, 256 MiB expanded
per-package data tar, 64 MiB per regular member, 50,000 virtual entries,
512 MiB total regular data, and a 600-second preparation loop. Shared archive
and index limits remain enforced. Preparation uses bounded memory, not an OS
memory sandbox. Host Python and zstd remain trusted preparation tools.

## Reviewed CPU execution

**This command executes packaged Linux tools in Docker.** Run it only after
exact-head independent review, full tests/CI and coordination with the owner
of the one bounded CPU experiment. Preparation alone does not grant that run.
No Docker build, image pull, cloud resource or GPU is needed.

```sh
python3 -I host/simulate-guest-apt.py --run \
  --metadata "$NIB_PACKAGE_METADATA" \
  --archives "$NIB_PACKAGE_ARCHIVES" \
  --bundle "$NIB_APT_TOOLS_BUNDLE" \
  --output "$NIB_APT_SIMULATION_RESULT"
```

The runner first reconstructs and verifies all bundle bytes from the committed
locks and authenticated archives. It refuses a preexisting generated image tag.
It loads the reviewed archive, checks platform/layer linkage, creates and
inspects the fixed container policy, then starts it. The daemon's `.Id` is not
treated as raw config-byte verification.

The container is linux/amd64, UID/GID 65534, read-only, network-none, no published
ports, GPUs, host mounts or credentials, private IPC, all capabilities dropped,
and no-new-privileges. Limits are 1 GiB RAM with no extra swap, 2 CPUs, 64 PIDs,
and one 128 MiB noexec/nosuid/nodev tmpfs. Docker's init reaps child processes;
the Docker daemon, its init and the host kernel remain trusted, not attested.
See the official [create](https://docs.docker.com/reference/cli/docker/container/create/)
and [image-load](https://docs.docker.com/reference/cli/docker/image/load/) contracts.

Python starts with `-I -S -B`. The shared source-pinned process collector bounds
APT output and process-group lifetime. APT gets a fixed environment, initial
`APT_CONFIG`, private config fragments/cache/lists/logs, and **empty target
status**, not the tools root's package state. Only the verified local file
repository is configured. Local trusted-repository status comes from prior
archive/index verification, not a fabricated new repository signature.

The entrypoint checks APT 2.8.3, indexes that local repository, then invokes
`apt-get --simulate --no-download --no-install-recommends install` with all
145 exact package/version arguments. The
[pinned APT archive](https://archive.ubuntu.com/ubuntu/pool/main/a/apt/apt_2.8.3_amd64.deb)
SHA-256 is `c9ace99efc7726fe0035f921a9acf262a9723bbf35b861aab4f9075a0be67442`;
its `apt-get.8` and `apt.conf.5` specify simulation, config-load ordering and
explicit state paths. Both Inst and Conf records must exactly cover the
requested set; removals, extra versions/packages, incomplete plans, warnings,
nonzero status and malformed output fail closed.

The exact native Ubuntu source archive
[`apt_2.8.3.tar.xz`](https://archive.ubuntu.com/ubuntu/pool/main/a/apt/apt_2.8.3.tar.xz)
is 2,354,680 bytes, SHA-256
`088522b3613b28fdbcfa61f1f7e476bf6dc6b0120a8f74409e9527580c9f9d3b`.
Its authenticated `noble-updates/main/source/Sources.xz` hash is
`56956fdc9dbdf980e52b8c02fb211cced4bf01ffc3cd4773779d2dd2b27783e2`,
bound by the already-audited Release. `apt-pkg/algorithms.cc` SHA-256
`6631395fa897fdb93783a00c3942b3a837a9e3a87c32a6a10d5230f53edfcf6e`
defines `pkgSimulate::Describe`, `RealInstall`, `RealConfigure` and `ShortBreaks`.
The validator accepts their transient unpack-order bracket suffix only for
selected native/all package IDs. It does not accept conflicts, missing final
configuration, unknown IDs or failed APT exit. This source was read, not run.

There are no guest archive payloads in the simulation image and no dpkg install
or maintainer-script invocation. APT simulation cannot test file collisions,
package scripts, DKMS compilation, initramfs creation or boot.

APT has one shared 120-second deadline; output is capped at 2 MiB stdout and
64 KiB stderr. The host bounds attach to 160 seconds and Docker work to
360 seconds, with separately bounded cleanup. SIGINT/SIGTERM initiate cleanup.
Load/create completion uncertainty remains `cleanup=unconfirmed`, even after
an empty listing. Owned-container and image-tag removal are independent and
each absence query must succeed. Abrupt host death can leave stopped resources;
it is not covered by a false cleanup-success claim.

`report.json` contains fixed stages, source/image identities, aggregate
package counts and a hash of the public APT plan; no raw stdout/stderr,
environment, host paths or credentials. This is a software rehearsal report,
not a hardware acceptance record. Even success retains
`installable_verified=false`, `bootable=false`, `deployable=false`.

## Next build boundary

A successful simulation would justify proceeding to a separate reviewed
installation rehearsal: extend the tools lock with the exact upstream mkosi
27.1 source/runtime and boot/filesystem utilities, review the pinned maintainer
scripts, constrain DKMS/compiler jobs and network, then invoke the nonbootable
mkosi candidate in that boundary. Only subsequent actual installation, LKCA
build, UKI/verity generation and boot/attestation evidence can establish those
distinct claims. This task does not change PLAN or any gate.
