<!-- SPDX-License-Identifier: Apache-2.0 -->

# P0.T4 local weight integrity

## What changed

Add `nib model check-local <pack> <staged-directory>` to verify actual staged
bytes against the trusted complete model-pack manifest. This is P0 operator
tooling preparation, not the start or acceptance of a gated P2/P5 hardware run.

The shared manifest parser retains all schema, ordering, path, size and total
checks, returning no partial results on failure. Local verification checks the
exact file set, rejects symlinks and special files, hashes with bounded memory,
and checks descriptor identity and file metadata before/after reading. It uses
root-confined file operations, no-follow/nonblocking leaf opens on Linux/macOS,
and fixed sanitized errors. Cancellation is checked between reads.

## Verification

Tests use small synthetic model files only. Coverage includes zero-byte files,
nested paths, missing/extra files and directories, same-size corruption,
truncation, inode replacement with identical bytes/mtime, symlink/FIFO swaps,
unsafe roots, cancellation and diagnostic redaction. Existing parser mutation
fixtures also exercise the shared typed parser and forbid partial output.

Focused and full checks, hosted CI and independent final-head review must pass
before merge. The PR records exact commands and outcomes.

## Limits

No full-model download, GPU, cloud mutation, external inference or confidential
success is claimed. A quiescent staging directory is required. This is not a
snapshot: mutable files can change after reading, and a malicious writer may
restore metadata. No result is used for guest admission or replaces the later
dm-verity/measurement gates. Kernel-blocked filesystem I/O cannot be forcibly
cancelled by Go context checks. See [operator guidance](../../models/README.md).
