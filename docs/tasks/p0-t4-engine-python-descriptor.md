# Read-Only Python Layout Descriptor

**Implemented and tested with synthetic files and host stdlib children. No actual
image run yet.** This extends the existing committed-source image recorder; it is
not an engine launcher, image application, ABI check or hardware qualification.
The candidate remains `BLOCKED`, `deployable:false`, `image:null`. No default
overlay admission rule or runtime profile changes.

## One Bounded Collection

After independent source review and separate approval, the parent can run:

```sh
python3 -I -S scripts/record-engine-image.py --python-descriptor --slug official-vllm-python-layout
```

The existing exact image/config pins, cached-image-only preflight, raw OCI binding,
offline authoritative schema validation, private no-overwrite evidence and
owned-container cleanup are reused. No pull, build, apply, package installation,
weights, network, GPU, ports, credentials or engine/plugin import occurs. The
image root and source payload stay read-only. Only this mode adds a 16 MiB
`/tmp` tmpfs (`rw,nosuid,nodev,noexec`) for stdlib forkserver sockets; its exact
configuration is checked before start. Docker's existing 1 GiB, two CPU, 32 PID
and 600-second attached-process limits remain in force.

Independent sections retain bounded observations when another section fails:

- Interpreter and base-interpreter file hashes, explicitly resolved symlink
  chains, prefixes, isolated `sys.path`, sysconfig library/script paths, cache
  tag/prefix, extension suffixes, SOABI/MULTIARCH and debug/shared build fields.
  Only fixed fields and public image `/usr`, `/lib`, `/lib64`, `/bin` paths are
  reportable. No environment, host identity or arbitrary error text is emitted.
- Installed stdlib source hashes for startup/import, subprocess, spawn,
  forkserver, resource tracker and context/reentry code. A disk source hash does
  **not** prove that the interpreter loaded it instead of frozen code or bytecode.
- Metadata-only scans of stdlib, dynload, selected/alternate package roots and
  the selected root's CUTLASS `dsl_packages`: entry/type/byte counts, startup-hook
  counts, source-name bytecode counterparts and bounded top-level collisions.
  These walks never read bytecode, follow links or execute hooks. Exact hashes
  of the two known hooks and optional CUTLASS initializer are separate reads;
  missing/unreadable required observations make collection partial.
- The exact [PR35 entry-point map](p0-t4-engine-packaging-contract.md), full
  RECORD/source verification and sealed unlisted-bytecode removal-plan digest,
  count and bytes, plus legacy/sourceless/ambiguous counts. The existing exact
  225,000-entry image policy and four published AOT stub classifications are
  diagnostic-only inputs. No file is removed. Unknown entrypoints are rejected,
  not printed. Source-name scan counts alone are not verified removal plans.
- All five candidate stages are checked/applied to the pinned source fixtures
  **in host memory** using the existing preparer. Its context/bundle receipt is
  source-bound into this payload. Installed base-source checks are independently
  required by packaging verification; the image is never patched.
- Fourteen installed API/engine/worker-chain files are hashed against
  [fixed upstream pins](../../scripts/engine-python-source-pins.json). Mismatches
  remain visible as mismatches, not hidden by a collection-complete label.
- Stdlib-only fresh, fork, spawn, spawn-grandchild and forkserver canaries report
  isolation flags, exact ordered-path digest and bootstrap/reentry assertions.
  Unavailable or failed canaries keep collection partial.

Descriptor source reads cap each file at 64 MiB and cumulative reads at 256 MiB.
Metadata scans cap each root at 300,000 entries and reuse the census depth/size
limits and 120-second per-scan deadline. All image filesystem observations share
a 300-second deadline; full wheel hashing retains existing inventory byte/file
limits. The two canary subprocess groups each have a 45-second limit and 8 KiB
output cap. The descriptor report is capped at 128 KiB. Report validation rejects
unknown fields, unsafe paths and inconsistent complete/qualified claims.

## Canary Boundary

The proposed order is measured stdlib, dynload, CUTLASS DSL path, selected package
root, with duplicates removed. Alternate package roots stay on disk but are
excluded. `.pth` files are inert under `-I -S -B`; no `site.main`/`addsitedir`
call is made. A canary-only import finder rejects filesystem imports outside
stdlib/dynload, including selected-root dependencies. Proposed paths are being
observed, **not approved for engine execution**.

The fixed wrapper reestablishes flags/paths before dispatch to the exact supported
stdlib spawn/forkserver/resource-tracker bootstrap shapes. It never evaluates
received `-c` text; AST validation accepts only those fixed calls and file
descriptors. Unknown commands, `-m`, arbitrary `-c`, preload lists and flag/path
overrides reject. Spawn isolation is established before either preparation or
process-object unpickling, not in the child target. Fork instead inherits the
already-isolated parent; fresh/fork have no initial unpickle operation. The
`before_unpickle` assertion means established-before-dispatch, not evidence of
an unpickle in those two modes. No untrusted pickle is supplied by these tests.

This does not qualify vLLM's actual worker startup. Its pinned
[multiprocess executor](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/v1/executor/multiproc_executor.py)
uses the context selected by
[system_utils](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/utils/system_utils.py).
Both upstream fork/spawn modes have stdlib canaries here, not engine-path tests.
The pinned [API entry](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/launchers/api_server/entry.py#L42)
also has a forkserver branch that preloads `vllm.v1.engine.async_llm`; the synthetic
empty-preload forkserver canary does **not** qualify that branch.
[NUMA configure_subprocess](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/utils/numa_utils.py#L508-L544)
can wrap the existing multiprocessing executable, pass it through
`NUMACTL_PYTHON`, and restore it afterward; disabled NUMA leaves it unchanged.
Its direct
Python/NUMA route, engine process targets and additional executor modes remain
unqualified; no sealed engine command or guessed launch flag is generated.

The bootstrap contract follows pinned CPython 3.12.3
[spawn](https://github.com/python/cpython/blob/v3.12.3/Lib/multiprocessing/spawn.py),
[forkserver](https://github.com/python/cpython/blob/v3.12.3/Lib/multiprocessing/forkserver.py)
and [interpreter flags](https://github.com/python/cpython/blob/v3.12.3/Lib/subprocess.py#L300-L358).
`-B` suppresses bytecode writes, not reads; `pycache_prefix` is not propagated by
that flag helper. This task does not adopt a cache-prefix strategy or claim stale
bytecode prevention. PR35's explicit source-verified removal contract is still
required before any future application, and dependency/stdlib cache policy plus
real engine child isolation require subsequent reviewed work.

## Verification And Result

```sh
uv run --project tools --frozen python -I -m unittest discover -s scripts -p 'test_engine*.py'
```

Tests use actual synthetic filesystem operations, real host stdlib children and
fake Docker lifecycle responses, including partial/tampered reports, FIFO/link/
size failures, unknown bytecode, dependency-import rejection and cleanup errors.
Host sandbox policy must allow local Unix sockets for forkserver tests; no network
or actual container is needed. A future complete collection still records
`result:fail`, `overlay_admission:not-evaluated`, exit 1 and no ABI/GPU/TEE claim.
No new actual-image evidence is included in this source change.
