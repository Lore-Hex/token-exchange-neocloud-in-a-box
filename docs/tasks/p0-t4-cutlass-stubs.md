# Exact CUTLASS AOT Stub Diagnostic

**Implemented and tested against synthetic files; not yet run against the
installed image.** This is a diagnostic classification, not an overlay admission
exception. The candidate remains `BLOCKED`, `deployable:false`, `image:null`.
No SDK/engine import, proprietary code copy, binary vendoring, package install,
image build, GPU execution, ABI or confidentiality validation is involved.

## Fixed Public Sources

The published CUTLASS 4.8.0 wheels below are the source of the four asset pins.
The already cached wheel archives were read without installation or executing
their contents. These are external distribution facts, not installed-image
measurements. PyPI metadata supplies each archive's name, size and SHA-256:

| Public wheel | Archive bytes | Archive SHA-256 |
|---|---:|---|
| [`nvidia_cutlass_dsl_libs_cu12-4.8.0-cp312-cp312-manylinux_2_28_x86_64.whl`](https://pypi.org/pypi/nvidia-cutlass-dsl-libs-cu12/4.8.0/json) | 91698149 | `2374cfdff635ceaf7e2c5bd17b4beb410830420a4fd65b12b7f24b5d5cbb9d4c` |
| [`nvidia_cutlass_dsl_libs_cu13-4.8.0-cp312-cp312-manylinux_2_28_x86_64.whl`](https://pypi.org/pypi/nvidia-cutlass-dsl-libs-cu13/4.8.0/json) | 91950801 | `463cd7f0c452ffeeecb530a15aeb1b10cbbd2c8da6c5aa2fecf65f836258a732` |

Exactly these ZIP members, also the expected selected-root-relative paths,
are eligible. This is a four-path set, not a wildcard or directory allowance:

```text
nvidia_cutlass_dsl/cu12/lib/stubs/aarch64-unknown-linux-gnu/libcute_dsl_runtime.so
nvidia_cutlass_dsl/cu12/lib/stubs/aarch64-unknown-nto-qnx8.0.0/libcute_dsl_runtime.so
nvidia_cutlass_dsl/cu13/lib/stubs/aarch64-unknown-linux-gnu/libcute_dsl_runtime.so
nvidia_cutlass_dsl/cu13/lib/stubs/aarch64-unknown-nto-qnx8.0.0/libcute_dsl_runtime.so
```

All four members are **71,320 uncompressed bytes**, SHA-256
`0e0fc2fbde506983308e2d02a9c7673f43e07b7638224cebd00ebcba1b1e5645`.
Their required header is ELF64, little-endian, `EI_VERSION=1`, `ET_DYN=3`,
`EM_AARCH64=183`, `e_version=1`. Header compatibility alone is insufficient:
the complete file must match the pinned size and hash.

[NVIDIA CUTLASS at `098de2a652cf8f00fd70b2df54051c7eccbb855a`,
`aot_config.py` lines 27-33](https://github.com/NVIDIA/cutlass/blob/098de2a652cf8f00fd70b2df54051c7eccbb855a/python/CuTeDSL/cutlass/cute/export/aot_config.py#L27-L33)
documents the `stubs/<triple>/` AOT cross-compilation link-time fallback;
the native-host default uses the library directory without that target suffix.
The cited file is NVIDIA-proprietary. Only this documented convention and
distribution hashes informed the original Apache-2.0 diagnostic code; no
implementation or binary was copied. This source context does not prove which
assets any installed loader will use or their ABI/runtime safety.

## Diagnostic Contract

The source-bound recorder enables the fixed policy only for:

- Image `vllm/vllm-openai@sha256:6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9`.
- Raw config SHA-256 `bd8a5d7a8774800c92d714c0c226b32c1863f929150f36efd610aebcd12a335d`.
- The existing exact-image/census-bound 225,000-entry
  [diagnostic budget](p0-t4-engine-image-diagnostic.md#image-specific-inventory-budget).

The recorder checks this identity before preparing the payload; the child
rechecks the contract. Policy and helper bytes are bound into the source/payload
hashes. Raw OCI linkage and daemon metadata association still precede a run.
The internal file classifier does not independently verify image origin.
There is no CLI/environment path, hash or budget override. Census mode is
unchanged and does not enable this content classification.

The existing no-follow, regular-file, single-link, identity and streaming hash
checks execute before classification. Every byte counts toward the unchanged
32 GiB aggregate and 4 GiB/file limits. Entry, depth, time and container resource
limits are unchanged. An eligible file is reported as
`published-cutlass-aot-crosscompile-stub`, with its exact path, size and SHA-256,
not as a host-native library. Other files retain the existing native checks.

All four exact assets are required for a complete inventory. A missing asset
reports `nonhost-asset-missing`; any size, header or hash mismatch at an exact
path reports `nonhost-asset-mismatch`, with no fallback to generic acceptance.
A relocated or extra foreign `.so` still rejects as `native-header`. On failure,
at most four prior classifications survive as incomplete observations; traversal
order is unspecified. RECORD/wheel checks remain unavailable until the inventory
completes. These subsequent checks cover the existing vLLM RECORD, source and
selected dependency metadata, not every installed distribution's RECORD.

Ordinary `inspect()` and `apply_overlay()` never receive this policy and still
reject the non-host assets. Root/startup-hook checks are unchanged. Classified
diagnostics always retain `overlay_admission:rejected` and recorder `result:fail`,
even if byte/RECORD checks complete. Classification does not validate native ABI,
loader behavior, dependencies as executable code, GPU memory, attestation or
confidentiality. The historical
[native-header rejection](../../evidence/p0/2026-10-19-official-vllm-bounded-integrity/run.json)
and its artifacts are unchanged; they do not claim this new policy ran.

## Software Verification

```sh
uv run --project tools --frozen python -I -m unittest discover -s scripts -p 'test_engine_image.py' -k stub -v
uv run --project tools --frozen python -I -m unittest discover -s scripts -p 'test_engine*.py'
```

Tests use original synthetic ELF-shaped files and a test-only expected digest,
not copied NVIDIA binaries. They exercise the real filesystem/streaming reader,
all four classifications, complete-file tampering, independent header/size
checks, missing assets, relocated/extra native files, links/FIFOs, report tamper,
identity binding, unchanged resource/root/hook gates and no-write generic
overlay rejection. Existing source-bound payload and historical artifact tests
remain in the combined suite.

An actual image diagnostic still requires independent review of the committed
source and separate run approval. Use the existing recorder and lifecycle;
do not rerun historical sources or claim installed-asset verification from
synthetic tests. Normalization, image builds and runtime activation are not part
of this task.
