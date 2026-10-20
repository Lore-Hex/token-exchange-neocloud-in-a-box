# P2.T3 scripted single-trial integration

Depends on PR84, reviewed head
`ffcb4f91d68337b5a6e5908294ca62e6d58517dd`, and its
[source/wheel closure](p2-t3-tau-runtime.md). This is a **synthetic integration
smoke**, not a live model adapter, tau quality result, attestation, or P2.T3
completion. The candidate and confidential release remain BLOCKED.

## Small executable boundary

[tau_trial.py](../../scripts/tau_trial.py) has two explicit operations:

1. `prepare` verifies the pinned public source archive, frozen manifest and all
   74 locked wheel files. It materializes a private, indexed payload without
   installing packages, invoking build hooks, importing tau, or using Docker.
2. `run` verifies that payload and its caller-supplied plan digest, uses only the
   cached immutable Python image, and runs one selected trial with **scripted
   in-memory completions**. No endpoint URL or credential is accepted.

[tau_trial_worker.py](../../scripts/tau_trial_worker.py) calls the normal pinned
`TextRunConfig` / `run_single_task` / `EvaluationType.ALL` path. The upstream
orchestrator and evaluators, including natural-language assertions when present
on the task, remain authoritative. There is no replacement orchestration or
scoring implementation. Scripted replies deliberately do not solve the task.
A reward of zero can still be an integration success. It is not evidence that
any served LLM works.

The fixed agent/simulator dialogue reaches normal user-stop termination.
Natural-language judgments, when requested, return an explicitly false result
for every selected task assertion. The bridge rejects missing, duplicated,
reordered or mistyped assertion results; it does not permit upstream's empty
`results` list to turn a nonempty assertion set into a vacuous success.
The first proposed smoke is airline task `0`, which has one assertion in the
pinned public data; only a task in the frozen manifest may be selected.

## Source contract

All tau references below are immutable revision
`fc0055dc4e0a316c3f83133267fbd6faaa770992` (MIT). Sources and licenses are copied
unchanged into the payload from the verified archive; selected dependency
licenses and distribution metadata remain in their verified wheel layout.

- [Single-task entry and log context](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/runner/batch.py):
  explicit `save_dir=None`, `verbose_logs=False`, `audio_debug=False`,
  `audio_taps=False`, `auto_review=False`; no batch runner, persistence or retry.
- [Simulation and standard evaluation](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/runner/simulation.py):
  upstream `run_simulation` attaches the standard evaluator result.
- [Shared completion hook and raw logging](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/utils/llm_utils.py):
  agent/user/evaluator aliases of `generate` still resolve the shared
  `completion` global. That global is replaced before the trial. The LLM log
  context is explicitly cleared before/after; `_write_llm_log` is an inert
  function. Upstream float cost bookkeeping is zeroed; it is not our ledger.
- [NL evaluator](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/evaluator/evaluator_nl_assertions.py):
  its own imported model/argument constants are bound to the scripted evaluator.
- [User stop marker](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/user/user_simulator_base.py)
  and [configuration/termination enums](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/data_model/simulation.py):
  normal stop, timeout, truncation and infrastructure/protocol errors remain
  distinct; caught budget failures cannot later become evaluated trials.

The [unchanged lock](../../bench/tau-runtime-lock.json) pins the complete wheel
bytes and their public download URLs. Source inspection, not package execution,
established these startup controls:

| Selected wheel member | SHA-256 |
| --- | --- |
| litellm 1.81.11 `litellm/__init__.py` | `e845de5ca5cb33a08033a96923a19f03e9b71b76bad77c9460e8def2a2d0b6dd` |
| litellm 1.81.11 `litellm/litellm_core_utils/get_model_cost_map.py` | `bb60ef117d577d2470efd6d85cf3de7b9d14fcff41281aa31aea796815318acf` |
| python-dotenv 1.2.1 `dotenv/main.py` | `a2e260a51fca18997d174f6fc22835c33b8ad0db09da20f74d60b5199692465c` |

`LITELLM_LOCAL_MODEL_COST_MAP=True` selects its packaged map before import;
`PYTHON_DOTENV_DISABLED=1` disables dotenv discovery before tau import.
LiteLLM telemetry and callback lists are disabled. Loguru sinks are removed and
standard logging disabled before tau import. Ordinary stdout/stderr, including
native writes, are redirected to `/dev/null`; a non-inheritable descriptor emits
only the bounded validated report. Socket access and subprocess/fork audit
events remain denied, in addition to the container's network/resource policy.
No output, exception text, dynamic path, prompt, tool arguments or trajectory
is used as a retained error category.

## Bounds and provenance

The existing `tau_preflight.Budget` is the only ledger. Every completion role
reserves its maximum request before dispatch; no retries or concurrent calls
are supported. Nested signal timers enforce the request and whole-trial
deadlines. Late/invalid/missing usage retains the reservation and marks usage
unknown. Known truncation retains settled usage and is not evaluated.
Requests/responses have independent 1 MiB / 256 KiB structural byte bounds,
including depth, node count, tool count and tool argument limits.

