# P2.T4: Pinned 1.7B Simulator Identity Diagnostic

Status: software-only opt-in profile. No run, simulator qualification, evaluator
qualification or tau quality result is established by this implementation.
The [original 0.6B identity profile](p2-t4-tau-cpu-identity.md), its evidence and
the default prompt-fit probe are unchanged.

## Fixed Source and Role Binding

The existing [CPU model pins](../../scripts/tau_cpu_support_models.json) select
`Qwen/Qwen3-1.7B-GGUF` revision
`90862c4b9d2787eaed51d12237eafdfe7c5f6077`, file
`Qwen3-1.7B-Q8_0.gguf`, 1,834,426,016 bytes, SHA-256
`061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a`.
The [pinned upstream file](https://huggingface.co/Qwen/Qwen3-1.7B-GGUF/blob/90862c4b9d2787eaed51d12237eafdfe7c5f6077/Qwen3-1.7B-Q8_0.gguf)
and existing llama.cpp revision `620d2225cc436ab0b001e3200e590e3b286ef9ab`
are reused, not replaced or converted. No model or engine flag is guessed.

The existing CPU pair still checks both pinned weights, independent tokenization,
four parity cases per model, distinct owned processes/listeners and per-connection
identity. Its original receipt slot for 1.7B is named `evaluator`. The new sealed
manifest explicitly maps diagnostic `simulator` to that owner slot, not to the
0.6B process. No receipt or model name is rewritten. The new profile declares
only a simulator model and creates only its client; the unused evaluator framing
is explicitly disabled. Neither support model becomes a qualified tau judge.

Before any diagnostic completion, the simulator runtime must match the pinned
1.7B revision, GGUF, model inventory, tokenizer and framing recipes, and the
owned endpoint's process identity. Metadata binds the actual runtime receipt,
process, model inventory, tokenizer and framing hashes, together with both
logical and original owner roles. The controller joins this binding to the
original hash-checked `evaluator-runtime.json`; the original pair readiness
already joins its tokenizer receipt. Swapped roles, source/receipt drift or
unknown cleanup cannot become successful completion.

## Diagnostic and Limits

The same pinned tau revision `fc0055dc4e0a316c3f83133267fbd6faaa770992`
[simulator implementation](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/user/user_simulator.py)
and hash-checked retail task 2 are used. Its normal initial turn is followed by
the same fixed email and name/ZIP questions documented in the
[original identity diagnostic](p2-t4-tau-cpu-identity.md#source-and-motivation).
There are exactly three simulator calls, zero candidate/evaluator/tool calls,
seed 42, temperature zero and no retry. The complete growing conversation must
fit 4,096 context tokens including the 256-token output reserve. No truncation
repair, history trimming, budget enlargement or automatic retry is permitted.

The diagnostic retains the 120-second request, 180-second trial, 300-second run
and 13,056-token aggregate ceilings. Owner startup/build/parity prerequisites
retain their separate existing bounds; the whole workflow is not a five-minute
job. The original owner expiry and cleanup reserve always take precedence.

Input/report schemas are `nib.tau-cpu-identity-17b-probe-input/v1` and
`nib.tau-cpu-identity-17b-probe/v1`. Reports retain actual usage and only four
literal-presence booleans per fixed turn, plus the closed simulator binding.
No raw names, email, ZIP, prompt, completion, arbitrary exception or explanation
is retained. PASS means complete bounded observations and confirmed cleanup,
not correct identity, useful simulation or benchmark quality. All quality,
live-admission and confidential flags stay false. A real tau quality judge
requires a separate qualification gate; this profile supplies none.

## Gated Workflow

After final-head independent review, full CI and separate dispatch approval,
the [existing native CPU workflow](../../.github/workflows/tau-cpu-session-native.yml)
can select `identity_followup_17b=true`. `support_probe` and
`identity_followup` must both remain false. All three default to false and
conflicting selections fail before preparation. Existing main-only hosted
runner, image/source/input locks, network-none CPU container, read-only/nonroot
confinement, workflow concurrency and metadata-only artifact paths are reused.
No paid resource, GPU, arbitrary URL or new credential is needed or authorized.

Offline preparation with already-present pinned public inputs is also explicit:

```sh
python3 -I -S -B scripts/tau_trial.py prepare-probe \
  --identity-followup-17b --source-archive "$TAU_ARCHIVE" \
  --wheelhouse "$TAU_WHEELS" --output "$FRESH_PROBE"
```

The existing local-controller identity flag accepts this exact sealed profile;
it does not accept caller-defined role/model/limit substitutions. No prior
failure or missing transcript is reconstructed, and no hardware dispatch is
part of this source change.
