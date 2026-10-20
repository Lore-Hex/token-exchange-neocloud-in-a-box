# Bounded DeepSeek Functional Lab

This is an **experimental, nonqualifying** path for one approved Linux x86_64
B200 CC test host. It is not a production profile, GCP gate completion, TDX
verification, an attestation, or ABI qualification. The candidate remains
`BLOCKED`, `deployable:false`, `confidential:false`, and evidence `result:fail`
even if the separate `functional_pass` field is true. No real lab run has
been performed by this implementation.

The P0 process-denying import bootstrap and its failed historical evidence
are unchanged. This separate lab policy permits normal engine/native JIT
subprocesses inside a bounded container; it does not grant exceptions to P0.
It retains isolated/no-site/source-only Python, fixed paths and spawn-only
reentry **before preparation and unpickling**. It is not a general sandbox
against malicious native engine code.

## Prerequisites

- An individually approved host, spend limit and external VM deletion deadline.
  The recorder removes its container, **not the VM**, and never changes CC,
  driver, device-ready, billing or cloud settings.
- Linux x86_64, Python 3.12+, local Docker, NVIDIA Container Toolkit with the
  `nvidia` runtime already registered, and four selected B200s accessible as
  Docker device IDs 0,1,2,3. Installation belongs to a separate reviewed task.
- The exact already-cached image
  `vllm/vllm-openai@sha256:6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9`.
  The recorder does not pull or build images. Raw OCI manifest/config inputs
  are independently digest-bound; Docker `.Id` is not treated as a config digest.
- The complete immutable pack at revision
  `1bae0e1088993b9b82de08f59de5be5a34e42170`, 510,311,045,295 bytes.
  The bundled committed-source `nib model check-local` rehashes the entire
  exact set before application. No downloader or partial-weight fallback.
- Capacity for a 768 GiB RAM cap, private 2 GiB SHM, private 16 GiB JIT/cache
  tmpfs, private 256 MiB `/tmp`, and the image's writable ephemeral layer.
  The selected image's native/source inventory is verified; generated JIT
  libraries are **not** verified native provenance. The ephemeral image layer
  has no portable Docker disk quota; host free-space monitoring remains an
  operator responsibility.

Read-only NVML requires named enabled/production/devtools-off values, a
documented protected-PCIe or NVLE multi-GPU value, global GPUs-ready state,
and four B200s each reporting protected memory. Unknown, mixed, unavailable,
plain, devtools, or legacy-disabled/NVLE-only combinations reject. These
observations are not cryptographic device readiness or attestation.

## Preparation And Run

Prepare only from the final reviewed committed source. The host prepare step
uses existing offline Go/cache controls and snapshot source binding. It emits
a private no-overwrite portable bundle with a Linux/amd64 validator; it does
not run an image. The existing verified raw OCI files must already be in
`build/engine-image-metadata/{manifest,config}.json`.

```sh
python3 -I -S host/record-deepseek-lab.py --prepare "$PRIVATE_BUNDLE"
```

Keep the printed receipt SHA-256 as the trusted transfer digest. Transfer the
bundle privately using the separately approved host-access procedure. On the
approved Linux host, use absolute existing bundle/weights paths, a new evidence
path, and a UTC cutoff **at most 80 minutes away and before VM teardown**:

```sh
nohup python3 -I -S "$PRIVATE_BUNDLE/record-deepseek-lab.py" \
  --run "$PRIVATE_BUNDLE" --receipt-sha256 "$BUNDLE_RECEIPT_SHA256" \
  --weights "$VERIFIED_WEIGHTS" --output "$PRIVATE_EVIDENCE" \
  --deadline "$APPROVED_UTC_CUTOFF" >"$PRIVATE_PROGRESS_LOG" 2>&1 </dev/null &
```

Use a private directory and `umask 077` before this invocation. The command
emits only fixed sanitized status, not engine logs. Its final exit is nonzero
because it does not establish qualification; inspect committed
`run.json`/`output.log`, `functional_pass`, stage and cleanup separately.
Failed attempts remain evidence. Never rerun automatically.

