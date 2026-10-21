# Build Resource Controller Probe

Status: candidate only, not a successful hardware run or image build.
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
