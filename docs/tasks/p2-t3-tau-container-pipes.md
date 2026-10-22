# P2.T3 isolated tau worker pipes

The existing scripted trial CLI has an explicit `--parent-pipe` option for
both preparation and execution. Default behavior remains unchanged. The
option is bound into the prepared plan and command; a plan prepared for the
other mode is rejected.

The pinned tau orchestrator and evaluator run in the same network-denying,
read-only, unprivileged container as before. Docker stdin is the only added
input. The worker duplicates the explicit stdin/stdout pipes, marks them
non-inheritable, then sends ordinary/native stdout and stderr to `/dev/null`.
The host parses bounded framed JSON directly from the attach pipe in memory;
it never uses the raw-output capture helper for this stream. Docker logging
remains disabled. Container identity, resources, no-network policy and exact
owned-container cleanup are still checked before and after execution.

All replies are still scripted using the existing scripted response generator.
There is no HTTP destination, credential or live-model option. The parent
reads task assertions from the verified upstream dataset, owns the sole
Bridge/Budget, and builds the existing content-free `synthetic-fixture` report.
The [terminal protocol](p2-t3-tau-trial-protocol.md) prevents worker-supplied
accounting. The source archive, wheel lock and upstream scorer are unchanged.

This integration is not a model benchmark. Token counts, pricing and serving
identities are synthetic. An observed upstream reward is not a DeepSeek score;
`quality_accepted`, `confidential` and `live_transport` remain false. Real
candidate/reference runtime proof, independent simulator/evaluator and live
tokenizer parity are still required.

Verification:

```sh
python3 -B -m unittest discover -s scripts -p 'test_tau*.py'
make check
```

The preparation and run commands are the existing
[single-trial commands](p2-t3-tau-single-trial.md), with `--parent-pipe` on both.
Any actual container run must retain its original success or failure artifact;
unit/subprocess tests alone do not establish upstream execution.

## Actual container result

Producer `6eb1484` ran the real pinned upstream airline task `0` on
2026-10-22 06:45:31-06:45:43 UTC through the pipe path. The
[original evidence](../../evidence/p2/2026-10-22-tau-pipe-pass/run.json) records
four scripted calls, an evaluated outcome and confirmed container cleanup.
The reported reward is from scripted responses, **not a model quality score**.

The preceding attempt used an inactive Docker Desktop socket and failed at
the image stage, before creation. Its
[failure evidence](../../evidence/p2/2026-10-22-tau-pipe-daemon-unavailable/run.json)
is retained unchanged. The successful retry used the already configured
OrbStack daemon; no Docker settings, security policy or model/network access
was changed.
