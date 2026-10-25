# Selected private protocol and active-drain observation

The committed host recorder at
`faf23b0babf55eb5383da3c3d28ce11443716d3b` completed **PASS** on the retained
ordinary eight-B300 node on 2026-10-25. This is an original private-container
observation, not public deployment or confidential qualification. GPU CC was
disabled. The host receipt has no run timestamps; none have been added to it.

## Original artifacts

| File | SHA-256 |
|---|---|
| [smoke.json](smoke.json) | `5c1b3f01d8f914963eeb24a69479ef9bc8ab00a7fbeb51411020c6e173d962fc` |
| [build.json](build.json) | `5ab7a1412e3c1226dcc3efe99e0c2885384e2aedc8f45f313db7ac8e8de2d826` |
| [preparation.json](preparation.json) | `831b0c86d85ed49c95ff442b01c1f7727cc9f800cadd66fc41bf02484d5f5821` |

Preparation binds 801 files and the exact selected checkpoint. The build binds
image `sha256:af9d220638c2bb6bca270fdeeb874b8aecf76118640dd42f203a1101d559d3a2`
and prepared contract
`a37849fb6d86dfb61b5b90964ddb87ba3d9baefbb0fe921fd2f361031f83a5d4`.
The original bytes are unchanged; the preparation/build flags continue to
describe preparation/build only. The runtime receipt is separate evidence.

The committed `host/record-selected-service-smoke.py --run --protocol-smoke`
used private operator API/TLS files, the read-only selected weight view, no
network or published ports, and an immutable `2026-10-25T02:40:00Z` cutoff.
Only local owned TLS received fixed synthetic requests. Source, image and
preparation joins were independently revalidated after the run; private paths,
hosts and credentials are intentionally absent here.

## What passed

- Engine, guard and TLS became ready. Missing and incorrect API keys were
  rejected, and authenticated inference returned 36 prompt / 13 output tokens.
- All 17 mandatory pinned provider-check assertions passed. The separate
  subset validated the owned catalog, authenticated streaming and actual usage
  (34 prompt / 121 output tokens).
- With `reasoning_effort: none`, the fixed drain request reached first content
  while still active. Retirement was then requested and signaled; its reader
  completed, joined and closed its resources, retaining 25 prompt / 256 output
  tokens. The long fixture intentionally permits the fixed output cap; this
  demonstrates lifecycle completion, not answer quality.
- TLS, guard and engine drained and were reaped in that order. The host
  confirmed container/watchdog cleanup. A subsequent independent read-only
  check found no running containers and all eight GPUs at zero memory usage
  and zero utilization; that check is operational corroboration, not additional
  fields retrofitted into the receipt.

The [earlier failure](../2026-10-25-selected-protocol-drain/README.md) remains
unchanged. It passed the checker but did not establish the active first-content
drain precondition. This run tests the narrow explicit-reasoning change; it does
not establish a general causal explanation for the prior response.

## Limits

At most 17 HTTP requests, 11 authenticated, one in flight and no retries.
8192-token context, 256-output-token cap, DP2/TP4 and eager execution remain
unchanged. This is not tool reliability, full tau evaluation, sustained-load
qualification, public networking, availability, ZDR or confidential inference.
Every original capability/qualification/deployment/privacy claim remains false.
The API is not published by this evidence-only change.
