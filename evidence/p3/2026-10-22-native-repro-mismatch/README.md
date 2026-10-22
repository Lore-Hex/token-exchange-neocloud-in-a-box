# Native Repeat-Build Mismatch

**Reproducibility failed.** Both clean native builds and export verification
passed, but the root GPT, verity root and unsigned UKI differ. The original
successful build receipts are unchanged; their success does not mean the
cross-build comparison passed.

Both attempts used producer `c8853e1ea7f537d7a7888d79e41d669032f8f19f` and
identical 42-file build and separate validator/workflow source closures.

- [Attempt 1](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39836415921/attempts/1):
  build 19:02:54-19:07:25 UTC, 2026-10-22; peak cgroup memory 7,616,069,632 bytes.
- [Attempt 2](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39836415921/attempts/2):
  build 19:10:13-19:14:33 UTC, same date; peak cgroup memory 7,616,376,832 bytes.

The second workflow execution was a rerun of the first, not a dispatch against
a moving main branch. Both jobs were GitHub-hosted Ubuntu 22.04. Each passed
resource cleanup and image deletion with no OOM. These are trusted CI-builder
observations, not cryptographic builder attestation or P8's cross-platform proof.

Each attempt folder retains the five original JSON artifact files; `output.log`
is a byte-identical copy of its final `validation.json`. No private image or
raw build content was uploaded. The retained metadata was compared using
`scripts/compare_native_exports.py` at committed producer
`b13c997168d82b8cc9fc89b0b4ccf3f39634bff9`:

```sh
python3 -B scripts/compare_native_exports.py /absolute/attempt-1 /absolute/attempt-2
```

Exit 1; original stdout is [comparison.json](comparison.json). The comparator
validated every receipt before reporting all three output-hash mismatches.

The compiled driver module identities, initrd, kernel, os-release and kernel
version sections match. The UKI command-line section differs, consistently
with its binding to the differing verity root. This narrows investigation to
root-image construction but does not identify the changing root files. No
file cleanup or normalization is justified solely by this observation.

Reproducibility, boot, signing and confidential-service qualification remain
unproven. Preserve this failure when later diagnostics or fixes are added.
