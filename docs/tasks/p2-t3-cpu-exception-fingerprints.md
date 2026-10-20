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
