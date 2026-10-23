# P3.T1 Native Private Export Validation

Implemented with local synthetic tests and a subsequent
[real native pass](../../evidence/p3/2026-10-22-native-private-export-pass/README.md)
verifying and deleting the exported files. **No boot, signing, reproducibility
or confidential-computing result is claimed here.** The separate
[two-build comparison](p3-t1-native-reproducibility.md) checks identical-input
output hashes without broadening those claims. The separate
[private export](p3-t1-private-boot-export.md) preserves the original verified
GPT and unsigned UKI; this workflow exercises that export and deliberately
discards both images before upload.

## Explicit Native Test

The existing manual, main-only `mkosi-native-build.yml` workflow adds a single
`validate_export` boolean, default false. True requires all five existing build
flags: `compile_driver`, `install_driver`, `guest_runtime`, `verity_root`, and
`boot_uki`. The resource launcher receives its existing `--export-boot` option.
Default false retains the original no-export command and evidence upload paths.

The job remains GitHub-hosted Ubuntu 22.04 x86-64. The launcher and verifier use
the existing isolated/no-site/no-bytecode Python 3.10 host gate; the worker stays
Python 3.12. There is no self-hosted/Spheron fallback, new dependency, altered
package/runtime pin, relaxed source gate, increased image/log cap, or GPU action.
The existing cgroup and 55-minute job bounds remain unchanged.

After the build step terminates, the unprivileged
`host/validate-native-boot-export.py` runs even if that step failed. It accepts
only the fixed build outcome, not artifact paths. All paths are fixed under
`build/native-install`. A 120-second alarm bounds the fresh 42-file build-source
audit plus separate verifier/workflow source audit; another 120-second alarm
bounds verification, deletion and metadata preparation. The workflow step is
limited to five minutes. These are not guarantees against a kernel-stuck
filesystem or forcibly terminated runner.

## Verification and Deletion

The verifier reuses the export's strict proof/JSON parsers, streaming full-file
hashing, resource receipt checks, descriptor-anchored file reads, private-parent
validation, and Python 3.10-compatible flat cleanup helper. No new GPT/PE parser
or image regeneration is added.

Success requires the build outcome to be `success`, no pending export, exactly
the six private bundle files, exact committed source closure and common root
hash, all prior build-stage proofs, and successful independent resource cleanup.
The original generation/resource receipts must match the bundle byte-for-byte;
the manifest must match their sizes/hashes and every qualification flag. Both
actual images are streamed and hashed again with the existing byte/time bounds.
Links, special files, malformed JSON, unexpected files, altered bytes, receipt
or source mismatches, and forged success/qualification flags are rejected.

Before preparing upload metadata, the verifier removes the original pending
and final bundle directory identities and confirms both names absent under the
same private parent. Unknown cgroup cleanup blocks this operation: a live
worker must not recreate an image after deletion. A nested/linked/replaced tree,
cleanup failure, source-audit failure or metadata preparation failure does not
authorize upload. Private residue may remain after such failures; inspect it
without publishing it. No cleanup-success assertion is fabricated.

## Metadata Only

Export mode disables the ordinary recursive evidence upload. Its only upload
uses these five explicit paths, with no directory, glob, parent path or binary:

```text
build/native-install/export-validation/validation.json
build/native-install/export-validation/manifest.json
build/native-install/export-validation/run.json
build/native-install/export-validation/result.json
build/native-install/export-validation/resources.json
```

Successful verification preserves the four original bounded JSON files and
adds `validation.json` with the verifier/workflow hashes, source closure, common
root hash, artifact hashes/sizes and confirmed deletion. Each JSON is at most
64 KiB. No image bytes or raw subprocess logs are copied into this upload tree.

A failed verification with confirmed safe cleanup produces only a fixed-field
failure `validation.json`; no raw receipt contents or exception text is
copied. The workflow still fails. If cleanup is unknown or metadata preparation
fails, no upload is authorized at all. The original build evidence remains
local to the disposable runner in export mode; the default no-export workflow
continues uploading its original diagnostic files unchanged.

