# P0.T4: Source Audit Blocker

Status: **historical failed audit; all-file total correction approved**.
DS1017 remains first. The user authorized resuming metadata manifests and draft
non-confidential profiles. See [current sources and limits](SOURCES.md).
The failed evidence below is preserved unchanged; its original acceptance totals
were wrong. PLAN changes belong to its separate owner. Packs remain not deployable
until built weight images and hardware validation exist. A newly verified
Tinfoil-only global-UVA patch prevents claiming upstream CC compatibility.

## Complete-Manifest Totals

The audit checked repository identity at the immutable revision, then fetched
its recursive tree. No pagination header was present. Directory entries do not
contribute to totals. The API supplies file `size` and LFS `lfs.oid` (SHA-256),
with matching `lfs.size`. This initial audit did not download/hash non-LFS files;
the subsequent generation and live-check evidence is linked from SOURCES.md.

| HF repository | Immutable revision | Files / weight shards | Planned total = weight bytes | All-file bytes |
|---|---|---:|---:|---:|
| `deepseek-ai/DeepSeek-V4.1-Flash-1017` | `1bae0e1088993b9b82de08f59de5be5a34e42170` | 83 / 48 | 510,296,708,312 | **510,311,045,295** |
| `zai-org/GLM-5.3` | `ba32f8c19c8738e2c57c71798c601d477a7bf844` | 156 / 141 | 755,632,050,320 | **755,663,689,302** |

