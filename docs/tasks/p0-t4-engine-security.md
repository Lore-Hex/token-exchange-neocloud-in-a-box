# P0.T4: Full-Model Engine Security Update

This advances the full DeepSeek V4.1 Flash 1017 model pack, not the Qwen pilot.
It also updates GLM's shared engine pin. Both remain **draft, plain-only and
not deployable**. There is no GPU, inference, image-build or confidential
hardware acceptance result in this task.

## Change

The prior model packs selected vLLM 0.31.0 through a historical Tinfoil build
reference, despite a documented security-update blocker. The new selection is
the official vLLM 0.32.0 release, revision
`5df63a7203cdb9328d824a27b682e77c7b9928be`. Sources and per-setting references
are in [model sources](../../models/SOURCES.md).

The registry index is
`sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34`.
The packs instead pin its exact Linux/amd64 manifest:
`sha256:6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9`.
This avoids selecting a different platform implicitly on the operator's Mac.
The configuration descriptor is
`sha256:bd8a5d7a8774800c92d714c0c226b32c1863f929150f36efd610aebcd12a335d`.

The committed checker verifies:

- the official release tag still identifies the reviewed source revision;
- fixed, bounded source reads contain the reviewed argument/configuration
  declarations; this is not execution of vLLM's argument parser;
- the selected manifest and configuration bytes match their SHA-256 pins;
- the configuration declares Linux/amd64, the reviewed source/build revision,
  the upstream source URL and the exact `vllm serve` entrypoint;
- both all-file weight manifests still match the pinned HF revisions.

These image fields are self-declared metadata. Their consistency is useful but
does not establish a signed/reproducible build, the installed source, image
vulnerability status or GPU compatibility. No layers are fetched or executed.
The generic reference-image checker retains its previous behavior; stricter
engine checks reject indexes. One Docker-documented CDN host is allowed only
for the exact config digest path, with no authorization/cookies sent, a bounded
opaque query, no further redirects, and the same byte/hash limits.

Both profiles explicitly disable upstream usage tracking in addition to the
existing request/output/access logging settings. Tests preserve the plain-only
profile, loopback binding, local model path, one-million-token setting,
GPU-resident DeepSeek Engram, and empty verity root that prevents deployment.

## Checks

```sh
make check
make test-e2e
go run ./models/packcheck check
```

The last command requires a clean committed checkout and reads public metadata
only. Its immutable run record belongs under `evidence/p0/`; prior failed and
successful runs are never replaced. It does not download 510 GB of weights,
start a cloud VM, execute a GPU kernel or claim confidential success.

The [live check](../../evidence/p0/2026-10-19T103148Z-model-pack-check/run.json)
passed from committed source `c6f5b54`. Its
[report](../../evidence/p0/2026-10-19T103148Z-model-pack-check/report.json)
records all seven source hashes, the image/configuration metadata proof and both
complete model manifests. Registry/config/auth responses totaled 59,187 bytes;
no container layers were downloaded. The exact selected DeepSeek manifest still
covers 83 files and 510,311,045,295 bytes. This remains metadata-only evidence.

## Remaining Full DeepSeek Work

1. Obtain the actual server details and bounded run authorization. Joseph's
   HydraHost outreach is still pending; no server access was supplied here.
2. Run P2's full pinned-weight download, engine startup, provider conformance,
   correctness and benchmarks on suitable GPUs in an isolated operator project
   or approved server. Metadata hashes alone are not downloaded-weight checks.
3. Qualify a licensed CC-compatible engine. Upstream 0.32.0 does not implement
   `VLLM_DISABLE_UVA`; disabling Engram CPU offload does not disable all UVA
   paths. No unlicensed Tinfoil patches are copied by this task.
4. Complete the plan's measured guest, verity volumes, fresh CPU/GPU appraisal,
   negative tests, reproducibility, soak and gateway verification before any
   confidential listing. The phase dependencies and [gates](../gates.md) stand.

An upgraded dependency removes one known blocker. It does not turn a source
configuration into a running full-model service.
