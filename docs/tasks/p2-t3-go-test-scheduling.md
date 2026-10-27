# Go race-suite package scheduling

The test harness explicitly sets `-p=1` in both root Makefile race commands
and the nested tagged guest suite. The nested command clears `GOFLAGS`, so
an inherited setting alone does not constrain both package schedulers.
Both race variants, `-count=1`, all packages, the nested coverage guarantee,
and every original assertion remain. No production source changes: the query's
two-second deadline, 4,096-byte bound, 100ms WaitDelay and process-group cleanup
are unchanged. This trades test throughput for bounded package scheduling.

## Historical diagnostic observations

The original full local gate on `1e5273062a49fb45f643a984f436bc300f777d70`
failed finite 4,095/4,096-byte queries, including a nested guest failure. Its
generic error did not identify a stage. A separate test-only instrumented copy
preserved the guards and added fixed, content-free phase timing. Six initial
bounded observations did not reproduce the failure. One later authorized pair
built/started all 34 outer packages with this fixed test filter:

```sh
go test -race -count=1 -json -run '^Test(TaggedGuestSuite|BoundedQueryFiniteOutput|BoundedQuery)$' ./...
```

The second observation added outer `-p=1` and set
`NIB_QUERY_DIAGNOSTIC_SERIAL=1`, which added explicit `-p=1` to the diagnostic
copy's nested command. Both used `NIB_QUERY_DIAGNOSTIC=1`, `GOPROXY=off`,
`GOSUMDB=off`, `GOWORK=off`, a private physical temporary directory, the same
local Go 1.27.2 darwin/arm64 toolchain and cache, and an unchanged source closure.
These are historical diagnostics, not executions of this scheduling patch.

| Observation | Exit | Elapsed | Valid finite queries |
| --- | ---: | ---: | --- |
| Default parallel | 1 | 11.931s | One rejection among four |
| Both schedulers serialized | 0 | 51.170s | Four pass, 120.285-140.698ms |

In the reproduced outer 4,095-byte failure, Start succeeded at 2.157ms,
cancellation fired at 2002.146ms, and Wait returned an exit error at 2002.310ms.
Both output lengths were zero; the context reported deadline expiry. Child CPU
was 0.037ms user plus 0.347ms system. Cleanup returned ESRCH, not a cleanup
rejection. The next 4,096-byte query passed at 1659.953ms. Negative controls
retained their assertions; both driver processes were reaped.

This supports **concurrency-sensitive child startup latency**, not a definitive
OS or CPU-oversubscription cause. One ordered pair does not isolate cache
warming, run order or unmeasured host activity. The original uninstrumented
failure's exact stage remains unknown. No retry-until-green was performed.

## Evidence and gates

[Imported evidence](../../evidence/p2/2026-10-27-go-test-scheduling/run.json)
retains the original failed full gate and both filtered observations.
The first scheduling-fix gate stopped before Python tests because the import
used UTC offsets rather than the evidence schema's `Z` timestamps. Its rejected
record and failure log remain as `schema-rejected-import.json` and
`schema-failure.log`; the committed recorder now uses the schema's format.
The seven focused regressions include that exact schema pattern.

`scripts/record_go_test_diagnostic.py` imports only the exact retained inputs,
scrubbing them through the existing `scripts/scrub-secrets.sh`. It never runs
Go or repeats the observations. Its `run.json` describes the import operation,
not a fresh successful test: the original failure and both observation exits
remain in separate artifacts. The observed source revision is distinct from
the committed importer revision. Output creation is exclusive; partial output
is retained on failure rather than overwritten on retry.

```sh
NIB_BIN=/absolute/path/to/reviewed/nib python3 -I scripts/record_go_test_diagnostic.py \
  --input /absolute/private/diagnostic-directory \
  --deny-file /absolute/private/literal-deny-file \
  --output /absolute/path/to/new-evidence-directory
```

The input directory contains `original-failed-full-check.log` and the retained
`full-breadth-once/` artifacts. Source and destination hashes are recorded.
The full before/after source listings remain private; their verified identical
digests are included in `output.log`. All diagnostic logs remain complete after
scrubbing. No secret-scanner exceptions are added for source-file hashes.
Private paths in this example are placeholders, not operator information.
Focused regression: `python3 -m unittest discover -s scripts -p 'test_go_test_*.py'`.
Required later gates remain independent exact-head review, full `make check`,
fake `make test-e2e` with cleanup, and hosted CI. Import success is not any of
those gates. Dependent branches require normal integration and fresh gates;
this does not authorize merging the previously failed qualification-owner PR.
