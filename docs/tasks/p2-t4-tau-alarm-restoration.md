# P2.T4: Preserve alarm ownership during deadline cleanup

## Failure and scope

The local PR316 full check at `e934cde7f3d6389a0fd191e9d95ac4628af277d0`
ended with SIGALRM (`make` Error 142) during the Python suite. Its output ordering
points to the selected-driver timeout test; the full log does not contain a
traceback identifying that test. The API-profile code is not changed here.

The shared tau alarm helper had two related lifecycle defects:

- Disarming `ITIMER_REAL` does not consume an already-pending SIGALRM. Restoring
  `SIG_DFL` before delivery can terminate the process rather than raise `Abort`.
- An expired enclosing timer was rearmed for one microsecond during exception
  unwinding, creating another asynchronous deadline exception during cleanup.
  Freshly loaded worker modules can also have different `Abort` classes.

A port-free nested-alarm subprocess probe reproduced signal death on unchanged
source. A deterministic regression holds delivery blocked until timer expiry,
then exits the helper and restores delivery: the original helper kills the
child with return code `-14`. The unittest runner is never exposed to that signal.

## Correction

Block SIGALRM during restoration, disarm and consume a pending signal, restore
the previous handler, and restore the caller's signal mask. An unexpired outer
timer retains only its original remaining duration. An expired outer timer is
delivered synchronously through its original handler, retaining its exception
class; it is never renewed for a microsecond. Pending expiry still fails closed.

Python documents that handlers run later on the main interpreter thread, and
provides `pthread_sigmask`, `sigpending`, and `sigwait` to control blocked pending
signals ([Python 3.12 signal documentation](https://docs.python.org/3.12/library/signal.html)).
These tests exercise the actual POSIX primitives in bounded child processes.

### Background threads

Review of the first candidate `47629bfe180150f196d87cd289c013cff2864cf6`
added a live background thread explicitly leaving SIGALRM unblocked. Bounded
stress passed 3,000 nested expiries, with both blocked and unblocked main-thread
masks, but two targeted subprocess probes found gaps that stress alone missed:

- A late signal delivered to that background thread during disarm dispatches its
  Python handler on the main thread and can interrupt restoration, leaking the
  temporary handler. The handler now records expiry without raising during the
  restoration phase; the context fails after cleanup completes.
- A non-main caller was rejected by `signal.signal`, but its `finally` disarmed
  the main thread's timer. An explicit main-thread precondition now rejects the
  caller before reading or changing timer ownership.

The helper is a main-interpreter/main-thread contract, not a process-wide thread
signal-mask facility. Python C-handler notifications differ from kernel-pending
signals: CPython checks queued notifications before replacing a handler, and a
notification whose Python handler has disappeared is reported as an unraisable
error rather than being redelivered through `SIG_DFL`
([CPython v3.12.13 signal implementation](https://github.com/python/cpython/blob/v3.12.13/Modules/signalmodule.c#L1682-L1705)).
Tests require empty child stderr as well as restored handler, timer, and mask.
They do not claim that finite stress proves every possible scheduling interleave.

The existing provider payload's two exact support-source SHA-256 pins are updated
together; source/hash rejection is not weakened. No deadline, cleanup reserve,
usage accounting, API behavior, or hardware claim is relaxed. Existing lab
bundles and running trials retain their original committed bytes.

## Verification

Focused regression coverage includes pending expiry before default-handler
restoration, fresh-module outer expiry, an earlier inner timeout without renewing
the outer deadline, exactly-once enclosing handler delivery, KeyboardInterrupt
and SystemExit cleanup, immediate expiry, and repeated nested real-timer expiry.
Threaded coverage includes forced late C-handler delivery during restoration,
rejected non-main entry preserving the enclosing timer, and another 200 nested
expiries with a live unblocked background thread and both main-thread masks.
The existing selected-driver timeout/cleanup and provider source-pin tests remain
unchanged. Full-suite and fixed-port validation require the coordinated test slot;
focused subprocess tests do not claim full-suite success.
