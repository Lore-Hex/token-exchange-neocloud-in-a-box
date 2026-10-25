# P2.T3: Fixed Original Tau CPU Prompt Probe

Status: one separately authorized native CPU probe passed on original retail task
2 with real support models and a scripted target. Simulator prompt/output counts
were 382/24 and evaluator counts 281/88, with original parsing, bound release and
cleanup confirmed. See the [original evidence and limits](../../evidence/p2/2026-10-22-tau-cpu-prompt-fit-pass/README.md).
**No DeepSeek, full tau, quality, live admission or confidential result is claimed.**
The fresh [CPU pair lifecycle](p2-t3-tau-cpu-controller.md) succeeded first; its
earlier failure remains unexplained. This does not authorize another CPU or GPU run.

The separate [fixed identity follow-up](p2-t4-tau-cpu-identity.md) reuses this owner
and transport for three simulator replies. It is software-tested only and does
not change this probe's default behavior or qualify a support model.

## Source and Scope

Two-model tokenizer parity does not establish original tau prompt fit or
evaluator structured-output behavior. This fixed probe uses tau2 revision
`fc0055dc4e0a316c3f83133267fbd6faaa770992`, retail base task `2`, trial 0 and seed
42. The authenticated original task has no initial-state/user-tool setup and
one NL assertion. The normal smoke manifest's first five lexicographic tasks
do not include it; ordinary comparison selection is unchanged.

Inside the existing sealed scorer, the original
[build_text_orchestrator](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/runner/build.py)
initializes and takes two original steps: actual CPU simulator reply, then the
existing fixed scripted target reply. Its actual trajectory goes to the original
[NLAssertionsEvaluator](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/evaluator/evaluator_nl_assertions.py).
Original prompt construction and parsing remain in use. The existing Bridge
also checks the exact assertion, boolean outcome and string reasoning. Early
simulator STOP fails, without inventing a continuation. Reward is not published
or promoted to quality evidence; a zero reward can still demonstrate parser fit.

## Fixed Bounds

The separate `nib.tau-cpu-support-probe-input/v1` contains source/task identity,
two pinned support models, generation/resource limits and false qualification
fields. It has no reference, serving or rate metadata. Pricing is explicitly
`not-measured`, not a zero-price or resource-cost estimate.

There are exactly three requests in simulator/candidate/evaluator order,
120 seconds per request, the existing 180-second trial limit and a 300-second
scorer operation bound. Context is 4096, output at most 256, total reservations
at most 13056 tokens, concurrency one, retries zero. The resource-only budget
inherits reserve, settle, deadlines and sticky abandon; only monetary hooks are
inactive. Ordinary cost-aware behavior is byte-compared against prior committed
reserve/settle/timeout/abandon traces.

Original support prompts are counted through the guarded `/apply-template` and
`/tokenize` path. Valid oversized tokenization or insufficient output reserve
records `context-exceeded` and fails before generation. Malformed responses
stay protocol failures. Raw IDs are cleared; prompts are not shortened.
Length termination, malformed JSON, timeout, EOF and unknown usage fail without
repair, retry or cap increases. The scripted target is not a target-fit test.

## Executable Mode

After separate approval, dispatch main-only `tau-cpu-session-native.yml` with
`support_probe=true`. The default is false, retaining release-only behavior.
Both modes share the existing CPU concurrency group, step/job bounds and
immutable model/image inputs. There is no GPU or public endpoint.

Opt-in preparation uses the existing bounded HTTPS fetch helper for the exact
92,081,935-byte pinned tau archive and all wheels already selected by
`bench/tau-runtime-lock.json`. Size and SHA-256 are verified. No host resolver,
installer, downloaded executable or upstream import runs. The existing offline
source/wheel verifier and sealed payload publisher are reused. No new dependency
or network exception is introduced.