**Scripted usage is synthetic (one input and one output token per completion),
not a tokenizer measurement or actual price.** Input-token/framing enforcement
for all real roles, endpoint identity, approved live transports and measured
quality remain future work. Manifest serving/model bindings are carried only
in an explicitly `synthetic-fixture` report, never represented as an actual
served-model identity.

Preparation preserves PR84's source-tree/not-installed mode and exact
root-version/build-backend mismatch handling. It does not modify `uv.lock`,
build tau, or invent installed root distribution metadata. The 17 shared
`typer` / `typer-slim` 0.20.0 files must be byte-identical and have their exact
reviewed names; all other file collisions fail. `.pth`, startup customization,
bytecode, `.data` layout relocation and unsafe archive entries fail.

The selected files are bounded by 11,000 members / 512 MiB expanded total and
verified again before upstream import. The host payload parent stays 0700;
the read-only bind subtree is 0555/0444 for unprivileged cross-UID reads.
The exact Python 3.12.13 linux/amd64 image is reused from the lock, with
`-I -S -B`, explicit verified source/package roots and an empty inherited
environment. Runtime Python/architecture/glibc are checked, not assumed from
the host interpreter.

The container uses `runc`, UID 65534, no GPU/device requests, no network/ports,
read-only rootfs, all capabilities dropped, no-new-privileges, 2 CPUs, 4 GiB
memory/swap ceiling, 64 PIDs, and a 256 MiB noexec scratch tmpfs. No image pull
is allowed. Docker uses a private credential-free config and an explicitly
selected local Unix socket. Inspection rejects policy drift before start;
image inspection explicitly selects the same `linux/amd64` platform as create.
The [Docker inspection contract](https://docs.docker.com/reference/cli/docker/image/inspect/)
requires API 1.49+ for `--platform`; unsupported daemons fail before create,
without falling back to host-default inspection. A multi-platform digest can
otherwise inspect the host's ARM member despite an AMD64 pull. The inspected
member must still match the pinned repository digest, platform and no-volumes
policy; its immutable ID is used for container creation.
Normal completion requires no OOM, exit zero and stopped state. The owned
container is force-removed and an independent label query must be empty.
Create completion is uncertain from immediately before dispatch until exact
owned-container inspection succeeds. Client timeout or failed inspection keeps
`cleanup=create-completion-unknown`, even if cleanup currently lists no matching
container: the daemon can finish a create after that query. Such an attempt
cannot claim cleanup or integration success.
The Docker operation shares 300 seconds, with a separate maximum 60-second
cleanup allowance; initial host revalidation is separately bounded to 60
seconds. A worker-wide 240-second timer also covers startup and import; file
verification is bounded to 60 seconds, and the trial to the smaller of the
manifest trial deadline and 180 seconds.

`run.json` is a software-only `nib.evidence/v1` envelope. `output.log` contains
only fixed fields, hashes and numeric observations; the included single-trial
report conforms to `nib.tau-trials/v1`. No raw child capture is saved. Cleanup
failure, invalid child output, cancellation and failed trials are failures,
not qualification. Publication is private/exclusive; retain incomplete
attempts and never overwrite them. Aggregation keeps every other frozen trial
missing, so a single result never shrinks the comparison denominator.

## Review-gated use

No upstream execution is part of author tests. After independent final-head
review, green CI and parent authorization, prepare from the exact checkout:

```sh
python3 -I -S -B scripts/tau_trial.py prepare \
  --source-archive /PRIVATE/tau-source.tar.gz \
  --wheelhouse /PRIVATE/wheels \
  --manifest /PRIVATE/frozen/manifest.json \
  --manifest-sha256 FROZEN_MANIFEST_SHA \
  --domain airline --task-id 0 --trial 0 --role candidate \
  --output /PRIVATE/new-trial
```

Then the parent may execute **one** reviewed scripted smoke, with the printed
plan digest and an already cached image. This command does not authorize a
pull, endpoint call, resource rental or later live benchmark:

```sh
python3 -I -S -B scripts/tau_trial.py run \
  --payload /PRIVATE/new-trial/payload --plan-sha256 PREPARED_PLAN_SHA \
  --docker-socket /PRIVATE/local-docker.sock \
  --output /PRIVATE/new-evidence
```

Tests use fake upstream modules, fake Docker, synthetic archives and planted
content. They cover the normal single-task/evaluator interface, role accounting,
tool responses, timers/cancellation, unknown usage, all outcome denominators,
native-output suppression, policy mutation, cleanup and no-overwrite:

```sh
python3 -B -m unittest discover -s scripts -p 'test_tau*.py'
```

An actual runtime report is still required to establish whether the selected
wheel/source layout imports and runs successfully in the pinned image.
