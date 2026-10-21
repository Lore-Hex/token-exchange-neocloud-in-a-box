# P2.T3: Bundled TVM dialect aliases

The [tenth B300 trial](../../evidence/p2/2026-10-21-b300-tvm-dialects/run.json)
completed application validation but failed during engine startup after 256047 ms.
The [original diagnostic](../../evidence/p2/2026-10-21-b300-tvm-dialects/output.log)
reports a worker `ValueError` at the sealed meta-path check, reached immediately
after bundled TVM imported `tvm.script`. Cleanup passed; no OOM was reported.
There is still no successful DeepSeek inference or confidential-service claim.

## Source and Fix

The [pinned TileLang 0.1.12 wheel](https://pypi.org/project/tilelang/0.1.12/#files)
has SHA-256 `bbeb5573cbe2544a51a5c9a6cc737c5e3b5c2d95411caa3687fd9b08eb9d5f97`.
Its bundled `tvm/script/__init__.py` has SHA-256
`dcf67eed9023c21aab21c3db4459b5f1e3adb715e5ad4d834e85700575ee2014`.
Lines 204-205 append `_DialectRedirectFinder` to `sys.meta_path`. The only
in-tree registrations are `tirx -> tvm.tirx.script` and
`relax -> tvm.relax.script`. This is distinct from the earlier optional TVM
probe and TileLang search-path problems.

The source-pinned AST adapter removes exactly this registration. The existing
sealed finder resolves these two dialects and their parser/builder aliases
inside the already approved bundled TVM tree. Real source packages take
precedence, matching upstream. It does not install or delegate to an arbitrary
finder, add search roots, accept out-of-tree dialects, or change spawn policy.
Registry/module replacement, unexpected mappings, source drift and reloads
remain fail-closed. An incomplete or failed registration cannot re-enable it.

## Verification

`python scripts/test_tilelang_loader.py` exercises aliases, physical-package
precedence, unchanged import paths, fresh interpreters, spawned children and
grandchildren, plus negative registry/source tests. The `--public-source` mode
checks and compiles the exact upstream adapter without executing upstream code.
Full `make check` and all final-head hosted checks are required before merge.
These are software checks, not a successful model or attestation result.
