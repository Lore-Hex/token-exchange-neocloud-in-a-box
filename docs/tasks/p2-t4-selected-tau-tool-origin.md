# P2.T4: content-free selected tool rejection origin

Status: implemented and tested without models. The preceding
[hardware diagnostic](../../evidence/p2/2026-10-25-selected-tau-tool-rejection-0245/README.md)
failed on one known assistant tool raising `ValueError`. Its originals do not
contain an exact rejection site. This change has not yet run on hardware.

The selected operational observer may now include `origin` with only
`source_sha256` and an integer `line`. Only three exact source files already
sealed by [the runtime lock](../../bench/tau-runtime-lock.json) are eligible:
the retail tools, environment and toolkit from tau revision
`fc0055dc` (the full revision is in the lock). It checks already-loaded modules,
regular-file identity, byte length, SHA-256 and line count once on first error.
It never imports a tool to collect diagnostics.

A bounded traceback walk accepts only the deepest frame, from an eligible file.
Unknown sources, unavailable files or more than 32 frames omit the origin;
they never substitute a known outer frame. No filename, function name, local
variable, tool identity, argument, exception message, prompt or response is
serialized. The wire validator rejects all additional fields and unknown
digests or lines. Old observations without an origin remain valid.

This is an ordinary trusted diagnostic, not an attestation or proof against
arbitrary modifications of Python modules in memory. It changes no task,
request, token, retry or error limit, and cannot turn a failed trial into a
quality result. The new regression test is included in the selected scorer's
source identity.

Validation includes byte-pin joins, missing/changed/symlink/FIFO files,
unknown and overlong tracebacks, hostile exception hooks, legacy records,
mixed sources and private-content canaries. The optional local audit checks
the pinned upstream archive and executes only its original `_get_user` and
`_get_order` lookup methods with synthetic absent entries:

```sh
python3 scripts/test_tau_tool_origin.py --upstream-source /path/to/pinned-tau.tar.gz
```

It checks exact source-line identification, with no imports of upstream tools,
model calls or changed scoring. Source:
[tau retail tools at the pinned revision](https://github.com/sierra-research/tau2-bench/blob/fc0055dc/src/tau2/domains/retail/tools.py).
