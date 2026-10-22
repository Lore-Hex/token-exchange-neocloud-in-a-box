# P3.T1: Stable Ubuntu Package Retrieval

The native installation workflow stopped before package execution in
[attempt 1](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39774287064/attempts/1)
and [attempt 2](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39774287064/attempts/2).
Both original content-free fetch logs are preserved unchanged under
[removed-ubuntu-input](../../evidence/p3/2026-10-22-removed-ubuntu-input/).
Both terminate with curl's HTTP failure code 22 for the same pinned archive:

- `ca-certificates_20260601~24.04.1_all.deb`
- 139,430 bytes
- SHA-256 `6bac2a01979e210d9eac1d4d56747ec709ea60654744d66705dc3c36e7629e50`

The earlier assumption was that hash-pinning a package on Ubuntu's moving
archive was sufficient for repeatable retrieval. It protects integrity, but
does not preserve availability when a package leaves the live pool. Retrying
the unchanged workflow cannot repair a missing archive. The archived signed
indexes still authenticate the required bytes; changing dependency versions to
make the build pass would be a different, unreviewed image.

## Fix

Fetch Ubuntu package files from the fixed Canonical snapshot
`https://snapshot.ubuntu.com/ubuntu/20261020T000000Z/` instead. Canonical documents
dated archives for reproducible deployments in its
[Snapshot Service documentation](https://snapshot.ubuntu.com/) (read 2026-10-22).
The date matches the existing build-input metadata snapshot. All existing
package names, versions, sizes, hashes, signed indexes, dependency checks and
offline installation gates stay unchanged. NVIDIA's separate archive stays
unchanged. Metadata is still read from the authenticated, hash-pinned index
archive in the native workflow.

The snapshot origin and date are source-pinned, not caller inputs. Only package
paths below `pool/` are permitted. No redirect, ambient proxy, alternate mirror,
credentials, fallback version or HTTP-error retry is introduced. A missing or
changed snapshot package still fails closed. Canonical currently states an
intended retention of at least two years, not permanent availability; a future
archive or pin update requires review, not silent fallback.

## Verification and Limits

Focused tests cover the exact package identity, unchanged NVIDIA/metadata
locations, deduplication, path traversal, origin/date substitution, private URL
fields, and preserved original failures. Existing tests still reject changed
bytes, oversized downloads, TLS/HTTP errors and global-deadline overruns.

```
uv run --project tools --frozen python -m unittest discover -s scripts -p 'test_native_tools.py'
uv run --project tools --frozen python -m unittest discover -s scripts -p 'test_concurrent_fetch.py'
make check
```

This is a package-retrieval fix, not a successful native build, appraiser,
initrd/UKI, confidential boot or hardware claim. The original failing workflows
remain failures. A new full native workflow must pass before claiming the
downstream runtime and root-verity stages work.
