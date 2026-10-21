# Bounded DeepSeek Functional Lab

This is an **experimental, nonqualifying** path for one approved Linux x86_64
B200 CC test host, or the explicit B300 correctness experiment below.
It is not a production profile, GCP gate completion, TDX
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

The application receipt's `before_inventory_sha256` hashes the complete
pre-application inspection report, including file inventory, console and
packaging metadata. It is not the files-only digest in the measured packaging
record. The existing application preflight enforces that measured files-only
digest before writes; the recorder retains the receipt writer's hash and the
authoritative receipt validation. A valid application receipt remains
recordable when subsequent engine startup fails. This is a software correction,
not evidence that model startup or inference succeeded.

## Startup Observations

Newly prepared bundles include a content-free `startup` record. The parent
observes a fixed outcome (`launch-failed`, `child-exited`, `startup-timeout`,
`models-rejected`, `ready`, or `supervisor-failure`), health-probe count,
startup-wait duration in milliseconds, and the child's exit code or signal
when it exits before readiness. The wait duration excludes process creation.
Exit observations are captured before cleanup; cleanup cannot overwrite them.

`ready` means that health and the exact served-model declaration passed. It
does not establish correct inference, quality, attestation or confidentiality.
`models-rejected` covers an unavailable or malformed model declaration.
SIGKILL alone does not establish an out-of-memory failure.

Engine stdout/stderr are still discarded. No raw errors, paths, prompts,
completions, request bodies or tokens are added to evidence. The recorder
rejects unknown fields, unbounded values and inconsistent startup states.
The existing runtime limits and fail-closed admission remain unchanged.
Older prepared bundles retain their original source and evidence shape.

New engine-entry checkpoint bundles pass one explicit numeric FD through
`Popen(pass_fds=...)` and the exact `engine --checkpoint-fd N` route. The child
requires a write-only, nonblocking FIFO, makes it non-inheritable, and closes
it before calling `serve.cmd`, including on earlier failures. Worker, tracker,
VecISA and other reentry routes do not accept checkpoint arguments.

Only ordered one-byte IDs are emitted: 1 source binding, 2 patched-source
recheck, 3 bootstrap preconditions, 4-7 the four pinned setuptools sources,
8 vendor root, 9 bootstrap installation, 10 bootstrap completed, 11 serve
import, 12 parser import, 13 parser construction, 14 parsing, 15 argument
validation, 16 boundary check, and 17 immediately before the serve call.
An ID marks entry to a boundary, not its successful completion or root cause.
ID 17 does not prove workers, health, inference or qualification.

The parent drains at most 18 bytes per nonblocking poll, retaining only the
last valid ID and fixed status (`not-started`, `eof`, `incomplete`, `invalid`,
`overflow`, or `read-failed`). It closes its read endpoint before engine
cleanup. EOF or a partial prefix is not success. Raw bytes, errors, engine
logs and arbitrary metadata are never copied to evidence. These diagnostics
do not authorize inference or relax any finder, admission or worker guard.

The outer recorder also identifies its fixed experiment step: `attach`,
`decode`, `terminal`, `validation`, or `completed`. Before cleanup it inspects
the owned container and retains only status, exit code and Docker's OOM flag;
unavailable or invalid observations remain null. The bounded output byte count
does not retain output content. Exit 137 alone is not an OOM claim. Unvalidated
inner results are still discarded, and diagnostic observations never authorize
a successful result.

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

### Local Correctness Smoke

For an explicitly selected B300 CC experiment, use
`--prepare-local-correctness-b300` instead. That flag binds `gpu_family:B300`
into the hashed contract and wrapper. It does not auto-detect or fall back to
another family. The default remains B200. All four selected GPUs must match
the chosen family, with protected memory of at least 120 GiB per B200 or
200 GiB per B300. Mixed/unknown devices and all existing CC, readiness and
devtools violations reject. Evidence names the observed family, never B200
for a B300 run. Diagnostic-only B300 and other families are not enabled.

