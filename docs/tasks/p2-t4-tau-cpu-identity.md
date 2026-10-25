# P2.T4: Fixed CPU Simulator Identity Follow-Up

Status: implemented and tested with synthetic private-pipe replies; no model run
or simulator qualification. The original [CPU prompt-fit probe](p2-t3-tau-cpu-prompt-fit.md)
remains the default. This explicit profile adds three fixed simulator calls, not
another GPU trial or an evaluator score.

## Source and Motivation

The pinned tau revision is `fc0055dc4e0a316c3f83133267fbd6faaa770992`;
archive SHA-256 is `7a227036d07fdeb088dbd0c99fdb40ee9b9e0ebc06aadbc4dec1217b3383e57e`.
Its [retail task data](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/data/tau2/domains/retail/tasks.json)
says task 2's user does not remember their email. The scenario supplies no email
address. Its expected first action uses name/ZIP, whose supplied values identify
exactly one row in the pinned database. Preparation verifies these facts from
the hash-checked task/database bytes; the database email is never injected into
the simulator prompt or exported in the prepared observation fixture.

The [original lookup implementation](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/domains/retail/tools.py#L286-L330)
supports name/ZIP when email is unavailable. The
[original simulator](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/user/user_simulator.py)
maintains the scenario, guidelines and flipped conversation history itself.
The diagnostic calls that implementation, built by the same original
`build_text_orchestrator` as the existing CPU probe, with its normal initial
greeting followed by exactly these two fixed assistant questions:

- What is the email address associated with your account?
- What are your first name, last name, and ZIP code?

No candidate, evaluator or tool generation is performed in these three calls.
The shared owner still starts and parity-checks both pinned CPU support models;
those existing prerequisite requests are separate from the three diagnostic
requests. Model identity, original rendering/tokenization, owned listener and
per-connection process checks remain mandatory. The original probe still uses
simulator/scripted candidate/evaluator, with its unchanged schema and defaults.

## Bounds and Interpretation

The new input schema is `nib.tau-cpu-identity-probe-input/v1`. It retains seed 42,
temperature 0, one admission, no retries, three requests, 4,096 context tokens,
256 output tokens per request, 120-second request/180-second trial/300-second
run limits and the existing 13,056-token aggregate ceiling. The final request
must fit its complete growing history plus output reserve; no trimming or cap
expansion is allowed. The existing CPU owner/controller lifetime and cleanup
reserve also apply, without extending readiness or response deadlines.

`nib.tau-cpu-identity-probe/v1` emits only source/plan binding, fixed step names,
actual integer usage, completion status and four booleans per observed reply:
an ASCII email-like literal, expected first name, last name and ZIP literals.
Name/ZIP comparisons are case-insensitive with word boundaries. The email
pattern examines whitespace-delimited runs of at most 320 characters; it is not
an RFC validator, semantic interpretation or proof of email absence. Quoted or
negated email text still sets the literal flag. No response, prompt, identity
value, arbitrary exception or model explanation is logged.

PASS means all three bounded observations completed and validated, not that
their literals were desirable. A reply containing an email is retained as a
boolean observation, not repaired or retried. STOP/tool replies, truncation,
malformed usage, timeout, cancellation, an extra request or uncertain cleanup
cannot become successful diagnostic completion. A zero internal pipe sentinel
is not an evaluator reward and is not included in the diagnostic report.
All quality, live-admission and confidential flags remain false.

The previous [email-containment failure](../../evidence/p2/2026-10-25-selected-tau-email-relation-0429/README.md)
cannot be reconstructed because conversation content was intentionally not
retained. This new preregistered test neither assigns blame nor changes that
original evidence or any selected-trial scoring/tool-error policy.

## Operator Handoff

Review and green CI are required before a separately authorized CPU-only run.
Use a clean exact source checkout, approved Linux amd64 venue with at least four
CPUs, 10 GiB total/8 GiB available RAM and 12 GiB free Docker storage. Existing
pinned Docker images, Buildx, llama source, both GGUF files, CMake wheel and the
tau wheelhouse must already be present and pass their existing validators.
No Docker image pull, GPU, arbitrary endpoint, public port or credential is
introduced. The explicit local-controller flag accepts this identity profile
only; it cannot relax the existing GitHub-only default controller preflight.

Offline preparation (no upstream/model execution):

```sh
python3 -I -S -B scripts/tau_trial.py prepare-probe \
  --identity-followup --source-archive "$TAU_ARCHIVE" \
  --wheelhouse "$TAU_WHEELS" --output "$FRESH_PROBE"
```

Use the emitted plan SHA, without editing the generated payload:

```sh
python3 -I -S -B scripts/record-tau-cpu-session.py \
  --local-identity-followup \
  --archive "$LLAMA_ARCHIVE" --model-06 "$MODEL_06" --model-17 "$MODEL_17" \
  --cmake-wheel "$CMAKE_WHEEL" --output "$FRESH_RESULT" \
  --probe-payload "$FRESH_PROBE/payload" --probe-plan-sha256 "$PLAN_SHA"
```

Retain original controller, pair and scorer receipts and their cleanup results.
Do not publish private input paths or reconstruct missing transcript content.
Source-only task/database verification is reproducible without Docker/models:

```sh
python3 -B scripts/test_tau_cpu_identity.py --upstream-source "$TAU_ARCHIVE"
```
