# Private startup diagnostic: preparation failed

The unchanged [smoke.json](smoke.json), SHA-256
`46c2ed87b05ee55fdfa77a77d4170b4845fdafee15241e4f29a39face89a4e12`,
was produced on 2026-10-24 by committed host recorder
`ba1cac58007d1ab4eb85b07ac3d9cfa2d1b6339e` after its independent review and CI.
This was one bounded attempt with the same image, prepared payload, private
inputs and selected read-only weight view as the
[first failed smoke](../2026-10-24-selected-service-smoke-failure/README.md).
No public ports or customer requests were used.

Preflight passed, the container was created and cleanup was confirmed. The
Docker start client returned 1. The newly retained, closed owner report says
`reason=preparation`, but that legacy catch-all also covers post-lifecycle input
cleanup and output handling. It does not establish the failing stage or check.
This does not prove a model-loading, GPU-capacity or inference failure. `runtime_started` remains
null, and all readiness, deployment and confidentiality claims remain unproven.
The original receipt contains no timestamps; none are added.

The diagnostic wrapper retained only validated fixed metadata, not stderr,
exception text, prompts, credentials or private deployment paths. This artifact
does not replace the first failure or turn either run into a passing result.
