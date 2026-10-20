# token-exchange-neocloud-in-a-box

Open-source software to run a small **confidential GPU inference cloud**: an
8-GPU server whose model runs inside an Intel TDX confidential VM, with the GPUs
in NVIDIA confidential-computing mode, served from an OpenAI-compatible endpoint
whose TLS key is bound to hardware attestation. Nodes can be verified by anyone
and listed on [TrustedRouter](https://trustedrouter.com/providers/marketplace),
and their tokens can be traded on the NY Token Exchange.

The first two nodes are planned to serve
[DeepSeek-V4.1-Flash-1017](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017)
and [GLM-5.3](https://huggingface.co/zai-org/GLM-5.3) on HGX 8×B200 servers.

**Read [PLAN.md](PLAN.md)** for sizing, the order of work and the phases.
Agents working in this repo read [AGENTS.md](AGENTS.md) first.

## Serve Models, Not a Billing System

This is a provider-neutral toolkit. Bring your own hardware, model and endpoint;
sell inference through TrustedRouter. Keep your operator identity, credentials
and deployment configuration outside this repository.

| This toolkit / your node | TrustedRouter |
|---|---|
| Serve inference behind an authenticated API | Authenticate customers and enforce their credit limits |
| Publish models, capabilities and agreed prices | Discover routes and apply customer pricing |
| Return actual engine token usage, including the final streaming usage chunk | Keep the accounting ledger and bill customers |
| Maintain health, capacity and verified attestation | Handle customer payments and provider payouts under the onboarding agreement |

No local customer accounts, balances, invoices, payment processor or settlement
database. Basic health and content-free lifecycle logs are enough to operate the
pilot; usage travels in API responses, not a second billing ledger.

Start with the [TrustedRouter operator guide](docs/operators/trustedrouter.md).
The [small Qwen pilot](docs/operators/qwen-pilot.md) exercises real inference
locally, and the [service runbook](docs/operators/qwen-service.md) describes its
endpoint configuration. Marketplace activation and payout terms are separate
onboarding steps; running the toolkit does not automatically list a provider.

## Status

Pre-alpha. **Nothing in this repository has run on confidential hardware yet.**

| Component | State |
|---|---|
| Plan, sizing, phase definitions | written ([PLAN.md](PLAN.md)) |
| CLI skeleton and repository checks | implemented; no serving runtime ([development](docs/development.md)) |
| Model packs (pinned revisions and checksums) | source-audited drafts; not deployable confidential profiles ([model packs](models/README.md)) |
| Pinned weight staging | bounded streaming download, verified-file restart and offline exact-set verification implemented; tested with synthetic files, not a complete DeepSeek download ([staging](docs/tasks/p0-t4-weight-staging.md)) |
| Engine candidate overlay preparation | offline committed-source context and installed-wheel checks tested with fake files; BLOCKED, no image build, ABI or hardware validation ([scope and limits](docs/tasks/p0-t4-engine-overlay.md)) |
| Experimental packaging contract | exact upstream entry-point map and sealed removal of uniquely source-backed unlisted bytecode tested with synthetic files; default admission unchanged, no actual image application ([scope](docs/tasks/p0-t4-engine-packaging-contract.md)) |
| Python layout descriptor | actual read-only collection partial: packaging verified, 14 source pins match; interpreter resolution rejects unsafe target, child canaries not run, no admission ([evidence](docs/tasks/p0-t4-engine-python-prerequisites.md#recorded-outcome)) |
| Pinned engine image diagnostic | exact-image bounded inventory and detailed collection complete; RECORD/wheel and root/selected-root admission rejected; 13 source expectations match, cleanup confirmed; overall FAIL, still BLOCKED ([evidence](evidence/p0/2026-10-19-official-vllm-record-diagnosis/run.json), [scope and limits](docs/tasks/p0-t4-engine-image-diagnostic.md)) |
| Published AOT stub classification | all four installed assets match exact published hashes, sizes and headers in the read-only diagnostic; generic admission unchanged, no native ABI/runtime qualification ([measured outcome and pins](docs/tasks/p0-t4-cutlass-stubs.md#recorded-outcome)) |
| RECORD/wheel cause diagnostics | read-only image run observed two strict-policy mismatches: extra entry-point group and 2,879 unlisted bytecode files with recorded source-name counterparts; selected dependency pins match; no bytecode trust or admission change ([measured outcome](docs/tasks/p0-t4-engine-record-diagnostic.md#recorded-outcome)) |
| Engine metadata census | selected root counted: 204,969 entries and 19,176,556,448 logical regular-file bytes; collection complete, cleanup confirmed, qualification intentionally failed; admission limits unchanged ([evidence](evidence/p0/2026-10-19-official-vllm-metadata-census/run.json), [scope](docs/tasks/p0-t4-engine-image-diagnostic.md#metadata-only-census)) |
| Guest services | implemented with release-deny behavior and tagged fake tests ([guest services](guest/README.md)) |
| GPU-free end-to-end node | tested against fakes locally and in CI; independently reviewed and merged in [PR #10](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/pull/10) ([evidence](evidence/p1/2026-10-19-combined-node-v4/run.json), [results](evidence/p1/2026-10-19-combined-node-v4/result.json)) |
| Local real-model provider pilot | Qwen3-0.6B CPU inference through standard TLS: 17 conformance checks, auth, usage, upstream failure and cleanup pass on native macOS and an offline Linux/amd64 container; not public or confidential ([native](evidence/p3/2026-10-19-qwen-native-regression-1/run.json), [Linux](evidence/p3/2026-10-19-qwen-linux-4/run.json), [runbook](docs/operators/qwen-pilot.md)) |
| Provider-configurable Qwen service | Real Linux model: operator metadata, all 18 provider checks, Catalog v2 and declared retirement pass; active streaming response finishes through shutdown; no public cloud or billing claim ([evidence](evidence/p3/2026-10-19-provider-neutral-linux/run.json), [results](evidence/p3/2026-10-19-provider-neutral-linux/output.log), [runbook](docs/operators/qwen-service.md)) |
| Model bring-up and benchmarks, non-confidential | not started (P2) |
| Confidential pipeline on a 1-GPU TDX VM | not started (P3) |
| TrustedRouter generic verifier | not started (P4) |
| Bare-metal 8×B200 confidential node | not started (P5) |

This table only says "validated" for things with committed evidence from real
hardware.

The P1 container run passes provider-check Tier 4, tagged fake verification,
fourteen rejecting mutation checks, admission, privacy and weight-gate tests.
The release verifier rejects the fake evidence. Runtime has no external
network, API secrets or GPU access; container and image removal are verified.
This is software integration evidence, not confidential inference or hardware
validation. See the [P1.T3 task record](docs/tasks/p1-t3-container-e2e.md).

## Start contributing

```sh
make check
make build
./build/nib --help
```

See [development instructions](docs/development.md) for prerequisites. This is
not yet a deployable node; hardware validation and independent attestation are
required before any confidential route can be listed on
[NY Token Exchange](https://nytokenexchange.com).

## Related projects

- [trustedrouter-confidential-stack](https://github.com/Lore-Hex/trustedrouter-confidential-stack):
  the `trcs` shim and the `tr-attestation/v0` protocol this project builds on.
- [trustedrouter-provider-check](https://github.com/Lore-Hex/trustedrouter-provider-check):
  the conformance checker every node must pass.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Report security issues
privately as described in [SECURITY.md](SECURITY.md).
