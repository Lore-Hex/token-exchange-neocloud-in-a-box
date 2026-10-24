# Read-only selected checkpoint view

The selected public checkpoint cache uses `0700` directories and `0600`
files. A non-root serving container cannot read it. Do not relax that cache
recursively: it may be an active test input, and broad permission changes
could expose unrelated private files.

[The preparer](../../host/prepare-selected-weight-view.py) makes a **separate
copy** of only the 79 files in the selected checkpoint's pinned
[manifest](../../models/deepseek-v4.1-flash-1017-uncensored-fp8/1/files.sha256).
The complete copy is 510,311,056,155 bytes. The preparer also requires spare
space equal to the greater of 10 GiB or 10% of that size. These are disk-space
requirements, not a claim of GPU memory requirements or tested runtime fit.

It reuses the hash-pinned public-cache copier, inventory checks, subprocess
supervision, and exclusive publication helper. There is no download, mount,
network access, inference, credential handling or source deletion. The CLI
cannot select another model, manifest, file list or file mode.

## Boundary

The source must have exactly the pinned inventory and hashes. Additional
files, symlinks, hardlinks and special files are rejected. The source is hashed
before copying; copied bytes and stable source identities are checked; the
completed copy is rehashed after its permissions are finalized. Source files
and directory identities are checked again before publication. Original
source modes remain unchanged.

Only newly created public model files become `0444`, and their directories
become `0555`. A private `0700` wrapper is published with an atomic no-replace
rename. Thus the container bind source is **`DESTINATION/model`**, not
`DESTINATION`. The destination's existing private parent remains `0700`.
The wrapper also avoids requiring a rename of a non-writable directory on
platforms that reject that operation for non-root users.

Publication fails closed if the filesystem does not support the exact
no-replace primitive. A concurrent destination is never replaced. Failed
partial copies remain in private staging for inspection; nothing is deleted
or retried automatically. A timeout after publication but before the worker's
acknowledgment remains a failed, unproven result, not success inferred from
directory existence.

An ordinary host administrator can still change these files. Permissions are
not attestation, immutability or a confidentiality guarantee. The service must
mount `DESTINATION/model` read-only and independently validate the complete
manifest before startup. This helper does not change the current non-root
container, cache/JIT or confidential-hardware acceptance gates.

## Operator interface

Use a clean committed checkout; producer identifies that exact revision.
All paths must be absolute, disjoint and free of symlink components. Output
and destination parents must already exist and be private. Run only after
approving the storage, host time and a bounded maintenance interval; do not
copy concurrently with a job that mutates the source.

```sh
python3 -I -S -B host/prepare-selected-weight-view.py \
  --source "$PUBLIC_WEIGHT_CACHE" \
  --destination "$NEW_PRIVATE_VIEW_DIRECTORY" \
  --output "$PRIVATE_EVIDENCE_DIRECTORY/view.json" \
  --producer "$REVIEWED_COMMIT" --timeout 1800
```

The Linux CLI requires root to prepare root-owned public files; the runtime
remains non-root. An isolated supervised worker has the requested timeout
minus a 60-second cleanup reserve. SIGTERM/interrupts use the same bounded
cleanup path. The receipt contains only revision/hashes, UTC times, fixed
categories, counts and modes. It contains no paths or file contents. No
positive result is emitted unless the worker returns the exact fixed packet.

## Verification

```sh
cd scripts
python3 -B -W error::ResourceWarning -m unittest -v test_selected_weight_view
```

The tests use small synthetic public files: unchanged originals, exact new
modes, destination races, post-copy and post-permission corruption, mode and
filesystem failures, insufficient space, deadline expiry, links, unexpected
private files, pinned manifest totals, and closed receipt/signal behavior.
These are software tests, **not a 510 GB copy or non-root GPU inference
result**. Actual prepared-container verification remains separate.
