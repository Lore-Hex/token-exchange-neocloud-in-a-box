# P2.T3 CuTe compiler import compatibility

The [original B300 trial](../../evidence/p2/2026-10-21-b300-cutlass-path/run.json)
at `584955e51b9726b9f1dc5594349f621ac921dbfc` verified the complete model pack
and all five application stages, then failed startup after 160055 ms. There
was no OOM; the engine stopped and container cleanup was confirmed. No
inference or correctness result was produced.

The [worker diagnostic](../../evidence/p2/2026-10-21-b300-cutlass-path/output.log)
ends at launch source SHA-256
`a407978e88b91ff3489a0f02fde765d7526a5b62d6154a2b635bc4e1696441bc`,
line 1017: the explicit `sys.path != layout["paths"]` rejection.
Earlier source hashes match FlashInfer's CuTe availability check, followed by
CuTe's package initializer, `base_dsl` initializer and `dsl.py:69`.

Static comparison against the published
[NVIDIA CuTe DSL core 4.8.0 wheel metadata](https://pypi.org/pypi/nvidia-cutlass-dsl-libs-core/4.8.0/json)
matched all three CuTe frame hashes. Wheel SHA-256:
`b32716d2ed10c185cdbbb6b5c6fca26a3113a60e6e2fe4e2f0cb15966422b621`.
The preceding compiler import appends its own directory to `sys.path` at
line 102. Compiler source SHA-256:
`26a940ac92440a63561411b0818c3f88da363b1247ab54fcb5572d242d382c3b`.
That mutation survives until `dsl.py` imports its next sibling. The compiler's
own sibling imports are relative; adding the directory globally also exposes
those files under unintended top-level names.

The lab-only source loader checks the exact distribution version, source
origin and hash, and replaces only that one top-level append with `pass`.
Relative imports and all other compiler code remain intact. It does not
temporarily relax the path guard, extend approved roots, execute cached
bytecode, modify installed files or change the production/P0 bootstrap.
Changed source, version, origin, preloaded compiler and reload fail closed.
No proprietary source is vendored in this Apache-2.0 repository.

Verification:

```sh
python -m unittest discover -s scripts -p test_cutlass_loader.py
python scripts/test_cutlass_loader.py --public-source /absolute/path/to/compiler.py
make check
```

Synthetic tests reproduce the original failure, verify the exact one-node
AST change, preserve exports in fresh/parent/spawn/grandchild processes, and
reject source/version/origin drift, preloading, cached-code substitution and
unrelated path changes. The public-source audit compiles but never executes
the proprietary source. A fresh bounded hardware trial is still required;
this change does not establish successful inference or confidential admission.