The rationale is the upstream [Blackwell TP recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash#blackwell-tp-and-dep)
and [Tinfoil's B200/B300 TP4 implementation](https://github.com/tinfoilsh/confidential-deepseek-v4-1-flash),
read October 21, 2026. They document this architecture on B300; they are not
our runtime qualification. This experiment retains the same pinned vLLM
image, model, TP4, eager execution, bounds and isolation. It imports no
Tinfoil kernel preset or measured profile. Compatibility must still be tested.
CPU SEV-SNP on a rented B300 host does not fulfill the plan's Intel TDX gate.

An optional disposable public-weight staging path uses an existing `/dev/shm`
tmpfs on a large-memory test VM. Run committed `host/prepare-lab.py ram-stage
--root /dev/shm/nib-deepseek-weights` as root. It requires at least 1536 GiB
available host RAM, at least 600 GiB free in that tmpfs, no swap, and a new
private directory. It changes no mounts or system settings. Stage the pack
under that directory with the existing hash-verifying bounded stager; the
engine still receives a read-only mount and its existing 768 GiB memory cap.
The 476 GiB pack plus that cap leaves at least 292 GiB at the admission floor
before other costs. This is a capacity preflight, not a memory reservation:
stop if concurrent memory consumption invalidates the budget. The cache is
lost on VM termination or spot eviction and is not a production weight pack.

An optional bundle exercises eight fixed synthetic cases inside the same
container and engine lifecycle:

```sh
python3 -I -S host/record-deepseek-lab.py \
  --prepare-local-correctness "$PRIVATE_CORRECTNESS_BUNDLE"
```

Use the same bounded `--run` command with the complete verified weights.
The original functional and diagnostic modes remain separate. This mode
checks four exact arithmetic/order/logic/extraction answers, two exact JSON
objects, and two two-turn read-only lookup tool calls. The lookup values
are fixed test fixtures; no model-supplied command is executed. Unexpected
tool names, arguments, duplicate JSON keys, refusals, reasoning-only answers,
truncation, malformed usage and wrong answers fail.

There are at most ten requests, one at a time, no retries, temperature zero,
reasoning effort `none`, and 512 output tokens per request. Each complete HTTP
request has a 60-second deadline, including header/body reads. The phase has a
ten-minute budget with 90 seconds reserved for hardware recheck and teardown;
the existing whole-run 80-minute watchdog and external deletion deadline do
not increase. Unrun cases remain visible and cannot become a passing score.
The existing engine, device, network and memory constraints are unchanged.

Before startup, ten protocol/parser/tokenizer source files must match their
SHA-256 pins at
[vLLM revision 5df63a7](https://github.com/vllm-project/vllm/tree/5df63a7203cdb9328d824a27b682e77c7b9928be).
The immutable protocol supports `max_completion_tokens`, `reasoning_effort`,
named function tools and `tool_choice`; the DeepSeek V4.1 parser handles the
tool continuation. A source mismatch rejects before any engine process starts.
Public source review alone is not proof that an installed wheel matches it.

Results retain only case IDs, fixed outcomes, elapsed time, integer token
usage and the fixed case-set hash. They never retain prompts, answers, tool
arguments, raw exceptions or raw transport output. `functional_pass` requires
all eight cases plus normal engine stop and the existing host cleanup checks.
The enclosing result remains nonqualifying and `tau_acceptance:false`.
This is **not tau2, a quality comparison, or evidence of confidential service**.
It is implemented and tested against synthetic responses, not a claimed GPU
result. The separate [tau validation plan](../benchmarks/tau-validation.md)
still requires a matched reference and approved simulator-call budget.

### Denial-Only Readiness Diagnostic

A separately prepared diagnostic bundle uses the same pinned image, sealed
`-I -S -B` startup, bootstrap source checks, Docker constraints and owned-container
cleanup. It cannot enter weight verification, patch application or engine
execution, and never emits functional or qualification success:

```sh
python3 -I -S host/record-deepseek-lab.py \
  --prepare-hardware-diagnostic "$PRIVATE_DIAGNOSTIC_BUNDLE"
```

Run that bundle using the same `--run` invocation above, its own receipt digest,
a separate existing empty private directory for `--weights`, and a **UTC cutoff
at most five minutes away**, still before VM teardown. The existing probe child
limit remains 15 seconds; its supervisor alarm is 60 seconds. Do not use or
modify an active staging directory. The empty directory is mounted read-only
but not inspected by this mode. This is a different input contract, not an
automatic retry of a failed functional run.

The report distinguishes child completion, timeout, failed exit and malformed
output. A completed child reports invocation booleans, source-binding/bootstrap
stage (including one of four fixed setuptools source indices), and bounded NVML
observations if reached. Calls record only fixed stages, device indices 0-3,
numeric return codes or null when no return was observed. The first failure
is sticky; shutdown has its own call result and cannot overwrite it.
Count, CC settings, readiness and memory observations are numeric; names,
handles, UUIDs, exception text, paths and environment values are not recorded.
Unknown/unreached observations stay null. A failure before the isolated child
can emit its envelope is reported as a failed child, not attributed to NVML.

The exact count-four, CC, readiness and memory policies are unchanged. Even
`policy_checks_passed:true` is only the existing read-only policy observation:
the enclosing report remains `BLOCKED`, `functional_pass:false`,
`deployable:false`, `confidential:false`, and evidence `result:fail`. In
particular a count mismatch must not be interpreted as proof that the toolkit
selected the wrong devices or as permission to change access selection.
Software regressions use fake NVML calls, bootstrap failures, bounded child
failures and redaction sentinels. No actual diagnostic run is claimed here.

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
explicit selected-package-root identity, retained separately from the appended
setuptools vendor import path, can supply that source. Only an
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

The separate [CPU-only entry diagnostic](p2-t3-cpu-entry.md) reuses application,
bootstrap and the pre-serve import/parser/state-check boundary without calling
`serve.cmd`, supplying weights or GPUs, or changing the original serving route.
