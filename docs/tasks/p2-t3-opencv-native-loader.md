# P2.T3 OpenCV Native Loader

## Observed Failure

The exact CPU-only startup at `2e93e62` completed five application stages but
failed at checkpoint 11 with `paths=true` and all other state flags false.
[Original evidence](../../evidence/p2/2026-10-20-cpu-optional-winreg-2e93e62/output.log)
is retained unchanged, including failure and confirmed cleanup.

The fingerprint identifies `cv2/__init__.py:153`. Its 6,630 bytes match OpenCV
revision [`40738fb16ceddb5fb3fea747585f7ce6abb0605b`](https://github.com/opencv/opencv/blob/40738fb16ceddb5fb3fea747585f7ce6abb0605b/modules/python/package/cv2/__init__.py).
The pinned `opencv-python-headless` 5.0.0.93 wheel's Python loader temporarily
inserts the native-extension directory into `sys.path`, removes the Python
package from `sys.modules`, imports the native `cv2`, then restores the package
and path. This violates the lab's fixed import-root contract. The guard is
correct; relaxing it would allow unrelated imports from an unreviewed root.

## Narrow Adaptation

Only the experimental P2 lab changes; the P0 bootstrap is untouched. After
checking the exact Python source hash, replace three AST operations:

1. Supply an executor that uses already-hashed configuration bytes in their
   exact expected order. Alternate discovered configs reject.
2. Validate the configured extension and binary directories instead of
   inserting an extension directory into `sys.path`.
3. Load only `cv2/cv2.abi3.so` through an explicit `ExtensionFileLoader`, after
   its SHA-256 is checked with the existing bounded, anchored, no-symlink read.

Keep the upstream Python/native export merging and extra Python modules. Keep
the existing finder, search-root and spawn checks. Source, configuration,
version, native-library, loader-order, module-replacement and reload failures
are sticky. Remove private callback globals even when import fails.

The generated wheel config and native-library hashes come from the exact
pinned vLLM image, not a claim of reproducible wheel provenance. The read-only
image/runtime and native dependencies remain trusted in this lab. Hashing and
direct loading here do not establish confidential admission or native ABI
qualification.

## Verification Scope

`scripts/test_opencv_loader.py` uses original synthetic source and a fake native
loader: Python/native export preservation, fresh process, spawn and grandchild,
unadapted path-mutation regression, changed source/config/native bytes, unknown
config paths/order, wrong configured roots, missing/symlinked native library,
wrong distribution version, reload, module replacement and failed-load cleanup.
The exact CPU-image rerun is a separate check; a fake native loader is not proof
of real OpenCV initialization or successful DeepSeek inference.

Full tests, independent review and hosted CI are required before merge. The
candidate stays blocked/nondeployable/nonconfidential. No new hardware run,
model score, TDX result or production deployment is claimed.
