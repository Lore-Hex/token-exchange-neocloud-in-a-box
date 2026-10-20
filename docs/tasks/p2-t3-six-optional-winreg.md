# P2.T3 Optional Windows Module Probe

## Root Cause

The exact CPU-only startup at `6e1e177` reached checkpoint 11 and failed inside
the sealed Six finder. See the unchanged
[diagnostic evidence](../../evidence/p2/2026-10-20-cpu-exception-sites-6e1e177/output.log).
Source fingerprints identify `dateutil/tz/win.py:13` followed by the registry
assertion. The source hash matches python-dateutil `2.9.0.post0`, revision
[`1ae807774053c071acc9e7d3d27778fba0a7773e`](https://github.com/dateutil/dateutil/blob/1ae807774053c071acc9e7d3d27778fba0a7773e/src/dateutil/tz/win.py#L13).
Its caller, [`dateutil/tz/tz.py:29`](https://github.com/dateutil/dateutil/blob/1ae807774053c071acc9e7d3d27778fba0a7773e/src/dateutil/tz/tz.py#L29),
probes Windows timezone support and catches `ImportError` to continue on Linux.

Both already-pinned Six sources register `winreg` only on Windows. Their exact
54-name Linux registry correctly excludes it. The adapter incorrectly treated
this expected absence as a corrupt registry, raised `ValueError`, and made the
failure sticky. No change to the registry or source pins is needed.

## Fix And Safety

After the unchanged state/identity checks, return `ModuleNotFoundError` for only
the absent `<pinned-six-namespace>.moves.winreg` name on non-Windows platforms.
Set the exception's `name` so Python's `from ... import` handling preserves its
ordinary `ImportError` behavior. Do not return `None` to the import machinery:
the unavailable module must never fall through to a filesystem finder.

All unknown names, reloads, modified registries, aliases, injected modules and
other state violations still reject with sticky failure. The same code handles
both pinned Six namespaces. This does not enable Windows support, import any
new source, change the P0 bootstrap or grant confidential admission.

## Verification

Before the fix, both added optional-import regressions failed with the same
`ValueError` at the registry assertion. Tests cover repeated direct/from imports
in parent, fresh child and spawned grandchild; successful allowed imports after
the optional miss; a planted filesystem shadow; and rejection after registry
mutation or a Windows-platform mismatch. Existing unknown-name, source-pin,
reload and tampering tests are retained unchanged.

The actual CPU/image rerun, full suite, independent review and hosted CI remain
separate checks. No GPU inference, model score, TDX validation or production
deployment is claimed by this compatibility change.
