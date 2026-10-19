# Model Pack Sources

Both packs are **draft, not deployable**. They have complete metadata manifests,
not downloaded/verified weights or built erofs/verity images. The profile name
`b200x8-gcp-plain` means a non-confidential P2 candidate, not a hardware result.
No inference container was executed. P2 must validate compatibility, correctness,
capacity, request-data logging behavior and performance before adopting a profile.

## Immutable Source Keys

Each flag comment in `pack.yaml` names a key below and the relevant source section.
These keys are references, not copied upstream implementation or a new license.

- **A**, vLLM `EngineArgs.add_cli_args` and `AsyncEngineArgs.add_cli_args` at build `db9527a46873454610df6dbedf79a36d6bf1a7f6`: [arg_utils.py](https://github.com/vllm-project/vllm/blob/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/engine/arg_utils.py).
- **F**, vLLM `BaseFrontendArgs`, `FrontendArgs`, and `add_cli_args`: [launchers/cli_args.py](https://github.com/vllm-project/vllm/blob/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/entrypoints/launchers/cli_args.py).
- **E**, vLLM environment definitions: [envs.py](https://github.com/vllm-project/vllm/blob/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/envs.py).
- **D**, official DeepSeek recipe, selected `variants.default` DS1017, `model`, `features`, `single_node_tp`, and GPU-resident Engram guide: [DeepSeek-V4.1-Flash.yaml](https://github.com/vllm-project/recipes/blob/2338e9edec1d5df5f823abf3c1f19e7ba0b2ed0e/models/deepseek-ai/DeepSeek-V4.1-Flash.yaml).
- **G**, official GLM recipe, native FP8 `model`, `features` and guide / FP8 on 8xB200: [GLM-5.3.yaml](https://github.com/vllm-project/recipes/blob/2338e9edec1d5df5f823abf3c1f19e7ba0b2ed0e/models/zai-org/GLM-5.3.yaml).
- **T**, Tinfoil [Dockerfile.b200](https://github.com/tinfoilsh/confidential-deepseek-v4-1-flash/blob/803246156cb54952d0fc93a9fb66cdd4d87af2b3/Dockerfile.b200) asserts upstream vLLM version/build and base digest. The [released B200 config](https://github.com/tinfoilsh/confidential-deepseek-v4-1-flash/blob/29bc3ea355129b1dda97282ce80ce972530ee4fc/tinfoil-config.yml) is a separate, patched four-GPU deployment.

## Deliberate Limits

The DeepSeek recipe now defaults B200 TP2 to Engram CPU offload. This pack instead
composes its documented TP4 and `cpu_offload:false` options with PLAN's DP2
target. The recipe's Blackwell attention/cache options are retained. This exact
combination is **not validated**. GLM uses the documented B200 TP8/FP8 recipe.
Speculation is opt-in in both recipes; these candidates leave it off. No external
draft models, remote-code trust flags or unpinned secondary downloads are added.
`/model` is the operator's proposed read-only verified-weight mount, not a remote
HF identifier. Image launch/mount integration belongs to later phases.

**CC blocker:** `VLLM_DISABLE_UVA` is not an upstream environment setting at the
pinned build. Tinfoil introduces it in
[0001-explicit-uva-fallback.patch](https://github.com/tinfoilsh/confidential-deepseek-v4-1-flash/blob/803246156cb54952d0fc93a9fb66cdd4d87af2b3/patches/0001-explicit-uva-fallback.patch).
Setting it on the unpatched base does not establish a global UVA prohibition.
The pack therefore does not set it or provide `b200x8-tdx`. Disabling Engram
offload is not equivalent to disabling all UVA use. CC requires a separately
reviewed, pinned patched engine and actual hardware evidence. Tinfoil's release
digest is checked only as a reference, not selected as this pack's engine.

**Tinfoil licensing warning (reference-only):** the coordinator's read-only
audit of the [released tree](https://github.com/tinfoilsh/confidential-deepseek-v4-1-flash/tree/29bc3ea355129b1dda97282ce80ce972530ee4fc)
found no LICENSE/COPYING/NOTICE; [repository metadata](https://api.github.com/repos/tinfoilsh/confidential-deepseek-v4-1-flash)
reported `license: null`, and the LICENSE endpoint returned 404. Reuse and
redistribution rights are not established: do not copy or relicense these patches,
or select the patched image as redistributable, without establishing those rights.
A future CC profile needs independently licensed upstream patches or permission.
The plain-only profiles are unchanged.

**Security freshness blocker:** the selected vLLM 0.31.0 is a reference candidate,
not a security-current deployment recommendation. Upstream
[v0.32.0 release notes](https://github.com/vllm-project/vllm/releases/tag/v0.32.0)
(published 2026-10-18) list fixes for GHSA-4hhp-h66f-j5j7 (chat-template resource
exhaustion), GHSA-q97c-p368-75c4 and GHSA-frvg-qw88-hww9. A security upgrade and
review are required before deployment beyond isolated, plain P2 testing with
synthetic inputs only; do not expose this candidate to users or untrusted traffic.
Keep existing pins until a matching recipe and immutable image have been verified.
The testing exception is not security validation or authorization to spend money.

Request/output logging and Uvicorn access logging are explicitly disabled using
the pinned Python frontend flags. This is configuration, not a proven zero-data
retention or error-path audit of the full engine. No confidential deployment is
authorized by these manifests. Empty verity roots must fail the shared deployable
validator even when every remote metadata check succeeds.

The [historical blocker audit](P0-T4-BLOCKER.md) retains the original failed
weights-only acceptance totals and source contradictions. The user approved
the complete all-file totals; the PLAN owner handles that correction separately.
SGLang's cited mutable preview/latest tags are not pack pins.

## Model Licenses

Repository tooling is Apache-2.0. Model files and licenses remain upstream and
are neither vendored nor relicensed. Manifests include their license files:

- DeepSeek: [MIT LICENSE at the pinned revision](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017/blob/1bae0e1088993b9b82de08f59de5be5a34e42170/LICENSE).
- GLM: [GLM-5.3 LICENSE at the pinned revision](https://huggingface.co/zai-org/GLM-5.3/blob/ba32f8c19c8738e2c57c71798c601d477a7bf844/LICENSE). This is not replaced with Apache-2.0 or assumed to be MIT.

## Generation And Verification

From a clean, committed checkout with Go 1.27.2 and public HTTPS access:

```sh
go run ./models/packcheck generate
go run ./cmd/nib model check-remote models/deepseek-v4.1-flash-1017/1
go run ./cmd/nib model check-remote models/glm-5.3/1
go run ./models/packcheck check
```

`generate` refuses existing manifests. Use a clean checkout of the generator
commit before manifests were added for exact regeneration; do not delete or
replace recorded evidence. Commit generated manifests before running `check`.
The latter exercises `CheckRemote` for both packs, checks fixed approved pins
and engine digests, and records fresh evidence without overwriting an older run.
It uses only fixed input paths/modes, never records arbitrary CLI arguments,
and requires a clean checkout so its evidence SHA identifies all running code.

HF limits per call: 4 MiB metadata response, 16 MiB non-LFS file, 64 MiB aggregate,
32 pages, 10,000 paths, 30-second request timeout and five-minute operation timeout.
Only pinned same-origin pagination and pinned same-file HF resolve-cache
redirects are accepted. Bodies use a one-byte overflow sentinel. LFS objects,
including small LFS files, are never downloaded. Non-LFS bytes must match the
tree's exact size and Git blob SHA-1 before their content SHA-256 is recorded.
The tree must cover exactly the pinned identity response's sibling file set.

Registry verification reads only a bounded manifest and, if necessary, a bounded
anonymous public-pull token response from an allowlisted registry auth endpoint.
It hashes the manifest bytes against the source-pinned digest; it downloads no
layers or image config and proves neither publisher identity nor image safety.
Tokens stay in memory and are not included in evidence. The live `check` runs
HF generation twice per pack (once to record transfer accounting, once through
the public check API), so its maximum HF body budget is four times 64 MiB.
Upstream text-source auditing has its own fixed request count and body caps.
No weight downloads, credentials supplied by the operator, paid resources or
publication are performed by either command.

## Recorded Runs

The final [live check](../evidence/p0/2026-10-19T015247Z-model-pack-check/run.json)
passed from committed checker `230be00`, including both public `CheckRemote`
calls, all-file manifests, fixed pack identities and both registry digests.
Its [structured report](../evidence/p0/2026-10-19T015247Z-model-pack-check/report.json)
contains file paths, sizes and SHA-256 values; its
[output](../evidence/p0/2026-10-19T015247Z-model-pack-check/output.log) records the
limits of these software-only checks. Hardware validation is false.

The successful [generation run](../evidence/p0/2026-10-19T014759Z-model-pack-generate/run.json)
used committed generator `4fc3bef`. Its [output](../evidence/p0/2026-10-19T014759Z-model-pack-generate/output.log)
records 83 DS1017 files / 510,311,045,295 bytes and 156 GLM files /
755,663,689,302 bytes. The two HF fetches consumed 14,150,160 and 151,962
metadata/non-LFS body bytes, respectively. All LFS objects were skipped.
Both source-pinned registry manifest digests passed. The initial generation
report serialized manifests as strings; the subsequent checker emits structured
model/file records. Historical evidence is not rewritten.

Two earlier failed runs remain recorded: the first had a sanitized generic
request failure, and the second identified rejection of HF's encoded nested-file
cache redirect. The corrected policy admits only the exact file's canonical
encoded cache path at the same repository/revision/origin. Other redirects fail.
Redirect response bodies are closed unread, so they cannot escape body accounting.

Software tests exercise metadata/file limits, pagination limits/cycles and URL
validation, exact all-file coverage, size overflow/mismatch, LFS and Git blob
hashes, network/read failures, cancellation, redirect bodies, anonymous registry
authentication, digest/media-type checks, local schema/file safety, draft
non-deployability, CLI dispatch/redaction and exclusive evidence writes.
