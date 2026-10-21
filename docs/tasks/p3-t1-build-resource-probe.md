# Build Resource Controller Probe

Status: the synthetic Linux probe passed on October 21, 2026; no image build.
`host/probe-build-resources.py` tests existing cgroup-v2 CPU, memory and PID
controllers on an explicitly approved root Linux AMD64 lab VM. It creates one
random, exclusive group and never changes parent controllers, host accounts,
existing processes, GPU settings or services.

The fixed synthetic worker joins that group before allocating 32 MiB or
forking. It checks the exact controller values, its own membership and inherited
membership in a child. Limits are 8 GiB memory, no swap, whole-group OOM behavior,
two CPU-equivalents of quota and 256 tasks. The parent bounds this probe to
20 seconds and cleanup to another 10 seconds. It kills only the newly owned
group, waits for `populated=0` and removes it. Changed identity, failed limit
readback or unconfirmed cleanup fails the recording. No raw child diagnostics,
cgroup identifiers, host paths or other workload metadata enter the evidence.

This is configuration/membership/accounting proof, not a CPU saturation, OOM
or fork-exhaustion stress test. It is also not a package runner. The host
interpreter and kernel remain observed, unpinned builder dependencies.
The future installer must join this boundary before starting package hooks,
retain its namespace/capability isolation, and use the 1800-second build
deadline rather than this 20-second synthetic-probe deadline.

The operator passes the SHA-256 of the exact committed script and its commit
when invoking the probe; a source mismatch fails before creating a group.
Only the fixed synthetic child is supported. Output must be a new directory
under the operator's private directory. Preserve both success and failure.

Source: Linux's [cgroup-v2 contract](https://docs.kernel.org/admin-guide/cgroup-v2.html),
specifically process membership/inheritance, `cgroup.kill`, `cgroup.events`,
`cpu.max`, `pids.max`, `memory.max`, `memory.swap.max` and `memory.oom.group`.
Focused tests: `python -B -m unittest discover -s scripts -p 'test_build_resources.py'`.

## Recorded Result

The [unaltered result](../../evidence/p3/2026-10-21-build-resource-limits/result.json)
and [run record](../../evidence/p3/2026-10-21-build-resource-limits/run.json) were
produced by committed script `d7a822abfcd7c14df4c33bf56af3d60185587120`, with
SHA-256 `0ee3cd72e7ddbddf7101dde634f4a2e79dce83ab1f4181c93b694942f95aafd5`,
on the approved isolated Linux lab. Exact limit readback, worker/child membership,
memory accounting, empty-group verification and removal passed. Peak accounted
memory was 34,865,152 bytes and OOM kills were zero.

This fixed child exited before cleanup. The record therefore proves empty-group
cleanup, not the destruction of a hostile detached process. A combined installer
and explicit surviving-descendant test remain required. The namespace probe ran
on a separate host; this record does not combine the two security boundaries.
