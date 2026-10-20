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
pinned vLLM image, not a claim of reproducible wheel provenance. The runtime,
native dependencies and writable experimental image layer remain trusted in
this lab. Only the host payload mount is read-only in the CPU recorder. This
does not protect against malicious native code or concurrent modification of
the native-library path after its hash check. Hashing and direct loading here
do not establish confidential admission or native ABI qualification.

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

## Exact-Image Result

Code revision `9115b95792e2d82571391dfa017872eca527746a` passed all 821 Python
tests, both Go race-test builds, static/schema/documentation checks and secret
scans. The unchanged process canary failed inside the host's restricted socket
sandbox; the same canary and full suite passed with local process/socket access.
The fake-node E2E passed all 18 provider checks and rejected all 14 attestation
mutations; cleanup was confirmed. These are software tests, not real inference.

The first [CPU attempt](../../evidence/p2/2026-10-20-cpu-opencv-loader-9115b95/run.json)
failed at Docker image preflight with no container created. The retry with
local Docker socket access completed all five image application stages:
[run](../../evidence/p2/2026-10-20-cpu-opencv-runtime-9115b95/run.json),
[output](../../evidence/p2/2026-10-20-cpu-opencv-runtime-9115b95/output.log).
Original artifacts are unchanged. Offline validation against the exact producer
reports `record_valid=true`, `entry_pass=false`; cleanup was confirmed.

The real image now reaches checkpoint 13, past both the serving-subcommand and
argument-parser imports. Every bootstrap-state mismatch is false and the
pattern-matcher adapter reports `applied`. It stops while constructing CLI
defaults: the captured fingerprint matches upstream
[`vllm/config/device.py:55`](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/config/device.py#L55),
SHA-256 `ac7d153000e76d6d6b554fa26eb947c51c62c4295f40fabe13294bc35ba3c066`.
That branch raises when `current_platform.device_type` is empty. The caller is
[`arg_utils.py:344`](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/engine/arg_utils.py#L344),
which instantiates configuration defaults while building the argument parser.

This is consistent with the diagnostic deliberately exposing no GPU. Do not
spoof a CUDA platform or relax the checkpoint-16 success criterion. Import
progress is observed; full argument validation, engine startup, workers,
inference and GPU ABI still require the separately gated hardware run.
