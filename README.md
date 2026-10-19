# token-exchange-neocloud-in-a-box

Open-source software to run a small **confidential GPU inference cloud**: an
8-GPU server whose model runs inside an Intel TDX confidential VM, with the GPUs
in NVIDIA confidential-computing mode, served from an OpenAI-compatible endpoint
whose TLS key is bound to hardware attestation. Nodes can be verified by anyone
and listed on [TrustedRouter](https://trustedrouter.com/providers/marketplace),
and their tokens can be traded on the NY Token Exchange.

The first two nodes serve
[DeepSeek-V4.1-Flash-1017](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017)
and [GLM-5.3](https://huggingface.co/zai-org/GLM-5.3) on HGX 8×B200 servers.

**Read [PLAN.md](PLAN.md)** for sizing, the order of work and the phases.
Agents working in this repo read [AGENTS.md](AGENTS.md) first.

## Status

Pre-alpha. **Nothing in this repository has run on confidential hardware yet.**

| Component | State |
|---|---|
| Plan, sizing, phase definitions | written ([PLAN.md](PLAN.md)) |
| Model packs (pinned revisions and checksums) | not started (P0) |
| GPU-free end-to-end node in CI | not started (P1) |
| Model bring-up and benchmarks, non-confidential | not started (P2) |
| Confidential pipeline on a 1-GPU TDX VM | not started (P3) |
| TrustedRouter generic verifier | not started (P4) |
| Bare-metal 8×B200 confidential node | not started (P5) |

This table only says "validated" for things with committed evidence from real
hardware.

## Related projects

- [trustedrouter-confidential-stack](https://github.com/Lore-Hex/trustedrouter-confidential-stack):
  the `trcs` shim and the `tr-attestation/v0` protocol this project builds on.
- [trustedrouter-provider-check](https://github.com/Lore-Hex/trustedrouter-provider-check):
  the conformance checker every node must pass.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Report security issues
privately as described in [SECURITY.md](SECURITY.md).