For a failed/cancelled build, `validation.json` now includes a
`nib.native-build-failure/v1` projection of the existing committed native
recorder. It binds the installation report's exact producer to the independently
audited source and includes only finite build/worker stages, finite failure
classes, package count, signed child/installer exit status, OOM count, peak bytes
and cleanup/readback booleans. The existing resource receipt has no producer
field of its own; it is read at the fixed sibling path after the same independent
cleanup check, not promoted to an independently source-authenticated record.
The complete input report's size/hash is retained, not its detailed contents.

Absent reports/workers and absent optional counters remain JSON null, never a
guessed stage or zero OOM count. A present record must have its fixed schema,
required fields and known keys; unknown fields/enums, wrong source, malformed or
duplicate-key JSON, nonfinite/bool-as-integer values, unsafe links/ownership,
changed files and oversized inputs reject metadata publication. Private-image
cleanup still runs if this diagnostic validation fails. Recognized detailed
records (including traces, process lists and image proofs) are omitted entirely;
they are not imported as code or used to weaken artifact acceptance. The
projection and original metadata remain inside the existing 64-KiB JSON bound
and 120-second verification alarm. Success metadata and the no-export path are
unchanged; no new workflow input, upload path, raw log or image is exposed.

The [original failed attempt](../../evidence/p3/2026-10-23-native-export-before-network-boot/README.md)
on `ff708bcfc5bac542cff0bc95a7c958926cb368d9` failed during native export,
before QEMU. Its original validation contains no stage/OOM/status projection;
that absence is preserved, not retroactively filled. This failure does not
identify the earlier guest `config-read` cause or prove an umask attribution.
The subsequent [original guest-network rejection](../../evidence/p3/2026-10-23-native-guest-network-rejection/README.md)
on exact `942dba9d38b8a19bf6c3ab6b9a3a7de44fcb3b0f` exercises the new projection:
worker `guest-network/rejected`, installer exit 0, child exit 1, OOM0 and confirmed
cleanup. It does not retain the precise rejected check, and QEMU was skipped.

The verifier prints only `pass` or `fail` after metadata preparation completes.
The shell accepts only those values, marks metadata ready, then independently
requires `pass`. Thus a failed validation can preserve its safe failure receipt
without turning the job green. The upload condition requires the ready marker,
including on build failure; `always()` alone never uploads export data.

Primary workflow/action semantics: [GitHub workflow inputs, conditions and step
outputs](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax),
and the [pinned upload-artifact README, Inputs and multiple paths](https://github.com/actions/upload-artifact/blob/ea165f8d65b6e75b540449e92b4886f43607fa02/README.md).
The action accepts files, directories and patterns, so this workflow deliberately
enumerates only the five JSON files rather than relying on hidden-file handling
or exclusion patterns. The GPT/UKI format and byte-preservation basis remain
those documented in the [export task](p3-t1-private-boot-export.md).

## Checks and Remaining Gate

From `scripts/`, run with actual Python 3.10 and the repository Python 3.12 test
environment:

```sh
python -B -m unittest test_native_boot_export_validation test_mkosi_export test_mkosi_build
```

Tests use synthetic local files and mocked host identity only; they do not run
the root launcher, download inputs, dispatch Actions, boot firmware or use GPUs.
Coverage includes exact metadata/receipt preservation, real image rehashes,
deletion before publication, failed build/verification outcomes, malformed or
changed proof/images, pending residue, unsafe tree refusal, unknown cleanup,
workflow output/upload gates and unchanged default behavior. A separately
authorized native run must establish actual export success and original
metadata/deletion receipts after merge. Even that run will not establish UEFI
boot, kernel verity enforcement, reproducibility, signing or confidentiality.

The current 212-package root is not the complete guest services/engine/trcs
image, and the separate generic UKI has no ESP. A future QEMU root-switch and
kernel-verity probe needs its own reviewed debug measurement and fixed,
content-free evidence channel. Neither partial root boot nor export success
would establish complete guest-service or production boot readiness. That
harness is explicitly outside this PR.
