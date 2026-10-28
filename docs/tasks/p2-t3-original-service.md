# Official DeepSeek Service Migration

Status: API guard support only; no image, weights, deployment or hardware
qualification is changed by this implementation.

`nib-pilot-api --profile original-deepseek-million` selects the canonical model
ID `deepseek/deepseek-v4.1-flash-1017` from the
[official pack](../../models/deepseek-v4.1-flash-1017/1/pack.yaml). That pack pins
`deepseek-ai/DeepSeek-V4.1-Flash-1017` revision
`1bae0e1088993b9b82de08f59de5be5a34e42170`. It is not an alias for the selected
abliterated checkpoint, whose existing profiles remain unchanged.

The guard reuses the million-token policy: 1,048,576 combined tokens, 393,216
maximum output, 2,048 default output, one global admission, a 16 MiB request
bound, the existing 3,540-second deadline and drain reserve, isolated cache
namespaces and replica affinity. These are configured software ceilings, not
new measured capabilities. The guard does not verify weight identity itself.
It forwards the canonical model ID unchanged and rejects both the selected
model ID and bare upstream aliases. Cached usage is passed through unchanged;
explicit cache pricing and finite retirement checks remain in force.

Before a real switch:

1. Independently review an official-pack-bound owner and prepared image. The
   current selected-service Python owner, image preparer, tokenizer fixtures
   and evaluation callers still bind the selected checkpoint. This guard does
   not make them compatible, and mutating their constants at runtime is not a
   supported migration.
2. Verify every official weight file against the pinned manifest. Stage a
   separate copy only with sufficient storage and reconciled budget. Preserve
   the current service and rollback inputs until a reviewed drain/cutover.
3. Validate the new engine's exact model ID, tokenizer, streaming usage, tools,
   output/context bounds, cache behavior and code-scored quality. Preserve
   failures and actual usage. Old selected-model results are not a reference
   score or qualification for the official model.
4. Coordinate catalog withdrawal/addition and normal marketplace gates so old
   requests cannot silently select different weights. Verify the serving route
   and its customer ledger after cutover; do not reuse an expired lifecycle.

No tariff, ranking, privacy claim or model listing is changed here. Ordinary
TLS and zero retention are not confidential computing. Hardware attestation
and the remaining phase exits still require their own evidence.
