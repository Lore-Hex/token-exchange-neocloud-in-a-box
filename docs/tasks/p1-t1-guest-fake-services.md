<!-- SPDX-License-Identifier: Apache-2.0 -->

# P1.T1 guest fake services

Scope: `guest/` and this task record only. Stack base: P0.T2/PR 2 head
`3a6c902`. Existing partial guest implementation was retained and completed.
No operator CLI, protocol, verifier, trcs, image, deployment or resource changes.

## What changed

- Four stdlib-only guest helpers: GPU gate, weights, network and engine.
  Release helpers deny startup unconditionally. Fake backend code requires
  `nibfake`; no environment switch or verifier injection is available.
- Exact simulated GPU/switch inventory, bounded per-file hashes, deterministic
  candidate nftables policy and a loopback synthetic HTTP/SSE engine with
  admission bounds, fixed diagnostics and ongoing simulated gate checks.
- Systemd dependency/hardening inputs, source references pinned to systemd
  v255 and nftables 1.0.9, and a runnable local fixture. See the
  [guest contract](../../guest/README.md).
- Positive and negative gate/config/privacy tests, in-flight corruption,
  shutdown/listener lifecycle, exact policy surface and service graph checks.
  The normal CI suite invokes tagged guest tests and builds/inspects all four
  Linux helper binaries in both tag modes, requiring fake symbols as a positive
  control and rejecting them in release output.

## How it was verified

Local machine: macOS/arm64, Go 1.27.2. Software-only tests, not hardware
validation. No `evidence/` hardware/benchmark result is claimed or created.

| Command | Result |
|---|---|
| `go test -race -tags nibfake -count=1 ./guest/...` | Passed, including real loopback fixture lifecycle |
| `go test -race -count=1 ./guest/...` | Passed; tagged child suite and Linux/amd64 fake/release symbol controls included |
| `go test -race -tags nibfake -count=1 ./...` | Passed across the full repository |
| `go vet -tags nibfake ./guest/...` | Passed |
| `./build/staticcheck -tags nibfake ./guest/...` | Passed |
| `make check` | Passed: formatting, staticcheck, shellcheck, YAML/schema/doc checks, vet, race tests, evidence scrub, secret scans and planted-secret controls |
| `make test-e2e` | Expected failure: existing P1.T3 harness placeholder exits 1 (`make` exits 2); not claimed as passing |

Commands use `GOCACHE=/private/tmp/nib-p1-t1-go-cache` for writable local build
cache; `make check` also uses `UV_CACHE_DIR=/private/tmp/nib-p1-t1-uv-cache`.
Initial attempts hit sandbox denials for the default Go/staticcheck caches and
loopback `bind`; checks were rerun with writable cache/local socket access.
No test was skipped or relaxed.

A regression test demonstrated that Go accepts Unicode case aliases for ASCII
struct fields even after lowercase-only validation. The fixture parser now
requires ASCII field names; the alias and duplicate-alias tests reject that
input. This hardening remains compiled only under `nibfake`.

## Not done / follow-ups

- Parent reports PR 2 CI green and merge in progress. This branch retains the
  tested stack base until integration; its own CI and independent final-head
  review remain merge prerequisites.
- No systemd boot/poweroff run, kernel nftables check/application or Linux
  namespace firewall test. Units/policy are source-backed inputs, not deployed
  controls. See the explicit integration limits in the guest contract.
- No vendor GPU/CPU cryptography, real readiness, verified mount, dm-verity
  corruption proof, measured image or inference hardware test. Ordinary
  binaries remain fail-closed until real backends are implemented and reviewed.
- P1.T2 trcs work belongs to the parent. P1.T4 owns `cmd/nib`,
  `internal/protocol` and `internal/verify`; none are modified here. Guest
  fixture `kind: switch` is private, not the upstream evidence wire type.
  Later P1.T3 integration must use the shared `device_type: gpu|nvswitch` and
  `device_id` protocol rather than treating these booleans as quote evidence.
- `make test-e2e` still belongs to P1.T3 and is expected to report the existing
  unimplemented harness. This PR does not claim the P1 exit criterion.
- No spending, external contact, production changes, resource provisioning,
  releases/images/packages, settings changes or deployment.
