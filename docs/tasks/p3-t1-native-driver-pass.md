# P3.T1: Native Driver Compilation Passed

[Run 39697030341, attempt 2](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39697030341/attempts/2)
executed producer `c70b1b7a60ed223ead65e12642fc368838244997` on Linux/amd64
from 00:10:23 to 00:13:41 UTC on October 22, 2026. The original
[run](../../evidence/p3/2026-10-22-native-driver-pass/run.json),
[result](../../evidence/p3/2026-10-22-native-driver-pass/result.json),
[build log](../../evidence/p3/2026-10-22-native-driver-pass/dkms-make-1.log),
and [resource report](../../evidence/p3/2026-10-22-native-driver-pass/evidence-resources.json)
are preserved byte-for-byte.

## Proven

The offline installer configured all 145 pinned guest packages. Explicit DKMS
compilation then built `nvidia`, `nvidia-modeset`, `nvidia-drm`, `nvidia-uvm`, and
`nvidia-peermem` from R595.91.07 for kernel `6.8.0-147-generic` with GCC 13.3.0.
The result records each module's ELF identity, byte count and SHA-256.

Independent limits were two CPUs, 8 GiB RAM, zero swap and 256 tasks. Peak memory
was 4,663,988,224 bytes; there were no OOM kills. Payload removal, process cleanup,
empty cgroup verification and cgroup removal passed. Nothing was installed into
the runner's kernel; the build root was removed.

## Warnings and Prior Failure

The [original stderr](../../evidence/p3/2026-10-22-native-driver-pass/dkms-build.stderr)
reports the missing `zstd` executable five times. The pinned DKMS 3.4.3 source's
`do_build` compression branch falls back to the uncompressed `.ko` on compressor
failure; those are the files inspected here. This is not a compressed-module
installation test. DKMS also explicitly reports that chroot-built modules are
unsigned. Kernel loading and the eventual signing policy are not tested.

Attempt 1 failed during public input download, before package execution, with
curl status 28. Its [original typed failure](../../evidence/p3/2026-10-22-native-driver-fetch-timeout/input-fetch.log)
is retained. A single retry succeeded without changing any hash, network or
resource guard. This does not demonstrate first-attempt download reliability.

## Still Required

This result proves compilation, not a bootable or confidential image. LKCA
compile-feature proof, module installation/loading, deterministic two-builder
comparison, UKI/rootfs/weight verification, measured boot and live CPU/GPU/fabric
attestation remain unproven. No production or marketplace admission changes.

`python -m unittest discover -s scripts -p test_native_driver_evidence.py`
checks the original artifact hashes and these claim boundaries. Entire local
`make check` and all final-head hosted checks must pass before merge.
