# Bounded Engine Import Smoke

**Experimental software only.** This recorder can apply the five unchanged
candidate patches inside one disposable container and attempt imports of four pinned
API/engine modules. It does not start an API server, construct a model or TP
worker, load weights, expose ports, allocate a GPU, or enable a production
profile. Imports execute engine/dependency/native initialization code. They are
not a Python sandbox, ABI proof, hardware qualification or confidential service.
The candidate remains `BLOCKED`, `deployable:false`, `image:null`.

Implementation tests use fake wheel trees, fake Docker lifecycle responses and
real local stdlib processes. The first actual bounded run applied all five
stages, then **rejected the first engine import** in each observed process. It
did not qualify a working engine. Earlier read-only collections are not
application or real engine import evidence; the actual result is recorded below.

## Recorded Attempt

The unchanged [run metadata](../../evidence/p0/2026-10-20-official-vllm-import-smoke/run.json)
and [sanitized output](../../evidence/p0/2026-10-20-official-vllm-import-smoke/output.log)
record source `4b6b90613cfcd806ea0d40bf0b1aa7a415880313`, from
2026-10-20 04:03:31 through 04:11:55 UTC. Payload SHA-256 is
`069c23588dc7e2381dd14ef6fc4a41561bed2cfbcaa7e45424779afe56734938`.

- Application completed all five patch stages, removed the 2,879 planned
  source-backed bytecode files, and retained the native inventory unchanged.
  This is exact-image experimental application evidence, not generic admission.
- Observed phase durations were 8,342 ms preflight, 482,225 ms application and
  4,611 ms imports. The shared application budget was not exhausted.
- Fresh, spawn and grandchild processes each rejected
  `vllm.entrypoints.launchers.api_server.entry` with fixed category `guard` and
  state-guard count 2; other guard counts were zero. The remaining three modules
  were **not run**, not independently failed. These counters do not identify
  the changed state field or its source; the root cause is unresolved here.
- Pre-unpickle/bootstrap observations passed, the owned tracker exited zero,
  and no permitted dependency entrypoint loads were recorded. The application
  receipt's `engine_executed:false` and `plugins_loaded:false` are pre-import
  scope only; the subsequent rejected import executed initialization code.
- Cleanup is `confirmed`; the parent also reported an independent empty Docker
  label query. The artifact does not contain that external query transcript.

The recorder's outer stage is `smoke-rejected`, `smoke_pass:false`, and
`result:fail`. The inner `stage:complete` means collection completed, not that
imports passed. No GPU, weights, inference, ABI, hardware or confidentiality
qualification occurred. Do not repeat this unchanged run or weaken a guard;
any source-backed correction belongs to a separate reviewed change.

### NumPy Startup Attempt

The subsequent unchanged [run metadata](../../evidence/p0/2026-10-20-official-vllm-numpy-startup-smoke/run.json)
and [sanitized output](../../evidence/p0/2026-10-20-official-vllm-numpy-startup-smoke/output.log)
record source `1c8c6a5edac427bfe4724159090cd461b4bab94e`, from
2026-10-20 04:47:36 through 04:56:00 UTC. Payload SHA-256 is
`4c14aaebaa180952d896dd5f990d1d0c225800650a0c925e564905cd106ed664`.

All five application stages completed again with native inventory unchanged
and the same 2,879 bytecode removals. Phase durations were 8,142 ms preflight,
480,489 ms application and 6,149 ms imports. Each fresh/spawn/grandchild process
then rejected the same first API-entry import with category `guard`: process
count 1, all other guard counts zero, `first_state_mismatch:null`, and zero
permitted dependency entrypoint loads. The other three imports were not run.
Bootstrap/pre-unpickle observations and tracker exit passed. Cleanup is
`confirmed`; the parent also reported an independent empty label query, whose
transcript is not part of the artifact.

This is an observed change from state rejection to process rejection, **not
successful engine initialization or proof of the original first failure's
cause**. The fixed counters do not identify the rejected executable, command or
audit event. The exact source-backed process path must be established before a
separate correction; no generic process exemption or unchanged repeat run is
authorized by this evidence. The outer stage remains `smoke-rejected`,
`smoke_pass:false`, `result:fail`, `deployable:false`. No GPU, weights, model
inference, ABI, hardware or confidentiality qualification occurred. The first
failed-run artifacts remain unchanged.