## Fixed Experiment

The container has no external network, published ports, credentials, Docker
socket or host IPC. Capabilities are dropped, no-new-privileges is set, and
limits are 1,024 PIDs, 64 CPUs and 768 GiB RAM/swap. The RAM bound is a
conservative experimental choice for the operator-reported approximately
1.3 TiB host and 510.3 GB weights, not a measured loader-memory requirement.
Only the payload and weights
are host-mounted, read-only. NVIDIA runtime injection is explicit; no driver
or CC settings are changed. Every `NVIDIA_REQUIRE_*` value from the digest-verified
image config is preserved exactly at container creation and checked again in
container inspection. The toolkit enforces these before application `env -i`;
the lab does not disable vendor compatibility constraints.

The reviewed exact-image application contract performs all eight inventories,
all five patch stages, bytecode removal and source/native/hash receipt checks.
It retains inert `.pth` files and excludes alternate roots. Application receipt
`engine_executed:false` and `plugins_loaded:false` describe **that phase**, not
the later functional experiment. vLLM plugins and Torch backend auto-loading
remain disabled; dependency initialization may load its own reviewed entry
points. Both stdlib/backport metadata lookup are restricted to fixed paths.
The lab additionally predeclares only the selected root's `setuptools/_vendor`
path after verifying four pinned setuptools 80.10.2 source files. This makes
the upstream vendor-path addition idempotent. A source-bound adapter routes
only its temporary `distutils` alias through the existing sealed finder,
without inserting an unrestricted finder. All four adapter sources execute
from verified bytes, not cached bytecode; preloaded setuptools/distutils
modules, source/version mismatches, symlinked vendor roots, and unrelated
path/finder changes reject. This lab-only compatibility is inherited before
spawn preparation/unpickling; it does not modify the P0 bootstrap.

After rechecking hardware, the launcher starts one Python API process, one
EngineCore and TP4 workers using mp/spawn, DP1/PP1, NUMA and EP off, GPU Engram,
zero CPU/group offload, no speculation, and every existing collective/fusion
exclusion. DeepSeek sparse attention remains enabled. Limits are 4,096 context,
one sequence, 4,096 batched tokens, eager mode and 128 output tokens for one
fixed synthetic chat request at loopback. Eager mode does not mean no JIT.
The response is checked for completion shape, finish and bounded usage. The
fixed prompt asks for the single digit for 1 + 1; `functional_pass` additionally
requires nonempty final assistant content and exact trimmed content `2`.
The completion must also end with `finish_reason:"stop"`, never `"length"`.
Only `final_content_present` and `synthetic_answer_match` booleans are retained.
`generated:true` alone means token generation was observed: reasoning-only,
length-limited responses or a wrong final answer do **not** pass the functional
check. Neither prompt nor generated content is recorded. This single exact
answer is not a quality benchmark or the provider/tau acceptance suite.

`sys.executable` points at a fixed `-I -S -B` wrapper. Spawn/tracker reentry is
parse-only and fixed; fork/forkserver, NUMA executable substitution and generic
Python `-c` reject. The sole additional `-c` form is the **exact pinned Torch
VecISA dlopen template**. Its source hash and template must match; only an
anchored no-follow regular single-link `.so` beneath `/cache/inductor/` is
accepted. It is opened with a 64 MiB bound and held by descriptor across
`dlopen`, with identity rechecked. The wrapper restores the fixed measured
Torch lib directory plus the exact public image loader path. It never
executes the received script and does not guess an ISA override.

Bounds: weight verification 1,800s; shared application deadline 720s (not reset
per inventory); engine startup 1,800s; request 120s; graceful engine stop 60s.
The complete container has an 80-minute alarm and a host hard cutoff. Child
stdout/stderr collectors use the existing bounded process-group lifecycle;
engine output goes to `/dev/null`. A separate pipe-liveness watchdog removes
the owned container after parent death or deadline and retries across the
bounded create-client window. Docker daemon completion after a failed create
cannot be proven cancelled: that result stays `create-completion-unknown`,
never successful cleanup. Confirm owned-container absence independently;
the external VM deletion deadline is still mandatory.

