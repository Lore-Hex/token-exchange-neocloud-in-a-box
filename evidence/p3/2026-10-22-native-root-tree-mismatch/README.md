# Native Root Tree Mismatch

**Cross-build comparison failed.** The original five metadata JSON files from
each diagnostic build are preserved without modification. `output.log` is an
exact copy of the corresponding final `validation.json`. No image or raw
filesystem contents were uploaded. Each individual build passed validation and
deleted its private images; that does not make the comparison a pass.

Producer: `ad1ab45e6b96a06a5972bedfcb5d4b5f5dcf5c9c` for both builds.

- [Attempt 1](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39839973107/attempts/1):
  2026-10-22 19:28:21-19:32:50 UTC.
- [Attempt 2](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39839973107/attempts/2):
  2026-10-22 19:37:12-19:40:56 UTC. Rerun of the same original commit, not moving main.

The committed comparator in this producer generated [comparison.json](comparison.json)
with exit 1. Root GPT, verity root and unsigned UKI all differ. Build source
and validator/workflow closures match, and resource cleanup passed.

The new diagnostics localize differences to `var/log`, `var/lib/dkms` and
remaining `var`. All five other fixed buckets match. The selected DKMS make
log content differs. Other differing files are not identified by these bucket
hashes. This evidence does not by itself justify broad cleanup or establish
that any proposed narrow fix makes complete images reproducible.

No boot, signing, independent-builder, hardware or confidential qualification.
