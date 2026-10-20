# CPU Entry With Fixed Finder Diagnostics

**Failed. No inference or confidentiality qualification.**

One bounded local CPU-only run used independently reviewed source
`983366709ffb1bd0682fb7692bb71b1e4777a450` after its final CI passed.
The original recorder outputs are preserved without modification:

| File | SHA256 |
| --- | --- |
| `run.json` | `36112d70c3a91c0f45b20da58dcffc6e916859771e72bafceeeb1b1e134f001b` |
| `output.log` | `c9ec7cb8d8eb841d3dcea5d0c7fd1fd88b7c10df749966b0ce46a836824c50e0` |

The exact cached image passed preflight and all five application stages.
The child exited 1 at checkpoint 11, before the `ServeSubcommand` import
completed. Its final observation reports only `finders=true`; the other nine
process-state mismatch bits are false. Preflight took 8,513 ms, application
494,707 ms, and entry 12,098 ms. Cleanup was confirmed by the recorder and an
independent post-run container listing.

The [fixed finder diagnostic](../../../docs/tasks/p2-t3-finder-identity.md)
observed `builtin`, `frozen`, `sealed`, then `unknown`. All four reference
sources were verified; comparison did not fail and the scan was not truncated.
No expected finder was missing, duplicated or reordered. The identity list
changed, and the pattern adapter remained `pending` at the observation point.
`unknown` does not identify a dependency, prove malicious behavior or permit
the finder. Neither the first rejection nor the earlier GPU failure's cause
has been established. The next step remains source-backed diagnosis, not
broader execution permission.

No model weights, GPU devices, credentials, network, ports, serving call or
paid resource were used. `entry_pass`, `inference_started`, `hardware_validated`,
`deployable` and `confidential` remain false. Earlier attempts are preserved
[before](../2026-10-20-cpu-entry-125036c/README.md) and
[after](../2026-10-20-cpu-entry-732fe9f/README.md) the pattern-matcher adapter.
