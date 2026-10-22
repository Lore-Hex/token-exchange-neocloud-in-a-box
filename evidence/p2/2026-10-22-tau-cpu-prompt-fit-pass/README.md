# Original CPU Tau Prompt/Parser Probe

Native GitHub-hosted workflow [39873102642](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39873102642)
ran once at producer `4fe0bd7df8f6ce3293e6dca609106b37dd9676a3`, using the
reviewed main-only `support_probe=true` path. The controller ran from
2026-10-22 23:39:53Z through 23:44:14Z. The original scorer reports **PASS**,
`evaluated`, with confirmed bound release and owned cleanup.

This is original prompt and parser compatibility for one fixed task using two
real CPU support models and a **scripted target**. **No DeepSeek execution, full
tau score, reference comparison, quality, live admission or confidentiality**
is established. The original software evidence keeps `hardware_validated=false`;
all qualification fields remain false and pricing is `not-measured`.

## Measured Result

Pinned tau2 `fc0055dc4e0a316c3f83133267fbd6faaa770992`, retail base task `2`,
trial 0, seed 42: the original builder constructs the simulator prompt and the
original NL evaluator parses the resulting trajectory. No prompt shortening,
output repair, retry, budget increase or substitute support response was used.

| Role | Model | Prompt tokens | Output tokens | Result |
| --- | --- | ---: | ---: | --- |
| Simulator | pinned Qwen3-0.6B Q8_0 | 382 | 24 | pass |
| Evaluator | pinned Qwen3-1.7B Q8_0 | 281 | 88 | pass |

Role counts are exactly one simulator, one scripted agent and one evaluator.
Only the two support completions are actual model generations in this probe;
the scripted agent is not a target-context or quality test. The earlier parity
stage separately passed all four unchanged probes for each model, with counts
20, 28, 160 and 198. Its eight one-token generations are not included in the
three-request probe budget.

The scorer interval was 23:43:57Z through 23:44:13Z (16 seconds, including scorer
setup/cleanup, **not** per-model inference latency). Both prompts plus the fixed
256-token output reservation fit the unchanged 4096-token context. Three requests,
three turns, no retries, 120 seconds per request, 180 seconds per trial and
300 seconds per scorer operation remained fixed. The CPU owner kept the existing
4-CPU/8-GiB/no-swap, network-none, read-only, no-capability profile.

The original process/endpoint identities are distinct; READY binds four fresh
CPU runtime/tokenizer receipts. The second metadata frame binds the scorer,
plan and READY. Controller, producer, CPU worker and scorer cleanup are confirmed;
the owned CPU servers stopped. These historical receipts cannot admit a new run.
Reward and model text are not published, and parser success does not establish
that these small support models are suitable quality judges.

## Originals and Replay

All eleven JSON/log files are byte-identical downloaded originals, not reconstructed
summaries. The `tau-cpu-session-39873102642-1` artifact supplies root `run.json`,
`output.log` and six `pair/` files. The `tau-cpu-support-probe-39873102642-1`
artifact supplies `probe-inputs/setup.json` (stored here as `setup.json`) and
`proof/pair/scorer/{run.json,output.log}` (stored under `pair/scorer/`). No model,
wheel, task body, payload, pipe or request/response body is published.

The [evidence regression](../../../scripts/test_tau_cpu_probe_evidence.py)
hash-binds every original, verifies all historical source closures, reconstructs
the exact original READY frame, and reuses production receipt/diagnostic validators.
Only wall time is set to the recorded observation time for historical freshness
replay; source checks are not bypassed. Mutated provenance, expiry, scorer claims,
release and cleanup are rejected. The same historical receipt rejects after expiry.

Actual offline preparation from the exact producer, pinned original tau archive
and existing locked wheelhouse reproduced plan
`06247d35d8022a7f91fcb64d52c138baadb4567f58adabaa4e73251e621d52de` across 9379
payload files and 74 selected wheels. That replay executed no upstream/model code.
The [implemented probe](../../../docs/tasks/p2-t3-tau-cpu-prompt-fit.md) describes
the unchanged source, confinement and resource contract. The earlier failed
held-pair attempt remains preserved; its cause is still unknown, not fixed by
this successful run. A real target trial still needs separately authorized
mixed-runtime lifecycle/namespace integration and fresh target receipts.
