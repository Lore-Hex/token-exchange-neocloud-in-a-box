# P2.T3 tau trial completion protocol

Implemented and tested with local pipes and a forked synthetic worker. No GPU
benchmark, live tau score, container wiring or confidential result is claimed.

The existing [parent transport](p2-t3-tau-parent-transport.md) now accepts a
sequenced terminal outcome after completion calls. The worker may report only
a fixed status, finite reward, and digest of the task assertions. It cannot
provide token totals, costs, role counts or elapsed time. Those come from the
parent's existing Bridge and its single Budget. An evaluated result requires
agent and simulator requests, the required evaluator requests, matching
assertions, settled known usage and an unexpired trial deadline. Replay,
forged accounting, missing roles, EOF and failed delivery remain failures.

`scripts/tau_pipe_trial.py` connects the unchanged tau orchestrator callback to
the WorkerProxy. It does not create another ledger or scorer. Upstream errors
become fixed statuses; exception text and transcripts are never serialized.
The worker waits for a matching acknowledgement and cannot reuse a completed
session. The result is `nib.tau-transport-trial/v1`, explicitly
`quality_eligible=false`, not the frozen scored-comparison report format.

The [pinned tau source contract](p2-t3-tau-parent-transport.md#primary-source-contract)
and existing `run_upstream` remain unchanged. The caller still must verify and
isolate the runtime before invoking this library. No public CLI now runs
unverified dependencies. The next integration needs explicit container pipe
wiring with ordinary stdout/stderr suppressed, content-free evidence, and
runtime identity verification before local vLLM use.

Verification:

```sh
python3 -B -m unittest discover -s scripts -p 'test_tau*.py'
make check
```
