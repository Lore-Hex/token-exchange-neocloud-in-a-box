# CPU Finder Attempt Stopped During Application

**Failed before entry. No finder attribution or runtime qualification.**

One bounded local CPU-only run used independently reviewed source
`7fb794a442287bb6c7ac974c2a72f1067ed22fe6` after its final CI passed.
It ran from `2026-10-20T16:12:39Z` to `2026-10-20T16:25:06Z`.
The original recorder files are preserved byte-for-byte:

| File | SHA256 |
| --- | --- |
| `run.json` | `3b013bb7e0597537cfaec42d20bba1683794ccac4b295a2538f949483e575f28` |
| `output.log` | `3e25936d68048e5e5c92ac1772a8674838a0ebc9bee3728d2fc72e8bd9b9c092` |

Payload SHA256:
`d0246f9465eb864072203397ed2b495ae15c4694fbbf4b79c4673b2648d9b4a8`.
The regression reconstructs this payload and its source bundle using the
producer revision's complete repository-helper closure, not today's helpers.
It also checks the recorded validator-source digest and historical result
validator. It does not execute the image or upstream engine.

Application preflight took 18,233 ms and application took 701,769 ms.
Their sum is 720,002 ms against the shared 720-second application deadline.
This is consistent with budget exhaustion. The sanitized
`bounded-cpu-entry-rejected` failure does not identify the exact internal
exception, application operation or host-side cause.

The outer recorder completed, accepted the failed inner report and confirmed
owned cleanup. The inner experiment stopped at `stage=application` with
`application=null`, `entry=null` and no entry timing. There is no completed
five-stage application receipt, native-inventory success claim, child
checkpoint, state observation or finder identity from this attempt.

The image binding records the pinned manifest and public raw config link,
plus the daemon-reported platform and DiffIDs. It explicitly does **not**
claim to have rehashed the daemon's local config or layers. Docker, the host OS
and interpreter remain trusted.

No model weights, GPU devices, credentials, network, ports, serving call or
paid resource were used. `entry_pass`, `inference_started`, `hardware_validated`,
`abi_validated`, `deployable` and `confidential` remain false; the candidate is
`BLOCKED`. This attempt does not identify the [previous unknown
finder](../2026-10-20-cpu-finder-9833667/README.md), establish an adapter fix,
or authorize relaxing any guard. The independently prepared Botocore-six
candidate is not part of this evidence change.
