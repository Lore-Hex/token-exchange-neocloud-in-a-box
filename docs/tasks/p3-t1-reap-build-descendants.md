# P3.T1: Reap adopted build descendants

[Native run 39681414823](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39681414823)
at `551a50d7a1fba2e8baf6b5314bd4f3e3e18b2177` identifies the rejection at
`snapshot.py:76`: the process group was still present after cleanup.
The [original result](../../evidence/p3/2026-10-21-native-build-zombie/result.json)
records exactly one zombie and the running PID-1 worker, with no other live
process. Outer namespace/cgroup cleanup succeeded. The installer exit/output
were masked by the cleanup exception; they remain unknown, not successful.

The private worker is PID 1 and adopts orphaned descendants. Signaling an
already-dead process cannot reap it. After waiting for the direct Popen child,
reap waitable children in that tool's process group using nonblocking
`waitpid(-pgid, WNOHANG)`, then still require independent group absence.
See [Linux wait semantics](https://man7.org/linux/man-pages/man2/waitpid.2.html).
Never reap unrelated groups or drop their exit statuses. Batches are bounded
at 256 calls inside the original five-second deadline; signal, timeout, pipe,
namespace, seccomp and resource checks remain unchanged.

A Linux-only subprocess regression uses a
[subreaper](https://man7.org/linux/man-pages/man2/PR_SET_CHILD_SUBREAPER.2const.html)
to reproduce adoption without changing the test runner or host settings. It
proves the old cleanup fails with a zombie, the new cleanup reaps it, a
separate live child is untouched, and installer exit 7/output are preserved.
The exact constant comes from
[Linux v6.8 prctl.h](https://github.com/torvalds/linux/blob/v6.8/include/uapi/linux/prctl.h).
Portable unit tests cover bounded batches, interruptions and denied reaping;
existing tests still reject live descendants and unproven absence.

Verification: focused snapshot/process-cleanup tests, full local `make check`
(Linux regression runs in hosted CI), all final-head checks, then another
bounded native installation. Refresh the full shared-helper hash chain;
historical evidence is unchanged. No successful image, DKMS, UKI, verity,
boot, reproducibility or confidential qualification is claimed.
