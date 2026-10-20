# Bounded Engine Import Smoke

**Experimental software only.** This recorder can apply the five unchanged
candidate patches inside one disposable container and import four pinned
API/engine modules. It does not start an API server, construct a model or TP
worker, load weights, expose ports, allocate a GPU, or enable a production
profile. Imports execute engine/dependency/native initialization code. They are
not a Python sandbox, ABI proof, hardware qualification or confidential service.
The candidate remains `BLOCKED`, `deployable:false`, `image:null`.

Implementation tests use fake wheel trees, fake Docker lifecycle responses and
real local stdlib processes. **No actual image experiment has been run for this
change.** An actual run requires final independent source review, parent
coordination and committed-source evidence. Earlier read-only collections are
not application or real engine import evidence.

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
for startup/report overhead. Neither allowance has been validated in an actual
application/import run. The outer bound still stops an overrun.
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
