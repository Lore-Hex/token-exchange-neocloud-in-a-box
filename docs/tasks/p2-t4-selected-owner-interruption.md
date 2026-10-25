# P2.T4: Selected Owner Cancellation Boundary

Status: implemented with local process regression tests. No live recovery,
replacement service, successful soak or confidential qualification is claimed.

## Cause And Fix

The ordinary-service owner used `InterruptedError` for its first SIGINT or
SIGTERM and latched subsequent signals off. CPython's selector implementations
catch `InterruptedError` and return an empty ready list. A signal delivered
while the owner drains its Docker attach pipes can therefore be swallowed;
the process continues with its cancellation latch already set.

The owner now raises a dedicated `Cancelled(BaseException)`. Existing `finally`
cleanup and fixed failure envelopes remain unchanged. This is not a new success
path or an extension of the original deadline. The real subprocess regression
delivers both signals while blocked inside the actual default selector, verifies
the interrupted frame, demonstrates the legacy swallow, and proves the new
exception reaches cleanup. Linux CI additionally exercises a real pidfd and
pipe-child exit. Synthetic process tests are not live service cleanup evidence.

Primary source: [CPython v3.14.4 selectors.py](https://github.com/python/cpython/blob/v3.14.4/Lib/selectors.py),
`SelectSelector`, `_PollLikeSelector`, `EpollSelector` and `KqueueSelector`.
The existing runtime's Python source was also inspected read-only. Source
behavior establishes the failure mechanism; it does not prove recovery.

## One Legacy Recovery

Changing files cannot repair a Python handler already loaded in a live process.
[release-selected-attach.py](../../host/release-selected-attach.py) admits only
the exact legacy producer `4e0bfd7052d9a1e74d9fa83dda54272efd425351` and a
hash-pinned receipt proving the earlier identity-bound cancellation request.
It does not retry that signal or invoke Docker stop/remove directly.

The helper verifies the private expectation, complete current source closure,
parent executable/argv/namespaces/log identity, and original network/container
journals. It discovers exactly one child with the journal's full container ID,
then checks its root UID, executable hash from the existing venue pin, argv,
environment, session/group, parent relation, namespaces and both output pipes.
The parent must hold the corresponding read ends. Both processes are bound
with pidfds and rechecked immediately before the single permitted signal.

`--inspect` performs no mutation. After separately reviewing that receipt,
`--release` writes and fsyncs a new exclusive intent, then sends SIGKILL to
**only that attach child's pidfd**. Never signal the parent, watchdog, a numeric
PID, a process group, or an arbitrary container. Docker CLI
[v29.1.3 start.go](https://github.com/docker/cli/blob/v29.1.3/cli/command/container/start.go)
installs signal forwarding for non-TTY `start --attach`; its
[signals.go](https://github.com/docker/cli/blob/v29.1.3/cli/command/container/signals.go)
forwards catchable signals including SIGTERM. SIGKILL cannot be forwarded.

Closing the verified attach pipes lets the original owner enter its existing
failure and watchdog-cleanup path. Admission is bounded to 60 seconds and
observation to 240 seconds, clipped to the original wall/monotonic deadline.
Any uncertain intent blocks repetition. There is no retry or automatic fallback.

Only after observing both exits does the helper preserve the exact terminal
JSON line, validated against the original image, preparation and deadline.
Other log lines are never copied. An exception after identification cannot
retain an `identified` success; an exception after a possible signal stays
`unconfirmed`. The helper always keeps `cleanup_confirmed=false`,
`original_run_pass=false` and `soak_validated=false`. Owner-reported cleanup
still requires independent fresh container/network/policy absence and GPU-idle
checks before replacement. A missing original exit code stays unknown.

No new run, host mutation or production authorization follows from this document.
The operational sequence requires final-head independent review, full green
checks, merge, an exact private expectation and prior receipt, a fresh successful
inspect, and a separately reviewed one-shot release. Preserve failures unchanged.

## Verification

Run `test_selected_attach_release.py`, `test_selected_service_owner.py` and
`test_selected_service_cancel.py`, then full `make check`. Cases include PID,
source, journal, namespace, executable, environment and pipe drift; denied signal
or pidfd; durable-intent failure; malformed terminal envelopes; ambiguous exits;
and content-free CLI failure reporting. No test calls Docker or a live endpoint.
