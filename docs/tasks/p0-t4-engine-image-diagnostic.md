# Read-Only Engine Image Diagnostic

**Recorded integrity-mode outcome: complete selected-root inventory with four exact AOT
stub classifications; RECORD/wheel and admission rejected.** See the
[measured result](p0-t4-cutlass-stubs.md#recorded-outcome). The earlier partial
[recorded run](../../evidence/p0/2026-10-19-official-vllm-image-diagnostic/run.json)
below remains a failed P0.T4 software diagnostic, not qualification. The candidate
remains `BLOCKED`, `deployable:false`, `image:null`. No engine import, patch
application, model weights, image build/publication, GPU, ABI, confidentiality or
hardware qualification is performed. The existing overlay admission rules remain
fail-closed; diagnostics do not remove hooks or override package-root checks.

The separate [Python layout descriptor](p0-t4-engine-python-descriptor.md#recorded-outcome)
recorded a partial actual-image collection: packaging observations completed,
interpreter-file checks rejected and child canaries were not attempted. It cannot
admit an overlay; the earlier results below remain unchanged historical evidence.

## Recorded Run

The [unchanged recorder output](../../evidence/p0/2026-10-19-official-vllm-image-diagnostic/output.log)
was produced from source `694394cfe631ab6d09cd5695087a70351f203fae` on
2026-10-19, 22:17:02 to 22:17:38 UTC. The recorder exited 1 and retained
`result:fail`, `collection:partial`, `overlay_admission:rejected` and
`cleanup:confirmed`. Its payload SHA-256 is
`fa024d5cf969cdde5210d05f1c449d33684bff2a1cbd06994712edd4458a0226`.

- The inspected environment reported Linux/amd64 and Python 3.12.3. Raw OCI
  manifest/config linkage and local Docker metadata association passed the
  checks described below; local layer/config storage was not independently
  rehashed.
- `root-0` has 42 entries and OS-distribution metadata. `root-1` has 516 entries,
  vLLM 0.32.0 metadata and the two exact public hooks listed below. These entry
  counts are not package counts or dependency qualification.
- All 13 candidate base-source expectations match: 12 readable source files and
  the expected absence of the new helper. No patch was applied.
- Root admission and full filesystem inventory were rejected. RECORD, wheel
  integrity and selected-root admission remain unavailable. The exact inventory
  rejection cause is **not identified by this artifact**; further bounded
  diagnosis is a separate task, not a reason to weaken acceptance.
- The owned diagnostic container was removed and absence confirmed. The cached
  image was retained. No GPU, model inference, ABI validation, attestation or
  confidential-runtime validation occurred.

### Inventory Rejection Identified

The [second recorded run](../../evidence/p0/2026-10-19-official-vllm-inventory-rejection/run.json),
from source `4f97ef0b9116571620706c9080f40986c9624baa` on 2026-10-19,
22:22:29 to 22:23:09 UTC, identifies the inventory rejection as `entry-limit`.
The pinned image exceeds the inspector's 100,000-entry bound. This does not
establish the complete count, total bytes or validity of entries beyond that
bound. Root admission still rejects the hooks and alternate package root.

The [unchanged output](../../evidence/p0/2026-10-19-official-vllm-inventory-rejection/output.log)
retains `result:fail`, partial collection and confirmed cleanup. All 13 source
expectations still match. Its payload SHA-256 is
`c7e3be375e2220e56b373100242d9731523b7f0635889823ea00e88fd9a0ba25`.
No admission limit was increased. The separate metadata-only census below now
records the selected root's counts and logical sizes. The diagnostic-only entry
allowance described below uses that evidence without changing generic admission.

## Metadata-Only Census

### Recorded Counts

The [census run](../../evidence/p0/2026-10-19-official-vllm-metadata-census/run.json)
from source `e753c351e8e5ec025c172fb3193b4d4268183923` on 2026-10-19,
22:37:02 to 22:37:16 UTC, completed counting with `collection:pass` and
`cleanup:confirmed`. The [unchanged output](../../evidence/p0/2026-10-19-official-vllm-metadata-census/output.log)
retains `overlay_admission:not-evaluated`, `result:fail` and
`stage:metadata-census-complete`; exit 1 is intentional, not failed counting.
Its source-bound payload SHA-256 is
`6c4697b6e7a397efad1642f05e86e425a007df5c7cb7a33c3af621c7f8d56777`.

| Selected-root measurement | Observed value |
|---|---:|
| Entries, excluding the root itself | 204,969 |
| Regular-file entries | 188,365 |
| Directories | 16,604 |
| Total logical regular-file bytes | 19,176,556,448 |
| Largest regular-file bytes | 541,607,888 |
| Maximum entry depth | 15 |
| Symlink, multiply-linked regular and special-file entries | 0 each |

These are metadata observations for the selected package root, not the whole
image or a verified dependency inventory. No contents or link targets were read.
Raw OCI linkage and local Docker metadata association were checked as described
below; local layer/config storage was not independently rehashed. The owned
container's absence was confirmed and the cached image retained.

The census establishes a count above the existing 100,000-entry admission limit,
but does not change that limit or validate entries. Wheel/RECORD consistency,
native contents, startup hooks and alternate-root admission remain unresolved;
the prior diagnostic's rejections are not overridden. No overlay application,
engine import, model inference, GPU, ABI, attestation or confidentiality
qualification occurred. Candidate status remains `BLOCKED`, `deployable:false`,
`image:null`.

### Mode And Limits

After independent review of the committed source and separate run approval:

```sh
python3 -I -S scripts/record-engine-image.py --metadata-census --slug engine-metadata-census
```

This explicit mode uses the same pinned image, raw OCI linkage, committed-source
payload, offline schema validator, isolation, private no-overwrite publication
and owned-container cleanup as the integrity diagnostic. There is no alternate
image, root-path argument or budget override. The mode is included in the hashed
payload contract and recorded command/provenance. It does not run wheel/RECORD
validation, source hashing or hook-content collection inside the image.

The image's isolated Python interpreter must report matching `purelib` and
`platlib` roots. Only that selected root is walked, anchored with the existing
no-follow directory/Tree helpers. Ancestors and child directories are opened
without following links; non-directory entries are only statted with
`follow_symlinks=False`. Directory identities are checked across traversal,
device boundaries are rejected, and no file contents or link targets are read.
The image interpreter and daemon remain trusted; this is not a filesystem
snapshot, signature verification or hostile-OS proof.

The fixed diagnostic budget is **1,000,000 entries, depth 32, 120 seconds and
16 KiB child JSON**. Time checks occur between metadata operations; the existing
600-second container supervisor bounds stalled operations. Counters use finite
1 PiB/file and 1 EiB aggregate logical-byte ceilings. These are census safety
bounds, not permission to accept files of those sizes. The ordinary inspector's
100,000 entries, 32 GiB total, 4 GiB/file and all other admission rules are
unchanged. No bytes are read to compute the census size totals.

The bounded report contains entry-type counts, logical regular-file bytes,
largest regular-file size, multiply-linked regular-entry count, maximum observed
entry depth and six fixed size buckets. The root itself is excluded from entry
counts. Hard-linked regular files are counted once per directory entry, not
deduplicated; sparse-file sizes are logical, not allocated disk bytes. Fixed
groups `vllm`, `torch`, `nvidia` and `triton` aggregate entries under exactly
those top-level names; everything else falls into `other`. They are not verified
package identities or a dependency inventory. No observed file names, paths,
link targets, environment values or raw exceptions are published in this mode.

On a limit, race or metadata error, preceding counters survive as
`collection:partial` with a fixed reason. They are incomplete observations, not
complete totals; entries counted before an error can be included, and traversal
order is unspecified. A complete traversal reports `collection:pass` only for
counting. In **both** cases, `overlay_admission:not-evaluated`, `result:fail`
and recorder exit 1 are intentional: counting can never grant admission.
Candidate status remains `BLOCKED`; deployment, ABI and hardware validation are
false. Counting alone does not grant an entry allowance or admission.

## Image-Specific Inventory Budget

**Recorded against the cached official image: partial inventory, native-header
rejection and confirmed cleanup.** The bounded failure is preserved below.
The default `Tree.inventory()` limit remains `MAX_FILES = 100000`.
Ordinary wheel inspection, overlay application,
startup-hook scans and other existing uses retain that limit. No CLI/environment
budget override is available.

Only the source-bound integrity diagnostic can request the fixed **225,000-entry**
policy in [the inspector](../../scripts/engine_overlay.py). It requires this exact
tuple, not a floating tag, unknown image or new config:

- Image: `vllm/vllm-openai@sha256:6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9`.
- Raw config: `bd8a5d7a8774800c92d714c0c226b32c1863f929150f36efd610aebcd12a335d`.
- Committed [census output](../../evidence/p0/2026-10-19-official-vllm-metadata-census/output.log):
  SHA-256 `006235bf2a5ce571a307efc942b33978f4ef79ab4be91d88db1824470e8f0fd0`.

The recorder reads the census from the same committed Git snapshot as its other
inputs, verifies the exact hash before preparing the payload, and includes it in
the source-bundle digest. The mode/policy are bound into the payload contract and
the child reports the applied policy. Raw OCI/config verification and local
manifest/platform/DiffID association still precede container execution. A missing
or modified census, mismatched identity or changed ceiling fails closed.
The internal inventory helper is not independently an image-origin verifier.

**Budget rationale:** the measured 204,969 entries plus 20,031 entries of finite
headroom gives 225,000, approximately 9.8% above the observed count. This is a
conservative diagnostic allowance for that immutable image, not an allowance for
future package additions. The measured 19,176,556,448 logical bytes and largest
file of 541,607,888 bytes are below the unchanged 32 GiB total and 4 GiB/file
limits. Depth 15 is below the unchanged depth-32 limit. Inventory still hashes
each file it processes and applies the existing native-header, Python-ABI,
hardlink, symlink and non-regular-file checks; the census did not validate
contents. A rejected inventory does not establish that every file was processed.

The unchanged container cap is 1 GiB memory/swap total, 2 CPUs and 32 PIDs. The
inventory retains its 300-second per-tree deadline; the outer attached-process
supervisor remains bounded at 600 seconds. Neither memory nor completion within
those limits is proven by entry/byte counts. OOM, timeout or any other rejection
remains a failed run; no automatic retry or relaxed fallback is added.

This policy cannot bypass root/startup admission and is not forwarded into
ordinary `inspect()` or `apply_overlay()`. The observed hooks and alternate root
still reject; wheel/RECORD/native integrity are still unproven until a subsequent
reviewed run completes those checks. The recorded attempt below preserves the
next exact failure. Image normalization/building and runtime activation remain separate,
blocked work; this task makes no ABI, GPU or confidentiality claim.

### Recorded Native-Header Rejection

The [bounded integrity run](../../evidence/p0/2026-10-19-official-vllm-bounded-integrity/run.json)
from source `472f998df3d128d5dd5f1ad4b26cb628e7042fc8` ran on 2026-10-19,
22:49:35 to 22:50:38 UTC, and exited 1. The
[unchanged output](../../evidence/p0/2026-10-19-official-vllm-bounded-integrity/output.log)
records the exact 225,000-entry image/config/census policy and payload SHA-256
`16752cb9ce4d1e1adba509ff25e9bb789b08e158444823b340e302495694599c`.

The inventory failed its existing `native-header` check at
`nvidia_cutlass_dsl/cu12/lib/stubs/aarch64-unknown-linux-gnu/libcute_dsl_runtime.so`.
This is the reported relative path and fixed rejection class, not proof of the
file's purpose, actual runtime use or safety to ignore. No native-file exception
was added by that recorded source and no image content was changed.

External source context only: NVIDIA CUTLASS at
[`098de2a652cf8f00fd70b2df54051c7eccbb855a`](https://github.com/NVIDIA/cutlass/blob/098de2a652cf8f00fd70b2df54051c7eccbb855a/python/CuTeDSL/cutlass/cute/export/aot_config.py#L27-L33)
documents `stubs/<triple>/` beneath the library directory as a link-time fallback
for AOT cross-compilation, while an empty target uses the native-host library
directory. This explains an upstream convention, not the installed asset's bytes,
provenance or loader behavior, and grants no native-header exemption. The source
file is NVIDIA-proprietary; no implementation code was copied.

- Collection remains `partial`; filesystem inventory and root admission are
  `rejected`. RECORD integrity, wheel integrity and selected-root admission are
  `unavailable`. The run does not establish a complete integrity inventory.
- All 13 patched-source expectations still match, including the expected absent
  helper. This is narrower than whole-wheel or native-code validation.
- `result:fail`, `overlay_admission:rejected` and `cleanup:confirmed` are retained.
  The owned container was removed; the cached image was retained. Raw OCI linkage
  and local Docker metadata association passed, not independent local layer
  rehashing or attestation.
- Candidate status stays `BLOCKED`, with no deployable image, ABI/GPU/hardware or
  confidential-runtime qualification. SDK stub usage, native admission and the
  existing hook/root blockers require separate source-backed investigation.

### Exact Published Stub Classification

The subsequent [four-asset diagnostic policy](p0-t4-cutlass-stubs.md) has now
[completed a recorded inventory](p0-t4-cutlass-stubs.md#recorded-outcome) from
source `2dea53cd9ab7556de9dd1a705266496c949735b7`: all four assets match,
collection and filesystem inventory pass, and cleanup is confirmed. RECORD,
wheel, root and selected-root admission checks reject; overall result remains
`fail`. That run did not identify the exact RECORD/wheel rejection causes.
It requires the exact image/config, path, whole-file hash, size and AArch64 header
for four published CUTLASS 4.8.0 AOT link-time stubs. It does not alter generic
native checks, overlay admission, root/startup gates or historical evidence.
All four assets are required for a complete classified inventory; missing or
changed assets fail closed. Even complete classification never grants admission.

The subsequent [bounded RECORD/wheel metadata diagnostic](p0-t4-engine-record-diagnostic.md#recorded-outcome)
completed collection from source `aed2389f4fcb56aeaad9a573f59c5b351b15718b`.
It identified two strict-policy mismatches: an additional entry-point group and
2,879 unlisted bytecode files, all with corresponding recorded source names.
Selected wheel metadata and dependency pins match. This does not validate
bytecode contents or native ABI, change admission, or resolve the hook/root
blockers; the overall result remains `fail`, with cleanup confirmed.

## Fixed Inputs And Identity

The only supported image is the existing
[pinned Linux/amd64 image and config](../../scripts/engine-overlay-pins.json).
It must already be cached in the user's local Unix-socket Docker daemon. There
is no image argument, pull, registry request, layer export or metadata download.

Before an approved run, the private repository-relative directory
`build/engine-image-metadata/` must contain exactly two regular files:

- `manifest.json`: original manifest bytes whose SHA-256 is `6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9`.
- `config.json`: original config bytes whose SHA-256 is `bd8a5d7a8774800c92d714c0c226b32c1863f929150f36efd610aebcd12a335d`, length 45,008 bytes.

These are raw public OCI/registry objects, **not** reserialized Docker inspect
output. The manifest's config descriptor must match the raw config hash/size,
and the config must declare Linux/amd64. The local Docker descriptor or RepoDigest
must bind the exact manifest, and its platform and ordered RootFS DiffIDs must
agree. Missing or inconsistent linkage fails before container creation.

Docker `.Id` is recorded only as a daemon-reported identifier and compared for
local container consistency. It is **not equated to the registry config digest**:
the recorded Docker identifier is the manifest digest, not the config digest.
This procedure trusts Docker's local metadata/content association, the host OS
and Python interpreter. It does not rehash local layers/config storage, verify
an image signature, establish ABI compatibility or attest the runtime.

See the OCI v1.1.0 [manifest descriptor](https://github.com/opencontainers/image-spec/blob/v1.1.0/manifest.md)
and [config/DiffID specification](https://github.com/opencontainers/image-spec/blob/v1.1.0/config.md),
and Docker's [image inspection](https://docs.docker.com/reference/cli/docker/image/inspect/)
and [container creation](https://docs.docker.com/reference/cli/docker/container/create/)
interfaces. Installed CLI help was checked without executing the image.

## Source And Lifecycle

After independent review and explicit execution approval:

```sh
python3 -I -S scripts/record-engine-image.py --slug engine-image-diagnostic
```

The recorder binds its bootstrap and helpers to raw blobs from one commit,
ignoring working files, ignored bytecode, Git replacements and lazy fetches.
Bounded direct-source compilation verifies helper hashes before execution.
The committed overlay checker still validates all five candidate stages in
memory; no stage is applied to the image. Shared helper changes are hash-bound.

An offline, cache-only build of the same commit's `nib` validates evidence using
the existing schema. A missing Go toolchain/module cache or schema rejection
prevents publication; it does not trigger downloads or image execution.
Production and the schema regression use the same sealed environment, with
`GOTOOLCHAIN=go1.27.2`, `GOPROXY=off` and `GOSUMDB=off`. An older host bootstrap
is queried in local-only mode to locate the already-cached pinned toolchain;
the recorder checks its reported version and invokes it directly, avoiding the
bootstrap's checksum-database-dependent updater. Missing or mismatched cached
toolchains fail closed. Compiler binaries and local caches remain trusted host
inputs, not attested artifacts.
Go is resolved once from the host's `PATH` to an absolute executable, including
CI's setup-go installation outside the fixed Git/Docker tool path. Sanitized
`go env` with `GOENV=off` and the account's real home discovers default cache
locations; explicit `GOCACHE` and `GOMODCACHE` take precedence. Only already
cached tools/modules can be used. Other ambient Go/Docker/Git settings, proxy
variables and credentials are not forwarded to tool children.
Before **every** Go invocation, including discovery and version qualification,
the environment redirects `TEST_TELEMETRY_DIR` to a private directory with an
`off` mode file and sets `GO_TELEMETRY_CHILD=2`. This disables counter writes and
detached sidecar startup without changing the account's telemetry settings.
The controls are backed by the inspected Go 1.24.2 and pinned 1.27.2 sources:
the [counter shim](https://github.com/golang/go/blob/go1.27.2/src/cmd/internal/telemetry/counter/counter.go),
[telemetry shim](https://github.com/golang/go/blob/go1.27.2/src/cmd/internal/telemetry/telemetry.go)
and [sidecar selector](https://github.com/golang/go/blob/go1.27.2/src/cmd/vendor/golang.org/x/telemetry/start.go).
They must be rechecked on toolchain upgrades. `GOPROXY=off` and `GOSUMDB=off`
alone do not disable Go telemetry. Tests use actual Go with a temporary
telemetry-on account and a confined counter-positive control; sidecars/uploads
remain disabled in both cases.
Only local Docker endpoint discovery reads the user's selected context.

The container is created stopped and inspected before start. It uses `runc`,
network-none, no devices/GPU requests, no ports, a read-only rootfs and payload,
UID/GID 65532, no capabilities, no-new-privileges, private namespaces, no restart
or healthcheck, and no Docker log driver. The image entrypoint is replaced with
isolated `python3 -I -S -B`; inherited image environment values are cleared.
The sole bind mount contains committed diagnostic sources and the contract.
It contains no host credentials or Docker socket. The enclosing host temporary
directory remains private; payload files are readable by the container UID.

Limits: 2 CPUs, 1 GiB memory/swap total, 32 PIDs, 600 seconds for the attached
diagnostic, 30 seconds per management call, 2 MiB stdout and 64 KiB stderr.
Generic filesystem limits remain 100,000 entries, 32 GiB total, 4 GiB/file and
300 seconds per tree. Only the exact image-specific diagnostic policy above can
use 225,000 entries; it changes none of the other limits. Root observations allow four roots, 4,096 entries/root,
2,048 distribution records/root and 64 hooks/root. Hook reads stop at 16 KiB.
No successful completion is inferred from partial/oversized output.

Cleanup targets only a randomly named, ownership-labelled container. An
independent successful empty listing must confirm absence. Uncertain creation
or runtime completion remains a failure even after current absence. The cached
image is never deleted. Failed isolation checks never start the container;
foreign ownership prevents deletion and records unresolved cleanup.

## Evidence And Interpretations

Exclusive private `evidence/p0/YYYY-MM-DD-<slug>/{run.json,output.log}` publication
never overwrites a previous run. Evidence includes source, payload, validator
source, requested manifest and raw-config digests. Command paths are fixed and
repository-relative; host paths, endpoint socket, container UUID, environment
values and raw Docker errors are never published. Runtime errors use fixed
classifications. An invalid child report is discarded, not retained as raw logs.

The output separates root admission, selected-root admission, combined wheel
integrity, RECORD consistency, per-patched-file hashes and distribution metadata
inventory. The latter is not a complete inventory of every importable OS module.
Partial integrity checks are observations, not alternative admission paths.
Malformed metadata or an unsupported/failed full filesystem inventory preserves
earlier bounded root/hook observations with `collection:partial` and fixed
rejected/unavailable check statuses. Safely readable patched sources can still
be hashed; an unreadable source is never treated as confirmed absent. Such a
partial report cannot admit an overlay or produce a passing run.
Filesystem rejections may include `inventory_failure`: a fixed reason code and
a bounded relative path within the pinned public image. Symlink targets,
file contents and raw exception strings are never included. Unsafe or oversized
names are omitted. This diagnostic context does not change inventory admission:
links, non-regular files, unsupported native headers and exceeded limits still
fail closed. Previously recorded reports without this optional field stay valid.
An overlay rejection makes `run.json` fail even if diagnostic collection and
cleanup complete successfully. Successful software checks still leave the
candidate blocked and ABI/hardware validation false.

The recorded diagnostic observed two exact public hooks:
`distutils-precedence.pth` (`2638ce9e2500e572a5e0de7faed6661eb569d1b696fcba07b0dd223da5f5d224`)
and `nvidia_cutlass_dsl_packages.pth` (`cc710b99ddb3d76361a70461461d274212a979d00c381d821fce5bb3ad9cf246`).
Their text is included only after exact hash verification; unknown contents are
omitted. This is a content-disclosure pin, **not a hook execution allowlist**.
The reported populated distro package root also remains rejected. Sealed Python
paths or reviewed image normalization are future work, not part of this task.

## Local Verification

```sh
python3 -I -m unittest discover -s scripts -p 'test_engine*.py'
```

Tests use real filesystem fixtures, resolver observations, fake Docker lifecycle
responses, the preserved diagnostic artifact and authoritative schema validation
contracts. They never pull, build, start or modify an actual image. The artifact
regression validates the recorded failure; it does not repeat the diagnostic.
The PR28 acceptance regressions remain enabled.
Metadata-census regressions exercise real sparse files, hard links, symlinks,
FIFOs, no-content/no-link-target reads, root ambiguity, directory replacement,
partial limits/errors, counter/report tampering, source-bound mode selection,
fake Docker cleanup and authoritative evidence schema validation. They do not
execute the image or relax the original inspector.
The recorded census regression checks the unchanged artifact hashes, child
contract, source/payload references, exact totals, intentional qualification
failure and schema validity without repeating the image run.
Entry-policy tests exercise exact 100,000/100,001 and 225,000/225,001 boundaries
with lazy synthetic directory entries, plus real filesystem tests for preserved
byte/time/depth/link/native gates, rejected unknown identities and unchanged
startup/root admission. No changed-image diagnostic has been executed by tests.
The bounded-integrity artifact regression preserves both raw hashes, validates
the report and authoritative evidence schema, and asserts the exact native-header
rejection and unavailable integrity checks without repeating the image run.
