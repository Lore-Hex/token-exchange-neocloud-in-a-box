# Selected Checkpoint On Persistent Storage

These original receipts are copied byte for byte from producer
`bca89c19f5e15ce0989ee34eac98b94afd5cd822`. They establish storage preparation,
not inference or confidential qualification.

- `admission.json`: the private virtiofs directory passed ownership,
  lock-contention, no-replace hardlink and file/directory fsync checks.
  It checked no model bytes or capacity and explicitly remains mutable.
- `run.json` and `output.log`: the bounded eight-worker staging operation
  completed from 07:57:40 to 09:16:46 UTC on October 24. All 79 files and
  510,311,056,155 bytes matched the selected manifest, with no reused files.
  Process cleanup was confirmed within the original 5,400-second bound.

The selected checkpoint is
`dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8`, revision
`c19ca5ca4eaeb79d29fdf08aea8327da95b1116a`, manifest
`5c6891a9649702b1ac0327b945600acb40c571a069942517cf14ba661a3cfcbc`.

The final proof is separate from `last_observation`, whose progress counts
correctly retain `verified=false`. The filesystem admission does not prove
power-loss durability or future availability. No runtime, immutable storage,
API conformance, model-quality, throughput or attestation claim follows.
Future execution must freshly verify the complete set again.
