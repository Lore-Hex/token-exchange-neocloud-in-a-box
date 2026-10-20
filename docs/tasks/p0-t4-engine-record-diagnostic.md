# Bounded RECORD And Wheel Diagnosis

**Read-only software diagnostic recorded; overall result FAIL, not qualification.**
The previous [recorded diagnostic](p0-t4-cutlass-stubs.md#recorded-outcome)
completed the selected-root inventory and four exact stub classifications, but
rejected RECORD, wheel and admission checks. Its artifacts do not identify those
RECORD/wheel causes. The new [recorded outcome](#recorded-outcome) does identify
the strict-policy mismatches. This task adds observations, not acceptance or normalization.
Candidate status remains `BLOCKED`, `deployable:false`, `image:null`.

## Sources And Limits

Reviewed 2026-10-19: the [PyPA installed-project RECORD specification](https://packaging.python.org/en/latest/specifications/recording-installed-packages/#the-record-file)
allows corresponding `.pyc` files to be omitted when their `.py` files are listed.
It also permits hashed `.pyc` entries. The [wheel installation specification](https://packaging.python.org/en/latest/specifications/binary-distribution-format/#installing-a-wheel-distribution-1-0-py32-none-any-whl)
includes bytecode compilation during installation. These are packaging rules,
not proof that arbitrary bytecode is safe or corresponds to the named source.

The pinned [vLLM Dockerfile at `5df63a7203cdb9328d824a27b682e77c7b9928be`, line 847](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/docker/Dockerfile#L847)
sets `UV_COMPILE_BYTECODE=1`. Its full-file SHA-256 was verified as
`c62823feb562a4c3ab3050276c44c848b9354b11a21c077d29df114a07c7d911`, matching
[the existing source pin](../../scripts/engine-overlay-pins.json).
The recorded image contains unlisted bytecode with matching source names. The
build setting makes installation generation plausible, but does **not** establish
how those bytes were generated, their source correspondence or their safety.

The existing inspector intentionally requires every owned file to be recorded,
and requires empty hash/size fields on `.pyc` rows. Those rules are unchanged.
A diagnostic can identify a difference from PyPA's permitted forms without
accepting it, declaring the wheel invalid under PyPA, or choosing a relaxation.

## What Is Observed

The existing source-bound recorder adds `diagnostic_integrity_details:true` to
the integrity payload only. No new CLI, image, root, budget or bypass argument
is introduced. Details require a complete filesystem inventory and the same
exact image/config identity. Metadata census and historical artifacts are unchanged.

Independent stages continue after ordinary validation failures so one bounded
run can reveal multiple causes:

- RECORD: row/unique-path counts; duplicate, malformed or unsafe rows; recorded
  paths missing from inventory; recorded paths outside the strict owned set;
  owned files absent from RECORD; self/console coverage; hash/size mismatches.
  The strict owned set remains `vllm/` plus its selected `.dist-info/` directory.
- Bytecode: owned/listed/unlisted counts, source-name correspondence, listed
  empty/nonempty/partial hash fields, and independently matching nonempty hashes
  and sizes. Unlisted files are divided by whether a corresponding source name
  is recorded. This is filename accounting, not bytecode/header/coherence proof.
- Metadata: exact entry-point text match and at most 16 safe group names/counts
  (no entry-point values or imports), engine/generated version match,
  Requires-Python, wheel version/purelib/tags, editable-source flags, distribution
  duplicates, candidate-source expectations and import-name collision rejection.
- Dependencies: only the existing pinned package names, expected versions,
  at most two observed versions plus the actual count, installed-version match,
  declared requirement count and exact pin match. This follows the existing
  inspector's literal pin checks, not a dependency resolver or marker evaluator.

Shared pure predicates in [the inspector](../../scripts/engine_overlay.py) serve
both strict validation and observations. `inspect()` and `apply_overlay()` are
unchanged. The existing strict functions still fail on a violated predicate;
the diagnostic aggregates failures without creating an alternate admission path.
Even an otherwise clean detail report cannot grant overlay admission.

## Bounds And Failure Semantics

Details use fixed reason codes, counts and at most **four safe relative-path
samples per reason, 180 characters per path**. Unsupported/long sample paths are
omitted, not followed or printed raw; counters still record the finding.
Console-path traversal is never followed: only the already anchored, existing
`vllm` console handle is used, with no raw external path in the report.
No raw exceptions, entry-point content, direct URLs, requirement markers,
unknown requirement names, environment values or hook/engine execution appear.
Selected metadata values use bounded grammars and lists of at most 16 values.

Additional diagnostic limits are **2,048 metadata reads, 100,000 RECORD rows,
4,096 requirements, 30 seconds and 64 KiB detail JSON**. The cumulative metadata
budget is **16 MiB**, checked after each existing bounded read (at most one
additional file of at most 2 MiB before byte-budget rejection). Reads reuse the
no-follow, nonblocking, single-link regular-file/identity checks. Time is checked
between operations; the unchanged outer container supervisor bounds stalled I/O.
The existing inventory and Docker limits are unchanged.

Limits, unavailable reads and incomplete CSV parsing retain preceding bounded
observations, with `complete:false` and outer `collection:partial`. Metadata
parse failures and values suppressed by the reporting grammar also
require these partial flags; later independent stages still run. The producer
and validator share a fixed set of incomplete-observation reasons. An unknown
version or failed source-version evaluation is not reported as an ordinary
version mismatch. The module-collision check conservatively marks rejection
incomplete because its existing exception does not distinguish a collision from
an interrupted scan. Fully observed policy mismatches, such as a readable wrong
wheel tag or extra entry-point group, may remain complete observations, never
acceptance.

Separate RECORD/bytecode completeness flags qualify counts; absent totals are `null` and
observed partial counts are not complete totals. Accounting uses interpretable,
safe paths only; malformed rows retain their own fixed rejection counts. Sample
lists are not complete file lists. Other metadata stages still run after normal
RECORD failures, but a shared budget exhaustion stops further work.

Exact rejection codes are defined in `INTEGRITY_REASONS`; they describe repository
policy or an observation failure, not native ABI, installed-code trust, GPU or
confidentiality. Reports are strictly validated before publication. The current
root/startup restrictions and all prior failure evidence remain intact.

## Verification And Next Gate

```sh
uv run --project tools --frozen python -I -m unittest discover -s scripts -p 'test_engine_image.py' -k integrity_details -v
uv run --project tools --frozen python -I -m unittest discover -s scripts -p 'test_engine*.py'
```

Tests exercise real synthetic filesystem layouts, independently combined
failures, listed/omitted/hashed/orphan bytecode, duplicates, unsafe paths, limits,
FIFO rejection, metadata redaction, report tamper and unchanged strict failures.
These tests do not import SDK/engine/bytecode, install packages or run an image.
The separately recorded read-only image diagnostic below does not execute the
engine, use GPUs/weights, normalize the image or establish runtime acceptance.

Commit, independent review and integration checks precede any separately approved
read-only image run using the existing recorder. No hook/root allowlist,
packaging relaxation or import-path normalization decision is made here.

## Recorded Outcome

[run.json](../../evidence/p0/2026-10-19-official-vllm-record-diagnosis/run.json) and
[output.log](../../evidence/p0/2026-10-19-official-vllm-record-diagnosis/output.log)
are preserved unchanged from the parent-run diagnostic, 2026-10-19
23:57:58Z through 23:59:06Z. The committed source is
`aed2389f4fcb56aeaad9a573f59c5b351b15718b`; its independent source review was CLEAN.
The parent combined suite passed 261 Python tests, both Go race builds and fake
E2E (18 provider checks, 14 negative mutations, cleanup confirmed) at integration
`608cabec` / tree prefix `1c53d9`, E2E `43e8d32911fc`.

The author reconstructed the source/payload bindings locally without another
image execution:

- Payload SHA-256: `16378fdd3216a2b44282b2c62db3bc7a5ce58c00e56a18ba1c6e69f5aaa4cdc8`.
- Source bundle SHA-256: `e0a4af2583f7592a67f1fbe7a912ba10827fbd9f7676dd3e88865f6b23357fc2`.
- Validator-source SHA-256: `c18f7f7028758f683f01a67806cbe5db9d44f503c6f58357393329b01a243232`.
- `run.json` SHA-256: `8ac7a81e9984c83a22d0c56d0014ef7177219d433818682fa58b5fa81fbd23e2`.
- `output.log` SHA-256: `abf7f9fbb4dce9ace3da05fa2873f4e27041baf13a18405ddce8c71e7febfc76`.

Collection, filesystem inventory and integrity-detail observation are complete.
There are exactly two detail findings:

1. `entrypoints`, count 1: one `console_scripts` entry and two entries in
   `vllm.general_plugins` differ from the inspector's exact console-only text.
   Only group names/counts were collected, not the plugin targets or behavior.
2. `record-unlisted-owned`, count 2,879: all 2,879 are bytecode filenames with
   corresponding source names present in RECORD. There are no orphan filenames,
   listed bytecode rows or hashed-bytecode rows. This is **filename accounting,
   not bytecode validation or trust**; bytecode was not imported or executed.

RECORD has 5,449 unique rows, no missing files or unowned rows, and 8,327 owned
files. The one anchored console row is outside that owned set, so
`5,449 - 1 + 2,879 = 8,327`. All 14 selected installed dependency pins match;
all 13 expected direct requirement pins match. NCCL has no direct requirement,
as expected by the existing policy. These are selected literal checks, not a
complete resolver or dependency security audit.

WHEEL reports `1.0`, non-purelib, `cp38-abi3-linux_x86_64`; metadata/generated
engine version is `0.32.0`. All 13 candidate source expectations match (12 source
files and one expected absent helper). All four exact published CUTLASS stub
classifications remain matched. None of these observations establishes native ABI.

The strict RECORD/wheel checks and both root admission checks remain rejected.
The observed startup hooks and populated alternate root remain blockers. The
command exited 1 with `result:fail`, `stage:overlay-rejected`, `cleanup:confirmed`;
the parent independently confirmed an empty owned-container label listing.
The cached image was retained. Raw OCI config linkage and local Docker metadata
association passed; local config bytes/layers were not independently rehashed.
Docker, OS and interpreter remain trusted, not attested.

No image application/build, engine/SDK import, GPU execution, normalization or
production activation occurred. The candidate remains BLOCKED with no deployable
image and no hardware/confidentiality qualification. Any exact-image packaging
compatibility and explicit import-path contract is a separate reviewed task.
