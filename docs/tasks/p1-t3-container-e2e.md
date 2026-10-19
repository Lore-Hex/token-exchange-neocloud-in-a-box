# P1.T3: Software-only container integration

## Status and dependencies

Full local software-only integration passed with the merged trcs pin. Final
independent review and PR CI remain separate gates; this is not yet P1 CI
acceptance. This branch preserves PR7 guest history and merges PR9 verifier
and PR8 catalog history; it does not copy unmerged implementation files. No
hardware, production service, API keys, deployment, paid resources, image
publication or confidentiality claim.

Historical pre-hardening [metadata](../../evidence/p1/2026-10-19-full-fake-node/run.json),
[inputs](../../evidence/p1/2026-10-19-full-fake-node/inputs.json),
[results](../../evidence/p1/2026-10-19-full-fake-node/result.json) and
[scrubbed operational output](../../evidence/p1/2026-10-19-full-fake-node/output.log)
record source `25b8268dda016ec3451ed65c01242b93db84ca3e`. All 18 provider
checks passed, all 14 mutations were rejected, the fake verifier passed and
release verifier failed. Admission, privacy, weight gate and both resource
absence checks passed. The output is bounded status/build output, not request
or response content.

That first recorder relied on a clean working-tree preflight and inherited
child environments. Review demonstrated a `MAKEFLAGS` shell override could
forge a pass, and a FIFO result file could block reading. Preserve the run as
history, not the final source-bound proof. The replacement recorder is tested
against both reproductions; a fresh committed-snapshot run is required below.

The bounded prerequisite command is:

```sh
python3 scripts/e2e/run.py --engine-only
```

It builds the guest fake engine and PR8's actual catalog generator, then runs
the pinned provider checker through Tier 4 and a rejecting-catalog control.
The generated declaration uses a synthetic model pack derived from the schema
test fixture, not either production-sized model. It checks PONG, timed flushed
deltas, usage, optional fields, synthetic 503/Retry-After, privacy canaries and
weight-gate shutdown. It does not start trcs or verify attestation.

`make test-e2e` is the full integration entry point, now also selected by CI.
The final dependency is merged trcs main
`c02fd478484f93732ba880d2d8e93aa8c5d90dbe`, independently checked with GitHub
and `git fetch origin main`; its history includes upstream PRs 1-5. The source
archive and linux/amd64 binary hashes are in `scripts/e2e/pins.json`. No
unmerged candidate revision is used. Full execution covers real trcs TLS/CoCo
AA transport, fake CPU and GPU evidence, fake verifier pass, release verifier
rejection, fourteen evidence mutations, admission 429 and health failure.
The verifier's exact-byte CoCo AA case and file-read hardening are integrated
by merging parent head `a4ed474`.

Record a clean committed source run with:

```sh
python3 scripts/e2e/prove.py --output evidence/p1/YYYY-MM-DD-full-fake-node
```

This wrapper transfers Git objects with replacement objects disabled, archives
the exact commit in a private repository, and verifies every extracted file
against its committed blob. It executes `make test-e2e` and the scrubber there,
not in the working tree. An allowlisted environment excludes inherited Make,
Git, Go, Python and Docker overrides; Go uses isolated caches, readonly modules,
`GOENV=off`, `GOWORK=off`, exact `GOTOOLCHAIN=go1.27.2`, and `go mod download`
plus `go mod verify`. Toolchain/module downloads use the public checksum
database with an isolated cache and no inherited credentials. Only a
local Unix-socket Docker endpoint is accepted. The original commit ID is
retained in that private repository, so the harness records the correct SHA.

The wrapper checks snapshot integrity before/after commands and requires the
reported build-context digest to match the expected committed snapshot digest.
`snapshot.json` records that binding. It preserves bounded input, result and
cleanup records and scrubs operational output through the committed
`scripts/scrub-secrets.sh` and freshly verified binary. Record reads use
nonblocking/no-follow opens, regular-file checks and a bounded read; FIFOs,
symlinks and oversized records are rejected. The bootstrap recorder/helper
must themselves match the commit. Uncommitted application files are not used.

The first hardened attempt at source `4fb6c9b` is preserved in
`evidence/p1/2026-10-19-committed-snapshot-node`. It failed before Docker:
`GOTOOLCHAIN=local` selected the host's Go 1.24.2, below the committed module's
Go 1.27.2 requirement. No container or image was created. Exact toolchain
selection replaces that host-dependent assumption; the failed artifact is
not overwritten or counted as a passing proof.

