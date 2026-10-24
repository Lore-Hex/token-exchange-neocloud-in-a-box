# First private service smoke: failed

The unchanged [smoke.json](smoke.json), SHA-256
`ee0eba39d2f94b6ec8ab98395d35f83dc908d36fb73108e69bd2e51c5c742f59`,
was produced by committed host owner
`3dca539103cef7702fd43bf8c2f477f8b3928c3b` on 2026-10-24.
It consumed the [recorded service image](../2026-10-24-selected-service-image/README.md)
and the separately verified selected-checkpoint read-only view.

Image/private-input preflight passed. One container was created; the lifecycle
stage failed and owned-container cleanup was confirmed. `runtime_started` is
null: the host did not retain a validated terminal owner report, so readiness,
inference, retirement and in-container cleanup are not established. The original
receipt does not contain timestamps or the underlying cause; none are added.
All deployment and privacy claims remain false. Networking was isolated with no
public ports; this was not customer traffic.

The diagnostic gap is in the host wrapper: its shared command helper raises on
a nonzero exit before the wrapper can validate the owner's closed stdout
metadata. A follow-up now retains only a strictly shaped failed lifecycle
report, never stderr or raw exception/output content. That change does not
explain this original failure, rerun the test or turn it into success.
