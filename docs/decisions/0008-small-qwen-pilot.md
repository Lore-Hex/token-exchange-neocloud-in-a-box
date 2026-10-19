# D8: A small real-model pilot before the large-model node

Status: accepted for the local pilot on 2026-10-19. Cloud deployment remains gated.

Joseph requested a smaller Qwen first, in an operator-owned project separate
from TrustedRouter. This adds P3.T7, prioritized before the GPU-dependent tasks; it
does not waive the confidential verification requirements or cloud gates.

## Decision

Run `qwen/qwen3-0.6b` locally through the same `trcs` TLS front door, using the
official Qwen Q8_0 weights and a source-pinned llama.cpp CPU server. This is an
**API-plumbing pilot**, not a quality benchmark, commercial route, or confidential
node. There is no fake engine or attestation in this run: `tee=none` is explicit.
No production TrustedRouter code, credentials, data, resources, or requests are
needed. Any cloud test must use an isolated operator-owned project.

Primary sources checked 2026-10-19:

- [Official Qwen weights and model card](https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/tree/23749fefcc72300e3a2ad315e1317431b06b590a):
  Apache-2.0, 639,446,688-byte Q8_0 file. This small checkpoint exercises real
  inference without paid hardware. The pilot advertises only 4,096 context.
- [Pinned server flags](https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/tools/server/README.md):
  localhost binding, offline operation, API-key file, disabled logging, disabled
  web UI and slots, CPU-only execution, Jinja thinking control.
- [Pinned engine build configuration](https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/CMakeLists.txt):
  disable UI/prebuilt UI, subprocess tools and optional GPU builds.

This is a narrow local-test exception to [D6](0006-inference-engine.md), not a
replacement for the GPU production engine. We do not translate a GGUF hash into
a claim about the eventual safetensors/FP8 GPU weights. That deployment needs
its own verified model pack, engine pin and hardware evidence.

## Acceptance

The committed runner verifies private copies of all input hashes, builds the
engine/front door from those archives, and checks:

- authenticated real generation and a known-answer prompt;
- missing/wrong credentials rejected;
- provider-check Tiers 1-4, including SSE framing, incremental output and usage;
- a dead inference engine produces an error, not an empty successful response;
- subprocesses and disposable key/certificate/build files removed.

Only synthetic test prompts are sent. Service output goes to `/dev/null`; the
recorded report contains fixed check IDs, statuses, counts and hashes, never
response excerpts, API keys or raw errors. Native compiler builds are not
claimed to be hermetic or reproducible measured images. The release verifier
is unchanged and must still reject unverified hardware.

The public catalog-contract check is intentionally skipped: the pilot has no
approved prices, public endpoint, production billing integration or listing.
Passing these checks does not satisfy P3's confidential exit or authorize DNS.
