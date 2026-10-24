# P2.T4: Selected-Trial CPU Owner

Implemented and synthetic-tested, **not hardware validated**. This is the CPU
half of the [process-owner transport](p2-t4-tau-owner-channel.md), not a complete
selected-weight tau run, quality result, or confidential admission.

`record-tau-cpu-parity.py --selected-owner` is a separate opt-in mode. It requires
local private FIFO stdin/stdout, rejects simultaneous session/probe modes, and
keeps the existing offline image, UID, namespace, resource limits and measured
two-model parity checks. GitHub's parity controller does not select this mode.

After parity, the worker checks each original receipt against the actual owned
process and endpoint. It publishes the original readiness bytes only to the
private controller, then serves simulator/evaluator requests using the existing
`MeasuredClient` checks. Count and completion must be paired, with an unchanged
body, model and prompt-token count. The coordinator owns the shared budget; this
adapter neither retries nor invents a second allowance. At most 32 completed
requests can pass through the CPU relay. Readiness expiry is never renewed.

A final `finish` frame must carry an allowed status and receive an exact bound
acknowledgement with the completed request count. EOF, pending count-only work,
wrong bindings, expired deadlines and duplicate finish frames fail closed.
`evaluated` requires at least one completed request. The worker rechecks both
processes and endpoints, then the existing supervisor stops both children and
confirms container/image cleanup. A finish acknowledgement alone is not proof
that cleanup succeeded or that a tau task earned a reward.

The `nib.tau-cpu-session/v4` result retains only the finish status and numeric
request count in addition to existing content-free measurements. The host must
join it to the private relay's observed result. Older v2/v3 contracts and default
commands remain unchanged. Prompts, responses and exception text are not logged.

Verification: `test_tau_cpu_owner.py`, `test_tau_owner_channel.py`, existing CPU
session/parity/probe tests, and the full `make check` gate. Tests include actual
subprocesses and binary private pipes but use synthetic inference responses.

Still required: fresh GPU-owner integration, verified scorer/owner launch,
end-to-end selected-weight tau execution and committed hardware evidence.
