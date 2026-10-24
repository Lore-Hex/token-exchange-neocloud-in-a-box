# Selected weight-view parent rejection

Original metadata-only [receipt](receipt.json), produced by committed
`host/prepare-selected-weight-view.py` at
`9c1fb3c62318ad83087b2ab08f8ff7c4e7ae79d9`. The bytes are unchanged.

The approved ordinary-node attempt started at 2026-10-24 21:58:37.717972 UTC
and ended at 21:58:37.837156 UTC, before a prepared view was established.
The receipt remains `result: fail`, `publication: unproven`, with unknown
source-preservation and unconfirmed worker exit. Do not infer success or
cleanup from its short elapsed time. It contains no host, credential, path,
prompt or model-content data.

Subsequent read-only directory inspection found a root-owned `755` `/dev`,
root-owned `1777` `/dev/shm`, and root-owned `700` source and destination parent.
The producer's pinned cache helper admits sticky ancestors such as `/tmp`,
but not `/dev/shm`. Its `directory(..., private=True)` therefore rejects the
destination parent before copying. The new Linux regression exercises this
specific rejection and the narrowed selected-view fix. The receipt alone
does not record a traceback or establish any further cause.

This is failed preparation evidence, not a 510 GB copy, inference, quality,
confidentiality or deployment result. No automatic retry was performed.