## Isolation and pins

- `pins.json` contains registry-resolved immutable Go 1.27.2 and Python 3.12.13
  image digests for linux/amd64. The source archive and actual compiled trcs
  binary are both hash-checked. No upstream internal implementation copy.
- Provider-check is the actual PyPI 0.1.8 wheel, installed with pinned transitive
  versions and mandatory hashes in `scripts/e2e/requirements.lock`. Go module
  checksums and `-mod=readonly` apply to the operator tools. Guest helpers and
  the fake agent's transitive imports use the Go standard library only.
- Downloads happen at build time. Runtime is nonroot with `--network none`,
  read-only rootfs, dropped capabilities, no-new-privileges, limits and a
  noexec/nosuid/nodev tmpfs. No host mounts, ports, devices, Docker socket or
  inherited credential environment are passed to runtime children.
- Docker's network-none driver supplies only loopback. The runner checks that
  interface inventory, UID and Linux process capability flags. The checker
  naturally uses its packaged catalog-schema fallback without internet access.
- In the full path, a public certificate is bootstrapped only from the isolated
  fixture's loopback TLS socket for checker trust. This is not authentication
  of a real endpoint. `nib verify` separately fetches and appraises its own
  same-connection transcript.
- Cleanup independently attempts removal of the unique container and local
  image, then verifies their absence with successful Docker listings. Removal
  errors/timeouts do not skip the other resource. Already-absent resources are
  clean success; remaining resources or failed verification force a failed run
  and are identified in the saved `cleanup` report. Success is printed only
  after this verification. Docker build cache is not purged. Raw process
  logs stay private in anonymous runtime files and are checked for a synthetic
  canary. Only bounded status/ID summaries and build-input hashes are saved
  under ignored `build/e2e/<run-id>/`; no inference bodies or private keys.

## Explicit fake evidence

`tests/e2e/fake-agent` has a denying default build. `nibfake` alone includes the
loopback AA and GPU helper. Release tests build both variants and assert the
collector symbols are absent in release and present in the positive control.
Tagged contract tests send actual loopback HTTP requests to the AA and pass
its bytes plus helper output through the merged verifier's public policy/check
API. The valid fixture and recovery pass as fake only; all fourteen mutations
fail. This is collector/appraiser contract coverage, not a replacement shim or
evidence that the real-trcs container run passed.

The AA responds to the real CoCo request (`GET /aa/evidence` with raw-URL-base64
64-byte `runtime_data` and `encoding=base64`) with shared
`protocol.FakeTDXQuote` bytes beginning `NIBFAKE-TDX-v1`. Measurements are the
schema fixture's four repeated-cc values. They are not an Intel quote. trcs
retains source/provider `coco-aa/coco-aa`; only the tagged appraiser may accept
that transport pair, with exactly the same fake-byte/measurement checks.
Release must continue rejecting it.

The separate helper consumes `TRCS_GPU_NONCE` and outputs two `gpu` records and
one `nvswitch` record with distinct device IDs and `nibfake-device-v1` evidence.
The verifier gets device counts from a pinned synthetic profile. Mutations
alter CPU SPKI and nonce binding, MRTD, each RTMR0-2, debug/migratable flags,
length and GPU count, duplicate ID,
bound nonce, identity or kind. A private local fixture-state file controls
these tests; nonblocking/no-follow open rejects FIFOs and symlinks before any
read. These are test-only collector controls, not production trcs fake flags.

## Sources and validation

