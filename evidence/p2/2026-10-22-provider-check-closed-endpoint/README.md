# Actual Checker, Closed Endpoint

Original receipts from source commit
`720bfa7aabd787744a56a94a31162498cf235270`, copied byte-for-byte from the local
producer output. [run.json](run.json) is the software-test envelope;
[output.log](output.log) is the content-free structured result, not the raw
upstream report.

The existing pinned Linux/amd64 CPython image and all 17 authenticated e2e-lock
wheels were used. No package code was imported on the host. The exact unchanged
provider checker 0.1.8 executed `run_checks(..., tier=4)` in a CPU-only container
with networking disabled and no endpoint. This is not a fake HTTP response test.

Observed 2026-10-22 18:54:30-18:54:38 UTC:

- All 18 expected check IDs were returned: 7 warnings and 11 skips.
- Upstream's own conformance gate was `true`; it only rejects `fail`.
- The strict wrapper gate was **false** and the worker exited **1**. It requires
  all 17 mandatory checks to pass and exact native identity, not merely no hard
  upstream failures. This negative control must never become node acceptance.
- No OOM or surviving worker was observed; owned container removal and absence
  were checked. No GPU, model, public endpoint, credential or remote VM was used.
- Endpoint identity, quality acceptance and confidentiality remain false.

The envelope's `result: pass` means the negative-control **test** passed. It does
not mean provider conformance. No actual DeepSeek conformance claim is made.
The implemented boundaries, exact source audit, commands and remaining live
lifecycle integration are in the [task note](../../../docs/tasks/p2-t3-private-provider-check.md).

The preparer CLI separately generated the same payload manifest digest
`ba3768f58f82afd3b034998b6284470fab0ce8267b0ea8a1c94647f48deca446`.
The receipt binds all executed repository source inputs to their original Git
objects; the requirements lock is unchanged. Full package bytes are retained in
the private prepared payload, not published as a new package/image.
