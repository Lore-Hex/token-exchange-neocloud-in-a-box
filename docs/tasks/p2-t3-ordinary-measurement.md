# Ordinary B300 streaming measurements

Status: implemented and tested with synthetic fixtures only. This change is
not a hardware result, measured throughput, quality score, public endpoint or
confidential qualification.

The explicit ordinary B300 TP4 x DP2 profile can now opt into the existing
tokenizer-parity, owned-process observation and fixed streaming-cohort paths.
It reuses their source-bound workers, exact token-count fixture, identity
checks, deadlines, usage accounting and cleanup; no second benchmark runner
or alternate inference implementation is introduced.

After baseline correctness succeeds and a separate bounded run is approved,
prepare from a clean reviewed commit:

```sh
python3 -I -S -B host/record-deepseek-lab.py \
  --prepare-nonconfidential-uncensored-b300-dp2 "$PRIVATE_BUNDLE" \
  --model-profile uncensored \
  --tokenizer-parity --tokenizer-materialization --runtime-observation \
  --stream-cohort --stream-profile chat-2k-512-c1-600s
```

The checkpoint remains `dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8`
at revision `c19ca5ca4eaeb79d29fdf08aea8327da95b1116a`. The ordinary profile
must be explicitly selected during preparation; no runtime switch or automatic
confidential-to-ordinary fallback exists. The default correctness preparation
is unchanged. Original checkpoints, B200 ordinary profiles, single replicas,
unknown security modes and provider-check integration are not admitted.
The separate [tuning integration](p2-t4-tuning-measurements.md) admits only the
complete measurement option set with the fixed ordinary execution profiles.

Run the prepared bundle with the existing [bounded host recorder](p2-t3-deepseek-lab.md).
Use an independently retained builder-receipt digest, fully verified weights,
a fresh evidence directory and an explicit UTC deadline no more than 80 minutes
away. Public-cache filesystem admission does not replace model hash checks.

## What Is Measured

- Four fixed tokenizer cases compare local token IDs with the owned API and
  generated usage before streaming begins.
- One warmup and eight measured requests use 2,048 input tokens and at most
  512 output tokens, at concurrency one. The explicit profile gives the cohort
  600 seconds; absent that profile, the original 180-second window remains.
- Process identity and listener ownership are checked before and after the
  workload; unknown usage is never treated as zero. Prompts and responses are
  not logged.
- All eight devices must affirmatively report CC disabled. The container keeps
  a 512 GiB memory-and-swap cap, 4,096-token context, one sequence per replica,
  GPU-resident Engram, isolated networking and unchanged teardown checks.

Outputs stay `confidential=false` and `deployable=false`. The original
confidential hardware validator still rejects these devices. Any reported
throughput belongs to this exact ordinary profile, not a confidential one.
This is not the concurrency sweep, tau2 quality evaluation, production
conformance or sustained serving acceptance required by the full plan.

## Checks

`test_deepseek_model_profile.py` exercises prepare/CLI admission, source-bound
payloads, runtime validation, unchanged launch isolation, malformed target
rejection, zero-swap enforcement and denial of confidential claims. Existing
stream/tokenizer/process-observation tests cover the reused workers.
The CPU parity test retains its exact helper-source digest and the separate
historical byte comparison excluding only GPU eligibility. Its digest changes
with this eligibility update; the fixed probes and semantic checks do not.

```sh
python3.13 -B -m unittest discover -s scripts -p 'test_deepseek_model_profile.py' -v
make check
```
