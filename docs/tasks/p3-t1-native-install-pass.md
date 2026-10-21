# P3.T1: Native Package Installation Passed

[Hosted run 39691976863, attempt 2](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39691976863/attempts/2)
executed producer `66ef40db80da15cc54305872815d5e40d67f134f` on Linux/amd64.
The original [run](../../evidence/p3/2026-10-21-native-install-pass/run.json),
[result](../../evidence/p3/2026-10-21-native-install-pass/result.json),
[package output](../../evidence/p3/2026-10-21-native-install-pass/installer.stderr),
and [independent resource report](../../evidence/p3/2026-10-21-native-install-pass/evidence-resources.json)
are preserved byte-for-byte. Installation ran from 23:22:35 to 23:23:45 UTC on
October 21, 2026. Attempt 1 failed while fetching inputs; this is not evidence of
unconditional first-attempt reliability.

## Proven

- All 145 pinned guest packages were installed and their exact versions and
  `install ok installed` states were checked against the authenticated inputs.
- The package build ran offline, with the tools root read-only and scratch
  confined to private mounts. The `/tmp` correction did not make `/var/tmp` or
  the tools root writable.
- Independent cgroup limits were read back: two CPUs, 8 GiB RAM, zero swap,
  and 256 tasks. Peak memory was 4,665,065,472 bytes; no OOM kills occurred.
- Payload removal, process cleanup, empty cgroup verification and cgroup removal
  passed. No process or installation root was retained.

## Not Proven

This is an installation result, not a bootable guest or production image.
NVIDIA DKMS compilation was deliberately disabled during package installation.
Explicit pinned-kernel compilation, module inspection, two-builder comparisons,
UKI/rootfs/weight verification, measured boot and live CPU/GPU/fabric attestation
still need their own evidence. The P3 phase is not complete.

`python -m unittest discover -s scripts -p test_native_install_evidence.py`
checks the original evidence hashes and the limited claim boundary. Full
`make check` and all final-head hosted checks are required before merge.
