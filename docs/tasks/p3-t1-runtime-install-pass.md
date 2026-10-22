# P3.T1: Native Runtime Installation Passed

[Workflow 39727664370](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39727664370)
executed producer `97c8a1e40e39f2286641586b3d7bd3b0a4b97d4f` on Linux/amd64
from 05:34:34 to 05:38:24 UTC on October 22, 2026. The original
[run](../../evidence/p3/2026-10-22-native-runtime-pass/run.json),
[result](../../evidence/p3/2026-10-22-native-runtime-pass/result.json),
[build log](../../evidence/p3/2026-10-22-native-runtime-pass/dkms-make-1.log),
and [resource report](../../evidence/p3/2026-10-22-native-runtime-pass/evidence-resources.json)
are preserved byte-for-byte.

## Proven

The offline installer configured all 212 pinned runtime and build packages.
The source-bound package-hook inventory passed before installation. Five NVIDIA
R595.91.07 modules were compiled for kernel `6.8.0-147-generic` with GCC 13.3.0,
decompressed from the installed zstd format, checked as ELF objects, and
installed into the private guest root. The result includes hashes and sizes
for both compressed and uncompressed modules, the required Linux crypto
references, and the generated module-dependency indexes.

The independent resource wrapper enforced two CPUs, 8 GiB RAM, zero swap and
256 tasks. Peak memory was 4,911,923,200 bytes with zero OOM kills. Payload
removal, process cleanup, empty-cgroup verification and cgroup removal passed.
No driver was loaded into the runner's kernel and the build root was removed.

## Not Proven

This is installation evidence, not a bootable image or a confidential service.
The producer predates the runtime startup-policy change, so this run does not
test that policy. The driver receipt explicitly leaves loading, runtime LKCA,
bootability and reproducibility false. The worker leaves deployability false.
The 212-package candidate includes build dependencies; it is not a minimized
release filesystem.

The Ubuntu 24.04 appraisal tools, guest-service production backends, immutable
engine and model volumes, UKI/root-hash binding, two clean reproducible builds,
measured boot and live CPU/GPU/fabric verification remain required. Nothing in
this record admits a production route or changes the verifier's fail-closed
behavior.

`python -m unittest discover -s scripts -p test_native_runtime_evidence.py`
protects the original artifact hashes, installed package count, cleanup and
claim boundaries.
