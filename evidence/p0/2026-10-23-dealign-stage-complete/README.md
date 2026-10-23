# Complete Dealign Checkpoint Stage

The original [run.json](run.json) and [output.log](output.log) record one
successful public-byte stage using producer
`3dbbd3aafc5e26adaac5bc4ebfc48a7fb4c4140d`. Both files are preserved byte for
byte, including the producer's private-path placeholders and software-only
classification.

From 2026-10-23 09:28:00 to 11:01:56 UTC (5,636 seconds), the terminal proof
verified all 79 files / 510,311,056,155 bytes for
`dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8` at
`c19ca5ca4eaeb79d29fdf08aea8327da95b1116a`. The manifest SHA-256 is
`5c6891a9649702b1ac0327b945600acb40c571a069942517cf14ba661a3cfcbc`.

The stage reused 48 files and downloaded 199,573,065,758 bytes. This agrees
with the earlier [partial seed](../../p2/2026-10-23-dealign-partial-seed/README.md):
310,737,990,397 seeded bytes plus those downloaded bytes equals the complete
pack. The seed receipt remains partial; this separate terminal proof records
full staging. Progress observations remain explicitly `verified=false`, even
when their counts reach 79 files. The last observation's 5,616-second elapsed
counter is not the full run's wall-clock duration. Neither is substituted for
the terminal proof.

The unchanged limits were eight download workers, 10,800 seconds and
510,311,056,234 HTTP body bytes. The capacity check recorded 618,593,964,032
available bytes against 561,342,161,771 required bytes. Cleanup is confirmed
only for the local process group; it does not attest to cloud deletion,
unmounting or removal of the retained model files.

## Preservation And Replay

| Original | Bytes | SHA-256 |
| --- | ---: | --- |
| `run.json` | 1,087 | `1e9b01e39b67f47bcbc4ae4bed63aed838c2a7fdefd74186c0edce3bcb91ca95` |
| `output.log` | 1,727 | `ab2b4c8af5544ff0cc76c34c0ce35a47759d219a4c1711e1b639971fe9f82e7a` |

[Focused tests](../../../scripts/test_dealign_stage_evidence.py) pin these
bytes, load the exact historical recorder and its six pinned builder helpers,
validate the fixed profile and manifest, recompute the reported Go source
closure digest, and replay the original plan/proof validators with negative
controls. These tests do not download or rehash the 510 GB model, rebuild the
recorded binary, or reconstruct the offline bundle receipt. The binary and
bundle receipt hashes are preserved as reported identities, not independently
reproduced build results.

## Limits

This is complete local public-file staging, not proof of durable NFS upload,
backup, restore, immutable storage, engine readiness, inference, tokenizer
parity, quality, hardware validation or confidential qualification. Upload is
a separate operation and no upload result is claimed here. The original
`runtime_ready`, `hardware_validated` and `confidential_qualified` fields all
remain false. No private host endpoint or model content is added.