Primary sources: [DS1017 pinned tree](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash-1017/tree/1bae0e1088993b9b82de08f59de5be5a34e42170?recursive=true&limit=1000)
and [GLM pinned tree](https://huggingface.co/api/models/zai-org/GLM-5.3/tree/ba32f8c19c8738e2c57c71798c601d477a7bf844?recursive=true&limit=1000).
The identity API responses, tree response hashes, counts and arithmetic are
recorded in [sources.json](../evidence/p0/2026-10-19-model-pack-source-audit/sources.json).

The difference is 14,336,983 bytes for DS1017 and 31,638,982 for GLM. A manifest
including configuration, tokenizers, licenses and code cannot equal the
weights-only total. The full manifest must sum to **all-file bytes**; dropping
required non-weight files or relabeling a weights-only sum is not acceptable.
T3's complete-manifest sum validation agrees with this interpretation.

The user subsequently approved the all-file totals. The original weight-byte
values remain separately named facts, not a complete pack's `total_bytes`.

## Tinfoil Reference

The source corresponding to release `v0.0.7+b200` is pinned at
`29bc3ea355129b1dda97282ce80ce972530ee4fc`:
[immutable released configuration](https://github.com/tinfoilsh/confidential-deepseek-v4-1-flash/blob/29bc3ea355129b1dda97282ce80ce972530ee4fc/tinfoil-config.yml).
Observed settings, not a new deployment profile:

| Configuration field | Observed value |
|---|---|
| Profile | B200, four GPUs, tensor parallel size four |
| HF identity | `deepseek-ai/DeepSeek-V4.1-Flash-1017@1bae0e1088993b9b82de08f59de5be5a34e42170` |
| Served name | `deepseek-v4-1-flash`, distinct from the HF repository name |
| GPU-resident Engram settings | `VLLM_DISABLE_UVA=1`; `--engram-config {"cpu_offload":false}` |
| Context / scheduler limit | `--max-model-len 1048576`; `--max-num-seqs 64` |
| Image | `ghcr.io/tinfoilsh/confidential-deepseek-v4-1-flash@sha256:989d5d0d8c02b240254f39ada9ac4e898c430ac955b12feb68070d41bb12b639` |

The main-branch snapshot at `803246156cb54952d0fc93a9fb66cdd4d87af2b3` has
an all-zero placeholder image digest in its
[B200 config](https://github.com/tinfoilsh/confidential-deepseek-v4-1-flash/blob/803246156cb54952d0fc93a9fb66cdd4d87af2b3/tinfoil-config.b200.yml).
That placeholder must never be used as a pin. Its
[Dockerfile.b200](https://github.com/tinfoilsh/confidential-deepseek-v4-1-flash/blob/803246156cb54952d0fc93a9fb66cdd4d87af2b3/Dockerfile.b200)
asserts vLLM `0.31.0`, build commit `db9527a46873454610df6dbedf79a36d6bf1a7f6`,
and names base image
`vllm/vllm-openai@sha256:a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b`.
It applies Tinfoil patches and builds a distinct image. Neither that base
digest nor the released Tinfoil digest was independently checked against a
registry manifest in this audit. No container or hardware execution occurred.
Do not generalize this reference into a validated upstream-vLLM or DP2/TP4
eight-GPU profile.

## Official Engine Sources

Recipe snapshot `vllm-project/recipes@2338e9edec1d5df5f823abf3c1f19e7ba0b2ed0e`:

- [DeepSeek recipe](https://github.com/vllm-project/recipes/blob/2338e9edec1d5df5f823abf3c1f19e7ba0b2ed0e/models/deepseek-ai/DeepSeek-V4.1-Flash.yaml), `model.min_vllm_version` and `variants.default`: vLLM `0.31.0` minimum and DS1017 as the default variant. The recipe's base `model_id` still names the older launch repository; do not mistake it for the selected variant.
- [GLM recipe](https://github.com/vllm-project/recipes/blob/2338e9edec1d5df5f823abf3c1f19e7ba0b2ed0e/models/zai-org/GLM-5.3.yaml), `model` and `variants.default.hardware_overrides.h200`: vLLM `0.29.0` minimum, native FP8 repository, and H200 context cap `262144`.

Cookbook snapshot `sgl-project/sglang@299da60131dbbd9d3be0e1b2463b1f82cd984729`:

- [DeepSeek cookbook](https://github.com/sgl-project/sglang/blob/299da60131dbbd9d3be0e1b2463b1f82cd984729/docs/cookbook/autoregressive/DeepSeek/DeepSeek-V4_1.mdx), installation: NVIDIA support uses the preview tag `lmsysorg/sglang:dev-dsv41`, not a release claimed by this audit.
- [GLM cookbook](https://github.com/sgl-project/sglang/blob/299da60131dbbd9d3be0e1b2463b1f82cd984729/docs/cookbook/autoregressive/GLM/GLM-5.3.mdx), installation: the command uses `lmsysorg/sglang:latest`. A mutable tag is not an admissible measured-image pin.

The resumed work performs registry digest verification separately and cites
each candidate flag. No flags or image hashes have been invented.
Recipe/config byte hashes in the evidence identify what was inspected; they are
not container digests. The HF identity API can include changing download/like
counters even for a pinned revision, so its response hash is an observation,
not a promise of byte-identical API responses on every rerun.

## Reproduction And Safety

The source-audit program was committed as
`d5c6bcf907994d10cd8c1d28f5fc4e06b9a61722` before execution. From a clean checkout
with Go 1.27.2 and permitted public HTTPS access:

```sh
go build -trimpath -o build/sourceaudit ./models/sourceaudit
./build/sourceaudit
```

The command accepts no arguments. It writes to the fixed repository-relative
`evidence/p0/<UTC-date>-model-pack-source-audit/` and refuses to overwrite an
existing directory. Rerun from a separate checkout if that day's evidence is
already present. It refuses uncommitted changes to its own source directory.
An interrupted or failed fetch can leave an incomplete directory; inspect it
before any cleanup. Never replace existing evidence with a new run.

The audit makes twelve fixed HTTPS GET requests to the two allowlisted public
hosts. Each response is capped at 4 MiB, with at most one additional byte read
to detect overflow; known excessive Content-Length fails before reading.
Redirects and every pagination Link header fail closed. The request timeout is
30 seconds and the whole run deadline is three minutes. There are no weight
resolve/download requests, container requests, credentials, retries or paid
resources. Actual response bodies totaled **235,337 bytes**, recorded in the
source report. Only derived facts, source hashes and public fixed URLs are
saved, not raw upstream bodies or request/response headers.

The observed exit status was **1**, intentionally: the source collection
succeeded but P0.T4 acceptance did not. See [run.json](../evidence/p0/2026-10-19-model-pack-source-audit/run.json)
and [output.log](../evidence/p0/2026-10-19-model-pack-source-audit/output.log).
The evidence claims software execution only, with hardware validation false
and driver/CUDA null. It is not proof of model serving, weight integrity,
attestation, licensing eligibility or deployability.

New audit code is Apache-2.0 under the repository license. No upstream code,
model weights, recipes or license texts have been vendored or relicensed.
The model licenses remain upstream: [DeepSeek LICENSE](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017/blob/1bae0e1088993b9b82de08f59de5be5a34e42170/LICENSE)
and [GLM-5.3 LICENSE](https://huggingface.co/zai-org/GLM-5.3/blob/ba32f8c19c8738e2c57c71798c601d477a7bf844/LICENSE).

## Software Checks

- `make check` passed with Go 1.27.2, including staticcheck, vet, race tests,
  shellcheck, yamllint, local doc links and gitleaks working-tree/history scans.
  Both secret-scanner positive controls passed.
- Source-audit tests cover body limits with and without Content-Length,
  pagination rejection, redirect rejection, sanitized errors, credential-free
  requests, invalid metadata, overflow, weight/all-file accounting and refusal
  to overwrite an existing evidence directory.
- T3's `nib lint /private/tmp/nib-p0-t4`, built from its local validator branch
  at `6a9d1a4`, accepted the generated evidence against `evidence.schema.json`.
- T5's committed `scripts/scrub-secrets.sh` at `373207a`, with its built `nib`
  and a private local deny list, was piped to `cmp - output.log`. It exited
  successfully and made no changes. The deny list was not committed; no
  credentials were read or sent by the source audit.

The initial sandboxed network attempt could not connect and left only an empty
directory. That empty directory was removed before the successful bounded
metadata run with network permission. No evidence file was overwritten.
