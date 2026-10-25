# Selected private native tool smoke

The committed host recorder at
`76013ab9d7da6a98d35d7bae0a7dc5eead494737` completed **PASS** on the retained
ordinary eight-B300 node on 2026-10-25. This is one fixed synthetic tool round
trip through the owned TLS front door, guard and selected native engine, not
general tool reliability or quality qualification. The original host receipt
contains no run timestamps; none have been invented or added.

## Original artifacts

| File | SHA-256 |
|---|---|
| [smoke.json](smoke.json) | `d8c240a97219983d93ad382ff03e531f30f44198ba67446035e3f1a46d94c416` |
| [build.json](build.json) | `b7c1825e24399b1ff1e4234e7d8ce95d8f5d2eb2e09b959e91fa61577190faaf` |
| [preparation.json](preparation.json) | `0725c91777e43111617b5cc01b8853efc5fc5bb1fb8531f8915c22aef158d7f9` |

All three files preserve their original bytes. Preparation binds 803 files,
the selected checkpoint, and the source revision above. The successful build
binds image
`sha256:350dffcbf2d9d413993bd638651f48c104b92d3194977a927f7950c66bdde2be`
and prepared contract
`9521f55c70946c2839d9347d52beff0f2715f21055ddeb22f91189bb3b1b9b0a`.
Preparation and build retain their original non-runtime claims; the host
receipt supplies the separate runtime observation. These are existing
source/receipt joins, not new measurements of image layers or host trust.

The command was `host/record-selected-service-smoke.py --run --tool-smoke`,
using private API/TLS inputs, the read-only public weight view, no container
network or published ports, and the original fixed absolute cutoff. The
[committed helper contract](../../../docs/tasks/p2-t4-selected-service-tools.md)
permits the authentication precheck and exactly two tool requests, one at a
time, with no retries. No arbitrary tool execution is authorized.

## What passed

- Engine, guard and TLS became ready. Missing and wrong API keys were rejected;
  authenticated inference retained 36 prompt / 13 completion tokens. The
  receipt explicitly does not claim measured zero upstream calls for the
  unauthorized attempts.
- The fixed call retained 303 prompt / 41 completion tokens and finish reason
  `tool_calls`. The helper checked exactly one expected tool with the fixed
  arguments, without repairing the response.
- The continuation retained 364 prompt / 6 completion tokens and finish reason
  `stop`. It returned the exact fixed answer after the synthetic tool result.
  Both checks passed, both resource sets closed, and neither usage was unknown.
- TLS, guard and engine each drained and were reaped, in that order. Host
  container/watchdog cleanup was confirmed. The operator's subsequent
  independent read-only check at 02:44 UTC found Docker empty and all eight
  GPUs at zero memory usage and utilization. This is operational corroboration,
  not additional fields reconstructed into the receipt.

## Limits

The selected 8192-token context, 256-output cap, one admission, DP2/TP4 and
eager execution remain unchanged. The fixed requests use `reasoning_effort: none`;
this does not establish general reasoning behavior. Bodies, arguments,
API keys, TLS keys, private paths and host addresses are not retained here.

This is not a full benchmark, full tau evaluation, support-model qualification,
provider-check run, sustained-load result, public networking proof or deployment
authorization. Every original capability, qualification, deployment and privacy
flag remains false, including `tools`, `reasoning`, `streaming`, `confidential`,
`e2ee` and `zdr` wherever defined. The private smoke does not promote those
claims. Earlier evidence remains unchanged.
