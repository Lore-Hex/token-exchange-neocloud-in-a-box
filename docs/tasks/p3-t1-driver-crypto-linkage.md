# P3.T1: Driver Crypto Linkage Check

The explicit offline driver build now also inspects strong undefined ELF symbols
in its `nvidia.ko`. It requires `crypto_alloc_aead`, `crypto_alloc_akcipher` and
`crypto_alloc_shash`, using the pinned guest's `x86_64-linux-gnu-nm`. Module hashes
must be identical before and after inspection. Missing, weak, duplicate or
malformed references, diagnostics on stderr, tool failure and timeouts fail the
build. No module is installed or loaded.

This distinguishes linked kernel crypto references from byte strings in a file
or the driver's no-LKCA stub branches. It is **not** proof that all conftests
passed, runtime crypto algorithms are available, the modules load, or confidential
inference works. `lkca_verified`, `loaded`, `bootable` and reproducibility remain
false; the separate `crypto_linkage_verified` field describes only this check.

## Primary Sources

Inspected October 22, 2026 from the authenticated R595.91.07 source package in
the [guest lock](../../image/inputs/r595-ubuntu2404.json), matching NVIDIA commit
[`9f087a6d4e86d85acc0ce1d354d6276fe5047b29`](https://github.com/NVIDIA/open-gpu-kernel-modules/tree/9f087a6d4e86d85acc0ce1d354d6276fe5047b29):

- `kernel-open/nvidia/internal_crypt_lib.h` selects `USE_LKCA` using kernel
  configuration and generated conftests. SHA-256
  `f877669b7ffa91518beca9c9bd666f331c5e1ab406818ec2bb173dddd6680e0f`.
- `libspdm_aead.c`, `libspdm_ecc.c` and `libspdm_shash.c` call the respective
  required crypto allocators only on their LKCA branches. Their exact hashes
  are checked before compilation; no source modification is made.
- [GNU binutils 2.42 nm](https://sourceware.org/binutils/docs-2.42/binutils/nm.html)
  documents `--undefined-only`, POSIX output and the strong undefined `U` type.
  The guest's pinned `2.42-4ubuntu2.10` binary SHA-256 is
  `3708df3d7ce16e2c1dcdcd722784ae382d4d582276a71ab7bbe2d542f5621202`.

The inspection uses the existing private guest chroot and unchanged final
capability/seccomp boundary, with a 30-second timeout and bounded output. It
requires no network, GPU, host kernel access or new dependency. The native build
completed a real native build on October 22, 2026. The original
[run](../../evidence/p3/2026-10-22-crypto-linkage-pass/run.json),
[result](../../evidence/p3/2026-10-22-crypto-linkage-pass/result.json) and
[resource cleanup](../../evidence/p3/2026-10-22-crypto-linkage-pass/evidence-resources.json)
receipts come from producer `6bf7880b2c5fa59299c0324cc79107803a2c6b5f`,
[workflow 39701625092](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39701625092).
All three strong references passed, five modules were compiled, 145 packages
were configured, no OOM was reported and the private payload/cgroup were removed.

This run exposed a diagnostic-export omission: the worker checked the symbol
table, but the parent collector's explicit file list did not copy
`dkms-symbols.stdout` or `.stderr` before deleting the payload. Those original
bytes are unavailable and are not reconstructed here. The collector now
includes those two names, preserving the existing 2 MiB bound and rejecting
symlinks; unknown files are still excluded. A subsequent native run is required
to demonstrate the corrected end-to-end export. Unit tests use synthetic
symbol tables and mocked commands, not a compiler or a real module.
