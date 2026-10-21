# P2.T3: Preserve optional TVM discovery semantics

The [ninth B300 trial](../../evidence/p2/2026-10-21-b300-optional-tvm/run.json)
verified all weights and application stages, then failed during startup after six
seconds. No inference or confidentiality success is claimed. Cleanup completed.

The original source-coordinate diagnostic maps to pinned Torch
[`trace_rules.py`](https://github.com/pytorch/pytorch/blob/cf30153c4c131c8164ee7798e5022d810682e2cb/torch/_dynamo/trace_rules.py)
lines 4019 and 3761, SHA-256
`380d4eee07f0b7e8c2b2e108d36de99f629ddd7c412ed1797bf9f584e07d577c`.
Its third-party skip-list initialization calls `find_spec("tvm")` before
TileLang has registered its bundled TVM. The new private namespace adapter
incorrectly treated that ordinary availability probe as a boundary violation.

Before registration, the two top-level private names now return no spec. This
does not delegate to another finder or search a broader root: this is the final
sealed finder, so an actual early import still raises `ModuleNotFoundError`.
After registration, the existing exact-source and origin verification applies.
Loading/failed states, supplied package paths, child-name probes and reloads
remain rejected. The ordinary P0 bootstrap is unchanged.

Synthetic tests exercise optional probes and early imports followed by successful
registration in the parent, fresh process, spawned child and grandchild. A
same-name untrusted file in the broader selected root must never execute.
The complete local suite and all five final-head hosted checks gate merge.
The next hardware run must preserve its own original result, including failure.
