# P2.T4: Long-Job Cancellation Boundaries

Status: software cancellation hardening; no hardware cancellation or cleanup
claim. Existing running processes and original evidence are unchanged.

## Boundary

[CPython v3.14.4 selectors.py](https://github.com/python/cpython/blob/v3.14.4/Lib/selectors.py)
catches `InterruptedError` in its selector implementations and returns an empty
ready list. An operator signal handler must not use that exception to request
shutdown: the caller can continue with its one-shot cancellation latch set.

The cancellation helper, selected network-none smoke, DeepSeek lab recorder,
image recorder, public-weight cache, selected weight-view preparer and
pre-container recovery now raise a dedicated `Cancelled(BaseException)`.
Existing failure-envelope boundaries explicitly admit that exception where
necessary. Cleanup, ownership checks, original deadlines and output bounds are
retained. A lab or image cancellation remains failed even if received during
cleanup. The recovery helper latches repeated cancellation while restoring its
original signal handlers. No change is made to legitimate EINTR retry handling
in shared process/pipe utilities.

These entrypoints have different isolated/bootstrap source contracts. Their
small exception declarations stay local; no unverified shared import is added.
Exact cache and application-preparer/recorder source hashes are updated in their
current consumers. Historical source-bound artifacts and pins remain historical.
The import-smoke and CPU-entry diagnostics share the image recorder handler;
their failure boundaries and exact transitive bootstrap pins change with it.
Fresh preparations must be regenerated from the new committed source; do not
patch a staged bundle, running owner, or historical receipt in place.

The selected tau controller's existing abort path sends SIGTERM to its candidate
lab recorder before closing stdin. Its signalling order, 180-second maximum
abort grace and original cutoff remain unchanged. A diagnostic prepared from
`987b012` still has the old cancellation boundary and must not be launched.
A new bounded natural diagnostic requires a fresh source clone and regenerated
tau-owner bundle from the final independently reviewed, fully gated hardened
producer, plus a newly reviewed absolute cutoff. This change does not authorize
that diagnostic or qualify its simulator.

## Verification

[test_cancellation_boundaries.py](../../scripts/test_cancellation_boundaries.py)
delivers SIGINT and SIGTERM while blocked in the real platform selector and
checks the interrupted frame. Synthetic work exercises failed receipts, absence
of subsequent mutations, owned cleanup and restored signal handlers. Existing
source-tamper and lifecycle tests remain required. Linux CI exercises epoll;
macOS executes the same tests against kqueue. No test invokes Docker, mounts,
remote services or GPUs.
The selected-launch regression also aborts a real child blocked in a selector
using the actual lab handler and fake owned-container operations; it retains the
failed report and checks both confirmed and unconfirmed cleanup paths.

This is a separate follow-up to the selected-owner cancellation/recovery change.
It neither authorizes another signal nor supplies live recovery evidence.
