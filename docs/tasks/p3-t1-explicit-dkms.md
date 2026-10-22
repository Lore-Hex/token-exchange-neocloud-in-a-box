# Explicit Offline Driver Compilation

The native build workflow has an opt-in `compile_driver` boolean, off by default.
It first performs the same 145-package installation and exact configured-state
check as the installation-only run. It then verifies the installed build
controls, the reviewed DKMS script and kernel `compile.h`, and GCC's reported
version before invoking explicit `dkms add` and `dkms build` for NVIDIA
`595.91.07`, kernel `6.8.0-147-generic`, architecture `x86_64`.

This is a compile experiment, not a deployable image. It does not install or load
modules, boot a kernel, create a UKI or verity image, prove LKCA inclusion, or
qualify confidential hardware. Those report fields remain false even if all
five module hashes are produced. The package guard remains installed; no
`autoinstall`, signing-policy override, compiler-ignore flag, or host-kernel
target is introduced.

## Isolation and Evidence

The existing main-only manual workflow, immutable input hashes, private
network/PID/mount namespaces, 8 GiB memory limit, two CPU quota, 256-task limit,
final capability allowlist, no-new-privileges and seccomp policy are unchanged.
The nested sandbox exposes only the private guest root writable, minimal device
nodes and the existing private proc view. No host GPU devices are exposed. A
fixed Python worker checks confinement before chroot and executes only one of
three fixed commands with a clean environment.

Compiler and add operations have 30-second limits; build has a 1,500-second
limit, still inside the existing overall deadline. Each output stream is limited
to 2 MiB. Only fixed-path, regular, non-symlink, bounded make logs are collected.
No exception text is published. On failure the build remains failed and the
outer process-group/cgroup cleanup still runs.

Five uncompressed ELF64 little-endian relocatable x86-64 modules are required:
`nvidia`, `nvidia-modeset`, `nvidia-drm`, `nvidia-uvm`, and `nvidia-peermem`.
Their byte lengths and SHA-256 values are recorded, not treated as proof that
they can load or implement confidential-computing functionality.

## Reviewed Sources

Read October 21, 2026 from the authenticated archives in the
[guest and kernel lock](../../image/inputs/r595-ubuntu2404.json), using the
[existing build controls](p3-t1-dkms-build-controls.md):

- DKMS `3.4.3`, `usr/sbin/dkms`: SHA-256
  `a7a45df4e9f8a4fbea258a54b31d21760121560fe0b6187e34d5b39944e2767b`.
  Lines 344-354 select compressed module suffixes from existing target modules.
  Lines 1664-1683 fall back to the uncompressed module when compression fails.
  The pinned guest has `libzstd1` but no `zstd` executable; the tools tree's
  `zstd` is outside the chroot. The inspected kernel configuration uses ZSTD.
  No compressor or compression-policy change is added by this experiment.
- DKMS lines 1700-1711 finalize output, save `log/make.log`, and remove the
  temporary build directory. Successful builds therefore do not retain
  conftest headers; separate LKCA evidence is still required.
- Kernel `6.8.0-147.147` generated `compile.h`: SHA-256
  `aeebd7e60ca6d32c1ea7b4823d42a33fd5fdb455d19c957e8075e6c3d4b0613b`.
  It names GCC `13.3.0-6ubuntu2~24.04.1` and binutils `2.42`, matching the pinned
  guest compiler. The independent hash check prevents NVIDIA's absent-header
  compiler-check bypass from being mistaken for compatibility.
- The pinned mkosi 27.1 source
  [`ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b`](https://github.com/systemd/mkosi/tree/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b)
  supplies the same namespace helper already used by package installation.

## Validation Status

Unit tests cover exact commands, explicit opt-in/default stability, compiler and
source mismatch, missing modules, wrong ELF type, bounded output, build failures,
make-log allowlisting and file/link rejection. They do not execute a compiler.
An actual successful compile requires original native workflow evidence from a
committed producer; this document does not claim such a result.
