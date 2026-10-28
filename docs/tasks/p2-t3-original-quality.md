# P2.T3: Official-model quality calibration

Status: implementation and synthetic tests, not a new score or live qualification.
The calibration runner can target the official checkpoint separately from the
historical selected-model runs. This does not authorize inference or spending.

`scripts/record-quality-calibration.py` accepts the existing private, hash-bound
contract input. A new `nib.quality-calibration-endpoint/v2` contract requires:

- Model `deepseek/deepseek-v4.1-flash-1017` and serving profile
  `original-long1m-graphs`.
- The complete `model_profile` returned by `deepseek_model_profile.select("original")`,
  including the fixed official revision and full manifest SHA-256.
- The same system-root TLS, explicit public address, port 443, declared 1M
  serving context and absolute, bounded retirement time as the existing public
  contract. No TLS downgrade, arbitrary model or alternate upstream is admitted.

The existing public v1 and pinned legacy contracts retain their original selected
model behavior. Official-model requests and response model checks use the same
new identity. They do not silently opt into an old selected-model contract.

## Evidence And Scoring

New official runs emit `nib.quality-calibration/v4` receipts and v4 summaries with
the model, serving profile and complete pinned checkpoint identity. Interrupted
official runs emit v2 interruption audits with that same identity. Historical
v1-v3 receipts retain their existing interpretation; no old result is relabelled.
Receipt metadata is not proof of deployed weight bytes or hardware attestation.

The tokenizer files in both pinned model manifests have identical sizes and
SHA-256 values, checked by regression tests. The existing verified tokenizer,
formatters and code-based GSM8K, MMLU-Pro and IFEval scorers are reused unchanged.
Fresh serving-side tokenizer parity is still required before a live evaluation.

Serial requests, no retries, 8,192 maximum generated tokens, 32,768-token test
context, 119-second request bound and one-hour run bound remain unchanged.
The full expected denominator, failures, truncation, durable pre-send intents,
known total/cache usage and unknown-usage counts remain visible. No external
judge, simulator or GPT API is called. These calibration samples do not satisfy
the separate frozen lm-eval matched-reference protocol or full tau validation.

Before a live run: verify staging and actual service identity, reconcile the
aggregate budget, review the bounded run plan, prepare the pinned dependencies
and save receipts durably. Neither a successful software test nor a numeric
receipt proves confidential inference or completion of P2/P5.
