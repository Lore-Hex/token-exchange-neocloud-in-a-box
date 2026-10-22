# P3.T1: Bounded Public Input Retries

Native build [39704351859, attempt 1](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39704351859/attempts/1)
failed before package execution when curl returned 28 while retrieving a
77,218-byte pinned input. The unchanged-input second attempt passed. That is
evidence of a recoverable transport failure, not permission to accept different
package bytes or refresh the authenticated snapshot.

The input fetcher now retries only curl transport codes 6, 7, 28, 52, 55 and 56,
and the process supervisor's timeout. Their meanings come from the
[curl error reference](https://curl.se/libcurl/c/libcurl-errors.html).
Each input gets at most three attempts, with one- and two-second backoffs.
Every attempt and backoff shares the existing 600-second total budget; there
is no nested curl retry or whole-workflow retry. Completed verified downloads
are not requested again during the same invocation.

Every successful attempt must still match the pinned byte length and SHA-256.
HTTP errors, TLS verification errors, unexpected stderr, invalid return codes,
oversized output, invalid metadata and wrong content remain terminal. No
redirect, alternate mirror, proxy, credentials or package execution is added.
Retry diagnostics include only the public input hash/size, fixed failure class,
return code, attempt and delay. Partial response bodies and exception text are
discarded, not logged or reused.

Synthetic tests cover recovery, retry exhaustion, time spent in backoff,
timeout recovery, wrong bytes after a transport failure and terminal TLS/HTTP
errors. They do not simulate or claim production network reliability. The
original failed run remains a failure; no historical evidence is rewritten.
