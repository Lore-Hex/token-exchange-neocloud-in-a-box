# P2.T4: Sealed macOS External Checker

Status: software-only observer preparation. No endpoint request, credential
read, service deployment or hardware qualification is authorized or claimed.

## Separate Platform Contract

The Linux preparer intentionally admits only its original Linux/amd64 CPython
wheel profile. It rejected the otherwise hash-verified native Mac Brotli and
rpds wheels. No Mac payload existed merely because those downloads completed.

[external_checker_payload.py](../../scripts/external_checker_payload.py) adds
one explicit profile, `macos-arm64-cpython-3.12.13`, for the external observer.
It requires that exact CPython patch release and SOABI, native arm64, and macOS
11 or newer before preparing or importing. This is not a general wheel installer
or a claim about every macOS/Python distribution. No Rosetta/x86, ABI3, other
Python release, dependency installation, native build or platform fallback is
accepted. Unsupported platforms must stop, not relabel their payload.

The source basis is the PyPA
[compatibility-tag specification](https://packaging.python.org/en/latest/specifications/platform-compatibility-tags/)
(`Python Tag`, `ABI Tag`, macOS platform tags and compressed tag sets) and
[wheel specification](https://packaging.python.org/en/latest/specifications/binary-distribution-format/)
(`WHEEL` metadata and filename tags), checked 2026-10-25. The native artifacts
are the existing lock's Brotli 1.2.0 `cp312-cp312-macosx_10_13_universal2` and
rpds-py 2026.9.1 `cp312-cp312-macosx_11_0_arm64` wheels. Both contain an arm64
slice for the target; the effective minimum for this observer is macOS 11.
Every archive hash must already occur in the unchanged 17-package requirements
lock. Wheel metadata must match its filename, distribution and version exactly.

The existing archive-integrity and safe-unpacking helpers are shared. Linux
tag selection still runs before archive validation, with no new option on its
preparer or serving-image verifier. Its `nib.private-provider-payload/v1` format
is unchanged and cannot accept this observer's distinct
`nib.external-checker-payload/v1` manifest. The only tau runner change is its
exact source hash for the factored archive validator; tau target policy is
unchanged.

The observer manifest binds the platform, committed producer and exact source
closure, selected model identity, lock/checker hashes, all 17 wheel identities,
and every extracted byte. The same committed source is required at import.
The external input must separately pin the manifest SHA. Extras, symlinks,
mutable payload permissions and source/platform/hash drift fail before import.
The existing private publisher retains a 0700 wrapper around 0555 directories
and 0444 files. Failed/partial destinations are never overwritten or reused.
Imports use the existing isolated checker loader and restore its module/path
state. No endpoint or API key is involved in preparation.

## Offline Preparation

Use a fresh absolute destination and the already verified Mac wheel directory:

```sh
python3.12 -I -S -B scripts/external_checker_payload.py \
  --profile macos-arm64-cpython-3.12.13 \
  --wheelhouse "$LOCKED_MAC_WHEELS" --output "$NEW_PRIVATE_DESTINATION"
```

The command validates the complete published tree before printing its manifest
SHA. Use `$NEW_PRIVATE_DESTINATION/payload` for the external runner, never the
Linux serving-image builder. A separate offline isolated import check must
exercise actual native Brotli/rpds imports and the vendored catalog schema;
merely validating a synthetic inventory is not native-import evidence.
Public-service calls remain separately authorized, finite and non-retrying.

## Verification

[Portability tests](../../scripts/test_external_checker_payload.py) cover exact
runtime/tag admission, unchanged Linux rejection, archive metadata/hash and
import-hook negatives, source closure, complete sealed inventory, byte drift,
extra files, links, writable files/directories and expiry. Existing owned
conformance/protocol tests remain unchanged apart from sharing the import block.
The [required checker gate](../../scripts/check_checker_tests.py) now includes
the external checker-dependent class and fails on any skip or expected failure;
the dedicated regression prevents silently dropping that coverage.

No serving image, engine flag, model limit, transport retry, TLS trust policy,
checker status or privacy/quality claim is changed.
