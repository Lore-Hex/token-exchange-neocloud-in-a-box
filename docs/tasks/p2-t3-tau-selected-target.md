# Selected-target live tau admission

Status: implemented and tested with synthetic receipts, fake kernel observations,
fake loopback HTTP and real subprocess pipes. No actual model, tau benchmark,
container or GPU was run. This changes the existing live consumer, not the
upstream worker/scorer, evidence history, resource limits or quality gate.

## Source audit

All tau references below are pinned to
`fc0055dc4e0a316c3f83133267fbd6faaa770992` (the existing source/archive closure).

- [run_single_task](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/runner/batch.py)
  builds one orchestrator and executes one task with its seed.
- [build_orchestrator](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/runner/build.py)
  selects one agent and one user simulator from that run configuration.
- [run_simulation](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/runner/simulation.py)
  runs their conversation, then evaluates that trajectory. It does not run a
  second reference agent. The NL evaluator remains separately configured.

Our `tau_transport.request_body` already maps the agent category to the selected
`candidate` OR `reference`, while simulator/evaluator remain separate roles.
The old concurrent-reference requirement came from live deployment admission,
not from tau's execution path. Removing that requirement for an explicitly
selected trial is therefore justified; removing the later matched reference
comparison is not.

## Explicit contract

Use `nib.tau-local-deployment/v2` as described in the
[live input contract](p2-t3-tau-live-runner.md#private-input-contract).
`selected_target` is required and is exactly `candidate` or `reference`.
`roles` contains exactly that target, `simulator` and `evaluator`, each with
original independently produced runtime and tokenizer receipts. There are seven
private JSON files, not placeholder receipts for the inactive target.

The original frozen benchmark and embedded transport manifest still bind all
four roles, both same-revision target serving configurations, matched generation
settings, task/trial seeds, simulator/evaluator identities, framing and budgets.
Unknown reference settings still block preparation; do not fill them with invented
values. No inactive reference runtime measurement is claimed or required for a
candidate trial. Reference execution later requires its own real receipts.

Preparation's `--role`, the hashed plan and the live deployment must agree.
The runner rejects disagreement before launching the scorer; the pipe driver
and session independently enforce it too. Output binds the selected role and
the three actual receipt pairs through `nib.tau-live-binding/v2`. It cannot
relabel a candidate runtime receipt as a reference receipt.

Each active role retains source/weight/runtime/tokenizer checks, receipt expiry,
owner and process identity, exact loopback listener ownership and connected-peer
checks before sending a body. Simulator/evaluator models and weight inventories
remain distinct from each other and the tested model. No aliases, proxies,
default receipt values, request logs or fallback transport are added.

Legacy v1 remains four-role and has unchanged bindings. The default scripted CLI
is unchanged. V2 does not silently reinterpret v1 or admit incomplete v1 inputs.

## Sequential operation

1. Freeze both independently qualified target configurations before preparation.
   Admit a v2 candidate deployment with its two support models and run the
   existing [prepare/run commands](p2-t3-tau-live-runner.md#commands) with
   `--role candidate`, a new private payload and a new evidence directory.
2. Finish the trial and confirm scorer cleanup. The separately approved engine
   owner must stop and confirm cleanup of the candidate before reusing its GPU
   resources. This consumer does not own engines or manufacture that proof.
3. Load the approved reference configuration, measure its actual new process and
   tokenizer, and authenticate a different v2 deployment selecting `reference`.
   Prepare a new payload with `--role reference` and run the same frozen task/seed
   with unchanged simulator/evaluator configurations. Never relabel the candidate
   run or treat a rerun of the same unqualified candidate as an independent reference.

The existing per-trial Bridge/Budget, request accounting, unknown-usage
reservations, 180-second trial ceiling and scorer cleanup are unchanged.
These are separate invocations, not a new campaign controller or a persistent
cross-invocation budget. Approval must bound the combined loading, trials and
teardown; repeated invocations do not authorize additional spend.
Partial or failed attempts remain attributable to their actual target.
The unchanged aggregator still requires both report slots and frozen coverage;
missing reference results remain incomplete. Operational results remain
`quality_eligible=false`/`quality_accepted=false`, not scored acceptance.

## Practical model step

Prefer testing two small CPU support models before trying to share the current
DP2 GPU allocation. The existing [Qwen pilot](../operators/qwen-pilot.md) pins
Qwen3-0.6B Q8_0 at `23749fefcc72300e3a2ad315e1317431b06b590a` and llama.cpp at
`620d2225cc436ab0b001e3200e590e3b286ef9ab`; its 639,446,688-byte GGUF already has
a full-file hash in `scripts/pilot/pins.json`. That is disk size, not a RAM or
tau-latency measurement. A distinct evaluator candidate is the official
[Qwen3-1.7B-GGUF revision](https://huggingface.co/Qwen/Qwen3-1.7B-GGUF/tree/90862c4b9d2787eaed51d12237eafdfe7c5f6077),
identified by [publisher metadata](https://huggingface.co/api/models/Qwen/Qwen3-1.7B-GGUF)
on 2026-10-22. It is a proposed pin, not an admitted or downloaded model here.

The [explicit CPU transport](p2-t3-tau-cpu-framing.md) now implements and tests the
pinned render/tokenize/chat protocol without enabling live admission.
Next bounded implementation: verify that second model's complete selected-file
inventory, reuse the pilot's CPU-only launcher, and add an explicitly pinned
llama.cpp transport/receipt kind with independent full-template token parity.
Current live admission is vLLM-only; the pilot's pinned
[text-tokenization API](https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/tools/server/README.md)
is not the
vLLM chat-tokenization contract. Do not pretend compatibility or weaken the
engine pin. Keep the original tau prompts/scorer and measure context fit,
structured evaluator output, memory and latency under unchanged limits before
combining these models with the retained DP2 agent. A GPU alternative requires
measured headroom and separate approval, not an inference from DP2 occupancy.

The pinned tau [guidance](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/docs/leaderboard-submission.md)
recommends `gpt-5.2` for simulator accuracy; its
[code defaults](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/config.py)
use `gpt-4.1-2025-04-14` for the user simulator and NL evaluator. Small Qwen
support models are an operational experiment, not validated substitutes for
that quality recommendation. No external paid API is authorized by this plan.

## Verification

```sh
python3 -B -m unittest discover -s scripts -p 'test_tau*.py'
python3 -B scripts/check-doc-links.py
git diff --check
```

Tests exercise three fake endpoints for either selected target, reject wrong
role/receipt/configuration bindings before dispatch, retain distinct models and
process/socket identities, preserve failure accounting and scorer cleanup, and
keep missing paired reference coverage incomplete. Fixtures are not receipts.