### Denied-Process Attempt

The unchanged [run metadata](../../evidence/p0/2026-10-20-official-vllm-denied-process-smoke/run.json)
and [sanitized output](../../evidence/p0/2026-10-20-official-vllm-denied-process-smoke/output.log)
record source `b72f7a0a62ea0e4cf9540fa0ef832dcc6c69ace3`, from
2026-10-20 05:27:39 through 05:36:04 UTC. Payload SHA-256 is
`09e233ab1fb334849456967332ab58fcf9fb90ea00667b2f3029a4e5502844b3`.

All five application stages completed, with native inventory unchanged and
2,879 bytecode removals. Phase durations were 8,146 ms preflight, 482,321 ms
application and 6,036 ms imports. Fresh, spawn and grandchild each rejected the
first API-entry import with `first_denied_process` event `subprocess.Popen`,
command `other` and caller `other`. Each recorded process count 1, all other
guard counts zero, `first_state_mismatch:null`, and no permitted dependency
entrypoint loads. The remaining three imports were not run. Bootstrap and
pre-unpickle observations passed; the owned tracker exited zero.

This identifies a denied Popen event, **not the executable, command, caller or
root cause**. Both `other` categories are bounded classifier results, not
complete attribution or justification for a compatibility exception. The
recorder reports `cleanup:confirmed`; the parent independently reported an
empty Docker label query, whose transcript is not in the artifact. The result
remains `smoke-rejected`, `smoke_pass:false`, `result:fail`, `deployable:false`.
No GPU, weights, model inference, ABI, hardware or confidentiality qualification
occurred. Both prior failed-run artifacts remain unchanged. Any next source
change belongs to a separate reviewed task; do not repeat this unchanged run.

### Denied-Process Diagnostics

The recorder diagnostics were exercised in the attempt above, which remained
non-qualifying. The change preserves every existing process denial and the
exact permitted spawn/resource-tracker/fresh reentry paths. It adds `first_denied_process` to
each fresh/spawn/grandchild report: `null` before any process rejection,
otherwise a sticky object containing only fixed `event`, `command` and `caller`
categories. Caught denials still increment the process counter and prevent
success; later events cannot overwrite the first observation. Host validation
requires the field and consistency with that counter. Historical evidence is
validated using its original committed producer/validator, not rewritten to
contain new observations.

- Event classes are `subprocess.Popen`, `multiprocessing.spawnv_passfds`,
  `os.exec`, `os.posix_spawn`, `os.system`, `os.fork` and `os.forkpty`.
- Command classes are `uname-processor`, `gxx-version`, `ptxas-version` and
  `other`. Known classes require exactly two arguments and executable equality
  with argv[0]: `uname -p`, `g++ --version`, or `--version` for exactly
  `/usr/local/lib/python3.12/dist-packages/triton/backends/nvidia/bin/ptxas`
  or its `ptxas-blackwell` sibling. No basename/path normalization, shell
  interpretation or private-path matching occurs. Extra arguments, alternate
  executables, malformed/non-plain values and near misses yield `other`.
- Caller categories are `stdlib-platform`, `torch-cpp-builder`,
  `triton-nvidia-compiler`, `triton-knobs` and `other`. At most 32 frames are
  examined for the nearest exact public module name in the source table below.
  This is an untrusted module-name hint, not source identity, complete stack
  attribution or authorization. Unknown/deeper frames remain `other`.
- Reports contain no raw executable, argv, environment, cwd, arbitrary module
  name, frame path, locals, traceback or exception text. Only bounded plain
  string/byte arguments are compared; arbitrary object conversion is not used.

These upstream command forms are **candidates**, not proof of the first
process rejection in the NumPy startup attempt. Synthetic tests show they can
produce the same old process-counter-only result. No actual executable identity,
compiler behavior, JIT safety or engine compatibility is established, and no
subprocess is newly permitted. Any subsequent actual changed-source run remains
parent-owned and gated on independent review and tests.

