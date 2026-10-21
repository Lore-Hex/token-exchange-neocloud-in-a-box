# Detached Build-Child Cleanup

The v2 `host/probe-build-resources.py` candidate strengthens the synthetic
resource probe. Its trusted worker forks a child which creates a new session,
rechecks cgroup membership, closes standard descriptors and acknowledges
readiness through a private pipe. The worker verifies the child is still alive
before it exits. The parent must observe a populated group, invoke only that
owned group's `cgroup.kill`, observe it empty and remove it.

The child cannot hold the parent's output pipe open. Its independent 60-second
alarm is a last-resort lifetime bound, not a successful-cleanup substitute.
The existing 20-second worker and 10-second cleanup limits remain. An already
empty group cannot pass this new proof. No arbitrary command, parent controller,
host service or account is modified.

This follows the Linux [cgroup.kill contract](https://docs.kernel.org/admin-guide/cgroup-v2.html):
all processes in a cgroup and its descendants receive SIGKILL, including
processes no longer in the original process group. The fixed child demonstrates
that case; it is not a malicious-workload escape test. No package installation,
complete build sandbox or image qualification is implied.

Status: implemented, actual Linux execution pending. Earlier v1 evidence remains
valid for its narrower membership/accounting and empty-group cleanup claims.
