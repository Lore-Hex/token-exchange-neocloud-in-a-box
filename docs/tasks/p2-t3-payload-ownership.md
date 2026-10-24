# Private Lab Bundle Ownership

The first ordinary B300 provider-check attempt on October 24 exited before
hardware observation, with container exit code 2 and no OOM. Its owner confirmed
cleanup. The original [envelope](../../evidence/p2/2026-10-24-provider-transfer-ownership-failure/run.json)
and [report](../../evidence/p2/2026-10-24-provider-transfer-ownership-failure/output.log)
are retained byte-for-byte at producer
`68ab473fda5b29bf85be07099494702b63aeba69`.

Operator inspection found the transferred payload directory and launcher still
owned by the workstation's UID 501, with modes `0700` and `0600`. The container
uses UID 0 with every capability dropped. Unlike the host's privileged root
process, it cannot bypass those permissions. Re-extracting the same verified
archive as Linux root with `--no-same-owner` preserved every byte and permission
while assigning the runtime owner. The subsequent attempt progressed beyond
the immediate launch failure; its final inference result is separate evidence,
not established by this document.

The failed receipt alone does not identify the unreadable filename or contain
the raw runtime error. The diagnosis joins its early exit with the operator's
ownership inspection and the unchanged-byte retry. No request or completion
was exposed by this investigation.

The launcher now validates the runtime payload before any container/watchdog
creation. It requires root ownership, owner read/traverse access, execution
permission on the two fixed executable files, no group/world write, and bounded
regular-file/directory traversal with no links or special files. It never
changes permissions or ownership automatically. Bundle hash/exact-file checks,
container capability restrictions and all model/inference gates remain intact.

Tests cover the non-root archive owner, unreadable files/directories,
nonexecutable wrappers, writable entries, links/special files, validation order
and the retained failed evidence. This is transfer plumbing, not model quality,
provider conformance, production admission or confidential verification.
