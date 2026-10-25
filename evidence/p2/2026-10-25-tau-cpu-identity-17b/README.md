# CPU 1.7B Identity Diagnostic: Transport PASS, Simulator Unqualified

Eleven unchanged original metadata files from native GitHub-hosted Linux/amd64
[workflow run 40174257318, attempt 1](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/40174257318).
Exact producer: `19ece8918ed4f89121bbe704bf5466559e3805a1`, with the explicit
`identity_followup_17b` profile. The original scorer records
2026-10-25 08:01:38-08:02:01 UTC; this is not a per-request latency measurement.

## Observations and Limits

| Fixed step | Prompt tokens | Completion tokens | Email-like literal | Expected first name / last name / ZIP literals |
|---|---:|---:|---|---|
| Initial | 382 | 10 | false | false / false / false |
| Email question | 411 | 17 | false | false / false / false |
| Name/ZIP question | 450 | 26 | false | true / true / true |

The pinned Qwen3-1.7B completed three simulator diagnostic requests: **1,243
prompt and 53 completion tokens**, zero agent/evaluator diagnostic calls,
zero retry. The shared CPU owner retains its original role names: its
`evaluator` slot runs Qwen3-1.7B and is explicitly mapped to this diagnostic's
`simulator`. The original `evaluator-runtime.json` is not relabeled. The
Qwen3-0.6B owner slot also starts and receives prerequisite parity probes;
those are not additional identity-diagnostic calls or evaluator qualification.

The 1.7B binding joins the exact model/revision, runtime, process, model-files,
tokenizer and framing hashes. Controller process/producer, owner
container/image/build, CPU processes and scorer cleanup are confirmed in the
original receipts. The fixed contract remains three turns, 4K context with a
256-token output reserve, 120-second request / 180-second trial / 300-second
diagnostic bounds, concurrency one and no retries.

**PASS means bounded transport completion, not semantic correctness or a
qualified simulator.** The third reply contained the expected name/ZIP
literals; the first two contained none. No reply matched the bounded ASCII
email-like pattern. This does not establish the meaning or accuracy of a reply,
absence of every email representation, or the cause of earlier tau failures.
Model text was not retained and cannot be reconstructed from these receipts.
There is no candidate inference, reward, full tau result, qualified
simulator/evaluator, live admission, public service or confidential claim.
All corresponding original flags remain false. The earlier
[0.6B diagnostic](../2026-10-25-tau-cpu-identity-followup/README.md) and failed
tau trials remain unchanged; this is not a controlled quality comparison.

## Original Bytes and Source Joins

Only paths were assembled from the two downloaded artifact directories:

- `tau-cpu-session-40174257318-1`: root `run.json`, `output.log`, and six
  `pair/` owner/readiness files retain their relative paths.
- `tau-cpu-support-probe-40174257318-1`: `probe-inputs/setup.json` becomes
  `setup.json`; `proof/pair/scorer/{run.json,output.log}` become `pair/scorer/`.

No JSON was reserialized. All eleven per-file SHA-256 hashes are fixed in
[the replay tests](../../../scripts/test_tau_cpu_identity_17b_evidence.py).
The `output.log` files are structured metadata, not model transcripts.
Private paths, workflow logs, prepared payloads and model text are not published.
Any scanner exceptions are restricted to exact public hash, field and evidence
path, with wrong-value/field/path and same-line secret controls.

Plan SHA-256: `48952e2805c3c5cbbb0ef4861ac4b1035b945d57502ee2a3b9096da591344a87`.
The setup receipt binds the pinned upstream archive, runtime lock and 74
locked wheels; its `upstream_executed=false` describes preparation, not the
later scorer execution. Tests check producer ancestry, all 28 owner / 33
controller / 22 scorer source hashes, original readiness and terminal receipt
joins, the explicit owner-to-simulator mapping, and rejection of mutated
identity/cleanup/content/qualification fields. Only freshness is replayed at
the original time; tests make no model or endpoint calls.

See the [1.7B diagnostic contract](../../../docs/tasks/p2-t4-tau-cpu-identity-17b.md).
