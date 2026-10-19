# P0.T4: Blocked CC Source Candidate

This is a bounded source-preparation increment toward full DS1017, not engine
qualification or deployment. **BLOCKED** remains machine-readable in
[candidate.json](../../models/engine-patches/vllm-0.32.0-cc/candidate.json):
`deployable:false`, `image:null`. Existing packs, plain-only profiles, schemas,
release admission and phase gates are unchanged. The DeepSeek pack retains
`cpu_offload:false`. No runtime consumes this candidate directory.

## Provenance

Base: official vLLM 0.32.0,
[`5df63a7203cdb9328d824a27b682e77c7b9928be`](https://github.com/vllm-project/vllm/tree/5df63a7203cdb9328d824a27b682e77c7b9928be).
The Apache-2.0 PR59981 series has this original parent and exact chain:

1. [`ce49174247fd84c322f293a31cbe1d0460aaa7fe`](https://github.com/vllm-project/vllm/tree/ce49174247fd84c322f293a31cbe1d0460aaa7fe), original parent, not the release base.
2. [`c8309e4215fefe376a190ad68c872f832418d9f2`](https://github.com/vllm-project/vllm/commit/c8309e4215fefe376a190ad68c872f832418d9f2), V2 copy-path changes.
3. [`c16c248aa2255dc2b13f732b00b90e44bb7311e4`](https://github.com/vllm-project/vllm/commit/c16c248aa2255dc2b13f732b00b90e44bb7311e4), unconditional prep-stream ordering.

The [candidate directory](../../models/engine-patches/vllm-0.32.0-cc/README.md)
preserves original patches, authorship, SPDX headers and the upstream license.
Its NOTICE identifies local modifications. Published benchmark numbers in
upstream commit messages are external claims, not this repository's results.
No unlicensed patch implementation was inspected or copied.

The original first patch does not apply directly to the release in three files.
Our first patch rebases those hunks while retaining the release's lazy
`PIN_MEMORY`, direct pin-availability lookup, DP seed handling, `ultraquant_4bit`,
ZMQ imports/listener ownership, nonblocking fallback copies and 64-bit staged-write
indexing. Whole-module AST comparisons outside the intended additions protect
these semantics; `async_copy_to_np(copy=True)` is also retained. This is source
equivalence testing, not execution of all those release features.

The second patch is byte-identical to upstream commit 3. The third and fourth
are local Apache-licensed hardening patches. Every touched file has five SHA-256
values in the manifest: release base (null for the new helper), rebased first
patch, upstream ordering correction, strict selection/buffer ordering, and the
generic CUDA host-mapping guard. The first three patches and their stage hashes
are unchanged by the fourth patch. No intermediate stage is usable.

## Selection And Ordering

`VLLM_CONFIDENTIAL_COMPUTE` belongs to this candidate, not stock 0.32.0.
The supported explicit override is exactly the string `1`. It selects a copy
implementation without reading NVML. `0`, empty strings, whitespace, other
spellings and arbitrary values abort. It is not an attestation bypass or an
admission signal: **software selection never proves confidential hardware**.

With no override, CUDA must be available and the legacy NVML `ccFeature` field
must equal the pinned binding's named `NVML_CC_SYSTEM_FEATURE_ENABLED` constant.
The binding defines that constant in its
[immutable source](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/third_party/pynvml.py#L1194-L1195).
Missing fields, unknown types/values, legacy-disabled state and query errors all
abort. In particular, legacy zero cannot establish non-CC when `multiGpuMode`
is unqualified. No new numeric NVML enum policy is invented. Successful legacy
detection selects copies only, including where a device might be in devtools
mode; production/devtools policy and real GPU verification remain elsewhere and
unimplemented by this patch. Diagnostics contain only fixed messages, never
override values or NVML exception text. Failed detection is not cached as false.

Both `staged_h2d()` and `StagedH2DBuffer.uva()` now unconditionally order the
caller compute stream after the prep-stream copy and record the device tensor's
lifetime on that compute stream. This includes sliced buffers and an explicitly
forced path on non-CC hardware; tests do not rely on `is_pinned()`. **The caller
must still retire every GPU reader before reusing a pool slot.** A stream wait
does not establish safe slot reuse, hardware memory coherence or CUDA graph
correctness. Deferred D2H behavior remains an upstream candidate requiring real
runner/lifetime validation.

## Generic Mapping Guard

The release's
[`get_accelerator_view_from_cpu_tensor`](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/utils/torch_utils.py#L920-L946)
CUDA-alike branch directly invokes `torch.ops._C.get_cuda_view_from_cpu_tensor`.
Patch 4 checks `current_platform.is_confidential_compute()` immediately before
that call. When true, it raises only the fixed message
`CUDA host mapping is disabled by the CC candidate`. It does not inspect or log
the tensor, override values or raw detection errors. Selection failures from the
candidate's existing fail-closed detector propagate without reaching mapping.

The helper promises a view, so substituting a device copy would change its
aliasing contract. This patch rejects instead. When selection returns false,
the original raw mapping call receives the same tensor and its result is
returned unchanged. XPU and unsupported-platform branches are unchanged.
The existing strict CUDA detector does not gain a new opt-out or false-return
path: unknown hardware remains unqualified. Software selection is not hardware
attestation, and blocking this one helper is not a blanket no-UVA policy.

Focused AST tests execute only the helper and existing detector with local fake
objects. They assert no raw mapping or copy on CC rejection/detection failure,
fixed diagnostics, unchanged non-CC alias identity, and XPU behavior. The
whole-module release comparison excludes only the deliberate additions; all
existing release-semantics and stream-ordering tests still run. These checks do
not establish GPU memory coherence or coverage of other mapping/collective paths.

## Reproduction

From the repository root with Python 3.11+ and Git, on Linux/macOS, no installed
torch, vLLM, NVML Python package, container runtime or GPU is required:

```sh
python3 -I scripts/test_engine_candidate.py -v
python3 -I scripts/check-engine-candidate.py
```

The offline runner applies all patches only to a new temporary sparse source
tree, checks the exact file set/modes and every intermediate hash, parses Python
syntax, then runs the focused software tests. Tests execute only selected,
reviewed helper AST functions with fake torch/NVML objects and restricted
imports. They do not import or execute the complete upstream package. Standard
`make check` discovers these tests without adding network access to CI.

After committing the runner, tests and candidate inputs, the checkout may
produce a source-bound acceptance record:

```sh
python3 -I scripts/check-engine-candidate.py --record --fetch
go run ./cmd/nib lint
```

The recorder reads regular blobs directly from the pinned local Git commit,
not the working tree, an archive with attributes, ignored bytecode or imports
from ambient paths. Git configuration/environment overrides and replacement
objects are disabled. Executing bootstrap bytes must match the committed
recorder. A deterministic length-framed bundle digest binds the runner, tests
and candidate inputs; this is a sparse input digest, not a whole-repository
archive digest or attestation of the compiler/OS. Installed Python, Git, OS and
the local trusted checkout remain trusted. Stop other writers during recording.
Uncommitted candidate/test edits are ignored in recording mode; commit intended
changes first. There is no `git status`/checkout/filter execution in the recorder.

`--fetch` makes exactly eleven fixed anonymous HTTPS GETs: ten release source
files and LICENSE at the immutable base on `raw.githubusercontent.com`. No
credentials, cookies, environment proxy, redirects, retries, package downloads,
Git history, image layers or weights are used. Each body is limited to its exact
pinned byte length plus one overflow byte; all bytes must equal the committed
fixture. Reads have a 20-second socket timeout, a 180-second fetch deadline and
a process-wide 300-second check deadline, including slow response headers.
The original patch provenance remains committed input, not a claim that this
fetch rechecks current PR status or parent relationships.

Records are created exclusively under `evidence/p0/<UTC>-vllm-cc-candidate/`;
existing records cannot be overwritten. Success or failure has `execution:
software`, `hardware_validated:false` and candidate status BLOCKED. `output.log`
contains structured hashes/counts and fixed failure codes, not upstream bodies,
headers or raw tool/exception output. A failed verification run is retained.
Preflight/bootstrap failures occur before evidence creation and cannot claim
success. `nib lint` remains the authoritative evidence-schema check.

## Recorded Result

The original three-stage [committed-source run](../../evidence/p0/2026-10-19T190821Z-vllm-cc-candidate/run.json)
passed from `9403b662a6e6da103e3e3d5bf64dace3f95600b9`. Its
[structured output](../../evidence/p0/2026-10-19T190821Z-vllm-cc-candidate/output.log)
records 20 focused software tests, exact hashes at all patch stages, and eleven
public source comparisons totaling 340,003 response bytes. Input bundle SHA-256:
`bd02bc8d1c6b7a54d7d6e0313b91de52a51b78f8f2e2fdfc915bddab394e0e94`.
The existing `nib lint` accepted the evidence record. Candidate status remains
BLOCKED, `deployable:false`, `hardware_validated:false`. This is not a GPU or
full-model run. Full repository checks and independent final-head review are
separate requirements; this source preparation does not satisfy hardware gates.
That historical run predates the fourth-stage guard and does not prove it.

The four-stage [sink-guard run](../../evidence/p0/2026-10-19T195056Z-vllm-cc-candidate/run.json)
passed from `8a742261a7143e9ecb1910c0173bbb84f184d966`. Its
[structured output](../../evidence/p0/2026-10-19T195056Z-vllm-cc-candidate/output.log)
records 24 focused software tests and all five file hashes (base plus four
stages). Input bundle SHA-256:
`da977f3dd6c9b27430b43a15b9d555b2cb5b2049777879aa8bbfc0fb28103a55`.
This run used `python3 -I scripts/check-engine-candidate.py --record` without
`--fetch`: it verified the committed, unchanged upstream fixtures offline,
not fresh public responses. The earlier public-source comparisons remain
historical evidence. No GPU package import, image build, hardware run or runtime
activation occurred; status remains BLOCKED and `cpu_offload:false` is unchanged.

## Remaining Gates

- This is not a blanket no-UVA guarantee. The fourth patch guards one generic
  CUDA mapping helper only; other raw mapping/collective paths need their own
  source audit and policy. Disabling a UVA capability query or rejecting this
  helper also does not prohibit explicit-copy weight offload.
- Multi-GPU settings, device inventory/readiness, production versus devtools,
  CPU/GPU evidence and topology qualification remain blocked. No interpretation
  of `multiGpuMode` is added here.
- DS V2 runner coverage, mHC/NCCL symmetric-memory multicast and GEMM-RS paths
  need a source audit and real testing for the exact intended topology. See
  [DS collectives](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/models/deepseek_v41/nvidia/ops/mhc.py).
- [Engram](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/models/deepseek_v41/common/engram.py#L981)
  with CPU offload requires UVA and raises when unavailable; the patch does not
  relocate tables automatically. Keep `cpu_offload:false`. `EngramPeerExchange`,
  offload variants and GPU capacity/fit remain unqualified, not enabled.
- Release requirements are unchanged: Python `>=3.11,<3.15`, Torch `2.13.0`
  and the pinned release's CUDA/kernel package set. NVML uses vLLM's vendored
  binding, not a new pip dependency. See
  [pyproject](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/pyproject.toml)
  and [CUDA requirements](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/requirements/cuda.txt).
  No installation, resolver, image build, native ABI or dependency compatibility
  test is claimed.
- No GPU server has been admitted or exercised by this increment. Accounts,
  listings and available payment methods do not prove TDX/CC capability or
  authorize spend. Hardware, measured images, licensing, reproducibility,
  full-model correctness/conformance and all existing [gates](../gates.md) remain.
