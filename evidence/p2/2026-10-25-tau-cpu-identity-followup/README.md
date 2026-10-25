# Fixed CPU Identity Follow-Up: Transport PASS, Simulator Unqualified

These eleven files are unchanged original metadata from native GitHub-hosted
Linux/amd64 [run 40169596281, attempt 1](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/40169596281),
dispatched on `main` with `identity_followup=true`, `support_probe=false`.
Exact producer: `fa59772be64e61147058e448d28cc1f3eb34ae03`.
The workflow job ran 06:35:00-06:41:48 UTC on 2026-10-25. The original scorer
records 06:41:30-06:41:43 UTC; this is not a per-request latency measurement.

## Observations

| Fixed step | Prompt tokens | Completion tokens | Email-like literal | Expected first name / last name / ZIP literals |
|---|---:|---:|---|---|
| Initial | 382 | 24 | false | false / false / false |
| Email question | 425 | 11 | false | false / false / false |
| Name/ZIP question | 458 | 18 | false | false / false / false |

The pinned Qwen3-0.6B simulator completed all three diagnostic requests: 1,265
prompt and 53 completion tokens, zero agent/evaluator calls, no retry. The
shared owner also started and parity-checked pinned Qwen3-1.7B as the evaluator;
those prerequisite probes are not additional identity-diagnostic calls.
Controller process/producer, owner container/image/build, both CPU processes
and scorer cleanup are confirmed in the original receipts.

**PASS means bounded transport completion, not correct simulator behavior.**
None of the replies, including the direct name/ZIP question, contained those
expected literals. No email-like literal was detected by the bounded ASCII
pattern. Neither observation establishes semantic refusal, fabricated identity,
absence of every possible email representation, or the cause of earlier tau
failures. The model text was not retained; it cannot be reconstructed from this
evidence. There is no candidate inference, reward, full tau result, qualified
simulator/evaluator, live admission or confidential claim. All corresponding
flags remain false. The earlier failed trials remain unchanged.

## Original Bytes and Joins

The workflow's two original ZIP artifact hashes were checked before extraction:

- `tau-cpu-session-40169596281-1`: `76ba100f57ba4e0909bd2edfe821240a36933f3aed7816da0f287a0400785c95`.
- `tau-cpu-support-probe-40169596281-1`: `b06b2f2f68dd6f3b417ec3218ddb4f94a61a813d93413caeb36b52f4c6320db3`.

Only paths were assembled into this directory: `setup.json` came from
`probe-inputs/setup.json`, and `pair/scorer/*` from `proof/pair/scorer/*`.
No JSON was reserialized. Per-file hashes are pinned in
[the replay tests](../../../scripts/test_tau_cpu_identity_evidence.py).
The `output.log` files are structured metadata, not model/stdout transcripts.
Exact public hash exceptions are limited by field, value and evidence path;
no real credential is exempted. Private paths, workflow logs, model text and
prepared payloads are not published.

Plan SHA-256: `5db8bd56b1c657d180703153238fcd14f2ddcca6d10b1e6c3b53ad250165c46a`.
Independent offline reconstruction from the same producer, pinned tau archive
and 74 locked wheels produced that hash and 9,382 prepared files without
executing upstream or a model. Tests bind the 28 owner sources, 33 controller
sources, 22 scorer sources, original readiness frame, model/tokenizer hashes,
three observations and terminal cleanup. Historical freshness alone is
replayed at the original wall time; no live endpoint is contacted.

See the [fixed diagnostic contract](../../../docs/tasks/p2-t4-tau-cpu-identity.md)
for the unchanged three-turn, 4K-context, 256-output, no-retry limits.
