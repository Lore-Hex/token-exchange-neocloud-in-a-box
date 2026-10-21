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

Pre-alpha. **No fully verified confidential inference service yet.** Real
8xB200 lab checks and a complete DeepSeek weight download have run. Fresh
eight-GPU appraisal and readiness readback have passed; DeepSeek inference and
quality remain unproven. The lab uses AMD SEV-SNP, not the plan's Intel TDX
deployment.

| Component | State |
|---|---|
| Plan, sizing, phase definitions | written ([PLAN.md](PLAN.md)) |
| CLI skeleton and repository checks | implemented; no serving runtime ([development](docs/development.md)) |
| Model packs (pinned revisions and checksums) | source-audited drafts; not deployable confidential profiles ([model packs](models/README.md)) |
| Pinned weight staging | real DeepSeek download completed: 83 files, 510,311,045,295 bytes, exact-set/hash verification PASS and cleanup confirmed; no model execution implied ([evidence](evidence/p2/2026-10-20-weight-stage-verified/run.json), [checkpoint](docs/tasks/p2-t3-verified-weights-checkpoint.md)) |
| Engine candidate overlay preparation | offline committed-source context and installed-wheel checks tested with fake files; BLOCKED, no image build, ABI or hardware validation ([scope and limits](docs/tasks/p0-t4-engine-overlay.md)) |
| Experimental packaging contract | standalone contract tested with synthetic files; exact entry-point map and sealed bytecode removal subsequently exercised by the bounded smoke application below; default admission unchanged ([scope](docs/tasks/p0-t4-engine-packaging-contract.md)) |
| Python layout descriptor | read-only collection complete: exact interpreter chain, packaging and 14 source pins verified; stdlib child canaries pass, overall qualification remains failed, no engine/GPU admission ([evidence](docs/tasks/p0-t4-engine-python-chain.md#recorded-outcome)) |
| Exact-image application contract | standalone preparer and sealed import-path contract tested with synthetic files; later smoke applied all five stages to the exact image, but no real engine launcher or generic admission is granted ([scope](docs/tasks/p0-t4-engine-application-contract.md)) |
| Bounded engine import smoke | latest actual run verified all 12 source references and completed five-stage application; first import rejected the exact `ldconfig -p` query through `ctypes` during Torch extension initialization; remaining imports not run, cleanup confirmed, no model/GPU/ABI or production qualification ([latest evidence](evidence/p0/2026-10-20-official-vllm-distro-callsite-smoke/run.json), [prior coverage gaps](evidence/p0/2026-10-20-official-vllm-callsite-smoke/run.json), [scope](docs/tasks/p0-t4-engine-import-smoke.md)) |
| Pinned engine image diagnostic | exact-image bounded inventory and detailed collection complete; RECORD/wheel and root/selected-root admission rejected; 13 source expectations match, cleanup confirmed; overall FAIL, still BLOCKED ([evidence](evidence/p0/2026-10-19-official-vllm-record-diagnosis/run.json), [scope and limits](docs/tasks/p0-t4-engine-image-diagnostic.md)) |
| Published AOT stub classification | all four installed assets match exact published hashes, sizes and headers in the read-only diagnostic; generic admission unchanged, no native ABI/runtime qualification ([measured outcome and pins](docs/tasks/p0-t4-cutlass-stubs.md#recorded-outcome)) |
| RECORD/wheel cause diagnostics | read-only image run observed two strict-policy mismatches: extra entry-point group and 2,879 unlisted bytecode files with recorded source-name counterparts; selected dependency pins match; no bytecode trust or admission change ([measured outcome](docs/tasks/p0-t4-engine-record-diagnostic.md#recorded-outcome)) |
| Engine metadata census | selected root counted: 204,969 entries and 19,176,556,448 logical regular-file bytes; collection complete, cleanup confirmed, qualification intentionally failed; admission limits unchanged ([evidence](evidence/p0/2026-10-19-official-vllm-metadata-census/run.json), [scope](docs/tasks/p0-t4-engine-image-diagnostic.md#metadata-only-census)) |
| Guest services | implemented with release-deny behavior and tagged fake tests ([guest services](guest/README.md)) |
| Guest package preparation | executable offline archive/control verification and deterministic private repository preparation; nonbootable mkosi candidate only, no installation, guest build or reproducibility claim ([scope](docs/tasks/p3-t1-offline-package-preparation.md)) |
| Linux package-plan rehearsal | offline rehearsal passes after explicit private empty preferences: 145 packages planned, zero removals, cleanup confirmed. No installation or guest-build qualification ([passing rehearsal](evidence/p3/2026-10-20-apt-private-preferences-pass/run.json), [original failure](evidence/p3/2026-10-20-apt-resolve-failure/run.json), [diagnostic failure](evidence/p3/2026-10-20-apt-missing-preferences/run.json), [scope](docs/tasks/p3-t1-apt-diagnostics.md)) |
| mkosi build inputs | exact 27.1 source and 203-package tools graph; nonexecuting private bundle preparation, not installation or a guest build ([scope and blockers](docs/tasks/p3-t1-mkosi-build-inputs.md)) |
| mkosi tools-tree members | exact pinned certificate data, awk preparation aliases and non-host GRUB classification; data-only tar preparation, no ABI or installability claim ([scope](docs/tasks/p3-t1-mkosi-tools-members.md)) |
| GPU-free end-to-end node | tested against fakes locally and in CI; independently reviewed and merged in [PR #10](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/pull/10) ([evidence](evidence/p1/2026-10-19-combined-node-v4/run.json), [results](evidence/p1/2026-10-19-combined-node-v4/result.json)) |
| Local real-model provider pilot | Qwen3-0.6B CPU inference through standard TLS: 17 conformance checks, auth, usage, upstream failure and cleanup pass on native macOS and an offline Linux/amd64 container; not public or confidential ([native](evidence/p3/2026-10-19-qwen-native-regression-1/run.json), [Linux](evidence/p3/2026-10-19-qwen-linux-4/run.json), [runbook](docs/operators/qwen-pilot.md)) |
| Provider-configurable Qwen service | Real Linux model: operator metadata, all 18 provider checks, Catalog v2 and declared retirement pass; active streaming response finishes through shutdown; no public cloud or billing claim ([evidence](evidence/p3/2026-10-19-provider-neutral-linux/run.json), [results](evidence/p3/2026-10-19-provider-neutral-linux/output.log), [runbook](docs/operators/qwen-service.md)) |
| Model bring-up and benchmarks | compatibility bring-up in progress; model inference, throughput and quality/tau benchmarks not yet run (P2) |
| Tau evaluation preparation | [offline pinned-input manifest/budget foundation](docs/tasks/p2-t3-tau-preflight.md) and [content-free aggregation](docs/tasks/p2-t3-tau-aggregation.md); synthetic software tests only, no live tau adapter or scored run |
| CPU-only startup diagnosis | native application passes all five stages; latest entry rejects the identified standard-six finder at checkpoint 11 after the Botocore adapter. Cleanup confirmed; no inference/GPU/confidentiality acceptance ([latest run](evidence/p2/2026-10-20-cpu-native-39485114646-1/run.json), [source and limits](docs/tasks/p2-t3-botocore-six-compat.md), [earlier Botocore observation](evidence/p2/2026-10-20-cpu-native-39467970211-1/README.md)) |
| GPU appraisal and gated readiness | fresh eight-device appraisal PASS; nonce, measurements, certificates/revocation, secure boot, debug-disabled and local identity checks passed; readiness 0 to 1 readback and NVML shutdown verified. Not full hardware/runtime/confidential qualification ([success](evidence/p2/2026-10-20-gpu-appraisal-121447763215/run.json), [prior failure](evidence/p2/2026-10-20-gpu-appraisal-120201396352/run.json), [checkpoint](docs/tasks/p2-t3-appraisal-checkpoint.md)) |
| Bounded DeepSeek CC functional lab | latest attempt completed weight validation and five application stages, then engine startup exited 1 after 8 seconds; Docker reported no OOM and cleanup confirmed. No inference or quality success ([latest failure](evidence/p2/2026-10-20-correctness-startup-failed/run.json), [prior recorder failure](evidence/p2/2026-10-20-correctness-recorder-failed/run.json), [limits](docs/tasks/p2-t3-correctness-checkpoint.md), [scope](docs/tasks/p2-t3-deepseek-lab.md)) |
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
