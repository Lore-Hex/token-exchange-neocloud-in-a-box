# Verified ordinary-node checkpoint staging

The original recorder artifacts report a complete staging pass from
2026-10-24 00:07:25 to 01:02:39 UTC. Producer
`323ab0eedacee474e5b12fcc80a169ef43400075` used eight download workers and a
7,200-second deadline. All 79 files / 510,311,056,155 bytes matched the pinned
`dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8` revision
`c19ca5ca4eaeb79d29fdf08aea8327da95b1116a` and manifest
`5c6891a9649702b1ac0327b945600acb40c571a069942517cf14ba661a3cfcbc`.
No files were reused. Local process-group cleanup was confirmed.

These bytes were staged into temporary RAM-backed storage for the separately
approved ordinary-GPU test. This receipt is not a persistent backup, a model
startup, an inference result or a confidential-computing result. The separate
[retained cloud upload](../../p2/2026-10-23-dealign-cache-upload/receipt.json)
records the same complete checkpoint on persistent storage; that earlier receipt
does not prove a fresh rehash of that storage today. The original upstream
checkpoint was not substituted for the selected variant.

Both files below are unchanged outputs from the committed recorder:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `run.json` | 1086 | `be1bda549c34db368bac8647850d63b0d2c4433426a222ff8d5e47f634047eee` |
| `output.log` | 1726 | `0bca7e41b985d4df4906920c4dff4a40b7a79fcbd13882fef823de5bdc9a3bbe` |

The final `proof` is distinct from `last_observation`, whose `verified:false`
correctly describes unverified progress. Hardware validation, runtime readiness
and confidential qualification remain false. No inference or benchmark claims
follow from this staging pass.
