# Selected Tau Controller: Second Readiness Failure

Original failed 17:41 attempt, committed producer
`025b7be189b18900d6ebd860e344a80096069aec`. The controller records
2026-10-24 17:41:44-17:44:55 UTC; the CPU record ends at 17:41:45 UTC.

- Controller: `result: fail`, stage `readiness`, cleanup **unconfirmed**.
  `owner_exits` and `ready_sha256` are empty; `owner_reports` and `scorer`
  are null. No exit codes or successful cleanup are inferred from missing data.
- CPU owner: `result: fail`, stage `inputs`. Its `container`, `image` and
  `build_completion` cleanup fields are all `confirmed`. Runtime, image ID and
  context hash are null; source and base-image maps are empty.
- CPU-only cleanup does not establish candidate/GPU cleanup or whole-run
  cleanup, and does not override the controller's unconfirmed outcome.
- Original hardware-validation, quality, live-admission and confidentiality
  flags remain false in the records that contain them.

No READY admission, inference or quality score is established. These reports
do not identify a precise root cause. A later installation or retry does not
change either original failure or reconcile their cleanup scopes.

The original layout and bytes are preserved:
[controller run](evidence/run.json), [controller output](evidence/output.log),
[CPU run](cpu/run.json), [CPU output](cpu/output.log).
[Shared regressions](../../../scripts/test_selected_tau_failure_evidence.py)
pin all four hashes and the exact producer sources, retain producer ancestry,
and check source-defined shapes and the distinction between cleanup scopes.
