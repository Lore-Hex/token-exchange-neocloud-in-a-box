# Bounded Weekly Serving Lease

The ordinary million-token service can explicitly select `ordinary-7d` instead
of the legacy `ordinary-24h` lease. This avoids a mandatory daily cold load of a
single-node service while preserving a finite operator-approved cutoff. It is
not automatic renewal, seamless handover, a seven-day soak result, or a claim
of confidential operation.

The same host owner, watchdog, container supervisor and Go API guard enforce
the original absolute deadline and a monotonic deadline. Only the closed
million-token profile can select a weekly lease. Unknown profiles, missing
cutoffs, an expired cutoff or one more than 604,800 seconds away fail closed.
The default smoke and day limits remain 4,800 and 86,400 seconds. Their launch
arguments and historical receipts are unchanged.

Request deadlines remain 3,540 seconds. The owner still reserves 7,240 seconds
for frontend drains and child cleanup, plus the outer host reserve. The catalog
publishes the earlier admission cutoff, never the later hard stop. A clock
rollback cannot lengthen either bound. New receipts and network journals bind
the literal weekly profile; they cannot be joined to day-profile evidence.
Smoke and long-qualification hooks cannot opt into the weekly serving lease.

Use the existing preparation, image build, owner inspection and launch path,
adding `--lifetime-profile ordinary-7d` only after the new exact source and image
have passed review and tests. An old image cannot opt into the new host protocol:
the prepared source equality check still runs before resource creation. Operator
configuration, keys, TLS certificates, network addresses and pricing remain
private and outside this repository.

Before the published cutoff, either drain and replace the owner with a newly
approved lease or withdraw its marketplace route. Keeping a GPU rental funded
does not renew a software lease. There is still a cold-load service gap on a
single node; uninterrupted rotations require a second qualified node. The
legacy attach-recovery helper intentionally supports only the day profile and
must not be used to recover a weekly owner.

Verification uses fake clocks, synthetic owner/network journals, receipt joins,
Go catalog/expiry tests and unchanged legacy suites. Live readiness and a
completed long soak require separate recorded evidence. Billing remains with
the marketplace; no billing database or customer-account system is introduced.
