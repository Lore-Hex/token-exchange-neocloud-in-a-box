# Original selected-image archive verification

Software-only preparation, not image import or deployment. The committed
[verifier](../../host/verify-selected-image-archive.py) authenticates an original
selected-service preparation, its separate original build receipt, and one exact
operator-pinned archive. It does not invoke Docker, build, load, pull, tag, delete,
mount, start a model, contact a registry, or authorize a cutover.

The [first actual local archive verification failed](../../evidence/p2/2026-10-29-original-image-archive-failure/README.md).
Its original receipt and log are retained. An empty provenance subject was
subsequently identified as a definite rejection condition. A separately
[rebuilt original image passed full local archive verification](../../evidence/p2/2026-10-29-original-image-verified/README.md).
The later [paired-owner image rebuild also passed local archive verification](../../evidence/p2/2026-10-29-original-handoff-image-verified/README.md)
with fresh source/preparation/build bindings. The original parser's subject
binding is unchanged. These archive results do not establish image import,
model-weight verification, consumer/mount execution, runtime or deployment.

## Closed reuse

The unchanged, SHA-256-pinned [Qwen archive validator](../../scripts/pilot/export.py)
runs in a fresh module instance, never shared through `sys.modules`. Only this
instance receives the source-owned selected limits. Qwen's existing 2 GiB archive,
4 GiB expanded-data and 300-second behavior, source hash, and receipts are unchanged.
No general OCI parser, repacker, alternate compression, or arbitrary limit CLI is
introduced. This profile requires gzip layers and an OCI image identity distinct
from its config identity; classic Docker/config-ID archives fail closed.

The existing parser verifies the entire archive SHA/size before traversing its
graph, every content-addressed blob (including unreferenced blobs), config hashes,
ordered uncompressed layer DiffIDs, descriptor sizes, one untagged runnable
Linux/amd64 image, and linked non-runnable BuildKit provenance. The wrapper observes
the parser's existing gzip digest calls to report expanded bytes and the complete
ordered DiffID list, including repeated layers. It does not count provenance JSON
as a runnable layer or treat provenance as attestation. JSON duplicate keys and
nonfinite numbers are rejected by this instance only.

The existing [preparation/build checks](../../scripts/selected_service_image.py)
authenticate all preparation files and join the separately pinned original build
receipt. The model must be the closed original profile and manifest, using
`original-long1m-graphs`. The archive's config must retain the nonroot selected
entrypoint with the exact prepared-source hash. No current-HEAD equality is imposed
on the older image builder. The verification producer is the operator-supplied
reviewed commit; the verifier and helper byte hashes are measured independently.
These are integrity bindings to separately trusted operator pins, not signatures.

The preparation validator's complete closed repository import graph is also
SHA-pinned. Every source is authenticated before any module in that graph executes;
fresh modules compile those exact buffered bytes, never a second filesystem read
or cached repository module. The temporary import context restores prior modules
after validation. Receipts include each helper's source hash and the canonical
module-map SHA-256. No existing dependency source or pin in another tool changes.
The two legacy direct-source loaders receive private `Path`/`exec` bindings so
their nested source reads also use the authenticated buffers. This does not
patch shared `pathlib`, builtins, or existing Qwen module instances.

## Bounds and private output

Limits are fixed in source: 10 GiB archive, 64 GiB total expanded layer hashing,
1 MiB JSON, 4,096 outer members, 128 layers, and 1,800 seconds. The Linux worker
sets hard 2 GiB address-space, 1,800 CPU-second, 128-FD, zero-core and 1 MiB
file-size limits. These are verifier limits, not Docker daemon resource limits or
an unpacked-filesystem capacity claim. Layer tar bytes include metadata and may
repeat shared layers; a later import needs its own storage/temporary-space budget.

The existing bounded process runner supervises a fresh isolated Python worker,
caps diagnostics, and attempts client-group cleanup on failure. The worker has a
wall alarm; the parent allows five additional seconds and the runner's existing
25-second cleanup reserve. Uninterruptible kernel I/O can defeat termination;
failure/timeout never asserts worker absence unless the runner confirmed it.

Inputs use anchored no-symlink regular-file reads and stable identities. Archive
and build receipt must be owned 0600 files in owned 0700 parents; preparation and
output directories must also be private. No artifact path or raw exception text
enters the result. An exclusive durable `.claim` precedes work. The complete final
0600 JSON receipt is atomically linked without replacement; claims and failures
are retained. Existing output/claim paths forbid replay. Immutable means no rewrite
by this tool, not protection against an administrator changing bytes later.

Accept only exit zero plus the matching terminal PASS receipt. A publication or
fsync failure, missing receipt, failure result, or unresolved worker exit is not a
successful verification. Successful receipts still say `image_loaded`,
`runtime_started`, `deployable`, `published`, `hardware_validated`, and
`confidential` are false. No automatic retry or fallback exists.

## Operator invocation

Run only after review, in an approved local Linux environment. Obtain all hashes
and sizes from independently retained source-bound records, not archive contents.
Keep real pins, host identities, private paths and receipts out of the repository.

```sh
python3 -I -S -B host/verify-selected-image-archive.py \
  --preparation "$PREPARATION" --preparation-sha256 "$PREPARATION_SHA256" \
  --build-report "$BUILD_REPORT" --build-sha256 "$BUILD_SHA256" \
  --archive "$ARCHIVE" --archive-sha256 "$ARCHIVE_SHA256" \
  --archive-bytes "$ARCHIVE_BYTES" --producer "$REVIEWED_VERIFIER_COMMIT" \
  --output "$PRIVATE_RECEIPT"
```

[Focused synthetic tests](../../scripts/test_selected_image_archive.py) cover parser
reuse, byte/digest/lineage rejection, complete expansion/DiffIDs, unsafe inputs,
private publication, crash replay and worker-failure semantics. Tests use tiny
generated archives; they do not verify a real image, call a daemon, use a GPU or
mount NFS. The unchanged [Qwen export tests](../../scripts/test_qwen_export.py)
remain the compatibility regression suite. Target image load, daemon identity,
old-image preservation, load-completion uncertainty, weight staging and cutover
remain separate unfinished gates described by the
[offline handoff contract](../operators/qwen-export.md#offline-handoff).
