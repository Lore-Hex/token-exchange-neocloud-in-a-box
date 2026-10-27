# Ordinary-serving core policy

Software and offline regressions only; no deployed hardening, crash experiment,
zero-data-retention, confidential-computing or phase-completion claim.

## Source-backed policy

Linux [core(5), DESCRIPTION](https://man7.org/linux/man-pages/man5/core.5.html)
documents that an **empty** `kernel.core_pattern` with `kernel.core_uses_pid=0`
suppresses core files. Empty with `core_uses_pid=1` instead creates hidden PID
files. Piped capture ignores `RLIMIT_CORE`. Do not use a literal `/dev/null`
pattern: the kernel can unlink an existing destination before opening it.

Reviewed Linux v6.8 commit `e8f897f4afef0031fe618a8e94127a0934896aba`:
[`_proc_do_string`](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/kernel/sysctl.c#L146)
stores an empty string when a newline is written at offset zero;
[`do_coredump`](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/fs/coredump.c#L650)
cannot open the resulting empty pathname. This is source verification, not a
measurement of an operator's kernel. Exact readback is mandatory on application.

Reviewed systemd v255
[`parse_file`](https://github.com/systemd/systemd/blob/v255/src/sysctl/sysctl.c#L258)
preserves an empty assignment, rather than treating it as an absent option.
[`sysctl_write`](https://github.com/systemd/systemd/blob/v255/src/basic/sysctl-util.c#L47)
uses [`write_string_stream_ts`](https://github.com/systemd/systemd/blob/v255/src/basic/fileio.c#L167),
which appends a newline, including for an empty value. The helper invokes
`/usr/lib/systemd/systemd-sysctl` on only its fixed file and checks exact bytes:

```ini
fs.suid_dumpable = 0
kernel.core_uses_pid = 0
kernel.core_pattern =
```

It installs `/etc/sysctl.d/zz-nib-disable-core.conf` without overwriting a
different file. It rejects later `.conf` names in all four systemd configuration
directories, consistent with [sysctl.d ordering](https://www.freedesktop.org/software/systemd/man/latest/sysctl.d.html).
It masks `apport.service` with `--no-reload`, without `--now`, and verifies and
fsyncs the persistent filesystem mask. Neither the service nor Docker nor the
serving container is stopped/restarted, and the systemd manager is not reloaded.
[systemctl's `--no-reload`](https://www.freedesktop.org/software/systemd/man/255/systemctl.html#--no-reload)
deliberately leaves the manager's loaded unit state unverified. The disk mask
is evidence of persistent configuration, not proof that a currently loaded
Apport unit cannot be started/restarted or that an active collector stopped.
An administrator or another privileged writer can still change host policy.

NVIDIA's [Container Toolkit troubleshooting](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/troubleshooting.html#containers-losing-access-to-gpus-with-error-failed-to-initialize-nvml-unknown-error),
reviewed October 27, 2026, documents that `systemctl daemon-reload` can trigger
container cgroup updates and GPU-access loss with the systemd cgroup driver.
Do not add a global reload to this live migration to obtain a `LoadState=masked`
readback. This documented hazard does not establish the cause of any particular
memory-limit failure. Effective manager-state verification or activation must
be separately coordinated outside an active GPU lifetime; it is not performed
by this helper.
The mask's `/dev/null` target must be character device 1:3, but **no core
destination uses that path**. A reboot and all alternative crash collectors
have not been tested. Already captured dumps and in-flight collectors are not
erased, inspected, stopped or retroactively covered.

## Reviewed application only

Run from the independently reviewed checkout on the actual Linux Docker host
as root. The state argument is the existing owner's **container** subdirectory,
not its network journal. Pre-create a new root-owned mode-0700 receipt directory
for each invocation; never reuse one. Paths must have no symlink ancestors.
Private receipts contain exact container identity and kernel configuration;
do not publish them without a separate redaction review.

```sh
python3 -I -S -B host/harden-selected-service-core.py \
  --owner-state /private/owner-state/container \
  --receipt-dir /private/new-core-inspection
```

Default mode reads only; its temporary metadata worker does not change limits.
That worker is excluded from the serving-process count. `result=pass` means
inspection completed, not that hardening passed: inspect the separate
`core_policy_verified` and `persistent_policy_verified` booleans. The latter
covers the exact sysctl file and disk mask, not the manager's cached unit state;
`effective_unit_mask_verified` remains false in both modes. ZDR remains false.
After independent patch/CLI review and explicit coordination, use a different
empty receipt directory and add `--apply`. The fsynced `baseline.json` precedes
all changes; `result.json` records success/failure without exception text.
Failure or interruption may leave partial hardening. Preserve both records,
investigate, and do not automatically retry or restore insecure settings.
Hard limits cannot be raised back by the unprivileged serving processes.

The migration reuses the existing local-daemon and owner-journal identity
checks and exact container ID. A fixed Python worker executes as 65532:65532
inside that container's verified private PID namespace. It lowers only
`RLIMIT_CORE`, checks all four UID/GID fields, scans at most 1024 processes
three times (two lowering passes, one verification), and has an eight-second
alarm. Host commands use existing output/time bounds and process-group cleanup;
the complete invocation has a 90-second cutoff capped by the original lease.
There are no serving-process signals, retries, API calls, GPU operations,
environment/argv/log/memory reads, profile changes or lease extensions.

For future launches, both selected-service entry paths reject a nonempty host
pattern, nonzero PID suffix setting, or nonzero suid-dump setting before resource
work. Docker creation explicitly supplies
[`--ulimit core=0:0`](https://docs.docker.com/reference/cli/docker/container/run/#ulimit)
and inspection requires the exact core limit. Migration cannot update an
existing container's Docker creation settings; its process limits are hardened
in place, while future containers use the new launch contract. Do not restart
an old container as a substitute for that contract.

## Verification and limits

Focused offline tests cover empty versus pipe/device patterns, suffix drift,
bounded reads/process scans, UID/namespace mismatch, fixed worker calls,
readback failure, durable immutable baseline, partial failure, no restart,
no manager reload and no inferred effective unit-mask claim.
Run from the repository root without network or listening sockets:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts TMPDIR=/private/tmp \
  python3 -m unittest test_selected_core_limits test_selected_service_smoke \
    test_selected_service_owner test_selected_service_network
```

This closes one potential persistence channel only after a successful live
readback. Prefix KV caching retains request-derived state in RAM until eviction
or process exit; no per-request zeroization is established. Logging, swap,
telemetry, existing crash artifacts and upstream retention require separate
evidence and policy. No privacy or readiness flag is changed by this helper.
