# P1.T4: Content-Free Upstream Failure Evidence

The software-only E2E harness now retains a small observation for its existing
`upstream-503-retry-after` check, whether that check passes or fails:

- HTTP status, restricted to integers 100-599 (otherwise null).
- `Retry-After` classification: `expected_7`, `absent`, or `other`.
- Whether the response contains the SSE `data:` prefix.
- Whether this check used the test TLS front door.

The record contains no response body, raw header value, URL, credentials or
process output. The original requirement remains HTTP 503, `Retry-After: 7`,
and no SSE data. There are no new retries, sleeps, deadlines, admission changes,
or ignored failures. A transport exception produces no invented HTTP observation.

This fixes an evidence gap, not a proven service bug. An earlier retained run
failed the compound assertion without recording which predicate differed.
A later pass cannot recover that missing observation or prove a particular
cause for the earlier failure.

Verification covers both TLS modes, expected success, wrong status, missing or
wrong retry header, an SSE-prefixed response, invalid status types, a transport
error and private-content canaries. Each case makes exactly one request.
The existing full software-only node E2E remains required. None of these tests
establishes GPU functionality, confidentiality or production uptime.

```sh
TMPDIR=/private/tmp PYTHONPATH=scripts python -m unittest test_e2e
make check
make test-e2e
```
