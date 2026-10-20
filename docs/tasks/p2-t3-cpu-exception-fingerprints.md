# P2.T3 CPU Startup Exception Fingerprints

The original local CPU diagnostic at `94ec89e` completed all five image
application stages, then exited 1 at checkpoint 11. The two Six registries now
leave the expected import-finder list intact, with no observed bootstrap-state
mismatch. That narrows the failure but does not identify its cause:
[run](../../evidence/p2/2026-10-20-cpu-six-adapter-94ec89e/run.json),
[output](../../evidence/p2/2026-10-20-cpu-six-adapter-94ec89e/output.log).
Cleanup was confirmed. No model, GPU, network, credentials or inference ran.

Add diagnostic-only exception evidence to the existing explicit child pipe:

- One fixed exception-class category; unknown subclasses remain `other`.
- At most eight source-file SHA-256 digests and line numbers, never filenames,
  function names, source lines, exception messages, locals or environment data.
- At most 64 traceback frames scanned and 2 MiB read per source. Only files
  below the already-bound payload/interpreter/module roots may be read, through
  the existing bounded no-symlink reader. Unavailable sources stay explicit.
- The complete state packet remains below the 512-byte portable atomic-pipe
  floor. Invalid packets and extra data still fail closed.
- Original exceptions propagate. An observed exception prevents diagnostic
  success even if an inconsistent record claims exit zero.

These observations do not authorize imports, change the sealed finder, modify
the application or GPU launcher, enable stdout/stderr, or establish source/ABI
admission. A digest reports bytes observed during diagnosis, not independent
provenance. The candidate remains nondeployable and nonconfidential.

`scripts/test_engine_cpu_entry.py` exercises fixed classes, malicious exception
hooks, bounded recursion/source reads, unavailable source, packet corruption,
content canaries, child descriptor cleanup, and rejection of unearned success.
The old evidence and producer bytes remain unchanged in Git history.

## First Instrumented Run

The run at `ffddd59` confirms a `ValueError` with unchanged finder/state checks
and confirmed cleanup, but its diagnostic rejected the source-root count:
[run](../../evidence/p2/2026-10-20-cpu-exception-sites-ffddd59/run.json),
[output](../../evidence/p2/2026-10-20-cpu-exception-sites-ffddd59/output.log).
The pinned layout includes stdlib, dynamic extensions, the CUTLASS DSL root and
site packages, plus the diagnostic payload root. Permit exactly this bounded
five-root layout and test the actual prepared contract; a sixth root still
reports observer failure. This does not change any engine import roots.

## Corrected Observation

The exact `6e1e177` producer passed all 804 Python tests and both Go race-test
builds. Its bounded local CPU run completed with cleanup confirmed but still
failed at checkpoint 11:
[run](../../evidence/p2/2026-10-20-cpu-exception-sites-6e1e177/run.json),
[output](../../evidence/p2/2026-10-20-cpu-exception-sites-6e1e177/output.log).
The observer now succeeds. The frame list is explicitly truncated and contains
unavailable sources; it is not a complete traceback.

The last frames identify the sealed Six finder's registry assertion. Immediately
before it, `dateutil/tz/win.py` line 13 imports `six.moves.winreg`. Its source
SHA-256 is `c49b335a04b0135c4fc7f1c98f86647a9cae902fe1372d35e9631c5e16d1681f`.
`dateutil/tz/tz.py` line 29 calls that module and catches only `ImportError`;
its SHA-256 is `1149c474c7de4e15e263a978b21f7205a6d9eb7fcf3b3cb4d734ac87db619f5a`.
Both match the official python-dateutil `2.9.0.post0` wheel and
[source revision](https://github.com/dateutil/dateutil/tree/1ae807774053c071acc9e7d3d27778fba0a7773e/src/dateutil/tz).
The pinned Six source excludes `winreg` on Linux. A separate compatibility fix
must preserve this expected absence without permitting a filesystem fallback
or weakening the existing unknown-name and tampering failures. No such engine
change is part of this diagnostic PR.
