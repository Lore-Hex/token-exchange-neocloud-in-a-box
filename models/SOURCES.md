# Model Pack Sources

Both packs are **draft, not deployable**. They have complete metadata manifests,
not downloaded/verified weights or built erofs/verity images. The profile name
`b200x8-gcp-plain` means a non-confidential P2 candidate, not a hardware result.
No inference container was executed. P2 must validate compatibility, correctness,
capacity, request-data logging behavior and performance before adopting a profile.

## Immutable Source Keys

Each flag comment in `pack.yaml` names a key below and the relevant source section.
These keys are references, not copied upstream implementation or a new license.

- **A**, vLLM `EngineArgs.add_cli_args` and `AsyncEngineArgs.add_cli_args` at v0.32.0 revision `5df63a7203cdb9328d824a27b682e77c7b9928be`: [arg_utils.py](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/engine/arg_utils.py).
- **F**, vLLM `BaseFrontendArgs`, `FrontendArgs`, and `add_cli_args`: [launchers/cli_args.py](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/launchers/cli_args.py).
- **E**, vLLM environment definitions, including telemetry opt-outs: [envs.py](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/envs.py).
- **C**, Engram configuration: [engram.py](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/config/engram.py). `cpu_offload` defaults true upstream; our DeepSeek profile explicitly overrides it to false.
- **R**, official [v0.32.0 release](https://github.com/vllm-project/vllm/releases/tag/v0.32.0) and [release tag revision](https://api.github.com/repos/vllm-project/vllm/git/ref/tags/v0.32.0). Both packs pin its Linux/amd64 manifest `sha256:6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9`, not the mutable tag or multi-platform index. `packcheck` verifies the manifest and config digest, platform, entrypoint and source-revision metadata. Registry metadata is not signed build provenance or proof of the installed code.
- **D**, official DeepSeek recipe, selected `variants.default` DS1017, `model`, `features`, `single_node_tp`, and GPU-resident Engram guide: [DeepSeek-V4.1-Flash.yaml](https://github.com/vllm-project/recipes/blob/2338e9edec1d5df5f823abf3c1f19e7ba0b2ed0e/models/deepseek-ai/DeepSeek-V4.1-Flash.yaml).
- **G**, official GLM recipe, native FP8 `model`, `features` and guide / FP8 on 8xB200: [GLM-5.3.yaml](https://github.com/vllm-project/recipes/blob/2338e9edec1d5df5f823abf3c1f19e7ba0b2ed0e/models/zai-org/GLM-5.3.yaml).
- **T (historical reference only)**, Tinfoil [Dockerfile.b200](https://github.com/tinfoilsh/confidential-deepseek-v4-1-flash/blob/803246156cb54952d0fc93a9fb66cdd4d87af2b3/Dockerfile.b200) supplied the former upstream 0.31.0 base pin. Its [released B200 config](https://github.com/tinfoilsh/confidential-deepseek-v4-1-flash/blob/29bc3ea355129b1dda97282ce80ce972530ee4fc/tinfoil-config.yml) is a separate patched four-GPU deployment, not our selected image. Current checks do not fetch or depend on Tinfoil's patches/image.

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
Neither our prior 0.31.0 base nor the selected 0.32.0 defines that variable.
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
The plain-only profiles do not incorporate those patches.

**Security update, still a draft:** the former vLLM 0.31.0 pin is replaced with
the official 0.32.0 Linux/amd64 image. Upstream
[v0.32.0 release notes](https://github.com/vllm-project/vllm/releases/tag/v0.32.0)
(published 2026-10-18) announce security and robustness changes. Their mapping
of GHSA-4hhp-h66f-j5j7 to chat-template exhaustion conflicts with the
[advisory itself](https://github.com/vllm-project/vllm/security/advisories/GHSA-4hhp-h66f-j5j7),
which concerns MiMo SSRF/local-file access and names 0.26.0 as patched. Do not
claim that advisory makes 0.31.0 vulnerable or that a version bump proves safety.
The pinned recipe
settings were reviewed against this release's argument/configuration sources;
that is not execution of the CLI, a vulnerability scan, a compatibility test
or permission to expose it. Only isolated plain P2 synthetic testing is an
intended next use, subject to its hardware/run gates. Complete layer/dependency
review, logging/error-path tests, full-model execution and all confidential
hardware work remain outstanding. A newer version alone never marks this safe
for untrusted traffic or deployable.

Request/output logging and Uvicorn access logging are explicitly disabled using
the pinned Python frontend flags. `VLLM_NO_USAGE_STATS=1` and
`VLLM_DO_NOT_TRACK=1` also opt out of upstream usage tracking. This is
configuration, not a proven zero-data
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

Registry verification reads only a bounded manifest/configuration and, if necessary, a bounded
anonymous public-pull token response from an allowlisted registry auth endpoint.
It hashes the manifest bytes against the source-pinned digest and the bounded
config bytes against that manifest's descriptor; it downloads no layers and
proves neither publisher identity nor image safety. The selected image must be
Linux/amd64, declare the reviewed revision in its source/build metadata, and
use the `vllm serve` entrypoint. Multi-platform indexes are rejected by the
engine-specific check; the older generic reference checker remains unchanged.
Docker's documented `production.cloudfront.docker.com` host is allowed for one
307 redirect to the exact config-digest path only. Its signed query stays
bounded and in memory; no registry authorization or cookies are forwarded.
Further redirects fail, and the config size/hash must still match the manifest.
Tokens stay in memory and are not included in evidence. The live `check` runs
HF generation twice per pack (once to record transfer accounting, once through
the public check API), so its maximum HF body budget is four times 64 MiB.
Upstream source auditing has seven fixed requests capped at 4 MiB each, including
the public release-tag reference; credentials and redirects are not used.
No weight downloads, credentials supplied by the operator, paid resources or
publication are performed by either command.

## Recorded Runs

The [0.32.0 engine check](../evidence/p0/2026-10-19T103148Z-model-pack-check/run.json)
passed from `c6f5b54`. The
[report](../evidence/p0/2026-10-19T103148Z-model-pack-check/report.json)
records the reviewed release source, exact Linux/amd64 manifest/configuration
hashes and self-declared source metadata, and both unchanged complete weight
manifests. This is the current software metadata audit; it does not execute
vLLM or establish GPU/confidential compatibility.

The earlier [0.31.0 live check](../evidence/p0/2026-10-19T015247Z-model-pack-check/run.json)
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
