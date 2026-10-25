# Finite ordinary selected-service owner: PASS

[owner.json](owner.json) is the original, unchanged terminal receipt from the
bounded ordinary eight-B300 run. SHA-256:
`d35f6873ec1e3db9a7f7ec7c07a51f2ecf0f54f5fb624801b4a774d3907133ae`.

The operator launched the committed owner at
`b586b874fbf86430f19a8f97092901aa02ca9de3`. Its
`host/record-selected-service.py` SHA-256 is
`39dd688dcf91ecc6cd2a31372969c00cb1b5422b2c0ec80c6947e62f4d74737b`.
This source attribution comes from the execution record: the original receipt
does not embed a source revision, timestamps, endpoint or absolute cutoff.
The offline test preserves that limitation; it does not add those fields or
claim a cryptographic source/run or external-client/server join.

The receipt reports:

- Preflight passed and the owned container was created.
- The engine, API guard and TLS front door reached readiness.
- The lifecycle ended with `reason=retired`, not forced cancellation.
- TLS, guard and engine were all drained and reaped.
- The watchdog confirmed quarantine, resolved creations, absent container and
  network, and removed policy. Host cleanup is `confirmed`.
- Temporary private input snapshots were removed.

Its build, image and preparation identities match the preserved
[earlier startup attempts](../2026-10-25-selected-owner-startup-failures/README.md).
Those attempts remain FAIL; this is a distinct successful run, not a correction
to their original receipts. The earlier
[external HTTPS smoke](../2026-10-25-selected-external-smoke/README.md) remains a
separate client observation and cannot independently identify this owner.

This establishes one finite ordinary service lifecycle, including its terminal
cleanup. It does not establish 24-hour availability, public DNS or trusted public
TLS, marketplace billing, general tool reliability, benchmark quality, release
qualification, ZDR, confidential computing or E2EE. All original qualification
flags remain false. The `published=false` field is not a denial that an explicit
test ingress was requested: `tls_publication_requested` is true.

No destination, credential, prompt, response text or host capture is included.
See the [finite-owner contract](../../../docs/tasks/p2-t4-selected-finite-owner.md)
and [offline evidence checks](../../../scripts/test_selected_finite_evidence.py).
