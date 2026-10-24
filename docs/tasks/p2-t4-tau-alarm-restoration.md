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

The existing provider payload's two exact support-source SHA-256 pins are updated
together; source/hash rejection is not weakened. No deadline, cleanup reserve,
usage accounting, API behavior, or hardware claim is relaxed. Existing lab
bundles and running trials retain their original committed bytes.

## Verification

Focused regression coverage includes pending expiry before default-handler
restoration, fresh-module outer expiry, an earlier inner timeout without renewing
the outer deadline, exactly-once enclosing handler delivery, KeyboardInterrupt
and SystemExit cleanup, immediate expiry, and repeated nested real-timer expiry.
The existing selected-driver timeout/cleanup and provider source-pin tests remain
unchanged. Full-suite and fixed-port validation require the coordinated test slot;
focused subprocess tests do not claim full-suite success.
