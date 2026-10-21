# P2.T3 scripted response model boundary

This fixes one adapter defect after the immutable-image create fix. It does not
enable a model endpoint, change upstream scoring, or establish a successful
scripted trial. The parent owns any subsequent reviewed runtime execution.

## Original failure

The unchanged [run envelope](../../evidence/p2/2026-10-20-tau-scripted-model-missing-7d21a55/run.json)
and [content-free output](../../evidence/p2/2026-10-20-tau-scripted-model-missing-7d21a55/output.log)
were produced by `7d21a5582050c4d532eb6bb6c6cd5bcfa75b6fae` on
2026-10-20, 21:50:35-21:50:45 UTC. Container creation and upstream execution
occurred. The trial failed with `infrastructure_error`, after one simulator
request and no agent/evaluator requests. Synthetic usage remained known: one
input and one output token, not a tokenizer or spend measurement. Cleanup was
confirmed. `completed:true` describes report completion, not trial success.

An earlier invocation supplied the bundle root rather than `/payload` and was
rejected before container creation. It is not the preserved runtime attempt;
no run envelope or timestamps are invented for that preflight rejection.

Original SHA-256 values:

- `run.json`: `1d4f2d59c99cde9309c967f7b811b0695f831f0e7e20bebb3a0756d03731d245`
- `output.log`: `7ecde99657580c32ce28d1a343cbb0f8b5fb765e7e4a9bd7c4e80863f71c78ea`

## Source-backed defect and correction

At tau `fc0055dc4e0a316c3f83133267fbd6faaa770992`,
[get_response_cost](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/utils/llm_utils.py#L119)
regex-parses `response.model` before its exception handler and before calling
`completion_cost`. The file SHA-256 is
`8c1f1c297e879b2a2d48ac65394cdd497bbd10f42fefa0ce770e7a65ce42bf95`.

The unchanged [runtime lock](../../bench/tau-runtime-lock.json) pins LiteLLM
1.81.11 wheel SHA-256
`06a66c24742e082ddd2813c87f40f5c12fe7baa73ce1f9457eaf453dc44a0f65`.
Its `litellm/types/utils.py` SHA-256 is
`8f72dfab8107ad66d5af7d7cf06602b6471c0d7b3dcb105663c5a29652332190`.
`ModelResponse.__init__` defaults `model=None` and retains that value. The old
adapter supplied only choices/usage, so the subsequent regex receives None
and raises TypeError. Patching the cost callback alone cannot prevent this.
These facts were checked from authenticated source/wheel bytes as data, not
by importing or executing the packages. The original report retains no
exception trace, so this deterministic defect is consistent with the failure,
not a claim that its exact historical exception was observed.

The response constructor now receives the same fixed scripted role alias
already validated by Bridge. This is not a physical model identity. Choices,
usage, sole accounting ledger, evaluator behavior, unknown-role rejection,
logging suppression and runtime isolation remain unchanged. No raw error or
content diagnostic is added.

Tests use an attribute-bearing response fixture with the pinned None default
and cost-parse boundary. All three aliases, tool response preservation and
unknown-role rejection are covered. The original artifact regression verifies
all 14 historical toolkit source hashes, producer binding and exact envelope
reconstruction; it does not replace the original producer with this fix.

```sh
python3 -B -m unittest discover -s scripts -p 'test_tau_trial.py'
make check
```

No new image, package, model or endpoint execution is part of these tests.
Live transport, quality acceptance and confidential qualification remain absent.
