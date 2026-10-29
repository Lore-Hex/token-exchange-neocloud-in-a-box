# Original Model Startup Budget

Software change only. No serving lifecycle, deadline, price, deployment or
benchmark was changed by this work.

The closed `original-long1m-graphs` service profile now has a maximum 7,200-second
startup allowance. Every other profile keeps the existing 1,800 seconds.
[The shared contract](../../scripts/selected_service.py) captures the deadline
once and caps it at the original absolute retirement deadline minus the full
cleanup reserve. There is no CLI/environment override, restart renewal or
change to request, drain, admission or service lifetime limits.

## Rationale And Limits

The original read-only pack inspection took 1,552.85 seconds for 83 files and
510,311,045,295 bytes under the bounded host worker. The original evidence is
preserved in [PR492](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/pull/492).
That is one observed inspection, not an engine-loading or serving result.

**Estimate:** two integrity passes at that observed duration require about
3,106 seconds. Adding the existing 1,800-second engine startup allowance totals
about 4,906 seconds; a closed 7,200-second ceiling leaves about 2,294 seconds for
admission, storage variance and preparation. This is a conservative operational
budget, not a vendor setting, throughput guarantee, spending approval or proof
that a future run will finish. Slow storage still fails closed at its original
deadline. The scan's measured CPU limit does not establish consumer scan speed.

Paired mount/consumer handoff must carry the original startup deadline into
the actual consumer, never start a fresh allowance after host verification.
The startup budget does not implement that handoff. A fresh prepared image,
independent review, green CI, staging and coordinated drain/cutover remain
separate requirements. Existing running services are not modified.

## Verification

[Fake-clock regressions](../../scripts/test_original_startup_budget.py) check
the closed profile selection, unchanged legacy ceiling and hard cutoffs,
cleanup reserve, frozen deadline, expired-startup rejection and wall-clock
rollback. Existing lifecycle and official-runtime tests remain unchanged.
These tests make no network, Docker, GPU or inference calls.

The offline image verifier's authenticated `selected_service` source hash is
updated with this change, as is the importer's verifier hash. Fresh verification
and import must use these exact sources; existing receipts stay unchanged.
The archive and helper-integrity regressions remain
strict; the first hosted CI run rejected the stale pin, and that failure is
retained in the PR checks rather than bypassed.
