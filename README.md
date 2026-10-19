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

Our first provider is **Hixtube** (`hixtube`), using `hixtube.com`. This is
the operator's identity, not a rename of the reusable Apache-2.0 toolkit.
The [Hixtube launch checklist](docs/operators/hixtube.md) separates domain setup
from an actually serving, independently verified confidential node.
The first functional pilot uses [small Qwen](docs/operators/qwen-pilot.md) locally;
its eventual cloud deployment is confined to the separate `hixtube-neocloud`
project, not TrustedRouter production. See the [cloud preflight](docs/operators/hixtube-isolated-cloud.md).

## Status

Pre-alpha. **Nothing in this repository has run on confidential hardware yet.**

| Component | State |
|---|---|
| Plan, sizing, phase definitions | written ([PLAN.md](PLAN.md)) |
| CLI skeleton and repository checks | implemented; no serving runtime ([development](docs/development.md)) |
| Model packs (pinned revisions and checksums) | source-audited drafts; not deployable confidential profiles ([model packs](models/README.md)) |
| Guest services | implemented with release-deny behavior and tagged fake tests ([guest services](guest/README.md)) |
| GPU-free end-to-end node | tested against fakes locally and in CI; independently reviewed and merged in [PR #10](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/pull/10) ([evidence](evidence/p1/2026-10-19-combined-node-v4/run.json), [results](evidence/p1/2026-10-19-combined-node-v4/result.json)) |
| Local real-model provider pilot | Qwen3-0.6B CPU inference through standard TLS: 17 conformance checks, auth, usage, upstream failure and cleanup pass on native macOS and an offline Linux/amd64 container; not public or confidential ([native](evidence/p3/2026-10-19-qwen-native-regression-1/run.json), [Linux](evidence/p3/2026-10-19-qwen-linux-3/run.json), [runbook](docs/operators/qwen-pilot.md)) |
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