The [actual hosted runner image](https://github.com/actions/runner-images/blob/ubuntu24/20261012.334/images/ubuntu/Ubuntu2404-Readme.md)
ships Docker 28.0.4. Its [image-inspect CLI](https://github.com/docker/cli/blob/v28.0.4/cli/command/image/inspect.go)
has no `--platform` flag, and its [classic store](https://github.com/moby/moby/blob/v28.0.4/daemon/images/image_inspect.go)
returns a config digest without an OCI descriptor. Probe mode therefore uses
one explicit classic-store profile, not a fallback or a change to the ordinary
Docker 29 scorer. Before CPU build/start and again before scorer creation it
requires the exact image config ID, canonical index RepoDigest and linux/amd64;
container checks bind the same config ID and keep every confinement check.
An unexpected descriptor/store profile fails closed.

Read-only public registry verification of the existing pinned Python image
established this immutable chain (not a runtime measurement):

- Index `4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2`, 6546 bytes.
- Linux/amd64 manifest `6e13e65c55e33adf203d77ee371cf8bf5d81bd4902ef07565721f46bf44917af`, 1752 bytes.
- Config `64d91f7b885eed272bba87909446b12ff408d4aaa5f1a0e9ca787bbea1a020b9`, 5676 bytes.

The index and manifest response bytes were SHA-256 checked against their pins;
the config identity is their authenticated descriptor, not a guessed image ID.
No image version, image contents, daemon setting or dependency pin was changed.

With private pinned inputs, the same setup is directly callable:

```sh
python3 -I -S -B scripts/tau_trial.py prepare-probe \
  --source-archive PRIVATE_ARCHIVE --wheelhouse PRIVATE_WHEELHOUSE \
  --output PRIVATE_NEW_DIRECTORY
```

Pass its plan digest and `payload` directory to `record-tau-cpu-session.py`,
alongside the existing four CPU input flags, using
`--probe-payload PRIVATE --probe-plan-sha256 SHA`. The controller stays native
GitHub-hosted/main-only. There is no arbitrary callback, command or model URL.

## Lifecycle and Claims

The existing owner guards both CPU processes and publishes four fresh private
runtime/tokenizer originals after parity. The controller validates their current
source, image and READY hash. Probe mode waits before release. The existing
sealed scorer owner cross-connects private attach pipes to the held CPU worker;
model text remains transient, never host output or artifacts. Logging suppression,
audit restrictions, source closure and network-none confinement remain intact.

A bounded second metadata frame binds the plan, READY and original scorer hash,
fixed statuses, request hashes and token counts. No bodies, token arrays, error
text or dynamic paths are retained. The controller verifies the private scorer
originals before sending the existing bound release, then rechecks both original
report sets, source/image bindings, exit status and owned cleanup. A failed probe
remains failed even when teardown succeeds. Missing or invalid cleanup cannot pass.

The scorer attach now reuses the source-pinned process-group cleanup helper:
denied signals still lead to bounded waits, followed by an independent group
absence check. An exited-child signal race does not replace the original
protocol failure. Persistent denial, an unreaped child or a surviving group
cannot return success; an existing protocol/cancellation exception retains its
identity with a fixed, content-free cleanup-failure cause when necessary.
The borrowed CPU-worker pipes remain owned by the enclosing controller.
Synthetic denial tests and real private-pipe tests cover this recovery; they
do not establish daemon/container cleanup or a new hardware result.

Artifact paths are explicit metadata only: existing controller/pair reports and
four originals, setup metadata, and scorer `run.json`/`output.log`. Payloads,
archives, wheels, task text and pipes are excluded. Historical originals remain
unchanged. No dollar, reference, full-tau, quality or confidentiality claim follows.

Actual selected-target tau still requires six fresh role-bound originals, three
admitted concurrent processes/endpoints and compatible UID/PID/network/control
ownership. The UID-0 GPU versus UID-65532 CPU ownership mismatch is not waived.
A separately approved target run and later reference comparison remain required.
