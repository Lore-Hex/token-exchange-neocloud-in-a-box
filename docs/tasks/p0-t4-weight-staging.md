<!-- SPDX-License-Identifier: Apache-2.0 -->

# P0.T4 bounded weight staging

## What changed

`nib model stage <pack>` produces an offline transfer plan. Actual downloading
requires `--download`, an explicit body-byte budget, an overall deadline and an
absolute private destination. This is P0 model-pack preparation; it does not
start or satisfy gated P2/P5 hardware work.

The manifest is loaded once. Root-confined writes, exclusive directory locking,
bounded directory enumeration and no-follow/nonblocking reads reuse the local
integrity checker. A separate streaming HTTP client avoids the metadata client's
short whole-request timeout and buffered-body behavior. File hashes and exact
sizes are checked before atomic no-overwrite publication; file and directory
sync errors fail closed. Existing complete files are independently verified
before reuse. The final exact-set verification uses the original contract,
not metadata reloaded after the transfer.

The HTTPS redirect allowlist comes from Hugging Face's
[network requirements](https://huggingface.co/docs/hub/datasets-downloading#network-requirements),
reviewed 2026-10-19. Only `huggingface.co` pinned-file and resolve-cache paths and
the four explicitly listed blob CDN hosts are accepted. No wildcard, dynamic
allowlist expansion, credentials, cookies or environment proxy is used.
The [download guide](https://huggingface.co/docs/huggingface_hub/guides/download)
documents immutable full-commit revisions and resolve URLs. This implementation
uses whole-file HTTP transfer, not Xet reconstruction, partial Range resume or
the Hugging Face cache directory format. Upstream endpoint changes may require
reviewed code updates; they must not silently weaken the host or integrity gate.

## Verification

Focused command:

```sh
go test -race ./internal/modelpack ./cmd/nib
```

Full regression commands:

```sh
make check
make test-e2e
```

Tests use small synthetic files and injected HTTP transports, with no weight
download or GPU dependency. They cover complete transfer and zero-byte files,
verified-file restart after failure, private directory ownership/permissions,
concurrent locking, corruption, extras, symlinks, insufficient budgets, time
limits, cancellation before and during reads, oversized/truncated/incorrect
bodies, bytes returned alongside errors, destination races, redirect limits,
disallowed redirects, unsupported HTTP status/encoding, missing directories,
crash leftovers, changed metadata and sanitized diagnostics.

Ordinary local verification continues to require the complete file set. Partial
inventory mode is private to staging. Metadata reads now share the no-follow,
nonblocking open path and verify descriptor identity before/after reading.
Independent review also required syncing the root's parent, retrying directory
syncs on fully reused runs and explicit crash-recovery guidance. Failure/retry
tests cover both new-root and published-file sync errors. The PR records final-head review,
exact local command outcomes and hosted CI results before merge.

## Not done

No cloud, GPU, paid inference or production change. No full DeepSeek download,
power-loss or disk-exhaustion hardware test, model benchmark, dm-verity volume,
confidential engine or vendor attestation result. A trusted pack, private local
filesystem and quiescent directory are prerequisites, not a malicious-writer
security boundary. A local integrity proof is not a runtime admission token.
Unknown hardware, missing confidential-engine patches/rights, vendor keys and
run approvals remain blockers documented in [gates](../gates.md) and
[model sources](../../models/SOURCES.md).

The [operator instructions](../../models/README.md) give exact DeepSeek byte
limits and restart behavior. Keep individual provider deployment details and
secrets outside this reusable Apache-2.0 repository.