Primary source observations (read without executing upstream packages):

| Exact public module | Pinned source and observation |
|---|---|
| `platform` | [CPython 3.12.3 platform.py](https://github.com/python/cpython/blob/v3.12.3/Lib/platform.py): processor fallback invokes `uname -p`. |
| `torch._inductor.cpp_builder` | [PyTorch cf30153 cpp_builder.py](https://github.com/pytorch/pytorch/blob/cf30153c4c131c8164ee7798e5022d810682e2cb/torch/_inductor/cpp_builder.py): compiler search probes the selected compiler with `--version`; this classifier recognizes only literal `g++`, not arbitrary configured compilers. |
| `triton.backends.nvidia.compiler` | [Triton f797708 compiler.py](https://github.com/triton-lang/triton/blob/f797708c0626e5f9840ca5b0a98790e2c7cb09ad/third_party/nvidia/backend/compiler.py): version probing uses the selected PTXAS path. |
| `triton.knobs` | [Triton f797708 knobs.py](https://github.com/triton-lang/triton/blob/f797708c0626e5f9840ca5b0a98790e2c7cb09ad/python/triton/knobs.py): version probe, default `backends/nvidia/bin` location, and tool declarations naming both PTXAS variants. Absolute diagnostic paths combine that source layout with the already measured selected root; they are not independent installed-tool verification. |

[CPython's Popen audit call](https://github.com/python/cpython/blob/v3.12.3/Lib/subprocess.py)
precedes child execution. The existing guard observes it without permitting
external tools. The separate
[spawnv_passfds path](https://github.com/python/cpython/blob/v3.12.3/Lib/multiprocessing/util.py)
calls the low-level process helper; the existing route rejection is retained
and now classified separately. No later vLLM finder adapter is added, and
Inductor-lowering reachability is still not established by these observations.

## Exact Inputs

The [application contract](p0-t4-engine-application-contract.md) and its unchanged
[measured layout](../../evidence/p0/2026-10-20-official-vllm-python-chain/run.json)
remain the gates. The recorder reuses their source-pinned helpers, complete
measured inventory, bytecode-removal plan, fixed entrypoints, dependency/source
checks and all five patch-stage receipts. It does not add a generic admission
exception, normalize the image, change native libraries, execute `.pth` hooks,
or delete alternate roots. Application is pre-import; the later smoke permits
only the exact dependency context entrypoint described below.

- Base manifest: `6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9`.
- Raw config: `bd8a5d7a8774800c92d714c0c226b32c1863f929150f36efd610aebcd12a335d`.
- Upstream vLLM: `5df63a7203cdb9328d824a27b682e77c7b9928be`.
- Measured CPython terminal: `/usr/bin/python3.12`, version 3.12.3, SHA-256
  `e50d468e8b0adfb05733f5b87b3cff34829c4a8c1aea50c865aa8bdfe4bb150f`.
- Ordered roots: `/usr/lib/python3.12`, `/usr/lib/python3.12/lib-dynload`,
  `/usr/local/lib/python3.12/dist-packages/nvidia_cutlass_dsl/dsl_packages`,
  `/usr/local/lib/python3.12/dist-packages`.

The same raw OCI manifest/config linkage, platform and ordered Docker DiffIDs
must match before container creation. Docker `.Id` is not treated as the raw
config digest; local layers/config are not independently rehashed. Docker, the
OS/interpreter and this immutable public image remain trusted inputs.

## Restricted Process Contract

The source-concatenated launcher starts with `-I -S -B`, verifies its payload,
fixed interpreter and final patched source hashes, then installs the fixed
paths **before** multiprocessing preparation or unpickling. It accepts only
the pinned CPython spawn/resource-tracker command shapes, never evaluates the
received `-c` text, and rejects other launch forms. Spawn preparation must retain
the exact paths, own main file, argv, cwd and `spawn` method. The ordinary Python
entrypoint, fork/forkserver, NUMA executable replacement, external shells,
`site.main`, `addsitedir`, startup customization imports and changed paths/env
are rejected. Caught guard errors still prevent a passing import result.
Distribution metadata discovery supports both stdlib and pinned backport APIs
with a copied, validated subset of the sealed roots. It preserves version and
entrypoint enumeration; outside-root, alias and malformed contexts reject with
fixed diagnostics. Neither unrestricted `PathFinder` nor an additional metadata
finder is installed. Entry loading has the separate exact restriction below.

Primary source for these boundaries:

- [CPython 3.12.3 spawn](https://github.com/python/cpython/blob/v3.12.3/Lib/multiprocessing/spawn.py):
  `get_command_line`, `get_preparation_data`, `_main`, `prepare`; preparation is
  unpickled first and can otherwise restore paths or run a different main file.
- [CPython process spawning](https://github.com/python/cpython/blob/v3.12.3/Lib/multiprocessing/popen_spawn_posix.py)
  and [resource tracker](https://github.com/python/cpython/blob/v3.12.3/Lib/multiprocessing/resource_tracker.py).
  Its `_stop` discards `waitpid` status; this smoke instead checks a bounded,
  owned tracker PID exit and rejects nonzero exit, restart or unknown ownership.
- [CPython 3.12.3 distribution discovery](https://github.com/python/cpython/blob/v3.12.3/Lib/importlib/metadata/__init__.py):
  `Distribution._discover_resolvers`, `DistributionFinder.Context` and
  `MetadataPathFinder.find_distributions`; replacing the path finder without
  `find_distributions` would silently hide installed package metadata.
- [Pinned worker creation](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/v1/executor/multiproc_executor.py):
  context Process followed by `configure_subprocess` and `proc.start`.
- [Pinned NUMA wrapper](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/utils/numa_utils.py):
  enabled NUMA changes the multiprocessing executable. This experiment rejects
  that path, rather than silently losing its wrapper.
- [Pinned API entry](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/launchers/api_server/entry.py):
  the forkserver branch preloads `vllm.v1.engine.async_llm`. It is unsupported,
  not qualified by a stdlib-only preload test.

The import list is fixed: `vllm.entrypoints.launchers.api_server.entry`,
`vllm.entrypoints.openai.api_server`, `vllm.v1.engine.async_llm`, and
`vllm.v1.executor.multiproc_executor`. Their source files are already among the
14 verified worker-chain pins. The smoke observes imports in a spawn child,
grandchild and fresh reentry, plus the resource-tracker bootstrap. It does not
invoke the real API-to-engine-to-TP worker chain. A later real worker run must
still qualify that behavior; this wrapper is not a general engine launcher.

The immutable environment reuses the candidate's all-off collective and
telemetry controls, `VLLM_PLUGINS=''`, and `VLLM_WORKER_MULTIPROC_METHOD=spawn`.
The [pinned plugin loader](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/plugins/__init__.py)
applies this allowlist across its plugin groups; the exact known entrypoint
names cannot match the empty string. This does not disable dependency
entrypoints: OpenTelemetry context initialization loads its reviewed context
provider during engine imports. The harness constrains that load separately.
No user args/env/paths are accepted. TP4, EP off, Engram `cpu_offload:false`,
zero explicit weight offload and fusion-disabled controls remain in the
unchanged non-executable launch plan; no engine configuration is instantiated
here. Software CC selection is not attestation. Unsupported upstream imports
or environment/process changes fail this experiment, not silently relax it.

### Import Environment Sources

The sealed environment also preconfigures the exact four values used by
[vLLM env_override](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/env_override.py#L164):
`PYTORCH_NVML_BASED_CUDA_CHECK=1`, `TORCHINDUCTOR_COMPILE_THREADS=1`,
`TRITON_CACHE_AUTOTUNING=1`, `TILELANG_CLEANUP_TEMP_FILES=1`.
[vLLM initialization](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/__init__.py#L14)
imports that module early. Preconfiguration makes those assignments idempotent;
the exact-environment guard still rejects any changed value or new variable.

`TORCH_DEVICE_BACKEND_AUTOLOAD=0` disables PyTorch's separate out-of-tree backend
entrypoint autoload, which defaults on in
[PyTorch initialization](https://github.com/pytorch/pytorch/blob/cf30153c4c131c8164ee7798e5022d810682e2cb/torch/__init__.py#L3040).
`VLLM_PLUGINS` alone does not control that loader. The single compile thread also
makes [AsyncCompile.warm_pool](https://github.com/pytorch/pytorch/blob/cf30153c4c131c8164ee7798e5022d810682e2cb/torch/_inductor/async_compile.py#L341)
return before pool setup. No compiler-pool/process exception is added, and no
conditional CUPTI variables are guessed or enabled.

Public source observations, retrieved through GitHub's contents API and checked
against its Git blob IDs, are bound into `smoke.json` as
`import_environment_sources`. PyTorch v2.13.0 resolved to immutable commit
`cf30153c4c131c8164ee7798e5022d810682e2cb`; vLLM uses the commit above.

| Upstream File | Bytes | SHA-256 |
| --- | ---: | --- |
| `vllm/__init__.py` | 3662 | `087e15875e49ba50f4969c2229d450bb8397302ea9344b3a3bd06ee400263ebd` |
| `vllm/env_override.py` | 37970 | `8a22650f862565a9ce2ba4441d39a99afbcecc2b60664be2272dd18e2df1cd1d` |
| `torch/__init__.py` | 111841 | `cf40c075c95864036e835795756d69b8cccfafa76f3bcde5eba9d06065ccd3d1` |
| `torch/_inductor/async_compile.py` | 34432 | `401933b518592b8958bb137ddd963c8dd623f4c63b9de215c933424ccc296c7d` |

These are upstream observations, not new measurements of installed files or
proof that all imports succeed. The existing full measured inventory, installed
dependency pins and source checks remain mandatory. Local fake imports exercise
these environment actions without importing upstream engine/dependency code.

### NumPy Startup Compatibility

The [historical installed-package observation](../../evidence/p0/2026-10-19-official-vllm-record-diagnosis/output.log)
reports NumPy 2.2.6. Its immutable
[core initialization source](https://github.com/numpy/numpy/blob/2b686f659642080e2fc708719385de6e8be0955f/numpy/_core/__init__.py#L16-L52)
adds `OPENBLAS_MAIN_FREE=1` and `GOTOBLAS_MAIN_FREE=1` only when absent, performs
a nested import, and removes only its additions in `finally`. A guard invoked
during that import rejects the transient environment change, even if the
variables are restored afterward. Both values are now preconfigured to exactly
`1` in the sealed environment; no mutation exemption or extra finder is allowed.

The public source was retrieved through GitHub's contents API with the Git blob
recomputed and verified. Its observation is included in the payload's
`import_environment_sources` alongside the vLLM/PyTorch observations:

| NumPy Revision | Path | Bytes | Git Blob | SHA-256 |
| --- | --- | ---: | --- | --- |
| `2b686f659642080e2fc708719385de6e8be0955f` | `numpy/_core/__init__.py` | 5612 | `4b90877138a3ae5e06f35eb668200931073f394b` | `1fde7ecd24f4087ea99e739b8d75175e23e0b6e6fd337e4804669813eab8c2b5` |

Original synthetic tests exercise the conditional changes and nested import in
parent, spawn, grandchild and fresh processes. Both absent and either one absent
reject; both preconfigured pass with zero guard counters. Restoration does not
erase the rejection. Changed values, unknown variables and extra finders still
reject. No NumPy/engine/GPU code runs in these tests. This proves a software
compatibility issue, **not the first cause of the recorded two state rejections**.
The prior artifact is unchanged. The subsequent actual run above records no
state mismatch before a process-guard rejection, not a passing smoke. No later
vLLM finder adapter is introduced without reachability evidence.

Later smoke rows also report `first_state_mismatch`, initially `null`, then one
fixed category: `paths`, `argv`, `executable`, `env`, `cwd`, `method`, `pycache`,
or `finders`. This list is the original short-circuit check order, not a causal
ranking. The first observed mismatch stays recorded after restoration or later
failures; state counters and fail-closed checks are unchanged. No variable name,
argument, path, finder identity, value or raw exception is published. Host
validation rejects unknown categories and inconsistent category/counter pairs.
Historical reports use their historical validator and are not rewritten.

### Metadata And Dependency Context

The observed image contains `importlib_metadata==8.7.1` and
`opentelemetry-api==1.40.0`. Pinned OpenTelemetry
[uses the backport API](https://github.com/open-telemetry/opentelemetry-python/blob/dd2202172982d1b3cc896ddecc2bef27d7956acb/opentelemetry-api/src/opentelemetry/util/_importlib_metadata.py).
The backport has its own
[Context and finder](https://github.com/python/importlib_metadata/blob/84e9028d39062af975d0659c0e987c28bcc808a5/importlib_metadata/__init__.py),
and its [registration decorator](https://github.com/python/importlib_metadata/blob/84e9028d39062af975d0659c0e987c28bcc808a5/importlib_metadata/_compat.py)
normally appends a finder and disables stdlib discovery. Only the two exact
hash-verified backport sources receive an in-memory loader adapter: registration
retains the single sealed finder, accepts the two exact Context types, and
delegates each query to its own metadata implementation after root validation.
An unexpected finder, source, version or registration attempt rejects. This is
not a generic finder exemption or a change to installed source/RECORD bytes.

The exact [upstream entrypoint map](https://github.com/open-telemetry/opentelemetry-python/blob/dd2202172982d1b3cc896ddecc2bef27d7956acb/opentelemetry-api/pyproject.toml)
permits only group `opentelemetry_context`, name `contextvars_context`, target
`opentelemetry.context.contextvars_context:ContextVarsRuntimeContext`, owned by
the selected-root `opentelemetry-api==1.40.0` distribution. Both APIs reject other
entry loads before importing their target. The permitted target source is
rehashed before each load, and its exact selected-root loader compiles those
verified bytes rather than accepting cached bytecode. Preloaded, injected or
replaced target modules, changed target attributes and alternate-root shadows
reject; `-B` alone would not prevent reading a timestamp-valid `.pyc`.
`OTEL_PYTHON_CONTEXT=contextvars_context` fixes the
[context choice](https://github.com/open-telemetry/opentelemetry-python/blob/dd2202172982d1b3cc896ddecc2bef27d7956acb/opentelemetry-api/src/opentelemetry/context/__init__.py);
`OTEL_PROPAGATORS=none` disables the otherwise default tracecontext/baggage
[propagator loads](https://github.com/open-telemetry/opentelemetry-python/blob/dd2202172982d1b3cc896ddecc2bef27d7956acb/opentelemetry-api/src/opentelemetry/propagate/__init__.py).
The context provider uses stdlib `ContextVar`; it is not an exporter. This scope
does not claim that arbitrary Python/native dependencies are sandboxed.

These immutable public source hashes are bound into `smoke.json` and checked
against installed bytes before the adapter or permitted entrypoint is used:

| Source (Revisions Above) | SHA-256 |
| --- | --- |
| `importlib_metadata/__init__.py` | `bbb130e3e524a73358fa46ba93e591903850652d5a9242cc7d6b367849d4986a` |
| `importlib_metadata/_compat.py` | `542e590cb953f8172c85ab824a174526f30d2e7b4925f67334ad667866be7a7c` |
| `opentelemetry/context/contextvars_context.py` | `6074f62ec7694e56c9d1fdbfc5e927805e24d59796f94fbfe27ccf91c67fa41d` |

`application.packaging.plugins_loaded:false` describes **only the pre-import
application phase**. It is not a claim about the overall experiment. Later
process reports count successful permitted loads in `dependency_entrypoint_loads`;
zero does not imply that initialization was reached. Historical application
receipts are unchanged. Tests use an original synthetic backport/context model
and real local parent/spawn/grandchild/fresh processes, including an ordinary
loader positive control for forged timestamp-valid bytecode, not installed upstream
engine/dependency imports or evidence that the real image smoke succeeds.

## Bounds And Failure

There is no image pull/build/commit/publication. The already-cached exact base
is used in one owned container: network none, no devices/GPUs/ports/secrets,
all capabilities dropped, no-new-privileges, private IPC (16 MiB), 128 PIDs,
8 GiB memory/no extra swap, two CPUs, 512 MiB noexec tmpfs and a 1080-second
outer deadline. The read-only source payload is the only host mount. Root is
used to patch the ephemeral container layer; this layer is not a read-only
filesystem or an adversarial-native-code sandbox. No independent disk quota
for that layer is claimed. Home/cache scratch is directed to the bounded tmpfs.

The generic inspector retains its 300-second deadline. Only this exact-image
application gets **one shared 720-second deadline**, including its layout
preflight and eight full inventory passes: initial inspection, before apply,
after deletion, and five after-stage checks. No stage renews it. The measured
inventory has 19,176,556,448 logical bytes; eight passes read approximately
153.4 GB before small metadata overhead. The budget is a finite operational
allowance, **not a measured application throughput guarantee**. The planning
**estimate** is eight times the historical 84-second read-only collection:
`8 * 84 = 672` seconds, leaving 48 seconds in the application budget. That
collection was not a timed inventory pass; this arithmetic is an allowance
estimate, not a measurement of eight application passes. Controlled-clock tests
exercise all eight inventories completing at 672 seconds and rejecting at the
single shared 720-second deadline, without resetting it between phases.
The import process group has a separate 300-second cap. The 1080-second outer
attach bound leaves `1080 - 720 - 300 = 60` seconds: the unchanged process helper
allows up to 25 seconds for TERM/KILL/group-absence cleanup, leaving 35 seconds
for startup/report overhead. The recorded attempt completed application within
these bounds and reached a rejected import; one run does not guarantee future
throughput or successful engine initialization. The outer bound still stops an overrun.
All entry/byte/depth/link/native checks and exact-image 225,000-entry policy stay
unchanged. A slower host fails safely; nothing skips rehashes to finish on time.

Both raw import streams go to `/dev/null`. Only fixed module identifiers,
statuses, guard/load counters, hashes and reviewed application receipts can leave
the payload; host validation rejects extra/arbitrary fields. Partial failures
remain failed evidence. A rejected import has one fixed `error_category`:
`guard`, `missing-module`, `import`, `OS`, `runtime` or `other`; a guard rejection
takes priority. Passing/not-run rows have `null`. No exception text, traceback
or missing-module name is published. `phase_elapsed_ms` has exactly three fixed keys:
`application-preflight`, `application`, and `import-smoke`. Integer monotonic
elapsed milliseconds include a failed phase; `null` means not started. Import
timing includes process-group cleanup. These are observations, not renewed
deadlines, and do not include initial payload bootstrap or Docker startup.
Cleanup removes only the uniquely named, correctly
labelled container and confirms absence; an uncertain create/run completion
stays `completion-unknown` even if a current listing is empty. The base image
is retained, and no other Docker resource is removed.

## Commands

Preparation is offline and does not call Docker. The source and all helpers
must first be committed; output is private, new and never overwritten:

```sh
python3 -I -S scripts/record-engine-import-smoke.py --prepare /private/tmp/nib-engine-import-smoke-payload
uv run --project tools --frozen python -I -m unittest discover -s scripts -p 'test_engine_python_bootstrap.py'
uv run --project tools --frozen python -I -m unittest discover -s scripts -p 'test_engine_import_smoke.py'
```

Only after independent review and explicit parent coordination, using the
existing cached image and verified `build/engine-image-metadata/{manifest,config}.json`:

```sh
python3 -I -S scripts/record-engine-import-smoke.py --slug official-vllm-import-smoke
```

The authoritative evidence validator is built from the same commit using
already-populated Go caches, pinned Go 1.27.2, disabled private telemetry and
`GOPROXY=off`, `GOSUMDB=off`. Missing dependencies/toolchain fail offline.
The source reader cannot use Git replacements or lazy network fetches.

The run deliberately exits 1 and records `result:fail` even if `smoke_pass:true`:
this is not runtime admission. Read `stage`, `experiment`, `smoke_pass` and
`cleanup` together. An exit alone, import success, or a current absence listing
is not evidence of ABI correctness, GPU memory coherence, full DeepSeek support,
attestation, confidentiality or a deployable image. Historical artifacts and
production profiles are unchanged.
