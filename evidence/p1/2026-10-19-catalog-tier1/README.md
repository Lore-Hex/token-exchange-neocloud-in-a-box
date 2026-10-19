# P1.T5 local catalog proof

The [run record](run.json) identifies committed implementation revision
`105532b359877f9ee44ce6ca18faada06574efd3`. The script was run against merged
P0/main. This is fake local discovery and catalog validation only.

## Results

- [Provider report](provider-report.json): both Tier 1 checks pass, with no
  warnings or skips. The checker exited 0.
- [Negative control](negative-control-report.json): the invalid decimal fails
  declaration validation; discovery still passes. The checker exited 1 as
  required by the harness.
- [Request counters](request-counts.json): only allowlisted local GETs, no
  inference requests, authentication, or unexpected routes.
- [Provenance](provenance.json): pinned checker version/contract, both schema
  hashes, dependency lock hash and generated catalog hash.
- [Operational log](output.log): scrubbed by `scripts/scrub-secrets.sh`.

Reproduce in a new output directory:

```sh
uv run --project catalog/checker --frozen python catalog/checker/prove.py --output build/catalog-tier1-reproduction
```

Other final-tree checks run successfully:

```sh
make check
go test -race -count=1 ./internal/catalog ./cmd/nib-catalog
uv run --project catalog/checker --frozen python -m unittest discover -s catalog/checker
uv run --project tools --frozen yamllint --strict catalog/pricing.yaml
```

`make check` includes formatting, staticcheck, shellcheck, YAML/schema lint,
Markdown link checks, documentation tests, Go vet, all Go race tests, builds,
scrubber tests, working-tree/history secret scans and scanner positive controls.
The catalog proof builds both `cmd/nib` and `cmd/nib-catalog` itself. Local
cache environment overrides placed Go/uv caches under `/private/tmp`; the
loopback proof needed sandbox permission to bind its ephemeral local listener.
The [earlier failed sandbox attempt](../2026-10-19-catalog-tier1-sandbox-failed/README.md)
is retained separately.

`git diff --check` reports trailing alignment spaces in the checker's raw
`output.log` table. They are retained as emitted, not cleaned out of evidence;
the repository's required checks above pass.

## Explicitly incomplete

`make test-e2e` was run and still fails at the existing P1 placeholder:
`P1 is not implemented: no GPU-free end-to-end node exists yet.` This proof
does not complete the real-trcs/fake-engine Tier 4 pipeline or verification.

`cmd/nib` was left to its concurrent owner; `nib catalog` still needs the small
dispatch to `internal/catalog.Run`. The standalone `cmd/nib-catalog` works.
No real model was served, no hardware or privacy claim was validated, no
commercial rates were selected, and no catalog was published. Production
declarations remain unsupported and gated.
