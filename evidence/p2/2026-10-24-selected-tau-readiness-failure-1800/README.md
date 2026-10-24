# Selected Tau Controller: Third Readiness Failure

Original failed 18:00 attempt, producer
`025b7be189b18900d6ebd860e344a80096069aec`. The controller records
2026-10-24 17:59:09-18:05:41 UTC; the CPU record ends at 18:02:31 UTC.

- Controller: `result: fail`, stage `readiness`, cleanup **unconfirmed**.
  `owner_exits` and `ready_sha256` are empty; `owner_reports` and `scorer`
  are null. No GPU report is present in the supplied original set.
- CPU owner: `result: fail`, stage `runtime`; its session failed at `parity`,
  model index 0, substage `health`. The original bounded diagnostic records
  `kind: other`, `server_exit: -6`, and source-bound frames, not exception text.
  No token-comparison cases completed and no READY receipt was produced.
- CPU container, image, build-completion and runtime cleanup are `confirmed`.
  This does not establish GPU or whole-run cleanup. All original qualification,
  live-admission and confidentiality flags remain false.

No selected-model inference or quality score is established. These originals
do not prove an ownership, HTTP-thread or cancellation root cause. The producer
already sent SIGINT on controller abort; absence of a GPU report does not show
why its reporting/cleanup path failed. Later fixes or retries do not rewrite
this failure or confirm its missing cleanup.

Original layout and bytes: [controller run](evidence/run.json),
[controller output](evidence/output.log), [CPU run](cpu/run.json),
[CPU output](cpu/output.log).
[Shared regressions](../../../scripts/test_selected_tau_failure_evidence.py)
pin all four files, retain the exact producer ancestry, check the original
controller constructors, and validate the CPU failure with its recorded source
closure. The [later CPU-only retry](../2026-10-24-cpu-http-threads-pass/README.md)
is a separate observation, not a successful selected tau trial.
