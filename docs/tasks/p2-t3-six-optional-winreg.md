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

At code revision `2e93e625fc8fc5c0f30af7b1a731bfcf8ede565a`, `make check`
passed 810 Python tests, both Go race-test variants, and the static/documentation
checks. `make test-e2e` passed all 18 provider checks and 14 negative attestation
mutations against fakes; cleanup was confirmed.

The exact-image CPU-only rerun is preserved unchanged in
[run.json](../../evidence/p2/2026-10-20-cpu-optional-winreg-2e93e62/run.json)
and [output.log](../../evidence/p2/2026-10-20-cpu-optional-winreg-2e93e62/output.log).
All five application stages passed, but startup still **failed** at checkpoint
11. Unlike the preceding optional-module failure, `paths` is now the sole state
mismatch. The captured source fingerprint identifies `cv2/__init__.py` at lines
181 and 153: OpenCV temporarily inserts its native-extension directory into
`sys.path`, then imports `cv2` again. The guard correctly rejects that mutation.
This separate compatibility issue is not fixed here. Cleanup was confirmed.

Independent review and hosted CI remain outstanding. No GPU inference, model
score, TDX validation or production deployment is claimed by this change.
