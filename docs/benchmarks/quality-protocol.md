# Frozen Quality-Smoke Inputs

Status: offline input verification only. This is not an inference runner, a score,
or P2 completion. No model requests, provider credentials, or network access are
used by `scripts/quality_protocol.py`.

The [pin file](../../bench/quality-upstream-pins.json) records the public datasets
and task implementation examined on October 25, 2026:

- [lm-eval v0.4.13](https://github.com/EleutherAI/lm-evaluation-harness/tree/ddd67220430a2470529f25fd5c05a576ca1057a0),
  including its existing evaluator, task loader, GSM8K task, and all MMLU-Pro
  subject definitions. These hashes verify the inspected files, not the entire
  harness dependency closure. Use lm-eval for scoring; do not write a second
  answer parser or silently substitute tasks.
- [GSM8K](https://huggingface.co/datasets/openai/gsm8k/tree/740312add88f781978c0658806c59bc2815b9866/main):
  main configuration, 1,319 test rows, 7,473 training rows for few-shot examples.
- [MMLU-Pro](https://huggingface.co/datasets/TIGER-Lab/MMLU-Pro/tree/b189ec765aa7ed75c8acfea42df31fdae71f97be/data):
  12,032 test rows, 70 validation rows for few-shot examples.

Dataset member sizes and SHA-256 values come from each revision's Hugging Face
tree API `lfs.oid` and are checked against the local complete Parquet files.
Dataset row counts are declarations from their pinned cards, not a fresh
Parquet decode by this utility; the eventual runtime must verify those counts.
Download public inputs separately. No download is hidden in preparation.

## Identical Questions and Settings

For each dataset, rank all zero-based global test-row indices by the SHA-256 of
`nib-quality-smoke-v1`, dataset name, immutable dataset revision, and decimal
index, separated by NUL bytes. Select the lowest 200 hashes and emit indices in
ascending row order. This freezes an answer-independent sample without relying
on Python's random implementation. It is 200 MMLU-Pro questions in total, not
200 per subject. Preserve these global indices when mapping rows to lm-eval's
subject-local `samples` lists; never feed global indices directly to a filtered
subject task. No subject-balance claim is made.

Both candidate and reference must use those same rows, five few-shot examples,
few-shot seed 1234, greedy decoding, and 2,048 maximum generated tokens. MMLU-Pro
already specifies 2,048 upstream; GSM8K gets this explicit shared cap instead
of an adapter-dependent default. Preserve upstream prompts, stop sequences,
filters and metrics. Use GSM8K strict-match as the primary metric, and retain
flexible extraction only as a separately labelled secondary metric. Candidate
and reference must use the same documented chat-template policy.

Preparation rejects a declared output capacity below 2,048. That declaration is
not an endpoint probe. Context fit, actual output capacity, runtime dependency
locking, reference-model identity and bounded transport still require their
own preflight. No request or response caching or sample-content logging should
be enabled. A generation ending at its token limit must remain visible in
aggregate truncation counts; do not hide it by regenerating that question.

## Local Preparation

Place the exact harness source at one local root. Under a separate dataset root,
put the listed files beneath `gsm8k/` and `mmlu_pro/`, preserving their pinned
repository-relative paths. Then run:

```sh
python scripts/quality_protocol.py --harness-root /local/lm-eval \
  --dataset-root /local/quality-data --max-output-tokens 2048
```

The JSON contains only pins, indices, settings and explicit incomplete flags.
It includes neither questions, answers, model output nor local paths. Inputs
are not executed. Missing, altered, empty, oversized or symlink members fail.

## Remaining Gates

Implement and review the lm-eval adapter, complete source/dependency lock,
subject-index mapping, context counting, time/request/cost budgets, actual usage,
truncation/error accounting and private evidence export before live evaluation.
The selected abliterated checkpoint is not the original DeepSeek revision:
comparing it to a different first-party checkpoint is diagnostic, not proof of
the plan's matched-revision tolerance. For a genuinely matched pair, report the
paired accuracy difference and uncertainty, not just whether it lies within two
percentage points. Two hundred questions cannot establish universal model
quality, even when the smoke passes. Full tau and confidential reruns remain
separate requirements.
