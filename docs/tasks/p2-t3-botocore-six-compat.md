# Pinned Six Compatibility

This P2-only adapter handles exactly two source-pinned virtual-module registries:
standard `six` and `botocore.vendored.six`, inside the existing sealed lab finder.
One source/alias loader implementation serves both, with independent per-module
state, registries, importer/class identities, aliases and sticky failures.
It does not add or admit a
finder, relax a process-state check, change the P0 bootstrap, or qualify the
candidate. The candidate remains **BLOCKED**, non-deployable and
`confidential=false`. No image, engine, GPU or model was run for this change;
regression execution uses original synthetic fixtures only.

## Source And Boundary

Botocore `1.43.114`, immutable commit
`fca45a8afc1294fb6c1fc3d2d4a9187e415f37e1`, includes
[`botocore/vendored/six.py`](https://github.com/boto/botocore/blob/fca45a8afc1294fb6c1fc3d2d4a9187e415f37e1/botocore/vendored/six.py).
The file is 34,549 bytes with SHA-256
`4ce39f422ee71467ccac8bed76beb05f8c321c7f0ceda9279ae2dfa3670106b3`.
Its embedded Six version is `1.16.0`. The source carries Benjamin Peterson's
MIT notice, which stays in the original installed source; this repo does not
copy the upstream implementation.

Standard `six` `1.17.0`, immutable commit
`ebd9b3af90247b8858d415a05e96e9ee61e48d07`, has
[`six.py`](https://github.com/benjaminp/six/blob/ebd9b3af90247b8858d415a05e96e9ee61e48d07/six.py)
at 34,703 bytes, SHA-256
`c51c91f703d3d4b3696c923cb5fec213e05e75d9215393befac7f2fa6a3904df`.
Its MIT notice also remains in the installed source; no implementation is
copied here. Both public raw files were independently hash/length verified
and compared as data, never imported/executed for author validation.

The Botocore primary-source boundaries are `_SixMetaPathImporter` (lines 167-240),
the moved-module registry construction (lines 251-507), and the final
`sys.meta_path.append(_importer)` registration (line 998).
The registry has exactly 54 names for the reviewed Linux/Python 3 path.
It includes true virtual packages, shared aliases, and lazy moved modules.
They cannot be resolved by treating them as ordinary filesystem packages.
The standard file has the same importer class AST and the same exact 54-name
Linux/Python 3 registry, independently extracted with AST inspection of
`_moved_attributes` and `_importer._add_module` calls. No source evaluation is
needed to determine those names. The Windows-only `winreg` entry is excluded
from this Linux contract, not silently admitted.

The two sources differ in copyright/version text, a Python-2 `UserDict`
attribute spelling and a Python-version conditional around urllib request
attributes. Those are not importer/module-registry differences and are left
intact. Both mark the module as a virtual package and have the same cleanup
loop for an existing importer with matching module name before their final
append (standard lines 984-1003, Botocore 979-998). Preloaded copies or finder
changes already reject; that loop is unchanged and cannot authorize a finder.

[`engine_lab_bootstrap.py`](../../scripts/engine_lab_bootstrap.py) verifies
the selected-root source path, distribution version, exact source length/hash
and final AST registration shape before executing source-only code. It
replaces only that final registration call with a private callback. The rest
of the pinned source, including lazy aliases and module creation, is retained.
The callback captures the exact finite registry, importer/class identities,
method identities and moved-module targets. Its temporary global is removed
even on failure. No cached bytecode supplies the adapted source.

The existing sealed finder resolves only the two named sources and each one's
54 registered virtual names through this adapter. It delegates the pinned
loader protocol with pre/post boundary checks, retains `sys.modules` alias
identity. Other vendored copies are not adapted or granted finder permission.
Unknown virtual names, reloads, shadows, preloaded source/aliases, registry
replacement or mutation, target changes, and source/create/exec failures
reject. Failure is sticky: catching an import error or supplying a cached
module cannot restore the next guarded boundary.

The importer itself is never appended to `sys.meta_path`. Its initial
three-finder list remains unchanged, and a genuine arbitrary finder addition
still rejects. This is source-bound compatibility for trusted pinned engine
code, not an adversarial in-process Python sandbox. It does not make all
possible Six features or mutable Python objects safe in an untrusted process.
In particular, dynamic registry extension is outside the contract.

## Verification And Remaining Work

[`test_botocore_six.py`](../../scripts/test_botocore_six.py) uses an original
synthetic loader fixture, not an execution of upstream source. Tests cover
fresh/spawn/grandchild setup before unpickling, shared virtual aliases,
lazy-from imports, unchanged meta-path identity, exact AST edit and source
rejection, registry/class/module/target mutation, reload and unknown names,
sticky source/target/create/exec failures, version/shadow/preload rejection,
and cached/sourceless bytecode rejection, separately for both source namespaces.
Combined tests cover both import orders, interleaved nested/flat aliases,
distinct virtual packages, shared resolved stdlib-style targets, simultaneous
registry state, cross-namespace replacement and reload after both loads.
Every successful case keeps the original meta-path list; either module's
sticky failure still blocks the shared boundary. The source pins are checked
against the unchanged fixed-ID finder diagnostic catalog.

Both the CPU recorder and GPU-lab preparation bind this regression file
alongside the bootstrap in their committed source-bundle digest. Existing
payload, stage, privacy, deadline and cleanup tests remain mandatory.

The Botocore adapter followed the parent-owned native diagnostic's fixed
`botocore-vendored-six` identity. The standard-six extension follows the later
parent-owned run `39485114646`, producer
`4001efbd2a28ddb00a98dff05eef1d47bdc3acab`. Its unchanged
[envelope](../../evidence/p2/2026-10-20-cpu-native-39485114646-1/run.json) and
[output](../../evidence/p2/2026-10-20-cpu-native-39485114646-1/output.log)
record 20:36:10-20:45:28 UTC, all five application stages, unchanged native
inventory, and checkpoint 11/EOF with exit 1. Only `finders` differs; the fixed
entries are `builtin`, `frozen`, `sealed`, **`six`**, not Botocore. All six
diagnostic source rows are verified, with no truncation/comparison failure;
the pattern adapter remains pending. Cleanup is confirmed by that recorder.
The artifact regression reconstitutes the original producer payload and
validator sources, verifies byte-identical artifact hashes and retains the
failed, software-only, nonqualifying result.

This does not assert that the adapter fixes every
startup failure. Actual post-change behavior requires a new explicitly
authorized parent-owned CPU run after final-head independent review and green
CI. Serving, weights, GPU readiness, quality and confidential acceptance remain
separate gates.
