# P2.T3 offline original image input packaging

Software only. This adds a local composer for the six non-image components of
the [input-set transfer adapter](p2-t3-original-image-input-transfer.md). It does
not transfer, admit, import, load, run, deploy or establish confidentiality.
It does not complete P2. The existing image, preparation, importer, receiver,
materializer, validators and historical evidence remain unchanged.

## Interface and reuse

[`scripts/original_image_package.py`](../../scripts/original_image_package.py)
exposes `package(repository, preparation, build, verification, output, *,
preparation_sha256, build_sha256, verification_sha256, deadline)`. There is no
CLI, image-path argument, network/SSH operation, operational manifest builder,
transport configuration, target discovery, approval writer or importer call.
The caller must authenticate this committed module and the three original
input pins independently before invocation. A colocated self-declared checksum
is not authentication. All paths must be canonical absolute paths.

No existing helper produces this joined six-component set. The composer reuses:

- `host/receive-original-image-inputs.py`: fixed d18/tree/helper pins, inventory
  caps, deadline checks, private ownership primitives and `joined_inputs`.
- `host/verify-selected-image-archive.py`: authenticated helper graph, private
  no-follow reads, strict JSON, exclusive durable records and preparation
  validation. Final joins execute this exact verifier from the verified d18 tree.
- `host/prepare-lab.py`: `expected_tree` and the adapter's existing materialization
  path. No extractor or deadline-resetting `checkout()` wrapper is added.
- `scripts/e2e/snapshot.py`: bounded Git process-group capture and blob identity.
- `scripts/original_image_input.py`: reopened output hash and inode checks.

The adapter is authenticated before execution with SHA256
`4141ad7b9bb778507ee5f205de619e75237233bd89eef72fd6030bd0cc620fde`.
Its compiled helper pins and the verifier's transitive preparation-source pins
are enforced unchanged. The package receipt separately binds the packager's
current committed producer and exact script bytes. An uncommitted packager
cannot produce a successful package.

## Exact components

Image producer remains `d18aee4ce01062134d0e893c72555ef104a9ed84`.
`source-tree/payload` is its exact `git ls-tree -rlz --full-tree` output, whose
fixed SHA256 is
`78e561ec5fbf998ab211942de3c7f8e7ae6d4ed37b83f86b73461ba5204b0322`.
`source/payload` is an uncompressed tar of exactly those Git blobs. The current
checkout, ignored/untracked files, attributes, filters, hooks and replacement
objects do not supply source bytes. Lazy fetching, Git protocols, automatic
maintenance and inherited user/global Git configuration are disabled. The local
OS, Python/stdlib, Git executable and repository storage remain trusted inputs.

The complete pinned preparation is validated before packaging. No undeclared
file is included. `preparation-tree/payload` contains sorted regular-file blob
rows for the exact files plus the unchanged original `receipt.json`;
these rows are an inventory, **not Git provenance**. `preparation/payload` is
their separate uncompressed tar. Zero-byte wheel markers stay empty and must
satisfy the frozen validator's exact empty hash, checker inventory and prepared
source joins. Non-checker empty files remain rejected.

Both tars use sorted members, Git executable modes for source (100644 for
preparation), zero uid/gid/mtime and empty owner names. Only regular payload
members are written; deterministic PAX path headers preserve the existing
512-byte path contract. No symlinks, devices, traversal, duplicate names or
case collisions are admitted. Identical authenticated inputs yield identical
inventory/tar bytes; attempt receipts intentionally carry distinct ownership
and timing information.

`build/payload` and `verification/payload` preserve the original pinned records
byte for byte. The image descriptor is copied from the authenticated verifier
receipt only after the full d18 preparation/build/model/profile/helper/limits
joins pass. No image path is accepted, no image copy is made and no image bytes
are read or rehashed. `image_content_verified` is always false: this is not a
fresh verification of the image archive or its continued presence.

The composer reopens and hashes each output, then uses the unchanged adapter to
materialize both tars into retained `source-files/` and `preparation-files/`
and validate the complete joins. These trees remain private audit artifacts,
not another source/preparation producer. The resulting descriptors match the
existing transfer contract, but this library does not create its operational
manifest, select a transport, receive terminal acknowledgments or observe a
remote process exit.

## Bounds, ownership and failure

