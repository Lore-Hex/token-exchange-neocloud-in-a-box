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
