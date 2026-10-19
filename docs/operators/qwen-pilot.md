# Local Qwen pilot

This is a real local model behind Hixtube's reusable TLS front door. It is not
public, billed, attested, or confidential. See [D8](../decisions/0008-small-qwen-pilot.md).

Prerequisites: Python 3.12+, CMake, a C/C++ compiler, Go (the runner selects
1.27.2), OpenSSL and `uv`. Allow approximately 3 GB of temporary disk and a few
GB of RAM. The native build uses four CPU threads. No cloud credits are used.

Download the three public, immutable inputs into a private local directory:

```sh
mkdir -p /tmp/hixtube-pilot-inputs
curl --fail --location --max-time 300 \
  https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/23749fefcc72300e3a2ad315e1317431b06b590a/Qwen3-0.6B-Q8_0.gguf \
  --output /tmp/hixtube-pilot-inputs/model.gguf
curl --fail --location --max-time 120 \
  https://codeload.github.com/ggml-org/llama.cpp/tar.gz/620d2225cc436ab0b001e3200e590e3b286ef9ab \
  --output /tmp/hixtube-pilot-inputs/llama.tar.gz
curl --fail --location --max-time 120 \
  https://codeload.github.com/Lore-Hex/trustedrouter-confidential-stack/tar.gz/c02fd478484f93732ba880d2d8e93aa8c5d90dbe \
  --output /tmp/hixtube-pilot-inputs/trcs.tar.gz
```

The runner verifies the exact model bytes/SHA-256 and both archive hashes.
It reuses the reviewed front-door pin and hash-locked provider checker from the
P1 harness. It does not load a Hugging Face token, cloud account, production key,
ambient engine settings or CMake/Go build overrides. Go/Python build dependencies
may be downloaded during setup; inference uses local weights and loopback only.

From a clean committed checkout, run the evidence recorder:

```sh
uv run --project tools --frozen python scripts/record-qwen-pilot.py \
  --inputs /tmp/hixtube-pilot-inputs --slug qwen-local-1
```

Use a new slug for each run. The recorder executes a private committed-source
snapshot and saves `run.json` and sanitized `output.log` under `evidence/p3/`.
A successful report requires all 17 applicable provider checks, real generation,
authentication checks, upstream-failure propagation and cleanup. The separate
public catalog declaration is not tested; it needs approved prices and listing.
The test intentionally stops both listeners when it finishes. It does not leave
an unaudited development server exposed, even on the local network.

[The 2026-10-19 recorded run](../../evidence/p3/2026-10-19-qwen-local-2/run.json)
passed these checks using real Qwen weights. Its
[sanitized results](../../evidence/p3/2026-10-19-qwen-local-2/output.log) include
24 prompt tokens and 2 completion tokens for the synthetic known-answer test.

Cloud deployment is a separate operation with an exact project, approved budget,
expiry, teardown and evidence. Do not substitute `quill-cloud-proxy` when the
dedicated Hixtube project is unavailable. CPU confidential testing, GPU
confidential testing and commercial readiness are distinct milestones.
