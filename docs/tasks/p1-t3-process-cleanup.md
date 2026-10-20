# P1.T3 cleanup after denied process-group signals

Two local macOS test runs on October 20, 2026 observed `PermissionError` from
the first `killpg` call while rejecting excessive stderr. The original
`finally` block stopped there, before `wait` and pipe closure. A subsequent
read-only check found the first named child absent; that later observation does
not prove cleanup at the failure time. The reason for the operating-system
signal denial is not established.

The shared [snapshot helper](../../scripts/e2e/snapshot.py) now attempts both
bounded termination/reaping stages despite a denied signal, and closes pipes
even when reaping fails. It still requires the child to be reaped and a separate
process-group query to return `ProcessLookupError`. Permission denial on that
query, an unreaped child, or a surviving group is a failure. A denied termination
signal alone is neither proof of absence nor permission to claim success.

No child command, network policy, sandbox, output limit, timeout or successful
result acceptance is relaxed. Existing operation errors remain errors after
successful cleanup; the stderr overflow still raises its original output-limit
failure. There are still two waits of at most ten seconds and a final
five-second group-absence deadline.

Only live source pins are updated. Historical evidence, historical-source tests
and the weight-stage recorder's explicitly frozen older builder closure retain
their original hashes and behavior. This change does not backport the helper
into those historical producers or qualify already prepared bundles.

The [regressions](../../scripts/test_qwen_export.py) use synthetic process
objects for denied signals, denied absence checks, unreaped children, surviving
descendants and original-error preservation. Existing real-child tests exercise
stdout/stderr overflow, timeout, bounded success and reaping. These are local
software tests, not container, hardware, inference or confidentiality evidence.
