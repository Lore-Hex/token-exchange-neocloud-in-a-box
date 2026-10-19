# Local Qwen pilot

This is a real local model behind the reusable `trcs` TLS front door. It is not
public, billed, attested, or confidential. See [D8](../decisions/0008-small-qwen-pilot.md).

Prerequisites: Python 3.12+, CMake, a C/C++ compiler, Go (the runner selects
1.27.2), OpenSSL and `uv`. Allow approximately 3 GB of temporary disk and a few
GB of RAM. The native build uses four CPU threads. No cloud credits are used.

Download the three public, immutable inputs into a private local directory:

```sh
mkdir -p /tmp/nib-pilot-inputs
curl --fail --location --max-time 300 \
  https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/23749fefcc72300e3a2ad315e1317431b06b590a/Qwen3-0.6B-Q8_0.gguf \
  --output /tmp/nib-pilot-inputs/model.gguf
curl --fail --location --max-time 120 \
  https://codeload.github.com/ggml-org/llama.cpp/tar.gz/620d2225cc436ab0b001e3200e590e3b286ef9ab \
  --output /tmp/nib-pilot-inputs/llama.tar.gz
curl --fail --location --max-time 120 \
  https://codeload.github.com/Lore-Hex/trustedrouter-confidential-stack/tar.gz/8a87d9765ceabe76bbfa5e56a72c91da6d8290d5 \
  --output /tmp/nib-pilot-inputs/trcs.tar.gz
```

The runner verifies the exact model bytes/SHA-256 and both archive hashes.
It reuses the reviewed front-door pin and hash-locked provider checker from the
P1 harness. It does not load a Hugging Face token, cloud account, production key,
ambient engine settings or CMake/Go build overrides. Go/Python build dependencies
may be downloaded during setup; inference uses local weights and loopback only.

From a clean committed checkout, run the evidence recorder:

```sh
uv run --project tools --frozen python -I scripts/record-qwen-pilot.py \
  --inputs /tmp/nib-pilot-inputs --slug qwen-local-1
```

Use a new slug for each run. The recorder executes a private committed-source
snapshot and saves `run.json` and sanitized `output.log` under `evidence/p3/`.
Isolated Python mode is required; helpers are loaded from source bytes, not
ignored bytecode, and the bootstrap source must match the committed snapshot.
A successful report requires all 17 applicable provider checks, real generation,
authentication checks, upstream-failure propagation and cleanup. The Linux path
also checks the persistent service's Catalog v2 using explicitly synthetic test
prices; these are not approved public route prices. It signals shutdown after
the first real content delta and requires the final finish reason, usage and
`[DONE]` before exit. The native development path does not run this persistent
service or catalog check.
The test intentionally stops both listeners when it finishes. It does not leave
an unaudited development server exposed, even on the local network.

[The 2026-10-19 recorded run](../../evidence/p3/2026-10-19-qwen-local-3/run.json)
passed these checks using real Qwen weights. Its
[sanitized results](../../evidence/p3/2026-10-19-qwen-local-3/output.log) include
24 prompt tokens and 2 completion tokens for the synthetic known-answer test.

## Offline Linux runtime

The same conformance runner can also run inside a local Linux/amd64 container:

```sh
uv run --project tools --frozen python -I scripts/record-qwen-pilot.py \
  --inputs /tmp/nib-pilot-inputs --slug qwen-linux-1 --linux-container
```

Docker with BuildKit is required. The recorder permits only a local Unix-socket
Docker endpoint and uses an empty Docker configuration, not your registry or
cloud credentials. It builds from the existing digest-pinned Go/Python bases,
verified source archives, hash-locked Python dependencies and verified Qwen
weights. The build needs public network access; the resulting test runtime does
not. Allow several GB of local disk and 3 GB of runtime RAM. Apple hosts execute
Linux/amd64 through their local Docker virtualization/emulation; this is not a
native cloud hardware benchmark.

Inference runs as a non-root user with a read-only filesystem, no capabilities,
no-new-privileges, no external network, no host mounts and no published ports.
The only writable filesystem is bounded temporary memory. The runner creates
ephemeral API/TLS keys there and exercises the exact same authentication, real
generation, streaming, usage and backend-failure checks as the native pilot.
It records the private build-context digest, resulting image ID, binary hashes
and sanitized results. Success additionally requires confirmed absence of both
the temporary container and its tagged image after cleanup.

This is a development/test image, not a reproducible measured release or a
confidential guest image. No image is pushed to a registry. Public base images
and local Docker build cache can remain; the recorder does not prune unrelated
images or caches. An approved cloud run still needs its own bounded execution,
network, billing, teardown and attestation gates.

[The final Linux run](../../evidence/p3/2026-10-19-qwen-linux-4/run.json)
passed all 17 applicable conformance checks and the real-generation, auth, usage,
backend-failure and cleanup checks. Its
[results](../../evidence/p3/2026-10-19-qwen-linux-4/output.log) record Linux/x86_64
under local Docker on the macOS host, not a cloud VM. The native path also passed
after the shared-runtime refactor ([regression evidence](../../evidence/p3/2026-10-19-qwen-native-regression-1/run.json)).

Review caught a build-timeout cleanup race before merge. The recorder now stops
and verifies the whole local tool process group, including Docker plugins. If a
build or run is interrupted before completion, it reports cleanup as unproven
even when the current image/container listings are empty: stopping the client
alone does not prove the daemon has finished. Completion requires a validated
successful build or a valid terminal runtime report with the expected exit code.
Regression tests cover a real delayed child process, signals, Docker failure
codes, invalid output and this fail-closed classification. The earlier successful
Linux runs remain as source-bound historical evidence, not timeout-path proof.

Cloud deployment is a separate operation with an exact project, approved budget,
expiry, teardown and evidence. Do not substitute TrustedRouter production when
the operator's dedicated project is unavailable. CPU confidential testing, GPU
confidential testing and commercial readiness are distinct milestones.
See the [operator guide](trustedrouter.md) for the integration boundary and
[gates](../gates.md) before infrastructure changes.

The persistent service requires the shutdown fix reviewed in
[trcs PR #6](https://github.com/Lore-Hex/trustedrouter-confidential-stack/pull/6).
Its source revision, archive hash and Linux binary hash are pinned together in
`scripts/e2e/pins.json`. The binary pin was built offline with the exact pinned
Go image and the same build flags as the proof image. See the
[service runbook](qwen-service.md) for runtime limits and activation order.
