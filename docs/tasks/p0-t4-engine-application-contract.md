# Exact-Image Application Contract

**Experimental software contract, not runtime admission.** The candidate remains
`BLOCKED`, `deployable:false`, `image:null`. Tests apply the five unchanged patch
stages to synthetic file trees only. This change does not build or execute the
official image, import vLLM/plugins/dependencies, load a model, run a GPU, or
establish ABI, attestation or confidentiality.

## Measured Inputs

The contract binds the unchanged [complete Python collection](../../evidence/p0/2026-10-20-official-vllm-python-chain/run.json)
from committed source `d033d2198866f7fdbd509e56329702942f22de14`:

- [Output](../../evidence/p0/2026-10-20-official-vllm-python-chain/output.log)
  SHA-256 `52a15aafc3044821740c5d408e5fc44f6635bea36c30f5af51f5866120810440`.
- Linux/amd64 manifest `6ee133356d49ed24cee03dd0cda3d3980279a11c107ce3e752c89e76540f41c9`,
  official raw config `bd8a5d7a8774800c92d714c0c226b32c1863f929150f36efd610aebcd12a335d`.
- Interpreter terminal `/usr/bin/python3.12`, 8,020,928 bytes, SHA-256
  `e50d468e8b0adfb05733f5b87b3cff34829c4a8c1aea50c865aa8bdfe4bb150f`.
- Selected inventory SHA-256
  `456a79e70743e77ce61bb05200093d769264372dd472a48fa08a2c1cbf37883a`.
- 2,879 source-verified unlisted cache files, 48,909,952 bytes; removal-plan
  SHA-256 `210a50805b36797d06e1d17425f6eacba6f90f1e04aaa7c0560692c05ffb3b3f`.

The historical run remains `result:fail`, `collection:pass`. Its stdlib child
canaries are not real vLLM workers. No original artifacts are edited. Docker/OS
and interpreter bootstrap remain trusted; Docker `.Id` is not treated as an
independent config-byte digest. A future builder must separately enforce raw OCI
linkage and the prepared context digest, as the read-only recorder already does.

## Prepare, Do Not Execute

From committed source, with an existing private parent and new absolute output:

```sh
python3 -I scripts/prepare-engine-application.py --output /private/tmp/nib-engine-application-context
uv run --project tools --frozen python -I -m unittest discover -s scripts -p 'test_engine_application.py' -v
```

Preparation uses the existing committed-blob reader, bounded direct-source
bootstrap, five-stage verifier and private/no-overwrite publisher. It cannot
fetch lazy Git blobs, use replacements, import ignored bytecode, install packages,
or launch Docker. Existing failed output is not reused. The receipt binds the
complete source bundle, measurement, candidate, payload and generated context.

The generated Docker recipe is an **unexecuted input for separately reviewed
experimental application**, not an instruction to build now. Its base is the
exact manifest above, its application step has no network, and its entrypoint
deliberately exits `BLOCKED: no engine launcher`. The in-image application script
defaults to inspection; only its explicit `--apply` form writes. Neither form
can dispatch the engine or accept operator paths/flags/policy overrides.

## Application Checks

Before opening the selected package root for application, the tool rechecks the
measured interpreter/sysconfig/flags, exact alternatives chain and terminal hash,
stdlib bootstrap files, both hook hashes, all 14 worker sources, bounded root
metadata and observed name collisions. The two verified interpreter invocation
spellings are supported; no arbitrary alias or `/etc` path is allowed. Only scan
elapsed time may differ from the recorded layout. Missing optional roots must
still match the recorded absence; they are not reclassified from a site index.

The selected tree must match the **entire measured file inventory digest**, not
merely a self-consistent RECORD or matching package versions. The exact-image
225,000-entry budget and all four exact CUTLASS cross-compile assets are reused;
byte/time/depth/link/native checks remain bounded. Classification does not mean
those assets are host ABI-compatible or used at runtime. Generic `MAX_FILES`
remains 100,000, and the generic CLI/root/hook/native admission stays unchanged.

The exact entrypoint map, dependency/source checks and removal-plan digest must
match before any write. All five stage hashes are validated before deletion.
The shared writer rechecks removal, each full-stage inventory, unchanged native
bytes/modes and the console file. Original RECORD and hook bytes remain intact.
Any mismatch, interruption or failed deletion invalidates the partially modified
experimental image; discard it, never resume or infer acceptance from files.
This assumes a trusted, quiescent build filesystem, not atomic protection against
a concurrently hostile OS or filesystem writer.

## Import Contract, Not A Launcher

The generated, non-executable import contract requires `-I -S -B` and exactly:

1. `/usr/lib/python3.12`
2. `/usr/lib/python3.12/lib-dynload`
3. `/usr/local/lib/python3.12/dist-packages/nvidia_cutlass_dsl/dsl_packages`
4. `/usr/local/lib/python3.12/dist-packages`

The path digest is
`ecf3cf1ec68e98d1e5aca0ea5d79b2645d24746714e824b839a6a42bda96dd77`.
Alternate distro roots are excluded, not deleted. The two `.pth` hooks and
stdlib `sitecustomize` stay inert only under the no-site bootstrap; this is not
permission to run ordinary Python or `site.main()`/`addsitedir()`. `-B` alone
does not prevent loading bytecode: the verified vLLM removal plan is mandatory.
Other exact-image dependency/stdlib cache bytes are not claimed source-verified
or removed by this contract.

Future execution is restricted to the source-supported `spawn` method and `mp`
executor, with NUMA binding off and `VLLM_PLUGINS=""`. Config changes, forkserver,
fork, Ray, external launchers, arbitrary Python subprocess commands and implicit
`pycache_prefix` propagation are unsupported. These are **validated contract
fields, not an implemented runtime sandbox**. This PR deliberately supplies no
engine launcher. A later launcher must fix paths before spawn/unpickling, seal
the parent's preparation data, preserve wrapper reentry and reject unsupported
fresh subprocess forms. The real forkserver preload path is not qualified by
the historical synthetic empty-preload canary.

Primary source at vLLM `5df63a7203cdb9328d824a27b682e77c7b9928be`:

- [envs.py](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/envs.py)
  declares `VLLM_WORKER_MULTIPROC_METHOD` choices `fork`/`spawn`.
- [parallel.py](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/config/parallel.py)
  defines `distributed_executor_backend` and `numa_bind=False`.
- [multiproc_executor.py](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/v1/executor/multiproc_executor.py)
  starts context processes through `configure_subprocess`.
- [numa_utils.py](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/utils/numa_utils.py)
  handles the optional NUMA executable wrapper.
- [API entry](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/launchers/api_server/entry.py)
  has a real-engine forkserver preload branch, excluded here.
- [CPython 3.12.3 subprocess](https://github.com/python/cpython/blob/v3.12.3/Lib/subprocess.py#L300-L358)
  propagates isolated/no-site/no-bytecode flags, but not `-X pycache_prefix`.

All earlier TP4/EP-off/zero-offload/collective-off/Engram-on-device controls remain
in the separate partial launch plan. No production profile, new engine image,
native ABI/GPU result, real-worker result or hardware availability is implied.
