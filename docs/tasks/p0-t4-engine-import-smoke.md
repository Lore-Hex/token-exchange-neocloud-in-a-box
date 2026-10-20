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
delete alternate roots or import plugins.

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

Primary source for these boundaries:

- [CPython 3.12.3 spawn](https://github.com/python/cpython/blob/v3.12.3/Lib/multiprocessing/spawn.py):
  `get_command_line`, `get_preparation_data`, `_main`, `prepare`; preparation is
  unpickled first and can otherwise restore paths or run a different main file.
- [CPython process spawning](https://github.com/python/cpython/blob/v3.12.3/Lib/multiprocessing/popen_spawn_posix.py)
  and [resource tracker](https://github.com/python/cpython/blob/v3.12.3/Lib/multiprocessing/resource_tracker.py).
  Its `_stop` discards `waitpid` status; this smoke instead checks a bounded,
  owned tracker PID exit and rejects nonzero exit, restart or unknown ownership.
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
names cannot match the empty string. No plugin loader is called by our harness.
No user args/env/paths are accepted. TP4, EP off, Engram `cpu_offload:false`,
zero explicit weight offload and fusion-disabled controls remain in the
unchanged non-executable launch plan; no engine configuration is instantiated
here. Software CC selection is not attestation. Unsupported upstream imports
or environment/process changes fail this experiment, not silently relax it.

## Bounds And Failure

There is no image pull/build/commit/publication. The already-cached exact base
is used in one owned container: network none, no devices/GPUs/ports/secrets,
all capabilities dropped, no-new-privileges, private IPC (16 MiB), 128 PIDs,
8 GiB memory/no extra swap, two CPUs, 512 MiB noexec tmpfs and a 900-second
outer deadline. The read-only source payload is the only host mount. Root is
used to patch the ephemeral container layer; this layer is not a read-only
filesystem or an adversarial-native-code sandbox. No independent disk quota
for that layer is claimed. Home/cache scratch is directed to the bounded tmpfs.

The generic inspector retains its 300-second deadline. Only this exact-image
application gets **one shared 540-second deadline**, including its layout
preflight and eight full inventory passes: initial inspection, before apply,
after deletion, and five after-stage checks. No stage renews it. The measured
inventory has 19,176,556,448 logical bytes; eight passes read approximately
153.4 GB before small metadata overhead. The budget is a finite operational
allowance, **not a measured application throughput guarantee**. Controlled-clock
tests cover completion after 300 seconds and rejection at the shared deadline.
The import process group has a separate 300-second cap within the outer limit.
All entry/byte/depth/link/native checks and exact-image 225,000-entry policy stay
unchanged. A slower host fails safely; nothing skips rehashes to finish on time.

Both raw import streams go to `/dev/null`. Only fixed module identifiers,
statuses, guard counters, hashes and reviewed application receipts can leave
the payload; host validation rejects extra/arbitrary fields. Partial failures
remain failed evidence. Cleanup removes only the uniquely named, correctly
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
