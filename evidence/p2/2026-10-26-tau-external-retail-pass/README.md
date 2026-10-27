# Live original tau2 retail task

Producer `4fb3ddc637893ea41126f4f4b2cb57510393b598` ran the pinned
tau2 revision `fc0055dc4e0a316c3f83133267fbd6faaa770992` retail task 2,
trial 0, seed 42, with the original task, tools and ALL_WITH_NL_ASSERTIONS scorer.
The simulator and evaluator were `gpt-4.1-2025-04-14`; the agent was the
selected `dealignai/deepseek-v4.1-flash-1017-uncensored-fp8` checkpoint.

The task scored **reward 1.0**, with normal user-stop termination and zero tool
errors: 14 agent, five simulator and one evaluator requests in 57.241 seconds.
All calls returned complete, accountable usage; no retries or unknown responses.
The owned local scorer container was removed. The retained GPU service was not
restarted or deleted.

The agent consumed 101,324 input and 1,431 output tokens; support roles consumed
9,001 input and 374 output tokens. The receipt's $0.08892955 is a token-price
estimate including operator-declared candidate rates, not measured GPU cost or
an invoice. Six support counting calls conservatively reserved $12.570912
separately; this is not a claim that those calls were charged that amount.

The v2 endpoint contract uses certificate-pinned ordinary TLS and retains the
actual `long1m-graphs` serving configuration. This trial deliberately stayed
within 32K context and 2,048 output tokens per call. It neither changes the
service's 1M combined context nor establishes full-context reasoning quality.

Preserved failed prerequisites:

- [Missing local pinned tokenizer source](../2026-10-26-tau-external-preflight-missing-source/output.log):
  external preflight stopped before endpoint initialization.
- [Seed transport mismatch](../2026-10-26-tau-external-simulator-seed-failure/output.log):
  zero generations, rejected the original simulator's seed before counting.
  Chat Completions fixed this without dropping the seed or modifying messages.
- An earlier producer `bc125a4f1248c7a240b6f224eff158ad592c5a88` had an evidence
  writer contract mismatch (`observation.json` versus the standard evidence pair).
  It did not produce a usable receipt and is not used as endpoint evidence.

One task is not the complete retail/airline/telecom suite. No matched baseline,
full tau2 pass^k, broad quality acceptance, production listing, or confidential
claim follows. See [run.json](run.json) and the content-free [output.log](output.log).
