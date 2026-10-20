# Pinned Botocore Six Compatibility

This P2-only adapter handles the exact Botocore vendored-six virtual-module
registry inside the existing sealed lab finder. It does not add or admit a
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

The primary-source boundaries are `_SixMetaPathImporter` (lines 167-240),
the moved-module registry construction (lines 251-507), and the final
`sys.meta_path.append(_importer)` registration (line 998).
The registry has exactly 54 names for the reviewed Linux/Python 3 path.
It includes true virtual packages, shared aliases, and lazy moved modules.
They cannot be resolved by treating them as ordinary filesystem packages.

[`engine_lab_bootstrap.py`](../../scripts/engine_lab_bootstrap.py) verifies
the selected-root source path, distribution version, exact source length/hash
and final AST registration shape before executing source-only code. It
replaces only that final registration call with a private callback. The rest
of the pinned source, including lazy aliases and module creation, is retained.
The callback captures the exact finite registry, importer/class identities,
method identities and moved-module targets. Its temporary global is removed
even on failure. No cached bytecode supplies the adapted source.

The existing sealed finder resolves only `botocore.vendored.six` and its
54 registered virtual names through this adapter. It delegates the pinned
loader protocol with pre/post boundary checks, retains `sys.modules` alias
identity, and leaves canonical `six` and other vendored copies unchanged.
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
and cached/sourceless bytecode rejection.

Both the CPU recorder and GPU-lab preparation bind this regression file
alongside the bootstrap in their committed source-bundle digest. Existing
payload, stage, privacy, deadline and cleanup tests remain mandatory.

This change follows the parent-owned native diagnostic's fixed
`botocore-vendored-six` identity; preservation of that run's original artifacts
is a separate evidence task. It does not assert that this adapter fixes every
startup failure. Actual post-change behavior requires a new explicitly
authorized parent-owned CPU run after final-head independent review and green
CI. Serving, weights, GPU readiness, quality and confidential acceptance remain
separate gates.
