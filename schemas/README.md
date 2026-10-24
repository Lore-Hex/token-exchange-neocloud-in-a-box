<!-- SPDX-License-Identifier: Apache-2.0 -->

# NIB Configuration Validation

Integration contract (P0.T3): operator-only package `internal/config` exposes
`ValidateRepository(root string) error`, `ValidateModelPack([]byte) error`,
`ValidateDeployableModelPack([]byte) error`, `ValidateHardwareProfile([]byte) error`,
`ValidateRelease([]byte) error`, and `ValidateEvidence([]byte) error`.

The JSON Schema Draft 2020-12 documents in this directory are authoritative.
`common.schema.json` supplies shared definitions. The deployable-pack schema
extends the ordinary pack schema without duplicating its rules. `$id` URLs
are offline identifiers; validation never fetches them. Format assertions are
enabled explicitly. Unknown fields are rejected at every structured boundary.

Operator dependencies (the integrator owns module files):

- `github.com/santhosh-tekuri/jsonschema/v6 v6.0.2` (Apache-2.0)
- `go.yaml.in/yaml/v3 v3.0.4` (Apache-2.0 and MIT; not vendored)
- `golang.org/x/text v0.14.0` (transitive, BSD-3-Clause; not vendored)

All code, schemas and fixtures authored here are Apache-2.0. These dependencies
must not enter guest imports. Go 1.27.2 is the integration toolchain target.

## Drafts And Trust

A model pack may have `weights_volume.verity_root_hash: ""` before P5 and
`engine_profiles: {}` before engine selection. These are incomplete drafts,
not deployable packs. Every supplied revision, digest or engine image must
still be an immutable pin. The deployable validator requires a weight-volume
hash and at least one engine profile. It does not claim the image was built.
Known all-zero placeholder revisions/digests are rejected. For DeepSeek packs,
TDX-named deployable profiles must set `VLLM_DISABLE_UVA: "1"`. Repository
release validation checks all selected confidential profiles, including custom
names, while permitting unrelated plain P2 offload comparisons. This check does not
interpret engine arguments or approve launch security; conflicting Engram
offload options, unreviewed engines and flags still require upstream review.
Structural deployability is **not launch approval or security validation**.

Input policy: one UTF-8 JSON document, or JSON-compatible YAML for packs and
hardware profiles. Duplicate keys, aliases/anchors, merge/custom tags, non-string
keys and non-JSON numeric spellings (hex, octal, leading plus, underscores,
NaN/infinity) are rejected. JSON numeric lexemes are preserved exactly, including
decimal/exponent spellings; no validation decision uses rounded float64 values.
Before schema arithmetic, numeric representations are limited to 128 characters
and decimal exponents to -1024 through 1024 in both formats. These resource
bounds are not rounding rules and do not apply to quoted strings.

Release manifests only accept `variant: release`, complete hashes and TDX
measurements MRTD/RTMR0-2. Repository validation checks their pack/profile
references and rejects incomplete packs, non-confidential hardware profiles,
and mismatched engine or weight-volume pins. A schema-valid release is **not**
signed, published, trusted or verified on hardware. Signature checks, measured
boot prediction and attestation verification belong to later plan tasks.

Evidence uses `execution: not_run|software|fake|hardware`. A scaffold has
`result: not_run`, null task/host/timestamps, an explicit command placeholder,
and `hardware_validated: false`. Completed runs have `result: pass|fail` and
UTC timestamps. Only a passing `hardware` record can claim
`hardware_validated: true`; software, fake, unexecuted and failed tests cannot.
Hardware passes require both driver and CUDA version strings even when
`hardware_validated` is false. Only failed, unvalidated hardware attempts with
`host.gpus` matching `^(four|eight)-selected-(B200|B300)$` may record either or
both versions as null when those facts were not collected. This matches the
selected-execution target descriptors emitted by
[the GPU lab recorder](../host/record-deepseek-lab.py) and
[the selected controller](../scripts/record-tau-selected.py), not generic
software diagnostics or appraisal scopes. It is a declared attempted target,
not proof of device observation, readiness, execution success or qualification.
The fields remain required, and supplied strings must still satisfy the
version formats.
This is a consistency rule, not proof of truthful provenance. Never infer a
hardware result from schema validation. Synthetic evidence is only test data.

## Sources

- [JSON Schema 2020-12 validation, including format assertions](https://json-schema.org/draft/2020-12/json-schema-validation)
- [Pinned validator source and compiler API](https://github.com/santhosh-tekuri/jsonschema/tree/v6.0.2)
- [Pinned YAML parser](https://github.com/yaml/go-yaml/tree/v3.0.4)
- [Pinned YAML dependency license](https://github.com/yaml/go-yaml/blob/v3.0.4/LICENSE)
- [DeepSeek confidential environment setting, containers env](https://github.com/tinfoilsh/confidential-deepseek-v4-1-flash/blob/main/tinfoil-config.yml)
- [TDX MRTD and RTMR size constants, 48 bytes each](https://github.com/google/go-tdx-guest/blob/main/abi/abi.go)
- Local requirements: `PLAN.md` sections 5, 7, P0.T3 and `AGENTS.md`.
