# P3.T1: Native Driver Installation Pass

Status: **compiled and installed in a private offline guest root**, not loaded,
booted or confidentially deployed. Original failed attempts remain unchanged.

[Native workflow 39717604735](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39717604735)
ran merged source `3987926d4e13e2312fbe468ae992fc060b8272ac` on an Ubuntu 22.04
GitHub runner without GPUs. The bounded build ran on October 22, 2026,
03:39:58-03:42:42 UTC, after the authenticated input fetch passed in 48 seconds.
Both `compile_driver` and `install_driver` were explicitly enabled.

The [original evidence](../../evidence/p3/2026-10-22-driver-install-pass/run.json)
and [result](../../evidence/p3/2026-10-22-driver-install-pass/result.json) establish:

- All 145 exact package versions reached the configured state.
- NVIDIA 595.91.07 compiled for kernel 6.8.0-147 with GCC 13.3.0.
- All five installed ELF modules matched their compiled objects byte for byte.
  The same module hashes were observed in the earlier clean compilation-only
  run; this does not establish reproducibility of a complete image.
- The raw symbol table retains strong references to `crypto_alloc_aead`,
  `crypto_alloc_akcipher` and `crypto_alloc_shash`.
- Each installed module appeared once in `modules.dep`; that file and the
  three required binary indexes were nonempty, bounded and hashed.
- The resource supervisor independently confirmed no OOM, zero remaining
  processes, removed cgroup and removed private build payload. Peak memory was
  4,665,442,304 bytes under the 8 GiB limit; swap remained disabled.

`output.log` is an unchanged copy of the original `dkms-install.stdout`.
The original raw symbol table, build stderr and resource receipt are also
retained. Build stderr contains five `zstd: command not found` warnings: this
candidate's DKMS path produced the uncompressed `.ko` files that the installer
explicitly validates. These warnings are not erased or presented as an
error-free log. No module compression or signing claim is made.

## Verification

```sh
python3 -B -m unittest discover -s scripts -p 'test_driver_install_evidence.py'
python3 -B -m unittest discover -s scripts -p 'test_mkosi_dkms.py'
make check
```

Regression tests pin every retained original, verify installed/source module
hash correspondence, and require the unproven flags to remain false. The
[installation contract](p3-t1-driver-install.md) still rejects unknown DKMS
configuration, missing or changed pins and unsafe filesystem layouts. The
[omitted-package-config failure](p3-t1-dkms-package-config.md) is preserved,
not replaced by this success.

## Remaining Work

This proves the isolated driver installation step only. The guest runtime
dependency set and real service backends, authenticated UKI/verity image,
two clean full-image builds, boot testing, runtime LKCA, CPU/GPU appraisal and
TLS/workload binding remain required. The installed files were intentionally
removed after verification; no boot image was exported or deployed. P3 and
the production confidential-service acceptance criteria remain incomplete.
