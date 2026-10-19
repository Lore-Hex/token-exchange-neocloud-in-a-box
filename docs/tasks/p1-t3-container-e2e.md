# P1.T3: Software-only container integration

## Status and dependencies

Work in progress, not full P1.T3 acceptance. This branch preserves PR7 guest
history and merges PR9 verifier and PR8 catalog history; it does not copy
unmerged implementation files. No hardware, production service, API keys,
deployment, resource creation, image publication or confidentiality claim.

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

`make test-e2e` is the full integration entry point. It deliberately refuses to
build while `scripts/e2e/pins.json` has `trcs: null`. The parent owns the final
merged trcs commit/archive/binary provenance. The full path is implemented but
not yet exercised: real trcs TLS/CoCo AA transport, fake CPU and GPU evidence,
fake verifier pass, release verifier rejection, fourteen evidence mutations,
admission 429 and health failure. CI currently runs only the bounded command;
switch its step to `make test-e2e` when the final pin is integrated and the full
path passes locally. The verifier's exact-byte CoCo AA case and file-read
hardening are integrated by merging parent head `a4ed474`.

## Isolation and pins

- `pins.json` contains registry-resolved immutable Go 1.27.2 and Python 3.12.13
  image digests for linux/amd64. The source archive and actual compiled trcs
  binary will both be hash-checked. No upstream internal implementation copy.
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
- A unique container and local image are removed in `finally`. Raw process
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
