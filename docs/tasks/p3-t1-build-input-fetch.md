# Fixed Guest and Tools Downloads

`scripts/fetch_mkosi_tools.py --include-guest` fetches the union of the existing
203-package tools lock and 145-package guest lock. It downloads 269 unique
archives (516,283,494 bytes) and seven metadata indexes. It does not resolve
versions, execute packages, install drivers or change either lock.

The default tools-only layout and its original limits remain unchanged.
Guest mode permits only the pinned Ubuntu archive/security hosts and NVIDIA's
exact Ubuntu 24.04 x86-64 CUDA repository. Ubuntu indexes use by-hash URLs.
An October 21 live fetch found the old `noble-security/main` by-hash URL
returned HTTP 404 after all 269 archives verified. NVIDIA's current index
also changed size. A live mirror is not a durable archive of reviewed indexes.
`--metadata` accepts the previously authenticated index files, checking every
size/hash before any network request. Wrong, missing or extra in-memory inputs
fail closed. It does not re-resolve dependencies from a newer index.
Without that argument, changed or missing upstream indexes cause rejection,
not an automatic pin refresh or fallback. Every archive/index must match its
locked size and SHA-256. Shared package records and index inputs must agree
exactly before anything is fetched.

```sh
python3 -I -S -B scripts/fetch_mkosi_tools.py \
  --include-guest --metadata /absolute/private/archived-indexes \
  --output /absolute/private/inputs
python3 -I scripts/prepare_mkosi_inputs.py \
  --metadata /absolute/private/inputs/metadata \
  --archives /absolute/private/inputs/archives \
  --mkosi-source /absolute/private/mkosi-source.tar.gz \
  --output /absolute/private/prepared
```

The caller must supply the separately verified, pinned mkosi source archive
described in [build input preparation](p3-t1-mkosi-build-inputs.md).
For the current lock, use the archived `metadata/` directory from the verified
prepared bundle, or export its deterministic index-only archive:

```sh
python3 -I -S -B scripts/fetch_mkosi_tools.py --include-guest \
  --metadata /absolute/private/archived-indexes --export-metadata \
  --output /absolute/private/index-export
```

This verifies all seven original hashes before exporting `metadata.tar.gz` and
its checksum; it makes no network request. A fresh builder can use
`--metadata-archive /absolute/private/metadata.tar.gz` instead of `--metadata`.
Archive decoding is bounded and rejects links, traversal, duplicates, unexpected
entries, ownership/mode changes and changed contents. It never extracts paths
from the archive to disk. The same seven-index archive supports either the
tools-only or combined fetch. An independently retrievable published archive
is still required before calling this a self-contained clean-host build.
The combined archive cache uses SHA-256 filenames consumed by that preparer;
the default tools-only layout remains compatible with the existing native probe.

All transfers share one 600-second deadline, have a maximum 60-second per-file
deadline and a size cap, and run without environment proxies, credentials,
redirects, retry or curl configuration. The combined plan is capped at the
preparer's 320 unique packages and 640 MiB total. Metadata remains capped at
32 MiB/file; guest archives at the existing 128 MiB/file cap. Files publish
only after every transfer verifies. A later installation must still reconstruct
and validate these inputs and enforce the reviewed resource/namespace boundary.

Sources are the previously authenticated, committed
[guest lock](../../image/inputs/r595-ubuntu2404.json) and
[tools lock](../../image/inputs/mkosi-tools-ubuntu2404.json). This is not a new
claim about upstream freshness, installation success or reproducibility.
Tests exercise exact union arithmetic, deduplication, changed NVIDIA metadata,
conflicting package/index inputs, path/host rejection and transfer bounds.
