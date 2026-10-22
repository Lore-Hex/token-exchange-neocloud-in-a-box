# P2.T3 pinned tokenizer request materialization

Status: opt-in executed-code correction and CPU controls implemented; actual
fixed-image execution and GPU parity rerun are separate gates. Not qualification,
quality, confidential success, boot validation, or complete tokenizer equivalence.

## Observed baseline and primary sources

The original local exact-image CPU diagnostic at producer
`39851d259c126ce6bf9cb696040834a4e3dc44a1` is preserved in
[run.json](../../evidence/p2/2026-10-22-tokenizer-continuation-local-39851d2/run.json). Its
[output.log](../../evidence/p2/2026-10-22-tokenizer-continuation-local-39851d2/output.log)
reached checkpoint 15, then recorded `type` at source
`fff82623f5676021bd93766e8ec7106ff0384181b83ab4f2aa3a492ffa0b3f1f`
line 23 followed by `copy.py` frames. Cleanup was confirmed. The original bytes
are preserved, including their failure status. This confirms the exact CPU
request/parser/deep-copy failure, not the unretained exception on the earlier
GPU HTTP failure. It does not execute BPE, the renderer, or HTTP serving.

The separate native CPU run `39795135328` did **not** reach entry: its
16,982 ms preflight plus 703,019 ms application exhausted the existing 720-second
application ceiling. It is an application-timeout failure, not a tokenizer
failure or independent confirmation of the local TypeError. No bounds change.

The first fixed control at producer `34f7968c5239354e2f2fba58ef1294874e9b2783`
is also preserved in [run.json](../../evidence/p2/2026-10-22-tokenizer-materialization-local-34f7968/run.json)
and [output.log](../../evidence/p2/2026-10-22-tokenizer-materialization-local-34f7968/output.log).
It constructed all four requests and passed list/idempotence checks, but failed
at checkpoint 12: the diagnostic's negative **Python generator** input was
accepted. This is not an HTTP JSON input or a parity pass. The corrected negative
control supplies a JSON array with an invalid second item, allowing Pydantic to
create its own validator iterator, as on the actual endpoint. A two-valid-item
control additionally checks that no later tool call disappears. The server
adapter and the four original parity inputs are unchanged by this control fix.

All upstream sources below are pinned to
`5df63a7203cdb9328d824a27b682e77c7b9928be`:

- [TokenizeChatRequest](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/serve/tokenize/protocol.py)
  lacks the after-validator that materializes validated tool calls.
- [ChatCompletionRequest](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/openai/chat_completion/protocol.py#L581-L598)
  already provides `_materialize_tool_calls_after` for Pydantic's lazy iterable.
- [Generic parser](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/renderers/chat_utils.py)
  consumes the tool-call iterator into a separate parsed conversation.
- [DeepSeek renderer](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/renderers/deepseek_v4.py)
  passes the original messages to the
  [normalizer](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/tokenizers/deepseek_v41.py#L23),
  whose deep copy fails on the ValidatorIterator. Merely converting JSON
  arguments into dictionaries is not the failure mechanism.

## Narrow correction

The existing sealed lab loader checks the complete target and delegate source
hashes and sizes before executing them. For the opted-in TokenizeChatRequest
only, its AST extends one existing import and adds one `mode="after"` validator
**before Pydantic constructs the class**. The wrapper delegates to the existing
ChatCompletionRequest method; it does not duplicate normalization logic or
change the client payloads. Every call checks identities before and in `finally`.

The guard verifies selected source origins, loader/module/class identities,
delegate and wrapper code against compiled references, globals and used builtin
bindings, and the Pydantic validator/decorator identities. Reload, substitution,
source drift, alias collisions and sticky failure state reject. This is an
explicit compatibility adapter, not a sandbox against arbitrary Python code.

`tokenizer_materialization` binds target, delegate, wrapper and method identity
into the source-bound contract. The complete loader source is also bound by
the payload and source-bundle hashes. CPU `adapter_verified=true` requires the
fixed control child to verify active identities before checkpoint 16, clean exit,
unchanged finder state, and no exception. The GPU receipt's identity is only
**contract-bound adapter configuration**, not an observed activation receipt.
An actual full parity rerun is still required.

The original five-stage application, disk inventory digests, immutable image,
model pack, four parity payloads, default DP1 behavior, network policy, resource
limits, request budgets and cleanup are unchanged. The baseline diagnostic still
requires the lazy iterator and preserves its TypeError; it cannot silently pass
through the fixed control path. No historical evidence is rewritten.

## Explicit commands

After committing the producer, prepare or run only the bounded CPU mode:

```sh
python3 -I -S scripts/record-engine-cpu-entry.py \
  --prepare /private/tmp/nib-tokenizer-fixed-payload \
  --tokenizer-continuation --tokenizer-materialization
python3 -I -S scripts/record-engine-cpu-entry.py \
  --slug tokenizer-materialization-local \
  --tokenizer-continuation --tokenizer-materialization
```

The cached immutable image and existing verified raw OCI metadata are required.
The `engine-cpu-entry.yml` workflow adds a default-false boolean
`tokenizer_materialization`; it requires `tokenizer_continuation=true` and is
forwarded identically to record and artifact validation. Main-only execution and
all safety bounds remain unchanged. A CPU pass leaves overall `result=fail`,
`BLOCKED`, `deployable=false` and `confidential=false`.

The fixed CPU path uses all four unchanged probes, compares raw and validated
encoding before and after both generic parser routes, retains assistant null
content and the tool call, checks idempotence, and rejects a later invalid
JSON-array item through the real Pydantic ValidationError. No body, message,
token ID, rendered prompt, exception text or private path is logged.

For a separately approved GPU parity rerun, add `--tokenizer-materialization`
to the existing `--tokenizer-parity` preparation command. Neither option is a
runtime override; the prepared receipt binds the selection. The flag is denied
outside the B300 CC correctness/parity profile. Hardware execution, approval and
the final parity evidence remain separate requirements.

Focused software checks:

```sh
python3 -B -m unittest discover -s scripts -p 'test*tokenizer*.py'
python3 -B -m unittest discover -s scripts -p test_engine_cpu_entry.py
python3 -B -m unittest discover -s scripts -p test_deepseek_lab.py
```

The unit loader/Pydantic fixtures are explicitly synthetic. They cover default
compatibility, fail-closed pins, real isolated source-loader routing, sticky
reload denial, identity drift, receipt binding, late validation and unchanged
container budgets. They are not substituted for actual pinned-image controls.
