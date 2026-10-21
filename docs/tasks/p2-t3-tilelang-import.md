# P2.T3: Private TileLang import namespaces

The [eighth B300 trial](../../evidence/p2/2026-10-21-b300-tilelang-import/run.json)
passed all 83 weight checks, five application stages, and the effective private
cache execution check. Engine startup failed after 257074 ms; cleanup succeeded
and Docker reported no OOM kill. There is still no successful inference result.
The original [diagnostic](../../evidence/p2/2026-10-21-b300-tilelang-import/output.log)
now reaches TileLang rather than the previous Triton native loader failure.

The worker exception is `ValueError` at the sealed `sys.path` equality check.
Its source frames match vLLM `5df63a7203cdb9328d824a27b682e77c7b9928be`
(`models/deepseek_v41/nvidia/model.py`, `model_executor/kernels/mhc/tilelang.py`,
`tilelang_kernels.py`, `tilelang_utils/__init__.py`) and TileLang 0.1.12
`__init__.py` SHA-256
`f503f405623d7a028a4fa3e7de6a9b8186ac44d259f340b33c528a8808150242`.

## Source Review

The [public TileLang 0.1.12 wheel](https://pypi.org/project/tilelang/0.1.12/#files)
for Linux x86_64 ABI3 has SHA-256
`bbeb5573cbe2544a51a5c9a6cc737c5e3b5c2d95411caa3687fd9b08eb9d5f97`.
We downloaded and inspected this artifact without installing or executing it.
Its `env.py` prepends two global search roots: `tilelang/lib` and
`tilelang/3rdparty/tvm/python`. The latter also changes `PYTHONPATH`.
The Cython adapter imports `tilelang_cython_wrapper` from the former;
TileLang imports its bundled `tvm` package from the latter.

The lab adapter checks exact distribution/version and source hashes, replaces
only these two mutation sites, and resolves only `tvm`, its children, and
`tilelang_cython_wrapper` from their fixed private locations. TVM uses source-only
loading; the ABI3 extension's bytes are checked before loading. No general
search root is added and `PYTHONPATH` stays absent. The existing sealed-path,
spawn-only, no-bytecode, and metadata restrictions remain in force. Development
layouts, alternate paths, reloads, preloaded modules, symlinks, drift, and partial
registration fail closed. Application files remain on the read-only mounted
verified application tree; this is not a replacement for measured production
storage or attestation.

## Verification

`python scripts/test_tilelang_loader.py` exercises synthetic imports in the
parent, fresh interpreter, spawned child, and grandchild. It also reproduces the
original global-path failure and rejects namespace/source/environment tampering.
`python scripts/test_tilelang_loader.py --public-source <pinned-wheel>` compiles
the exact source adapter and hashes the bundled TVM/native files, without running
upstream code. Run full `make check` before a hardware trial and require all
final-head hosted checks before merging. These software checks do not establish
GPU inference, quality, or confidentiality.