- [Provider-check 0.1.8](https://pypi.org/project/trustedrouter-provider-check/0.1.8/),
  source revision `5bd56589d002705b4aa7e6fa8b37674fe10f907e`: CLI `--tier 4`,
  chat optional fields, stream framing and incremental-delivery assertions.
- [CoCo transport and helper command source](https://github.com/Lore-Hex/trustedrouter-confidential-stack/tree/b1e976da57cca6ca78fbb5f7d2559dd8d50f8b85):
  `cmd/trcs/main.go` and `internal/attest` show `--gpu-evidence-cmd` and the AA
  raw-byte transport. Revalidate against the parent's final merged pin; the
  proposed `--gpu-evidence-helper`/`--gpu-evidence-source` flags are not in this
  inspected revision and have not been guessed into the harness.
- [Admission source](https://github.com/Lore-Hex/trustedrouter-confidential-stack/tree/b4f07bb38af19991c20a16502fddd3b51f04d68f):
  `--max-inflight`, 429 and `Retry-After` behavior. These upstream PR revisions
  are inspection references, not substitutes for the merged-main runtime pin.
- [Final merged trcs source](https://github.com/Lore-Hex/trustedrouter-confidential-stack/tree/c02fd478484f93732ba880d2d8e93aa8c5d90dbe)
  contains all five upstream P1.T2 PRs. Its codeload tarball SHA-256 is
  `df3fbc8886f6751ff62f6e028e60799a3c1ea9e59b7e8d63b7319b482fb03a80`.
  Two clean linux/amd64 builds using the pinned Go image, `CGO_ENABLED=0`,
  `GOTOOLCHAIN=local`, `GOFLAGS=-mod=readonly`, build network disabled and
  `go build -a -trimpath -buildvcs=false -ldflags='-s -w' ./cmd/trcs` produced
  SHA-256 `e6fe6b412cfb974b35eb6e442141d3ff8ffd4adf0a702917979a1d6ba4267e4f`.
  The committed full Dockerfile independently rebuilt and checked that same
  binary hash. The pin-build outputs were local files, not published images.
- [Docker network-none](https://docs.docker.com/engine/network/drivers/none/)
  and [Docker run reference](https://docs.docker.com/reference/cli/docker/container/run/),
  inspected with local Docker 29.4.0.

Checks run successfully:

```sh
go test -race -tags nibfake ./tests/e2e/... ./guest/...
go test -race -count=1 ./...
go test -race -tags nibfake -count=1 ./...
python3 -m unittest discover -s scripts -p 'test_*.py'
make check
go vet -tags nibfake ./...
./build/staticcheck -tags nibfake ./...
python3 scripts/e2e/run.py --engine-only
```

### Earlier bounded runs

The first container attempt failed on an HTTP connection lifecycle mistake in
the Python harness, and the first catalog attempt rejected an incorrect
synthetic pack path. Both were corrected, not bypassed. Run
`build/e2e/e1ad84ac626c/result.json` passed all 18 provider checks through Tier 4,
the invalid-catalog control (exit 1), synthetic 503 response at the engine
boundary, privacy and weight failure. Its `inputs.json` records immutable build
inputs and the working-source digest. This does not prove the unrun real-trcs
path. `make test-e2e` was also checked to fail before runtime when the pin is
missing. These are software-only test results, not hardware evidence.

The full-run admission assertion now withholds a rejected request's entire
body and checks socket EOF, not only `Connection: close`. This covers the
upstream slow-429 drain regression. Separate assertions require distinct,
proxy-generated request IDs even when the client supplies its own ID, and an
empty health body. These full-run assertions are unit-tested where possible
but await the real merged trcs binary. Parent catalog head `cd06de2` is merged
with both catalog and verifier dispatch/help retained. The harness now builds
the release `nib` CLI and generates its declaration via `nib catalog`; the
standalone `nib-catalog` executable is no longer used by this container test.
The follow-up container run at `build/e2e/7c14a8c5833a/result.json` passed all 18
checks through Tier 4 using that main CLI, plus the negative catalog, privacy,
503 and weight-gate checks. Full `make check`, tagged staticcheck, all fourteen
collector/appraiser mutations and twelve Python harness tests passed after the
integration changes. No unmerged trcs revision has been assigned as a final pin.

The mutation matrix is defined at module scope before `full_node` is called.
A thirteenth harness test executes that orchestration with mocked external
boundaries and checks all fourteen mutations plus the final valid-state recovery;
this catches a missing definition or skipped loop even in engine-only CI.
The repeat container run `build/e2e/b968fe228874/result.json` and full
`make check` both passed. The upstream upload-read deadline/oversize fix changes
neither the fixture requests nor the pending final-pin requirement.

The cleanup review fix adds fault-injection coverage for removal exit 1,
removal timeouts, already-absent resources, failed absence queries, and
resources still present after a successful removal command. Both removal
attempts and both absence checks run independently; unresolved cleanup makes
the saved result and process exit status fail. All sixteen Python harness
tests and full `make check` passed. Engine-only run
`build/e2e/ab0eece1c59b/result.json` passed all eighteen provider checks, the
negative catalog, privacy and weight-gate checks, and confirmed absence of
both its container and image. Pins are unchanged; this is not full-node P1
acceptance or hardware evidence.
