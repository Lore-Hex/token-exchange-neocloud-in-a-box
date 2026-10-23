# Native Export Failed Before Guest-Network Boot

Original [workflow run 39885063707](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39885063707)
used exact committed producer `ff708bcfc5bac542cff0bc95a7c958926cb368d9` with
`debug_boot=true` and `guest_network=true`. It was dispatched once after exact
main full/catalog CI and an active-run check passed. No retry was made.

The job ran 2026-10-23T01:37:42Z through 01:43:37Z. Authenticated public input
fetch passed. Native export failed during 01:39:32Z-01:43:33Z; debug assembly
and QEMU were skipped. The unused-export cleanup step and its fixed metadata
upload passed. This is a failed build, not a boot or guest-network result.

## Original Bytes

Artifact `12472417484`, `unused-debug-export-39885063707-1`, is a 3,710-byte ZIP
containing exactly one regular mode-0600 member: `validation.json`, 8,089 bytes.
The downloaded ZIP SHA-256 matches GitHub's artifact digest:
`90299250a0b967b37a6bb943ce237197251469252aa4a9a84cc27feab6b7c05d`.
The private original archive is retained outside the repository at
`/private/tmp/nib-debug-network-39885063707-original.zip`; no image or raw log
was downloaded. The committed JSON is byte-identical to the extracted original.

- `validation.json`: SHA-256 `37f5d028914f0539a24b5e9bcef8c979f0aab277432d392043711bff0276766b`.
- `artifact-metadata.json`: original structured GitHub artifact API response,
  SHA-256 `2a19bce7e98445cffb9442b678984b9f077fb195da4f5f6e07b08eb25cea6a5a`.
- `workflow-run.json`: original structured `gh run view --json` response,
  SHA-256 `870b62861403930521df686073737a3a8117e88d7ae7ef4756bdb4c2da63cc2e`.
- `output.log`: exact duplicate of `validation.json`, not an invented console
  transcript. No raw serial, journal, nft, build command output or error text.

No standard native `run.json` survived this workflow branch. The original
GitHub run metadata is retained under its own name rather than fabricating a
`nib.evidence/v1` envelope after the fact. The validation producer is the
committed `host/validate-native-boot-export.py`; its source record contains 68
native input hashes and two validator/workflow hashes, all checked against the
exact ancestor above.

## What This Proves And Does Not Prove

The original validation records `build_result=failure`, `result=fail`,
`artifacts_verified=false`, `private_images_removed=true`, and
`metadata_only=true`. Signing, boot, reproducibility and confidentiality all
remain false. The validator required the resource receipt's empty-cgroup and
removed-cgroup markers before deleting private pending/final image names.

The original contains no failed-stage, child status, OOM count or peak-memory
observation. The source at this producer discarded detailed metadata on build
failure and published only this generic cleanup receipt. No precise native
failure cause can be inferred from it. In particular it is not another guest
status67/config-read observation and does not establish a native umask cause.

The subsequent bounded failure projection is an implementation tested with
synthetic records; it does not retroactively add observations to this original.
No new actual run, TDX/GPU test, routing, full guest-service readiness,
production readiness, signing or reproducibility claim is made here.
