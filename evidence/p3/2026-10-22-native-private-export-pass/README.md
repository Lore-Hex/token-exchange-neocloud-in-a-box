# Native private export verification

Original metadata from [workflow 39825942855](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39825942855),
producer `db23faee25adddccd5d090ca55ec3120d5f7a957`. The native operation ran
2026-10-22 17:50:25-17:54:47 UTC on GitHub-hosted Ubuntu 22.04 x86-64 with all
five build options and explicit `validate_export=true`.

All five uploaded JSON files are preserved byte-for-byte. `output.log` is an
exact copy of `validation.json`, not a rewritten report. No disk or UKI binary
is included here or was uploaded by that workflow.

- All 212 runtime packages, installed driver modules, runtime policy and
  appraisal CLI probes passed, followed by userspace verity and initrd/UKI checks.
- The original 928,989,184-byte GPT and 97,898,496-byte unsigned UKI were staged
  and privately exported. The independent export validator rehashed their actual
  bytes against the source-bound manifest and original build/resource receipts.
- Both private images were deleted before the five metadata files were prepared
  for upload. Process/payload cleanup and cgroup removal passed, with exit 0,
  zero OOM kills and 7,616,405,504-byte peak memory.

This proves **private export, verification and deletion**, not boot, kernel
verity enforcement, Secure Boot, reproducibility, GPU execution or confidential
inference. All corresponding original flags remain false. The GPT and UKI are
separate artifacts, not a boot disk with an ESP or a complete serving guest.

The root hash and UKI hash differ from the earlier assembly run; the initrd
hash is unchanged. No reproducibility conclusion is inferred from either fact.
The actual source and build path also differ; these are not matched rebuilds.

`scripts/test_native_export_evidence.py` pins the original hashes, reuses the
existing proof/resource validators, checks receipt cross-bindings, and prevents
these results from being described as boot or confidentiality validation.
