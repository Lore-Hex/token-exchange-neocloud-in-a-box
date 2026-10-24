# Selected weight view: passed

The original [preparation.json](preparation.json) records one bounded preparation of a separate
read-only serving copy of the selected abliterated checkpoint. The producer is
`9fe2adcf1c096387834ef3c7ad2c6e955fc16f2d` and the unchanged receipt SHA-256 is
`9417e4f94c4b62cecd6cfcedf438fa848b2e139ab18cbb6aed1684923b9a39e2`.

The committed [preparer](../../../host/prepare-selected-weight-view.py)
ran from 2026-10-24 22:15:59.947875 UTC to 22:32:33.292601 UTC, within its
2,700-second bound. It verified all 79 files, totaling 510,311,056,155 bytes,
against manifest `5c6891a9649702b1ac0327b945600acb40c571a069942517cf14ba661a3cfcbc`.
It copied rather than hard-linked the files, hashed the new view after mode
preparation, preserved the original source inventory and permissions, and
published the new directory with an atomic no-replace operation. The worker
exit is confirmed. New files are `0444`; model directories are `0555`, beneath
a private operator-owned wrapper.

The [first attempt's original failure](../2026-10-24-selected-weight-view-parent-failure/README.md)
is retained separately. This successful retry uses the reviewed, narrowly
scoped sticky-RAM-disk parent fix; it does not relax the original cache modes.

This is file preparation on an ordinary eight-B300 test node, not inference,
durable-storage verification, public service, quality or confidential-computing
acceptance. The receipt's `runtime_ready` and `confidential` remain false.
The local host and root are trusted. A serving runtime must independently
check the manifest and mount this view read-only before admitting requests.
Private source/destination paths and operator identity are intentionally not
published; the original closed metadata receipt is unmodified.
