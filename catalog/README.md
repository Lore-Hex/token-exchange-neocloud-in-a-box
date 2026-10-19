# Local Catalog v2 generator

P1.T5 implements **synthetic local tests only**, not a commercial price list,
live route, validated model deployment, or confidential-service declaration.
[pricing.yaml](pricing.yaml) contains arbitrary decimal parser fixtures, not
invented market rates. Both referenced model packs remain unvalidated drafts.

## Generate and check

From the repository root:

```sh
go build -trimpath -o build/nib-catalog ./cmd/nib-catalog
./build/nib-catalog --root . --pricing catalog/pricing.yaml > build/catalog.v2.json
go test -race -count=1 ./internal/catalog ./cmd/nib-catalog
uv run --project catalog/checker --frozen python -m unittest discover -s catalog/checker
uv run --project catalog/checker --frozen python catalog/checker/prove.py
```

The proof directory defaults to `build/catalog-tier1` and must not exist. Use
`--output evidence/p1/YYYY-MM-DD-catalog-tier1` for a new committed evidence run.
Installing the hash-locked test dependencies may download open-source packages.
The checker itself is restricted to literal loopback addresses, removes API-key
and proxy environment variables, and does not send inference requests.

The CLI writes JSON to stdout only after generation and validation succeed.
Use a new destination: shell redirection can truncate an existing file even when
generation fails. Exit codes are 0 success/help, 1 invalid configuration or I/O,
and 2 invalid command-line usage. Diagnostics do not echo configuration values.

`cmd/nib` is intentionally untouched to avoid the concurrent CLI owner's work.
The integration API is `catalog.Run(argsAfterCatalog, stdout, stderr) int` in
[internal/catalog](../internal/catalog/cli.go). `nib catalog` wiring and the full
P1.T3 `make test-e2e` pipeline remain with the parent/integration owner. This
task adds a separate read-only CI job for its local Tier 1 proof, not a claim
that all of P1 is complete.

## Contract and safety

- Input requires `schema: nib.catalog-pricing/v1` and
  `purpose: synthetic-local-test`. Unknown fields are errors at every level.
- Every model selects an existing `pack.yaml` and engine profile. Pack schemas
  and sorted file manifests are validated; missing references, duplicate
  canonical/native IDs and output limits above the selected context fail.
  These checks do **not** verify weights, hardware, serving or attestation.
- All five price fields are required. Non-negative, quoted, plain decimal
  strings are preserved byte-for-byte, including trailing zeroes. Only cache
  prices may be null. No float parsing, rounding or per-token conversion occurs.
  Values are limited to 128 bytes; exponents, signs, whitespace and leading-zero
  integers are rejected. Currency is USD and unit is `per_1m_tokens`.
- Supported numeric configuration uses bounded, plain decimal integers. Aliases,
  anchors, custom tags, duplicate keys, multiple documents, excessive nesting,
  oversized files, path traversal and escaping symlinks fail closed.
- Models sort by canonical TR ID and object keys sort lexically. Output has no
  clock, machine path or environment-derived data; input order cannot change it.
- There is deliberately **no production mode or privacy-claim switch**. Attempts
  to set confidential/TEE/attestation/ZDR/receipt claims are rejected, even if a
  pack has a complete verity hash. Pack structure cannot prove those claims.

The public contract has no synthetic flag, so every generated model name starts
with `SYNTHETIC LOCAL TEST ONLY`, the provider is `nib-synthetic-local-test`, its
contacts are `.invalid`, and its only region is `local-test-only`. Required
`active` lifecycle and deadline fields are synthetic fixture values, not live
availability or measured performance. Required capabilities are all false;
neither native capabilities nor privacy properties are inferred from draft
engine arguments. Canonical IDs/context limits come from the selected packs;
the local `/v1/models` stub retains their native IDs separately.

The [unmodified public schema](../internal/catalog/catalog.v2.schema.json) is
embedded with a runtime SHA-256 check; the compiler has no network loader.
[Schema provenance](schema-provenance.json) records its source, date, hash and
the separately pinned checker contract. The checker is stricter than the public
schema for some optional fields, so only their common required fields are emitted.

## Local proof

[prove.py](checker/prove.py) builds both CLIs, regenerates and compares the golden
catalog, and serves that file plus a synthetic native-model list on an ephemeral
`127.0.0.1` port. It invokes the unmodified checker CLI with `--tier 1` and
`--catalog-url`. A Python socket audit guard blocks remote DNS/connect attempts;
the checker's public-schema fetch therefore uses its own vendored fallback.
The Go generator separately validates the public schema pin. No checks are
monkeypatched, skipped, or weakened.

The harness requires exactly two passing Tier 1 checks, not just an exit code:
discovery and declaration. A second run with an invalid decimal must fail the
declaration while discovery still passes. It rejects unexpected requests,
authentication, POSTs, and any non-fallback schema result. Servers shut down
after the run. The report, control report, request counters, catalog, hashes,
timestamps and `hardware_validated: false` run record are retained; operational
output passes through the repository secret scrubber. No requests, bodies,
headers, prompts or completions are logged by the server.

Nothing is uploaded, published or sent through TrustedRouter's gateway. Real
prices, validated deployment claims and publication require later work and
their human gates, including G-PUBLISH. Schema or checker success is not an
authorization to list these drafts.
