<!-- SPDX-License-Identifier: Apache-2.0 -->

# P1.T4 verifier

Implemented software-only. No hardware acceptance or production confidentiality
is claimed. See [the verification contract](../verification.md).

## What changed

- Same-TLS evidence fetch with fresh nonce, actual peer-SPKI binding, strict
  bounded parsing, no redirect/retry/proxy and no inference or credentials.
- Independently hash-pinned local release/profile policy, protocol golden
  vectors, MRTD/RTMR0-2 and debug/migratable checks, Intel signature verification
  with collateral, CRLs and TCB status explicitly enabled.
- Exact-origin collateral retrieval with byte, time and request budgets.
- Device inventory/nonce structural checks, with production always returning
  fail/partial until real NVIDIA appraisal exists. Fake positive results require
  a build tag and never claim hardware verification or confidentiality.
- Both race-test modes in `make check`, Linux/amd64 release symbol checks with
  positive fake controls, adversarial transport tests and a complete local fake
  TLS-to-verdict round trip.

## How it was verified

Local macOS/arm64, Go 1.27.2. Commands use writable temporary Go/uv caches.

- `make check`: passed formatting, lint, schemas, doc links, both race suites,
  Linux verifier builds/symbol controls, evidence scrub and secret scans.
- `go test ./internal/verify -run '^$' -fuzz FuzzAttestationJSON -fuzztime 10s -parallel 4`:
  passed. This is parser robustness testing, not attestation evidence.
- `GOTOOLCHAIN=go1.27.2 go run golang.org/x/vuln/cmd/govulncheck@v1.9.0 -show verbose ./...`:
  no reachable or imported-package vulnerabilities. A module-only advisory
  remains for the unused, unmaintained `golang.org/x/crypto/openpgp` package
  ([GO-2026-5932](https://pkg.go.dev/vuln/GO-2026-5932)); no OpenPGP code is
  imported. The verifier dependencies are pinned, including crypto v0.58.0,
  sys v0.49.0 and protobuf v1.36.12, instead of the TDX module's old minimums.

The first vulnerability-scanner invocation selected Go 1.26.9 and could not
parse this Go 1.27 repository. It was rerun with the exact repository toolchain;
the failed invocation is not reported as a clean scan.

## Not done

- No real TDX positive verification, NVIDIA cryptography, hardware inventory
  validation, firmware/mode appraisal, measurement prediction or boot proof.
  These remain P3/P5 acceptance work and cannot be inferred from fake tests.
- No signed release/transparency validation (P8), vendor engine code, production
  integration, image publication, paid requests or cloud resources.
- P1.T3 owns container/provider-check integration. Real upstream trcs must be
  pinned after its reviewed feature PRs merge; its code is not vendored here.
