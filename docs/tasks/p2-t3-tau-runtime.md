# Offline tau runtime inputs

Status: executable **offline source/wheel input validation**, with synthetic
archive tests. No upstream package is installed, imported or executed. No live
transport, task evaluation, benchmark score, image execution or qualification.
This is a prerequisite for the [tau plan](../benchmarks/tau-validation.md), not
its completion. The existing [manifest/budget](p2-t3-tau-preflight.md) and
[trial aggregation](p2-t3-tau-aggregation.md) contracts are unchanged.

## Source and dependency closure

`bench/tau-runtime-lock.json` extends, without changing, the 59-input audit in
`bench/tau-upstream-pins.json`. It pins 273 source/data files, 28,154,884 bytes,
including **all 252 files under `src/tau2`**, empty files and package
initializers included. The earlier three-domain inputs remain byte-identical.
Other domains' data, voice assets and external tokenizer downloads are not
part of this text-only input set.

The immutable upstream commit is
[`fc0055dc4e0a316c3f83133267fbd6faaa770992`](https://github.com/sierra-research/tau2-bench/tree/fc0055dc4e0a316c3f83133267fbd6faaa770992).
The public [source archive](https://codeload.github.com/sierra-research/tau2-bench/tar.gz/fc0055dc4e0a316c3f83133267fbd6faaa770992)
has SHA-256 `7a227036d07fdeb088dbd0c99fdb40ee9b9e0ebc06aadbc4dec1217b3383e57e`.
Git's immutable commit/tree API links commit root
`4837da1c2b310152f63d3d7987f4325183ca6f7c`, `src` tree
`cb6bdc1750b81e8e6789eeeca195f06e7cac801f`, and `src/tau2` tree
`a11f9406b7c54d840a7006900c9e4d085059d1ec`.
The validator reconstructs the last tree from every package file's mode/name
and Git blob hash, and compares every SHA-256/length to the committed catalog.
The archive is read as data without extraction. Unselected archive members are
not runtime inputs. The upstream MIT license remains an included pinned input;
any later redistribution must retain it and all dependency notices.

The target reuses the repository's existing `scripts/e2e/pins.json` Python
3.12.13 slim-bookworm image digest, Linux/amd64, CPython `cp312`.
Debian 12's documented GNU libc version is
[2.36](https://www.debian.org/releases/bookworm/amd64/release-notes/ch-whats-new.en.html#newdistro).
This selects public `py3` wheels and compatible CPython/stable-ABI manylinux
x86_64 wheels through glibc 2.36; it does not measure the image/interpreter or
test native ABI compatibility. Other architectures, musl, newer glibc
requirements and incompatible Python tags reject.

The unmodified upstream `uv.lock` remains SHA-256
`62d3a8c4807b89e85703b3c03f2c21048a2da9736ca83d9af6f61adc74ac5314`.
The derived catalog selects **74 wheels, 66,315,648 bytes**, following the
declared base dependency graph, with no extras or upgrades. For each package
it records the original lock's version, URL, SHA-256, size and active edges.
When several compatible wheels exist, the lexicographically first filename
is selected deterministically. Only the three exact marker expressions
present in this graph are evaluated, for the fixed target; unknown markers
or extras reject instead of being guessed. This is the **declared base wheel
closure**, not proof that importing every upstream module succeeds or that
optional voice/knowledge packages are unnecessary on every path.

## Root/build mismatch

Upstream `pyproject.toml` says `tau2` version `1.0.1`, whereas the same commit's
lock contains an editable `tau2` root version `1.0.0`. The validator recognizes
only that exact discrepancy and requires the base requirement declarations
to agree. Any other version, requirement, root origin or build declaration
change rejects. Upstream bytes are not rewritten.

This preparation uses **verified source-tree, not-installed** mode for tau.
It does not install the editable root, synthesize distribution metadata or
claim `uv sync --locked` succeeds. The declared build backend is
`hatchling.build` with unpinned `hatchling`; therefore building is explicitly
unsupported and `build_backend_locked` is false. A future decision to build
a root wheel needs a separate exact backend/build dependency closure.
This avoids introducing an unreviewed build step into input validation.

## Commands and output

Run from an unchanged committed checkout. Both modes require the exact local
public source archive and produce a new private receipt:

```sh
python3 -I -S -B scripts/tau_runtime.py \
  --source-archive "$TAU_SOURCE_ARCHIVE" --source-only \
  --output "$NEW_PRIVATE_SOURCE_RECEIPT"

python3 -I -S -B scripts/tau_runtime.py \
  --source-archive "$TAU_SOURCE_ARCHIVE" --wheelhouse "$LOCAL_SELECTED_WHEELS" \
  --output "$NEW_PRIVATE_WHEEL_RECEIPT"
```

No download, resolver, installer, package build, archive extraction, engine,
model or network client is present. Wheel mode requires every selected
artifact; missing/corrupt wheels reject, never fall back to source-only.
The wheel checker validates pinned compressed bytes, bounded ZIP members,
CRC, canonical non-traversing unique paths, regular files/directories, and
distribution name/version/WHEEL tags. It does not validate an installed
environment's RECORD, execute startup hooks or establish native ABI integrity.
Extra local wheelhouse files are ignored and are not authorized for installation.

Some pinned Maturin-built wheels use compressed triples in `WHEEL` `Tag`
headers as well as filenames. The checker applies the same
[PyPA Cartesian expansion](https://packaging.python.org/en/latest/specifications/platform-compatibility-tags/#compressed-tag-sets)
to each side and requires exact set equality, not merely one compatible tag.
It bounds each triple to 512 characters, metadata to 128 tag headers and total
expansion to 256 triples. Malformed, missing, additional or over-budget tags
reject; selected hashes and the CPython/Linux/glibc compatibility rules remain
unchanged. This handles the observed pinned metadata representation without
executing the wheels.

All reads are bounded, no-follow, regular/single-link and identity-checked.
There is a shared 180-second check deadline; source archive size is at most
100 MiB, selected source retention at most 32 MiB, each wheel at most 64 MiB,
total compressed wheels at most 512 MiB, and total checked wheel expansion
at most 1 GiB. The pinned source archive also has finite member/expansion
limits. These are local input-check bounds, not live request deadlines.

The committed helper/script/catalog and reused image pin are Git-source-bound
using the existing offline Git controls. The new `summary.json` is 0600 in an
exclusively created 0700 directory; existing output is never overwritten.
Publication failure may leave an incomplete attempt, which must be retained.
Only fixed status/count/hash/public-pin fields are emitted, never input paths,
exceptions, archive content, tasks or tool messages.

Exit 0 means the explicitly requested **input check** completed. Source-only
reports zero verified wheels. Both modes always retain `runtime_verified`,
`dependencies_installed`, `upstream_executed`, `benchmark_executed`,
`quality_accepted` and `confidential` as false.

## Next integration boundary

The standard `run_single_task` orchestrator/evaluator should remain authoritative.
This PR deliberately adds no second orchestrator, grader or budget ledger.
Before upstream execution, a reviewed worker must verify installed runtime
integrity and text import closure, suppress content sinks before package import,
disable dotenv/caches/callbacks, and bind all agent/simulator/judge calls to
the existing budget and outcome contracts. Missing tokenizer/framing bounds
for any role still block a claimed input-token guarantee.

The wrapper must explicitly clear the LLM log context for each trial, use
no-save/no-verbose/no-auto-review settings, discard ordinary stdout/stderr,
and retain only a bounded structured outcome channel. These controls are
**not implemented or proven by a wheel/source hash**. The existing source audit
documents why merely selecting `ERROR` log level or `save_dir=None` is insufficient.

Tests use synthetic tar/ZIP files with code that must never execute, metadata
and marker drift, target tag mismatches, missing/transitive wheels, traversal,
links/FIFOs, duplicates, bounds/deadlines, planted content, private receipts
and no-overwrite. No upstream evaluator or API is exercised:

```sh
python3 -B -m unittest discover -s scripts -p test_tau_runtime.py
```

The separate [scripted single-trial integration](p2-t3-tau-single-trial.md)
implements the next review-gated boundary. Its synthetic transport is not a
live endpoint adapter or a claim that these source-only checks proved runtime
compatibility.
