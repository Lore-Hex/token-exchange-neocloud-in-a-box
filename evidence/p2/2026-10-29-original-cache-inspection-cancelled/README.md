# Original cache inspection cancelled

[receipt.json](receipt.json) is the byte-original content-free receipt supplied
by the operator after retrieval from the failed read-only inspection. Its
producer is `f5ded64c3bf1b41a8a38fcda6c83620e65a3bcfc`; the recorded interval is
2026-10-29T04:51:41.843537+00:00 to 04:51:59.361235+00:00.
The receipt is 1,832 bytes with SHA-256
`f6484e3ebfe9179ee8208de1629af3f656e9fff90ad77bbc1a3f72c9864a5f70`.
[output.log](output.log) preserves the operator-supplied empty original log:
zero bytes, SHA-256
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
Neither original artifact has been rewritten or supplemented with diagnostic
fields. Neither contains private host identities, addresses or paths.

[run.json](run.json) is preservation metadata, not another execution. It
records the inspector invocation with private values redacted, not the private
controller/monitor wrapper or an executable replay command. GPU class is
operator-reported, not measured by the receipt; driver and CUDA versions were
not recorded. The producing inspector's source bytes hash to the receipt's
`script_sha256`. Its actual producing commit is already reachable through
normal merge ancestry; no producer relabelling or history exception is needed.

The original result remains **fail / cancelled**, with zero verified files and
bytes, `pack_verified=false`, and overall cleanup **unproven**. Worker exit,
process-group absence, private namespace verification and private mount cleanup
are all false. Zero verification counters and `mount_changed=false` do not
establish that no mount or read was attempted. There is no worker-stage failure
envelope in this receipt; no NFS, mount, permission, hash or other underlying
failure cause is established by the receipt or empty log.

The resource section records limit readback, empty membership, group removal,
zero OOM kills, a 5,496,598,528-byte memory peak and populated membership before
cleanup. Those resource observations do not upgrade the original overall
cleanup or worker acknowledgment flags.

Separately, the operator reports that a private monitoring overlap check
observed an additional matching process and requested cancellation. Whether
that process was foreign or part of the owned launch/session wrapper remains
unproven. This artifact does not retroactively classify it as foreign or owned.
The operator also reports a later external confirmation of owned process and
cgroup absence, retained in a private monitoring journal. That journal is not
published here or substituted for the worker receipt. External absence is a
separate observation, not retroactive acknowledgment or a successful scan.

The [earlier failed inspection](../2026-10-29-original-cache-inspection-failure/README.md)
is preserved separately and unchanged. Neither failure proves cache
availability, complete model integrity, runtime readiness, serving,
immutability or confidentiality. No mount, read, retry, inference, resource
change or live-controller change was performed to preserve this evidence.
Any later attempt retains its independent source/review/CI and operator gates.
