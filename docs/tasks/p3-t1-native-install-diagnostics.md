# P3.T1: Native installation rejection diagnostics

The original artifacts from workflow 39678969234 show installation rejected
with no installer exit code or output. Its cgroup was empty and removed, with
zero OOM kills. The output-limit change did not establish the cause.

Record at most eight source labels and line numbers from our worker and process
supervisor, plus bounded private PID-namespace process-state counts. Do not
record exception messages, locals, process names, command lines or paths. The
enclosing evidence pins the source revision. These diagnostics do not change
admission, cleanup, namespace, output or resource limits.

Verification: focused `scripts.test_mkosi_build`, full `make check`, and all
final-head hosted checks before merge. Then repeat the main-branch bounded
native installation workflow and preserve its original evidence.

Not done: the exact installation cause remains unproven; no successful package
installation, DKMS, UKI, verity, boot or confidential qualification is claimed.
