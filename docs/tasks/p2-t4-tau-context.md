# Selected DeepSeek: 8K Tau Prerequisite

Status: software implementation, not a GPU measurement or a tau result.
The fixed `tau8k-eager` profile enables an ordinary, selected-weight B300
TP4 x DP2 tokenizer/runtime prerequisite. It does not execute the tau scorer.

The existing task prefix measured 4,785 tokens before its first user turn.
With a 256-token output reservation that is already 5,041 tokens, exceeding
the old 4,096-token lab context. An 8,192-token limit clears this lower bound,
but does not prove a whole multi-turn trial fits. Never truncate the task or
silently change the benchmark to fit a limit. Every later turn still needs a
fresh count and budget check through the existing process owners.

The only engine changes are `--max-model-len 8192` and
`--max-num-batched-tokens 8192`. One sequence, eager execution, GPU-resident
Engram, image and weight pins, 512 GiB memory/no extra swap, closed networking,
deadlines and owner cleanup remain unchanged. The pinned vLLM
[`SchedulerConfig.verify_max_model_len`](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/config/scheduler.py#L314)
rejects a smaller batch-token budget when chunked prefill is disabled.
That source is re-hashed before engine startup. This is a bounded experiment,
not a recommended production context or a capacity guarantee.

From a clean reviewed commit, prepare a separately approved run:

```sh
python3 -I -S -B host/record-deepseek-lab.py \
  --prepare-nonconfidential-uncensored-b300-dp2 "$PRIVATE_BUNDLE" \
  --model-profile uncensored --execution-profile tau8k-eager \
  --tokenizer-parity --tokenizer-materialization --runtime-observation
```

All three prerequisite options are mandatory. Streaming and provider checker
combinations are rejected. The profile is restricted to the selected
uncensored checkpoint on ordinary eight-B300 hardware; no confidential or
original-weight path is broadened.

Four fixed tokenizer cases compare local token hashes, engine `/tokenize`
hashes and completion usage. `/tokenize` must report exactly 8,192 context.
Its v2 receipt binds that limit and retains `tau_acceptance=false`,
`quality_accepted=false`, and `confidential=false`. A 4K receipt cannot satisfy
an 8K validation, nor vice versa. Existing 4K default receipt bytes and engine
arguments are unchanged. These short controls are not a long-context test.

Still required: a live 8K prerequisite run, a fresh selected-model prefix
measurement, live wiring of the [process-owner channel](p2-t4-tau-owner-channel.md), and actual bounded tau
trials with distinct pinned CPU user/verifier models. Keep original benchmark
qualification separate from an operational selected-weight trial.

Focused tests: `test_deepseek_tuning.py`, `test_engine_tokenizer_parity.py`,
`test_tokenizer_materialization.py`, `test_tau_cpu_parity.py` and
`test_deepseek_model_profile.py`. Full gate: `make check` plus hosted CI and
independent final-head review.
