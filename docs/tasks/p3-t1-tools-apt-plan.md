# Expanded Tools APT Plan

The existing offline APT simulation has a second explicit target:
`--target mkosi-tools`. It resolves the 203-package mkosi build-tools lock
with its own authenticated metadata scope. The default remains the
145-package guest/kernel/driver plan.

```sh
python3 -I scripts/prepare_guest_tools.py --target mkosi-tools \
  --metadata /absolute/private/metadata --archives /absolute/private/archives \
  --output /absolute/private/tools-plan-bundle
python3 -I host/simulate-guest-apt.py --run --target mkosi-tools \
  --metadata /absolute/private/metadata --archives /absolute/private/archives \
  --bundle /absolute/private/tools-plan-bundle --output /absolute/private/tools-plan-result
```

Preparation and execution must use the same committed source. The host
reconstructs the image and verifies the bundle before execution. The selected
target lock and its exact package/version set are source-bound; a different
target or mismatched lock rejects. The legacy `guest_lock_sha256` result field
now denotes the selected target lock, not a claim that tools are guest packages.

This reuses the fixed APT worker and shared offline container lifecycle without
new installation flags, hooks, privileges, mounts, network access or package
execution. Only the small APT runtime's binaries execute; the expanded tools
are dependency targets, not installed programs. Successful resolution requires
all exact versions, no removals, and confirmed cleanup. The separate
[seven-tool startup check](p3-t1-tools-startup.md) tests binary startup.

The committed runner writes `run.json` and content-free `output.log` alongside
the existing `report.json`. They bind the producer, UTC interval and selected
target. Success is dependency-plan evidence only: package installation,
maintainer-script safety, mkosi execution, boot, reproducibility and attestation
remain unproven.

## Recorded Result

Producer `a4ecfe11d5051f873d4efacb3eb37d647e86219c` ran the tools profile
offline on Linux/amd64 through local Docker at 2026-10-21 03:29:30-03:29:35 UTC.
The exact 203 packages were planned for installation and configuration, with
zero removals. Container and image cleanup were confirmed. These are APT's
simulated actions, not installed packages.

- [Run record](../../evidence/p3/2026-10-21-tools-apt-plan-pass/run.json)
- [Unmodified result](../../evidence/p3/2026-10-21-tools-apt-plan-pass/output.log)

The target lock SHA-256 is
`0ad871ad797954c2a107a196aa31e8543931cfe947fa919b24fdbcd3737d9b90`;
the resolved plan SHA-256 is
`5299b5a5d99c3f2012021c958ec03d2c93beb7382a122f851491289a77f34974`.
All installation, boot and attestation flags remain false.