Implementation ceilings are unchanged: each inventory at most 1 MiB; each tar
at most 80 MiB; each expanded tree at most 64 MiB, 4096 files and 16 MiB per
file; build 16384 bytes; verification 65536 bytes; image descriptor at most
10 GiB. These are rejection bounds, not capacity reservations or approvals.
The caller supplies one absolute deadline on this process's monotonic clock,
no later than 1800 seconds after entry. Every Git invocation uses its remaining
budget; the original cutoff is checked after capture cleanup, output rehash,
validation, journal writes and final descriptor cleanup. No phase rearms it.

The output's private existing parent must be owned by the caller and mode 0700.
The new output basename is a caller-chosen fresh 32-hex attempt ID, disjoint
from source/preparation/code trees. Before output creation, the existing
no-follow directory helper validates every component of each input root and
the output parent; input-root and parent symlinks are rejected, not resolved
and adopted. Containment checks use these validated paths and compare the
output parent's ancestor device/inode identities against the retained input
root descriptors. A rejected alias creates no output, intent or payload and
does not mutate an input. The root is exclusive and its identity is
checked through the retained parent/root descriptors. Durable `intent.json`
precedes payload writes; component directories are exclusive 0700 and files
exclusive/no-follow 0600. Each file and containing directory is fsynced.
Materialized intermediate directories are created privately by the existing
adapter before extraction, including under normal umask 022.

Failure retains the attempt, original records, partial payloads, validation
trees and any previously written receipt. Where time and storage still permit,
`failure.json` records a fixed stage and error class, never raw exceptions,
paths or bodies. If an interruption, filesystem/journal error or deadline
prevents that record, the durable intent and retained partial state remain
failure/uncertainty evidence. No removal, overwrite, adoption, resume or retry
is provided. A different directory/attempt must not be used to evade unresolved
failure ownership. Rejected preconditions before any mutation create no claim.

`receipt.json` is only a local observation. Its existence is not successful
return: subsequent cleanup can fail or cross the deadline. Consumers must
require observed successful return and independently authenticate the output
before later use. All transfer/admission/import/runtime/deployment/confidential
and cleanup-confirmation flags stay false. Original evidence is never relabeled.

Python cannot preempt synchronous filesystem stalls, and the reused process
capture has bounded cleanup grace that can overrun the caller's cutoff; late
return is rejected, not described as guaranteed termination. The caller must
provide an isolated, externally bounded process, private durable scratch,
capacity/inode reserves, same-UID writer/mount-alias exclusion and monitoring.
This is not a general mount-topology or concurrent-writer controller. Memory can
hold a full bounded preparation or tar plus validator/Git allocations; these
byte limits are not an RSS limit. Disk includes both tars and retained expanded
trees (up to 288 MiB together), two inventories, records and filesystem overhead.
Source packaging performs one bounded Git blob read per file; no throughput or
completion-time claim is made. Budget, no-overlap, target/daemon/storage
ownership and any operational approval remain external gates.

## Verification handoff

Author work is static only: no test, Docker, actual packaging, archive read,
upload, import, build or runtime operation was performed. Parent-run command
from the committed checkout in an isolated offline Git-capable environment:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -p 'test_original_image_package.py'
```

The test environment needs read-only public source and complete d18/current
Git objects, plus bounded writable private scratch; it needs no network,
Docker socket, GPU or operator inputs. Fixtures exercise deterministic member
bytes/modes, empty files, bounds, private/exclusive outputs, substitution,
retained failures, deadlines, original record copies and a real full-source
join with the frozen validators. The positive integration uses a tiny generated
preparation/build/verifier fixture, deletes its synthetic image before packaging
and never uses an actual image artifact. It is software-only test data, not
official transfer or image evidence. This follow-up requires new independent
exact-head review, parent-run focused/related tests and all required CI.

## Preserved verification history

For candidate `d0bbaae6f81479cdcb2b90eae5f66e163b6faaaf`, the parent reported
22 focused tests PASS in 12.531 seconds and 169 related tests PASS in 23.101
seconds, with strict ResourceWarning handling in the same offline, 2-CPU,
1-GiB, noexec-scratch container. Documentation links, whitespace and gitleaks
also passed. These are parent-reported software results, not operational
evidence or author-run checks.

Independent exact-head review of that candidate was **BLOCKED**, with one P2
finding: lexical input containment allowed a symlink preparation/repository
alias to create output claims and payloads inside the original real input
before later rejection. Those passing tests did not discharge the finding.
This follow-up adds pre-mutation no-follow/identity validation and regressions
for both input roles, including root aliases, input-parent symlinks and
output-parent symlinks. The regressions require zero input/output mutation and
no journal or payload call. The author has not run them; retesting and a new
independent review remain required.