## Source Basis

- [vLLM serve entry](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/cli/serve.py):
  one API server stays in the current process; fixed CLI parser/dispatch.
- [Engine arguments](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/engine/arg_utils.py),
  [parallel configuration](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/config/parallel.py)
  and [the existing source-indexed pack](../../models/SOURCES.md): model,
  tokenizer/parsers, sparse attention, finite scheduling and parallel flags.
- [Worker startup](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/v1/executor/multiproc_executor.py)
  and [NUMA process wrapper](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/utils/numa_utils.py):
  `configure_subprocess` and spawn reentry; NUMA is explicitly off.
- [Shared-memory broadcast](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/distributed/device_communicators/shm_broadcast.py):
  **estimate** 16 MiB x 10 plus four x 24 MiB x 10 = 1,120 MiB plus metadata;
  private 2 GiB headroom is not measured sufficiency.
- [Torch VecISA](https://github.com/pytorch/pytorch/blob/cf30153c4c131c8164ee7798e5022d810682e2cb/torch/_inductor/cpu_vec_isa.py):
  `_avx_py_load`, `_build_probe_env`, `_probe_load` (113-162), 22,427 bytes,
  SHA-256 `6cd49584e3cfdab9f0883c5a24d088f992909775f7d400ff0a4cefccbe6998a0`.
  This is a source compatibility route, not proof of actual runtime reachability.
- [Setuptools 80.10.2 initialization](https://github.com/pypa/setuptools/blob/5cf2d085186f2c8053940076db99045b826ec22a/setuptools/__init__.py)
  and [distutils shim](https://github.com/pypa/setuptools/blob/5cf2d085186f2c8053940076db99045b826ec22a/_distutils_hack/__init__.py):
  the published tag resolves to this immutable commit. Source SHA-256 pins in
  `engine_lab_bootstrap.py` also cover
  [override.py](https://github.com/pypa/setuptools/blob/5cf2d085186f2c8053940076db99045b826ec22a/_distutils_hack/override.py)
  and [the distutils package initializer](https://github.com/pypa/setuptools/blob/5cf2d085186f2c8053940076db99045b826ec22a/setuptools/_distutils/__init__.py).
  The four retrieved files total 16,433 bytes and pass compile/parse-only
  checks under Python 3.12; upstream package code was not executed by the
  author. Synthetic parent/child regressions are not real-image compatibility
  or GPU execution evidence.
- [NVIDIA Container Toolkit 1.20.1 constraints](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/1.20.1/docker-specialized.html#constraints):
  `NVIDIA_REQUIRE_*` constraints are enforced by the runtime, before application
  environment replacement; no `NVIDIA_DISABLE_REQUIRE=true` bypass is passed.
- [Pinned NVML binding](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/third_party/pynvml.py):
  structs/getters 2724-2754, 6384-6425; named constants 1167-1195.
  [NVIDIA NVML R595 reference](https://docs.nvidia.com/deploy/pdf/NVML_API_Reference_Guide.pdf),
  Confidential Computing definitions p105, documents NVLE=2.
- [DeepGEMM cache](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/utils/deep_gemm.py),
  [Triton build/cache](https://github.com/triton-lang/triton/blob/f797708c0626e5f9840ca5b0a98790e2c7cb09ad/python/triton/runtime/build.py),
  [FlashInfer JIT](https://github.com/flashinfer-ai/flashinfer/blob/946200de1ae94fc93fdd0926f0a13afd1fa7f0f1/flashinfer/jit/cpp_ext.py):
  native JIT subprocesses are expected, bounded by the container rather than
  silently allowed through the P0 process-denial policy.

Software tests use fake layouts, synthetic children and fake Docker/NVML.
They cannot prove actual EngineCore/TP4 reentry, model compatibility, GPU memory
coherence, multi-GPU collective correctness, kernel/compiler ABI or throughput.
