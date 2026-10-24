# Selected Tau Controller: Readiness Failure

Original failed attempt labelled 1736, produced by committed controller
`025b7be189b18900d6ebd860e344a80096069aec`. Both recorded timestamps are
`2026-10-24T17:37:00Z`; the records have whole-second precision and do not
establish a precise elapsed duration.

- Controller stage: `readiness`; result: `fail`.
- Both `candidate` and `support` owner processes exited 1.
- `ready_sha256` is empty; `owner_reports` and `scorer` are null.
- Cleanup is **unconfirmed**, not successful. Owner process exits alone do not
  prove container or resource cleanup.
- `hardware_validated`, `quality_eligible`, `confidential` and `live_admission`
  remain false in their respective original records.

No inference or quality score was established. The report's fixed failure
classification does not prove an ownership/staging root cause, or identify
which prerequisite failed. Later diagnosis or retries do not change this
original outcome. No private deployment name, address or raw exception message
is included.

[run.json](run.json) and [output.log](output.log) preserve the original bytes
and flags. [Regression tests](../../../scripts/test_selected_tau_failure_evidence.py)
pin both hashes and the committed producer source, verify producer ancestry,
and compare the source-defined report/envelope shape without launching owners,
a scorer or hardware. No input-contract or independent owner report is claimed
for this two-file record.
