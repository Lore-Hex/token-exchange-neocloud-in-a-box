# P3.T1 mkosi build-input checkpoint

Status: **nonexecuting preparation implemented**. This extends the
[APT rehearsal inputs](p3-t1-linux-tools-simulation.md), not its execution policy.
No package payload, maintainer script, mkosi program or container is run by this
preparer. A successful receipt means the source and archive bytes were checked;
it is not installability, reproducibility, boot, LKCA or hardware evidence.

## Pinned source and tools

The [source manifest](../../image/inputs/mkosi-27.1.json) binds mkosi 27.1 to
commit `ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b`. Its
[public archive](https://codeload.github.com/systemd/mkosi/tar.gz/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b)
is 473,225 bytes, SHA-256
`3d38e848f9b1108ec76d873f2c378dfc7d4b536ad89deffe8c97504f7a8dff43`.
The complete 325-file/symlink source set totals 1,616,290 bytes. Each entry has
an exact Git blob ID, SHA-256, size and Git mode; the original archive and
license files remain intact. The checker reads the archive in memory and never
extracts, imports, compiles or executes upstream code.

### Local service-denial correction

The [local LGPL-2.1-or-later patch](../../image/patches/README.md) makes the
installer's denial file executable before every APT install transaction. Pinned
upstream `mkosi/installer/apt.py:259-274` writes `policy-rc.d` under a `0644`
umask, then removes it after installation. Pinned `init-system-helpers`
`1.66ubuntu1` checks executability before using it: data members
`usr/sbin/invoke-rc.d:138-140` and `usr/bin/deb-systemd-invoke:71-86` in the
archive bound by the [guest lock](../../image/inputs/r595-ubuntu2404.json), SHA-256
`8eb801f9ce084dab9bf4cbc3ec0ee5ac75516a4cc1f25e3f92298c632e75b700`.
The finding is from source inspection, not an observed service start.

A one-time executable skeleton file is insufficient: essential-package
installation in `mkosi/distribution/debian.py:213-218` removes it, and later
requested-package installation at `mkosi/__init__.py:299-300` recreates it.
The local insertion calls `policyrcd.chmod(0o755)` after the unchanged deny-101
body is written and before APT invocation. Failed chmod aborts before invocation.
Success cleanup remains unchanged; no new exception handling is introduced.

The preparer binds original/patch/result bytes using the
[patch manifest](../../image/inputs/mkosi-27.1-service-denial.json), requires the
exact hunk position and context, and emits only the changed file in
`mkosi-overlay/mkosi/installer/apt.py`. The original archive, licenses and source
manifest are retained unchanged. The receipt records in-memory application,
not execution or build authorization. Synthetic tests execute only a reviewed
fixture with fake APT invocation; they cover absent/prior `0644` files, repeat
transactions, failure/retry and integrity rejection. No upstream module or
package program is executed by these tests.

The pinned [pyproject](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/pyproject.toml)
requires Python >=3.9 and declares `pefile` for bootable-image support. Source
preparation does not invoke setuptools, pip, a resolver, or Ubuntu's older mkosi.
The [tools lock](../../image/inputs/mkosi-tools-ubuntu2404.json) selects the
upstream [common tools](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/resources/mkosi-tools/mkosi.conf),
[Debian/Ubuntu tools](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/resources/mkosi-tools/mkosi.conf.d/debian-kali-ubuntu/mkosi.conf)
and their applicable Ubuntu noble, x86-64/UEFI conditionals, plus explicit dpkg
and bubblewrap. It excludes upstream debug/development/QEMU additions.

The selected graph has **203 packages / 76,004,920 archive bytes**, including
all declared Depends/Pre-Depends and Essential packages. The upstream requested
virtual package `systemd-repart` is supplied by the exact selected `systemd`
package's `Provides`; that mapping is checked, not silently dropped.
The six Ubuntu indexes and their signed-Release bindings are the previously
authenticated [snapshot](p3-t1-r595-package-inputs.md). This is static graph
coverage, not a claim that APT has accepted this expanded tools set or that it
is the newest available software.

| Component | Exact selected version |
| --- | --- |
| Python runtime | `3.12.3-1ubuntu0.17` |
| APT | `2.8.3` |
| systemd, systemd-boot, systemd-ukify | `255.4-1ubuntu8.18` |
| python3-pefile | `2023.2.7-3` |
| bubblewrap | `0.9.0-1ubuntu0.3` |
| libcryptsetup12 | `2:2.7.0-1ubuntu4.2` |
| dosfstools / e2fsprogs | `4.2-1.1build1` / `1.47.0-2.4~exp1ubuntu4.1` |
| erofs-utils / squashfs-tools | `1.7.1-1build2` / `1:4.6.1-1build1` |
| xfsprogs / sbsigntool | `6.6.0-1ubuntu2.1` / `0.9.4-3.1ubuntu7` |
| guest GCC / DKMS | `13.3.0-6ubuntu2~24.04.1` / `1:3.4.3-1ubuntu1` |
| guest kernel headers / NVIDIA DKMS | `6.8.0-147.147` / `595.91.07-1ubuntu1` |

The existing 145-package guest lock is unchanged. Its union with the tools
lock is **269 unique archives / 516,283,494 bytes**. Duplicate package names must
have identical locked records. The preparer checks every selected archive and
its control metadata, producing hashes for 1,175 control members, including
326 maintainer/config/trigger members. These are inspection counts, not execution
or a completed security audit.

## Run preparation, not installation

Use a committed producer, physical absolute paths, the seven previously pinned
index files and the public mkosi archive. The shared archive cache may include
other packages; only exact `<SHA256>.deb` names selected by these locks are read.
Selected inputs must be bounded, stable, single-link regular files opened
without following symlinks or blocking on FIFOs. No network fetch is implemented.

```sh
python3 -I scripts/prepare_mkosi_inputs.py \
  --metadata "$NIB_PACKAGE_METADATA" \
  --archives "$NIB_PACKAGE_ARCHIVES" \
  --mkosi-source "$NIB_MKOSI_SOURCE_ARCHIVE" \
  --output "$NIB_MKOSI_INPUT_BUNDLE"

# Reconstruct all expected bytes from the same committed producer and inputs.
python3 -I scripts/prepare_mkosi_inputs.py \
  --metadata "$NIB_PACKAGE_METADATA" \
  --archives "$NIB_PACKAGE_ARCHIVES" \
  --mkosi-source "$NIB_MKOSI_SOURCE_ARCHIVE" \
  --verify "$NIB_MKOSI_INPUT_BUNDLE"
```

Output is owner-only, no-overwrite, with `receipt.json` written last. It contains
the original mkosi archive, both package locks, exact local repository/index
bytes, the existing **nonbootable** candidate configuration, a hashed control
member inventory, the exact local patch/pin/overlay and a build prerequisite
contract. Verification recomputes
every expected output byte and rejects extra/missing/changed files.
Git reads disable replacement objects, lazy fetching, network protocols and
prompts; the producer and all loaded repository helpers must match the commit.

Finite preparation limits: 8 MiB compressed/expanded source, 512 source blobs,
1,024 archive entries, 256 tools packages/128 MiB tools archives, 320 union
packages, 640 MiB total bundle and one 600-second archive loop. Existing
per-package/control/index bounds remain in force. These are parser limits, not
an OS memory sandbox; host Python, Git and bounded host zstd remain trusted.

## Exact blockers before installation

1. The matching guest APT rehearsal must pass. It cannot establish expanded
   tools runtime linkage or maintainer-script safety. The expanded tools graph
   also needs a real APT plan and Linux tools-runtime check before it is used.
2. Review all relevant maintainer scripts and triggered hooks before executing
   them. The pinned NVIDIA postinst reads `uname -r`, considers the newest
   `/boot/config-*`, and invokes DKMS; kernel postinst runs depmod and kernel
   hooks. The package source bytes are pinned, but they are not yet an approved
   installation program.
3. Constrain the explicit target kernel, GCC and compiler parallelism. Static
   inspection of the selected DKMS `common.postinst` confirms the
   `/etc/dkms/no-autoinstall` guard, but that is not proof that every kernel hook
   is suppressed. NVIDIA's `dkms.conf` uses quoted `make`, `-j$(nproc)` and
   `IGNORE_CC_MISMATCH=1`; do not assume a generic job limit or matching compiler
   evidence. No module compilation or LKCA conftest has occurred.
4. Review a Linux namespace/mount policy for mkosi's pinned
   [sandbox implementation](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/sandbox.py).
   It uses namespace/mount syscalls itself. PR87's unprivileged APT container
   policy is not asserted sufficient, and no `--privileged` workaround is given.
   Upstream [build ordering](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/__init__.py)
   installs skeleton trees before distribution installation, but later scripts
   do not replace pre-install controls. `WithNetwork=no` is not a universal
   process network boundary.
   A future runner must verify and use the local service-denial overlay for
   every APT install transaction; merely verifying the original archive is not
   enough. This patch does not replace outer service, PID or network isolation.
5. Only after those checks, source review and CI, add a bounded installation
   runner with two fresh workspaces, fixed epoch, no shared incremental cache,
   actual package/configuration checks and independent cleanup. The contract's
   1,800 seconds/build, 8 GiB, two CPUs and 256 PIDs are **proposed limits**, not
   runtime-validated sizing or permission to execute.

UKI/verity generation, two-build byte identity, release/debug measurement
separation, actual boot, TDX/configfs-tsm, R595/LKCA/UVM and GPU attestation remain
unproven. No image is built or published and no PLAN gate is changed.
